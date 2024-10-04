import os, requests, polars, time, duckdb, numpy, tqdm, copy

class EnvironmentData():

    def __init__(self, CatsUserID, data_path = 'data/', days_back = 90, testing = False):

        if not os.path.exists(data_path):
            print('Create data folder.')
            os.makedirs(data_path)
        
        self.apikeys = {'CORIS': os.environ.get('CORIS_API_KEY')}
        self.CatsUserID = CatsUserID
        self.data_path = data_path
        self.testing = testing

        # set up the readings data structure that will be used throughout.
        self.readings = {
            'Temperature': {'ReadingType': 'SensorReadingF', 'data': []}, 
            'Humidity':{'ReadingType': 'SensorReadingRh', 'data': []}
        }
        for key in self.readings:
            self.readings[key]['datafile'] = f'{data_path}/{key}{"-testing" if testing else ""}.parquet'

        self.initialize_database(days_back = days_back)

    def get_sensor_ids(self):

        url = f'https://cats.corismonitoring.com/api/cats/user/?ApiKey={self.apikeys["CORIS"]}&CatsUserID={self.CatsUserID}'
        response = requests.get(url)
        current_status = polars.DataFrame(response.json()['Sensors'])
        sensor_ids = copy.deepcopy(self.readings)
        for key in sensor_ids:
            sensor_ids[key]['sensor_ids'] = current_status.filter(polars.col('SensorType') == key)['SensorID'].unique().to_list()
            if self.testing:            
                sensor_ids[key]['sensor_ids'] = sensor_ids[key]['sensor_ids'][0:3]

        return sensor_ids

    def get_current_status(self):
        
        current_utc = int(time.time())
        url = f'https://cats.corismonitoring.com/api/cats/user/?ApiKey={self.apikeys["CORIS"]}&CatsUserID={self.CatsUserID}'
        response = requests.get(url)
        current_status = polars.DataFrame(response.json()['Sensors'])
        
        # extract data from the repsonse in the format to match the database. database format is:
        #  - one file per reading type at self.data_path/readingtype.parquet
        #  - columns: UTC, Reading, SensorID

        # polars iterator uses tuples so we need numeric indices of columns.
        def val(row, x): 
            if x not in current_status.columns:
                raise ValueError(f'Column {x} not in response columns.')
            colidx = list(numpy.where(numpy.array(current_status.columns) == x)[0])
            return row[ colidx[0] ]
        
        readings = copy.deepcopy(self.readings)
        for row in current_status.iter_rows():
            sensortype = val(row, 'SensorType')
            if sensortype in readings:
                readings[ val(row, 'SensorType') ]['data'].append({
                    'UTC': val(row, 'SensorReadingUTC'), 
                    'Reading': val(row, 'SensorReading'), 
                    'SensorID': val(row, 'SensorID')
                })

        # concatenate data and save it.
        for key in readings:
            if len(readings[key]['data']) > 0:                
                os.makedirs(f'{self.data_path}/new-readings/', exist_ok = True)
                filename = f'{self.data_path}/new-readings/{key}-{current_utc}.parquet'
                polars.DataFrame(readings[key]['data']).write_parquet(filename)

    def initialize_database(self, days_back):

        sensor_ids = []
        current_utc = int(time.time())
        start_utc = current_utc - days_back * 24 * 60 * 60

        # get historical readings.
        readings = copy.deepcopy(self.readings)
        for key in readings:

            # if the data already exists, skip this reading type.
            if os.path.exists(readings[key]['datafile']):
                continue

            # get sensor ids here to prevent doing it if we don't actually need any new data. 
            if len(sensor_ids) == 0:
                print('Get sensor Ids.')
                sensor_ids = self.get_sensor_ids()

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
                response = requests.get(url)
                if not response.ok:
                    raise Exception(response.json())
                data = polars.read_csv(response.content, has_header = False)
                data.columns = ['UTC', 'Reading']
                data = data.with_columns(polars.lit(sensor_id).alias('SensorID'))
                data = data.with_columns(polars.col("Reading").cast(polars.Float32))
                readings[key]['data'].append(data.drop_nulls())
                pbar.update(1)

            pbar.close()

        # combine readings from multiple sensors into one dataframe.
        # initialize database.
        for key in readings:
            if len(readings[key]['data']) > 0:
                print(f'Initialize database for {key}.')
                polars.concat(readings[key]['data']).write_parquet(readings[key]['datafile'])

    def consolidate_readings(self):
        
        # bring new-readings into the database.
        new_readings = copy.deepcopy(self.readings)
        for key in new_readings:

            # get new readings. 
            files = [i for i in os.listdir(f'{self.data_path}/new-readings/') if i.startswith(f'{key}-')]
            for file in files:
                new_readings[key]['data'].append(polars.read_parquet(f'{self.data_path}/new-readings/{file}'))

            # combine new readings and set correct data types. 
            dt = polars.concat(new_readings[key]['data']).with_columns(polars.col("Reading").cast(polars.Float32))
            dt = dt.with_columns(polars.col("UTC").cast(polars.Int64))
            dt = dt.with_columns(polars.col("SensorID").cast(polars.Int32))

            # append these to the database.
            dt = polars.concat([polars.read_parquet(new_readings[key]['datafile']), dt])
            dt.write_parquet(new_readings[key]['datafile'])

            # if all this was successful, remove the new-readings files. 
            for file in files:
                os.remove(f'{self.data_path}/new-readings/{file}')

        os.rmdir(f'{self.data_path}/new-readings')
