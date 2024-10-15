# use EnvironmentData to initialize the database.
from EnvironmentData import EnvironmentData
envdt = EnvironmentData(CatsUserID = 2496, out_of_scope = ['-80', 'Cryo tank', 'Water'], testing = True) # this will initialize the database.

# Detailed info is saved in the log.
# with open('data/EnvironmentData.log', 'r') as file:
#     print(file.read())
    
# Data is now in the database.
import polars
sensor_readings = polars.read_parquet('data/sensor_readings.parquet')
#print(sensors)

# Get current readings. 
envdt.get_current_readings()

# Data is read into new-readings folder for consolidation at the end of the day.
import os
os.listdir('data/new-readings')

# To consolidate these into the database, run consolidate_readings.
envdt.consolidate_readings()
#print(os.listdir('data/new-readings'))

# We now also have the devices table. 
device_readings = polars.read_parquet('data/device_readings.parquet').filter(polars.col('DeviceName').is_null().not_())
#print(devices)

sensors = polars.read_parquet('data/sensors.parquet')
devices = polars.read_parquet('data/devices.parquet')
utc_lookup = polars.read_parquet('data/utc_lookup.parquet')

envdt.close()

sensors
devices
sensor_readings
device_readings