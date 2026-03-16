"""
Class for managing environmental sensor data from multiple APIs.

Initializes with a historical data pull and provides get_current_readings() method for fetching current data. 
Consolidates readings into an analytical database of parquet files that can be read with duckdb.

## Commands:
- Create HTML documentation in `docs/clients`: `pdoc EnvironmentData.py -o docs/ --no-search` 
- See experiments/1-examples-pull-data.ipynb for example usage.
- Test with `pytest tests/test_EnvironmentData.py` or `python experiments/run_environment_data.py`
"""

import os
import polars
import numpy
import logging
import datetime
import warnings
from clients.conserv_client import ConservAPIClient
from clients.coris_client import CorisClient
from clients.licor_client import LicorClient
from modules import validation
from modules import consolidation
from modules import sensor_name_validator
from modules import rejected_sensors_tracker
from modules import weather_enrichment


class EnvironmentData:
    """
    Manages environmental sensor data from multiple API sources (Conserv, Coris, LI-COR).
    
    Attributes
    ----------
    home_directory : str
        Base directory for finding .env file and resolving relative paths.
    data_path : str
        Path to store parquet files which make up the database.
    testing : bool
        Flag indicating if running in testing mode (limited sensors for fast tests).
    testing_sensor_ids : list
        List of sensor IDs to use when testing mode is enabled.
    out_of_scope : list
        List of sensor name patterns to exclude from data collection.
    conserv_enabled : bool
        Whether Conserv API integration is enabled.
    coris_enabled : bool
        Whether Coris API integration is enabled.
    licor_enabled : bool
        Whether LI-COR API integration is enabled.
    logger : logging.Logger
        Main logger instance for general logging (EnvironmentData.log).
    logger_err : logging.Logger
        Error-only logger instance (EnvironmentData-Errors.log).
    conserv_client : ConservAPIClient or None
        Client for Conserv API interactions (None if not enabled).
    coris_client : CorisClient or None
        Client for Coris API interactions (None if not enabled).
    licor_client : LicorClient or None
        Client for LI-COR API interactions (None if not enabled).
    acceptable_range : dict
        Dictionary defining acceptable value ranges for sensor readings.
        Keys are reading types (e.g., 'SensorReadingF', 'SensorReadingRh').
    cron_status : str
        Current initialization status ('not-initialized' or 'initialized').
    """

    def __init__(
        self,
        data_path: str = "./data/",
        days_back: int = int(365 * 2),
        testing: bool = False,
        conserv_enabled: bool = False,
        coris_enabled: bool = False,
        licor_enabled: bool = False,
        home_directory: str = ".",
    ):
        """
        Initialize resources for managing the environmental readings.

        Parameters
        ----------
        data_path : str, default='./data/'
            Path to store the parquet files which make up the database.

        days_back : int, default=int(365 * 2)
            Number of days of historical data to pull when initializing the database.

        testing : bool, default=False
            Create a class in "testing mode". Only a few sensors will be included so that tests can run quickly and use fewer API calls.

        conserv_enabled : bool, default=False
            Enable Conserv API integration for additional sensor data sources.

        coris_enabled : bool, default=True
            Enable Coris API integration for primary sensor data source.

        licor_enabled : bool, default=False
            Enable LI-COR API integration for additional sensor data sources.

        home_directory : str, default="."
            Base directory for finding .env file and resolving relative paths.
            Use ".." when running from subdirectories like experiments/.

        Returns
        -------
        EnvironmentData
            Initialized EnvironmentData object with the following key attributes:
            
            home_directory : str
                Base directory for .env file location
            data_path : str
                Path to parquet database files
            testing : bool
                Testing mode flag
            testing_sensor_ids : list
                Sensor IDs for testing
            out_of_scope : list
                Excluded sensor patterns
            conserv_enabled : bool
                Conserv API integration status
            coris_enabled : bool
                Coris API integration status
            licor_enabled : bool
                LI-COR API integration status
            logger : logging.Logger
                Main logger (EnvironmentData.log)
            logger_err : logging.Logger
                Error logger (EnvironmentData-Errors.log)
            conserv_client : ConservAPIClient or None
                Conserv API client
            coris_client : CorisClient or None
                Coris API client
            licor_client : LicorClient or None
                LI-COR API client
            acceptable_range : dict
                Acceptable sensor value ranges
            cron_status : str
                Initialization status
        """

        # Save inputs to the class instance.
        self.home_directory = home_directory
        # Make data_path relative to home_directory if it's a relative path
        if os.path.isabs(data_path):
            self.data_path = data_path
        else:
            self.data_path = os.path.join(home_directory, data_path)
        self.testing = testing
        self.testing_sensor_ids = []
        self.out_of_scope = ['-80', 'Cryo tank', 'Water']
        self.conserv_enabled = conserv_enabled
        self.coris_enabled = coris_enabled
        self.licor_enabled = licor_enabled

        # Check that at least one data source is enabled
        if not (conserv_enabled or coris_enabled or licor_enabled):
            raise ValueError(
                "At least one data source must be enabled. "
                "Set conserv_enabled=True, coris_enabled=True, or licor_enabled=True"
            )

        # Check that .env file exists before initializing clients
        env_file_path = os.path.join(home_directory, ".env")
        if not os.path.exists(env_file_path):
            raise FileNotFoundError(
                f".env file not found at {env_file_path}. "
                f"Please create a .env file with required API keys (LICOR_API_KEY, CORIS_API_KEY, CATS_USER_ID). "
                f"If running from a subdirectory like experiments/, set home_directory='..'"
            )

        # Create the data folder.
        if not os.path.exists(self.data_path):
            os.makedirs(self.data_path)

        # Set up logging to allow status to be viewed when run as a cron job.
        # https://docs.python.org/3/howto/logging-cookbook.html#logging-cookbook
        self.logger = logging.getLogger("EnvironmentData")
        self.logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        fh = logging.FileHandler(
            f"{self.data_path}/EnvironmentData.log"
        )  # log to EnvironmentData.log in the data folder.
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)

        # ch = logging.StreamHandler() # log to console. disabling to prevent breaking tdqm
        # ch.setFormatter(formatter)
        # self.logger.addHandler(ch)

        # Set up a second logger for errors only.
        self.logger_err = logging.getLogger("EnvironmentData-Errors")
        self.logger_err.setLevel(logging.ERROR)
        fh = logging.FileHandler(f"{self.data_path}/EnvironmentData-Errors.log")
        fh.setFormatter(formatter)
        self.logger_err.addHandler(fh)

        # Read cron status, or initialize the status file.
        if os.path.exists(f"{self.data_path}/cron_status.txt"):
            with open(f"{self.data_path}/cron_status.txt") as f:
                self.cron_status = f.read()
        else:
            self.update_cron_status("not-initialized")

        # Initialize Conserv API client if enabled
        self.conserv_client = None
        if self.conserv_enabled:
            self.conserv_client = ConservAPIClient(self.logger, home_directory=self.home_directory)

        # Initialize Coris API clients if enabled (supports multiple accounts)
        self.coris_client = None
        self.coris_clients = []
        if self.coris_enabled:
            # Primary account (Yale Museum) - reads from CORIS_API_KEY and CATS_USER_ID
            self.coris_client = CorisClient(self.logger, home_directory=self.home_directory)
            self.coris_clients.append(self.coris_client)

            # Additional accounts (e.g., Yale Libraries for lux sensors)
            additional_accounts = self._read_additional_coris_accounts()
            for account in additional_accounts:
                client = CorisClient(
                    self.logger,
                    home_directory=self.home_directory,
                    api_key=account["api_key"],
                    cats_user_id=account["cats_user_id"],
                )
                self.coris_clients.append(client)

        # Initialize LI-COR API client if enabled
        self.licor_client = None
        if self.licor_enabled:
            self.licor_client = LicorClient(self.logger, home_directory=self.home_directory)

        # Set up the readings data structure that will be used throughout.
        self.acceptable_range = {"SensorReadingF": [], "SensorReadingRh": [], "SensorReadingLux": []}

        # Debug: Show which data sources are actually enabled
        enabled_sources = []
        if self.conserv_enabled and self.conserv_client:
            enabled_sources.append("Conserv") 
        if self.coris_enabled and self.coris_clients:
            enabled_sources.append(f"Coris ({len(self.coris_clients)} account(s))")
        if self.licor_enabled and self.licor_client:
            enabled_sources.append("LI-COR")
        
        print(f"DEBUG: Enabled data sources: {enabled_sources}")
        self.logger.info(f"Enabled data sources: {enabled_sources}")

        # Initialize the database by creating a parquet file for each reading type and populate it with historical data.
        self.initialize_database(days_back=days_back)

    def _read_additional_coris_accounts(self):
        """
        Read additional Coris account credentials from the .env file.
        Looks for CORIS_API_KEY_LIBRARIES / CATS_USER_ID_LIBRARIES pairs.

        Returns
        -------
        list[dict] : List of dicts with 'api_key' and 'cats_user_id' keys.
        """
        accounts = []
        env_file_path = os.path.join(self.home_directory, ".env")

        # Read all env variables into a dict for exact key matching
        env_vars = {}
        with open(env_file_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()

        # Check for Yale Libraries account
        api_key = env_vars.get("CORIS_API_KEY_LIBRARIES")
        cats_user_id = env_vars.get("CATS_USER_ID_LIBRARIES")
        if api_key and cats_user_id and not api_key.startswith("your_"):
            accounts.append({"api_key": api_key, "cats_user_id": cats_user_id})
            self.logger.info(f"Found additional Coris account: CATS_USER_ID_LIBRARIES={cats_user_id}")

        return accounts

    # function to update cron status.
    def update_cron_status(self, status: str):
        """
        cron_status is used to prevent new cron-triggered API queries during initialization while the historical data is being pulled. Update the status of the cron job.
        There are only two statuses: "not-initialized" and "initialized".
        This function updates the status by writing to "cron_status.txt" and setting the class instance variable self.cron_status.

        Parameters
        ----------
        status : str {"not-initialized", "initialized"}
            New status of the cron job.
        """

        self.cron_status = status
        with open(f"{self.data_path}/cron_status.txt", "w") as f:
            f.write(status)

    def close(self):
        """
        Close the class.
        Disconnects the connection to the log files so they can be deleted, etc.
        """

        for handler in self.logger.handlers:
            handler.close()
            self.logger.removeHandler(handler)
        del self.logger

        for handler in self.logger_err.handlers:
            handler.close()
            self.logger_err.removeHandler(handler)
        del self.logger_err

    def error(self, msg, raise_exception=False):
        """
        Helper function for errors.
        Logs an error message to the both log files (error regular) and raises an exception if you choose.

        Parameters
        ----------
        msg : str
            Error message.

        raise_exception : bool, default=False
            If True, log the error and raise an exception, which will halt processing. If False, log the error, generate a warning, and continue processing.
        """

        self.logger_err.error(msg)
        self.logger.error(msg)

        if raise_exception:
            raise Exception(msg)
        else:
            warnings.warn(msg)

    def initialize_database(self, days_back: int):
        """
        Get and save historical data for each sensor.

        Parameters
        ----------
        days_back : int
            Number of days of historical data to pull.
        """

        # If the data already exists, initialization is not necessary.
        if self.cron_status != "not-initialized":
            return

        # Make a log entry and gather current and starting UTC.
        # self.logger.info("initialize_database")
        current_utc = self.get_current_utc()
        start_utc = current_utc - days_back * 24 * 60 * 60

        # ============ CONSERV HISTORICAL DATA PROCESSING ============
        conserv_readings = []
        if self.conserv_enabled and self.conserv_client:
            self.logger.info("Fetching Conserv historical data for all customers")

            try:
                # Get Conserv data for the same time period as Coris
                conserv_data = self.conserv_client.get_historical_data(
                    start_utc=start_utc,
                    end_utc=current_utc,
                    test=self.testing,
                )

                if conserv_data is not None and not conserv_data.is_empty():
                    # Conserv data is already transformed by clients/conserv_client.py
                    # Clean and validate Conserv data
                    conserv_data = self.clean_validate_sensors(
                        sensors=conserv_data, step="initialize_database_conserv"
                    )

                    # Add to readings list
                    conserv_readings.append(conserv_data)
                    self.logger.info(
                        f"Successfully processed {conserv_data.shape[0]} Conserv historical records"
                    )
                else:
                    self.logger.info(
                        "No Conserv historical data available for the specified period"
                    )

            except Exception as e:
                self.logger.warning(f"Failed to fetch Conserv historical data: {e}")
                # Continue without Conserv data - don't fail the entire initialization

        # ============ CORIS HISTORICAL DATA PROCESSING ============
        readings = []
        if self.coris_enabled and self.coris_clients:
            # Get historical data from all Coris accounts
            for client_idx, coris_client in enumerate(self.coris_clients):
                coris_readings = coris_client.get_historical_data_bulk(
                    acceptable_range=self.acceptable_range,
                    start_utc=start_utc,
                    end_utc=current_utc,
                    out_of_scope=self.out_of_scope,
                    testing=self.testing,
                    testing_sensor_ids=self.testing_sensor_ids
                )

                # Clean and validate the Coris data
                for i, data in enumerate(coris_readings):
                    coris_readings[i] = self.clean_validate_sensors(
                        sensors=data, step=f"initialize_database_coris_{client_idx}"
                    )

                readings.extend(coris_readings)

        # ============ LI-COR HISTORICAL DATA PROCESSING ============
        licor_readings = []
        if self.licor_enabled and self.licor_client:
            # print("DEBUG: Fetching LI-COR historical data")
            # self.logger.info("Fetching LI-COR historical data")
            
            try:
                # Get LI-COR data for the same time period
                licor_data_list = self.licor_client.get_historical_data(
                    start_utc=start_utc,
                    end_utc=current_utc,
                    out_of_scope=self.out_of_scope,
                    testing=self.testing
                )
                
                if licor_data_list is not None and len(licor_data_list) > 0:
                    # Clean and validate each DataFrame in the list
                    total_records = 0
                    for i, data in enumerate(licor_data_list):
                        if data is not None and not data.is_empty():
                            licor_data_list[i] = self.clean_validate_sensors(
                                sensors=data, step="initialize_database_licor"
                            )
                            total_records += licor_data_list[i].shape[0]
                    
                    # Add all DataFrames to readings list
                    licor_readings.extend(licor_data_list)
                    # print(f"DEBUG: Successfully processed {total_records} LI-COR historical records from {len(licor_data_list)} DataFrames")
                    # self.logger.info(
                    #     f"Successfully processed {total_records} LI-COR historical records from {len(licor_data_list)} DataFrames"
                    # )
                else:
                    print("DEBUG: No LI-COR historical data available for the specified period")
                    self.logger.info(
                        "No LI-COR historical data available for the specified period"
                    )
                    
            except Exception as e:
                print(f"DEBUG: Failed to fetch LI-COR historical data: {e}")
                self.logger.warning(f"Failed to fetch LI-COR historical data: {e}")
                # Continue without LI-COR data - don't fail the entire initialization
        else:
            print(f"DEBUG: LI-COR not enabled or client not initialized. Enabled: {self.licor_enabled}, Client: {self.licor_client}")

        # ============ COMBINE ALL DATA SOURCES ============
        # Combine Conserv, Coris, and LI-COR readings into a single polars DataFrame
        all_readings = conserv_readings + readings + licor_readings
        dt = (
            polars.concat(all_readings, how="diagonal")
            if all_readings
            else polars.DataFrame()
        )

        if dt.is_empty():
            error_msg = "No data from any source - initialization failed"
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

        # Standardize the DataFrame (rename columns, filter, and reorder)
        dt = self.standardize_sensor_dataframe(dt)

        # Write the database file.
        dt.write_parquet(f"{self.data_path}/sensor_readings.parquet")
        self.logger.info(
            f"Historical data initialization complete: {dt.shape[0]} total records from {len(all_readings)} sources"
        )

        self.update_cron_status("initialized")

    def get_current_utc(self) -> int:
        """
        Get the current UTC timestamp in seconds.

        Returns
        -------
        int : Current UTC timestamp.
        """

        return int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    def enforce_schema(
        self, df: polars.DataFrame, step_name: str = ""
    ) -> polars.DataFrame:
        """
        SCHEMA GATE: Enforce master schema on any DataFrame before concatenation.
        This prevents ALL type mismatch errors by converting data to expected types.

        Parameters
        ----------
        df : polars.DataFrame
            DataFrame to enforce schema on
        step_name : str
            Name of the step for logging

        Returns
        -------
        polars.DataFrame
            DataFrame with all columns converted to master schema types
        """
        return validation.enforce_schema(df, self.logger, step_name)

    def get_current_readings(self) -> dict:
        """
        Get current readings from the API. Validate the data. Send alerts.
        Save the readings to a file in the new-readings folder.
        This function will save data as separate files to facilitate easy tracking of new data vs consolidated data.
        A batch process will clean, validate, and consolidate readings later.
        """

        if self.cron_status == "not-initialized":
            return

        # Make a log entry and gather the current UTC.
        current_utc = self.get_current_utc()
        # self.logger.info(f"get_current_readings: {current_utc}")

        # ============ CONSERV DATA PROCESSING ============
        conserv_sensors = None
        if self.conserv_enabled and self.conserv_client:
            self.logger.info("Fetching current Conserv data for all customers")

            try:
                # Get current readings.
                conserv_data = self.conserv_client.get_current_readings(
                    test=self.testing
                )

                if conserv_data is not None and not conserv_data.is_empty():
                    self.logger.info(f"Conserv current data shape: {conserv_data.shape}")

                    # Conserv data is already transformed by clients/conserv_client.py
                    conserv_sensors = conserv_data

                    if conserv_sensors is not None and not conserv_sensors.is_empty():
                        # Clean and validate Conserv data
                        conserv_sensors = self.clean_validate_sensors(
                            sensors=conserv_sensors, step="get_current_readings_conserv"
                        )

                        if (
                            conserv_sensors is not None
                            and not conserv_sensors.is_empty()
                        ):
                            self.logger.info(
                                f"Successfully processed {conserv_sensors.shape[0]} current Conserv records"
                            )
                            print(f"✓ Conserv: {conserv_sensors.shape[0]} records")
                        else:
                            self.logger.warning(
                                "Conserv data was cleaned out completely during validation!"
                            )
                    else:
                        self.logger.warning(
                            "Conserv transformation returned empty data!"
                        )
                else:
                    self.logger.info("No current Conserv data available")

            except Exception as e:
                self.logger.warning(f"Failed to fetch current Conserv data: {e}")
                import traceback

                self.logger.warning(
                    f"Conserv error traceback: {traceback.format_exc()}"
                )
                # Continue with Coris data only - don't fail the entire process

        # ============ CORIS DATA PROCESSING ============
        coris_sensors = None
        if self.coris_enabled and self.coris_clients:
            self.logger.info("Fetching current Coris data")

            coris_dfs = []
            for client_idx, coris_client in enumerate(self.coris_clients):
                client_sensors = coris_client.get_current_readings(
                    out_of_scope=self.out_of_scope,
                    testing=self.testing,
                    testing_sensor_ids=self.testing_sensor_ids
                )

                # Convert data types to match expected schema before validation
                if not client_sensors.is_empty():
                    for reading in self.acceptable_range:
                        if reading in client_sensors.columns:
                            client_sensors = client_sensors.with_columns(
                                polars.col(reading).cast(polars.Float32)
                            )
                    coris_dfs.append(client_sensors)

            if coris_dfs:
                # Normalize schemas across Coris accounts to prevent type mismatches
                if len(coris_dfs) > 1:
                    for i, df in enumerate(coris_dfs):
                        coris_dfs[i] = df.cast({col: polars.Float64 for col in df.columns if df[col].dtype == polars.Int64 and col.startswith('SensorCalibration')})
                coris_sensors = polars.concat(coris_dfs, how="diagonal")
                self.validate_sensors(
                    sensors=coris_sensors, utc=current_utc, step="get_current_readings_coris"
                )
                print(f"✓ Coris: {coris_sensors.shape[0]} records")
            else:
                coris_sensors = polars.DataFrame()
        else:
            # Create empty DataFrame with required schema when Coris is disabled
            coris_sensors = polars.DataFrame()
            self.logger.info("Coris integration is disabled")

        # DEBUG: Log Coris schema types
        # self.logger.info(
        #     f"CORIS schema types: {dict(zip(coris_sensors.columns, [str(dtype) for dtype in coris_sensors.dtypes]))}"
        # )

        # ============ LI-COR DATA PROCESSING ============
        licor_sensors = None
        if self.licor_enabled and self.licor_client:
            self.logger.info("Fetching current LI-COR data")

            try:
                # Get current readings from LI-COR API
                licor_sensors = self.licor_client.get_current_readings(
                    out_of_scope=self.out_of_scope,
                    testing=self.testing,
                    testing_device_limit=3 if self.testing else None
                )

                if licor_sensors is not None and not licor_sensors.is_empty():
                    self.logger.info(f"Raw LI-COR data shape: {licor_sensors.shape}")

                    # Clean and validate LI-COR data
                    licor_sensors = self.clean_validate_sensors(
                        sensors=licor_sensors, step="get_current_readings_licor"
                    )

                    if licor_sensors is not None and not licor_sensors.is_empty():
                        self.logger.info(
                            f"Successfully processed {licor_sensors.shape[0]} current LI-COR records"
                        )
                        print(f"✓ LI-COR: {licor_sensors.shape[0]} records")
                    else:
                        self.logger.warning(
                            "LI-COR data was cleaned out completely during validation!"
                        )
                else:
                    self.logger.info("No current LI-COR data available")

            except Exception as e:
                self.logger.warning(f"Failed to fetch current LI-COR data: {e}")
                import traceback

                self.logger.warning(
                    f"LI-COR error traceback: {traceback.format_exc()}"
                )
                # Continue with other data sources - don't fail the entire process

        # ============ COMBINE ALL DATA SOURCES ============
        # Prepare list of valid data sources for merging
        data_sources = []
        source_info = []

        # Add Conserv data if enabled and available
        if conserv_sensors is not None and not conserv_sensors.is_empty():
            conserv_sensors = self.enforce_schema(
                conserv_sensors, "Conserv_Current_Readings"
            )
            data_sources.append(conserv_sensors)
            source_info.append(f"CONSERV: {conserv_sensors.shape[0]} rows")

        if coris_sensors is not None and not coris_sensors.is_empty():
            coris_sensors = self.enforce_schema(coris_sensors, "Coris_Current_Readings")
            data_sources.append(coris_sensors)
            source_info.append(f"CORIS: {coris_sensors.shape[0]} rows")

        if licor_sensors is not None and not licor_sensors.is_empty():
            licor_sensors = self.enforce_schema(
                licor_sensors, "LI-COR_Current_Readings"
            )
            data_sources.append(licor_sensors)
            source_info.append(f"LI-COR: {licor_sensors.shape[0]} rows")

        # Log merging information and combine data sources
        if len(data_sources) > 1:
            # self.logger.info("MERGING DATA SOURCES:")
            for info in source_info:
                self.logger.info(f"  {info}")

            all_sensors = polars.concat(data_sources, how="diagonal")
        elif len(data_sources) == 1:
            all_sensors = data_sources[0]
            self.logger.info(f"Current readings: {source_info[0]} only")
        else:
            # No data sources available
            all_sensors = polars.DataFrame()
            self.logger.warning("No data sources enabled or available - creating empty dataset")

        # ============ PROCESS ALERTS AND SAVE ============
        # Process alerts on combined data
        self.send_alerts(all_sensors, current_utc)

        # Standardize the current readings data (rename columns, filter, and reorder) 
        if not all_sensors.is_empty():
            all_sensors = self.standardize_sensor_dataframe(all_sensors)

        # Save the new-readings file. A daily process will pull these later to clean, validate, and consolidate them into the database.
        os.makedirs(f"{self.data_path}/new-readings/", exist_ok=True)
        all_sensors.write_parquet(
            f"{self.data_path}/new-readings/{current_utc}.parquet"
        )

    def consolidate_readings(self):
        """
        Combine new and historical readings into one database. Build (or re-build) the analytical tables.
        If successful, remove the new-readings files that have been processed into the database.
        Meant to run as a daily batch process to consolidate readings made throughout the day.
        """

        if self.cron_status == "not-initialized":
            return

        # Make a log entry.
        # self.logger.info("consolidate_readings")
        new_readings = []

        # Read new readings from the parquet files saved by calls to get_current_readings.
        # These are deleted after each consolidation, so these files will always be the un-consolidated files.
        if os.path.exists(f"{self.data_path}/new-readings/"):
            files = os.listdir(f"{self.data_path}/new-readings/")
            files_read = []
            for file in files:

                # Read any per-file parquet and collect them
                try:
                    new_readings.append(
                        polars.read_parquet(f"{self.data_path}/new-readings/{file}")
                    )
                    files_read.append(file)
                except Exception:
                    # swallow and continue to the next file
                    pass
        else:
            files_read = []

        # Combine the readings into a single polars DataFrame.
        dt = polars.concat(new_readings) if new_readings else polars.DataFrame()
        self.logger.info(f"{dt.shape[0]} new readings after schema enforcement.")

        # Clean the data.
        try:
            historical = polars.read_parquet(
                f"{self.data_path}/sensor_readings.parquet"
            )
        except FileNotFoundError:
            # Create empty DataFrame with correct schema for first run
            historical = polars.DataFrame()

        # Only clean/validate new readings if there are any
        if not dt.is_empty():
            dt = self.clean_validate_sensors(
                sensors=dt, historical=historical, step="consolidate_readings"
            )

            # Apply schema gate before concatenating with historical data
            dt = self.enforce_schema(dt, "CleanedNewReadings")
            if not historical.is_empty():
                historical = self.enforce_schema(historical, "HistoricalData")

            # Append these to the database.
            dt = polars.concat([historical, dt], how="diagonal")
        else:
            # No new readings - just use historical data
            self.logger.info("No new readings to consolidate. Using historical data only.")
            dt = historical

        # Remove duplicate readings based on SensorID and SensorReadingUTC
        # This handles cases where Conserv (15-min intervals) might pull the same readings multiple times
        initial_count = dt.shape[0]
        dt = dt.unique(subset=["SensorID", "SensorReadingUTC"])
        final_count = dt.shape[0]
        
        if initial_count != final_count:
            duplicates_removed = initial_count - final_count
            self.logger.info(f"Removed {duplicates_removed} duplicate readings (SensorID + SensorReadingUTC)")

        # ============ FILTER INVALID SENSOR NAMES ============
        # Filter out sensors that don't conform to the Yale naming convention
        # This ensures data quality before the final parquet is written
        if not dt.is_empty() and "Source" in dt.columns:
            valid_df, invalid_df = sensor_name_validator.filter_invalid_sensors(
                df=dt,
                logger=self.logger
            )

            # Generate rejected sensors report if there are any invalid sensors
            if not invalid_df.is_empty():
                unique_invalid = invalid_df.select("SensorName").unique().shape[0]
                self.logger.info(
                    f"Sensor name validation: filtering out {invalid_df.shape[0]} readings "
                    f"from {unique_invalid} sensors with non-conforming names"
                )

                # Generate the rejected sensors CSV report
                csv_path = sensor_name_validator.generate_rejected_sensors_report(
                    invalid_df=invalid_df,
                    data_path=self.data_path,
                    logger=self.logger
                )
                if csv_path:
                    self.logger.info(f"Rejected sensors report written to: {csv_path}")

                # Update persistent tracking
                new_count, fixed_count, active_count = rejected_sensors_tracker.update_tracking_file(
                    invalid_df=invalid_df,
                    tracking_file_path=f"{self.data_path}/rejected_sensors_tracking.csv",
                    logger=self.logger
                )
                self.logger.info(f"Rejected sensors tracking: {new_count} new, {fixed_count} fixed, {active_count} still active")

                # Generate report for units
                report_path = rejected_sensors_tracker.generate_needs_attention_report(
                    tracking_file_path=f"{self.data_path}/rejected_sensors_tracking.csv",
                    output_path=f"{self.data_path}/rejected_sensors_needs_attention.csv",
                    logger=self.logger
                )

            # Use only valid sensors for the final output
            dt = valid_df

        # ============ WEATHER ENRICHMENT ============
        # Enrich sensor readings with outdoor weather data from Open-Meteo Archive API
        # Drop existing weather columns first to prevent double C-to-F conversion
        # on rows that were already enriched in a prior consolidation cycle.
        if not dt.is_empty():
            weather_cols = [c for c in dt.columns if c.startswith("weather_")]
            if weather_cols:
                self.logger.info(f"Dropping {len(weather_cols)} existing weather columns before re-enrichment")
                dt = dt.drop(weather_cols)
            coordinates_path = os.path.join(self.home_directory, "data", "building_coordinates.csv")
            if os.path.exists(coordinates_path):
                try:
                    dt = weather_enrichment.enrich_sensors_with_weather(
                        sensors=dt,
                        coordinates_path=coordinates_path,
                        cache_dir=os.path.join(self.data_path, "weather_cache"),
                        logger=self.logger,
                    )
                except Exception as e:
                    self.logger.warning(f"Weather enrichment failed, continuing without: {e}")
            else:
                self.logger.info(f"Building coordinates not found at {coordinates_path}, skipping weather enrichment")

        # Add time difference between readings for each sensor within each source, device, and type
        # Only calculate for non-Historical data to avoid mixing backfilled data with real-time readings
        if not dt.is_empty():
            dt = dt.sort(["Source", "DeviceID", "SensorType", "SensorID", "SensorReadingUTC"])
            
            # Calculate time differences grouping by Historical flag to prevent mixing historical and real-time data
            dt = dt.with_columns(
                (
                    polars.col("SensorReadingUTC")
                    - polars.col("SensorReadingUTC").shift(1).over(["Source", "DeviceID", "SensorType", "SensorID", "Historical"])
                ).alias("SensorReadingUTC_SecondsFromPrior")
            )
            
            # Then set Historical data timing to None since it shouldn't have real-time timing
            dt = dt.with_columns(
                polars.when(polars.col("Historical") == True)
                .then(None)
                .otherwise(polars.col("SensorReadingUTC_SecondsFromPrior"))
                .alias("SensorReadingUTC_SecondsFromPrior")
            )
        else:
            dt = dt.with_columns(
                polars.lit(None, dtype=polars.Int64).alias(
                    "SensorReadingUTC_SecondsFromPrior"
                )
            )

        # Standardize the DataFrame (rename columns, filter, and reorder)
        dt = self.standardize_sensor_dataframe(dt)

        # Write the file.
        dt.write_parquet(f"{self.data_path}/sensor_readings.parquet")
        # self.logger.info(f"{dt.shape[0]} total readings.")

        # Run validation with return_results=True to collect all validation results
        validation_results = self.validate_sensors(
            sensors=dt,
            step="consolidate_final",
            return_results=True
        )

        # If all this was successful, remove the new-readings files to prepare for the next consolidation.
        for file in files_read:
            os.remove(f"{self.data_path}/new-readings/{file}")

        # Refresh the devices table.
        devices = self.build_devices(dt)
        self.validate_devices(devices)
        devices.write_parquet(f"{self.data_path}/device_readings.parquet")

        # Update lookup tables and cubes.
        self.update_lookups()
        self.update_cubes()
        
        # Generate diagnostics CSV including gaps and alerts
        # This must happen AFTER update_lookups() so sensors.parquet exists for building info check
        validation.generate_validation_results(
            sensors=dt,
            validation_results=validation_results,
            acceptable_range=self.acceptable_range,
            data_path=self.data_path,
            logger=self.logger,
            step="consolidate_readings"
        )
        
        # Export data to Excel.
        self.export_to_excel()

    def match_types(
        self, data: polars.DataFrame, match: polars.DataFrame
    ) -> polars.DataFrame:
        """
        Modify the column data types of one DataFrame to match another.
        Useful to prevent errors during concatenation.

        Parameters
        ----------
        data : polars.DataFrame
            DataFrame to match (data types may get changed).

        match : polars.DataFrame
            DataFrame to match column data types to.

        Returns
        -------
        polars.DataFrame: Same data as the "data" DataFrame, but with types that match the "match" DataFrame.
        """

        for col in match.columns:
            if col in data.columns and data[col].dtype != match[col].dtype:
                data = data.with_columns(data[col].cast(match[col].dtype))

        return data

    def build_devices(self, data: polars.DataFrame) -> polars.DataFrame:
        """
        Reformat the sensor data to create a DataFrame of devices.
        Each Device can have multiple sensors.
        In some cases it is easier to work with data indexed by Device with multiple types of readings (Temperature, Humidity)
        in the same row instead of Sensors which only have one type of reading per row.

        Parameters
        ----------
        data : polars.DataFrame
            DataFrame containing the sensor data.

        Returns
        -------
        polars.DataFrame: DataFrame containing the devices data.
        """
        return consolidation.build_devices(
            data=data,
            acceptable_range=self.acceptable_range,
            error_callback=self.error,
        )

    def clean_validate_sensors(
        self,
        sensors: polars.DataFrame,
        historical: polars.DataFrame = polars.DataFrame(),
        step: str = "",
    ) -> polars.DataFrame:
        """
        Clean the sensor readings data. Delegates to validation.clean_validate_sensors().
        """
        return validation.clean_validate_sensors(
            sensors=sensors,
            acceptable_range=self.acceptable_range,
            logger=self.logger,
            historical=historical,
            step=step,
            error_callback=self.error
        )

    def standardize_sensor_dataframe(self, dt: polars.DataFrame) -> polars.DataFrame:
        """
        Standardize sensor data DataFrame by renaming columns, filtering, and reordering.
        
        Parameters
        ----------
        dt : polars.DataFrame
            Input DataFrame to standardize
            
        Returns
        -------
        polars.DataFrame
            Standardized DataFrame with proper ordering
        """

        # Apply standard column ordering and filter to only desired columns
        desired_columns = [
            "SensorReadingUTC",
            "QueryUTC",
            "Source",  
            "DeviceID", 
            "DeviceName",
            "SensorID", 
            "SensorName", 
            "SensorType",
            "SensorReadingF",
            "SensorReadingRh",
            "SensorReadingLux",
            "SensorReadingUTC_SecondsFromPrior",
            "Historical",
            "weather_temp_f",
            "weather_humidity_pct",
            "weather_dew_point_f",
            "weather_apparent_temp_f",
            "weather_pressure_msl_hpa",
            "weather_pressure_surface_hpa",
            "weather_precip_mm",
            "weather_rain_mm",
            "weather_snowfall_cm",
            "weather_cloud_cover_pct",
            "weather_wind_speed_kmh",
            "weather_wind_direction_deg",
            "weather_shortwave_rad_wm2",
            "weather_direct_rad_wm2",
            "weather_wmo_code",
            "weather_wmo_description",
        ]
            
        # Only select columns that exist in both the data and the allowed columns
        available_columns = [col for col in desired_columns if col in dt.columns]
        dt = dt.select(available_columns)
        
        return dt

    def validate_sensors(
        self,
        sensors: polars.DataFrame,
        historical: polars.DataFrame = polars.DataFrame(),
        utc: int = None,
        step: str = "",
        return_results: bool = False,
    ) -> list:
        """
        Validate Sensor reading data. Delegates to diagnostics.validate_sensors().
        
        Failed validations are printed as errors to the log files, and surfaced as warnings.

        Parameters
        ----------
        sensors: polars.DataFrame
            Sensor readings DataFrame to validate.
        historical: polars.DataFrame
            Historical data for comparison.
        utc: int
            Current UTC timestamp for freshness checks.
        step: str
            Name of the step for logging.
        return_results: bool
            If True, return validation results list instead of raising exceptions.
            
        Returns
        -------
        list : List of validation result dicts with keys: test_name, run_utc, result, details
        """
        # Call the standalone validation function in diagnostics module
        validation_results = validation.validate_sensors(
            sensors=sensors,
            historical=historical,
            utc=utc,
            acceptable_range=self.acceptable_range,
            logger=self.logger,
            step=step,
        )
        
        # If return_results is True, return validation results without raising exceptions
        if return_results:
            return validation_results

        # Collect errors from FAIL results
        errs = []
        for result in validation_results:
            if result["result"] == "FAIL":
                errs.append(f"{result['test_name']}: {result['details']}")

        # Log the errors and raise exception to stop processing with bad data.
        if len(errs) > 0:
            self.error(
                step + " validation errors : " + "; ".join(errs) + "\n",
                raise_exception=True,
            )
        
        return validation_results

    def validate_devices(self, devices: polars.DataFrame):
        """
        Validate Device data. Delegates to validation.validate_devices().
        """
        validation.validate_devices(
            devices=devices,
            acceptable_range=self.acceptable_range,
            logger=self.logger,
            error_callback=self.error
        )

    def which(x):
        return list(numpy.where(x)[0])

    def send_alerts(self, sensors: polars.DataFrame, utc: int):
        """
        Send alerts based on the sensor data.
        Currently, this function saves alerts to "alerts.txt" in the data folder.
        Later, this can be connected to an messaging system like Twilio.

        Parameters
        ----------
        sensors: polars.DataFrame
            Sensor readings DataFrame.

        utc: int
            Current UTC timestamp. Passed from another function to keep alerts aligned with QueryUTC.
        """

        alerts = []
        for reading in self.acceptable_range:

            if len(self.acceptable_range[reading]) == 0:
                continue

            reading_idx = self.which(sensors.columns == reading)

            for i, row in sensors.iter_rows():
                if (not not row[reading_idx].is_null()) and (
                    row[reading_idx] < self.acceptable_range[reading][0]
                    or row[reading] > self.acceptable_range[reading][1]
                ):
                    alerts.append(row)

        # Write the alerts to a file.
        # Later, this can be connected to an alerting system like Twilio.
        if len(alerts) > 0:
            with open(f"{self.data_path}/alerts.txt", "w") as f:
                f.write("\n".join(alerts))

    def update_lookups(self):
        """
        Build (or re-build) the lookup tables. Delegates to consolidation.update_lookups().
        """
        consolidation.update_lookups(
            data_path=self.data_path,
            acceptable_range=self.acceptable_range,
            logger=self.logger,
            error_callback=self.error
        )

    def update_cubes(self):
        """
        Build (or re-build) the cubed tables. Delegates to consolidation.update_cubes().
        """
        consolidation.update_cubes(
            data_path=self.data_path,
            acceptable_range=self.acceptable_range,
            logger=self.logger
        )

    def export_to_excel(self):
        """
        Export consolidated data to Excel using the template at templates/consolidated-data-sample-template.xlsx.
        Fills in sheets: sensors, devices, device_readings_daily, and device_readings_last1000.
        Saves the result to data/consolidated-data-sample.xlsx.
        """
        consolidation.export_to_excel(
            data_path=self.data_path,
            home_directory=self.home_directory,
            logger=self.logger
        )
