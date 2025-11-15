"""
Conserv API Client for environmental sensor data integration.

This module provides a client interface that handles sensor data retrieval, 
historical data queries, and data transformation
to maintain compatibility with the EnvironmentData schema.

## Commands:
- Create HTML documentation in `docs/clients`: `pdoc clients/conserv_client.py -o docs/ --no-search` 
- Save API output to `samples/` folder: `python -c "from clients.conserv_client import ConservAPIClient; ConservAPIClient().sample_raw_data()"`

## API Endpoints

There are multiple Conserv customers (API keys). For each, we trigger an export, wait for it to process, and download the data. 

- New customers can be added by adding a new API key in the `.env` file as `CONSERV_API_KEY_{customer_id}`.
- Auth is handled by sending headers = {"x-api-key": api_key, ...}

### Trigger Export

- `POST https://api.conserv.io/v1/sensors/export`

### Poll Export Status

- `GET https://api.conserv.io/v1/sensors/export/{uuid}/status`

### Get Download URL

- `POST https://api.conserv.io/v1/sensors/export/{uuid}/download`
- API limits exports to 7-day windows, automatically chunked for historical data
- Returns CSV data with columns: Sensor Name, Time, Temperature (°C), Humidity (%)

"""

import os
import requests
import time
import logging
import datetime
import polars
import io
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
import urllib3

class ConservAPIClient:
    """
    Client for the Conserv API that handles export → status → download workflow.
    Supports multiple customer tenants and handles the 7-day window limitation.
    """

    def __init__(self, logger: Optional[logging.Logger] = None, home_directory: str = "."):
        """
        Initialize the Conserv API client.

        Parameters
        ----------
        logger : Optional[logging.Logger]
            Logger instance for tracking operations
        home_directory : str, default="."
            Base directory for finding .env file
        """
        # Read configuration dynamically from .env file
        customers = []
        env_file_path = os.path.join(home_directory, ".env")
        
        # First try to read from .env file
        try:
            with open(env_file_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("CONSERV_API_KEY_") and "=" in line:
                        var_name, api_key = line.split("=", 1)
                        api_key = api_key.strip()
                        # Extract customer ID from variable name
                        customer_id = int(var_name.replace("CONSERV_API_KEY_", ""))
                        customers.append({'customer_id': customer_id, 'api_key': api_key})
        except FileNotFoundError:
            if logger:
                logger.warning(
                    f"No .env file found at {env_file_path}. "
                    f"If running from a subdirectory, set home_directory parameter to parent directory (e.g., home_directory='..').")

        if not customers:
            raise ValueError("No Conserv API keys found in .env file")

        self.base_url = "https://api.conserv.io"
        self.customers = customers
        self.logger = logger or logging.getLogger(__name__)
        self.max_window_days = 7  # API limitation
        self.poll_interval_seconds = 30  # How often to check export status
        self.max_wait_minutes = 15  # Maximum time to wait for export completion
        
        if logger:
            logger.info(f"Initialized Conserv client with {len(customers)} customers")

    def _make_api_request(
        self, method: str, endpoint: str, api_key: str, **kwargs
    ) -> requests.Response:
        """
        Make a request to the Conserv API with proper headers and error handling.

        Parameters
        ----------
        method : str
            HTTP method (GET, POST, etc.)
        endpoint : str
            API endpoint (without base URL)
        api_key : str
            Customer API key
        **kwargs
            Additional arguments for requests

        Returns
        -------
        requests.Response
            Response object
        """
        url = f"{self.base_url}{endpoint}"
        headers = {"x-api-key": api_key, "Content-Type": "application/json"}

        # Log the request (without sensitive key)
        safe_headers = {**headers, "x-api-key": "XXXX"}
        self.logger.info(f"Conserv API {method} {url} headers={safe_headers}")

        try:
            # Add SSL verification handling for potential certificate issues
            response = requests.request(
                method, url, headers=headers, verify=True, **kwargs
            )
            response.raise_for_status()
            return response
        
        except requests.exceptions.SSLError as e:
            # Log the SSL error for debugging
            self.logger.warning(f"SSL verification failed for {url}: {str(e)}")
            self.logger.info("Retrying with SSL verification disabled...")

            # Try again with SSL verification disabled if certificate issues
            # Temporarily disable SSL warnings only for this specific request
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            try:
                response = requests.request(
                    method, url, headers=headers, verify=False, **kwargs
                )
                response.raise_for_status()
                
                self.logger.info("Request succeeded with SSL verification disabled")
                return response
            
            except requests.exceptions.RequestException as retry_error:
                # If the retry also fails, log and re-raise with cleaner error context
                if isinstance(retry_error, requests.exceptions.HTTPError):
                    self.logger.error(f"API request failed after SSL fallback: {retry_error.response.status_code} {retry_error.response.reason}")
                else:
                    self.logger.error(f"Retry with SSL disabled also failed: {str(retry_error)}")
                raise retry_error
            
            finally:
                # Always re-enable SSL warnings after the request
                # Reset to default urllib3 warning behavior
                import warnings
                warnings.resetwarnings()

    def launch_export(
        self, api_key: str, start_time: datetime.datetime, end_time: datetime.datetime
    ) -> str:
        """
        Launch a data export for a specific time window.

        Parameters
        ----------
        api_key : str
            Customer API key
        start_time : datetime.datetime
            Start time for export (UTC)
        end_time : datetime.datetime
            End time for export (UTC)

        Returns
        -------
        str
            Export UUID for polling status
        """
        # Validate window size
        window_days = (end_time - start_time).days
        if window_days > self.max_window_days:
            raise ValueError(
                f"Export window of {window_days} days exceeds maximum of {self.max_window_days} days"
            )

        payload = {"start": start_time.isoformat(), "end": end_time.isoformat()}

        # Debug the request payload
        if self.logger:
            self.logger.info(f"Launch export payload: {payload}")

        response = self._make_api_request("POST", "/v1/sensors/export", api_key, json=payload)

        result = response.json()

        uuid = result.get("uuid")
        if not uuid:
            raise ValueError(f"No UUID returned from export launch: {result}")

        self.logger.info(f"Conserv export launched: {uuid}")
        return uuid

    def check_export_status(self, uuid: str, api_key: str) -> str:
        """
        Check the status of an export.

        Parameters
        ----------
        uuid : str
            Export UUID
        api_key : str
            Customer API key

        Returns
        -------
        str
            Status: "pending", "completed", or "failed"
        """
        response = self._make_api_request(
            "GET", f"/v1/sensors/export/{uuid}/status", api_key
        )
        result = response.json()

        status = result.get("status")
        if not status:
            raise ValueError(f"No status returned for export {uuid}: {result}")

        return status

    def get_download_url(self, uuid: str, api_key: str) -> str:
        """
        Get the download URL for a completed export.

        Parameters
        ----------
        uuid : str
            Export UUID
        api_key : str
            Customer API key

        Returns
        -------
        str
            S3 download URL
        """
        response = self._make_api_request(
            "POST", f"/v1/sensors/export/{uuid}/download", api_key
        )
        result = response.json()

        url = result.get("url")
        if not url:
            raise ValueError(f"No download URL returned for export {uuid}: {result}")

        return url

    def download_export_data(self, download_url: str) -> polars.DataFrame:
        """
        Download and parse the export data from S3.

        Parameters
        ----------
        download_url : str
            S3 download URL

        Returns
        -------
        polars.DataFrame
            Parsed sensor data
        """
        self.logger.info("Downloading export data from S3")

        try:
            response = requests.get(download_url)
            response.raise_for_status()

            # Parse CSV data
            csv_content = response.content
            df = polars.read_csv(io.BytesIO(csv_content))

            self.logger.info(f"Downloaded {df.shape[0]} rows, {df.shape[1]} columns")
            return df

        except Exception as e:
            self.logger.error(f"Failed to download export data: {e}")
            raise

    def wait_for_export_completion(self, uuid: str, api_key: str) -> str:
        """
        Poll export status until completion or timeout.

        Parameters
        ----------
        uuid : str
            Export UUID
        api_key : str
            Customer API key

        Returns
        -------
        str
            Final status ("completed" or "failed")
        """
        start_time = time.time()
        max_wait_seconds = self.max_wait_minutes * 60

        while time.time() - start_time < max_wait_seconds:
            status = self.check_export_status(uuid, api_key)

            if status == "completed":
                self.logger.info(f"Export {uuid} completed successfully")
                return status
            elif status == "failed":
                self.logger.error(f"Export {uuid} failed")
                return status
            elif status in ("pending", "processing", "queued"):
                self.logger.info(
                    f"Export {uuid} still {status}, waiting {self.poll_interval_seconds}s..."
                )
                time.sleep(self.poll_interval_seconds)
            elif status == "processing":
                self.logger.info(
                    f"Export {uuid} still processing, waiting {self.poll_interval_seconds}s..."
                )
                time.sleep(self.poll_interval_seconds)
            else:
                raise ValueError(f"Unknown export status: {status}")

        raise TimeoutError(
            f"Export {uuid} did not complete within {self.max_wait_minutes} minutes"
        )

    def export_data(
        self,
        api_key: str,
        customer_id: int,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
    ) -> Optional[polars.DataFrame]:
        """
        Complete export workflow: launch → wait → download for a single customer.

        Parameters
        ----------
        api_key : str
            Customer API key
        customer_id : int
            Customer ID for data tagging
        start_time : datetime.datetime
            Start time for export (UTC)
        end_time : datetime.datetime
            End time for export (UTC)

        Returns
        -------
        Optional[polars.DataFrame]
            Sensor data with customer_id added, or None if no data found
        """
        try:
            self.logger.info(
                f"Starting export for customer {customer_id}: {start_time} to {end_time}"
            )

            # Launch export
            uuid = self.launch_export(api_key, start_time, end_time)

            # Wait for completion
            status = self.wait_for_export_completion(uuid, api_key)

            if status != "completed":
                self.logger.warning(
                    f"Export failed for customer {customer_id}, UUID {uuid}"
                )
                return None

            # Add delay to ensure download URL is ready after export completion
            time.sleep(5)

            # Get download URL and fetch data
            download_url = self.get_download_url(uuid, api_key)
            df = self.download_export_data(download_url)

            if df.shape[0] == 0:
                self.logger.info(f"No data found for customer {customer_id}")
                return None

            # Add customer_id and source columns
            df = df.with_columns(
                [
                    polars.lit(customer_id).alias("customer_id"),
                    polars.lit("Conserv").alias("source"),
                ]
            )

            # Transform to standardized schema
            current_utc = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
            df = self._transform_to_standard_schema(df, customer_id, current_utc)

            self.logger.info(
                f"Successfully exported {df.shape[0]} rows for customer {customer_id}"
            )
            return df

        except Exception as e:
            self.logger.error(f"Export failed for customer {customer_id}: {e}")
            return None

    def export_data_chunked(
        self,
        api_key: str,
        customer_id: int,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
    ) -> List[polars.DataFrame]:
        """
        Export data in chunks to handle the 7-day window limitation.

        Parameters
        ----------
        api_key : str
            Customer API key
        customer_id : int
            Customer ID for data tagging
        start_time : datetime.datetime
            Start time for export (UTC)
        end_time : datetime.datetime
            End time for export (UTC)

        Returns
        -------
        List[polars.DataFrame]
            List of DataFrames, one per chunk
        """
        chunks = []
        current_start = start_time

        while current_start < end_time:
            # Calculate chunk end (max 7 days)
            chunk_end = min(
                current_start + datetime.timedelta(days=self.max_window_days), end_time
            )

            self.logger.info(
                f"Exporting chunk for customer {customer_id}: {current_start} to {chunk_end}"
            )

            # Export this chunk
            chunk_data = self.export_data(
                api_key, customer_id, current_start, chunk_end
            )

            if chunk_data is not None:
                chunks.append(chunk_data)

            # Move to next chunk
            current_start = chunk_end

        return chunks

    def get_current_readings(self, hours_back: int = 24) -> polars.DataFrame:
        """
        Get current readings for all configured customers.

        Parameters
        ----------
        hours_back : int
            Number of hours of data to retrieve

        Returns
        -------
        polars.DataFrame
            Combined data from all customers
        """
        end_time = datetime.datetime.now(datetime.timezone.utc)
        start_time = end_time - datetime.timedelta(hours=hours_back)

        all_data = []

        for customer in self.customers:
            self.logger.info(f"Processing customer {customer['customer_id']}")

            try:
                # Export data (chunked if necessary)
                if hours_back > (self.max_window_days * 24):
                    chunks = self.export_data_chunked(
                        customer['api_key'], customer['customer_id'], start_time, end_time
                    )
                    if chunks:
                        customer_data = polars.concat(chunks, how="vertical")
                        all_data.append(customer_data)
                else:
                    customer_data = self.export_data(
                        customer['api_key'], customer['customer_id'], start_time, end_time
                    )
                    if customer_data is not None:
                        all_data.append(customer_data)

            except Exception as e:
                self.logger.error(f"Failed to process customer: {e}")
                # Continue with other customers
                continue

        if not all_data:
            self.logger.warning("No data retrieved from any customer")
            return polars.DataFrame()

        # Combine all customer data
        combined_data = polars.concat(all_data, how="vertical")
        self.logger.info(
            f"Combined data: {combined_data.shape[0]} total rows from {len(all_data)} customers"
        )

        return combined_data

    def get_historical_data(
        self, start_utc: int, end_utc: int, max_concurrent_jobs: int = 5
    ) -> Optional[polars.DataFrame]:
        """
        Get historical data for all customers for a specific time period.
        Handles chunking for periods longer than 7 days.

        Parameters
        ----------
        start_utc : int
            Start time as UTC timestamp in seconds
        end_utc : int
            End time as UTC timestamp in seconds
        max_concurrent_jobs : int, default=5
            Maximum number of concurrent API jobs

        Returns
        -------
        Optional[polars.DataFrame]
            Combined historical data from all customers for the specified period
        """
        if self.logger:
            self.logger.info(
                f"Fetching Conserv data for period: {start_utc} to {end_utc}"
            )

        # Convert UTC timestamps to datetime objects
        start_time = datetime.datetime.fromtimestamp(
            start_utc, tz=datetime.timezone.utc
        )
        end_time = datetime.datetime.fromtimestamp(end_utc, tz=datetime.timezone.utc)

        all_customer_data = []

        # Process each customer
        for customer in self.customers:
            try:
                if self.logger:
                    self.logger.info(
                        f"Fetching data for customer {customer['customer_id']}"
                    )

                # Use chunked export for reliable data retrieval
                customer_data_chunks = self.export_data_chunked(
                    api_key=customer['api_key'],
                    customer_id=customer['customer_id'],
                    start_time=start_time,
                    end_time=end_time,
                )

                if customer_data_chunks:
                    # Combine all chunks for this customer
                    customer_data = polars.concat(customer_data_chunks, how="diagonal")

                    # Add customer_id column if not present
                    if "customer_id" not in customer_data.columns:
                        customer_data = customer_data.with_columns(
                            polars.lit(customer['customer_id']).alias("customer_id")
                        )

                    all_customer_data.append(customer_data)

                    if self.logger:
                        self.logger.info(
                            f"Successfully fetched {customer_data.shape[0]} records for customer {customer['customer_id']}"
                        )
                else:
                    if self.logger:
                        self.logger.info(
                            f"No data available for customer {customer['customer_id']}"
                        )

            except Exception as e:
                if self.logger:
                    self.logger.warning(
                        f"Failed to fetch data for customer {customer['customer_id']}: {e}"
                    )
                # Continue with other customers
                continue

        # Combine all customer data with master schema enforcement
        if all_customer_data:
            # Apply master schema enforcement before concatenation
            normalized_data = []
            for i, customer_data in enumerate(all_customer_data):
                normalized = self._apply_conserv_master_schema(
                    customer_data, f"Customer_{i}"
                )

                # DEBUG: Log detailed schema information for each customer
                if self.logger:
                    self.logger.info(f"CUSTOMER {i} POST-NORMALIZATION SCHEMA:")
                    for col in normalized.columns:
                        self.logger.info(f"  {col}: {normalized[col].dtype}")

                normalized_data.append(normalized)

            # DEBUG: Check for schema mismatches between customers before concat
            if len(normalized_data) > 1 and self.logger:
                self.logger.info("SCHEMA MISMATCH DETECTION:")
                base_schema = {
                    col: normalized_data[0][col].dtype
                    for col in normalized_data[0].columns
                }
                for i, df in enumerate(normalized_data[1:], 1):
                    mismatches = []
                    for col in df.columns:
                        if col in base_schema and df[col].dtype != base_schema[col]:
                            mismatches.append(
                                f"{col}: Customer_0={base_schema[col]} vs Customer_{i}={df[col].dtype}"
                            )

                    if mismatches:
                        self.logger.error("SCHEMA MISMATCHES found between customers:")
                        for mismatch in mismatches:
                            self.logger.error(f"  {mismatch}")
                    else:
                        self.logger.info(
                            f"Customer_{i}: No schema mismatches with Customer_0"
                        )

            combined_data = polars.concat(normalized_data, how="diagonal")
            if self.logger:
                self.logger.info(
                    f"Successfully combined data from {len(normalized_data)} customers: {combined_data.shape[0]} total records"
                )
            return combined_data
        else:
            if self.logger:
                self.logger.warning("No data retrieved from any customer")
            return None

    def _apply_conserv_master_schema(
        self, df: polars.DataFrame, step_name: str
    ) -> polars.DataFrame:
        """
        Apply master schema enforcement to Conserv data for consistency.
        Uses the same master schema as EnvironmentData to ensure perfect compatibility.

        Parameters
        ----------
        df : polars.DataFrame
            Customer data to normalize
        step_name : str
            Step name for logging

        Returns
        -------
        polars.DataFrame
            Data with master schema types applied
        """
        if df.is_empty():
            return df

        if self.logger:
            self.logger.info(
                f"CONSERV MASTER SCHEMA [{step_name}]: Enforcing schema on {df.shape[0]} rows"
            )

        # Use EXACT master schema from EnvironmentData for perfect compatibility
        master_schema = {
            "source": polars.String,
            "SensorID_Coris": polars.Int32,
            "SensorID_Conserv": polars.String,
            "customer_id": polars.Int32,
            "QueryUTC": polars.Int32,
            "SensorReadingUTC": polars.Int64,
            "DeviceID": polars.String,
            "SensorReadingUTC_SecondsFromPrior": polars.Int64,
            "SensorReadingF": polars.Float32,
            "SensorReadingRh": polars.Float32,
            "SensorName": polars.String,
            "SensorPort": polars.Int64,
            "ServerUTC": polars.Int64,
            "HexGatewayMac": polars.String,
            "LoraHexGatewayMac": polars.String,
            "LoraGatewayLastHeardUTC": polars.Int64,
            "SensorUnplugged": polars.Boolean,
            "LinkQualityText": polars.String,
            "HexMac": polars.String,
            "SensorDeleted": polars.Boolean,
            "SensorDeactivated": polars.Boolean,
            "SensorReading": polars.Float64,
            "DeviceName": polars.String,
            "DevTypeInt": polars.Int64,
            "SensorTempPref": polars.String,
            "DeviceTempPref": polars.Null,
            "UserTempPref": polars.String,
            "SensorTimeZone": polars.String,
            "SensorZipcode": polars.Null,
            "SensorType": polars.String,
            "SensorState0String": polars.String,
            "SensorState1String": polars.String,
            "ExpectedSensorReadingIntervalSeconds": polars.Int64,
            "SensorReadingC": polars.Float64,
            "SensorCalibrationOffsetC": polars.Float64,
            "SensorCalibrationOffsetF": polars.Float64,
            "SensorCalibrationOffsetExplanationText": polars.String,
            "SensorCalibrationOffsetExplanationFirstName": polars.String,
            "SensorCalibrationOffsetExplanationLastName": polars.String,
            "SensorCalibrationOffsetUTC": polars.Int64,
            "LoraBatteryPresent": polars.Int64,
            "LoraBattery_mV": polars.Int64,
            "LoraBatteryPercentage": polars.Int64,
            "LoraBatteryUTC": polars.Int64,
            "LoraBatteryIsCharging": polars.Int64,
            "LastSensorErrorValue": polars.Int64,
            "LastSensorErrorUTC": polars.Int64,
            "UnivID": polars.Int64,
            "SensorSerialNumber": polars.Null,
            "HeatIndexRh": polars.Float64,
            "ConjoinedRhSensorSensorReadingRh": polars.Float64,
            "SensorReadingHeatIndexF": polars.Float64,
            "SensorReadingHeatIndexC": polars.Float64,
            "HeatIndexWarningTier": polars.Int64,
            "LoraExternalPowerPresent": polars.Int64,
            "SensorCalibrationOffsetRh": polars.Int64,
            "SensorEventCount": polars.Int64,
            "SensorState": polars.String,
            # ALL Conserv-specific columns that appear in the data
            "Sensor Name": polars.String,
            "Sensor Serial": polars.String,
            "Time": polars.Int64,
            "Temperature (°C)": polars.Float64,
            "Humidity (%)": polars.Float64,
            "Dewpoint (°C)": polars.Float64,
            "Illuminance (lux)": polars.Float64,
            "UV (µW/cm²)": polars.Float64,
        }

        # Track conversions for logging
        conversions_made = []
        conversion_exprs = []

        for column in df.columns:
            if column in master_schema:
                target_type = master_schema[column]
                current_type = df[column].dtype

                if current_type != target_type:
                    conversions_made.append(
                        f"{column}: {current_type} -> {target_type}"
                    )

                    try:
                        # Handle specific conversion cases
                        if target_type == polars.Null:
                            # Keep null columns as-is
                            continue
                        elif str(current_type).startswith("Int") and str(
                            target_type
                        ).startswith("Int"):
                            # Int64 -> Int32 or vice versa
                            conversion_exprs.append(
                                polars.col(column).cast(target_type, strict=False)
                            )
                        elif str(current_type).startswith("Float") and str(
                            target_type
                        ).startswith("Float"):
                            # Float64 -> Float32 or vice versa
                            conversion_exprs.append(
                                polars.col(column).cast(target_type)
                            )
                        else:
                            # Generic conversion
                            conversion_exprs.append(
                                polars.col(column).cast(target_type, strict=False)
                            )

                    except Exception as conv_error:
                        if self.logger:
                            self.logger.warning(
                                f"CONSERV MASTER SCHEMA: Failed to convert {column}: {conv_error}"
                            )
                        continue

        # Apply all conversions at once
        if conversion_exprs:
            df = df.with_columns(conversion_exprs)

        # Log conversions made (using ASCII characters to avoid Unicode issues)
        if conversions_made and self.logger:
            self.logger.info(
                f"CONSERV MASTER SCHEMA [{step_name}]: Made {len(conversions_made)} conversions"
            )
            for conv in conversions_made:
                self.logger.info(f"  -> {conv}")
        elif self.logger:
            self.logger.info(
                f"CONSERV MASTER SCHEMA [{step_name}]: No conversions needed, schema already correct"
            )

        return df

    def _transform_to_standard_schema(
        self, df: polars.DataFrame, customer_id: int, query_utc: int = None
    ) -> polars.DataFrame:
        """
        Transform Conserv API data to standardized schema.

        Parameters
        ----------
        df : polars.DataFrame
            Raw Conserv data with columns: Sensor Name, Time, Temperature (°C), Humidity (%), customer_id, source
        customer_id : int
            Customer ID for generating SensorID and DeviceID

        Returns
        -------
        polars.DataFrame
            Data transformed to match standardized schema
        """
        if self.logger:
            self.logger.info("Transforming Conserv data to standardized schema")

        # Create a copy to avoid modifying the original
        df = df.clone()

        # ============ COLUMN TRANSFORMATIONS ============

        # 1. Temperature: Convert °C to °F
        if "Temperature (°C)" in df.columns:
            df = df.with_columns(
                ((polars.col("Temperature (°C)") * 9 / 5) + 32).alias("SensorReadingF")
            ).drop("Temperature (°C)")

        # 2. Humidity: Map directly
        if "Humidity (%)" in df.columns:
            df = df.rename({"Humidity (%)": "SensorReadingRh"})

        # 3. Time: Map to SensorReadingUTC
        if "Time" in df.columns:
            # Handle string time if needed
            if df["Time"].dtype == polars.String:
                df = df.with_columns(
                    polars.col("Time")
                    .str.strptime(polars.Datetime, "%Y-%m-%d %H:%M:%S%.f")
                    .dt.timestamp("s")
                    .alias("SensorReadingUTC")
                ).drop("Time")
            else:
                df = df.rename({"Time": "SensorReadingUTC"})

        # 4. Generate SensorID for Conserv
        if "Sensor Name" in df.columns:
            df = df.with_columns([
                polars.concat_str([
                    polars.lit("conserv:"),
                    polars.col("customer_id").cast(polars.String),
                    polars.lit(":"),
                    polars.col("Sensor Name")
                ]).alias("SensorID"),
                polars.col("Sensor Name").alias("SensorName"),
            ]).drop("Sensor Name")

        # 5. Generate DeviceID and add null columns for schema compatibility
        df = df.with_columns([
            polars.concat_str([
                polars.lit("conserv:"),
                polars.col("customer_id").cast(polars.String),
                polars.lit(":device")
            ]).alias("DeviceID"),
            polars.lit(None, dtype=polars.String).alias("DeviceName"),
            polars.lit(None, dtype=polars.String).alias("SensorType"),
        ])

        # 6. Add QueryUTC if provided
        if query_utc is not None:
            df = df.with_columns(polars.lit(query_utc).alias("QueryUTC"))

        # 7. Final schema enforcement for common columns
        cast_columns = [
            polars.col("SensorReadingF").cast(polars.Float32).alias("SensorReadingF"),
            polars.col("SensorReadingRh").cast(polars.Float32).alias("SensorReadingRh"),
            polars.col("SensorReadingUTC").cast(polars.Int64).alias("SensorReadingUTC"),
            polars.col("SensorID").cast(polars.String).alias("SensorID"),
            polars.col("DeviceID").cast(polars.String).alias("DeviceID"),
            polars.col("customer_id").cast(polars.Int32).alias("customer_id"),
        ]
        
        # Add QueryUTC casting if column exists
        if "QueryUTC" in df.columns:
            cast_columns.append(polars.col("QueryUTC").cast(polars.Int32).alias("QueryUTC"))
            
        df = df.with_columns(cast_columns)

        if self.logger:
            self.logger.info(f"Conserv transformation complete: {df.shape[0]} records")

        return df

    def sample_raw_data(self, save_to_samples: bool = True) -> Dict[str, Any]:
        """
        Generate sample raw API data for testing and exploration.
        
        This method triggers one export workflow using the first available customer
        and optionally saves the raw CSV data to the samples directory with timestamps.
        
        Parameters
        ----------
        save_to_samples : bool, default=True
            Whether to save the raw CSV data to samples/conserv/ directory
            
        Returns
        -------
        Dict[str, Any]
            Dictionary containing the raw export data:
            {
                'customer_id': customer_id,
                'export_uuid': export_uuid,
                'raw_csv_data': raw_csv_content,
                'processed_data': polars_dataframe
            }
        """
        if not self.customers:
            raise ValueError("No Conserv customers configured")
        
        # Use first available customer for sampling
        customer = self.customers[0]
        customer_id = customer['customer_id']
        api_key = customer['api_key']
        
        print(f"🔍 Generating sample data for Conserv customer {customer_id}...")
        
        results = {}
        
        # Export last 6 hours for quick sample
        end_time = datetime.datetime.now(datetime.timezone.utc)
        start_time = end_time - datetime.timedelta(hours=6)
        
        print(f"📅 Time range: {start_time} to {end_time}")
        print(f"🚀 Launching export for customer {customer_id}...")
        
        # Launch export
        uuid = self.launch_export(api_key, start_time, end_time)
        results['export_uuid'] = uuid
        results['customer_id'] = customer_id
        
        print(f"⏳ Waiting for export {uuid} to complete...")
        
        # Wait for completion
        status = self.wait_for_export_completion(uuid, api_key)
        
        if status != "completed":
            raise Exception(f"Export failed with status: {status}")
        
        print(f"✅ Export completed successfully")
        
        # Add delay to ensure download URL is ready
        time.sleep(5)
        
        # Get download URL and fetch raw data
        download_url = self.get_download_url(uuid, api_key)
        print(f"📥 Downloading raw CSV data...")
        
        # Download raw CSV data
        response = requests.get(download_url)
        response.raise_for_status()
        raw_csv_data = response.text
        results['raw_csv_data'] = raw_csv_data
        
        # Also parse into DataFrame for reference
        df = polars.read_csv(io.StringIO(raw_csv_data))
        results['processed_data'] = df
        
        print(f"📊 Retrieved {df.shape[0]} rows, {df.shape[1]} columns")
        
        if save_to_samples:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"raw_export_data_customer{customer_id}_{uuid}_{timestamp}.csv"
            filepath = Path(__file__).parent.parent / "samples" / "conserv" / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            
            with open(filepath, 'w', newline='') as f:
                f.write(raw_csv_data)
                
            print(f"💾 Saved raw CSV to: {filepath}")
            
            # Also save metadata as JSON
            metadata = {
                'customer_id': customer_id,
                'export_uuid': uuid,
                'start_time': start_time.isoformat(),
                'end_time': end_time.isoformat(),
                'download_url': download_url,
                'timestamp': timestamp,
                'rows_count': df.shape[0],
                'columns_count': df.shape[1],
                'columns': df.columns
            }
            
            metadata_filename = f"export_metadata_customer{customer_id}_{uuid}_{timestamp}.json"
            metadata_filepath = filepath.parent / metadata_filename
            
            with open(metadata_filepath, 'w') as f:
                json.dump(metadata, f, indent=2, default=str)
                
            print(f"📋 Saved metadata to: {metadata_filepath}")
        
        # Print summary
        print(f"\n📋 Sample Data Summary:")
        print(f"   • Customer ID: {customer_id}")
        print(f"   • Export UUID: {uuid}")
        print(f"   • Time range: {start_time} to {end_time}")
        print(f"   • Rows retrieved: {df.shape[0]}")
        print(f"   • Columns: {', '.join(df.columns)}")
        
        if save_to_samples:
            print(f"   • Files saved to: samples/conserv/")
        
        return results



