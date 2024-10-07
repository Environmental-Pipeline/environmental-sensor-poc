from EnvironmentData import EnvironmentData
import polars, os

api = EnvironmentData(CatsUserID = 2496, testing = True)

# read data. 
temperature = polars.read_parquet('data/Temperature-testing.parquet')
print(temperature.shape)

humidity = polars.read_parquet('data/Humidity-testing.parquet')
print(humidity.shape)

# get current status.
api.get_current_status()

# note how new reading files exist. 
print(os.listdir('data/new-readings'))

# consolidate them. 
api.consolidate_readings()

# verify a rows were added. 
temperature = polars.read_parquet('data/Temperature-testing.parquet')
print(temperature.shape)

humidity = polars.read_parquet('data/Humidity-testing.parquet')
print(humidity.shape)


