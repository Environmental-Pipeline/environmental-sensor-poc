import os, requests, polars, time
from tqdm import tqdm

class EnvironmentData():

    def __init__(self, CatsUserID):

        self.apikeys = {'CORIS': os.environ.get('CORIS_API_KEY')}
        self.CatsUserID = CatsUserID

    def get_current_status(self):

        url = f'https://cats.corismonitoring.com/api/cats/user/?ApiKey={self.apikeys['CORIS']}&CatsUserID={self.CatsUserID}'
        response = requests.get(url)
        self.current_status = polars.DataFrame(response.json()['Sensors'])
        self.sensor_ids = {
            'Temperature': self.current_status.filter(polars.col('SensorType') == 'Temperature')['SensorID'].unique(),
            'Humidity': self.current_status.filter(polars.col('SensorType') =='Humidity')['SensorID'].unique()
        }
        return self.current_status

    def get_historical_status(self, days_back, testing = False):

        if testing:
            for key in self.sensor_ids:        
                    self.sensor_ids[key] = self.sensor_ids[key][0:10]

        current_utc = int(time.time())
        start_utc = current_utc - days_back * 24 * 60 * 60

        pbar = tqdm(total = len(self.sensor_ids['Temperature']) + len(self.sensor_ids['Humidity']), desc="Gather historical readings")
        readings = {'Temperature': [], 'Humidity': []}
        for sensor_id in self.sensor_ids['Temperature']:
            url = '&'.join([
                f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={self.apikeys['CORIS']}',
                f'SensorID={sensor_id}',
                f'ReadingType=SensorReadingF',
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
            readings['Temperature'].append(data.drop_nulls())
            pbar.update(1)

        for sensor_id in self.sensor_ids['Humidity']:
            url = '&'.join([
                f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={self.apikeys['CORIS']}',
                f'SensorID={sensor_id}',
                f'ReadingType=SensorReadingRh',
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
            readings['Humidity'].append(data.drop_nulls())    
            pbar.update(1)

        pbar.close()

        for key in readings:
            readings[key] = polars.concat(readings[key])

        self.historical_readings = readings
        return self.historical_readings

        