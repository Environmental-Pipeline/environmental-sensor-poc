"""
Hobolink API Client Module

This module provides a client interface for the Hobolink/LI-COR environmental monitoring API.
It handles sensor data retrieval, historical data queries, and data transformation
to maintain compatibility with the EnvironmentData schema.

API Endpoints:
- GET https://api.licor.cloud/v2/devices?includeSensors=true Identify available sensors.
- GET https://api.licor.cloud/v2/data?deviceSerialNumber=X&sensorSerialNumber=Y&startTime=Z&endTime=W Get sensor data.
Authentication: Bearer Token via HOBOLINK_API_KEY environment variable
"""

import os
import requests
import polars
import datetime
import logging
import tqdm
from typing import List, Dict, Optional, Any


class HobolinkClient:
    """
    Client for interacting with the Hobolink/LI-COR environmental monitoring API.
    
    This client handles:
    - Device and sensor metadata retrieval
    - Historical sensor data queries
    - Current sensor readings
    - Data transformation to standardized schema
    """
    
    def __init__(self, api_key: str, logger: Optional[logging.Logger] = None):
        """
        Initialize the Hobolink API client.
        
        Parameters
        ----------
        api_key : str
            Hobolink API key for Bearer token authentication
        logger : Optional[logging.Logger]
            Logger instance for recording API interactions
        """
        self.api_key = api_key
        self.logger = logger or logging.getLogger(__name__)
        self.base_url = "https://api.licor.cloud/v2"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # Configure logging for detailed API response inspection
        if self.logger.level == logging.NOTSET:
            self.logger.setLevel(logging.INFO)
    
    def get_current_utc(self) -> int:
        """
        Get the current UTC timestamp in seconds.
        
        Returns
        -------
        int : Current UTC timestamp
        """
        return int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    
    def get_current_utc_ms(self) -> int:
        """
        Get the current UTC timestamp in milliseconds (for Hobolink API).
        
        Returns
        -------
        int : Current UTC timestamp in milliseconds
        """
        return int(datetime.datetime.now(datetime.timezone.utc).timestamp() * 1000)
    
    def _make_api_request(self, endpoint: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Make a standardized API request with comprehensive logging.
        
        Parameters
        ----------
        endpoint : str
            API endpoint (without base URL)
        params : Dict[str, Any], optional
            Query parameters for the request
            
        Returns
        -------
        Dict[str, Any]
            Parsed JSON response
            
        Raises
        ------
        Exception
            If API request fails
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        
        # Log request info
        self.logger.info(f"Making API request to: {endpoint}")
        if params:
            self.logger.debug(f"Parameters: {params}")
        
        try:
            response = requests.get(url, headers=self.headers, params=params)
            
            if response.ok:
                return response.json()
            else:
                error_msg = f"API request failed: {response.status_code} - {response.text}"
                self.logger.error(error_msg)
                raise Exception(error_msg)
                
        except requests.exceptions.RequestException as e:
            error_msg = f"Request exception: {str(e)}"
            self.logger.error(error_msg)
            raise Exception(error_msg)
    
    def get_devices(self, include_sensors: bool = True) -> Dict[str, Any]:
        """
        Get the list of all devices from the Hobolink API.
        
        Parameters
        ----------
        include_sensors : bool, default=True
            Whether to include sensor information with devices
            
        Returns
        -------
        Dict[str, Any]
            Raw device data from API for inspection
        """
        self.logger.info("get_devices")
        
        params = {}
        if include_sensors:
            params["includeSensors"] = "true"
        
        response_data = self._make_api_request("devices", params)
        
        # Log basic device count for tracking
        if isinstance(response_data, list):
            self.logger.info(f"Found {len(response_data)} devices")
        elif isinstance(response_data, dict) and "devices" in response_data:
            devices = response_data["devices"]
            self.logger.info(f"Found {len(devices)} devices")
        
        return response_data
    
    def get_sensor_data(self, device_serial: str, sensor_serial: str, 
                       start_time_ms: int, end_time_ms: int) -> Dict[str, Any]:
        """
        Get sensor data for a specific device and sensor.
        
        Note: Hobolink API limits historical data queries to maximum 1 year (365 days).
        If the requested date range exceeds this limit, it will be automatically capped.
        
        Parameters
        ----------
        device_serial : str
            Device serial number
        sensor_serial : str
            Sensor serial number
        start_time_ms : int
            Start time as UTC timestamp in milliseconds
        end_time_ms : int
            End time as UTC timestamp in milliseconds
            
        Returns
        -------
        Dict[str, Any]
            Raw sensor data from API for inspection
        """
        # Validate and cap date range - Hobolink API limits to less than 1 year maximum
        max_days = 364  # Use 364 days to be safe with API limitations
        max_milliseconds = max_days * 24 * 60 * 60 * 1000
        date_range_ms = end_time_ms - start_time_ms
        
        if date_range_ms > max_milliseconds:
            original_start = start_time_ms
            start_time_ms = end_time_ms - max_milliseconds
            self.logger.warning(
                f"Date range ({date_range_ms // (24 * 60 * 60 * 1000)} days) exceeds Hobolink API limit of {max_days} days. "
                f"Capping to most recent {max_days} days for {device_serial}/{sensor_serial}."
            )
        
        self.logger.info(f"get_sensor_data for device {device_serial}, sensor {sensor_serial}")
        
        params = {
            "deviceSerialNumber": device_serial,
            "sensorSerialNumber": sensor_serial,
            "startTime": start_time_ms,
            "endTime": end_time_ms
        }
        
        response_data = self._make_api_request("data", params)
        
        # Check for pagination - this is an unhandled case that could mean missing data
        if isinstance(response_data, dict) and response_data.get("moreResults") is True:
            error_msg = (
                f"CRITICAL: API returned moreResults=true for {device_serial}/{sensor_serial}. "
                f"This indicates pagination/truncated results that are not currently handled. "
                f"Data may be incomplete!"
            )
            self.logger.error(error_msg)
            raise Exception(error_msg)
        
        # Log basic data retrieval info
        if isinstance(response_data, dict):
            if "message" in response_data:
                self.logger.info(f"API message: {response_data['message']}")
            
            # Log data count for tracking
            for key in ["data", "readings", "measurements", "values", "results", "sensors"]:
                if key in response_data and isinstance(response_data[key], list):
                    self.logger.info(f"Retrieved {len(response_data[key])} items from '{key}'")
                    break
        
        return response_data
    
    def transform_to_standardized_schema(self, raw_data: Dict[str, Any], 
                                       device_metadata: polars.DataFrame = None) -> polars.DataFrame:
        """
        Transform Hobolink API response data to standardized EnvironmentData schema.
        
        Note: This method assumes moreResults=false (complete data). If moreResults=true
        is ever encountered, get_sensor_data() will raise an exception since pagination
        is not currently handled.
        
        Parameters
        ----------
        raw_data : Dict[str, Any]
            Raw API response from get_sensor_data
        device_metadata : polars.DataFrame, optional
            Device metadata to attach to readings
            
        Returns
        -------
        polars.DataFrame
            Transformed data matching the standardized schema
        """
        if not isinstance(raw_data, dict) or "sensors" not in raw_data:
            self.logger.warning("No sensor data found in response")
            return polars.DataFrame()
        
        all_readings = []
        current_utc = self.get_current_utc()
        
        for sensor_info in raw_data["sensors"]:
            sensor_serial = sensor_info.get("sensorSerialNumber", "unknown")
            
            for measurement_data in sensor_info.get("data", []):
                measurement_type = measurement_data.get("measurementType", "unknown")
                units = measurement_data.get("units", "")
                records = measurement_data.get("records", [])
                
                # Convert records to DataFrame
                if records:
                    # Create DataFrame from records (timestamp_ms, value pairs)
                    df = polars.DataFrame({
                        "timestamp_ms": [record[0] for record in records],
                        "value": [record[1] for record in records]
                    })
                    
                    # Add metadata
                    df = df.with_columns([
                        # Convert timestamp from ms to seconds
                        (polars.col("timestamp_ms") / 1000).cast(polars.Int64).alias("SensorReadingUTC"),
                        polars.lit(sensor_serial).alias("SensorID_Hobolink"),
                        polars.lit(measurement_type).alias("SensorType"),
                        polars.lit(units).alias("SensorUnits"),
                        polars.lit(current_utc).alias("QueryUTC"),
                        polars.lit("hobolink").alias("source"),
                        
                        # Standardized schema columns (set to None initially)
                        polars.lit(None, dtype=polars.Int32).alias("SensorID_Coris"),
                        polars.lit(None, dtype=polars.String).alias("SensorID_Conserv"),
                        polars.lit(None, dtype=polars.Int32).alias("customer_id"),
                        polars.lit(None, dtype=polars.Int32).alias("DeviceID_Coris"),
                        polars.lit(None, dtype=polars.String).alias("SensorName"),
                        polars.lit(None, dtype=polars.String).alias("DeviceName"),
                    ])
                    
                    # Map measurement types to standardized reading columns
                    # Focus on temperature and humidity only (matching Coris behavior)
                    if measurement_type.lower() in ["temperature", "temp"]:
                        if "°F" in units or "F" in units:
                            # Already in Fahrenheit - keep this sensor
                            df = df.with_columns(polars.col("value").cast(polars.Float32).alias("SensorReadingF"))
                        elif "°C" in units or "C" in units:
                            # Celsius detected - skip this sensor and log suppression
                            self.logger.info(f"Suppressing Celsius temperature sensor: {sensor_serial} "
                                           f"(measurement_type: {measurement_type}, units: {units})")
                            continue
                        else:
                            # Unknown temperature units - skip and log
                            self.logger.warning(f"Unknown temperature units for sensor {sensor_serial}: "
                                              f"measurement_type={measurement_type}, units='{units}' - skipping")
                            continue
                    elif measurement_type.lower() in ["rh", "humidity", "relative humidity"]:
                        df = df.with_columns(polars.col("value").cast(polars.Float32).alias("SensorReadingRh"))
                    # elif measurement_type.lower() in ["dew point", "dewpoint"]:
                    #     if "°F" in units or "F" in units:
                    #         df = df.with_columns(polars.col("value").cast(polars.Float32).alias("SensorReadingDewPointF"))
                    #     else:
                    #         df = df.with_columns(polars.col("value").cast(polars.Float64).alias("SensorReadingDewPointC"))
                    else:
                        # Skip other sensor types - only collect temperature and humidity
                        continue
                    
                    # Drop the temporary columns
                    df = df.drop(["timestamp_ms", "value"])
                    
                    all_readings.append(df)
        
        if not all_readings:
            return polars.DataFrame()
        
        # Concatenate all readings
        result = polars.concat(all_readings, how="diagonal")
        
        # Add device metadata if available
        if device_metadata is not None and not device_metadata.is_empty():
            # Try to match by device serial number (extract device part from sensor serial)
            # Sensor serials are like "22179174-2", device serials are like "22202141"
            result = result.with_columns([
                polars.col("SensorID_Hobolink").str.split("-").list.first().alias("DeviceSerial_Temp")
            ])
            
            # Join with device metadata
            if "deviceSerialNumber" in device_metadata.columns:
                device_meta_subset = device_metadata.select([
                    "deviceSerialNumber", "deviceName"
                ]).unique()
                
                result = result.join(
                    device_meta_subset,
                    left_on="DeviceSerial_Temp",
                    right_on="deviceSerialNumber",
                    how="left"
                ).with_columns([
                    polars.col("deviceName").alias("DeviceName")
                ]).drop(["DeviceSerial_Temp", "deviceName", "deviceSerialNumber"])
        
        self.logger.info(f"Transformed {len(result)} readings from Hobolink data")
        return result
    
    def get_devices_as_dataframe(self, include_sensors: bool = True, 
                               out_of_scope: List[str] = None,
                               testing: bool = False,
                               testing_device_limit: int = 3) -> polars.DataFrame:
        """
        Get devices and sensors as a standardized DataFrame similar to Coris client.
        
        Parameters
        ----------
        include_sensors : bool, default=True
            Whether to include sensor information
        out_of_scope : List[str], optional
            List of device name prefixes to exclude
        testing : bool, default=False
            If True, limit to first few devices for testing
        testing_device_limit : int, default=3
            Number of devices to include when testing mode is enabled
            
        Returns
        -------
        polars.DataFrame
            Device and sensor data with standardized schema
        """
        out_of_scope = out_of_scope or []
        current_utc = self.get_current_utc()
        
        self.logger.info("get_devices_as_dataframe")
        devices_response = self.get_devices(include_sensors=include_sensors)
        
        if not isinstance(devices_response, dict) or "devices" not in devices_response:
            self.logger.error("Invalid device response format")
            return polars.DataFrame()
        
        devices = devices_response["devices"]
        
        # Filter out-of-scope devices
        if out_of_scope:
            devices = [d for d in devices 
                      if not any(d.get("deviceName", "").startswith(prefix) for prefix in out_of_scope)]
        
        # Apply testing limits
        if testing and testing_device_limit:
            devices = devices[:testing_device_limit]
        
        device_records = []
        
        for device in devices:
            device_serial = device.get("deviceSerialNumber")
            device_name = device.get("deviceName", "Unknown Device")
            
            if include_sensors and "sensors" in device:
                # Create a record for each sensor (temperature and humidity only)
                for sensor in device["sensors"]:
                    sensor_serial = sensor.get("sensorSerialNumber")
                    measurement_type = sensor.get("measurementType", "Unknown")
                    units = sensor.get("units", "")
                    latest_value = sensor.get("latest")
                    
                    # Filter to only temperature and humidity sensors (matching Coris behavior)
                    if measurement_type.lower() not in ["temperature", "temp", "rh", "humidity", "relative humidity"]:
                        continue
                    
                    device_records.append({
                        "DeviceID_Hobolink": device_serial,
                        "DeviceName": device_name,
                        "SensorID_Hobolink": sensor_serial,
                        "SensorName": f"{device_name}_{measurement_type}",
                        "SensorType": measurement_type,
                        "SensorUnits": units,
                        "LatestReading": latest_value,
                        "ProductCode": device.get("productCode"),
                        "LastConnectionTime": device.get("lastConnectionTime"),
                        "LoggingState": device.get("loggingState"),
                        "Alarmed": device.get("alarmed", False),
                        "UnitSystem": device.get("unitSystem"),
                    })
            else:
                # Create a record for the device only
                device_records.append({
                    "DeviceID_Hobolink": device_serial,
                    "DeviceName": device_name,
                    "SensorID_Hobolink": None,
                    "SensorName": device_name,
                    "SensorType": None,
                    "SensorUnits": None,
                    "LatestReading": None,
                    "ProductCode": device.get("productCode"),
                    "LastConnectionTime": device.get("lastConnectionTime"),
                    "LoggingState": device.get("loggingState"),
                    "Alarmed": device.get("alarmed", False),
                    "UnitSystem": device.get("unitSystem"),
                })
        
        if not device_records:
            return polars.DataFrame()
        
        # Create DataFrame
        result = polars.DataFrame(device_records)
        
        # Add standardized schema columns
        result = result.with_columns([
            polars.lit(current_utc).alias("QueryUTC"),
            polars.lit("hobolink").alias("source"),
            polars.lit(None, dtype=polars.Int32).alias("SensorID_Coris"),
            polars.lit(None, dtype=polars.String).alias("SensorID_Conserv"),
            polars.lit(None, dtype=polars.Int32).alias("customer_id"),
            polars.lit(None, dtype=polars.Int32).alias("DeviceID_Coris"),
        ])
        
        self.logger.info(f"Retrieved {len(result)} device/sensor combinations")
        return result
    
    def get_historical_data_bulk(self, start_utc: int, end_utc: int,
                               out_of_scope: List[str] = None,
                               testing: bool = False,
                               testing_device_limit: int = 2,
                               testing_sensors_per_device: int = 2) -> List[polars.DataFrame]:
        """
        Get historical data for all devices and sensors in bulk.
        
        Note: Hobolink API limits historical data queries to maximum 1 year (365 days).
        If the requested date range exceeds this limit, it will be automatically capped.
        
        Parameters
        ----------
        start_utc : int
            Start time as UTC timestamp in seconds
        end_utc : int
            End time as UTC timestamp in seconds
        out_of_scope : List[str], optional
            List of device name prefixes to exclude
        testing : bool, default=False
            If True, limit to first few devices and sensors
        testing_device_limit : int, default=2
            Number of devices to include when testing
        testing_sensors_per_device : int, default=2
            Number of sensors per device when testing
            
        Returns
        -------
        List[polars.DataFrame]
            List of DataFrames containing historical readings
        """
        # Validate and cap date range - Hobolink API limits to less than 1 year maximum
        max_days = 364  # Use 364 days to be safe with API limitations
        max_seconds = max_days * 24 * 60 * 60
        date_range_seconds = end_utc - start_utc
        
        if date_range_seconds > max_seconds:
            original_start = start_utc
            start_utc = end_utc - max_seconds
            self.logger.warning(
                f"Date range ({date_range_seconds // 86400} days) exceeds Hobolink API limit of {max_days} days. "
                f"Capping to most recent {max_days} days."
            )
            self.logger.info(f"Adjusted start_utc from {original_start} to {start_utc}")
        
        self.logger.info(f"Using date range: {(end_utc - start_utc) // 86400} days of historical data")
        devices_df = self.get_devices_as_dataframe(
            include_sensors=True, 
            out_of_scope=out_of_scope,
            testing=testing,
            testing_device_limit=testing_device_limit
        )
        
        if devices_df.is_empty():
            self.logger.warning("No devices found for bulk historical data retrieval")
            return []
        
        # Convert timestamps to milliseconds for API
        start_utc_ms = start_utc * 1000
        end_utc_ms = end_utc * 1000
        
        readings_list = []
        
        # Get unique device/sensor combinations
        device_sensor_combinations = (
            devices_df
            .filter(polars.col("SensorID_Hobolink").is_not_null())
            .select(["DeviceID_Hobolink", "SensorID_Hobolink", "DeviceName", "SensorType"])
            .unique()
        )
        
        if testing:
            # Group by device and limit sensors per device
            limited_combinations = []
            for device_id in device_sensor_combinations["DeviceID_Hobolink"].unique():
                device_sensors = device_sensor_combinations.filter(
                    polars.col("DeviceID_Hobolink") == device_id
                ).head(testing_sensors_per_device)
                limited_combinations.append(device_sensors)
            
            if limited_combinations:
                device_sensor_combinations = polars.concat(limited_combinations)
        
        total_combinations = len(device_sensor_combinations)
        self.logger.info(f"Retrieving historical data for {total_combinations} device/sensor combinations")
        
        # Use progress bar for bulk operations
        pbar = tqdm.tqdm(total=total_combinations, desc="Gathering Hobolink readings")
        
        for row in device_sensor_combinations.iter_rows(named=True):
            device_serial = row["DeviceID_Hobolink"]
            sensor_serial = row["SensorID_Hobolink"]
            device_name = row["DeviceName"]
            sensor_type = row["SensorType"]
            
            try:
                # Get raw data from API
                raw_data = self.get_sensor_data(
                    device_serial=device_serial,
                    sensor_serial=sensor_serial,
                    start_time_ms=start_utc_ms,
                    end_time_ms=end_utc_ms
                )
                
                # Transform to standardized schema
                if raw_data and "sensors" in raw_data and raw_data["sensors"]:
                    transformed_data = self.transform_to_standardized_schema(
                        raw_data, 
                        device_metadata=devices_df.filter(
                            polars.col("DeviceID_Hobolink") == device_serial
                        ).select(["deviceSerialNumber", "deviceName"]).rename({
                            "DeviceID_Hobolink": "deviceSerialNumber",
                            "DeviceName": "deviceName"
                        })
                    )
                    
                    if not transformed_data.is_empty():
                        readings_list.append(transformed_data)
                        self.logger.info(f"Retrieved {len(transformed_data)} readings for {device_name}/{sensor_type}")
                
            except Exception as e:
                self.logger.warning(f"Failed to get data for {device_serial}/{sensor_serial}: {str(e)}")
            
            pbar.update(1)
        
        pbar.close()
        
        self.logger.info(f"Successfully retrieved historical data from {len(readings_list)} device/sensor combinations")
        return readings_list
    
    def get_current_readings(self, out_of_scope: List[str] = None,
                           testing: bool = False,
                           testing_device_limit: int = 3) -> polars.DataFrame:
        """
        Get current sensor readings from the Hobolink API.
        
        Parameters
        ----------
        out_of_scope : List[str], optional
            List of device name prefixes to exclude
        testing : bool, default=False
            If True, limit to testing devices only
        testing_device_limit : int, default=3
            Number of devices to include when testing
            
        Returns
        -------
        polars.DataFrame
            Current sensor readings with standardized schema
        """
        devices_df = self.get_devices_as_dataframe(
            include_sensors=True,
            out_of_scope=out_of_scope,
            testing=testing,
            testing_device_limit=testing_device_limit
        )
        
        if devices_df.is_empty():
            self.logger.warning("No devices found for current readings")
            return polars.DataFrame()
        
        # Filter to only sensors that have latest readings
        current_readings = devices_df.filter(
            polars.col("LatestReading").is_not_null()
        )
        
        if current_readings.is_empty():
            self.logger.warning("No current readings available")
            return polars.DataFrame()
        
        # Transform latest readings to match historical data format
        current_utc = self.get_current_utc()
        
        # Create standardized current readings
        result = current_readings.with_columns([
            # Use current timestamp for reading time
            polars.lit(current_utc).alias("SensorReadingUTC"),
            polars.col("LatestReading").cast(polars.Float64).alias("SensorReading"),
        ])
        
        # Map specific measurement types to appropriate columns  
        # Only include Fahrenheit temperatures (matching Coris behavior)
        result = result.with_columns([
            polars.when(polars.col("SensorType").str.to_lowercase().str.contains("temperature"))
            .then(
                polars.when(polars.col("SensorUnits").str.contains("°F|F"))
                .then(polars.col("LatestReading").cast(polars.Float32))  # Only Fahrenheit
                .otherwise(None)  # Skip Celsius and unknown temperature units
            )
            .otherwise(None)
            .alias("SensorReadingF"),
            
            polars.when(polars.col("SensorType").str.to_lowercase().str.contains("rh|humidity"))
            .then(polars.col("LatestReading").cast(polars.Float32))
            .otherwise(None)
            .alias("SensorReadingRh"),
        ])
        
        # Log suppressed Celsius temperature sensors
        celsius_sensors = result.filter(
            (polars.col("SensorType").str.to_lowercase().str.contains("temperature")) &
            (polars.col("SensorUnits").str.contains("°C|C")) &
            (polars.col("SensorReadingF").is_null())
        )
        
        if len(celsius_sensors) > 0:
            for row in celsius_sensors.select(["SensorID_Hobolink", "SensorType", "SensorUnits"]).iter_rows():
                sensor_id, sensor_type, units = row
                self.logger.info(f"Suppressing Celsius temperature sensor in current readings: {sensor_id} "
                               f"(measurement_type: {sensor_type}, units: {units})")
        
        # Select only the columns that match the standardized schema
        # Note: Only SensorReadingF (no SensorReadingC) to match Coris behavior
        standardized_columns = [
            "source", "SensorID_Hobolink", "SensorID_Coris", "SensorID_Conserv", 
            "customer_id", "QueryUTC", "SensorReadingUTC", "DeviceID_Hobolink", 
            "DeviceID_Coris", "SensorName", "DeviceName", "SensorType", 
            "SensorReading", "SensorReadingF", "SensorReadingRh"
        ]
        
        # Add missing columns with None values
        for col in standardized_columns:
            if col not in result.columns:
                if "ID" in col and col.endswith("_Coris"):
                    result = result.with_columns(polars.lit(None, dtype=polars.Int32).alias(col))
                elif col in ["customer_id", "QueryUTC", "SensorReadingUTC"]:
                    result = result.with_columns(polars.lit(None, dtype=polars.Int32).alias(col))
                else:
                    result = result.with_columns(polars.lit(None, dtype=polars.String).alias(col))
        
        result = result.select([col for col in standardized_columns if col in result.columns])
        
        self.logger.info(f"Retrieved {len(result)} current readings from Hobolink")
        return result
    
    def test_api_connection(self) -> bool:
        """
        Test the API connection and log response structure.
        
        Returns
        -------
        bool
            True if connection successful, False otherwise
        """
        try:
            self.logger.info("Testing API connection...")
            devices = self.get_devices(include_sensors=True)
            self.logger.info("API connection test successful!")
            return True
        except Exception as e:
            self.logger.error(f"API connection test failed: {str(e)}")
            return False
    
    def explore_device_structure(self) -> None:
        """
        Explore and log the structure of devices and sensors.
        """
        try:
            self.logger.info("Exploring device structure...")
            devices_response = self.get_devices(include_sensors=True)
            
            if isinstance(devices_response, dict) and "devices" in devices_response:
                devices = devices_response["devices"]
                self.logger.info(f"Total devices available: {devices_response.get('total', 'unknown')}")
                
                # Examine first few devices in detail
                for i, device in enumerate(devices[:3]):  # First 3 devices
                    self.logger.info(f"\n--- Device {i+1} Structure ---")
                    self.logger.info(f"Device keys: {list(device.keys())}")
                    
                    # Log key device properties
                    for key in ["serialNumber", "serial", "id", "name", "deviceName", "location", "status", "deviceSerialNumber"]:
                        if key in device:
                            self.logger.info(f"Device {key}: {device[key]}")
                    
                    # Examine sensors if present
                    for sensor_key in ["sensors", "sensorData", "measurements"]:
                        if sensor_key in device and device[sensor_key]:
                            sensors = device[sensor_key]
                            self.logger.info(f"Found {len(sensors)} items in '{sensor_key}'")
                            
                            # Examine first few sensors
                            for j, sensor in enumerate(sensors[:2]):  # First 2 sensors per device
                                self.logger.info(f"  Sensor {j+1} keys: {list(sensor.keys())}")
                                for s_key in ["serialNumber", "serial", "id", "name", "sensorName", "type", "unit", "sensorSerialNumber", "measurementType", "units", "latest"]:
                                    if s_key in sensor:
                                        self.logger.info(f"  Sensor {s_key}: {sensor[s_key]}")
                            break
                        
        except Exception as e:
            self.logger.error(f"Error exploring device structure: {str(e)}")
    
    def diagnose_api_endpoints(self, save_samples: bool = True) -> None:
        """
        Diagnostic function to examine raw API responses from each endpoint.
        
        This function calls each API endpoint and prints sample output to help
        understand the data format and structure returned by the Hobolink API.
        
        Parameters
        ----------
        save_samples : bool, default=True
            Whether to save sample responses to files in samples/ directory
        """
        import json
        
        # Create samples directory if saving is enabled
        samples_dir = None
        if save_samples:
            samples_dir = os.path.join(os.getcwd(), "samples")
            os.makedirs(samples_dir, exist_ok=True)
            print(f"Samples will be saved to: {samples_dir}")
        
        try:
            print("=" * 60)
            print("HOBOLINK API ENDPOINT DIAGNOSTICS")
            print("=" * 60)
            
            # 1. Devices endpoint
            print("\n1. DEVICES ENDPOINT")
            print("-" * 30)
            print("Endpoint: GET /devices?includeSensors=true")
            
            devices_response = self.get_devices(include_sensors=True)
            
            # Save devices response to file
            if save_samples and samples_dir:
                devices_file = os.path.join(samples_dir, "hobolink_devices_response.json")
                try:
                    with open(devices_file, 'w', encoding='utf-8') as f:
                        json.dump(devices_response, f, indent=2, default=str)
                    print(f"✓ Saved devices response to: {devices_file}")
                except Exception as e:
                    print(f"✗ Failed to save devices response: {e}")
            
            print(f"Raw devices response type: {type(devices_response)}")
            if isinstance(devices_response, dict):
                print(f"Raw devices response keys: {list(devices_response.keys())}")
            
            # Handle different response formats
            devices = None
            if isinstance(devices_response, list) and len(devices_response) > 0:
                devices = devices_response
            elif isinstance(devices_response, dict) and "devices" in devices_response:
                devices = devices_response["devices"]
                print(f"Found devices array with {len(devices)} items")
            
            if devices and len(devices) > 0:
                first_device = devices[0]
                print(f"Response type: {type(devices)} with {len(devices)} devices")
                print(f"First device keys: {list(first_device.keys())}")
                print(f"First device sample:")
                
                # Print key device info
                for key in ["deviceSerialNumber", "deviceName", "productCode", "unitSystem", "loggingState"]:
                    if key in first_device:
                        print(f"  {key}: {first_device[key]}")
                
                # Show sensors structure
                if "sensors" in first_device and first_device["sensors"]:
                    sensors = first_device["sensors"]
                    print(f"  sensors: array with {len(sensors)} items")
                    if len(sensors) > 0:
                        first_sensor = sensors[0]
                        print(f"    First sensor keys: {list(first_sensor.keys())}")
                        for sensor_key in ["sensorSerialNumber", "measurementType", "units", "latest"]:
                            if sensor_key in first_sensor:
                                print(f"    {sensor_key}: {first_sensor[sensor_key]}")
                

                
                # 2. Data endpoint - try to get readings for first temperature/humidity sensor
                print(f"\n2. DATA ENDPOINT")
                print("-" * 30)
                print("Endpoint: GET /data?deviceSerialNumber=X&sensorSerialNumber=Y&startTime=Z&endTime=W")
                
                # Find temperature or humidity sensors and try them until we get data
                candidate_sensors = []
                
                # Collect all temperature/humidity sensors from all devices
                for device in devices:
                    device_serial = device.get("deviceSerialNumber")
                    if "sensors" in device:
                        for sensor in device["sensors"]:
                            measurement_type = sensor.get("measurementType", "").lower()
                            if measurement_type in ["temperature", "temp", "rh", "humidity", "relative humidity"]:
                                candidate_sensors.append({
                                    'device': device,
                                    'sensor': sensor,
                                    'has_latest': sensor.get("latest") is not None
                                })
                
                # Sort by preference: sensors with latest data first
                candidate_sensors.sort(key=lambda x: x['has_latest'], reverse=True)
                
                print(f"Found {len(candidate_sensors)} candidate temperature/humidity sensors")
                
                # Try sensors until we find one with actual data
                successful_response = None
                attempts = 0
                max_attempts = min(10, len(candidate_sensors))  # Try up to 10 sensors
                
                # Get time range for data queries
                import datetime
                now = datetime.datetime.now(datetime.timezone.utc)
                end_time = now
                start_time = now - datetime.timedelta(hours=24)  # Try 24 hours for more data
                
                end_time_ms = int(end_time.timestamp() * 1000)
                start_time_ms = int(start_time.timestamp() * 1000)
                
                for candidate in candidate_sensors[:max_attempts]:
                    attempts += 1
                    target_device = candidate['device']
                    target_sensor = candidate['sensor']
                    
                    device_serial = target_device.get("deviceSerialNumber")
                    sensor_serial = target_sensor.get("sensorSerialNumber")
                    measurement_type = target_sensor.get("measurementType")
                    units = target_sensor.get("units")
                    latest = target_sensor.get("latest")
                    
                    print(f"\nAttempt {attempts}: Testing device {device_serial}, sensor {sensor_serial}")
                    print(f"  Sensor: {measurement_type}, {units}, latest: {latest}")
                    
                    try:
                        data_response = self.get_sensor_data(
                            device_serial, sensor_serial, start_time_ms, end_time_ms
                        )
                        
                        # Check if we got actual data (not just "No sensor found")
                        has_data = False
                        if isinstance(data_response, dict):
                            if "sensors" in data_response and data_response["sensors"]:
                                has_data = True
                            elif "data" in data_response and data_response["data"]:
                                has_data = True
                            elif data_response.get("message") != "No sensor found":
                                has_data = True
                        
                        if has_data:
                            print(f"  ✓ SUCCESS! Found data for {device_serial}/{sensor_serial}")
                            successful_response = data_response
                            
                            # Save successful data response to file
                            if save_samples and samples_dir:
                                data_file = os.path.join(samples_dir, f"hobolink_data_response_{device_serial}_{sensor_serial}.json")
                                try:
                                    with open(data_file, 'w', encoding='utf-8') as f:
                                        json.dump(data_response, f, indent=2, default=str)
                                    print(f"  ✓ Saved data response to: {data_file}")
                                except Exception as e:
                                    print(f"  ✗ Failed to save data response: {e}")
                            
                            # Show the successful response structure
                            print(f"  Data response type: {type(data_response)}")
                            if isinstance(data_response, dict):
                                print(f"  Data response keys: {list(data_response.keys())}")
                                
                                # Show message if present
                                if "message" in data_response:
                                    print(f"    message: {data_response['message']}")
                                
                                # Look for common data patterns
                                for key in ["sensors", "data", "readings", "measurements", "results"]:
                                    if key in data_response:
                                        data_content = data_response[key]
                                        print(f"    {key}: {type(data_content)}")
                                        
                                        if isinstance(data_content, list):
                                            print(f"      Length: {len(data_content)}")
                                            if len(data_content) > 0:
                                                first_item = data_content[0]
                                                print(f"      First item type: {type(first_item)}")
                                                if isinstance(first_item, dict):
                                                    print(f"      First item keys: {list(first_item.keys())}")
                                                    
                                                    # Show sample data structure
                                                    if key == "sensors" and "data" in first_item:
                                                        sensor_data = first_item["data"]
                                                        print(f"        sensor.data: {type(sensor_data)} with {len(sensor_data) if isinstance(sensor_data, list) else 'N/A'} items")
                                                        if isinstance(sensor_data, list) and len(sensor_data) > 0:
                                                            print(f"          Sample readings:")
                                                            for i, reading in enumerate(sensor_data[:3]):  # Show first 3
                                                                print(f"            [{i}]: {reading}")
                                                else:
                                                    # Show raw item if not complex structure  
                                                    print(f"      Sample item: {first_item}")
                                        else:
                                            print(f"      Content: {data_content}")
                            
                            break  # Found successful response, stop trying
                        else:
                            print(f"  ✗ No data found for {device_serial}/{sensor_serial}")
                            if "message" in data_response:
                                print(f"    Message: {data_response['message']}")
                            
                    except Exception as e:
                        print(f"  ✗ Error querying {device_serial}/{sensor_serial}: {str(e)}")
                
                if not successful_response:
                    print(f"\n✗ No sensors returned data after {attempts} attempts")
                    print("This could mean:")
                    print("  - Sensors are not currently logging data")
                    print("  - API requires different time ranges")
                    print("  - Sensor serial numbers have changed")
                else:
                    print(f"\n✓ Successfully found sensor data after {attempts} attempt(s)")
            
            else:
                print("No devices found or invalid response format")
                
            print(f"\n{'=' * 60}")
            print("DIAGNOSTICS COMPLETE")
            print(f"{'=' * 60}")
            
            # List saved files
            if save_samples and samples_dir:
                print(f"\nSaved sample files:")
                try:
                    for file in os.listdir(samples_dir):
                        if file.startswith('hobolink_'):
                            file_path = os.path.join(samples_dir, file)
                            file_size = os.path.getsize(file_path)
                            print(f"  - {file} ({file_size} bytes)")
                except Exception as e:
                    print(f"Error listing saved files: {e}")
            
        except Exception as e:
            print(f"Diagnostic error: {str(e)}")
            import traceback
            traceback.print_exc()

    def explore_recent_data(self, hours_back: int = 24) -> None:
        """
        Explore recent data from available devices/sensors.
        
        Parameters
        ----------
        hours_back : int, default=24
            How many hours back to look for data
        """
        try:
            # First explore structure
            self.explore_device_structure()
            
            # Get devices for data queries
            self.logger.info(f"\nExploring data from last {hours_back} hours...")
            devices_response = self.get_devices(include_sensors=True)
            
            # Calculate time range - use proper current time
            now = datetime.datetime.now(datetime.timezone.utc)
            end_time = now
            start_time = now - datetime.timedelta(hours=hours_back)
            
            # Convert to milliseconds
            end_time_ms = int(end_time.timestamp() * 1000)
            start_time_ms = int(start_time.timestamp() * 1000)
            
            self.logger.info(f"Time range: {start_time_ms} to {end_time_ms}")
            
            # Try to extract device and sensor info for data queries
            devices_to_query = []
            
            if isinstance(devices_response, dict) and "devices" in devices_response:
                devices = devices_response["devices"]
                
                for device in devices[:2]:  # Limit to first 2 devices for testing
                    if isinstance(device, dict):
                        # Try various possible device serial number fields
                        device_serial = None
                        for key in ["deviceSerialNumber", "serialNumber", "serial", "id", "deviceSerial", "deviceId"]:
                            if key in device and device[key]:
                                device_serial = str(device[key])
                                break
                        
                        if device_serial:
                            # Look for sensors in various possible locations
                            sensors = device.get("sensors", []) or device.get("sensorData", []) or []
                            for sensor in sensors[:2]:  # Limit to first 2 sensors per device
                                if isinstance(sensor, dict):
                                    # Try various possible sensor serial number fields
                                    sensor_serial = None
                                    for key in ["sensorSerialNumber", "serialNumber", "serial", "id", "sensorSerial", "sensorId"]:
                                        if key in sensor and sensor[key]:
                                            sensor_serial = str(sensor[key])
                                            break
                                    
                                    if sensor_serial:
                                        devices_to_query.append((device_serial, sensor_serial))
                                        self.logger.info(f"Will query: device={device_serial}, sensor={sensor_serial}")
            
            # Query data for found device/sensor combinations
            self.logger.info(f"\nFound {len(devices_to_query)} device/sensor combinations to query")
            
            for device_serial, sensor_serial in devices_to_query:
                self.logger.info(f"\n--- Querying data for device {device_serial}, sensor {sensor_serial} ---")
                try:
                    data = self.get_sensor_data(device_serial, sensor_serial, start_time_ms, end_time_ms)
                    self.logger.info(f"Successfully retrieved data for {device_serial}/{sensor_serial}")
                except Exception as e:
                    self.logger.warning(f"Failed to get data for {device_serial}/{sensor_serial}: {str(e)}")
                    
        except Exception as e:
            self.logger.error(f"Error exploring recent data: {str(e)}")


def create_hobolink_client_from_env(logger: Optional[logging.Logger] = None) -> HobolinkClient:
    """
    Create a Hobolink client using environment variables or .env file.
    
    Parameters
    ----------
    logger : Optional[logging.Logger]
        Logger instance for recording client creation
        
    Returns
    -------
    HobolinkClient
        Configured Hobolink client instance
        
    Raises
    ------
    ValueError
        If required environment variables are not found
    """
    def read_env_variable(var_name: str) -> Optional[str]:
        """Read environment variable from .env file or environment."""
        # First try environment variables
        value = os.getenv(var_name)
        if value:
            return value
            
        # Then try .env file
        try:
            with open(".env") as f:
                for line in f:
                    if line.startswith(var_name):
                        return line.split("=", 1)[1].strip()
        except FileNotFoundError:
            pass
        
        return None
    
    api_key = read_env_variable("HOBOLINK_API_KEY")
    
    if not api_key:
        raise ValueError("HOBOLINK_API_KEY not found in environment or .env file")
    
    if logger:
        logger.info("Created Hobolink client")
    
    return HobolinkClient(api_key=api_key, logger=logger)


def run_hobolink_diagnostics(save_samples: bool = True):
    """
    Convenience function to run API endpoint diagnostics.
    
    This function creates a Hobolink client and runs comprehensive diagnostics
    on all API endpoints to show raw response formats and data structures.
    
    Parameters
    ----------
    save_samples : bool, default=True
        Whether to save sample responses to files in samples/ directory
    
    Requires HOBOLINK_API_KEY environment variable to be set.
    """
    import logging
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    
    try:
        client = create_hobolink_client_from_env()
        client.diagnose_api_endpoints(save_samples=save_samples)
    except Exception as e:
        print(f"✗ Failed to run diagnostics: {e}")
        print("Make sure HOBOLINK_API_KEY environment variable is set")


# Example usage
if __name__ == "__main__":
    """
    Example usage of the Hobolink client.
    For comprehensive testing, run: python -m pytest test/test_hobolink.py -v
    """
    import logging
    
    # Set up basic logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)
    
    try:
        # Create client from environment
        client = create_hobolink_client_from_env(logger=logger)
        
        # Basic API test
        if client.test_api_connection():
            print("✓ Hobolink API connection successful!")
            
            # Quick device count
            devices = client.get_devices_as_dataframe(testing=True, testing_device_limit=1)
            print(f"✓ Found {len(devices)} device/sensor combinations")
            
            # Quick current readings check
            current = client.get_current_readings(testing=True, testing_device_limit=1)
            print(f"✓ Retrieved {len(current)} current readings")
            
            print("\nFor comprehensive testing, run:")
            print("python -m pytest test/test_hobolink.py -v")
            
        else:
            print("✗ API connection failed. Check your HOBOLINK_API_KEY environment variable.")
            
    except Exception as e:
        print(f"✗ Error: {e}")
        print("Make sure HOBOLINK_API_KEY is set in your environment or .env file.")