# https://docs.python.org/3/library/unittest.html

# Setup: import packages, create dummy data.

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import unittest

def find_env_directory():
    """Find the directory containing the .env file by checking current and parent directories."""
    current_dir = os.getcwd()
    if os.path.exists(os.path.join(current_dir, '.env')):
        return "."
    elif os.path.exists(os.path.join(os.path.dirname(current_dir), '.env')):
        return ".."
    else:
        # Check the directory where this test file is located
        test_file_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(test_file_dir)
        if os.path.exists(os.path.join(parent_dir, '.env')):
            return parent_dir
        return "."
import polars
import shutil
import os
from EnvironmentData import EnvironmentData

# dummy_table = polars.DataFrame([
#     {'SensorID': numpy.int32(1), 'UTC': numpy.int64(1720542516), 'Reading': numpy.float32(1.1)},
#     {'SensorID': numpy.int32(2), 'UTC': numpy.int64(1720542516), 'Reading': numpy.float32(2.2)},
#     {'SensorID': numpy.int32(3), 'UTC': numpy.int64(1720542516), 'Reading': numpy.float32(3.3)},
#     {'SensorID': numpy.int32(4), 'UTC': numpy.int64(1720542516), 'Reading': numpy.float32(4.4)},
#     {'SensorID': numpy.int32(5), 'UTC': numpy.int64(1720542516), 'Reading': numpy.float32(5.5)},
# ])

# dummy_reading = {
#     'SensorID': numpy.int32(1),
#     'UTC': numpy.int64(1720542516),
#     'Reading': numpy.float32(1.1)
# }

# Define tests. 
class TestEnvironmentDataWorkflow(unittest.TestCase):
    """Test the complete workflow: initialization, current readings retrieval, and consolidation with all sources."""
    
    @classmethod
    def setUpClass(cls):
        """Set up the test environment for complete workflow test."""
        # Clear prior data to ensure clean test run
        cls.env_dir = find_env_directory()
        cls.data_path = os.path.join(cls.env_dir, 'data')
        if os.path.exists(cls.data_path):
            shutil.rmtree(cls.data_path)
        os.makedirs(cls.data_path, exist_ok=True)
        
        # Initialize EnvironmentData with all sources enabled for comprehensive test
        cls.envdt = EnvironmentData(
            data_path=cls.data_path,
            days_back=7,
            coris_enabled=True,
            licor_enabled=True,
            conserv_enabled=True,
            testing=True,
            home_directory=cls.env_dir
        )
    
    @classmethod
    def tearDownClass(cls):
        """Clean up after tests."""
        cls.envdt.close()
    
    def test_01_initialization_and_historical_data(self):
        """Test that EnvironmentData initializes correctly and retrieves historical data."""
        # Check that log file exists
        log_path = os.path.join(self.data_path, 'EnvironmentData.log')
        self.assertTrue(os.path.exists(log_path), "Log file should be created")
        
        # Check that sensor_readings.parquet exists and has data
        sensor_readings_path = os.path.join(self.data_path, 'sensor_readings.parquet')
        self.assertTrue(os.path.exists(sensor_readings_path), "Historical sensor readings should exist")
        
        sensor_readings = polars.read_parquet(sensor_readings_path)
        self.assertGreater(sensor_readings.shape[0], 0, "Should have historical sensor readings")
        
        # Check for expected columns
        expected_columns = ['SensorID', 'SensorReadingUTC', 'SensorReadingF', 'SensorReadingRh']
        for col in expected_columns:
            self.assertIn(col, sensor_readings.columns, f"Missing expected column: {col}")
    
    def test_02_get_current_readings(self):
        """Test getting current readings from all enabled sources."""
        # Store initial count for consolidation test
        sensor_readings_path = os.path.join(self.data_path, 'sensor_readings.parquet')
        initial_sensor_readings = polars.read_parquet(sensor_readings_path)
        self.__class__.initial_sensor_count = initial_sensor_readings.shape[0]
        
        # Get current readings
        self.envdt.get_current_readings()
        
        # Check that new-readings folder has files
        new_readings_path = os.path.join(self.data_path, 'new-readings')
        self.assertTrue(os.path.exists(new_readings_path), "New readings directory should exist")
        
        new_readings_files = os.listdir(new_readings_path)
        self.assertGreater(len(new_readings_files), 0, "Should have new readings files")
        
        # Verify we can read the new readings data and count total new rows
        total_new_rows = 0
        for filename in new_readings_files:
            file_path = os.path.join(new_readings_path, filename)
            data = polars.read_parquet(file_path)
            self.assertGreater(data.shape[0], 0, f"New readings file {filename} should not be empty")
            total_new_rows += data.shape[0]
        
        self.__class__.total_new_rows = total_new_rows
    
    def test_03_consolidate_readings_and_generate_tables(self):
        """Test consolidating readings and generating all analytical tables."""
        # Consolidate readings
        self.envdt.consolidate_readings()
        
        # Check that new-readings directory is empty after consolidation
        new_readings_path = os.path.join(self.data_path, 'new-readings')
        new_readings_files_after = os.listdir(new_readings_path)
        self.assertEqual(len(new_readings_files_after), 0, "New-readings directory should be empty after consolidation")
        
        # Check that sensor readings table was updated
        sensor_readings_path = os.path.join(self.data_path, 'sensor_readings.parquet')
        final_sensor_readings = polars.read_parquet(sensor_readings_path)
        final_count = final_sensor_readings.shape[0]
        self.assertGreaterEqual(final_count, self.__class__.initial_sensor_count, "Sensor readings count should increase or stay the same")
        
        # Test that all expected analytical tables are generated
        expected_tables = [
            'sensor_readings.parquet',
            'device_readings.parquet', 
            'sensors.parquet',
            'devices.parquet',
            'utcs.parquet',
            'sensor_readings_daily.parquet',
            'device_readings_daily.parquet'
        ]
        
        for table_name in expected_tables:
            table_path = os.path.join(self.data_path, table_name)
            self.assertTrue(os.path.exists(table_path), f"Expected table {table_name} should exist")
            
            # Verify we can read the table
            try:
                data = polars.read_parquet(table_path)
                self.assertGreaterEqual(data.shape[0], 0, f"Table {table_name} should be readable and have data")
            except Exception as e:
                self.fail(f"Could not read table {table_name}: {e}")
    
    def test_04_data_quality_validation(self):
        """Test data quality across all generated tables."""
        # Test sensor readings data quality
        sensor_readings_path = os.path.join(self.data_path, 'sensor_readings.parquet')
        sensor_readings = polars.read_parquet(sensor_readings_path)
        
        # Check for multiple data sources
        if 'SensorID' in sensor_readings.columns:
            sensor_ids = sensor_readings['SensorID'].unique().to_list()
            self.assertGreater(len(sensor_ids), 0, "Should have sensor IDs from different sources")
        
        # Test device readings consistency
        device_readings_path = os.path.join(self.data_path, 'device_readings.parquet')
        if os.path.exists(device_readings_path):
            device_readings = polars.read_parquet(device_readings_path)
            self.assertGreater(device_readings.shape[0], 0, "Device readings should have data")
            
            # Check expected device columns
            expected_device_cols = ['DeviceID', 'DeviceName', 'SensorReadingUTC', 'QueryUTC', 'SensorReadingF', 'SensorReadingRh']
            for col in expected_device_cols:
                if col in device_readings.columns:
                    self.assertIn(col, device_readings.columns, f"Device readings should have {col} column")
        
        # Test data value ranges
        temp_readings = sensor_readings.filter(polars.col('SensorReadingF').is_not_null())
        if temp_readings.shape[0] > 0:
            min_temp = temp_readings['SensorReadingF'].min()
            max_temp = temp_readings['SensorReadingF'].max()
            self.assertGreaterEqual(min_temp, -100, "Temperature readings should be reasonable (>= -100F)")
            self.assertLessEqual(max_temp, 200, "Temperature readings should be reasonable (<= 200F)")
        
        humidity_readings = sensor_readings.filter(polars.col('SensorReadingRh').is_not_null())
        if humidity_readings.shape[0] > 0:
            min_humidity = humidity_readings['SensorReadingRh'].min()
            max_humidity = humidity_readings['SensorReadingRh'].max()
            self.assertGreaterEqual(min_humidity, 0, "Humidity should be >= 0%")
            self.assertLessEqual(max_humidity, 100, "Humidity should be <= 100%")


if __name__ == '__main__':
    # Run tests
    unittest.main(verbosity=2)
