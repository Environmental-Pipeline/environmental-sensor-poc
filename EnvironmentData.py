import os, requests, polars, numpy, tqdm, logging, datetime, warnings


class EnvironmentData():

    def __init__(
            self, 
            CatsUserID: int, 
            data_path: str = './data/', 
            days_back: int = int(365 * 2), 
            out_of_scope: list = [],
            testing: bool = False,
            HOBOlinkUserID: int = None,
            HOBOlinkLoggers: str | list = None,
            api_sources: list = ['CORIS']
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

        HOBOlinkUserID : int, optional
            HOBOlink User ID for accessing the HOBOlink API.

        HOBOlinkLoggers : str or list, optional
            List of HOBOlink logger IDs to monitor. Can be provided as a comma-separated string or a list.

        api_sources : list, default=['CORIS']
            List of API sources to use for data collection. Supported values: 'CORIS', 'HOBOLINK'.

        Returns
        -------
        EnvironmentData: EnvironmentData object.
        """
        
        # Save inputs to the class instance.
        self.CatsUserID = CatsUserID
        self.data_path = data_path
        self.testing = testing
        self.testing_sensor_ids = []
        self.out_of_scope = out_of_scope
        
        # Initialize HOBOlink parameters
        self.HOBOlinkUserID = HOBOlinkUserID
        self.HOBOlinkLoggers = HOBOlinkLoggers.split(',') if isinstance(HOBOlinkLoggers, str) else HOBOlinkLoggers
        self.api_sources = api_sources
        self.api_tokens = {}  # For storing API tokens
        self.token_expiry = {}  # For tracking token expiration

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
        
        # Get the API keys.
        # cron can't read environment variables so we need read the key in from the .env file.
        # See the README for a note about API key security.
        def read_env_variable(var_name):
            with open('.env') as f:
                for line in f:
                    if line.startswith(var_name):
                        return line.split('=', 1)[1].strip()

        # Initialize API keys dictionary with both CORIS and HOBOlink credentials
        self.apikeys = {
            'CORIS': read_env_variable('CORIS_API_KEY'),
            'HOBOLINK': {
                'client_id': read_env_variable('HOBOLINK_CLIENT_ID'),
                'client_secret': read_env_variable('HOBOLINK_CLIENT_SECRET')
            }
        }

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

    def initialize_coris_database(self, start_utc: int, current_utc: int) -> list:
        """
        Initialize the database with historical data from CORIS API.

        Parameters
        ----------
        start_utc : int
            Start timestamp in UTC seconds
        current_utc : int
            End timestamp in UTC seconds

        Returns
        -------
        list[polars.DataFrame]
            List of DataFrames containing sensor readings
        """
        self.logger.info('Initializing CORIS database')
        readings = []
        sensors = self.get_sensors()

        # Use the API to get historical data for each sensor type
        for reading in self.acceptable_range:
            sensor_ids = sensors.filter(polars.col(reading).is_nan().not_())['SensorID_Coris'].unique().to_list()
            if self.testing:           
                sensor_ids = sensor_ids[0:3]

            # Query the API for each sensor
            pbar = tqdm.tqdm(total=len(sensor_ids), desc=f'Gather CORIS readings: {reading}')
            for sensor_id in sensor_ids:
                if self.testing:
                    self.testing_sensor_ids.append(sensor_id)

                url = '&'.join([
                    f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={self.apikeys["CORIS"]}',
                    f'SensorID={sensor_id}',
                    f'ReadingType={reading}',
                    f'StartUTC={start_utc}',
                    f'EndUTC={current_utc}',
                    f'MinReadingSpacing=600',
                    f'RequestedOutputFormat=raw'
                ])

                # Create a duplicate of the url for logging purposes, without sensitive information
                logurl = '&'.join([
                    f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey=XXXX',
                    f'SensorID={sensor_id}',
                    f'ReadingType={reading}',
                    f'StartUTC={start_utc}',
                    f'EndUTC={current_utc}',
                    f'MinReadingSpacing=600',
                    f'RequestedOutputFormat=raw'
                ])
                self.logger.info(f'API call: {logurl}')
                response = requests.get(url)

                # Check for errors
                if not response.ok:
                    self.error(f'Error getting historical {reading} for {sensor_id}: {response.json()}', raise_exception=True)
                
                # Convert response to DataFrame
                data = polars.read_csv(response.content, has_header=False)
                data.columns = ['SensorReadingUTC', reading]

                # Add metadata
                data = data.with_columns(polars.lit(sensor_id).alias('SensorID_Coris'))
                sensor_data = sensors.filter(polars.col('SensorID_Coris') == sensor_id)
                for col in ['SensorName', 'DeviceName', 'DeviceID_Coris', 'SensorType']:
                    data = data.with_columns(polars.lit(sensor_data[col].to_list()[0]).alias(col))

                # Clean and validate
                data = self.clean_validate_sensors(sensors=data, step='initialize_coris_database')
                readings.append(data)

                pbar.update(1)
            pbar.close()

        return readings

    def initialize_hobolink_database(self, start_utc: int, current_utc: int) -> list:
        """
        Initialize the database with historical data from HOBOlink API.

        Parameters
        ----------
        start_utc : int
            Start timestamp in UTC seconds
        current_utc : int
            End timestamp in UTC seconds

        Returns
        -------
        list[polars.DataFrame]
            List of DataFrames containing sensor readings
        """
        if not self.HOBOlinkUserID or not self.HOBOlinkLoggers:
            self.logger.warning('HOBOlink credentials or loggers not configured, skipping initialization')
            return []

        self.logger.info('Initializing HOBOlink database')
        readings = []

        # Convert UTC timestamps to HOBOlink format
        start_date = datetime.datetime.fromtimestamp(start_utc, tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        end_date = datetime.datetime.fromtimestamp(current_utc, tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

        # Query each logger
        pbar = tqdm.tqdm(total=len(self.HOBOlinkLoggers), desc='Gather HOBOlink readings')
        for logger_id in self.HOBOlinkLoggers:
            try:
                logger_readings = self.get_historical_readings_hobolink(
                    logger_id=logger_id,
                    start_date=start_date,
                    end_date=end_date
                )
                readings.extend(logger_readings)
            except Exception as e:
                self.logger.error(f'Error fetching data for HOBOlink logger {logger_id}: {str(e)}')
                # Continue with other loggers even if one fails
                continue
            finally:
                pbar.update(1)
        pbar.close()

        return readings

    def initialize_database(self, days_back: int):
        """
        Get and save historical data from all configured API sources.

        Parameters
        ----------
        days_back : int
            Number of days of historical data to pull.
        """
        # If the data already exists, initialization is not necessary
        if self.cron_status != 'not-initialized':
            return

        # Make a log entry and gather current and starting UTC
        self.logger.info('Initializing database from all configured sources')
        current_utc = self.get_current_utc()
        start_utc = current_utc - days_back * 24 * 60 * 60

        all_readings = []

        # Get CORIS data if configured
        if 'CORIS' in self.api_sources:
            try:
                coris_readings = self.initialize_coris_database(start_utc, current_utc)
                all_readings.extend(coris_readings)
                self.logger.info(f'Successfully retrieved {len(coris_readings)} CORIS readings')
            except Exception as e:
                self.error(f'Error initializing CORIS database: {str(e)}', raise_exception=False)

        # Get HOBOlink data if configured
        if 'HOBOLINK' in self.api_sources:
            try:
                hobolink_readings = self.initialize_hobolink_database(start_utc, current_utc)
                all_readings.extend(hobolink_readings)
                self.logger.info(f'Successfully retrieved {len(hobolink_readings)} HOBOlink readings')
            except Exception as e:
                self.error(f'Error initializing HOBOlink database: {str(e)}', raise_exception=False)

        if not all_readings:
            self.error('No readings retrieved from any source', raise_exception=True)

        # Combine all readings and write to parquet file
        try:
            dt = polars.concat(all_readings, how='diagonal')
            dt.write_parquet(f'{self.data_path}/sensor_readings.parquet')
            self.logger.info(f'Successfully wrote {dt.shape[0]} total readings to database')
        except Exception as e:
            self.error(f'Error writing combined readings to database: {str(e)}', raise_exception=True)

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
        response = requests.get(url)
        
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
            
    def get_hobolink_token(self) -> str:
        """
        Get a valid OAuth2 access token for the HOBOlink API using client credentials flow.
        Checks for existing valid token before requesting a new one.
        Handles token storage and expiration tracking.

        Returns
        -------
        str : Valid access token for HOBOlink API
        """
        
        # Check if we have a valid token that hasn't expired
        current_utc = self.get_current_utc()
        if ('HOBOLINK' in self.api_tokens and 
            'HOBOLINK' in self.token_expiry and 
            current_utc < self.token_expiry['HOBOLINK']):
            return self.api_tokens['HOBOLINK']

        # Log that we're requesting a new token
        self.logger.info('Requesting new HOBOlink API token')

        try:
            # Prepare the token request
            token_url = 'https://webservice.hobolink.com/ws/auth/token'
            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json'
            }
            data = {
                'grant_type': 'client_credentials',
                'client_id': self.apikeys['HOBOLINK']['client_id'],
                'client_secret': self.apikeys['HOBOLINK']['client_secret']
            }

            # Make the token request
            response = requests.post(token_url, headers=headers, data=data)
            
            # Check for errors
            if not response.ok:
                error_msg = f'Error getting HOBOlink token: {response.status_code} - {response.text}'
                self.error(error_msg, raise_exception=True)

            # Parse the response
            token_data = response.json()
            if 'access_token' not in token_data or 'expires_in' not in token_data:
                error_msg = 'Invalid token response from HOBOlink API'
                self.error(error_msg, raise_exception=True)

            # Store the token and calculate expiry time (with 60-second buffer)
            self.api_tokens['HOBOLINK'] = token_data['access_token']
            self.token_expiry['HOBOLINK'] = current_utc + token_data['expires_in'] - 60

            # Log success
            self.logger.info('Successfully obtained new HOBOlink API token')
            
            return self.api_tokens['HOBOLINK']

        except requests.exceptions.RequestException as e:
            error_msg = f'Network error during HOBOlink token request: {str(e)}'
            self.error(error_msg, raise_exception=True)
        except ValueError as e:
            error_msg = f'Error parsing HOBOlink token response: {str(e)}'
            self.error(error_msg, raise_exception=True)
        except Exception as e:
            error_msg = f'Unexpected error during HOBOlink token request: {str(e)}'
            self.error(error_msg, raise_exception=True)
            
    def get_historical_readings_hobolink(self, logger_id: str, start_date: str, end_date: str) -> list:
        """
        Fetch historical data from the HOBOlink API for a specific logger.

        Parameters
        ----------
        logger_id : str
            The logger serial number to fetch data for
        start_date : str
            Start date in format 'YYYY-MM-DD HH:MM:SS'
        end_date : str
            End date in format 'YYYY-MM-DD HH:MM:SS'

        Returns
        -------
        list[polars.DataFrame]
            List of DataFrames containing sensor readings, one DataFrame per sensor/reading type
        """
        if not self.HOBOlinkUserID:
            self.error("HOBOlinkUserID not set", raise_exception=True)

        self.logger.info(f'Fetching HOBOlink data for logger {logger_id} from {start_date} to {end_date}')

        try:
            # Get OAuth token
            token = self.get_hobolink_token()

            # Prepare API request
            url = f'https://webservice.hobolink.com/ws/data/file/JSON/user/{self.HOBOlinkUserID}'
            headers = {
                'Authorization': f'Bearer {token}',
                'Accept': 'application/json'
            }
            params = {
                'loggers': logger_id,
                'start_date_time': start_date,
                'end_date_time': end_date
            }

            # Make the request
            response = requests.get(url, headers=headers, params=params)
            
            # Check for errors
            if not response.ok:
                error_msg = f'Error fetching HOBOlink data: {response.status_code} - {response.text}'
                self.error(error_msg, raise_exception=True)

            # Parse response
            data = response.json()
            if 'observation_list' not in data:
                error_msg = 'Invalid response format from HOBOlink API - missing observation_list'
                self.error(error_msg, raise_exception=True)

            observations = data['observation_list']
            if not observations:
                self.logger.warning(f'No data returned for logger {logger_id} in specified date range')
                return []

            # Process observations into DataFrames
            readings_by_sensor = {}
            for obs in observations:
                # Convert timestamp to UTC
                try:
                    timestamp = datetime.datetime.strptime(obs['timestamp'], '%Y-%m-%d %H:%M:%S')
                    timestamp_utc = int(timestamp.replace(tzinfo=datetime.timezone.utc).timestamp())
                except ValueError as e:
                    self.logger.warning(f'Invalid timestamp format in observation: {obs["timestamp"]}')
                    continue

                # Process each sensor reading
                for sensor in obs['sensor_data']:
                    sensor_id = f"{logger_id}_{sensor['sensor_sn']}"
                    reading_type = None
                    reading_value = None

                    # Determine reading type based on measurement_type
                    if 'temperature' in sensor['measurement_type'].lower():
                        reading_type = 'SensorReadingF'
                        # Convert to Fahrenheit if in Celsius
                        if 'celsius' in sensor['measurement_type'].lower():
                            reading_value = (float(sensor['value']) * 9/5) + 32
                        else:
                            reading_value = float(sensor['value'])
                    elif 'humidity' in sensor['measurement_type'].lower():
                        reading_type = 'SensorReadingRh'
                        reading_value = float(sensor['value'])

                    if reading_type and reading_value is not None:
                        key = (sensor_id, reading_type)
                        if key not in readings_by_sensor:
                            readings_by_sensor[key] = {
                                'SensorID': [],
                                'SensorReadingUTC': [],
                                reading_type: [],
                                'QueryUTC': []
                            }
                        readings_by_sensor[key]['SensorID'].append(sensor_id)
                        readings_by_sensor[key]['SensorReadingUTC'].append(timestamp_utc)
                        readings_by_sensor[key][reading_type].append(reading_value)
                        readings_by_sensor[key]['QueryUTC'].append(self.get_current_utc())

            # Convert to list of polars DataFrames
            result = []
            for (sensor_id, reading_type), readings in readings_by_sensor.items():
                if readings['SensorID']:  # Check if we have any readings
                    df = polars.DataFrame(readings)
                    # Add metadata columns
                    df = df.with_columns([
                        polars.lit(sensor_id).alias('SensorID_Coris'),
                        polars.lit(logger_id).alias('DeviceID_Coris'),
                        polars.lit(f"HOBOlink_{sensor_id}").alias('SensorName'),
                        polars.lit(f"HOBOlink Logger {logger_id}").alias('DeviceName'),
                        polars.lit(reading_type.replace('SensorReading', '')).alias('SensorType')
                    ])
                    result.append(df)

            self.logger.info(f'Successfully processed {len(result)} sensor readings for logger {logger_id}')
            return result

        except requests.exceptions.RequestException as e:
            error_msg = f'Network error during HOBOlink data request: {str(e)}'
            self.error(error_msg, raise_exception=True)
        except ValueError as e:
            error_msg = f'Error parsing HOBOlink data: {str(e)}'
            self.error(error_msg, raise_exception=True)
        except Exception as e:
            error_msg = f'Unexpected error during HOBOlink data request: {str(e)}'
            self.error(error_msg, raise_exception=True)
            
