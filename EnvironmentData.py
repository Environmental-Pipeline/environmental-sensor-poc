import os, requests, polars, time, numpy, tqdm, copy, logging

class EnvironmentData():

    def __init__(
            self, 
            CatsUserID: int, 
            data_path: str = './data/', 
            days_back: int = 90, 
            out_of_scope: list = [],
            testing: bool = False
        ):

        """
        Initialize resources for managing the environmental readings. 

        Parameters
        ----------
        CatsUserID: int
            Cats User ID from Coris. Necessary for querying the API.

        data_path: str
            Data is stored using parquet files. Indicate the path to store the data. Default is './data/'.
        
        days_back: int
            Number of days of historical data to pull when initializing the database. Default is 90.

        out_of_scope: list
            List of strings that indicate sensors that are out of scope and should be removed. 
            SensorName will be check and sensors ignored if it starts with one of these values. 
            Default is an empty list.
        
        testing: bool
            If True, only a few sensors will be used so tests run quickly and use fewer API calls. Default is False.
        """
        
        # Save inputs to the class instance.
        self.CatsUserID = CatsUserID
        self.data_path = data_path
        self.testing = testing
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

    def close(self):

        """
        The class uses a logger to track status and errors. This function closes the logger when the class is done.
        """

        for handler in self.logger.handlers:
            handler.close()
            self.logger.removeHandler(handler)
        
        for handler in self.logger_err.handlers:
            handler.close()
            self.logger_err.removeHandler(handler)

    def error(self, msg, raise_exception):

        """
        Log an error message to the error log file and regular log file and raise an exception.

        Parameters
        ----------
        msg: str
            Error message.

        raise_exception: bool
            If True, raise an exception and halt processing. If False, log the error and continue processing.
        """

        self.logger_err.error(msg)
        self.logger.error(msg)

        if raise_exception:
            raise Exception(msg)

    def initialize_database(self, days_back: int):

        """
        Create a parquet file for each reading type and populate it with historical data.

        Parameters
        ----------
        days_back: int
            Number of days of historical data to pull when initializing the database.
        """

        # If the data already exists, initialization is not necessary.
        if os.path.exists(f'{self.data_path}/sensors.parquet'):
            return

        # Make a log entry and gather current and starting UTC.
        self.logger.info('initialize_database')
        sensor_ids = []
        current_utc = int(time.time())
        start_utc = current_utc - days_back * 24 * 60 * 60
        sensors = self.get_sensors()

        # Use the API to get historical data for each sensor type.
        readings = []
        for reading in self.acceptable_range:

            sensor_ids = sensors.filter(polars.col(reading).is_nan().not_())['SensorID'].unique().to_list()
            if self.testing:           
                sensor_ids = sensor_ids[0:3]

            # Query the API for each sensor and save the data as a polars DataFrame with optimal data types.
            pbar = tqdm.tqdm(total = len(sensor_ids), desc=f'Gather readings: {reading}')
            for sensor_id in sensor_ids:

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
                response = requests.get(url)

                # Check for errors.
                if not response.ok:
                    self.logger.error(f'Error getting historical {reading} for {sensor_id}: {response.json()}', raise_exception = True)
                
                # Data comes in as comma separated values with no header. Convert this to a polars DataFrame.
                data = polars.read_csv(response.content, has_header = False)
                data.columns = ['SensorReadingUTC', reading]

                # Add the sensor ID.
                data = data.with_columns(polars.lit(sensor_id).alias('SensorID'))

                # Set data types. 
                data = data.with_columns(polars.col('SensorID').cast(polars.Int32))
                data = data.with_columns(polars.col('SensorReadingUTC').cast(polars.Int64))
                data = data.with_columns(polars.col(reading).cast(polars.Float32))

                # Rearrange columns.
                data = data[['SensorID', 'SensorReadingUTC', reading]]
                
                # Append the data to the list of DataFrame for this sensor type.
                readings.append(data)

                pbar.update(1)

            pbar.close()

        # Combine the readings into a single polars DataFrame.
        dt = polars.concat(readings, how = 'diagonal')

        # Clean the data.
        dt = self.clean_validate_sensors(dt)
        
        # Write the database file. 
        dt.write_parquet(f'{self.data_path}/sensors.parquet')

    def get_sensors(self) -> polars.DataFrame:

        """
        Get data from the sensors.

        Returns
        -------
        sensor: polars.DataFrame
            DataFrame containing the sensors returned by the API. Contains SensorID, Device, readings, and more. 
        """

        # Build the URL and call the API.
        self.logger.info('get_sensors')
        url = f'https://cats.corismonitoring.com/api/cats/user/?ApiKey={self.apikeys["CORIS"]}&CatsUserID={self.CatsUserID}'
        self.logger.info(f'API call: https://cats.corismonitoring.com/api/cats/user/?ApiKey=XXXX&CatsUserID=XXXX') # log a duplicate of the url for logging purposes, which doesn't include sensitive information.
        response = requests.get(url)
        
        # Check for errors and raise an exception if there is one.
        if not response.ok:
            self.logger.error(f'Error getting sensors: {response.json()}')
            raise Exception(response.json())
        
        # Remove out-of-scope sensors.
        sensors = polars.DataFrame(response.json()['Sensors'])
        for i in self.out_of_scope:            
            sensors = sensors.filter(polars.col('SensorName').str.starts_with(i).not_())

        # Return the data. 
        return sensors

    def get_current_readings(self) -> dict:

        """
        Get current readings from the API and save them to a file. 
        This function will save data as separate files to facilitate easy tracking of new data vs consolidated data.
        A batch process will clean, validate, and consolidate readings later.
        """
        
        # Make a log entry and gather the current UTC.
        current_utc = int(time.time())
        self.logger.info(f'get_current_readings: {current_utc}')

        # Get the current status from the API.
        sensors = self.get_sensors()

        # Process alerts.
        self.send_alerts(sensors, current_utc)

        # Save the new-readings file. A daily process will pull these later to clean, validate, and consolidate them into the database.
        os.makedirs(f'{self.data_path}/new-readings/', exist_ok = True)
        sensors.write_parquet(f'{self.data_path}/new-readings/{current_utc}.parquet')


    def consolidate_readings(self):

        """
        Combine new and historical readings into one database.
        This function will run as a daily batch process for better performance.
        """

        # Make a log entry.
        self.logger.info('consolidate_readings')
        new_readings = []
               
        # Read new readings from the parquet files saved by calls to get_current_readings.
        # These are deleted after each consolidation, so these files will always be the un-consolidated files. 
        files = os.listdir(f'{self.data_path}/new-readings/')        
        for file in files:
            new_readings.append(polars.read_parquet(f'{self.data_path}/new-readings/{file}'))

        # Combine the readings into a single polars DataFrame.
        dt = polars.concat(new_readings)
        self.logger.info(f'{dt.shape[0]} new readings.')

        # Clean the data. 
        dt = self.clean_validate_sensors(dt)

        # Set the column data types to match the database.
        db = polars.read_parquet(f'{self.data_path}/sensors.parquet')
        dt = self.match_types(dt, db)

        # Append these to the database.
        dt = polars.concat([db, dt], how = 'diagonal')

        # Move the most important columns to the front. 
        first_cols = ['DeviceDevID', 'DeviceName', 'SensorID', 'SensorReadingUTC'] + list(self.acceptable_range.keys())
        dt = dt.select(first_cols + [x for x in dt.columns if x not in first_cols])

        # Write the file. 
        dt.write_parquet(f'{self.data_path}/sensors.parquet')
        self.logger.info(f'{dt.shape[0]} total readings.')

        # If all this was successful, remove the new-readings files to prepare for the next consolidation.
        for file in files:
            os.remove(f'{self.data_path}/new-readings/{file}')

        # Refresh the devices table. 
        self.build_devices(dt).write_parquet(f'{self.data_path}/devices.parquet')

    def match_types(self, data: polars.DataFrame, match: polars.DataFrame) -> polars.DataFrame:
            
            """
            Match the data types of two DataFrames. 
    
            Parameters
            ----------
            data: polars.DataFrame
                DataFrame to match.
            
            match: polars.DataFrame
                DataFrame to match to.
    
            Returns
            -------
            data: polars.DataFrame
                DataFrame with matching data types.
            """
    
            for col in match.columns:
                if col in data.columns and data[col].dtype != match[col].dtype:
                    data = data.with_columns(data[col].cast(match[col].dtype))
            
            return data

    def build_devices(self, data: polars.DataFrame) -> polars.DataFrame:

        """
        Build a devices table from the sensor data.
        This requires pulling unique devices and joining sensor data.

        Parameters
        ----------
        data: polars.DataFrame
            DataFrame containing the sensor data.

        Returns
        -------
        devices: polars.DataFrame
            DataFrame containing the devices data.
        """

        # Get the data for each of the selected sensors.
        devices = None
        data = data.filter(polars.col('DeviceDevID').is_null().not_())
        for reading in self.acceptable_range:
            idt = data.filter(polars.col(reading).is_null().not_()).select(['DeviceDevID', 'SensorReadingUTC', reading])
            if isinstance(devices, polars.DataFrame):
                devices = devices.join(idt, how = 'full', on = ['DeviceDevID', 'SensorReadingUTC'])
            else:
                devices = idt
            del idt, reading
        
        # this will result in columns like DeviceDevID_right when there is not a perfect match. 
        # coalesce to a single column.
        cols_DeviceDevID = [x for x in devices.columns if 'DeviceDevID' in x]
        cols_SensorReadingUTC = [x for x in devices.columns if 'SensorReadingUTC' in x]
        devices = devices.with_columns(polars.coalesce(cols_DeviceDevID).alias('DeviceDevID'))
        devices = devices.with_columns(polars.coalesce(cols_SensorReadingUTC).alias('SensorReadingUTC'))
        devices = devices.drop([x for x in cols_DeviceDevID + cols_SensorReadingUTC if x not in ['DeviceDevID', 'SensorReadingUTC']])
        
        # If a device has multiple names, error out:
        device_names = data.filter(polars.col('DeviceDevID').is_null().not_()).select(['DeviceDevID', 'DeviceName']).unique()
        if device_names.shape[0] != device_names['DeviceName'].unique().shape[0]:
            self.error('DeviceName to DeviceDevID is not a 1-1 mapping.', raise_exception = True)
        
        # Attach the device name.
        devices = devices.join(device_names, how = 'left', on = 'DeviceDevID')

        # Rearrange columns. 
        devices = devices.select(['DeviceDevID', 'DeviceName', 'SensorReadingUTC'] + list(self.acceptable_range.keys()))

        # Return the data.
        return devices
    
    def clean_validate_sensors(self, sensors: polars.DataFrame) -> polars.DataFrame:

        """
        Clean sensor reading data. Data gets cleaned during consolidation.

        Parameters
        ----------
        sensors: polars.DataFrame
            Sensors API response DataFrame to clean.

        Returns
        -------
        data: polars.DataFrame
            Cleaned DataFrame.
        """

        # Set data types. 
        sensors = sensors.with_columns(polars.col('SensorID').cast(polars.Int32))
        sensors = sensors.with_columns(polars.col('SensorReadingUTC').cast(polars.Int64))
        for reading in self.acceptable_range:
            sensors = sensors.with_columns(polars.col(reading).cast(polars.Float32))

        # Validate the data.
        self.validate_sensors(sensors)

        return sensors

    def validate_sensors(self, sensors: polars.DataFrame):

        """
        Validate sensor reading data.

        Parameters
        ----------
        sensors: polars.DataFrame
            DataFrame to validate.
        """

        errs = []
        
        # We expect missing values in the data, so we don't check for them.

        # Is the data format as expected?
        expect_types = {"SensorReadingUTC": polars.Int64, "SensorID": polars.Int32}
        for reading in self.acceptable_range:
            expect_types[reading] = polars.Float32
        
        for col in expect_types:
            if sensors[col].dtype != expect_types[col]:
                errs.append(f'Unexpected data type for [{col}]. Expected [{expect_types[col]}] got [{sensors[col]}].')

        # Log the errors.
        if len(errs) > 0:
            self.error(f'Validation errors: {". ".join(errs)}', raise_exception = False)

    def which(x): 
        return list(numpy.where(x)[0])

    def send_alerts(self, sensors: polars.DataFrame, utc: int):

        """
        Send alerts based on the sensor data.

        Parameters
        ----------
        sensors: polars.DataFrame
            DataFrame containing the sensor data.
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
        