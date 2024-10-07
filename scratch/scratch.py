import os, requests, polars, time, numpy
from tqdm import tqdm

apikey = os.environ.get('CORIS_API_KEY')
CatsUserID = 2496
test = True
data_path = 'data/'

readings = {
    'Temperature': {'ReadingType': 'SensorReadingF', 'data': []}, 
    'Humidity':{'ReadingType': 'SensorReadingRh', 'data': []}
}
current_utc = int(time.time())

# get current data, which includes all the sensors. 
url = f'https://cats.corismonitoring.com/api/cats/user/?ApiKey={apikey}&CatsUserID={CatsUserID}'
response = requests.get(url)
current_status = polars.DataFrame(response.json()['Sensors'])

# get sensor ids. 
for key in readings:
    readings[key]['sensor_ids'] = current_status.filter(polars.col('SensorType') == key)['SensorID'].unique().to_list()
    if test:            
        readings[key]['sensor_ids'] = readings[key]['sensor_ids'][0:10]

# current status to the format that historical data is in.
        
# extract data from the repsonse in the format to match the database. database format is:
#  - one file per reading type at self.data_path/readingtype.parquet
#  - columns: UTC, Reading, SensorID

# polars iterator uses tuples so we need numeric indices of columns.
def val(row, x): 
    if x not in current_status.columns:
        raise ValueError(f'Column {x} not in response columns.')
    colidx = list(numpy.where(numpy.array(current_status.columns) == x)[0])
    return row[ colidx[0] ]

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
        os.makedirs(f'{data_path}/new-readings/', exist_ok = True)
        filename = f'{data_path}/new-readings/{key}-{current_utc}.parquet'
        polars.DataFrame(readings[key]['data']).write_parquet(filename)
        
        
        

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