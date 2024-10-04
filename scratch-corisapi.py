from EnvironmentData import EnvironmentData
import polars, os

api = EnvironmentData(CatsUserID = 2496)

# read data. 
temperature = polars.read_parquet('data/Temperature.parquet')
print(temperature.shape)

humidity = polars.read_parquet('data/Humidity.parquet')
print(humidity.shape)

# get current status.
api.get_current_status()

# note how new reading files exist. 
print(os.listdir('data/new-readings'))

# verify a rows were added. 
temperature = polars.read_parquet('data/Temperature.parquet')
print(temperature.shape)

humidity = polars.read_parquet('data/Humidity.parquet')
print(humidity.shape)

