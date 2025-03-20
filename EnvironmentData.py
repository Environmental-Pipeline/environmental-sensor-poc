import os, requests, polars, numpy, tqdm, logging, datetime, warnings, pathlib

class EnvironmentData():

    def __init__(
            self, 
            CatsUserID: int, 
            data_path: str = './data/', 
            days_back: int = int(365 * 2), 
            out_of_scope: list = [],
            testing: bool = False,
            hobolink_enabled: bool = None
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
            
        hobolink_enabled : bool, default=None
            Enable HOBOlink data collection. If None, will use the value from .env file.
            Set to False to disable HOBOlink data collection even if credentials are available.

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
        
        # Get the API keys and configuration.
        # cron can't read environment variables so we need read the key in from the .env file.
        # See the README for a note about API key security.
        def read_env_variable(var_name, default=None):
            try:
                with open('.env') as f:
                    for line in f:
                        if line.strip() and not line.strip().startswith('#') and line.startswith(var_name):
                            return line.split('=', 1)[1].strip()
            except Exception as e:
                self.logger.error(f"Error reading environment variable {var_name}: {e}")
            return default

        # Read API keys and configuration
        self.apikeys = {'CORIS': read_env_variable('CORIS_API_KEY')}
        
        # HOBOlink configuration
        self.hobolink = {
            'client_id': read_env_variable('HOBOLINK_CLIENT_ID'),
            'client_secret': read_env_variable('HOBOLINK_CLIENT_SECRET'),
            'user_id': read_env_variable('HOBOLINK_USER_ID'),
            'loggers': read_env_variable('HOBOLINK_LOGGERS', '').split(','),
            'enabled': hobolink_enabled if hobolink_enabled is not None else read_env_variable('HOBOLINK_ENABLED', 'False').lower() == 'true',
            'token': None,  # Will store the OAuth token
            'token_expiry': 0  # Will store token expiry timestamp
        }
        
        # Check if HOBOlink credentials are available and properly configured
        self.hobolink['available'] = (
            self.hobolink['client_id'] is not None and 
            self.hobolink['client_secret'] is not None and 
            self.hobolink['user_id'] is not None and 
            len(self.hobolink['loggers']) > 0 and 
            self.hobolink['loggers'][0] != ''
        )
        
        # Log HOBOlink configuration status
        if self.hobolink['available'] and self.hobolink['enabled']:
            self.logger.info(f"HOBOlink integration enabled with {len(self.hobolink['loggers'])} loggers")
        elif self.hobolink['available'] and not self.hobolink['enabled']:
            self.logger.info("HOBOlink credentials available but integration is disabled by configuration")
        else:
            self.logger.info("HOBOlink integration not available - missing or invalid credentials")

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

    def get_hobolink_token(self, force_refresh=False):
        """
        Get a valid OAuth token for HOBOlink API authentication.
        
        If a token exists and isn't expired, returns the existing token.
        Otherwise, retrieves a new token via OAuth.
        
        Parameters
        ----------
        force_refresh : bool, default=False
            If True, forces token refresh even if the current token is still valid.
            
        Returns
        -------
        str or None
            The OAuth token if successful, None if authentication fails or HOBOlink is not available.
        """
        if not self.hobolink['available'] or not self.hobolink['enabled']:
            self.logger.debug("HOBOlink integration not available or disabled. Not retrieving token.")
            return None
            
        current_utc = self.get_current_utc()
        
        # Return existing token if it's still valid
        if not force_refresh and self.hobolink['token'] is not None and current_utc < self.hobolink['token_expiry'] - 60:  # 60-second buffer
            self.logger.debug("Using existing HOBOlink token")
            return self.hobolink['token']
        
        # Request a new token
        token_url = 'https://webservice.hobolink.com/ws/auth/token'
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json'
        }
        data = {
            'grant_type': 'client_credentials',
            'client_id': self.hobolink['client_id'],
            'client_secret': self.hobolink['client_secret']
        }
        
        self.logger.info("Requesting new HOBOlink authentication token")
        
        try:
            response = requests.post(token_url, headers=headers, data=data)
            response.raise_for_status()
            
            token_data = response.json()
            self.hobolink['token'] = token_data['access_token']
            # Calculate expiry time (token_data['expires_in'] is in seconds)
            self.hobolink['token_expiry'] = current_utc + token_data['expires_in']
            
            self.logger.info(f"Successfully obtained HOBOlink authentication token. Expires in {token_data['expires_in']} seconds")
            return self.hobolink['token']
        except requests.exceptions.HTTPError as e:
            error_message = f"HTTP Error during HOBOlink authentication: {e}"
            self.error(error_message)
            if hasattr(response, 'text'):
                self.logger.error(f"Response body: {response.text}")
        except requests.exceptions.ConnectionError as e:
            self.error(f"Connection Error during HOBOlink authentication: {e}")
        except requests.exceptions.Timeout as e:
            self.error(f"Timeout Error during HOBOlink authentication: {e}")
        except requests.exceptions.RequestException as e:
            self.error(f"Request Exception during HOBOlink authentication: {e}")
        except ValueError as e:
            self.error(f"JSON parsing error: {e}")
            if hasattr(response, 'text'):
                self.logger.error(f"Response content: {response.text}")
        
        # Reset token in case of error
        self.hobolink['token'] = None
        self.hobolink['token_expiry'] = 0
        return None

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
        
        # Add Source column to identify CORIS data
        sensors = sensors.with_columns(polars.lit('CORIS').alias('Source'))

        # Use the API to get historical data for each sensor type.
        readings = []
        for reading in self.acceptable_range:

            sensor_ids = sensors.filter(polars.col(reading).is_nan().not_())['SensorID_Coris'].unique().to_list()
            if self.testing:           
                sensor_ids = sensor_ids[0:3]

            # Query the API for each sensor and save the data as a polars DataFrame with optimal data types.
            pbar = tqdm.tqdm(total = len(sensor_ids), desc=f'Gather CORIS readings: {reading}')
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
                response = requests.get(url)

                # Check for errors.
                if not response.ok:
                    self.logger.error(f'Error getting historical {reading} for {sensor_id}: {response.json()}', raise_exception = True)
                
                # Data comes in as comma separated values with no header. Convert this to a polars DataFrame.
                data = polars.read_csv(response.content, has_header = False)
                data.columns = ['SensorReadingUTC', reading]

                # Add data from the sensors dataset. 
                data = data.with_columns(polars.lit(sensor_id).alias('SensorID_Coris'))
                data = data.with_columns(polars.lit('CORIS').alias('Source'))
                sensor_data = sensors.filter(polars.col('SensorID_Coris') == sensor_id)
                for col in ['SensorName', 'DeviceName', 'DeviceID_Coris', 'SensorType']:
                    data = data.with_columns(polars.lit(sensor_data[col].to_list()[0]).alias(col))

                # Clean and validate the data. 
                data = self.clean_validate_sensors(sensors = data, historical = polars.DataFrame(), utc = current_utc, step = 'initialize_database')
                
                # Append the data to the list of DataFrame for this sensor type.
                readings.append(data)

                pbar.update(1)

            pbar.close()
            
        # Get HOBOlink historical data if available and enabled
        if self.hobolink['available'] and self.hobolink['enabled']:
            self.logger.info("Retrieving historical HOBOlink data")
            try:
                # Retrieve data for the specified number of days back
                hobolink_data = self.get_hobolink_data(days_back=days_back)
                
                if hobolink_data is not None and not hobolink_data.is_empty():
                    self.logger.info(f"Retrieved {hobolink_data.shape[0]} historical HOBOlink readings")
                    
                    # Clean and validate HOBOlink data
                    hobolink_data = self.clean_validate_sensors(sensors=hobolink_data, historical=polars.DataFrame(), utc=current_utc, step='initialize_database')
                    
                    # Add to the collection of readings
                    readings.append(hobolink_data)
                else:
                    self.logger.warning("No historical HOBOlink data was retrieved")
            except Exception as e:
                self.error(f"Error retrieving historical HOBOlink data: {e}")

        # Combine the readings into a single polars DataFrame.
        if not readings:
            self.error("No readings data available to initialize database", raise_exception=True)
            
        dt = polars.concat(readings, how='diagonal')
        
        # Ensure ID columns from both sources exist
        required_columns = ['SensorID_Coris', 'DeviceID_Coris', 'SensorID_HOBOlink', 'DeviceID_HOBOlink']
        for col in required_columns:
            if col not in dt.columns:
                dt = dt.with_columns(polars.lit(None).alias(col))
        
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
        sensors = self.clean_validate_sensors(sensors = sensors, historical = polars.DataFrame(), utc = current_utc, step = 'get_sensors')

        # Return the data. 
        return sensors
    
    def get_current_utc(self) -> int:
            
        """Get the current UTC timestamp (seconds since epoch)."""
        dt = datetime.datetime.now(datetime.timezone.utc)
        utc = int(dt.timestamp())
        return utc

    def get_hobolink_data(self, days_back=1):
        """
        Fetch data from the HOBOlink API for all configured loggers.
        
        Parameters
        ----------
        days_back : int, default=1
            Number of days of historical data to retrieve
            
        Returns
        -------
        polars.DataFrame or None
            DataFrame containing sensor readings from HOBOlink, or None if retrieval fails
        """
        if not self.hobolink['available'] or not self.hobolink['enabled']:
            self.logger.debug("HOBOlink integration not available or disabled. Not retrieving data.")
            return None
        
        token = self.get_hobolink_token()
        if not token:
            self.error("Failed to get HOBOlink authentication token. Cannot retrieve data.")
            return None
        
        # Calculate date range
        end_date = datetime.datetime.now(datetime.timezone.utc)
        start_date = end_date - datetime.timedelta(days=days_back)
        
        start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
        end_date_str = end_date.strftime('%Y-%m-%d %H:%M:%S')
        
        # Dictionary to hold readings by type
        readings_by_type = {
            'SensorReadingF': [],
            'SensorReadingRh': []
        }
        current_utc = self.get_current_utc()
        
        # Logger name mapping - can be expanded as needed
        logger_name_mapping = {
            "20284065": "C134",
            "20447203": "LML Cold Room",
            "22040302": "MX TRH 3",
            "22040303": "MX TRH 1",
            "22040304": "MX TRH 2",
            "22040305": "MX TRH 4"
        }
        
        for logger_id in self.hobolink['loggers']:
            if not logger_id:  # Skip empty strings
                continue
                
            self.logger.info(f"Retrieving HOBOlink data for logger {logger_id}")
            
            url = f'https://webservice.hobolink.com/ws/data/file/JSON/user/{self.hobolink["user_id"]}'
            headers = {
                'Authorization': f'Bearer {token}',
                'Accept': 'application/json'
            }
            params = {
                'loggers': logger_id,
                'start_date_time': start_date_str,
                'end_date_time': end_date_str
            }
            
            try:
                response = requests.get(url, headers=headers, params=params)
                response.raise_for_status()
                data = response.json()
                
                if 'observation_list' not in data or not data['observation_list']:
                    self.logger.warning(f"No observations found for HOBOlink logger {logger_id}")
                    continue
                    
                observations = data['observation_list']
                self.logger.info(f"Retrieved {len(observations)} observations for HOBOlink logger {logger_id}")
                
                # Process and transform observations
                for obs in observations:
                    # Extract key information
                    sensor_id = obs['sensor_sn']  # like "20284065-1"
                    measurement_type = obs['sensor_measurement_type']
                    
                    # Convert ISO timestamp to Unix timestamp
                    timestamp_str = obs['timestamp'].rstrip('Z')
                    dt = datetime.datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                    unix_timestamp = int(dt.timestamp())
                    
                    # Determine reading type and value
                    if measurement_type == 'Temperature':
                        reading_type = 'SensorReadingF'
                        reading_value = float(obs['us_value'])  # Fahrenheit value
                    elif measurement_type == 'RH':
                        reading_type = 'SensorReadingRh'
                        reading_value = float(obs['si_value'])  # RH value
                    else:
                        # Skip other sensor types for now
                        self.logger.debug(f"Skipping unsupported HOBOlink sensor type: {measurement_type}")
                        continue
                    
                    # Get device name from mapping or generate a default
                    device_name = logger_name_mapping.get(logger_id, f"HOBOlink Logger {logger_id}")
                    
                    # Create a record that matches the expected format
                    record = {
                        'SensorID_HOBOlink': sensor_id,
                        'SensorReadingUTC': unix_timestamp,
                        reading_type: reading_value,
                        'QueryUTC': current_utc,
                        'DeviceName': device_name,
                        'SensorName': f"{device_name} {measurement_type} {sensor_id.split('-')[1]}",
                        'SensorType': measurement_type,
                        'DeviceID_HOBOlink': logger_id,
                        'Source': 'HOBOlink'
                    }
                    
                    # Add to the appropriate list
                    readings_by_type[reading_type].append(record)
                
            except requests.exceptions.HTTPError as e:
                self.error(f"HTTP Error retrieving HOBOlink data for logger {logger_id}: {e}")
                if hasattr(response, 'text'):
                    self.logger.error(f"Response body: {response.text}")
                continue
            except requests.exceptions.ConnectionError as e:
                self.error(f"Connection Error retrieving HOBOlink data for logger {logger_id}: {e}")
                continue
            except requests.exceptions.Timeout as e:
                self.error(f"Timeout Error retrieving HOBOlink data for logger {logger_id}: {e}")
                continue
            except requests.exceptions.RequestException as e:
                self.error(f"Request Exception retrieving HOBOlink data for logger {logger_id}: {e}")
                continue
            except Exception as e:
                self.error(f"Unexpected error retrieving or processing HOBOlink data for logger {logger_id}: {e}")
                continue
        
        # Convert to DataFrames
        dataframes = []
        
        for reading_type, readings in readings_by_type.items():
            if readings:
                df = polars.DataFrame(readings)
                
                # Apply consistent data types
                df = df.with_columns([
                    polars.col('SensorReadingUTC').cast(polars.Int64),
                    polars.col('QueryUTC').cast(polars.Int64),
                    polars.col(reading_type).cast(polars.Float32),
                ])
                
                dataframes.append(df)
                self.logger.info(f"Processed {df.shape[0]} HOBOlink {reading_type} readings")
        
        # Combine all readings
        if not dataframes:
            self.logger.warning("No HOBOlink data found to process")
            return None
            
        combined = polars.concat(dataframes, how='diagonal')
        self.logger.info(f"Retrieved total of {combined.shape[0]} HOBOlink readings")
        return combined

    def get_current_readings(self) -> dict:

        """
        Get current sensor readings from CORIS API, validate data, send alerts, save to new-readings folder.
        This is meant to run as a cron job, several times per day.

        Returns
        -------
        dict : {'sensors': polars.DataFrame, 'utc': int} containing the sensor readings data.
        """
        
        # Make sure that the database is initialized.
        # Otherwise, block cron job.
        if self.cron_status == 'not-initialized':
            return

        # Make a log entry.
        current_utc = self.get_current_utc()
        self.logger.info(f'get_current_readings - {current_utc}')

        # Get the sensor data from CORIS API
        sensors = self.get_sensors()
        
        # Get HOBOlink data if available and enabled
        hobolink_data = None
        if self.hobolink['available'] and self.hobolink['enabled']:
            try:
                hobolink_data = self.get_hobolink_data(days_back=1)
                if hobolink_data is not None:
                    self.logger.info(f"Successfully retrieved HOBOlink data with {hobolink_data.shape[0]} readings")
            except Exception as e:
                self.error(f"Error retrieving HOBOlink data: {e}")
        
        # Combine CORIS and HOBOlink data if both are available
        if hobolink_data is not None:
            # Add Source column to CORIS data
            sensors = sensors.with_columns(polars.lit('CORIS').alias('Source'))
            
            # Make sure both datasets have compatible columns
            common_columns = set(sensors.columns).intersection(set(hobolink_data.columns))
            all_columns = set(sensors.columns).union(set(hobolink_data.columns))
            
            # Add missing columns with null values
            for col in all_columns:
                if col not in sensors.columns:
                    if col in ['SensorReadingUTC', 'QueryUTC']:
                        sensors = sensors.with_columns(polars.lit(None).cast(polars.Int64).alias(col))
                    elif col in ['SensorReadingF', 'SensorReadingRh']:
                        sensors = sensors.with_columns(polars.lit(None).cast(polars.Float32).alias(col))
                    else:
                        sensors = sensors.with_columns(polars.lit(None).alias(col))
                
                if col not in hobolink_data.columns:
                    if col in ['SensorReadingUTC', 'QueryUTC']:
                        hobolink_data = hobolink_data.with_columns(polars.lit(None).cast(polars.Int64).alias(col))
                    elif col in ['SensorReadingF', 'SensorReadingRh']:
                        hobolink_data = hobolink_data.with_columns(polars.lit(None).cast(polars.Float32).alias(col))
                    else:
                        hobolink_data = hobolink_data.with_columns(polars.lit(None).alias(col))

            # Combine the data
            sensors = polars.concat([sensors, hobolink_data], how='diagonal')
            self.logger.info(f"Combined data contains {sensors.shape[0]} readings (CORIS + HOBOlink)")
        
        # Validate the sensor data.
        historical = polars.read_parquet(f'{self.data_path}/sensor_readings.parquet')
        sensors = self.clean_validate_sensors(sensors, historical, current_utc, 'get_current_readings')
        
        # Validate the sensor data
        self.validate_sensors(sensors=sensors, historical=historical, utc=current_utc, step='get_current_readings')
        
        # Send alerts.
        self.send_alerts(sensors, current_utc)
        
        # Save the readings to parquet files in the new-readings dir.
        if not os.path.exists(f'{self.data_path}/new-readings/'):
            os.makedirs(f'{self.data_path}/new-readings/')
        
        # Append a timestamp to make the filename unique.
        # This also helps us know the chronology if needed.
        sensors.write_parquet(f'{self.data_path}/new-readings/{current_utc}_sensor_readings.parquet')
        
        return {'sensors': sensors, 'utc': current_utc}

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
            except Exception as e:
                self.logger.warning(f"Could not read file {file}: {e}")
                continue

        if not new_readings:
            self.logger.info("No new readings to consolidate")
            return
            
        # Combine the readings into a single polars DataFrame.
        dt = polars.concat(new_readings)
        self.logger.info(f'{dt.shape[0]} new readings.')

        # Clean the data.
        historical = polars.read_parquet(f'{self.data_path}/sensor_readings.parquet')
        dt = self.clean_validate_sensors(sensors=dt, historical=historical, step='consolidate_readings')

        # Check and handle different data sources (CORIS and HOBOlink)
        if 'Source' not in historical.columns and 'Source' in dt.columns:
            # Add Source column to historical data if it doesn't exist
            historical = historical.with_columns(polars.lit('CORIS').alias('Source'))
            self.logger.info("Added Source column to historical data")
            
        # Ensure ID columns from both sources exist
        required_columns = ['SensorID_Coris', 'DeviceID_Coris', 'SensorID_HOBOlink', 'DeviceID_HOBOlink']
        for col in required_columns:
            if col not in historical.columns and col in dt.columns:
                historical = historical.with_columns(polars.lit(None).alias(col))
            elif col not in dt.columns and col in historical.columns:
                dt = dt.with_columns(polars.lit(None).alias(col))

        # Set the column data types to match the database.
        dt = self.match_types(dt, historical)

        # Append these to the database.
        dt = polars.concat([historical, dt], how='diagonal')

        # Add difference between readings, handling both CORIS and HOBOlink data
        dt = dt.sort(['Source', 'SensorID_Coris', 'SensorID_HOBOlink', 'SensorReadingUTC'])
        
        # For CORIS data, group by SensorID_Coris
        coris_data = dt.filter(
            (polars.col('Source') == 'CORIS') | 
            ((polars.col('Source').is_null()) & (polars.col('SensorID_Coris').is_not_null()))
        )
        if not coris_data.is_empty():
            coris_diff = coris_data.group_by('SensorID_Coris', maintain_order=True).map_groups(
                lambda x: x.with_columns((
                    polars.col('SensorReadingUTC') - polars.col('SensorReadingUTC').shift(1)
                ).alias('SensorReadingUTC_SecondsFromPrior'))
            )['SensorReadingUTC_SecondsFromPrior']
            coris_data = coris_data.with_columns(coris_diff)
        
        # For HOBOlink data, group by SensorID_HOBOlink
        hobolink_data = dt.filter(
            (polars.col('Source') == 'HOBOlink') & (polars.col('SensorID_HOBOlink').is_not_null())
        )
        if not hobolink_data.is_empty():
            hobolink_diff = hobolink_data.group_by('SensorID_HOBOlink', maintain_order=True).map_groups(
                lambda x: x.with_columns((
                    polars.col('SensorReadingUTC') - polars.col('SensorReadingUTC').shift(1)
                ).alias('SensorReadingUTC_SecondsFromPrior'))
            )['SensorReadingUTC_SecondsFromPrior']
            hobolink_data = hobolink_data.with_columns(hobolink_diff)
        
        # Recombine the data
        if not coris_data.is_empty() and not hobolink_data.is_empty():
            dt = polars.concat([coris_data, hobolink_data], how='diagonal')
        elif not coris_data.is_empty():
            dt = coris_data
        elif not hobolink_data.is_empty():
            dt = hobolink_data
        
        # Move the most important columns to the front.
        important_columns = [
            'Source', 'SensorID_Coris', 'SensorID_HOBOlink', 'QueryUTC', 'SensorReadingUTC', 
            'DeviceID_Coris', 'DeviceID_HOBOlink', 'SensorReadingUTC_SecondsFromPrior'
        ]
        important_columns = [col for col in important_columns if col in dt.columns]
        reading_columns = [col for col in self.acceptable_range.keys() if col in dt.columns]
        dt = self.relocate(dt, important_columns + reading_columns)

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
    
    def clean_validate_sensors(self, sensors: polars.DataFrame, historical: polars.DataFrame = polars.DataFrame(), utc: int = None, step: str = '') -> polars.DataFrame:

        """
        Clean the sensor readings data.
        Currently, this only sets efficient data types. It can be expanded to include more cleaning steps.
        Then, validate the data because these two operations typically occur together.

        Parameters
        ----------
        sensors : polars.DataFrame
            Sensors API response.
            
        historical : polars.DataFrame, default=polars.DataFrame()
            Historical readings to compare against for validation.
            
        utc : int, default=None
            The UTC timestamp of when the data was collected, used for validation.
            
        step : str, default=''
            The processing step name for logging purposes.

        Returns
        -------
        polars.DataFrame : Cleaned DataFrame.
        """

        # Set data types.
        dtypes = {
            'SensorID': polars.Utf8,  # this is extracted from the SensorName, it isn't always a number.
            'SensorID_Coris': polars.Int32,
            'DeviceID_Coris': polars.Int32,
            'SensorID_HOBOlink': polars.Utf8,
            'DeviceID_HOBOlink': polars.Utf8,
            'SensorReadingUTC': polars.Int64,
            'QueryUTC': polars.Int64,
            'Source': polars.Utf8
        }
        
        for dtype in dtypes:
            if dtype in sensors.columns:
                # Only cast non-null values to avoid errors with incompatible data types
                if sensors[dtype].null_count() < sensors.shape[0]:
                    try:
                        sensors = sensors.with_columns(polars.col(dtype).cast(dtypes[dtype]))
                    except Exception as e:
                        self.logger.warning(f"Could not cast column {dtype} to {dtypes[dtype]}: {e}")
        
        for reading in self.acceptable_range:
            if reading in sensors.columns:
                sensors = sensors.with_columns(polars.col(reading).cast(polars.Float32))

        # Validate the data.
        self.validate_sensors(sensors=sensors, historical=historical, utc=utc, step=step)

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
        Validate the sensor data.
        Very important to keep the integrity of the data.
        Will:
        1. Check for nulls where they should not be.
        2. Check that all sensor IDs in this dataset are present in the historical database.
        3. Ensure the timestamp is recent enough (Coris API sometimes returns old data).

        Parameters
        ----------
        sensors : polars.DataFrame
            DataFrame to validate (from API or from the database).

        historical : polars.DataFrame, default=polars.DataFrame()
            DataFrame containing historical data. The validation will check if the sensors in the new data are present in the historical data.
            If not provided, that check will not be performed.
            
        utc : int, default=None
            Current UTC timestamp. Used to check that the sensor data is reasonably recent. 
            If not provided, that check will not be performed.

        step : str, default=''
            Which processing step this validation is part of. Used for error messages.
            
        Raises
        ------
        Exception
            If validation fails.
        """

        # Helper function to log and raise an error at the same time.
        def validation_error(msg, raise_exception = True):
            error_msg = f'Validation error in {step}: {msg}'
            self.logger_err.error(error_msg)
            self.logger.error(error_msg)
            if raise_exception:
                raise Exception(error_msg)
            else:
                warnings.warn(error_msg)

        # Check for duplicate indices.
        if 'SensorID_Coris' in sensors.columns and any(sensors.groupby('SensorID_Coris').agg(polars.count())[0].to_list() > 1):
            duplicated = sensors.groupby('SensorID_Coris').agg(polars.count()).filter(polars.col('count') > 1)
            validation_error(f'duplicated indices: {duplicated}')

        # Check for missing values in required fields.
        required_fields = ['SensorName', 'DeviceName']
        
        # For CORIS data, SensorID_Coris and DeviceID_Coris are required
        coris_data = sensors.filter(
            (polars.col('Source') == 'CORIS') | 
            ((polars.col('Source').is_null()) & (polars.col('SensorID_Coris').is_not_null()))
        )
        if not coris_data.is_empty():
            for field in required_fields + ['SensorID_Coris', 'DeviceID_Coris']:
                if field in coris_data.columns and coris_data[field].null_count() > 0:
                    validation_error(f'CORIS data is missing values in required field: {field}')
        
        # For HOBOlink data, SensorID_HOBOlink and DeviceID_HOBOlink are required
        hobolink_data = sensors.filter(
            (polars.col('Source') == 'HOBOlink') & (polars.col('SensorID_HOBOlink').is_not_null())
        )
        if not hobolink_data.is_empty():
            for field in required_fields + ['SensorID_HOBOlink', 'DeviceID_HOBOlink']:
                if field in hobolink_data.columns and hobolink_data[field].null_count() > 0:
                    validation_error(f'HOBOlink data is missing values in required field: {field}')

        # If historical data was provided, check that all sensor IDs in the current query are present in the historical data.
        # This check may not apply during initialization.
        if not historical.is_empty() and step != 'initialize_database':
            # Check CORIS sensors
            if 'SensorID_Coris' in sensors.columns and 'SensorID_Coris' in historical.columns:
                if not all(x in historical['SensorID_Coris'].unique() for x in sensors['SensorID_Coris'].unique() if x is not None):
                    validation_error(f'new sensor ids not in historical data: {set(sensors["SensorID_Coris"].unique()) - set(historical["SensorID_Coris"].unique())}', False)
                    
            # Check HOBOlink sensors
            if 'SensorID_HOBOlink' in sensors.columns and 'SensorID_HOBOlink' in historical.columns:
                hobolink_sensors = [x for x in sensors['SensorID_HOBOlink'].unique() if x is not None]
                if hobolink_sensors and not all(x in historical['SensorID_HOBOlink'].unique() for x in hobolink_sensors):
                    validation_error(f'new HOBOlink sensor ids not in historical data: {set(hobolink_sensors) - set(historical["SensorID_HOBOlink"].unique())}', False)

        # Check the timestamp is within 24 hours of the current time.
        if utc is not None and 'SensorReadingUTC' in sensors.columns:
            max_age = 24 * 60 * 60  # 24 hours in seconds
            if any(sensors['SensorReadingUTC'] < utc - max_age):
                # This produces a lot of false positives, leaving here as a warning for now. Can be upgraded to an error if needed.
                validation_error(f'timestamps too old: {sensors.filter(sensors["SensorReadingUTC"] < utc - max_age)["SensorReadingUTC"].unique()}', False)

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
            
