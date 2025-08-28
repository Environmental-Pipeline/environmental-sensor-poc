import os
import requests
import polars
import numpy
import tqdm
import datetime
import pandas as pd

apikey = os.environ.get('CORIS_API_KEY')
CatsUserID = 2496
days_back = int(365 * 2)
testing = True
data_path = 'data/'

acceptable_range = {'SensorReadingF': [], 'SensorReadingRh': []}

# get current data, which includes all the sensors. 
url = f'https://cats.corismonitoring.com/api/cats/user/?ApiKey={apikey}&CatsUserID={CatsUserID}'
response = requests.get(url)
sensors = polars.DataFrame(response.json()['Sensors'])
sensors.columns
sensors.shape

# drop out of scope. 
sensors = sensors.filter(polars.col('SensorName').str.starts_with('-80').not_())
sensors = sensors.filter(polars.col('SensorName').str.contains('Cryo tank').not_())
sensors = sensors.filter(polars.col('SensorName').str.starts_with('Water').not_())

# explore differences in timestamps.
diffs = pd.DataFrame({
    'sensor': sensors['SensorName'],
    'id': sensors['SensorID'],
    'type': ['SensorReadingF' if x == 'Temperature' else 'SensorReadingRh' for x in sensors['SensorType']],
    'reading': sensors['SensorReading'],
    'utc': sensors['SensorReadingUTC'],
    'time_diff_imins': numpy.round(
        (sensors['SensorReadingUTC'].to_numpy() - int(datetime.datetime.now(datetime.timezone.utc).timestamp())) / 60, 
        0
    )
}).sort_values('time_diff_imins')
diffs.to_csv('out/timedeltas.csv')

# see what historical looks like for problematic sensors. 
current_utc = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
start_utc = current_utc - days_back * 24 * 60 * 60
url = '&'.join([
    f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={apikey}',
    f'SensorID={diffs.id.values[0]}',
    f'ReadingType={diffs.type.values[0]}',
    f'StartUTC={start_utc}',
    'MinReadingSpacing=600', # every 10 minutes.
    'RequestedOutputFormat=raw'
])
response = requests.get(url)

# Check for errors.
if not response.ok:
    raise Exception(f'Error getting historical: {response.json()}')
    
hist = polars.read_csv(response.content, has_header = False)
hist.columns = ['SensorReadingUTC', diffs.type.values[0]]

# is UTC unique?
if (len(hist['SensorReadingUTC'].unique()) - hist.shape[0]) != 0:
    raise Exception('Duplicated UTC in history.')

# is the current UTC found in the historical data?
if diffs.utc[0] in hist['SensorReadingUTC']:
    raise Exception('Current reading is duplicated in historical.')

# print(hist.drop_nulls())
# hist.shape
# hist.drop_nulls().shape

def match_types(data: polars.DataFrame, match: polars.DataFrame) -> polars.DataFrame:
    for col in match.columns:
        if col in data.columns and data[col].dtype != match[col].dtype:
            data = data.with_columns(data[col].cast(match[col].dtype))    
    return data

# initialize database.
sensor_ids = []
current_utc = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
start_utc = current_utc - days_back * 24 * 60 * 60

# Use the API to get historical data for each sensor type.
readings = []
for reading in acceptable_range:

    sensor_ids = sensors.filter(polars.col(reading).is_nan().not_())['SensorID'].unique().to_list()
    if testing:           
        sensor_ids = sensor_ids[0:3]

    # Query the API for each sensor and save the data as a polars DataFrame with optimal data types.
    pbar = tqdm.tqdm(total = len(sensor_ids), desc=f'Gather readings: {reading}')
    for sensor_id in sensor_ids:

        url = '&'.join([
            f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={apikey}',
            f'SensorID={sensor_id}',
            f'ReadingType={reading}',
            f'StartUTC={start_utc}',
            f'EndUTC={current_utc}',
            'MinReadingSpacing=600', # every 10 minutes.
            'RequestedOutputFormat=raw'
        ])

        # create a duplicate of the url for logging purposes, which doesn't include sensitive information.
        logurl = '&'.join([
            'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey=XXXX',
            f'SensorID={sensor_id}',
            f'ReadingType={reading}',
            f'StartUTC={start_utc}',
            f'EndUTC={current_utc}',
            'MinReadingSpacing=600', # every 10 minutes.
            'RequestedOutputFormat=raw'
        ])
        response = requests.get(url)

        # Check for errors.
        if not response.ok:
            raise Exception(f'Error getting historical {reading} for {sensor_id}: {response.json()}')
        
        # Data comes in as comma separated values with no header. Convert this to a polars DataFrame.
        data = polars.read_csv(response.content, has_header = False)
        data.columns = ['SensorReadingUTC', reading]

        # Add data from the sensors dataset. 
        data = data.with_columns(polars.lit(sensor_id).alias('SensorID'))
        sensor_data = sensors.filter(polars.col('SensorID') == sensor_id)
        for col in ['SensorName', 'DeviceName', 'DeviceDevID', 'SensorType']:
            data = data.with_columns(polars.lit(sensor_data[col].to_list()[0]).alias(col))

        # Set data types. 
        data = data.with_columns(polars.col('SensorID').cast(polars.Int32))
        data = data.with_columns(polars.col('SensorReadingUTC').cast(polars.Int64))
        for reading in acceptable_range:
            if reading in sensors.columns:
                sensors = sensors.with_columns(polars.col(reading).cast(polars.Float32))

        # Rearrange columns.
        col_order = ['DeviceDevID', 'DeviceName', 'SensorID', 'SensorReadingUTC'] + list(acceptable_range.keys())
        col_order = [x for x in col_order if x in data.columns]
        data = data.select(col_order + [x for x in data.columns if x not in col_order])
        
        # Append the data to the list of DataFrame for this sensor type.
        readings.append(data)

        # Match data types to facilitate combining the DataFrames later.
        #readings[-1] = match_types(readings[-1], readings[0])

        pbar.update(1)

    pbar.close()

# Combine the readings into a single polars DataFrame.
dt = polars.concat(readings, how = 'diagonal')

# calculate difference between readings. 
dt = dt.sort(['SensorID', 'SensorReadingUTC'])

dt.group_by('SensorID').map_groups(
    lambda x: x.with_columns((
        polars.col('SensorReadingUTC') - polars.col('SensorReadingUTC').shift(1)
    ).alias('SensorReadingUTC_SecondsFromPrior')
)).filter(polars.col('SensorID') == 21378)

dt.group_by('SensorID').with_columns((
    polars.col('SensorReadingUTC') - polars.col('SensorReadingUTC').shift(1)
).alias('SensorReadingUTC_SecondsFromPrior'))
