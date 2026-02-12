"""
Conserv API Client for environmental sensor data integration.

This module provides a client interface that handles sensor data retrieval, 
historical data queries, and data transformation
to maintain compatibility with the EnvironmentData schema.

## Commands:
- Create HTML documentation in `docs/clients`: `pdoc clients/conserv_client.py -o docs/ --no-search` 
- Save API output to `samples/` folder: `python -c "from clients.conserv_client import ConservAPIClient; ConservAPIClient().sample_raw_data()"`
- Test with `pytest tests/test_conserv_client.py`

## API Endpoints

There are multiple Conserv customers (API keys). For each, we trigger an export, wait for it to process, and download the data. 

- Customers are configured via `CONSERV_API_KEYS` env var as a JSON array: `[{"id":123,"key":"abc"},...]`
- Auth is handled by sending headers = {"x-api-key": api_key, ...}
- Some customers are failing due to invalid keys. These are skipped with warnings logged.
- Exports can only be 7 days so for historical data we chunk into 7-day windows. This will be very slow for large date ranges.
- Export data is captured in 15 minute increments, so for get_current we take the last 30 minutes and then return the most recent reading per sensor.

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
import tqdm
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
        # Read configuration from CONSERV_API_KEYS env var (JSON array)
        customers = []
        env_file_path = os.path.join(home_directory, ".env")
        
        # First try to read from .env file
        try:
            with open(env_file_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("CONSERV_API_KEYS=") and "=" in line:
                        _, json_value = line.split("=", 1)
                        api_keys_list = json.loads(json_value.strip())
                        for entry in api_keys_list:
                            customers.append({
                                'customer_id': entry['id'],
                                'api_key': entry['key']
                            })
                        break
        except FileNotFoundError:
            raise ValueError(
                    f"No .env file found at {env_file_path}. If running from a subdirectory, set home_directory parameter to parent directory (e.g., home_directory='..')."
            )

        if not customers:
            raise ValueError("No Conserv API keys found in .env file")

        self.base_url = "https://api.conserv.io"
        self.customers = customers
        self.logger = logger or logging.getLogger(__name__)
        self.max_window_days = 7  # API limitation
        self.poll_interval_seconds = 10  # How often to check export status (reduced from 30s)
        self.max_wait_minutes = 15  # Maximum time to wait for export completion
        
        # Note: Conserv exports data in 15-minute increments natively.
        # This is consistent with Coris (MinReadingSpacing=900) and LI-COR (downsampled to 15 min).
        
        if logger:
            logger.info(f"Initialized Conserv client with {len(customers)} customers")

    def _make_api_request(
        self, method: str, endpoint: str, api_key: str, **kwargs
    ) -> requests.Response:
        """
        Make a request to the Conserv API with proper headers and error handling.
        SSL issues are expected so we handle them here.

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
            # Try the regular request. This is preferred even if we expect it to fail.
            response = requests.request(
                method, url, headers=headers, verify=True, **kwargs
            )
            response.raise_for_status()
            return response
        
        # SSL verification fails for api.conserv.io, retry without verification.
        # Suppress verbose logging since this is expected behavior.
        except requests.exceptions.SSLError as e:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            
            try:
                response = requests.request(
                    method, url, headers=headers, verify=False, **kwargs
                )
                response.raise_for_status()
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
    ) -> Optional[str]:
        """
        Launch a data export for a specific time window with error handling.

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
        Optional[str]
            Export UUID for polling status, or None if launch failed
        """

        # Validate window size
        window_days = (end_time - start_time).days
        if window_days > self.max_window_days:
            raise ValueError(
                f"Export window of {window_days} days exceeds maximum of {self.max_window_days} days"
            )

        response = self._make_api_request(
            "POST", 
            "/v1/sensors/export", 
            api_key, 
            json={"start": start_time.isoformat(), "end": end_time.isoformat()}
        )

        result = response.json()

        uuid = result.get("uuid")
        if not uuid:
            raise ValueError(f"No UUID returned from export launch: {result}")

        self.logger.info(f"Conserv export launched: {uuid}")
        return uuid

    def export_data(
        self,
        api_key: str,
        customer_id: str | int,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
    ) -> Optional[polars.DataFrame]:
        """
        Complete export workflow: launch → wait → download for a single customer.

        Parameters
        ----------
        api_key : str
            Customer API key
        customer_id : str | int
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
        
        self.logger.info(
            f"Starting export for customer {customer_id}: {start_time} to {end_time}"
        )

        # Launch export
        uuid = self.launch_export(api_key, start_time, end_time)
        if uuid is None:
            return None

        # Wait for completion - inline wait_for_export_completion
        wait_start_time = time.time()
        max_wait_seconds = self.max_wait_minutes * 60
        query_utc = int(datetime.datetime.now(datetime.timezone.utc).timestamp())

        while time.time() - wait_start_time < max_wait_seconds:
            # Check export status - inline check_export_status
            response = self._make_api_request(
                "GET", f"/v1/sensors/export/{uuid}/status", api_key
            )
            result = response.json()
            status = result.get("status")
            if not status:
                raise ValueError(f"No status returned for export {uuid}: {result}")

            if status == "completed":
                export_duration = time.time() - wait_start_time
                self.logger.info(f"Export {uuid} completed successfully in {export_duration:.1f} seconds")
                break
            elif status == "failed":
                self.logger.error(f"Export {uuid} failed")
                return None
            elif status in ("pending", "processing", "queued"):
                # Don't log individual status checks to reduce noise
                time.sleep(self.poll_interval_seconds)
            else:
                raise ValueError(f"Unknown export status: {status}")
        else:
            raise TimeoutError(
                f"Export {uuid} did not complete within {self.max_wait_minutes} minutes"
            )

        # Add delay to ensure download URL is ready after export completion
        time.sleep(5)

        # Get download URL - inline get_download_url
        response = self._make_api_request(
            "POST", f"/v1/sensors/export/{uuid}/download", api_key
        )
        result = response.json()
        download_url = result.get("url")
        if not download_url:
            raise ValueError(f"No download URL returned for export {uuid}: {result}")

        # Download and parse data - inline download_export_data
        self.logger.info("Downloading export data from S3")
        response = requests.get(download_url)
        response.raise_for_status()
        csv_content = response.content
        # Read all columns as strings to avoid schema mismatches during concatenation
        df = polars.read_csv(io.BytesIO(csv_content), infer_schema=False)
        self.logger.info(f"Downloaded {df.shape[0]} rows, {df.shape[1]} columns")

        if df.shape[0] == 0:
            self.logger.info(f"No data found for customer {customer_id}")
            return None

            # Add customer_id and QueryUTC columns
        df = df.with_columns(
            [
                polars.lit(customer_id).alias("customer_id"),
                polars.lit(query_utc).alias("QueryUTC"),
            ]
        )

        self.logger.info(
            f"Successfully exported {df.shape[0]} rows for customer {customer_id}"
        )
        return df

    def export_data_chunked(
        self,
        api_key: str,
        customer_id: str | int,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
    ) -> List[polars.DataFrame]:
        """
        Export data in chunks to handle the 7-day window limitation.

        Parameters
        ----------
        api_key : str
            Customer API key
        customer_id : str | int
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

    def get_current_data(self, hours_back: int = 6, test: bool = False) -> Optional[polars.DataFrame]:
        """
        Get current data for testing purposes without saving to samples directory.
        
        Parameters
        ----------
        hours_back : int, default=6
            How many hours back to retrieve data
        test : bool, default=False
            If True, only use the first customer for testing
            
        Returns
        -------
        Optional[polars.DataFrame]
            Current sensor data in standardized format, or None if no data
        """
        end_time = datetime.datetime.now(datetime.timezone.utc)
        start_time = end_time - datetime.timedelta(hours=hours_back)
        return self.get_historical_data(int(start_time.timestamp()), int(end_time.timestamp()), test=test)

    def get_current_readings(self, test: bool = False) -> Optional[polars.DataFrame]:
        """
        Get current readings from all customers (or just the first customer if test=True) for the last 30 minutes.
        
        Parameters
        ----------
        test : bool, default=False
            If True, only use the first customer for testing
            
        Returns
        -------
        Optional[polars.DataFrame]
            Current sensor data in standardized format from all customers, or None if no data
        """
        if self.logger:
            self.logger.info("Fetching current readings for last 30 minutes")
        
        # Filter customers based on test mode
        customers_to_process = self.customers
        if test:
            customers_to_process = self.customers[:1]  # Only first customer
            if self.logger:
                self.logger.info(f"Running in test mode - only processing first customer: {customers_to_process[0]['customer_id']}")
        
        all_customer_data = []
        
        # Process each customer with progress bar
        pbar = tqdm.tqdm(total=len(customers_to_process), desc="Gathering Conserv current readings")
        for customer in customers_to_process:
            try:
                if self.logger:
                    self.logger.info(f"Fetching current data for customer {customer['customer_id']}")
                
                # Calculate time range (last 30 minutes)
                end_time = datetime.datetime.now(datetime.timezone.utc)
                start_time = end_time - datetime.timedelta(minutes=30)
                
                # Use export_data to get recent data
                customer_data = self.export_data(
                    api_key=customer['api_key'],
                    customer_id=customer['customer_id'],
                    start_time=start_time,
                    end_time=end_time
                )
                
                if customer_data is not None and len(customer_data) > 0:
                    all_customer_data.append(customer_data)
                    
                    if self.logger:
                        self.logger.info(f"Retrieved {len(customer_data)} current records for customer {customer['customer_id']}")
                else:
                    if self.logger:
                        self.logger.info(f"No current data available for customer {customer['customer_id']}")
                        
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Failed to fetch current data for customer {customer['customer_id']}: {e}")
                # Continue with other customers
                continue
            finally:
                pbar.update(1)
        
        pbar.close()
        
        # Combine all customer data
        if all_customer_data:
            combined_data = polars.concat(all_customer_data, how="vertical")
            
            # Filter to only the last reading per sensor name/serial/customer combination
            # Take the last (most recent) reading for each sensor (assuming chronological order from API)
            combined_data = (
                combined_data
                .group_by(["customer_id", "Sensor Serial", "Sensor Name"])
                .last()
            )
            
            if self.logger:
                self.logger.info(f"Filtered to {len(combined_data)} most recent readings per sensor")
            
            # Add Historical=False for current readings and transform to standardized schema
            combined_data = combined_data.with_columns(polars.lit(False).alias("Historical"))
            combined_data = self.transform_to_standardized_schema(combined_data)
            
            if self.logger:
                self.logger.info(f"Successfully combined current readings from {len(all_customer_data)} customers: {len(combined_data)} total records")
            
            return combined_data
        else:
            if self.logger:
                self.logger.warning("No current readings retrieved from any customer")
            return None



    def get_historical_data(self, start_utc: int, end_utc: int, test: bool = False) -> Optional[polars.DataFrame]:
        """
        Get historical data for all customers for a specific time period.

        Parameters
        ----------
        start_utc : int
            Start time as UTC timestamp in seconds
        end_utc : int
            End time as UTC timestamp in seconds
        test : bool, default=False
            If True, only use the first customer for testing

        Returns
        -------
        Optional[polars.DataFrame]
            Combined historical data from all customers (or just first if test=True) for the specified period
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
        
        # Filter customers based on test mode
        customers_to_process = self.customers
        if test:
            customers_to_process = self.customers[:1]  # Only first customer
            if self.logger:
                self.logger.info(f"Running in test mode - only processing first customer: {customers_to_process[0]['customer_id']}")

        # Process each customer with progress bar
        pbar = tqdm.tqdm(total=len(customers_to_process), desc="Gathering Conserv historical data")
        for customer in customers_to_process:
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
            finally:
                pbar.update(1)

        pbar.close()

        # Combine all customer data
        if all_customer_data:
            combined_data = polars.concat(all_customer_data, how="vertical")            

            # Add Historical column and transform to standardized schema for this customer
            combined_data = combined_data.with_columns(polars.lit(True).alias("Historical"))
            combined_data = self.transform_to_standardized_schema(combined_data)
            
            if self.logger:
                self.logger.info(
                    f"Successfully combined data from {len(all_customer_data)} customers: {combined_data.shape[0]} total records"
                )
            return combined_data
        else:
            if self.logger:
                self.logger.warning("No data retrieved from any customer")
            return None

    def transform_to_standardized_schema(
        self, df: polars.DataFrame
    ) -> polars.DataFrame:
        """
        Transform Conserv API data to standardized schema.

        Parameters
        ----------
        df : polars.DataFrame
            Raw Conserv data with columns: Sensor Name, Time, Temperature (°C), Humidity (%), customer_id, source

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

        # source - rename existing lowercase column to uppercase
        if "source" in df.columns:
            df = df.rename({"source": "Source"})
        else:
            df = df.with_columns(polars.lit("Conserv").alias("Source"))

        # SensorReadingF - handle potential encoding issues in column names
        temp_col = None
        humidity_col = None
        
        for col in df.columns:
            if "Temperature" in col and ("°C" in col or "Â°C" in col):
                temp_col = col
            elif "Humidity" in col and "%" in col:
                humidity_col = col
        
        if temp_col is None:
            raise ValueError(f"Temperature column not found. Available columns: {df.columns}")
        if humidity_col is None:
            raise ValueError(f"Humidity column not found. Available columns: {df.columns}")
        
        df = df.with_columns(
            ((polars.col(temp_col).cast(polars.Float32) * 9 / 5) + 32).alias("SensorReadingF")
        )

        # SensorReadingRh
        df = df.rename({humidity_col: "SensorReadingRh"})
        df = df.with_columns(polars.col("SensorReadingRh").cast(polars.Float32))

        # SensorReadingUTC - handle ISO format like "2025-11-15T18:51:01.729Z"
        df = df.with_columns(
            polars.col("Time")
            .str.replace("Z$", "")  # Remove trailing Z if present
            .str.strptime(polars.Datetime, "%Y-%m-%dT%H:%M:%S%.f")
            .dt.timestamp("ms")
            .floordiv(1000)  # Convert milliseconds to seconds
            .alias("SensorReadingUTC")
        )

        # DeviceID
        df = df.with_columns([
            polars.concat_str([
                polars.lit("conserv:"),
                polars.col("customer_id").cast(polars.String),
                polars.lit(":"),
                polars.col("Sensor Serial")
            ]).alias("DeviceId"),
            polars.col("Sensor Name").alias("DeviceName"),
        ])

        # DeviceName
        df = df.with_columns(
            df["Sensor Name"].alias("DeviceName")
        )

        # The data comes in already in device oriented format. We need to transform to sensor oriented format.
        # Transform each row into two rows: one for Temperature sensor and one for RH sensor
        
        # Create Temperature sensor rows
        temp_rows = df.select([
            polars.col("Historical"),
            polars.col("SensorReadingUTC"),
            polars.col("Source"),
            polars.col("DeviceId").alias("DeviceID"),
            polars.col("DeviceName"),
            polars.concat_str([
                polars.col("DeviceId"),
                polars.lit(":Temperature")
            ]).alias("SensorID"),
            polars.col("DeviceName").alias("SensorName"),
            polars.lit("Temperature").alias("SensorType"),
            polars.col("SensorReadingF"),
            polars.lit(None, dtype=polars.Float32).alias("SensorReadingRh"),
            #polars.col("customer_id"),
            polars.col("QueryUTC")
        ])
        
        # Create RH sensor rows
        rh_rows = df.select([
            polars.col("Historical"),
            polars.col("SensorReadingUTC"),
            polars.col("Source"),
            polars.col("DeviceId").alias("DeviceID"),
            polars.col("DeviceName"),
            polars.concat_str([
                polars.col("DeviceId"),
                polars.lit(":RH")
            ]).alias("SensorID"),
            polars.col("DeviceName").alias("SensorName"),
            polars.lit("RH").alias("SensorType"),
            polars.lit(None, dtype=polars.Float32).alias("SensorReadingF"),
            polars.col("SensorReadingRh"),
            #polars.col("customer_id"),
            polars.col("QueryUTC")
        ])
        
        # Combine temperature and RH rows
        df = polars.concat([temp_rows, rh_rows], how="vertical")

        if self.logger:
            self.logger.info(f"Conserv transformation complete: {df.shape[0]} records (doubled from device to sensor format)")

        return df

    def sample_raw_data(self):
        """
        Generate sample raw API data for testing and exploration.
        
        Downloads actual data from the first customer and saves it to samples/conserv/ directory.
        """
        if not self.customers:
            raise ValueError("No Conserv customers configured")
        
        # Try each customer until one works
        end_time = datetime.datetime.now(datetime.timezone.utc)
        start_time = end_time - datetime.timedelta(hours=6)
        
        print(f"📅 Time range: {start_time} to {end_time}")
        
        # Use the first available customer. Ideally this will be customer 333 since it has data.
        if not self.customers:
            raise ValueError("No Conserv customers configured")
        
        target_customer = self.customers[0]
        
        customer_id = target_customer['customer_id']
        api_key = target_customer['api_key']
        
        print(f"🔍 Downloading sample data for Conserv customer {customer_id}...")
        
        # Get the full data using export_data
        raw_data = self.export_data(api_key, customer_id, start_time, end_time)
        
        if raw_data is not None:
            print(f"✅ Successfully downloaded data for customer {customer_id}: {raw_data.shape[0]} rows")
            
            # Save raw data as CSV
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_filename = f"raw_data_customer{customer_id}_{timestamp}.csv"
            csv_filepath = Path(__file__).parent.parent / "samples" / "conserv" / csv_filename
            csv_filepath.parent.mkdir(parents=True, exist_ok=True)
            raw_data.write_csv(csv_filepath)
            
            print(f"💾 Saved raw data to: {csv_filepath}")
            
            # Print summary
            print(f"\n📋 Sample Data Summary:")
            print(f"   • Customer ID: {customer_id}")
            print(f"   • Rows: {raw_data.shape[0]}")
            print(f"   • Columns: {raw_data.shape[1]}")
            print(f"   • Column names: {', '.join(raw_data.columns)}")
            print(f"   • Time range: {start_time} to {end_time}")
            print(f"   • Files saved to: samples/conserv/")
        else:
            raise Exception(f"Customer {customer_id} failed to download data")



