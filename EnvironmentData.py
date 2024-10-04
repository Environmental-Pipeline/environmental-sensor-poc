import os, requests, polars, time
from tqdm import tqdm

class EnvironmentData():

    def __init__(self, CatsUserID, data_path = 'data/', days_back = 90, testing = False):

        if not os.path.exists(data_path):
            print('Create data folder.')
            os.makedirs(data_path)
        
        self.apikeys = {'CORIS': os.environ.get('CORIS_API_KEY')}
        self.CatsUserID = CatsUserID
        self.data_path = data_path

        self.initialize_database(days_back = days_back, testing = testing)

    def get_sensor_ids(self, testing = False):

        url = f'https://cats.corismonitoring.com/api/cats/user/?ApiKey={self.apikeys["CORIS"]}&CatsUserID={self.CatsUserID}'
        response = requests.get(url)
        current_status = polars.DataFrame(response.json()['Sensors'])
        sensor_ids = {
            'Temperature': current_status.filter(polars.col('SensorType') == 'Temperature')['SensorID'].unique(),
            'Humidity': current_status.filter(polars.col('SensorType') =='Humidity')['SensorID'].unique()
        }

        if testing:
            for key in sensor_ids:        
                    sensor_ids[key] = sensor_ids[key][0:10]

        return sensor_ids


    def get_current_status(self):
        
        url = f'https://cats.corismonitoring.com/api/cats/user/?ApiKey={self.apikeys["CORIS"]}&CatsUserID={self.CatsUserID}'
        response = requests.get(url)
        current_status = polars.DataFrame(response.json()['Sensors'])
        return current_status

    def initialize_database(self, days_back, testing = False):

        sensor_ids = []
        current_utc = int(time.time())
        start_utc = current_utc - days_back * 24 * 60 * 60

        # get historical readings.
        readings = {
            'Temperature': {'ReadingType': 'SensorReadingF', 'data': []}, 
            'Humidity':{'ReadingType': 'SensorReadingRh', 'data': []}
        }
        for key in readings:

            # if the data already exists, skip this reading type.
            readings[key]['datafile'] = f'{self.data_path}/{key}{"-testing" if testing else ""}.parquet'
            if os.path.exists(readings[key]['datafile']):
                continue

            # get sensor ids here to prevent doing it if we don't actually need any new data. 
            if len(sensor_ids) == 0:
                print('Get sensor Ids.')
                sensor_ids = self.get_sensor_ids(testing = testing)

            pbar = tqdm(total = len(sensor_ids[key]), desc=f'Gather readings for {key}')
            for sensor_id in sensor_ids[key]:
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

        