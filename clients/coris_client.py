"""
Coris API Client Module

This module provides a client interface that handles sensor data retrieval, 
historical data queries, and data transformation
to maintain compatibility with the EnvironmentData schema.

## Commands:
- Create HTML documentation in `docs/clients`: `pdoc clients/coris_client.py -o docs/ --no-search` 
- Save API output to `samples/` folder: `python -c "from clients.coris_client import CorisClient; CorisClient().sample_raw_data()"`
- Test with `pytest tests/test_coris_client.py`

## API Endpoints

The client queries available sensors and then loops over them to get readings. APIs return JSON or CSV data. Authentication uses API key and CATS User ID from .env variables CORIS_API_KEY and CATS_USER_ID.

Individual sensors only make one type of reading, so data is stored as one table per sensor type to prevent excessively repetitive or sparse tables. 

The project reads `SensorType` = "Temperature", "Humidity", "LuxSensor" from the Coris API. New types can be brought in by adding a new entry to the `readings` object in the `EnvironmentData` class. For LuxSensor types, the API uses ReadingType=SensorReading which is mapped to the SensorReadingLux column.

### Get User Account and Sensor Information

- GET https://cats.corismonitoring.com/api/cats/user/?ApiKey={api_key}&CatsUserID={cats_user_id}
- Returns comprehensive user account data including all sensors, devices, gateways, alerts, and configuration
- Each sensor includes current readings in both Celsius and Fahrenheit, device associations, and sensor metadata

```json
{
  "CatsUserID": 2496,
  "CatsUserName": "YalePeabodyApi",
  "MasterCatsUserID": 666,
  "FirstName": "YaleApi",
  "LastName": "Peabody",
  "Sensors": [
    {
      "SensorName": "Cryo tank 1 on ETS 01AB4F (P3)",
      "SensorID": 21030,
      "SensorPort": 3,
      "ServerUTC": 1763165616,
      "HexGatewayMac": "000000000000",
      "LoraHexGatewayMac": "0080000000019E44",
      "SensorReading": -188.1,
      "SensorReadingUTC": 1763165582,
      "DeviceName": "Basement LN2 1 YALE TWhite ETS 1AB4F",
      "DeviceDevID": 11808,
      "SensorType": "Thermocouple",
      "SensorReadingC": -188.1,
      "SensorReadingF": -306.6,
      "SensorTempPref": "C",
      "UserTempPref": "F",
      "SensorTimeZone": "America/New_York",
      "LoraBatteryPresent": 0,
      "LoraBattery_mV": null,
      "LoraBatteryPercentage": null,
      ...
    },
    ...
  ],
  "Gateways": [...],
  "CriticalAlerts": [...],
  "ContactMethods": [...]
}
```

### Historical Sensor Data

- GET https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={api_key}&SensorID={sensor_id}&ReadingType={reading_type}&StartUTC={start_utc}&EndUTC={end_utc}&MinReadingSpacing=600&RequestedOutputFormat=raw
- Returns CSV format with timestamp,value pairs
- Common ReadingType values: 'SensorReadingF', 'SensorReadingC', 'SensorReadingRh'
- MinReadingSpacing=600 requests data every 10 minutes
- Times are in UTC seconds (not milliseconds like LI-COR)
- You can remove StartUTC and/or EndUTC to get the full historical data (this should be confirmed with the API provider though). 

```csv
1763079219,-309.1
1763079819,-309.1
1763080419,-309.1
1763081019,-309.1
1763081619,-309.1
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


class CorisClient:
    """
    Client for interacting with the Coris environmental monitoring API.
    
    This client handles:
    - Sensor metadata retrieval
    - Historical sensor data queries
    - Current sensor readings
    - Data transformation to standardized schema
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None, home_directory: str = ".",
                 api_key: Optional[str] = None, cats_user_id: Optional[str] = None):
        """
        Initialize the Coris API client.

        Parameters
        ----------
        logger : Optional[logging.Logger]
            Logger instance for recording API interactions
        home_directory : str, default="."
            Base directory for finding .env file
        api_key : Optional[str]
            Coris API key. If provided, skips reading from .env file.
        cats_user_id : Optional[str]
            CATS User ID. If provided, skips reading from .env file.
        """
        if api_key and cats_user_id:
            # Use explicitly provided credentials
            self.api_key = api_key
            self.cats_user_id = cats_user_id
        else:
            # Read API key from .env file
            env_file_path = os.path.join(home_directory, ".env")
            api_key_val = None
            with open(env_file_path) as f:
                for line in f:
                    key = line.split("=", 1)[0].strip()
                    if key == "CORIS_API_KEY":
                        api_key_val = line.split("=", 1)[1].strip()
                        break

            # Read CATS User ID from .env file
            cats_user_id_str = None
            with open(env_file_path) as f:
                for line in f:
                    key = line.split("=", 1)[0].strip()
                    if key == "CATS_USER_ID":
                        cats_user_id_str = line.split("=", 1)[1].strip()
                        break

            if not api_key_val:
                raise ValueError(
                    f"CORIS_API_KEY not found in environment or .env file at {env_file_path}. "
                    f"If running from a subdirectory, set home_directory parameter to parent directory (e.g., home_directory='..').")

            if not cats_user_id_str:
                raise ValueError(
                    f"CATS_USER_ID not found in environment or .env file at {env_file_path}. "
                    f"If running from a subdirectory, set home_directory parameter to parent directory (e.g., home_directory='..').")

            self.api_key = api_key_val
            self.cats_user_id = cats_user_id_str

        self.logger = logger or logging.getLogger(__name__)
        self.base_url = "https://cats.corismonitoring.com/api"

        # Reading interval in seconds (15 minutes = 900 seconds)
        # Consistent with Conserv (15-min exports) and LI-COR (downsampled to 15 min)
        self.reading_spacing_seconds = 900
    
    def get_current_utc(self) -> int:
        """
        Get the current UTC timestamp in seconds.
        
        Returns
        -------
        int : Current UTC timestamp
        """
        return int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    
    def get_sensors(self, out_of_scope: List[str] = None, testing: bool = False, 
                   testing_sensor_ids: List[int] = None) -> polars.DataFrame:
        """
        Get the list of all sensors from the Coris API.
        
        Parameters
        ----------
        out_of_scope : List[str], optional
            List of sensor name prefixes to exclude
        testing : bool, default=False
            If True, limit to testing sensors only
        testing_sensor_ids : List[int], optional
            List of sensor IDs to use when testing mode is enabled
            
        Returns
        -------
        polars.DataFrame
            Sensor data with columns: SensorID, DeviceID, SensorName, 
            DeviceName, SensorType, QueryUTC, source, customer_id
        """
        out_of_scope = out_of_scope or []
        testing_sensor_ids = testing_sensor_ids or []
        
        # Build the URL and call the API
        # self.logger.info("get_sensors")
        url = f'{self.base_url}/cats/user/?ApiKey={self.api_key}&CatsUserID={self.cats_user_id}'
        
        # Log sanitized URL for security
        self.logger.info(
            "API call: https://cats.corismonitoring.com/api/cats/user/?ApiKey=XXXX&CatsUserID=XXXX"
        )
        
        current_utc = self.get_current_utc()
        response = requests.get(url)
        
        # Check for errors
        if not response.ok:
            error_msg = f"Error getting sensors: {response.json()}"
            self.logger.error(error_msg)
            raise Exception(error_msg)
        
        # Process sensor data
        sensors = polars.DataFrame(response.json()["Sensors"])
        
        # Remove out-of-scope sensors
        for prefix in out_of_scope:
            sensors = sensors.filter(polars.col("SensorName").str.starts_with(prefix).not_())
        
        # If testing, only use selected sensors
        if testing and len(testing_sensor_ids) > 0:
            sensors = sensors.filter(polars.col("SensorID").is_in(testing_sensor_ids))
        
        # Generate SensorID and DeviceID with coris prefix
        sensors = sensors.with_columns([
            polars.concat_str([
                polars.lit("coris:"),
                polars.col("SensorID").cast(polars.String)
            ]).alias("SensorID"),
            polars.concat_str([
                polars.lit("coris:"),
                polars.col("DeviceDevID").cast(polars.String)
            ]).alias("DeviceID")
        ])
        
        # Add standardized schema columns
        sensors = sensors.with_columns([
            polars.lit(current_utc).alias("QueryUTC"),
            polars.lit("Coris").alias("Source"),
            polars.lit(None, dtype=polars.Int32).alias("customer_id"),
        ])

        # Map SensorReading to SensorReadingLux for LuxSensor types
        if "SensorReading" in sensors.columns and "SensorType" in sensors.columns:
            sensors = sensors.with_columns(
                polars.when(polars.col("SensorType") == "LuxSensor")
                .then(polars.col("SensorReading").cast(polars.Float32))
                .otherwise(polars.lit(None, dtype=polars.Float32))
                .alias("SensorReadingLux")
            )

        return sensors
    
    def get_historical_data(self, sensor_id: int, reading_type: str,
                          start_utc: int, end_utc: int,
                          sensor_metadata: polars.DataFrame,
                          column_name: Optional[str] = None) -> polars.DataFrame:
        """
        Get historical sensor data for a specific sensor and reading type.

        Parameters
        ----------
        sensor_id : int
            Coris sensor ID
        reading_type : str
            Type of reading (e.g., 'SensorReadingF', 'SensorReadingRh', 'SensorReading')
        start_utc : int
            Start time as UTC timestamp
        end_utc : int
            End time as UTC timestamp
        sensor_metadata : polars.DataFrame
            Sensor metadata to attach to readings
        column_name : Optional[str]
            Override name for the reading value column. If None, uses reading_type.
            Used to map API reading types to schema columns (e.g., 'SensorReading' -> 'SensorReadingLux').

        Returns
        -------
        polars.DataFrame
            Historical readings with standardized schema
        """
        url = "&".join([
            f'{self.base_url}/sensor/historical/?ApiKey={self.api_key}',
            f"SensorID={sensor_id}",
            f"ReadingType={reading_type}",
            f"StartUTC={start_utc}",
            f"EndUTC={end_utc}",
            f"MinReadingSpacing={self.reading_spacing_seconds}",
            "RequestedOutputFormat=raw",
        ])
        
        # Create sanitized URL for logging
        logurl = "&".join([
            "https://cats.corismonitoring.com/api/sensor/historical/?ApiKey=XXXX",
            f"SensorID={sensor_id}",
            f"ReadingType={reading_type}",
            f"StartUTC={start_utc}",
            f"EndUTC={end_utc}",
            f"MinReadingSpacing={self.reading_spacing_seconds}",
            "RequestedOutputFormat=raw",
        ])
        
        query_utc = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        self.logger.info(f"API call: {logurl}")
        response = requests.get(url)
        
        # Check for errors
        if not response.ok:
            error_msg = f"Error getting historical {reading_type} for {sensor_id}: {response.json()}"
            self.logger.error(error_msg)
            return polars.DataFrame()  # Return empty DataFrame instead of crashing
        
        # Parse CSV response
        output_column = column_name or reading_type
        data = polars.read_csv(response.content, has_header=False)
        data.columns = ["SensorReadingUTC", output_column]

        # Ensure consistent dtypes: reading values as Float32, timestamps as Int64
        data = data.with_columns(polars.col(output_column).cast(polars.Float32, strict=False))

        # Add sensor metadata
        consolidated_sensor_id = f"coris:{sensor_id}"
        data = data.with_columns(polars.lit(consolidated_sensor_id).alias("SensorID"))
        sensor_data = sensor_metadata.filter(polars.col("SensorID") == consolidated_sensor_id)
        
        if sensor_data.shape[0] > 0:
            for col in ["SensorName", "DeviceName", "DeviceID", "SensorType"]:
                if col in sensor_data.columns:
                    data = data.with_columns(
                        polars.lit(sensor_data[col].to_list()[0]).alias(col)
                    )
        
        # Add standardized schema columns including QueryUTC and Historical
        data = data.with_columns([
            polars.lit("Coris").alias("Source"),
            polars.lit(None, dtype=polars.Int32).alias("customer_id"),
            polars.lit(query_utc).alias("QueryUTC"),
            polars.lit(True).alias("Historical"),
        ])
        
        return data
    
    def get_historical_data_bulk(self, acceptable_range: Dict[str, List], 
                               start_utc: int, end_utc: int,
                               out_of_scope: List[str] = None,
                               testing: bool = False,
                               testing_sensor_ids: List[int] = None) -> List[polars.DataFrame]:
        """
        Get historical data for all sensors and reading types.
        
        Parameters
        ----------
        acceptable_range : Dict[str, List]
            Dictionary mapping reading types to acceptable ranges
        start_utc : int
            Start time as UTC timestamp
        end_utc : int
            End time as UTC timestamp
        out_of_scope : List[str], optional
            List of sensor name prefixes to exclude
        testing : bool, default=False
            If True, limit to first 3 sensors per reading type
        testing_sensor_ids : List[int], optional
            List to collect sensor IDs used during testing
            
        Returns
        -------
        List[polars.DataFrame]
            List of DataFrames containing historical readings
        """
        sensors = self.get_sensors(out_of_scope=out_of_scope, testing=testing,
                                 testing_sensor_ids=testing_sensor_ids or [])
        readings = []

        for reading_type in acceptable_range:
            # Handle SensorReadingLux: uses API ReadingType "SensorReading" filtered to LuxSensor types
            if reading_type == "SensorReadingLux":
                api_reading_type = "SensorReading"
                column_name = "SensorReadingLux"
                # Filter to LuxSensor types that have a SensorReading value
                if "SensorReading" not in sensors.columns or "SensorType" not in sensors.columns:
                    continue
                filtered = sensors.filter(
                    (polars.col("SensorType") == "LuxSensor") &
                    (polars.col("SensorReading").is_not_null()) &
                    (polars.col("SensorReading").is_nan().not_())
                )
                if filtered.is_empty():
                    continue
                sensor_ids = (
                    filtered["SensorID"]
                    .str.split(":")
                    .list.last()
                    .cast(polars.Int32)
                    .unique()
                    .to_list()
                )
            else:
                api_reading_type = reading_type
                column_name = None  # Use reading_type as column name (default)
                # Get sensor IDs that have this reading type - extract numeric ID from consolidated format
                if reading_type not in sensors.columns:
                    continue
                sensor_ids = (
                    sensors.filter(polars.col(reading_type).is_nan().not_())["SensorID"]
                    .str.split(":")
                    .list.last()
                    .cast(polars.Int32)
                    .unique()
                    .to_list()
                )

            if testing:
                sensor_ids = sensor_ids[0:3]
                if testing_sensor_ids is not None:
                    testing_sensor_ids.extend(sensor_ids)

            # Query API for each sensor
            pbar = tqdm.tqdm(total=len(sensor_ids), desc=f"Gather Coris readings: {reading_type}")

            for sensor_id in sensor_ids:
                data = self.get_historical_data(
                    sensor_id=sensor_id,
                    reading_type=api_reading_type,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    sensor_metadata=sensors,
                    column_name=column_name,
                )

                if not data.is_empty():
                    readings.append(data)

                pbar.update(1)

            pbar.close()

        return readings
    
    def get_current_readings(self, out_of_scope: List[str] = None,
                           testing: bool = False,
                           testing_sensor_ids: List[int] = None) -> polars.DataFrame:
        """
        Get current sensor readings from the Coris API.
        
        Parameters
        ----------
        out_of_scope : List[str], optional
            List of sensor name prefixes to exclude
        testing : bool, default=False
            If True, limit to testing sensors only
        testing_sensor_ids : List[int], optional
            List of sensor IDs to use when testing mode is enabled
            
        Returns
        -------
        polars.DataFrame
            Current sensor readings with standardized schema
        """
        sensors = self.get_sensors(
            out_of_scope=out_of_scope,
            testing=testing,
            testing_sensor_ids=testing_sensor_ids
        )
        
        # Add schema columns for compatibility including Historical
        sensors = sensors.with_columns([
            polars.lit("Coris").cast(polars.String).alias("Source"),
            polars.lit(None, dtype=polars.Int32).alias("customer_id"),
            polars.lit(False).alias("Historical"),
        ])
        
        return sensors
    
    def sample_raw_data(self, save_to_samples: bool = True) -> Dict[str, Any]:
        """
        Generate sample raw API data for testing and exploration.
        
        This method fetches raw data from the Coris API and optionally saves it
        to the samples directory with timestamps. Fetches sensor metadata and 
        sample historical data from multiple sensors for comprehensive testing.
        
        Parameters
        ----------
        save_to_samples : bool, default=True
            Whether to save the raw responses to samples/coris/ directory
            
        Returns
        -------
        Dict[str, Any]
            Dictionary containing the raw API responses:
            {
                'sensors': raw_sensors_response,
                'historical_data_samples': [list of raw historical data responses]
            }
        """
        results = {}
        
        print("🔍 Fetching raw sensor data...")
        # Get raw sensors data - make direct API call to get raw response
        url = f'{self.base_url}/cats/user/?ApiKey={self.api_key}&CatsUserID={self.cats_user_id}'
        
        self.logger.info(
            "API call: https://cats.corismonitoring.com/api/cats/user/?ApiKey=XXXX&CatsUserID=XXXX"
        )
        
        response = requests.get(url)
        
        if not response.ok:
            error_msg = f"Error getting sensors: {response.json()}"
            self.logger.error(error_msg)
            raise Exception(error_msg)
        
        raw_sensors = response.json()
        results['sensors'] = raw_sensors
        
        if save_to_samples:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"raw_sensors_response_{timestamp}.json"
            filepath = Path(__file__).parent.parent / "samples" / "coris" / filename
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with open(filepath, 'w') as f:
                json.dump(raw_sensors, f, indent=2, default=str)
            print(f"Saved sensors data to: {filepath}")
        
        sensors_data = raw_sensors.get('Sensors', [])
        print(f"✅ Retrieved {len(sensors_data)} sensors")
        
        # Try to get sample historical data from multiple sensors
        historical_data_samples = []
        max_sensors = 3  # Sample up to 3 sensors
        sensors_sampled = 0
        
        if len(sensors_data) > 0:
            print(f"🔍 Fetching historical data from up to {max_sensors} sensors...")
            
            # Get last 24 hours for quick sample
            end_time = datetime.datetime.now(datetime.timezone.utc)
            start_time = end_time - datetime.timedelta(hours=24)
            start_utc = int(start_time.timestamp())
            end_utc = int(end_time.timestamp())
            
            # Common reading types to sample
            reading_types = ['SensorReadingF', 'SensorReadingRh', 'SensorReadingC']
            
            for sensor in sensors_data:
                if sensors_sampled >= max_sensors:
                    break
                
                sensor_id = sensor.get('SensorID')
                sensor_name = sensor.get('SensorName', 'Unknown Sensor')
                
                if not sensor_id:
                    continue
                
                print(f"\n📊 Sensor: {sensor_name} (ID: {sensor_id})")
                
                # Try to get data for different reading types
                for reading_type in reading_types:
                    # Check if this sensor has this reading type
                    if reading_type in sensor and sensor.get(reading_type) is not None:
                        print(f"  🔍 Fetching {reading_type} data...")
                        
                        url = "&".join([
                            f'{self.base_url}/sensor/historical/?ApiKey={self.api_key}',
                            f"SensorID={sensor_id}",
                            f"ReadingType={reading_type}",
                            f"StartUTC={start_utc}",
                            f"EndUTC={end_utc}",
                            f"MinReadingSpacing={self.reading_spacing_seconds}",
                            "RequestedOutputFormat=raw",
                        ])
                        
                        logurl = "&".join([
                            "https://cats.corismonitoring.com/api/sensor/historical/?ApiKey=XXXX",
                            f"SensorID={sensor_id}",
                            f"ReadingType={reading_type}",
                            f"StartUTC={start_utc}",
                            f"EndUTC={end_utc}",
                            f"MinReadingSpacing={self.reading_spacing_seconds}",
                            "RequestedOutputFormat=raw",
                        ])
                        
                        self.logger.info(f"API call: {logurl}")
                        response = requests.get(url)
                        
                        if response.ok:
                            # Store raw response text since it's CSV data
                            raw_historical_data = response.text
                            
                            historical_data_samples.append({
                                'sensor_id': sensor_id,
                                'sensor_name': sensor_name,
                                'reading_type': reading_type,
                                'raw_data': raw_historical_data
                            })
                            
                            if save_to_samples:
                                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                                filename = f"raw_historical_data_{sensor_id}_{reading_type}_{timestamp}.csv"
                                filepath = Path(__file__).parent.parent / "samples" / "coris" / filename
                                filepath.parent.mkdir(parents=True, exist_ok=True)
                                with open(filepath, 'w') as f:
                                    f.write(raw_historical_data)
                                print(f"    💾 Saved to: {filepath}")
                            
                            print(f"    ✅ Retrieved {reading_type} data for sensor {sensor_id}")
                            
                            # Only sample one reading type per sensor to avoid too much data
                            break
                            
                        else:
                            print(f"    ⚠️ Could not get {reading_type} data: HTTP {response.status_code}")
                
                sensors_sampled += 1
        
        results['historical_data_samples'] = historical_data_samples
        
        # Print summary
        print(f"\n📋 Sample Data Summary:")
        print(f"   • Sensors: {len(sensors_data)} found")
        print(f"   • Historical data samples: {len(historical_data_samples)} collected from {sensors_sampled} sensors")
        
        if historical_data_samples:
            for i, sample in enumerate(historical_data_samples, 1):
                print(f"     {i}. {sample['sensor_name']} - {sample['reading_type']} (ID: {sample['sensor_id']})")
        
        if save_to_samples:
            print(f"   • Files saved to: samples/coris/")
        
        return results


