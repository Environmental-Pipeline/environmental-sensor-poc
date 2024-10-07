import os, requests, polars, time, numpy, tqdm, copy, logging

class EnvironmentData():

    def __init__(self, CatsUserID: int, data_path: str = './data/', days_back:int = 90, testing:bool = False):

        """
        Initialize resources for managing the environmental readings. 

        Parameters
        ----------
        CatsUserID: int
            Cats User ID from Coris. Necessary for quering the API.

        data_path: str
            Data is stored using parquet files. Indicate the path to store the data. Default is './data/'.
        
        days_back: int
            Number of days of historical data to pull when initializing the database. Default is 90.
        
        testing: bool
            If True, only a few sensors will be used so tests run quickly and use fewer API calls. Default is False.
        """
        
        # Save inputs to the class instance.
        self.CatsUserID = CatsUserID
        self.data_path = data_path
        self.testing = testing

        # Create the data folder.
        if not os.path.exists(data_path):
            os.makedirs(data_path)

        # Set up logging to allow status to be viewed when run as a cron job.
        # https://docs.python.org/3/howto/logging-cookbook.html#logging-cookbook
        self.logger = logging.getLogger('EnvironmentData')
        self.logger.setLevel(logging.DEBUG)
        #ch = logging.StreamHandler() # log to console. disabling to prevent breaking tdqm
        fh = logging.FileHandler(f'{data_path}/EnvironmentData.log') # log to EnvironmentData.log in the data folder.
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        #ch.setFormatter(formatter)
        fh.setFormatter(formatter)
        #self.logger.addHandler(ch)
        self.logger.addHandler(fh)
        
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
        self.readings = {
            'Temperature': {'ReadingType': 'SensorReadingF', 'data': []}, 
            'Humidity':{'ReadingType': 'SensorReadingRh', 'data': []}
        }
        for key in self.readings:
            self.readings[key]['datafile'] = f'{data_path}/{key}{"-testing" if testing else ""}.parquet'

        # Initialize the database by creating a parquet file for each reading type and populate it with historical data.
        self.initialize_database(days_back = days_back)

    def initialize_database(self, days_back: int):

        """
        Create a parquet file for each reading type and populate it with historical data.

        Parameters
        ----------
        days_back: int
            Number of days of historical data to pull when initializing the database.
        """

        # Make a log entry and gather current and starting UTC.
        self.logger.info('initialize_database')
        sensor_ids = []
        current_utc = int(time.time())
        start_utc = current_utc - days_back * 24 * 60 * 60

        # Use the API to get historical data for each sensor type.
        readings = copy.deepcopy(self.readings)
        for key in readings:

            # If the data already exists, skip this reading type.
            # This function will run each time the class is initialized, but we only need to pull historical data once.
            if os.path.exists(readings[key]['datafile']):
                continue

            # Get sensor IDs so we can pull data for each sensor.
            if len(sensor_ids) == 0:
                sensor_ids = self.get_sensor_ids()

            # Query the API for each sensor and save the data as a polars dataframe with optimal data types.
            pbar = tqdm.tqdm(total = len(sensor_ids[key]['sensor_ids']), desc=f'Gather readings for {key}')
            for sensor_id in sensor_ids[key]['sensor_ids']:

                url = '&'.join([
                    f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={self.apikeys["CORIS"]}',
                    f'SensorID={sensor_id}',
                    f'ReadingType={readings[key]["ReadingType"]}',
                    f'StartUTC={start_utc}',
                    f'EndUTC={current_utc}',
                    f'MinReadingSpacing=600', # every 10 minutes.
                    f'RequestedOutputFormat=raw'
                ])
                logurl = '&'.join([
                    f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey=XXXX',
                    f'SensorID={sensor_id}',
                    f'ReadingType={readings[key]["ReadingType"]}',
                    f'StartUTC={start_utc}',
                    f'EndUTC={current_utc}',
                    f'MinReadingSpacing=600', # every 10 minutes.
                    f'RequestedOutputFormat=raw'
                ])
                self.logger.info(f'API call: {logurl}')
                response = requests.get(url)

                # Check for errors.
                if not response.ok:
                    self.logger.error(f'Error getting historical data for {sensor_id}: {response.json()}')
                    raise Exception(response.json())
                
                # Data comes in as comma separated values with no header. Convert this to a polars dataframe.
                data = polars.read_csv(response.content, has_header = False)
                data.columns = ['UTC', 'Reading']

                # Add the sensor ID.
                data = data.with_columns(polars.lit(sensor_id).alias('SensorID'))
                data = data.with_columns(polars.col("Reading").cast(polars.Float32)) # 32 bit is more efficient than 64 bit.
                
                # Append the data to the list of dataframes for this sensor type.
                readings[key]['data'].append(data.drop_nulls())
                pbar.update(1)

            pbar.close()

        # Combine the multiple readings into one dataframe per sensor type and write the data to a parquet file.
        for key in readings:
            if len(readings[key]['data']) > 0:
                self.logger.info(f'Initialize database for {key}.')
                polars.concat(readings[key]['data']).write_parquet(readings[key]['datafile'])

    def get_sensor_ids(self) -> dict:

        """
        Get the list of sensor ids. This is necessary for querying the API to read each sensor.

        Returns
        -------
        sensor_ids: dict
            Dictionary matching the format of self.readings with the sensor ids for each sensor type added with key = "sensor_ids".
        """

        # Build the URL and call the API.
        self.logger.info('get_sensor_ids')
        url = f'https://cats.corismonitoring.com/api/cats/user/?ApiKey={self.apikeys["CORIS"]}&CatsUserID={self.CatsUserID}'
        self.logger.info(f'API call: https://cats.corismonitoring.com/api/cats/user/?ApiKey=XXXX&CatsUserID=XXXX')
        response = requests.get(url)
        
        # Check for errors and raise an exception if there is one.
        if not response.ok:
            self.logger.error(f'Error getting sensor ids: {response.json()}')
            raise Exception(response.json())
        
        # Extract the sensor ids and separate them by type.
        current_status = polars.DataFrame(response.json()['Sensors'])
        sensor_ids = copy.deepcopy(self.readings)
        for key in sensor_ids:
            sensor_ids[key]['sensor_ids'] = current_status.filter(polars.col('SensorType') == key)['SensorID'].unique().to_list()
            if self.testing:            
                sensor_ids[key]['sensor_ids'] = sensor_ids[key]['sensor_ids'][0:3]

        return sensor_ids

    def get_current_readings(self) -> dict:

        """
        Get current readings from the API and save them to a file. 
        A batch process will consolidate readings later, 
        this function will save data as separate files to facilitate easy tracking of new data vs consolidated data.

        Returns
        -------
        readings: dict
            Dictionary matching the format of self.readings with the sensor readings added to key = "data".
        """
        
        # Make a log entry and gather the current UTC.
        self.logger.info('get_current_readings')
        current_utc = int(time.time())

        # Get the current status from the API.
        url = f'https://cats.corismonitoring.com/api/cats/user/?ApiKey={self.apikeys["CORIS"]}&CatsUserID={self.CatsUserID}'
        self.logger.info(f'API call: https://cats.corismonitoring.com/api/cats/user/?ApiKey=XXXX&CatsUserID=XXXX')
        response = requests.get(url)
        
        # Check for errors and raise an exception if there is one.
        if not response.ok:
            self.logger.error(f'Error getting sensor ids: {response.json()}')
            raise Exception(response.json())

        # The polars iterator uses tuples. Use a function to extract data, since it is a multi-step process.
        def val(row, x): 
            if x not in current_status.columns:
                self.logger.error(f'Column {x} not in response columns.')
                raise ValueError(f'Column {x} not in response columns.')
            colidx = list(numpy.where(numpy.array(current_status.columns) == x)[0])
            return row[ colidx[0] ]

        # Extract data from the response in the format of the database:
        #  - One file per reading type at self.data_path/{readingtype}.parquet
        #  - Three columns: UTC (int64), Reading (float32), SensorID (int32)
        current_status = polars.DataFrame(response.json()['Sensors'])
        readings = copy.deepcopy(self.readings)
        for row in current_status.iter_rows():
            sensortype = val(row, 'SensorType')
            if sensortype in readings:
                readings[ val(row, 'SensorType') ]['data'].append({
                    'UTC': val(row, 'SensorReadingUTC'), 
                    'Reading': val(row, 'SensorReading'), 
                    'SensorID': val(row, 'SensorID')
                })

        # Concatenate data and save it.
        for key in readings:
            if len(readings[key]['data']) > 0:                
                readings[key]['data'] = polars.DataFrame(readings[key]['data'])
                os.makedirs(f'{self.data_path}/new-readings/', exist_ok = True)
                filename = f'{self.data_path}/new-readings/{key}-{current_utc}.parquet'
                readings[key]['data'].write_parquet(filename)

        # Return the readings in case the developer wants to use them.
        return readings

    def consolidate_readings(self):

        """
        Combine new and historical readings into one database.
        This function will run as a daily batch process for better performance.
        """

        # Make a log entry.
        self.logger.info('consolidate_readings')
        
        # Run consolidate for each sensor reading type.
        new_readings = copy.deepcopy(self.readings)
        for key in new_readings:

            # Read new readings from the parquet files saved by calls to get_current_readings.
            # These are deleted after each consolidation, so these files will always be the un-consolidated files. 
            files = [i for i in os.listdir(f'{self.data_path}/new-readings/') if i.startswith(f'{key}-')]
            if len(files) == 0:
                continue
            
            for file in files:
                new_readings[key]['data'].append(polars.read_parquet(f'{self.data_path}/new-readings/{file}'))

            # Combine the readings into a single polars dataframe and set optimal and set correct data types. 
            dt = polars.concat(new_readings[key]['data']).with_columns(polars.col("Reading").cast(polars.Float32))
            dt = dt.with_columns(polars.col("UTC").cast(polars.Int64))
            dt = dt.with_columns(polars.col("SensorID").cast(polars.Int32))
            self.logger.info(f'{dt.shape[0]} new readings for {key}')

            # Append these to the database.
            dt = polars.concat([polars.read_parquet(new_readings[key]['datafile']), dt])
            dt.write_parquet(new_readings[key]['datafile'])
            self.logger.info(f'{dt.shape[0]} total readings for {key}')

            # If all this was successful, remove the new-readings files to prepare for the next consolidation.
            for file in files:
                os.remove(f'{self.data_path}/new-readings/{file}')

