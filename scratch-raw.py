import os, requests, polars, time, numpy, tqdm

apikey = os.environ.get('CORIS_API_KEY')
days_back = 90
CatsUserID = 2496
testing = False
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

def match_types(data: polars.DataFrame, match: polars.DataFrame) -> polars.DataFrame:
    for col in match.columns:
        if col in data.columns and data[col].dtype != match[col].dtype:
            data = data.with_columns(data[col].cast(match[col].dtype))    
    return data

# initialize database.
sensor_ids = []
current_utc = int(time.time())
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
        response = requests.get(url)

        # Check for errors.
        if not response.ok:
            raise Exception(f'Error getting historical {reading} for {sensor_id}: {response.json()}')
        
        # Data comes in as comma separated values with no header. Convert this to a polars DataFrame.
        data = polars.read_csv(response.content, has_header = False)
        data.columns = ['SensorReadingUTC', reading]

        # Add data from the sensors dataset. 
        data = data.with_columns(polars.lit(sensor_id).alias('SensorID'))
        sensordt = sensors.filter(polars.col('SensorID') == sensor_id)
        for col in ['SensorName', 'DeviceName', 'DeviceDevID', 'SensorType']:
            data = data.with_columns(polars.lit(sensordt[col]).alias('SensorID'))

        # Set data types. 
        data = data.with_columns(polars.col('SensorID').cast(polars.Int32))
        data = data.with_columns(polars.col('SensorReadingUTC').cast(polars.Int64))
        data = data.with_columns(polars.col(reading).cast(polars.Float32))

        # Rearrange columns.
        data = data[['SensorID', 'SensorReadingUTC', reading]]
        
        # Append the data to the list of DataFrame for this sensor type.
        readings.append(data)

        # Match data types to facilitate combining the DataFrames later.
        #readings[-1] = match_types(readings[-1], readings[0])

        pbar.update(1)

    pbar.close()

# Combine the readings into a single polars DataFrame.
dt = polars.concat(readings, how = 'diagonal')














# get sensor ids. 
# for key in readings:
#     readings[key]['sensor_ids'] = current_status.filter(polars.col('SensorType') == key)['SensorID'].unique().to_list()
#     if test:            
#         readings[key]['sensor_ids'] = readings[key]['sensor_ids'][0:10]

# # current status to the format that historical data is in.
        
# # extract data from the repsonse in the format to match the database. database format is:
# #  - one file per reading type at self.data_path/readingtype.parquet
# #  - columns: UTC, Reading, SensorID

# # polars iterator uses tuples so we need numeric indices of columns.
# def val(row, x): 
#     if x not in current_status.columns:
#         raise ValueError(f'Column {x} not in response columns.')
#     colidx = list(numpy.where(numpy.array(current_status.columns) == x)[0])
#     return row[ colidx[0] ]

# for row in current_status.iter_rows():
#     sensortype = val(row, 'SensorType')
#     if sensortype in readings:
#         readings[ val(row, 'SensorType') ]['data'].append({
#             'UTC': val(row, 'SensorReadingUTC'), 
#             'Reading': val(row, 'SensorReading'), 
#             'SensorID': val(row, 'SensorID')
#         })

# # concatenate data and save it.
# for key in readings:
#     if len(readings[key]['data']) > 0:                
#         os.makedirs(f'{data_path}/new-readings/', exist_ok = True)
#         filename = f'{data_path}/new-readings/{key}-{current_utc}.parquet'
#         readings[key]['data'] = polars.DataFrame(readings[key]['data'])
#         readings[key]['data'].write_parquet(filename)
        
        
        

# # get historical readings.

# current_utc = int(time.time())
# days_back = 90
# start_utc = current_utc - days_back * 24 * 60 * 60

# pbar = tqdm(total = len(sensor_ids['Temperature']) + len(sensor_ids['Humidity']), desc="Gather historical readings")
# readings = {'Temperature': [], 'Humidity': []}
# for sensor_id in sensor_ids['Temperature']:
#     url = '&'.join([
#         f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={apikey}',
#         f'SensorID={sensor_id}',
#         f'ReadingType=SensorReadingF',
#         f'StartUTC={start_utc}',
#         f'EndUTC={current_utc}',
#         f'MinReadingSpacing=600', # every 10 minutes.
#         f'RequestedOutputFormat=raw'
#     ])
#     response = requests.get(url)
#     if not response.ok:
#         raise Exception(response.json())
#     data = polars.read_csv(response.content, has_header = False)
#     data.columns = ['UTC', 'Reading']
#     data = data.with_columns(polars.lit(sensor_id).alias('SensorID'))
#     data = data.with_columns(polars.col("Reading").cast(polars.Float32))
#     readings['Temperature'].append(data.drop_nulls())
#     pbar.update(1)

# for sensor_id in sensor_ids['Humidity']:
#     url = '&'.join([
#         f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={apikey}',
#         f'SensorID={sensor_id}',
#         f'ReadingType=SensorReadingRh',
#         f'StartUTC={start_utc}',
#         f'EndUTC={current_utc}',
#         f'MinReadingSpacing=600', # every 10 minutes.
#         f'RequestedOutputFormat=raw'
#     ])
#     response = requests.get(url)
#     if not response.ok:
#         raise Exception(response.json())
#     data = polars.read_csv(response.content, has_header = False)
#     data.columns = ['UTC', 'Reading']
#     data = data.with_columns(polars.lit(sensor_id).alias('SensorID'))
#     data = data.with_columns(polars.col("Reading").cast(polars.Float32))
#     readings['Humidity'].append(data.drop_nulls())    
#     pbar.update(1)

# pbar.close()

# for key in readings:             
#     readings[key] = polars.concat(readings[key])

# # append 