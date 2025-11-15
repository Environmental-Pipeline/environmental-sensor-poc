"""
Coris API Client Module

This module provides a client interface for the Coris environmental monitoring API.
It handles sensor data retrieval, historical data queries, and data transformation
to maintain compatibility with the EnvironmentData schema.
"""

import os
import requests
import polars
import datetime
import logging
import tqdm
from typing import List, Dict, Optional


class CorisClient:
    """
    Client for interacting with the Coris environmental monitoring API.
    
    This client handles:
    - Sensor metadata retrieval
    - Historical sensor data queries
    - Current sensor readings
    - Data transformation to standardized schema
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Initialize the Coris API client.
        
        Parameters
        ----------
        logger : Optional[logging.Logger]
            Logger instance for recording API interactions
        """
        # Read API key from environment variables
        api_key = None
        try:
            with open(".env") as f:
                for line in f:
                    if line.startswith("CORIS_API_KEY"):
                        api_key = line.split("=", 1)[1].strip()
                        break
        except FileNotFoundError:
            pass
        
        # Read CATS User ID from environment variables
        cats_user_id_str = None
        try:
            with open(".env") as f:
                for line in f:
                    if line.startswith("CATS_USER_ID"):
                        cats_user_id_str = line.split("=", 1)[1].strip()
                        break
        except FileNotFoundError:
            pass
        
        if not api_key:
            raise ValueError("CORIS_API_KEY not found in environment or .env file")
        
        if not cats_user_id_str:
            raise ValueError("CATS_USER_ID not found in environment or .env file")
        
        try:
            cats_user_id = int(cats_user_id_str)
        except ValueError:
            raise ValueError(f"CATS_USER_ID must be an integer, got: {cats_user_id_str}")
        
        self.api_key = api_key
        self.cats_user_id = cats_user_id
        self.logger = logger or logging.getLogger(__name__)
        self.base_url = "https://cats.corismonitoring.com/api"
    
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
            polars.lit("Coris").alias("source"),
            polars.lit(None, dtype=polars.Int32).alias("customer_id"),
        ])
        
        return sensors
    
    def get_historical_data(self, sensor_id: int, reading_type: str, 
                          start_utc: int, end_utc: int,
                          sensor_metadata: polars.DataFrame) -> polars.DataFrame:
        """
        Get historical sensor data for a specific sensor and reading type.
        
        Parameters
        ----------
        sensor_id : int
            Coris sensor ID
        reading_type : str
            Type of reading (e.g., 'SensorReadingF', 'SensorReadingRh')
        start_utc : int
            Start time as UTC timestamp
        end_utc : int
            End time as UTC timestamp
        sensor_metadata : polars.DataFrame
            Sensor metadata to attach to readings
            
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
            "MinReadingSpacing=600",  # every 10 minutes
            "RequestedOutputFormat=raw",
        ])
        
        # Create sanitized URL for logging
        logurl = "&".join([
            "https://cats.corismonitoring.com/api/sensor/historical/?ApiKey=XXXX",
            f"SensorID={sensor_id}",
            f"ReadingType={reading_type}",
            f"StartUTC={start_utc}",
            f"EndUTC={end_utc}",
            "MinReadingSpacing=600",
            "RequestedOutputFormat=raw",
        ])
        
        self.logger.info(f"API call: {logurl}")
        response = requests.get(url)
        
        # Check for errors
        if not response.ok:
            error_msg = f"Error getting historical {reading_type} for {sensor_id}: {response.json()}"
            self.logger.error(error_msg)
            return polars.DataFrame()  # Return empty DataFrame instead of crashing
        
        # Parse CSV response
        data = polars.read_csv(response.content, has_header=False)
        data.columns = ["SensorReadingUTC", reading_type]
        
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
        
        # Add standardized schema columns
        current_utc = self.get_current_utc()
        data = data.with_columns([
            polars.lit("Coris").alias("source"),
            polars.lit(None, dtype=polars.Int32).alias("customer_id"),
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
            # Get sensor IDs that have this reading type - extract numeric ID from consolidated format
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
            pbar = tqdm.tqdm(total=len(sensor_ids), desc=f"Gather readings: {reading_type}")
            
            for sensor_id in sensor_ids:
                data = self.get_historical_data(
                    sensor_id=sensor_id,
                    reading_type=reading_type,
                    start_utc=start_utc,
                    end_utc=end_utc,
                    sensor_metadata=sensors
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
        
        # Add schema columns for compatibility
        sensors = sensors.with_columns([
            polars.lit("Coris").cast(polars.String).alias("source"),
            polars.lit(None, dtype=polars.Int32).alias("customer_id"),
        ])
        
        return sensors


