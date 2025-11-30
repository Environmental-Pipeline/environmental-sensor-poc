"""
# LI-COR API Client

This module provides a client interface that handles sensor data retrieval, 
historical data queries, and data transformation
to maintain compatibility with the EnvironmentData schema.

## Commands:
- Create HTML documentation in `docs/clients`: `pdoc clients/licor_client.py -o docs/ --no-search` 
- Save API output to `samples/` folder: `python -c "from clients.licor_client import LicorClient; LicorClient().sample_raw_data()"`
- Test with `pytest tests/test_licor_client.py`

## API Endpoints

The client queries available sensors and then loops over them to get readings. APIs return JSON data. Authentication uses a Bearer Token from .env variable LICOR_API_KEY.

### Identify available devices and sensors.

- GET https://api.licor.cloud/v2/devices?includeSensors=true 
- The same sensorSerialNumber number can be used in multiple devices, so we create a combo ID of device and sensor serial numbers for Sensor ID.

```json
{
  "total": 16,
  "devices": [
    {
      "deviceName": "RX Station 1",
      "deviceSerialNumber": "22202142", 
      "productCode": "RX2100",
      "lastConnectionTime": "2025-11-14T21:31:12.022Z",
      "loggingState": "LOGGING",
      "alarmed": true,
      "unitSystem": "US",
      "sensors": [
        {
          "sensorSerialNumber": "22179174-2",
          "measurementType": "RH",
          "units": "%",
          "latest": 47.115281910429545
        },
        ...
      ]
    },
    ...
  ]
}
```

### Sensor data.

- GET https://api.licor.cloud/v2/data?deviceSerialNumber=X&sensorSerialNumber=Y&startTime=Z&endTime=W
- Only allows data within the last year. 
- Property "moreResults" is always False. The code will error out if it is ever true, in which case this would need to be handled. It isn't possible to handle it now since I can't find any data that has it true, so I'm not sure what that data would look like. It is either an unused property or only comes into play when there is too much data in one record, which didn't happen when I searched the max duration (one year) across all sensors.
- Most readings come in Fahrenheit, but some do come through in Celsius, in which case we convert to Fahrenheit for consistency.

```json
{
  "moreResults": false,
  "sensors": [
    {
      "totalRecords": 19,
      "sensorSerialNumber": "22179174-2",
      "latestTimestamp": 1763155800000,
      "data": [
        {
          "measurementType": "RH",
          "dataType": "CURRENT", 
          "units": "%",
          "records": [
            [1763139600000, 38.1353475242237],
            [1763140500000, 46.08224612802319],
            ...
          ]
        }
      ]
    }
  ]
}
```
"""

import os
import requests
import polars
import datetime
import logging
import tqdm
import json
from pathlib import Path
from typing import List, Dict, Optional, Any

class LicorClient:
    
    def __init__(self, logger: Optional[logging.Logger] = None, home_directory: str = "."):
        """
        Initialize the LI-COR API client.
        
        Parameters
        ----------
        logger : Optional[logging.Logger]
            Logger instance for recording API interactions
        home_directory : str, default="."
            Base directory for finding .env file
        """
        # Read API key from environment
        api_key = None
        env_file_path = os.path.join(home_directory, ".env")
        try:
            with open(env_file_path) as f:
                for line in f:
                    if line.startswith("LICOR_API_KEY"):
                        api_key = line.split("=", 1)[1].strip()
                        break
        except FileNotFoundError:
            pass
        
        if not api_key:
            raise ValueError(
                f"LICOR_API_KEY not found in .env file at {env_file_path}. "
                f"If running from a subdirectory, set home_directory parameter to parent directory (e.g., home_directory='..').")
        
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
    
    def get_devices(self) -> Dict[str, Any]:
        """
        Get the list of all devices from the LI-COR API.
            
        Returns
        -------
        Dict[str, Any]
            Raw device data from API for inspection
        """
        self.logger.info("get_devices")
        
        response_data = self._make_api_request("devices", {"includeSensors": "true"})
        
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
        
        Note: LI-COR API limits historical data queries to maximum 1 year (365 days).
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
        # Validate and cap date range - LI-COR API limits to less than 1 year maximum
        max_days = 364  # Use 364 days to be safe with API limitations
        max_milliseconds = max_days * 24 * 60 * 60 * 1000
        date_range_ms = end_time_ms - start_time_ms
        
        if date_range_ms > max_milliseconds:
            start_time_ms = end_time_ms - max_milliseconds
            self.logger.warning(
                f"Date range ({date_range_ms // (24 * 60 * 60 * 1000)} days) exceeds LI-COR API limit of {max_days} days. "
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
                                       device_serial: str = None,
                                       device_metadata: polars.DataFrame = None) -> polars.DataFrame:
        """
        Transform LI-COR API response data to standardized EnvironmentData schema.
        
        Note: This method assumes moreResults=false (complete data). If moreResults=true
        is ever encountered, get_sensor_data() will raise an exception since pagination
        is not currently handled.
        
        Parameters
        ----------
        raw_data : Dict[str, Any]
            Raw API response from get_sensor_data
        device_serial : str, optional
            Device serial number to populate DeviceID field
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
                        polars.lit(f"licor:{device_serial or 'unknown'}-{sensor_serial}").alias("SensorID"),
                        polars.lit(measurement_type).alias("SensorType"),
                        polars.lit(units).alias("SensorUnits"),
                        polars.lit("LI-COR").alias("Source"),
                        
                        # Add schema columns - populate DeviceID with device serial when available
                        polars.lit(f"licor:{device_serial}" if device_serial else None).alias("DeviceID"),
                        polars.lit(None, dtype=polars.Int32).alias("customer_id"),
                        polars.lit(None, dtype=polars.String).alias("SensorName"),
                        polars.lit(None, dtype=polars.String).alias("DeviceName"),
                        polars.lit(int(datetime.datetime.now(datetime.timezone.utc).timestamp())).alias("QueryUTC"),
                        polars.lit(True).alias("Historical"),
                    ])
                    
                    # Map measurement types to standardized reading columns
                    # Focus on temperature and humidity only
                    if measurement_type.lower() in ["temperature", "temp"]:
                        if "°F" in units or "F" in units:
                            # Already in Fahrenheit - keep this sensor
                            df = df.with_columns(polars.col("value").cast(polars.Float32).alias("SensorReadingF"))
                        elif "°C" in units or "C" in units:
                            # Celsius detected - convert to Fahrenheit
                            self.logger.info(f"Converting Celsius to Fahrenheit for sensor: {sensor_serial} "
                                           f"(measurement_type: {measurement_type}, units: {units})")
                            df = df.with_columns(
                                (polars.col("value") * 9.0 / 5.0 + 32.0).cast(polars.Float32).alias("SensorReadingF")
                            )
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
        # Note: For historical data, device names will be set to null since the
        # device serial -> sensor serial mapping is not straightforward in LI-COR API
        if device_metadata is not None and not device_metadata.is_empty():
            # For now, we'll rely on the DeviceName being set in the get_devices_as_dataframe method
            # The device metadata passed here is for a specific device, so we can use that device name
            if "deviceName" in device_metadata.columns and len(device_metadata) > 0:
                device_name = device_metadata.select("deviceName").item(0, 0)
                if device_name:
                    result = result.with_columns([
                        polars.lit(device_name).alias("DeviceName")
                    ])
        
        self.logger.info(f"Transformed {len(result)} readings from LI-COR data")
        return result
    
    def get_devices_as_dataframe(self, out_of_scope: List[str] = None,
                               testing: bool = False,
                               testing_device_limit: int = 3) -> polars.DataFrame:
        """
        Get devices and sensors as a standardized DataFrame.
        
        Parameters
        ----------
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
        current_utc = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        
        self.logger.info("get_devices_as_dataframe")
        devices_response = self.get_devices()
        
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
            
            if "sensors" in device:
                # Create a record for each sensor (temperature and humidity only)
                for sensor in device["sensors"]:
                    sensor_serial = sensor.get("sensorSerialNumber")
                    measurement_type = sensor.get("measurementType", "Unknown")
                    units = sensor.get("units", "")
                    latest_value = sensor.get("latest")
                    
                    # Filter to only temperature and humidity sensors
                    if measurement_type.lower() not in ["temperature", "temp", "rh", "humidity", "relative humidity"]:
                        continue
                    
                    device_records.append({
                        "DeviceID": f"licor:{device_serial}",
                        "DeviceName": device_name,
                        "SensorID": f"licor:{device_serial}-{sensor_serial}",
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
        
        if not device_records:
            return polars.DataFrame()
        
        # Create DataFrame
        result = polars.DataFrame(device_records)
        
        # Add standardized schema columns
        result = result.with_columns([
            polars.lit(current_utc).alias("QueryUTC"),
            polars.lit("LI-COR").alias("Source"),
            polars.lit(None, dtype=polars.Int32).alias("customer_id"),
        ])
        
        self.logger.info(f"Retrieved {len(result)} device/sensor combinations")
        return result
    
    def get_historical_data(self, start_utc: int, end_utc: int,
                               out_of_scope: List[str] = None,
                               testing: bool = False,
                               testing_device_limit: int = 2,
                               testing_sensors_per_device: int = 2) -> List[polars.DataFrame]:
        """
        Get historical data for all devices and sensors in bulk.
        
        Note: LI-COR API limits historical data queries to maximum 1 year (365 days).
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
        # Validate and cap date range - LI-COR API limits to less than 1 year maximum
        max_days = 364  # Use 364 days to be safe with API limitations
        max_seconds = max_days * 24 * 60 * 60
        date_range_seconds = end_utc - start_utc
        
        if date_range_seconds > max_seconds:
            original_start = start_utc
            start_utc = end_utc - max_seconds
            self.logger.warning(
                f"Date range ({date_range_seconds // 86400} days) exceeds LI-COR API limit of {max_days} days. "
                f"Capping to most recent {max_days} days."
            )
            self.logger.info(f"Adjusted start_utc from {original_start} to {start_utc}")
        
        self.logger.info(f"Using date range: {(end_utc - start_utc) // 86400} days of historical data")
        devices_df = self.get_devices_as_dataframe(
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
            .filter(polars.col("SensorID").is_not_null())
            .select(["DeviceID", "SensorID", "DeviceName", "SensorType"])
            .unique()
        )
        
        if testing:
            # Group by device and limit sensors per device
            limited_combinations = []
            for device_id in device_sensor_combinations["DeviceID"].unique():
                device_sensors = device_sensor_combinations.filter(
                    polars.col("DeviceID") == device_id
                ).head(testing_sensors_per_device)
                limited_combinations.append(device_sensors)
            
            if limited_combinations:
                device_sensor_combinations = polars.concat(limited_combinations)
        
        total_combinations = len(device_sensor_combinations)
        self.logger.info(f"Retrieving historical data for {total_combinations} device/sensor combinations")
        
        # Use progress bar for bulk operations
        pbar = tqdm.tqdm(total=total_combinations, desc="Gathering LI-COR readings")
        
        for row in device_sensor_combinations.iter_rows(named=True):
            consolidated_device_id = row["DeviceID"]
            device_serial = consolidated_device_id.split(":")[-1]  # Extract serial from licor:serial format
            consolidated_sensor_id = row["SensorID"]
            # Extract sensor serial from new format: licor:device-sensor -> sensor
            sensor_part = consolidated_sensor_id.split(":")[-1]  # Get device-sensor part
            sensor_serial = sensor_part.split("-", 1)[-1]  # Extract sensor part after first dash
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
                        device_serial=device_serial,
                        device_metadata=devices_df.filter(
                            polars.col("DeviceID") == f"licor:{device_serial}"
                        ).select(["DeviceID", "DeviceName"]).with_columns([
                            polars.col("DeviceID").str.replace("licor:", "").alias("deviceSerialNumber")
                        ]).select(["deviceSerialNumber", "DeviceName"]).rename({
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
        Get current sensor readings from the LI-COR API.
        
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
        current_utc = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        
        # Create standardized current readings
        result = current_readings.with_columns([
            # Use current timestamp for reading time
            polars.lit(current_utc).alias("SensorReadingUTC"),
            polars.col("LatestReading").cast(polars.Float64).alias("SensorReading"),
        ])
        
        # Map specific measurement types to appropriate columns  
        # Include both Fahrenheit and Celsius temperatures (convert Celsius to Fahrenheit)
        result = result.with_columns([
            polars.when(polars.col("SensorType").str.to_lowercase().str.contains("temperature"))
            .then(
                polars.when(polars.col("SensorUnits").str.contains("°F|F"))
                .then(polars.col("LatestReading").cast(polars.Float32))  # Already Fahrenheit
                .when(polars.col("SensorUnits").str.contains("°C|C"))
                .then((polars.col("LatestReading") * 9.0 / 5.0 + 32.0).cast(polars.Float32))  # Convert C to F
                .otherwise(None)  # Skip unknown temperature units
            )
            .otherwise(None)
            .alias("SensorReadingF"),
            
            polars.when(polars.col("SensorType").str.to_lowercase().str.contains("rh|humidity"))
            .then(polars.col("LatestReading").cast(polars.Float32))
            .otherwise(None)
            .alias("SensorReadingRh"),
        ])
        
        # Log converted Celsius temperature sensors
        celsius_sensors = result.filter(
            (polars.col("SensorType").str.to_lowercase().str.contains("temperature")) &
            (polars.col("SensorUnits").str.contains("°C|C")) &
            (polars.col("SensorReadingF").is_not_null())
        )
        
        if len(celsius_sensors) > 0:
            for row in celsius_sensors.select(["SensorID", "SensorType", "SensorUnits"]).iter_rows():
                sensor_id, sensor_type, units = row
                self.logger.info(f"Converting Celsius to Fahrenheit for current reading: {sensor_id} "
                               f"(measurement_type: {sensor_type}, units: {units})")
        
        # Select only the columns that match the standardized schema
        # Note: Only SensorReadingF (no SensorReadingC)
        standardized_columns = [
            "Source", "SensorID", "DeviceID", "customer_id", "QueryUTC", "SensorReadingUTC", 
            "SensorName", "DeviceName", "SensorType", "SensorReading", "SensorReadingF", "SensorReadingRh", "Historical"
        ]
        
        # Add missing columns with None values and Historical=False
        result = result.with_columns(polars.lit(False).alias("Historical"))
        
        for col in standardized_columns:
            if col not in result.columns:
                if col in ["customer_id", "QueryUTC", "SensorReadingUTC"]:
                    result = result.with_columns(polars.lit(None, dtype=polars.Int32).alias(col))
                elif col in ["SensorReadingF", "SensorReadingRh", "SensorReading"]:
                    result = result.with_columns(polars.lit(None, dtype=polars.Float32).alias(col))
                else:
                    result = result.with_columns(polars.lit(None, dtype=polars.String).alias(col))
        
        result = result.select([col for col in standardized_columns if col in result.columns])
        
        self.logger.info(f"Retrieved {len(result)} current readings from LI-COR")
        return result

    def sample_raw_data(self, save_to_samples: bool = True) -> Dict[str, Any]:
        """
        Generate sample raw API data for testing and exploration.
        
        This method fetches raw data from the LI-COR API and optionally saves it
        to the samples directory with timestamps. Fetches sensor data from multiple
        sensors across different devices for comprehensive testing.
        
        Parameters
        ----------
        save_to_samples : bool, default=True
            Whether to save the raw responses to samples/licor/ directory
            
        Returns
        -------
        Dict[str, Any]
            Dictionary containing the raw API responses:
            {
                'devices': raw_devices_response,
                'sensor_data_samples': [list of raw sensor data responses]
            }
        """
        results = {}
        
        try:
            print("🔍 Fetching raw device data...")
            # Get raw devices data
            raw_devices = self.get_devices()
            results['devices'] = raw_devices
            
            if save_to_samples:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"raw_devices_response_{timestamp}.json"
                filepath = Path(__file__).parent.parent / "samples" / "licor" / filename
                filepath.parent.mkdir(parents=True, exist_ok=True)
                with open(filepath, 'w') as f:
                    json.dump(raw_devices, f, indent=2, default=str)
                print(f"Saved JSON data to: {filepath}")
            
            devices_data = raw_devices.get('devices', []) if isinstance(raw_devices, dict) else raw_devices
            print(f"✅ Retrieved {len(devices_data) if isinstance(devices_data, list) else 'N/A'} devices")
            
            # Try to get sample sensor data from multiple devices/sensors
            sensor_data_samples = []
            max_devices = 3  # Sample up to 3 devices
            max_sensors_per_device = 2  # Sample up to 2 sensors per device
            devices_sampled = 0
            
            if isinstance(devices_data, list) and len(devices_data) > 0:
                print(f"🔍 Fetching sensor data from up to {max_devices} devices with {max_sensors_per_device} sensors each...")
                
                # Get last 6 hours for quick sample
                end_time = datetime.datetime.now()
                start_time = end_time - datetime.timedelta(hours=6)
                start_ms = int(start_time.timestamp() * 1000)
                end_ms = int(end_time.timestamp() * 1000)
                
                for device in devices_data:
                    if devices_sampled >= max_devices:
                        break
                        
                    if device.get('sensors') and len(device['sensors']) > 0:
                        device_serial = device.get('serialNumber') or device.get('deviceSerialNumber')
                        device_name = device.get('deviceName', 'Unknown Device')
                        
                        if not device_serial:
                            continue
                            
                        sensors_sampled = 0
                        print(f"\n📱 Device: {device_name} ({device_serial})")
                        
                        for sensor in device['sensors']:
                            if sensors_sampled >= max_sensors_per_device:
                                break
                                
                            sensor_serial = sensor.get('serialNumber') or sensor.get('sensorSerialNumber')
                            sensor_type = sensor.get('measurementType', 'Unknown')
                            
                            if not sensor_serial:
                                continue
                                
                            print(f"  🔍 Fetching data for sensor {sensor_serial} ({sensor_type})...")
                            
                            try:
                                raw_sensor_data = self.get_sensor_data(
                                    device_serial=device_serial,
                                    sensor_serial=sensor_serial,
                                    start_time_ms=start_ms,
                                    end_time_ms=end_ms
                                )
                                
                                sensor_data_samples.append({
                                    'device_serial': device_serial,
                                    'device_name': device_name,
                                    'sensor_serial': sensor_serial,
                                    'sensor_type': sensor_type,
                                    'raw_data': raw_sensor_data
                                })
                                
                                if save_to_samples:
                                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                                    filename = f"raw_sensor_data_{device_serial}_{sensor_serial}_{timestamp}.json"
                                    filepath = Path(__file__).parent.parent / "samples" / "licor" / filename
                                    filepath.parent.mkdir(parents=True, exist_ok=True)
                                    with open(filepath, 'w') as f:
                                        json.dump(raw_sensor_data, f, indent=2, default=str)
                                    print(f"    💾 Saved to: {filepath}")
                                
                                print(f"    ✅ Retrieved sensor data for {device_serial}/{sensor_serial}")
                                sensors_sampled += 1
                                
                            except Exception as e:
                                print(f"    ⚠️ Could not get sensor data for {device_serial}/{sensor_serial}: {e}")
                                continue
                        
                        if sensors_sampled > 0:
                            devices_sampled += 1
            
            results['sensor_data_samples'] = sensor_data_samples
            
            # Print summary
            print(f"\n📋 Sample Data Summary:")
            print(f"   • Devices: {len(devices_data)} found")
            print(f"   • Sensor data samples: {len(sensor_data_samples)} collected from {devices_sampled} devices")
            
            if sensor_data_samples:
                for i, sample in enumerate(sensor_data_samples, 1):
                    print(f"     {i}. {sample['device_name']} - {sample['sensor_type']} ({sample['sensor_serial']})")
            
            if save_to_samples:
                print(f"   • Files saved to: samples/licor/")
            
            return results
            
        except Exception as e:
            print(f"❌ Error generating sample data: {e}")
            raise

