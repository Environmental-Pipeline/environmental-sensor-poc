import os, requests, polars, time
from tqdm import tqdm

apikey = os.environ.get('CORIS_API_KEY')
CatsUserID = 2496
test = True

# get current data, which includes all the sensors. 
url = f'https://cats.corismonitoring.com/api/cats/user/?ApiKey={apikey}&CatsUserID={CatsUserID}'
response = requests.get(url)
current_status = polars.DataFrame(response.json()['Sensors'])
sensor_ids = {
    'Temperature': current_status.filter(polars.col('SensorType') == 'Temperature')['SensorID'].unique(),
    'Humidity': current_status.filter(polars.col('SensorType') =='Humidity')['SensorID'].unique()
}

if test:
   for key in sensor_ids:        
        sensor_ids[key] = sensor_ids[key][0:10]

# get historical readings.

current_utc = int(time.time())
days_back = 90
start_utc = current_utc - days_back * 24 * 60 * 60

pbar = tqdm(total = len(sensor_ids['Temperature']) + len(sensor_ids['Humidity']), desc="Gather historical readings")
readings = {'Temperature': [], 'Humidity': []}
for sensor_id in sensor_ids['Temperature']:
    url = '&'.join([
        f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={apikey}',
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

for sensor_id in sensor_ids['Humidity']:
    url = '&'.join([
        f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={apikey}',
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

# append 