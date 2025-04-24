import os, requests, polars, numpy, tqdm, logging, datetime, warnings
import asyncio # Added for weather enrichment
import pytz # Added for weather enrichment
from pathlib import Path # Added for weather enrichment

# Added for weather enrichment:
# Assumes weather_api.py is in the same directory or accessible in python path
try:
    from weather_api import get_historical_weather, get_daily_hourly_weather
except ImportError:
    # Log error or raise exception if weather_api is critical
    logging.error("Failed to import functions from weather_api.py")
    # Optionally, define placeholder functions or raise an error:
    async def get_historical_weather(*args, **kwargs):
        logging.error("weather_api.py not found, weather enrichment unavailable.")
        return None
    async def get_daily_hourly_weather(*args, **kwargs):
        logging.error("weather_api.py not found, weather enrichment unavailable.")
        return None

class EnvironmentData():

    # --- Constants Added for Weather Enrichment ---
    # Coordinates for Yale Peabody Museum (approximate)
    MUSEUM_LATITUDE = 41.3157
    MUSEUM_LONGITUDE = -72.9211
    # Max time difference (seconds) to match weather data to sensor reading
    WEATHER_MATCH_TOLERANCE_SECONDS = 1800 # 30 minutes
    # Max concurrent weather API calls
    MAX_CONCURRENT_WEATHER_CALLS = 15
    # --------------------------------------------

    def __init__(
            self, 
            CatsUserID: int, 
            data_path: str = './data/', 
            days_back: int = int(365 * 2), 
            out_of_scope: list = [],
            testing: bool = False
        ):

        """
        Initialize resources for managing the environmental readings.

        Parameters
        ----------
        CatsUserID : int
            Cats User ID from Coris. Necessary for querying the Coris API.

        data_path : str, default='./data/'
            Path to store the parquet files which make up the database.
        
        days_back : int, default=int(365 * 2)
            Number of days of historical data to pull when initializing the database.

        out_of_scope : list[str], default=[]
            List of strings indicating sensors that are out of scope and should be ignored. 
            If a SensorName starts with any of the strings in the list, that Sensor will be ignored. 
        
        testing : bool, default=False
            Create a class in "testing mode". Only a few sensors will be included so that tests can run quickly and use fewer API calls.

        Returns
        -------
        EnvironmentData: EnvironmentData object.
        """
        
        # Save inputs to the class instance.
        self.CatsUserID = CatsUserID
        self.data_path = Path(data_path) # Use Path object
        self.testing = testing
        self.testing_sensor_ids = []
        self.out_of_scope = out_of_scope

        # Create the data folder.
        if not os.path.exists(data_path):
            os.makedirs(data_path)

        # Set up logging to allow status to be viewed when run as a cron job.
        # https://docs.python.org/3/howto/logging-cookbook.html#logging-cookbook
        self.logger = logging.getLogger('EnvironmentData')
        self.logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        fh = logging.FileHandler(f'{data_path}/EnvironmentData.log') # log to EnvironmentData.log in the data folder.
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)
        
        #ch = logging.StreamHandler() # log to console. disabling to prevent breaking tdqm
        #ch.setFormatter(formatter)
        #self.logger.addHandler(ch)

        # Set up a second logger for errors only. 
        self.logger_err = logging.getLogger('EnvironmentData-Errors')
        self.logger_err.setLevel(logging.ERROR)
        fh = logging.FileHandler(f'{data_path}/EnvironmentData-Errors.log')
        fh.setFormatter(formatter)
        self.logger_err.addHandler(fh)

        # Read cron status, or initialize the status file.
        if os.path.exists(f'{data_path}/cron_status.txt'):
            with open(f'{data_path}/cron_status.txt') as f:
                self.cron_status = f.read()
        else:
            self.update_cron_status('not-initialized')
        
        # Get the API key.
        # cron can't read environment variables so we need read the key in from the .env file.
        # See the README for a note about API key security.
        def read_env_variable(var_name):
            with open('.env') as f:
                for line in f:
                    if line.startswith(var_name):
                        return line.split('=', 1)[1].strip()

        self.apikeys = {'CORIS': read_env_variable('CORIS_API_KEY')} # Use a dictionary in case we need more keys in the future.

        # Set up the readings data structure that will be used throughout.
        self.acceptable_range = {'SensorReadingF': [], 'SensorReadingRh': []}

        # Initialize the database by creating a parquet file for each reading type and populate it with historical data.
        self.initialize_database(days_back = days_back)

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
            with open(f'{self.data_path}/cron_status.txt', 'w') as f:
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

    def error(self, msg, raise_exception = False):

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
        if self.cron_status != 'not-initialized':
            return

        # Make a log entry and gather current and starting UTC.
        self.logger.info('initialize_database')
        sensor_ids = []
        current_utc = self.get_current_utc()
        start_utc = current_utc - days_back * 24 * 60 * 60
        sensors = self.get_sensors()

        # Use the API to get historical data for each sensor type.
        readings = []
        for reading in self.acceptable_range:

            sensor_ids = sensors.filter(polars.col(reading).is_nan().not_())['SensorID_Coris'].unique().to_list()
            if self.testing:           
                sensor_ids = sensor_ids[0:3]

            # Query the API for each sensor and save the data as a polars DataFrame with optimal data types.
            pbar = tqdm.tqdm(total = len(sensor_ids), desc=f'Gather readings: {reading}')
            for sensor_id in sensor_ids:

                if self.testing:
                    self.testing_sensor_ids.append(sensor_id)

                # it is possible to pull everything by leaving out StartUTC and EndUTC. 
                # leaving it in for now though, in case we do want to limit it.
                url = '&'.join([
                    f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={self.apikeys["CORIS"]}',
                    f'SensorID={sensor_id}',
                    f'ReadingType={reading}',
                    f'StartUTC={start_utc}',
                    f'EndUTC={current_utc}',
                    f'MinReadingSpacing=600', # every 10 minutes.
                    f'RequestedOutputFormat=raw'
                ])

                # create a duplicate of the url for logging purposes, which doesn't include sensitive information.
                logurl = '&'.join([
                    f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey=XXXX',
                    f'SensorID={sensor_id}',
                    f'ReadingType={reading}',
                    f'StartUTC={start_utc}',
                    f'EndUTC={current_utc}',
                    f'MinReadingSpacing=600', # every 10 minutes.
                    f'RequestedOutputFormat=raw'
                ])
                self.logger.info(f'API call: {logurl}')
                # Add verify=False to handle potential SSL certificate verification issues
                response = requests.get(url, verify=False)

                # Check for errors.
                if not response.ok:
                    self.logger.error(f'Error getting historical {reading} for {sensor_id}: {response.json()}', raise_exception = True)
                
                # Data comes in as comma separated values with no header. Convert this to a polars DataFrame.
                data = polars.read_csv(response.content, has_header = False)
                data.columns = ['SensorReadingUTC', reading]

                # Add data from the sensors dataset. 
                data = data.with_columns(polars.lit(sensor_id).alias('SensorID_Coris'))
                sensor_data = sensors.filter(polars.col('SensorID_Coris') == sensor_id)
                for col in ['SensorName', 'DeviceName', 'DeviceID_Coris', 'SensorType']:
                    data = data.with_columns(polars.lit(sensor_data[col].to_list()[0]).alias(col))

                # Clean and validate the data. 
                data = self.clean_validate_sensors(sensors = data, step = 'initialize_database')
                
                # Append the data to the list of DataFrame for this sensor type.
                readings.append(data)

                pbar.update(1)

            pbar.close()

        # Combine the readings into a single polars DataFrame.
        dt = polars.concat(readings, how = 'diagonal')
        
        # Write the database file. 
        dt.write_parquet(f'{self.data_path}/sensor_readings.parquet')

        self.update_cron_status('initialized')

    def get_sensors(self) -> polars.DataFrame:

        """
        Get the list of all sensors, along with data sent by the Coris API.

        Returns
        -------
        polars.DataFrame : sensor data returned by the API. Contains SensorID_Coris, Device information, readings, and more.
        """

        # Build the URL and call the API.
        self.logger.info('get_sensors')
        url = f'https://cats.corismonitoring.com/api/cats/user/?ApiKey={self.apikeys["CORIS"]}&CatsUserID={self.CatsUserID}'
        self.logger.info(f'API call: https://cats.corismonitoring.com/api/cats/user/?ApiKey=XXXX&CatsUserID=XXXX') # log a duplicate of the url for logging purposes, which doesn't include sensitive information.
        current_utc = self.get_current_utc()
        # Add verify=False to handle potential SSL certificate verification issues
        response = requests.get(url, verify=False)
        
        # Check for errors and raise an exception if there is one.
        if not response.ok:
            self.error(f'Error getting sensors: {response.json()}', raise_exception = True)
        
        # Remove out-of-scope sensors.
        sensors = polars.DataFrame(response.json()['Sensors'])
        for i in self.out_of_scope:            
            sensors = sensors.filter(polars.col('SensorName').str.starts_with(i).not_())

        # If testing, only use the selected sensors.
        if self.testing and (len(self.testing_sensor_ids) > 0):
            sensors = sensors.filter(polars.col('SensorID').is_in(self.testing_sensor_ids))

        # There are multiple sensors, so rename the ID to indicate the data source.
        sensors = sensors.rename({'SensorID': 'SensorID_Coris', 'DeviceDevID': 'DeviceID_Coris'})

        # Attach the query UTC.
        sensors = sensors.with_columns(polars.lit(current_utc).alias('QueryUTC'))

        # Clean and validate the data.
        sensors = self.clean_validate_sensors(sensors = sensors, step = 'get_sensors')

        # Return the data. 
        return sensors
    
    def get_current_utc(self) -> int:
            
        """
        Get the current UTC timestamp in seconds.

        Returns
        -------
        int : Current UTC timestamp.
        """

        return int(datetime.datetime.now(datetime.timezone.utc).timestamp())

    def get_current_readings(self) -> dict:

        """
        Get current readings from the API. Validate the data. Send alerts.
        Save the readings to a file in the new-readings folder.
        This function will save data as separate files to facilitate easy tracking of new data vs consolidated data.
        A batch process will clean, validate, and consolidate readings later.
        """

        if self.cron_status == 'not-initialized':
            return
        
        # Make a log entry and gather the current UTC.
        current_utc = self.get_current_utc()
        self.logger.info(f'get_current_readings: {current_utc}')

        # Get the current status from the API.
        sensors = self.get_sensors()
        self.validate_sensors(sensors = sensors, utc = current_utc, step = 'get_current_readings')

        # Process alerts.
        self.send_alerts(sensors, current_utc)

        # Save the new-readings file. A daily process will pull these later to clean, validate, and consolidate them into the database.
        os.makedirs(f'{self.data_path}/new-readings/', exist_ok = True)
        sensors.write_parquet(f'{self.data_path}/new-readings/{current_utc}.parquet')


    def consolidate_readings(self):

        """
        Combine new and historical readings into one database. Build (or re-build) the analytical tables. 
        If successful, remove the new-readings files that have been processed into the database.
        Meant to run as a daily batch process to consolidate readings made throughout the day.
        """

        if self.cron_status == 'not-initialized':
            return

        # Make a log entry.
        self.logger.info('consolidate_readings')
        new_readings = []
               
        # Read new readings from the parquet files saved by calls to get_current_readings.
        # These are deleted after each consolidation, so these files will always be the un-consolidated files. 
        files = os.listdir(f'{self.data_path}/new-readings/')
        files_read = []
        for file in files:

            # use try catch since there might be partial files being written by get_current_readings.
            try:
                new_readings.append(polars.read_parquet(f'{self.data_path}/new-readings/{file}'))
                files_read.append(file)
            except:
                pass

        # Combine the readings into a single polars DataFrame.
        dt = polars.concat(new_readings)
        self.logger.info(f'{dt.shape[0]} new readings.')

        # Clean the data.
        historical = polars.read_parquet(f'{self.data_path}/sensor_readings.parquet')
        dt = self.clean_validate_sensors(sensors = dt, historical = historical, step = 'consolidate_readings')

        # Set the column data types to match the database.
        dt = self.match_types(dt, historical)

        # Append these to the database.
        dt = polars.concat([historical, dt], how = 'diagonal')

        # Add difference between readings. 
        dt = dt.sort(['SensorID_Coris', 'SensorReadingUTC'])
        SensorReadingUTC_SecondsFromPrior = dt.group_by('SensorID_Coris', maintain_order = True).map_groups(
            lambda x: x.with_columns((
                polars.col('SensorReadingUTC') - polars.col('SensorReadingUTC').shift(1)
            ).alias('SensorReadingUTC_SecondsFromPrior')
        ))['SensorReadingUTC_SecondsFromPrior']
        dt = dt.with_columns(SensorReadingUTC_SecondsFromPrior)

        # Move the most important columns to the front. 
        dt = self.relocate(dt, ['SensorID_Coris', 'QueryUTC', 'SensorReadingUTC', 'DeviceID_Coris', 'SensorReadingUTC_SecondsFromPrior'] + list(self.acceptable_range.keys()))

        # Write the file. 
        dt.write_parquet(f'{self.data_path}/sensor_readings.parquet')
        self.logger.info(f'{dt.shape[0]} total readings.')

        # If all this was successful, remove the new-readings files to prepare for the next consolidation.
        for file in files_read:
            os.remove(f'{self.data_path}/new-readings/{file}')

        # Refresh the devices table. 
        devices = self.build_devices(dt)        
        self.validate_devices(devices)
        devices.write_parquet(f'{self.data_path}/device_readings.parquet')

        # Update lookup tables and cubes. 
        self.update_lookups()
        self.update_cubes()

    def match_types(self, data: polars.DataFrame, match: polars.DataFrame) -> polars.DataFrame:
            
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
        data = data.filter(polars.col('DeviceID_Coris').is_null().not_())
        for reading in self.acceptable_range:
            idt = data.filter(polars.col(reading).is_null().not_()).select(['DeviceID_Coris', 'SensorReadingUTC', 'QueryUTC', reading])
            if isinstance(devices, polars.DataFrame):
                devices = devices.join(idt, how = 'full', on = ['DeviceID_Coris', 'SensorReadingUTC', 'QueryUTC'])
            else:
                devices = idt
            del idt, reading
        
        # this will result in columns like DeviceID_Coris_right when there is not a perfect match. 
        # coalesce to a single column.
        cols_DeviceID_Coris = [x for x in devices.columns if 'DeviceID_Coris' in x]
        cols_SensorReadingUTC = [x for x in devices.columns if 'SensorReadingUTC' in x]
        cols_QueryUTC = [x for x in devices.columns if 'QueryUTC' in x]

        devices = devices.with_columns(polars.coalesce(cols_DeviceID_Coris).alias('DeviceID_Coris'))
        devices = devices.with_columns(polars.coalesce(cols_SensorReadingUTC).alias('SensorReadingUTC'))
        devices = devices.with_columns(polars.coalesce(cols_QueryUTC).alias('QueryUTC'))
        
        devices = devices.drop([x for x in cols_DeviceID_Coris + cols_SensorReadingUTC if x not in ['DeviceID_Coris', 'SensorReadingUTC', 'QueryUTC']])
        
        # If a device has multiple names, error out:
        device_names = data.filter(polars.col('DeviceID_Coris').is_null().not_()).select(['DeviceID_Coris', 'DeviceName']).unique()
        if device_names.shape[0] != device_names['DeviceName'].unique().shape[0]:
            self.error('DeviceName to DeviceID_Coris is not a 1-1 mapping.', raise_exception = True)
        
        # Attach the device name.
        devices = devices.join(device_names, how = 'left', on = 'DeviceID_Coris')

        # Rearrange columns. 
        devices = devices.select(['DeviceID_Coris', 'DeviceName', 'SensorReadingUTC', 'QueryUTC'] + list(self.acceptable_range.keys()))

        # Return the data.
        return devices
    
    def clean_validate_sensors(self, sensors: polars.DataFrame, historical: polars.DataFrame = polars.DataFrame(), step: str = '') -> polars.DataFrame:

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

        # Set data types.
        dtypes = {
            'SensorID': polars.String, # this is extracted from the SensorName, it isn't always a number.
            'SensorID_Coris': polars.Int32,
            'DeviceID_Coris': polars.Int32,
            'SensorReadingUTC': polars.Int64
        }
        for dtype in dtypes:
            if dtype in sensors.columns:
                sensors = sensors.with_columns(polars.col(dtype).cast(dtypes[dtype]))
        
        for reading in self.acceptable_range:
            if reading in sensors.columns:
                sensors = sensors.with_columns(polars.col(reading).cast(polars.Float32))

        # Validate the data.
        self.validate_sensors(sensors= sensors, historical = historical, step = step)

        return sensors

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

    def validate_sensors(self, sensors: polars.DataFrame, historical: polars.DataFrame = polars.DataFrame(), utc: int = None, step: str = ''):

        """
        Validate Sensor reading data: column data types, missing values, SensorReadingUTC close to QueryUTC, 
            no duplicated SensorReadingUTC, one SensorName per SensorID_Coris, 
            SensorReadingUTC_SecondsFromPrior less than 15 minutes, 
            all SensorID_Coris in historical data, no multiple names for SensorID_Coris.
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
        self.logger.info(f'{step} validation: correct column data types.')
        expect_types = {"SensorReadingUTC": polars.Int64, "SensorID": polars.Int32}
        for reading in self.acceptable_range:
            if reading in sensors.columns:
                expect_types[reading] = polars.Float32
        
        for col in expect_types:
            if col in sensors.columns:

                # data type.
                if sensors[col].dtype != expect_types[col]:
                    errs.append(f'Unexpected data type for [{col}]. Expected [{expect_types[col]}] got [{sensors[col]}].')

        # Readings should have at least one non-null value from expect_types. 
        self.logger.info(f'{step} validation: at least one non-null value in readings.')
        allnull = sensors[[x for x in expect_types if x in sensors.columns]].filter(polars.all_horizontal(polars.all().is_null()))
        missing_count = allnull.shape[0]
        if missing_count > 0:
            errs.append(f'{missing_count} missing values in [{col}].')

        # Are the SensorReadingUTC close to the QueryUTC (the time the data was requested via API)?
        if utc is not None:
            self.logger.info(f'{step} validation: SensorReadingUTC columns close to QueryUTC.')
            maxdiff_minutes = numpy.max(numpy.abs(sensors['SensorReadingUTC'].to_numpy() - utc)) / 60
            if maxdiff_minutes > 2: # readings should be happening every 2 minutes. 
                errs.append(f'SensorReadingUTC differs from UTC: UTC: {utc}, maximum absolute difference (minutes): {maxdiff_minutes:,.0}.')

        # Are there any duplicated SensorReadingUTC?
        if 'SensorReadingUTC' in sensors.columns:
            self.logger.info(f'{step} validation: no duplicated SensorReadingUTC per SensorID_Coris.')
            dup_count = sensors[['SensorID_Coris', 'SensorReadingUTC']].is_duplicated().sum()
            if dup_count > 0:
                errs.append(f'Count of duplicated SensorReadingUTC: {dup_count}.')

        # Do any sensors have multiple names (indicating a change in name)?
        self.logger.info(f'{step} validation: one SensorName per SensorID_Coris.')
        name_dups = sensors[['SensorID_Coris', 'SensorName']].unique()
        name_dups = name_dups.filter(name_dups['SensorID_Coris'].is_duplicated())
        dup_count = name_dups[['SensorID_Coris']].unique().shape[0]
        if dup_count > 0:
            errs.append(f'Count of multiple names for SensorID_Coris: {dup_count}.')

        # Time between readings should be less than ten minutes.
        if 'SensorReadingUTC_SecondsFromPrior' in sensors.columns:
            self.logger.info(f'{step} validation: SensorReadingUTC_SecondsFromPrior less than 15 minutes.')
            badrows = sensors.filter(sensors['SensorReadingUTC_SecondsFromPrior'] > 60 * 15)
            if badrows.shape[0] > 0:
                errs.append(f'Count of SensorReadingUTC_SecondsFromPrior > 15 minutes: {badrows.shape[0]}.')

        # Comparisons to historical.
        if historical.shape[0] > 0:

            # Did we lose any sensors?
            self.logger.info(f'{step} validation: all SensorID_Coris in historical data (no dropped SensorID).')
            missing = historical.filter(historical['SensorID_Coris'].is_in(sensors['SensorID_Coris']).not_())
            if missing.shape[0] > 0:
                errs.append(f'Count of sensors missing from historical data: {missing.shape[0]}.')

        # Log the errors.
        if len(errs) > 0:
            self.error(step + ' validation errors : ' + "; ".join(errs) + '\n', raise_exception = False)

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
                errs.append(f'{missing_count} missing values in [{col}].')

        # Log the errors.
        if len(errs) > 0:
            self.error('Validation errors: \n' + "\n\t".join(errs), raise_exception = False)


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
                if (not not row[reading_idx].is_null()) and (row[reading_idx] < self.acceptable_range[reading][0] or row[reading] > self.acceptable_range[reading][1]):
                        alerts.append(row)

        # Write the alerts to a file.
        # Later, this can be connected to an alerting system like Twilio.
        if len(alerts) > 0:
            with open(f'{self.data_path}/alerts.txt', 'w') as f:
                f.write('\n'.join(alerts))
    
    def update_lookups(self):

        """
        Build (or re-build) the lookup tables used for analytical queries using the consolidated parquet files: sensors, devices, and utcs.
        """

        # We need some manual fixes to reformat invalid names. 
        name_overrides = {
            '980D Unnamed Temp Sensor': 'Temp Unnamed Unnamed_980D',
            '980D Unnamed Humid Sensor': 'RH Unnamed Unnamed_980D',
        }

        building_name_map = {
            'ESC': 'Environmental Science Center',
            'YPM': 'Yale Peabody Museum',
            'KGL': 'Kline Geology Laboratory',
            'CSC': 'Collection Studies Center (West Campus)',
        }

        # Sensor Info.
        sensors_data = polars.read_parquet(f'{self.data_path}/sensor_readings.parquet', columns = [
            'SensorName', 'SensorID_Coris', 'DeviceID_Coris', 'SensorType'
        ])
        sensors_data  = sensors_data.unique().to_dicts()
        sensors = []
        for sensor in sensors_data:
            
            sensorname = sensor['SensorName']
            if sensorname in name_overrides:
                sensorname = name_overrides[sensorname]
            
            if 'floator' in sensorname.lower():
                info = sensorname.strip().split('_')
            else:
                info = sensorname.strip().split(' ')
                info = info[0:-1] + info[-1].split('_') 
            
            # If the cardinal direction is included, there will be 4 pieces of info.
            if len(info) == 5:
                
                sensors.append({
                    'SensorName': sensorname, 
                    #'SensorType_fromName': info[0],
                    'DeviceID': info[4],
                    'SensorType': sensor['SensorType'],
                    'SensorID_Coris': sensor['SensorID_Coris'],
                    'DeviceID_Coris': sensor['DeviceID_Coris'],
                    'BuildingID': info[1],
                    'Building': building_name_map[info[1]] if info[1] in building_name_map else '',
                    'Room': info[2].replace('_', ''),
                    'CardinalDirection': info[3]
                })
                
            elif len(info) == 4:
                
                sensors.append({
                    'SensorName': sensorname, 
                    #'SensorType_fromName': info[0],
                    'DeviceID': info[3],
                    'SensorType': sensor['SensorType'],
                    'SensorID_Coris': sensor['SensorID_Coris'],
                    'DeviceID_Coris': sensor['DeviceID_Coris'],
                    'BuildingID': info[1],
                    'Building': building_name_map[info[1]] if info[1] in building_name_map else '',
                    'Room': info[2].replace('_', ''),
                    'CardinalDirection': 'Not Indicated'
                })
                
            # Floaters are len 3.
            elif len(info) == 3:
                
                sensors.append({
                    'SensorName': sensorname, 
                    #'SensorType_fromName': info[0],
                    'DeviceID': info[2],
                    'SensorType': sensor['SensorType'],
                    'SensorID_Coris': sensor['SensorID_Coris'],
                    'DeviceID_Coris': sensor['DeviceID_Coris'],
                    'BuildingID': 'FLOATER',
                    'Building': building_name_map[info[1]] if info[1] in building_name_map else '',
                    'Room': 'FLOATER',
                    'CardinalDirection': None
                })
            
            else:
                
                raise Exception(f'Unexpected SensorName format: {sensorname}.')
                
        # Write the table to a file.
        sensors = polars.DataFrame(sensors).unique()
        sensors = sensors.sort(['BuildingID', 'Building', 'Room', 'DeviceID', 'SensorName'])
        sensors = self.clean_validate_sensors(sensors = sensors, step = 'update_lookups')
        sensors = self.relocate(sensors, ['SensorID_Coris', 'BuildingID', 'Room', 'CardinalDirection', 'DeviceID'])
        sensors.write_parquet(f'{self.data_path}/sensors.parquet')

        # It will be helpful to have the Building, Room, and CardinalDirection appended to Devices. 
        # Check for a valid mapping. 
        device_info_from_sensors = sensors.select(['DeviceID_Coris', 'BuildingID', 'Building', 'Room', 'CardinalDirection', 'DeviceID']).unique()
        bad_values = device_info_from_sensors.filter(device_info_from_sensors.select("DeviceID_Coris").is_duplicated()).sort('DeviceID_Coris')
        if bad_values.shape[0] > 0:
            # If there is a bad mapping, log it and remove the duplicates so we can use the data that is properly mapped.
            # For invalid mappings, Building, Room, and CardinalDirection will be null.
            self.error(f'DeviceID_Coris to Building, Room, CardinalDirection is not a 1-1 mapping. Invalid mappings will be excluded: \n{bad_values}', raise_exception = True)
            device_info_from_sensors = device_info_from_sensors.filter(polars.col('DeviceID_Coris').is_duplicated().not_())

        # Device Info.
        devices = polars.read_parquet(f'{self.data_path}/sensor_readings.parquet', columns = ['DeviceID_Coris', 'DeviceName']).unique()
        devices = devices.join(device_info_from_sensors, how = 'left', on = 'DeviceID_Coris').sort(['BuildingID', 'Room', 'DeviceName'])
        devices = self.relocate(devices, ['DeviceID', 'BuildingID', 'Room', 'CardinalDirection', 'DeviceName'])
        devices.write_parquet(f'{self.data_path}/devices.parquet')

        # UTC info.
        # Start with the timestamps and datetime in UTC.
        utc_timestamps = polars.read_parquet(f'{self.data_path}/sensor_readings.parquet', columns = 'SensorReadingUTC')['SensorReadingUTC'].unique().to_list()
        utcs = polars.DataFrame({
            "UTC": utc_timestamps,
            "datetime_utc": [datetime.datetime.fromtimestamp(x) for x in utc_timestamps]
        })

        # Convert to EST and round to seconds. 
        utcs = utcs.with_columns(
            polars.col("datetime_utc").dt.convert_time_zone("America/New_York").dt.round("1s").alias('datetime_est')
        )

        # Extract all the date parts. 
        utcs = utcs.with_columns(
            polars.col("datetime_est").dt.date().alias("date"),
            polars.col("datetime_est").dt.time().alias("time"),
            polars.col("datetime_est").dt.year().alias("year"),
            polars.col("datetime_est").dt.month().alias("month"),
            (polars.col("datetime_est").dt.strftime("%A")).alias("day_of_week"),
            polars.col("datetime_est").dt.weekday().alias("day_of_week_monday1_sunday7"),
            polars.col("datetime_est").dt.hour().alias("hour_24"),
            (polars.col("datetime_est").dt.hour() % 12).alias("hour_12"),
            polars.col("datetime_est").dt.strftime("%p").alias("am_pm"),
        )

        # Write the table to a file.
        utcs.write_parquet(f'{self.data_path}/utcs.parquet')
    
    def update_cubes(self):

        """
        Build (or re-build) the cubed tables using the consolidated parquet files.
        Cubes contain aggregated measures by day and device/sensor to facilitate faster queries on smaller files. 
        In the future, cubes can be modified to include different aggregation levels and dimensions.
        """

        sumcols = list(self.acceptable_range.keys())
        utcs = polars.read_parquet(f'{self.data_path}/utcs.parquet')

        sensor_readings = polars.read_parquet(f'{self.data_path}/sensor_readings.parquet')
        sensor_readings = sensor_readings.filter((polars.col('SensorReadingUTC') - polars.col('QueryUTC')).abs() < 60 * 5) # remove old readings coming in with new data.
        sensor_readings = sensor_readings.join(utcs[['UTC', 'date']], how = 'left', left_on = 'SensorReadingUTC', right_on = 'UTC')

        sensor_readings_daily = sensor_readings.group_by(['date', 'SensorID_Coris']).agg([
            polars.len().alias("row_count"),
            polars.col(sumcols).sum().name.suffix("_sum"),
            polars.col(sumcols).min().name.suffix("_min"),
            polars.col(sumcols).max().name.suffix("_max"),
        ]).sort(['date', 'SensorID_Coris'])

        sensor_readings_daily.write_parquet(f'{self.data_path}/sensor_readings_daily.parquet')

        device_readings = polars.read_parquet(f'{self.data_path}/device_readings.parquet')
        device_readings = device_readings.filter((polars.col('SensorReadingUTC') - polars.col('QueryUTC')).abs() < 60 * 5) # remove old readings coming in with new data.
        device_readings = device_readings.join(utcs[['UTC', 'date']], how = 'left', left_on = 'SensorReadingUTC', right_on = 'UTC')

        device_readings_daily = device_readings.group_by(['date', 'DeviceID_Coris']).agg([
            polars.len().alias("row_count"),
            polars.col(sumcols).sum().name.suffix("_sum"),
            polars.col(sumcols).min().name.suffix("_min"),
            polars.col(sumcols).max().name.suffix("_max"),
        ]).sort(['date', 'DeviceID_Coris'])

        device_readings_daily.write_parquet(f'{self.data_path}/device_readings_daily.parquet')
            
    # <<< NEW METHOD FOR WEATHER ENRICHMENT >>>
    async def enrich_readings_with_weather(self):
        """Enriches sensor readings incrementally with weather data using state tracking.

        Loads sensor data from 'sensor_readings.parquet', identifies readings newer
        than the last processed timestamp (stored in 'enrichment_last_processed_ts.txt'),
        fetches weather data only for the dates covered by these new readings,
        merges the new weather data, combines it with previously enriched data,
        saves the full result to 'sensor_readings_with_weather.parquet',
        and updates the last processed timestamp state file.
        """
        self.logger.info("Starting INCREMENTAL weather enrichment process (state tracking)...")

        # Define file paths
        sensor_file = self.data_path / "sensor_readings.parquet"
        enriched_file = self.data_path / "sensor_readings_with_weather.parquet"
        state_file = self.data_path / "enrichment_last_processed_ts.txt"
        last_processed_ts = 0
        new_max_ts = 0 # Initialize timestamp to save back to state file

        # --- 1. Read Last Processed Timestamp --- 
        try:
            if state_file.exists():
                with open(state_file, 'r') as f:
                    content = f.read().strip()
                    if content:
                        last_processed_ts = int(content)
                        self.logger.info(f"Read last processed timestamp: {last_processed_ts} ({datetime.datetime.fromtimestamp(last_processed_ts, tz=pytz.utc)})")
                    else:
                        self.logger.warning(f"State file {state_file} is empty. Processing all data.")
            else:
                self.logger.info(f"State file {state_file} not found. Processing all data.")
        except ValueError:
            self.error(f"Invalid content in state file {state_file}. Expected integer timestamp. Processing all data.", raise_exception=False)
            last_processed_ts = 0 # Reset on error
        except Exception as e:
            self.error(f"Error reading state file {state_file}: {e}. Processing all data.", raise_exception=False)
            last_processed_ts = 0 # Reset on error

        # --- 2. Load Main Sensor Data ---
        try:
            self.logger.info(f"Loading sensor data from {sensor_file}...")
            if not sensor_file.exists():
                 self.error(f"Input sensor file not found at {sensor_file}. Cannot enrich.", raise_exception=False)
                 return
            df_sensor = polars.read_parquet(sensor_file)
            if df_sensor.is_empty():
                self.logger.warning(f"Sensor data file {sensor_file} is empty. Skipping enrichment.")
                return
            self.logger.info(f"Loaded {df_sensor.height} total sensor readings.")
        except Exception as e:
            self.error(f"Error loading sensor data from {sensor_file}: {e}", raise_exception=False)
            return

        # --- 3. Filter New Data & Basic Validation ---
        try:
            if "SensorReadingUTC" not in df_sensor.columns:
                 self.error(f"Missing required column 'SensorReadingUTC' in {sensor_file}. Cannot enrich.", raise_exception=False)
                 return
             # Ensure it's Int64 for timestamp operations & filtering
            if not df_sensor["SensorReadingUTC"].dtype == polars.Int64:
                try:
                    df_sensor = df_sensor.with_columns(polars.col("SensorReadingUTC").cast(polars.Int64))
                    self.logger.warning(f"Converted 'SensorReadingUTC' column in {sensor_file} to Int64.")
                except Exception as e:
                    self.error(f"Failed to cast 'SensorReadingUTC' to Int64 in {sensor_file}: {e}. Cannot enrich.", raise_exception=False)
                    return

            # Filter for readings newer than the last processed timestamp
            df_new_sensor = df_sensor.filter(polars.col("SensorReadingUTC") > last_processed_ts)

            if df_new_sensor.is_empty():
                self.logger.info(f"No new sensor readings found since last processed timestamp ({last_processed_ts}). Enrichment complete.")
                return # Nothing new to process
            
            self.logger.info(f"Found {df_new_sensor.height} new sensor readings to process.")
            
            # Find the maximum timestamp in the *new* data to save later
            new_max_ts = df_new_sensor["SensorReadingUTC"].max()
            if new_max_ts is None: # Should not happen if df is not empty, but good practice
                self.error("Could not determine maximum timestamp from new data. Aborting.", raise_exception=True)
                return 
            
        except Exception as e:
            self.error(f"Error during data filtering or validation: {e}", raise_exception=False)
            return

        # --- 4. Identify Unique Dates (from new data) and Fetch Weather Data ---
        df_weather = None # Initialize
        try:
            self.logger.info("Identifying unique dates from NEW sensor data...")
            # Use only the new sensor data subset
            unique_dates = (
                df_new_sensor 
                .select(
                    polars.from_epoch("SensorReadingUTC", time_unit="s").dt.strftime('%Y-%m-%d')
                )
                .unique()
                .to_series()
                .to_list()
            )
            self.logger.info(f"Found {len(unique_dates)} unique dates spanning NEW sensor readings.")

            if not unique_dates:
                self.logger.warning("No unique dates found in new sensor data. Skipping weather fetch.")
                # If there are no dates, we still need to merge the new sensor data (without weather) later
                all_hourly_weather = [] 
            else:
                # --- Limit Concurrency & Fetch ---
                semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_WEATHER_CALLS)
                weather_tasks = []
                async def fetch_daily_with_semaphore(date_str):
                    async with semaphore:
                        return await get_daily_hourly_weather(self.MUSEUM_LATITUDE, self.MUSEUM_LONGITUDE, date_str)

                for date_str in unique_dates:
                    weather_tasks.append(fetch_daily_with_semaphore(date_str))
                
                self.logger.info(f"Fetching hourly weather data for {len(weather_tasks)} unique dates (concurrency: {self.MAX_CONCURRENT_WEATHER_CALLS})...")
                daily_weather_results = await asyncio.gather(*weather_tasks, return_exceptions=True)
                self.logger.info("Finished fetching weather data for new dates.")

                # --- Process and Consolidate Results ---
                all_hourly_weather = []
                successful_dates = 0
                failed_dates = 0
                for i, result in enumerate(daily_weather_results):
                    if isinstance(result, Exception):
                        self.logger.warning(f"Weather API call failed for date {unique_dates[i]}: {result}")
                        failed_dates += 1
                    elif result: 
                        all_hourly_weather.extend(result)
                        successful_dates += 1
                    else:
                        self.logger.warning(f"No weather data returned for date {unique_dates[i]}.")
                        failed_dates += 1
                self.logger.info(f"Successfully fetched weather data for {successful_dates} dates. Failed/No data for {failed_dates} dates.")

            # --- Create Weather DataFrame (if data exists) ---
            if all_hourly_weather:
                self.logger.info(f"Consolidated {len(all_hourly_weather)} total hourly weather data points for new dates.")
                df_weather = polars.DataFrame(all_hourly_weather)
                # Ensure the timestamp column is Int64
                if "weather_timestamp_utc" not in df_weather.columns:
                     self.error("Weather data missing 'weather_timestamp_utc' column. Aborting.", raise_exception=False)
                     return # Cannot proceed without timestamp
                if not df_weather["weather_timestamp_utc"].dtype == polars.Int64:
                    try:
                        df_weather = df_weather.with_columns(polars.col("weather_timestamp_utc").cast(polars.Int64))
                    except Exception as e:
                         self.error(f"Failed to cast 'weather_timestamp_utc' to Int64: {e}. Aborting.", raise_exception=False)
                         return # Cannot proceed without correct timestamp type
            else:
                 self.logger.info("No new weather data points fetched or available.")
                 df_weather = None # Ensure it's None if no data

        except Exception as e:
            self.error(f"Error during weather data fetching/processing for new data: {e}", raise_exception=False)
            return

        # --- 5. Merge Weather Data with NEW Sensor Data using join_asof ---
        df_new_enriched = None
        try:
            self.logger.info("Sorting new sensor data...")
            df_new_sensor_sorted = df_new_sensor.sort("SensorReadingUTC")

            if df_weather is not None and not df_weather.is_empty():
                self.logger.info("Merging fetched weather data with new sensor data...")
                df_weather_sorted = df_weather.sort("weather_timestamp_utc")
                df_new_enriched = df_new_sensor_sorted.join_asof(
                    df_weather_sorted,
                    left_on="SensorReadingUTC",
                    right_on="weather_timestamp_utc",
                    tolerance=self.WEATHER_MATCH_TOLERANCE_SECONDS,
                    strategy="nearest"
                )
                # Optional: Log how many new rows got weather data
                null_weather_count = df_new_enriched["weather_timestamp_utc"].is_null().sum()
                self.logger.info(f"{null_weather_count} new sensor readings did not find a weather match.")
            else:
                # If no weather data, the "new enriched" data is just the new sensor data
                # Need to add null columns for weather to match schema for concatenation
                self.logger.info("No new weather data to merge. Preparing new sensor data for concatenation.")
                weather_cols_to_add = {
                    'outdoor_temperature_f': polars.lit(None, dtype=polars.Float64),
                    'outdoor_humidity': polars.lit(None, dtype=polars.Int64),
                    'weather_condition_code': polars.lit(None, dtype=polars.Int64),
                    'weather_timestamp_utc': polars.lit(None, dtype=polars.Int64)
                }
                df_new_enriched = df_new_sensor_sorted.with_columns(**weather_cols_to_add)

            self.logger.info("Finished processing new data.")
            self.logger.info(f"Newly processed data has {df_new_enriched.height} rows.")

        except Exception as e:
            self.error(f"Error during merging new data (join_asof): {e}", raise_exception=False)
            return

        # --- 6. Combine with Existing Enriched Data & Save ---
        save_successful = False
        try:
            df_combined_enriched = None
            if enriched_file.exists():
                self.logger.info(f"Loading existing enriched data from {enriched_file}...")
                try:
                    df_old_enriched = polars.read_parquet(enriched_file)
                    self.logger.info(f"Combining {df_old_enriched.height} old rows with {df_new_enriched.height} new rows.")
                    # Ensure schemas match before concatenating - select common columns or cast
                    # For simplicity, assuming new enrichment adds needed columns (might need refinement)
                    df_combined_enriched = polars.concat([df_old_enriched, df_new_enriched], how="diagonal")
                except Exception as e:
                    self.error(f"Error loading or combining with existing enriched data from {enriched_file}: {e}. Will overwrite with new data only.", raise_exception=False)
                    df_combined_enriched = df_new_enriched # Fallback to new data if old is corrupted/uncombinable
            else:
                self.logger.info("No existing enriched file found. Saving new data only.")
                df_combined_enriched = df_new_enriched

            if df_combined_enriched is not None and not df_combined_enriched.is_empty():
                self.logger.info(f"Saving combined enriched data ({df_combined_enriched.height} rows) to {enriched_file}...")
                enriched_file.parent.mkdir(parents=True, exist_ok=True)
                df_combined_enriched.write_parquet(enriched_file)
                self.logger.info(f"Successfully saved combined enriched data to {enriched_file}.")
                save_successful = True
            else:
                self.logger.warning("Combined enriched data is empty or None. Nothing to save.")
                # Consider if state should be updated if only new data existed but merging failed
                # For now, only update state if save was attempted and successful

        except Exception as e:
            self.error(f"Failed to save combined enriched data to {enriched_file}: {e}", raise_exception=False)
            # Do not update state file if saving failed

        # --- 7. Update State File (ONLY if save was successful) ---
        if save_successful:
            try:
                self.logger.info(f"Updating state file {state_file} with timestamp: {new_max_ts} ({datetime.datetime.fromtimestamp(new_max_ts, tz=pytz.utc)})")
                with open(state_file, 'w') as f:
                    f.write(str(new_max_ts))
                self.logger.info("State file updated successfully.")
            except Exception as e:
                # Log error but don't raise - main process succeeded, state update is secondary
                self.error(f"CRITICAL WARNING: Failed to update state file {state_file} after successful enrichment: {e}. Next run will reprocess data since {last_processed_ts}.", raise_exception=False)
        else:
             self.logger.warning("Enriched data saving failed or was skipped. State file will NOT be updated.")

    # <<< END OF NEW METHOD >>>

# --- Test Block Removed --- 
# The following block was for manual testing and should not be included 
# in the code run by the Docker container's cron jobs or main process.
# if __name__ == "__main__":
#    ... (rest of test block commented out or deleted) ...
# --- End of Removed Test Block ---
            
