import os
import requests
import polars
import numpy
import tqdm
import logging
import datetime
import warnings
from modules.conserv_client import create_conserv_client_from_env
from modules.coris_client import create_coris_client_from_env
from modules.hobolink_client import create_hobolink_client_from_env


class EnvironmentData:

    def __init__(
        self,
        CatsUserID: int = None,  # Deprecated - now read from environment
        data_path: str = "./data/",
        days_back: int = int(365 * 2),
        out_of_scope: list = [],
        testing: bool = False,
        coris_enabled: bool = False,
        conserv_enabled: bool = False,
        hobolink_enabled: bool = False,
    ):
        """
        Initialize resources for managing the environmental readings.

        Parameters
        ----------
        CatsUserID : int, optional (deprecated)
            DEPRECATED: Cats User ID is now read from CATS_USER_ID environment variable.
            This parameter is maintained for backward compatibility but is ignored.

        data_path : str, default='./data/'
            Path to store the parquet files which make up the database.

        days_back : int, default=int(365 * 2)
            Number of days of historical data to pull when initializing the database.

        out_of_scope : list[str], default=[]
            List of strings indicating sensors that are out of scope and should be ignored.
            If a SensorName starts with any of the strings in the list, that Sensor will be ignored.

        testing : bool, default=False
            Create a class in "testing mode". Only a few sensors will be included so that tests can run quickly and use fewer API calls.

        coris_enabled : bool, default=True
            Enable Coris API integration for primary sensor data source.

        conserv_enabled : bool, default=False
            Enable Conserv API integration for additional sensor data sources.

        hobolink_enabled : bool, default=False
            Enable Hobolink API integration for additional sensor data sources.

        Returns
        -------
        EnvironmentData: EnvironmentData object.
        """

        # Save inputs to the class instance.
        self.CatsUserID = CatsUserID  # Kept for backward compatibility
        self.data_path = data_path
        self.testing = testing
        self.testing_sensor_ids = []
        self.out_of_scope = out_of_scope
        self.coris_enabled = coris_enabled
        self.conserv_enabled = conserv_enabled
        self.hobolink_enabled = hobolink_enabled

        # Check that at least one data source is enabled
        if not (coris_enabled or conserv_enabled or hobolink_enabled):
            raise ValueError(
                "At least one data source must be enabled. "
                "Set coris_enabled=True, conserv_enabled=True, or hobolink_enabled=True"
            )

        # Create the data folder.
        if not os.path.exists(data_path):
            os.makedirs(data_path)

        # Set up logging to allow status to be viewed when run as a cron job.
        # https://docs.python.org/3/howto/logging-cookbook.html#logging-cookbook
        self.logger = logging.getLogger("EnvironmentData")
        self.logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        fh = logging.FileHandler(
            f"{data_path}/EnvironmentData.log"
        )  # log to EnvironmentData.log in the data folder.
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)

        # ch = logging.StreamHandler() # log to console. disabling to prevent breaking tdqm
        # ch.setFormatter(formatter)
        # self.logger.addHandler(ch)

        # Set up a second logger for errors only.
        self.logger_err = logging.getLogger("EnvironmentData-Errors")
        self.logger_err.setLevel(logging.ERROR)
        fh = logging.FileHandler(f"{data_path}/EnvironmentData-Errors.log")
        fh.setFormatter(formatter)
        self.logger_err.addHandler(fh)

        # Read cron status, or initialize the status file.
        if os.path.exists(f"{data_path}/cron_status.txt"):
            with open(f"{data_path}/cron_status.txt") as f:
                self.cron_status = f.read()
        else:
            self.update_cron_status("not-initialized")

        # Initialize Coris API client if enabled
        self.coris_client = None
        if self.coris_enabled:
            try:
                self.coris_client = create_coris_client_from_env(self.logger)
                # self.logger.info("Coris API client initialized successfully")
            except Exception as e:
                self.logger.warning(f"Failed to initialize Coris client: {e}")
                self.coris_enabled = False

        # Initialize Conserv API client if enabled
        self.conserv_client = None
        if self.conserv_enabled:
            try:
                self.conserv_client = create_conserv_client_from_env(self.logger)
                # self.logger.info(
                #     f"Conserv API client initialized with {len(self.conserv_client.customers)} customers"
                # )
            except Exception as e:
                self.logger.warning(f"Failed to initialize Conserv client: {e}")
                self.conserv_enabled = False

        # Initialize Hobolink API client if enabled
        self.hobolink_client = None
        if self.hobolink_enabled:
            try:
                self.hobolink_client = create_hobolink_client_from_env(self.logger)
                # self.logger.info("Hobolink API client initialized successfully")
            except Exception as e:
                self.logger.warning(f"Failed to initialize Hobolink client: {e}")
                self.hobolink_enabled = False

        # Set up the readings data structure that will be used throughout.
        self.acceptable_range = {"SensorReadingF": [], "SensorReadingRh": []}

        # Initialize the database by creating a parquet file for each reading type and populate it with historical data.
        self.initialize_database(days_back=days_back)

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

        # ============ CORIS HISTORICAL DATA PROCESSING ============
        # Get historical data from Coris API using the client
        readings = self.coris_client.get_historical_data_bulk(
            acceptable_range=self.acceptable_range,
            start_utc=start_utc,
            end_utc=current_utc,
            out_of_scope=self.out_of_scope,
            testing=self.testing,
            testing_sensor_ids=self.testing_sensor_ids
        )

        # Clean and validate the Coris data
        for i, data in enumerate(readings):
            readings[i] = self.clean_validate_sensors(
                sensors=data, step="initialize_database"
            )

        # ============ CONSERV HISTORICAL DATA PROCESSING ============
        conserv_readings = []
        if self.conserv_enabled and self.conserv_client:
            self.logger.info("Fetching Conserv historical data for all customers")

            try:
                # Get Conserv data for the same time period as Coris
                conserv_data = self.conserv_client.get_data_for_period(
                    start_utc=start_utc,
                    end_utc=current_utc,
                    max_concurrent_jobs=3 if self.testing else 5,
                )

                if conserv_data is not None and not conserv_data.is_empty():
                    # Conserv data is already transformed by modules/conserv_client.py
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
                # Continue with Coris data only - don't fail the entire initialization

        # ============ COMBINE ALL DATA SOURCES ============
        # Combine Coris and Conserv readings into a single polars DataFrame
        all_readings = readings + conserv_readings
        dt = (
            polars.concat(all_readings, how="diagonal")
            if all_readings
            else polars.DataFrame()
        )

        if dt.is_empty():
            self.logger.error(
                "No data from any source - initialization failed", raise_exception=True
            )

        # Rename columns to match desired schema
        column_renames = {}
        if "source" in dt.columns:
            column_renames["source"] = "Source"
        if "customer_id" in dt.columns:
            column_renames["customer_id"] = "ConservCustomerID"
        
        if column_renames:
            dt = dt.rename(column_renames)

        # Remove ConservCustomerID column if Conserv is not enabled
        if not self.conserv_enabled and "ConservCustomerID" in dt.columns:
            dt = dt.drop("ConservCustomerID")

        # Apply standard column ordering
        desired_columns = self.get_sensor_readings_column_order()
        dt = self.relocate(dt, desired_columns)

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

    def get_master_schema(self) -> dict:
        """
        Get the master schema from existing parquet file.
        This defines the target schema that ALL data must conform to.

        Returns
        -------
        dict : Column name -> polars data type mapping
        """

        # Define the master schema based on existing parquet structure
        master_schema = {
            "Source": polars.String,
            "SensorID": polars.String,
            "DeviceID": polars.String,
            "QueryUTC": polars.Int32,
            "SensorReadingUTC": polars.Int64,
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
        }

        # Add ConservCustomerID to schema only if Conserv is enabled
        if self.conserv_enabled:
            master_schema["ConservCustomerID"] = polars.Int32

        return master_schema

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

        if df.is_empty():
            return df

        master_schema = self.get_master_schema()

        # Track conversions for logging
        conversions_made = []

        # Apply schema enforcement
        conversion_exprs = []
        failed = False

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
                            # Int64 -> Int32 or vice versa (check for overflow)
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
                        self.logger.error(
                            f"Schema validation failed for {step_name}: Failed to convert {column}: {conv_error}"
                        )
                        failed = True
                        # Keep original column if conversion fails
                        continue

        # Apply all conversions at once
        if conversion_exprs:
            df = df.with_columns(conversion_exprs)

        if not failed:
            self.logger.info(f"validation passed for {step_name}: Schema enforcement completed")

        return df

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

        # ============ CORIS DATA PROCESSING ============
        coris_sensors = None
        if self.coris_enabled and self.coris_client:
            self.logger.info("Fetching current Coris data")
            
            # Get the current status from the Coris API using the client
            coris_sensors = self.coris_client.get_current_readings(
                out_of_scope=self.out_of_scope,
                testing=self.testing,
                testing_sensor_ids=self.testing_sensor_ids
            )
            
            # Convert data types to match expected schema before validation
            if not coris_sensors.is_empty():
                for reading in self.acceptable_range:
                    if reading in coris_sensors.columns:
                        coris_sensors = coris_sensors.with_columns(
                            polars.col(reading).cast(polars.Float32)
                        )
            
            self.validate_sensors(
                sensors=coris_sensors, utc=current_utc, step="get_current_readings_coris"
            )
        else:
            # Create empty DataFrame with required schema when Coris is disabled
            coris_sensors = polars.DataFrame()
            self.logger.info("Coris integration is disabled")

        # DEBUG: Log Coris schema types
        # self.logger.info(
        #     f"CORIS schema types: {dict(zip(coris_sensors.columns, [str(dtype) for dtype in coris_sensors.dtypes]))}"
        # )

        # ============ CONSERV DATA PROCESSING ============
        conserv_sensors = None
        if self.conserv_enabled and self.conserv_client:
            self.logger.info("Fetching current Conserv data for all customers")

            try:
                # Get window size from environment (default 24 hours)
                def read_env_variable(var_name):
                    try:
                        with open(".env") as f:
                            for line in f:
                                if line.startswith(var_name):
                                    return line.split("=", 1)[1].strip()
                    except FileNotFoundError:
                        pass
                    return None

                hours_back = int(read_env_variable("RUN_WINDOW_HOURS") or "24")
                start_utc = current_utc - (
                    hours_back * 3600
                )  # Convert hours to seconds

                # Get Conserv data for the specified window
                conserv_data = self.conserv_client.get_data_for_period(
                    start_utc=start_utc,
                    end_utc=current_utc,
                    max_concurrent_jobs=3 if self.testing else 5,
                )

                if conserv_data is not None and not conserv_data.is_empty():
                    self.logger.info(f"Conserv data shape: {conserv_data.shape}")

                    # Conserv data is already transformed by modules/conserv_client.py
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

        # ============ HOBOLINK DATA PROCESSING ============
        hobolink_sensors = None
        if self.hobolink_enabled and self.hobolink_client:
            self.logger.info("Fetching current Hobolink data")

            try:
                # Get current readings from Hobolink API
                hobolink_sensors = self.hobolink_client.get_current_readings(
                    out_of_scope=self.out_of_scope,
                    testing=self.testing,
                    testing_device_limit=3 if self.testing else None
                )

                if hobolink_sensors is not None and not hobolink_sensors.is_empty():
                    self.logger.info(f"Raw Hobolink data shape: {hobolink_sensors.shape}")

                    # Clean and validate Hobolink data
                    hobolink_sensors = self.clean_validate_sensors(
                        sensors=hobolink_sensors, step="get_current_readings_hobolink"
                    )

                    if hobolink_sensors is not None and not hobolink_sensors.is_empty():
                        self.logger.info(
                            f"Successfully processed {hobolink_sensors.shape[0]} current Hobolink records"
                        )
                    else:
                        self.logger.warning(
                            "Hobolink data was cleaned out completely during validation!"
                        )
                else:
                    self.logger.info("No current Hobolink data available")

            except Exception as e:
                self.logger.warning(f"Failed to fetch current Hobolink data: {e}")
                import traceback

                self.logger.warning(
                    f"Hobolink error traceback: {traceback.format_exc()}"
                )
                # Continue with other data sources - don't fail the entire process

        # ============ COMBINE ALL DATA SOURCES ============
        # Prepare list of valid data sources for merging
        data_sources = []
        source_info = []

        # Add Coris data if enabled and available
        if coris_sensors is not None and not coris_sensors.is_empty():
            coris_sensors = self.enforce_schema(coris_sensors, "Coris_Current_Readings")
            data_sources.append(coris_sensors)
            source_info.append(f"CORIS: {coris_sensors.shape[0]} rows")

        if conserv_sensors is not None and not conserv_sensors.is_empty():
            conserv_sensors = self.enforce_schema(
                conserv_sensors, "Conserv_Current_Readings"
            )
            data_sources.append(conserv_sensors)
            source_info.append(f"CONSERV: {conserv_sensors.shape[0]} rows")

        if hobolink_sensors is not None and not hobolink_sensors.is_empty():
            hobolink_sensors = self.enforce_schema(
                hobolink_sensors, "Hobolink_Current_Readings"
            )
            data_sources.append(hobolink_sensors)
            source_info.append(f"HOBOLINK: {hobolink_sensors.shape[0]} rows")

        # Log merging information and combine data sources
        if len(data_sources) > 1:
            # self.logger.info("MERGING DATA SOURCES:")
            for info in source_info:
                self.logger.info(f"  {info}")

            try:
                all_sensors = polars.concat(data_sources, how="diagonal")
                # total_rows = sum(df.shape[0] for df in data_sources)
                # self.logger.info(
                #     f"MERGE SUCCESS: Combined current readings from {len(data_sources)} sources = {all_sensors.shape[0]} total (expected: {total_rows})"
                # )
            except Exception as merge_error:
                self.logger.error(f"MERGE FAILED even with schema gate: {merge_error}")
                self.logger.error("Falling back to first available data source")
                all_sensors = data_sources[0] if data_sources else polars.DataFrame()
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

        dt = self.clean_validate_sensors(
            sensors=dt, historical=historical, step="consolidate_readings"
        )

        # Apply schema gate before concatenating with historical data
        dt = self.enforce_schema(dt, "CleanedNewReadings")
        if not historical.is_empty():
            historical = self.enforce_schema(historical, "HistoricalData")

        # Append these to the database.
        dt = polars.concat([historical, dt], how="diagonal")

        # Add time difference between readings for each sensor
        if not dt.is_empty():
            dt = dt.sort(["SensorID", "SensorReadingUTC"])
            dt = dt.with_columns(
                (
                    polars.col("SensorReadingUTC")
                    - polars.col("SensorReadingUTC").shift(1).over("SensorID")
                ).alias("SensorReadingUTC_SecondsFromPrior")
            )
        else:
            dt = dt.with_columns(
                polars.lit(None, dtype=polars.Int64).alias(
                    "SensorReadingUTC_SecondsFromPrior"
                )
            )

        # Rename columns to match desired schema
        column_renames = {}
        if "source" in dt.columns:
            column_renames["source"] = "Source"
        if "customer_id" in dt.columns:
            column_renames["customer_id"] = "ConservCustomerID"
        
        if column_renames:
            dt = dt.rename(column_renames)

        # Remove ConservCustomerID column if Conserv is not enabled
        if not self.conserv_enabled and "ConservCustomerID" in dt.columns:
            dt = dt.drop("ConservCustomerID")

        # Apply standard column ordering
        desired_columns = self.get_sensor_readings_column_order()
        # Include additional columns that exist but aren't in the standard order
        additional_columns = ["QueryUTC", "SensorReadingUTC_SecondsFromPrior"]
        dt = self.relocate(dt, desired_columns + additional_columns)

        # Write the file.
        dt.write_parquet(f"{self.data_path}/sensor_readings.parquet")
        # self.logger.info(f"{dt.shape[0]} total readings.")

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

        # Get the data for each of the selected sensors.
        devices = None
        data = data.filter(polars.col("DeviceID").is_null().not_())
        for reading in self.acceptable_range:
            idt = data.filter(polars.col(reading).is_null().not_()).select(
                ["DeviceID", "SensorReadingUTC", "QueryUTC", reading]
            )
            if isinstance(devices, polars.DataFrame):
                devices = devices.join(
                    idt,
                    how="full",
                    on=["DeviceID", "SensorReadingUTC", "QueryUTC"],
                )
            else:
                devices = idt
            del idt, reading

        # this will result in columns like DeviceID_right when there is not a perfect match.
        # coalesce to a single column.
        cols_DeviceID = [x for x in devices.columns if "DeviceID" in x]
        cols_SensorReadingUTC = [x for x in devices.columns if "SensorReadingUTC" in x]
        cols_QueryUTC = [x for x in devices.columns if "QueryUTC" in x]

        devices = devices.with_columns(
            polars.coalesce(cols_DeviceID).alias("DeviceID")
        )
        devices = devices.with_columns(
            polars.coalesce(cols_SensorReadingUTC).alias("SensorReadingUTC")
        )
        devices = devices.with_columns(polars.coalesce(cols_QueryUTC).alias("QueryUTC"))

        devices = devices.drop(
            [
                x
                for x in cols_DeviceID + cols_SensorReadingUTC
                if x not in ["DeviceID", "SensorReadingUTC", "QueryUTC"]
            ]
        )

        # If a device has multiple names, error out:
        device_names = (
            data.filter(polars.col("DeviceID").is_null().not_())
            .select(["DeviceID", "DeviceName"])
            .unique()
        )
        if device_names.shape[0] != device_names["DeviceName"].unique().shape[0]:
            self.error(
                "DeviceName to DeviceID is not a 1-1 mapping.",
                raise_exception=True,
            )

        # Attach the device name.
        devices = devices.join(device_names, how="left", on="DeviceID")

        # Rearrange columns.
        devices = devices.select(
            ["DeviceID", "DeviceName", "SensorReadingUTC", "QueryUTC"]
            + list(self.acceptable_range.keys())
        )

        # Return the data.
        return devices

    def clean_validate_sensors(
        self,
        sensors: polars.DataFrame,
        historical: polars.DataFrame = polars.DataFrame(),
        step: str = "",
    ) -> polars.DataFrame:
        """
        Clean the sensor readings data.
        Currently, this only sets efficient data types. It can be expanded to include more cleaning steps.
        Then, validate the data because these two operations typically occur together.

        Parameters
        ----------
        sensors : polars.DataFrame
            Sensors API response.

        Returns
        -------
        polars.DataFrame : Cleaned DataFrame.
        """

        # Set data types - MATCH EXISTING SCHEMA EXACTLY
        dtypes = {
            "SensorID": polars.String,  # Consolidated sensor ID field with source prefixes
            "DeviceID": polars.String,  # Consolidated device ID field with source prefixes
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,  # Fixed: Match parquet file
            "Source": polars.String,  # Fixed: Use String not Utf8 (renamed from source)
        }
        
        # Add ConservCustomerID to dtypes only if Conserv is enabled
        if self.conserv_enabled:
            dtypes["ConservCustomerID"] = polars.Int32
        for dtype in dtypes:
            if dtype in sensors.columns:
                sensors = sensors.with_columns(polars.col(dtype).cast(dtypes[dtype]))

        for reading in self.acceptable_range:
            if reading in sensors.columns:
                sensors = sensors.with_columns(
                    polars.col(reading).cast(polars.Float32)
                )  # Fixed: Use Float32 not String

        # Also ensure validation expected columns are correct type
        validation_types = {
            "SensorReadingUTC": polars.Int64,
            "SensorID": polars.String,
            "SensorReadingF": polars.Float32,
            "SensorReadingRh": polars.Float32,
        }
        for col, expected_type in validation_types.items():
            if col in sensors.columns and sensors[col].dtype != expected_type:
                sensors = sensors.with_columns(polars.col(col).cast(expected_type))
                self.logger.info(f"Type conversion: {col} -> {expected_type}")

        # Validate the data.
        self.validate_sensors(sensors=sensors, historical=historical, step=step)

        return sensors

    def get_sensor_readings_column_order(self) -> list:
        """
        Get the standard column order for sensor_readings.parquet.
        
        Returns
        -------
        list[str] : Ordered list of column names
        """
        # Base columns in the desired order
        columns = [
            "SensorReadingUTC",
            "Source",  # Renamed from 'source' 
        ]
        
        # Add ConservCustomerID only if Conserv is enabled  
        if self.conserv_enabled:
            columns.append("ConservCustomerID")  # Renamed from 'customer_id'
            
        columns.extend([
            "DeviceID", 
            "DeviceName",
            "SensorID", 
            "SensorName", 
            "SensorType",
            "SensorReadingF", 
            "SensorReadingRh"
        ])
        
        return columns

    def relocate(self, data: polars.DataFrame, columns: list) -> polars.DataFrame:
        """
        Helper function for relocating columns to the front of a DataFrame.

        Parameters
        ----------
        data : polars.DataFrame
            DataFrame to relocate columns in.

        columns : list[str]
            Columns to move to the front.

        Returns
        -------
        polars.DataFrame : DataFrame with columns relocated.
        """

        columns = [x for x in columns if x in data.columns]
        data = data[columns + [x for x in data.columns if x not in columns]]
        return data

    def validate_sensors(
        self,
        sensors: polars.DataFrame,
        historical: polars.DataFrame = polars.DataFrame(),
        utc: int = None,
        step: str = "",
    ):
        """
        Validate Sensor reading data: column data types, missing values, SensorReadingUTC close to QueryUTC,
            no duplicated SensorReadingUTC, one SensorName per SensorID,
            SensorReadingUTC_SecondsFromPrior less than 15 minutes,
            all SensorID in historical data, no multiple names for SensorID.
        Can be expanded to add more validation steps.
        Failed validations are printed as errors to the log files, and surfaced as warnings.

        Parameters
        ----------
        sensors: polars.DataFrame
            Sensor readings DataFrame to validate.
        """

        errs = []

        # We expect missing values in the data, so we don't check for them.

        # Is the data format as expected?
        expect_types = {
            "SensorReadingUTC": polars.Int64,
            "SensorID": polars.String,
        }
        for reading in self.acceptable_range:
            if reading in sensors.columns:
                expect_types[reading] = (
                    polars.Float32
                )  # Fixed: Should be Float32 like existing data

        type_errors_found = False
        for col in expect_types:
            if col in sensors.columns:

                # data type.
                if sensors[col].dtype != expect_types[col]:
                    if not type_errors_found:
                        self.logger.info(f"{step} validation: correct column data types.")
                        type_errors_found = True
                    errs.append(
                        f"Unexpected data type for [{col}]. Expected [{expect_types[col]}] got [{sensors[col]}]."
                    )

        # Readings should have at least one non-null value from expect_types.
        allnull = sensors[[x for x in expect_types if x in sensors.columns]].filter(
            polars.all_horizontal(polars.all().is_null())
        )
        missing_count = allnull.shape[0]
        if missing_count > 0:
            self.logger.info(f"{step} validation: at least one non-null value in readings.")
            errs.append(f"{missing_count} missing values in [{col}].")

        # Are the SensorReadingUTC close to the QueryUTC (the time the data was requested via API)?
        if utc is not None:
            maxdiff_minutes = (
                numpy.max(numpy.abs(sensors["SensorReadingUTC"].to_numpy() - utc)) / 60
            )
            if maxdiff_minutes > 5: # readings should be happening every 2 minutes.
                errs.append(
                    f"SensorReadingUTC is {maxdiff_minutes:,.0f} minutes old"
                )
            else:                
                self.logger.info(
                    f"{step} validation passed: SensorReadingUTC columns close to QueryUTC."
                )

        # Are there any duplicated SensorReadingUTC?
        if "SensorReadingUTC" in sensors.columns:
            dup_count = (
                sensors[["SensorID", "SensorReadingUTC"]].is_duplicated().sum()
            )
            if dup_count > 0:
                errs.append(f"Count of duplicated SensorReadingUTC: {dup_count}.")
            else:                
                self.logger.info(
                    f"{step} validation passed: no duplicated SensorReadingUTC per SensorID."
                )

        # Do any sensors have multiple names (indicating a change in name)?
        name_dups = sensors[["SensorID", "SensorName"]].unique()
        name_dups = name_dups.filter(name_dups["SensorID"].is_duplicated())
        dup_count = name_dups[["SensorID"]].unique().shape[0]
        if dup_count > 0:
            errs.append(f"Count of multiple names for SensorID: {dup_count}.")
        else:            
            self.logger.info(f"{step} validation passed: one SensorName per SensorID.")

        # Time between readings should be less than ten minutes.
        if "SensorReadingUTC_SecondsFromPrior" in sensors.columns:
            badrows = sensors.filter(
                sensors["SensorReadingUTC_SecondsFromPrior"] > 60 * 15
            )
            if badrows.shape[0] > 0:
                errs.append(
                    f"Count of SensorReadingUTC_SecondsFromPrior > 15 minutes: {badrows.shape[0]}."
                )
            else:                
                self.logger.info(
                    f"{step} validation passed: SensorReadingUTC_SecondsFromPrior less than 15 minutes."
                )

        # Comparisons to historical.
        if historical.shape[0] > 0:

            # Did we lose any sensors?
            missing = historical.filter(
                historical["SensorID"].is_in(sensors["SensorID"]).not_()
            )
            if missing.shape[0] > 0:
                errs.append(
                    f"Count of sensors missing from historical data: {missing.shape[0]}."
                )
            else:
                self.logger.info(
                    f"{step} validation passed: all SensorID in historical data (no dropped SensorID)."
                )

        # Log the errors.
        if len(errs) > 0:
            self.error(
                step + " validation errors : " + "; ".join(errs) + "\n",
                raise_exception=False,
            )

    def validate_devices(self, devices: polars.DataFrame):
        """
        Validate Device data: column data types, missing values.
        Can be expanded to add more validation steps.

        Parameters
        ----------
        devices: polars.DataFrame
            Device readings DataFrame to validate.
        """

        errs = []

        # Check for missing values.
        for col in self.acceptable_range:
            missing_count = devices[col].is_null().sum()
            if missing_count > 0:
                errs.append(f"{missing_count} missing values in [{col}].")

        # Log the errors.
        if len(errs) > 0:
            self.error(
                "Validation errors: \n" + "\n\t".join(errs), raise_exception=False
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
        Build (or re-build) the lookup tables used for analytical queries using the consolidated parquet files: sensors, devices, and utcs.
        """

        # We need some manual fixes to reformat invalid names.
        name_overrides = {
            "980D Unnamed Temp Sensor": "Temp Unnamed Unnamed_980D",
            "980D Unnamed Humid Sensor": "RH Unnamed Unnamed_980D",
        }

        building_name_map = {
            "ESC": "Environmental Science Center",
            "YPM": "Yale Peabody Museum",
            "KGL": "Kline Geology Laboratory",
            "CSC": "Collection Studies Center (West Campus)",
        }

        # Sensor Info.
        columns_to_read = [
            "SensorName",
            "SensorID",
            "DeviceID",
            "SensorType",
            "Source",
        ]
        if self.conserv_enabled:
            columns_to_read.append("ConservCustomerID")
            
        sensors_data = polars.read_parquet(
            f"{self.data_path}/sensor_readings.parquet",
            columns=columns_to_read,
        )
        sensors_data = sensors_data.unique().to_dicts()
        sensors = []
        for sensor in sensors_data:

            sensorname = sensor["SensorName"]
            if sensorname in name_overrides:
                sensorname = name_overrides[sensorname]

            # Fix sensor names with problematic prefixes by removing everything up to and including the first space
            if "__" in sensorname and " " in sensorname:
                sensorname = sensorname[sensorname.index(" ") + 1:]

            # Fix sensor names with " - no comm" suffix
            if " - no comm" in sensorname:
                sensorname = sensorname.replace(" - no comm", "")

            if "floator" in sensorname.lower():
                info = sensorname.strip().split("_")
            else:
                info = sensorname.strip().split(" ")
                info = info[0:-1] + info[-1].split("_")
            
            # Clean up empty strings from consecutive separators (e.g., multiple underscores)
            info = [part for part in info if part.strip() != ""]

            # If the cardinal direction is included, there will be 4 pieces of info.
            if len(info) == 5:
                sensors.append(
                    {
                        "SensorName": sensorname,
                        #   'SensorType_fromName': info[0],
                        "DeviceSerialFromName": info[4],
                        "SensorType": sensor["SensorType"],
                        "SensorID": sensor["SensorID"],
                        "DeviceID": sensor["DeviceID"],
                        "BuildingID": info[1],
                        "Building": (
                            building_name_map[info[1]]
                            if info[1] in building_name_map
                            else "Unknown"
                        ),
                        "Room": info[2].replace("_", ""),
                        "CardinalDirection": info[3],
                    }
                )
                if info[1] not in building_name_map:
                    self.logger.warning(
                        f"Building ID {info[1]} not found in building_name_map for sensor {sensorname}"
                    )

            elif len(info) == 4:

                sensors.append(
                    {
                        "SensorName": sensorname,
                        #'SensorType_fromName': info[0],
                        "DeviceSerialFromName": info[3],
                        "SensorType": sensor["SensorType"],
                        "SensorID": sensor["SensorID"],
                        "DeviceID": sensor["DeviceID"],
                        "BuildingID": info[1],
                        "Building": (
                            building_name_map[info[1]]
                            if info[1] in building_name_map
                            else "Unknown"
                        ),
                        "Room": info[2].replace("_", ""),
                        "CardinalDirection": "Not Indicated",
                    }
                )
                if info[1] not in building_name_map:
                    self.logger.warning(
                        f"Building ID {info[1]} not found in building_name_map for sensor {sensorname}"
                    )

            # Floaters are len 3.
            elif len(info) == 3:
                sensors.append(
                    {
                        "SensorName": sensorname,
                        # 'SensorType_fromName': info[0],
                        "DeviceSerialFromName": info[2],
                        "SensorType": sensor["SensorType"],
                        "SensorID": sensor["SensorID"],
                        "DeviceID": sensor["DeviceID"],
                        "BuildingID": "FLOATER",
                        "Building": (
                            building_name_map[info[1]]
                            if info[1] in building_name_map
                            else "Unknown"
                        ),
                        "Room": "FLOATER",
                        "CardinalDirection": None,
                    }
                )
            else:
                # Handle malformed sensor names gracefully with NAs
                self.logger.warning(
                    f"Malformed SensorName format: {sensorname}. Info: {info}. "
                    f"Adding with NA values for parsed fields. "
                    f"Valid formats: "
                    f"'Temp ESC Room101 North_1234' (5 parts with cardinal direction), "
                    f"'RH YPM Gallery_2567' (4 parts without cardinal direction), "
                    f"'Temp_Floater ESC_3890' (3 parts for floaters)"
                )
                sensors.append(
                    {
                        "SensorName": sensorname,
                        "DeviceSerialFromName": None,  # NA for malformed names
                        "SensorType": sensor["SensorType"],
                        "SensorID": sensor["SensorID"], 
                        "DeviceID": sensor["DeviceID"],
                        "BuildingID": "MALFORMED",
                        "Building": "Unknown",
                        "Room": "Unknown", 
                        "CardinalDirection": None,
                    }
                )

        # Write the table to a file.
        sensors = polars.DataFrame(sensors).unique()
        sensors = sensors.sort(
            ["BuildingID", "Building", "Room", "DeviceSerialFromName", "SensorName"]
        )
        sensors = self.clean_validate_sensors(sensors=sensors, step="update_lookups")
        sensors = self.relocate(
            sensors,
            ["SensorID", "BuildingID", "Room", "CardinalDirection", "DeviceSerialFromName"],
        )
        sensors.write_parquet(f"{self.data_path}/sensors.parquet")

        # It will be helpful to have the Building, Room, and CardinalDirection appended to Devices.
        # Check for a valid mapping.
        device_info_from_sensors = sensors.select(
            [
                "DeviceID",
                "BuildingID",
                "Building",
                "Room",
                "CardinalDirection",
                "DeviceSerialFromName",
            ]
        ).unique()

        # Check for inconsistent DeviceID mappings (same DeviceID with different Building/Room/CardinalDirection)
        # Group by DeviceID and check if there are multiple unique combinations of Building/Room/CardinalDirection
        device_consistency_check = device_info_from_sensors.group_by("DeviceID").agg([
            polars.col("BuildingID").n_unique().alias("unique_buildings"),
            polars.col("Room").n_unique().alias("unique_rooms"), 
            polars.col("CardinalDirection").n_unique().alias("unique_directions")
        ])
        
        inconsistent_devices = device_consistency_check.filter(
            (polars.col("unique_buildings") > 1) |
            (polars.col("unique_rooms") > 1) |
            (polars.col("unique_directions") > 1)
        )

        if inconsistent_devices.shape[0] > 0:
            # Get the actual inconsistent mappings for logging
            bad_device_ids = inconsistent_devices.select("DeviceID").to_series().to_list()
            bad_values = device_info_from_sensors.filter(
                polars.col("DeviceID").is_in(bad_device_ids)
            ).sort("DeviceID")
            
            self.logger.warning(
                f"DeviceID has inconsistent Building/Room/CardinalDirection mappings. Using first occurrence: \n{bad_values}"
            )
            
            # Keep only the first occurrence of each DeviceID to maintain consistency
            device_info_from_sensors = device_info_from_sensors.group_by("DeviceID").first()
        else:
            # If all mappings are consistent, we can still have multiple rows per DeviceID (for different sensor types)
            # Just keep one representative row per DeviceID since Building/Room/CardinalDirection should be the same
            device_info_from_sensors = device_info_from_sensors.group_by("DeviceID").first()

        # Device Info.
        devices = polars.read_parquet(
            f"{self.data_path}/sensor_readings.parquet",
            columns=["DeviceID", "DeviceName"],
        ).unique()
        devices = devices.join(
            device_info_from_sensors, how="left", on="DeviceID"
        ).sort(["BuildingID", "Room", "DeviceName"])
        devices = self.relocate(
            devices,
            ["DeviceID", "BuildingID", "Room", "CardinalDirection", "DeviceName"],
        )
        devices.write_parquet(f"{self.data_path}/devices.parquet")

        # UTC info.
        # Start with the timestamps and datetime in UTC.
        utc_timestamps = (
            polars.read_parquet(
                f"{self.data_path}/sensor_readings.parquet", columns="SensorReadingUTC"
            )["SensorReadingUTC"]
            .unique()
            .to_list()
        )

        # Filter out None/null values that can't be converted to datetime
        utc_timestamps = [x for x in utc_timestamps if x is not None]

        utcs = polars.DataFrame(
            {
                "UTC": utc_timestamps,
                "datetime_utc": [
                    datetime.datetime.fromtimestamp(x) for x in utc_timestamps
                ],
            }
        )

        # Convert to EST and round to seconds.
        utcs = utcs.with_columns(
            polars.col("datetime_utc")
            .dt.convert_time_zone("America/New_York")
            .dt.round("1s")
            .alias("datetime_est")
        )

        # Extract all the date parts.
        utcs = utcs.with_columns(
            polars.col("datetime_est").dt.date().alias("date"),
            polars.col("datetime_est").dt.time().alias("time"),
            polars.col("datetime_est").dt.year().alias("year"),
            polars.col("datetime_est").dt.month().alias("month"),
            (polars.col("datetime_est").dt.strftime("%A")).alias("day_of_week"),
            polars.col("datetime_est")
            .dt.weekday()
            .alias("day_of_week_monday1_sunday7"),
            polars.col("datetime_est").dt.hour().alias("hour_24"),
            (polars.col("datetime_est").dt.hour() % 12).alias("hour_12"),
            polars.col("datetime_est").dt.strftime("%p").alias("am_pm"),
        )

        # Write the table to a file.
        utcs.write_parquet(f"{self.data_path}/utcs.parquet")

    def update_cubes(self):
        """
        Build (or re-build) the cubed tables using the consolidated parquet files.
        Cubes contain aggregated measures by day and device/sensor to facilitate faster queries on smaller files.
        In the future, cubes can be modified to include different aggregation levels and dimensions.
        """

        sumcols = list(self.acceptable_range.keys())
        utcs = polars.read_parquet(f"{self.data_path}/utcs.parquet")

        sensor_readings = polars.read_parquet(
            f"{self.data_path}/sensor_readings.parquet"
        )
        sensor_readings = sensor_readings.filter(
            (polars.col("SensorReadingUTC") - polars.col("QueryUTC")).abs() < 60 * 5
        )  # remove old readings coming in with new data.
        sensor_readings = sensor_readings.join(
            utcs[["UTC", "date"]],
            how="left",
            left_on="SensorReadingUTC",
            right_on="UTC",
        )

        sensor_readings_daily = (
            sensor_readings.group_by(["date", "SensorID"])
            .agg(
                [
                    polars.len().alias("row_count"),
                    polars.col(sumcols).sum().name.suffix("_sum"),
                    polars.col(sumcols).min().name.suffix("_min"),
                    polars.col(sumcols).max().name.suffix("_max"),
                ]
            )
            .sort(["date", "SensorID"])
        )

        sensor_readings_daily.write_parquet(
            f"{self.data_path}/sensor_readings_daily.parquet"
        )

        device_readings = polars.read_parquet(
            f"{self.data_path}/device_readings.parquet"
        )
        device_readings = device_readings.filter(
            (polars.col("SensorReadingUTC") - polars.col("QueryUTC")).abs() < 60 * 5
        )  # remove old readings coming in with new data.
        device_readings = device_readings.join(
            utcs[["UTC", "date"]],
            how="left",
            left_on="SensorReadingUTC",
            right_on="UTC",
        )

        device_readings_daily = (
            device_readings.group_by(["date", "DeviceID"])
            .agg(
                [
                    polars.len().alias("row_count"),
                    polars.col(sumcols).sum().name.suffix("_sum"),
                    polars.col(sumcols).min().name.suffix("_min"),
                    polars.col(sumcols).max().name.suffix("_max"),
                ]
            )
            .sort(["date", "DeviceID"])
        )

        device_readings_daily.write_parquet(
            f"{self.data_path}/device_readings_daily.parquet"
        )
