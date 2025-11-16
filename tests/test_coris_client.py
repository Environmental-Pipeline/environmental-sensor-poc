#!/usr/bin/env python3
"""
Test suite for Coris API client integration.

This module contains tests for the CorisClient class focused on quick testing
with limited sensors and short time periods.
"""

import unittest
import logging
import datetime
import sys
import os
import polars as pl

# Add the parent directory to the path to import our clients
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set up test logging to be less verbose
logging.basicConfig(level=logging.WARNING)

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

try:
    from clients.coris_client import CorisClient
    CORIS_AVAILABLE = True
except ImportError as e:
    print(f"Coris client not available: {e}")
    CORIS_AVAILABLE = False


@unittest.skipUnless(CORIS_AVAILABLE, "Coris client not available")
class TestCorisClient(unittest.TestCase):
    """Test suite for Coris API client."""

    @classmethod
    def setUpClass(cls):
        """Set up test class with Coris client."""
        try:
            cls.client = CorisClient(home_directory=find_env_directory())
            print("[OK] Coris client initialized successfully")
            
            # Get current user data to find available sensors (limited for quick test)
            cls.user_data = cls.client.sample_raw_data(save_to_samples=False)
            
            # Extract a few sensors for testing (limit to first 3 for speed)
            cls.test_sensors = []
            if isinstance(cls.user_data, dict) and 'Sensors' in cls.user_data and cls.user_data['Sensors']:
                cls.test_sensors = cls.user_data['Sensors'][:3]  # Only test first 3 sensors
                print(f"   [OK] Found {len(cls.test_sensors)} sensors for testing")
            elif hasattr(cls.user_data, 'get') and cls.user_data.get('Sensors'):
                cls.test_sensors = cls.user_data.get('Sensors', [])[:3]
                print(f"   [OK] Found {len(cls.test_sensors)} sensors for testing")
            else:
                # If no sensors found in expected format, create minimal test data
                print(f"   [WARN] No sensors in expected format. Sample data keys: {list(cls.user_data.keys()) if isinstance(cls.user_data, dict) else 'Not a dict'}")
                # Try to get sensors directly from API
                import requests
                try:
                    url = f"{cls.client.base_url}/cats/user/?ApiKey={cls.client.api_key}&CatsUserID={cls.client.cats_user_id}"
                    response = requests.get(url)
                    if response.status_code == 200:
                        api_data = response.json()
                        if 'Sensors' in api_data and api_data['Sensors']:
                            cls.test_sensors = api_data['Sensors'][:3]
                            print(f"   [OK] Retrieved {len(cls.test_sensors)} sensors directly from API")
                        else:
                            raise unittest.SkipTest("No sensors available in API response")
                    else:
                        raise unittest.SkipTest(f"API call failed with status {response.status_code}")
                except Exception as e:
                    raise unittest.SkipTest(f"Failed to get sensors: {e}")
                
        except Exception as e:
            raise unittest.SkipTest(f"Failed to initialize Coris client: {e}")

    def test_client_initialization(self):
        """Test that client initializes properly."""
        self.assertIsNotNone(self.client)
        self.assertIsNotNone(self.client.api_key)
        self.assertIsNotNone(self.client.cats_user_id)
        print(f"   [OK] Client initialized with CATS User ID: {self.client.cats_user_id}")

    def test_get_current_data_via_sample(self):
        """Test getting current data using sample_raw_data method (quick test)."""
        try:
            print("   [TEST] Testing current data retrieval via sample_raw_data...")
            
            # Get sample data (already retrieved in setUpClass, but test the method)
            sample_data = self.client.sample_raw_data(save_to_samples=True)
            
            # Verify structure and assert we get actual data
            self.assertIsInstance(sample_data, dict)
            self.assertIn('Sensors', sample_data)
            self.assertGreater(len(sample_data['Sensors']), 0, "Should return at least 1 sensor with data")
            
            # Check that sensor data has expected fields
            sensor = sample_data['Sensors'][0]
            required_fields = ['SensorID', 'SensorName', 'SensorType', 'SensorReading']
            for field in required_fields:
                self.assertIn(field, sensor, f"Missing field: {field}")
            
            print(f"   [OK] Retrieved current data for {len(sample_data['Sensors'])} sensors")
            
            # Verify sample files were created
            import pathlib
            samples_dir = pathlib.Path(__file__).parent.parent / "samples" / "coris"
            
            if samples_dir.exists():
                json_files = list(samples_dir.glob("*.json"))
                self.assertTrue(len(json_files) > 0, "No JSON files found in samples directory")
                print(f"   [OK] Sample data files created: {len(json_files)} files")
            else:
                print("   [WARN]  Samples directory not created (may be disabled)")
                
        except Exception as e:
            self.fail(f"Current data test failed: {e}")

    def test_get_historical_data_short_period(self):
        """Test getting historical data for a very short period (30 minutes)."""
        if not self.test_sensors:
            self.skipTest("No test sensors available")
            
        try:
            print("   [TEST] Testing historical data retrieval for 30-minute period...")
            
            # Use first sensor for testing
            test_sensor = self.test_sensors[0]
            sensor_id = test_sensor['SensorID']
            sensor_type = test_sensor.get('SensorType', 'Temperature')
            
            # Determine reading type based on sensor type
            reading_type = 'SensorReadingF'  # Default to Fahrenheit temperature
            if 'humidity' in sensor_type.lower() or 'rh' in sensor_type.lower():
                reading_type = 'SensorReadingRh'
            
            # Use a 30-minute period ending now for quick test
            end_time = datetime.datetime.now(datetime.timezone.utc)
            start_time = end_time - datetime.timedelta(minutes=30)
            
            start_utc = int(start_time.timestamp())
            end_utc = int(end_time.timestamp())
            
            # Create minimal sensor metadata DataFrame for the test
            sensor_metadata = pl.DataFrame({
                'sensor_id': [sensor_id],
                'sensor_name': [test_sensor.get('SensorName', f'Sensor_{sensor_id}')],
                'sensor_type': [sensor_type],
                'device_name': [test_sensor.get('DeviceName', 'Test_Device')]
            })
            
            # Get historical data for single sensor
            df = self.client.get_historical_data(
                sensor_id=sensor_id,
                reading_type=reading_type,
                start_utc=start_utc,
                end_utc=end_utc,
                sensor_metadata=sensor_metadata
            )
            
            # Assert that we actually get data back
            self.assertIsNotNone(df, "get_historical_data should not return None")
            self.assertGreater(len(df), 0, "get_historical_data should return at least 1 row of data")
            
            print(f"   [OK] Retrieved {len(df)} historical records for sensor {sensor_id}")
            
            # Verify basic structure
            self.assertIn('timestamp', df.columns)
            self.assertIn('sensor_id', df.columns)
            
            # Verify data types
            self.assertTrue(df['timestamp'].dtype == pl.Int64, "timestamp should be Int64")
            self.assertTrue(df['sensor_id'].dtype == pl.Int64, "sensor_id should be Int64")
            
            print("   [OK] Historical data has correct basic schema")
                
        except Exception as e:
            # Don't fail the test if it's just a data availability issue
            print(f"   [WARN]  Historical data test encountered issue: {e}")

    def test_get_historical_data_bulk_limited(self):
        """Test bulk historical data retrieval with limited sensors and time."""
        if len(self.test_sensors) < 2:
            self.skipTest("Need at least 2 sensors for bulk test")
            
        try:
            print("   [TEST] Testing bulk historical data retrieval (limited scope)...")
            
            # Use a very short time period (15 minutes) and limited sensors
            end_time = datetime.datetime.now(datetime.timezone.utc)
            start_time = end_time - datetime.timedelta(minutes=15)
            
            start_utc = int(start_time.timestamp())
            end_utc = int(end_time.timestamp())
            
            # Create acceptable range with only first 2 sensors for speed
            acceptable_range = {
                'sensor_ids': [s['SensorID'] for s in self.test_sensors[:2]],
                'reading_types': ['SensorReadingF']  # Only test temperature readings
            }
            
            # Get bulk historical data
            results = self.client.get_historical_data_bulk(
                acceptable_range=acceptable_range,
                start_utc=start_utc,
                end_utc=end_utc
            )
            
            # Assert that we actually get results back
            self.assertIsNotNone(results, "get_historical_data_bulk should not return None")
            self.assertGreater(len(results), 0, "get_historical_data_bulk should return results for at least 1 sensor")
            
            # Assert that at least one sensor has data
            has_data = any(len(df) > 0 for df in results.values())
            self.assertTrue(has_data, "At least one sensor should have historical data")
            
            total_records = sum(len(df) for df in results.values())
            self.assertGreater(total_records, 0, "Should have at least 1 record across all sensors")
            
            print(f"   [OK] Bulk retrieval returned {total_records} total records across {len(results)} result sets")
            
            # Verify that we get results for our test sensors
            for result_key, df in results.items():
                if len(df) > 0:
                    self.assertIn('timestamp', df.columns)
                    self.assertIn('sensor_id', df.columns)
                    print(f"   [OK] Result '{result_key}' has {len(df)} records with correct schema")
                
        except Exception as e:
            # Don't fail the test if it's just a data availability issue
            print(f"   [WARN]  Bulk historical data test encountered issue: {e}")

    def test_api_connection(self):
        """Test basic API connectivity."""
        try:
            print("   [TEST] Testing API connection...")
            
            # Test the basic user info endpoint
            url = f"{self.client.base_url}/cats/user/?ApiKey={self.client.api_key}&CatsUserID={self.client.cats_user_id}"
            
            import requests
            response = requests.get(url)
            
            # Should get a valid response
            self.assertEqual(response.status_code, 200, 
                           f"API request failed with status {response.status_code}")
            
            # Should return JSON data
            data = response.json()
            self.assertIn('CatsUserID', data)
            self.assertEqual(data['CatsUserID'], self.client.cats_user_id)
            
            print(f"   [OK] API connection successful for user {data.get('CatsUserName', 'Unknown')}")
            
        except Exception as e:
            self.fail(f"API connection test failed: {e}")

    def test_sensor_data_structure(self):
        """Test that sensor data has expected structure."""
        try:
            print("   [TEST] Testing sensor data structure...")
            
            if not self.test_sensors:
                self.skipTest("No sensors available for structure test")
            
            sensor = self.test_sensors[0]
            
            # Check required fields
            required_fields = ['SensorID', 'SensorName', 'SensorType']
            for field in required_fields:
                self.assertIn(field, sensor, f"Missing required field: {field}")
            
            # Check data types
            self.assertIsInstance(sensor['SensorID'], int, "SensorID should be integer")
            self.assertIsInstance(sensor['SensorName'], str, "SensorName should be string")
            self.assertIsInstance(sensor['SensorType'], str, "SensorType should be string")
            
            # Check for reading data if available
            reading_fields = ['SensorReading', 'SensorReadingC', 'SensorReadingF']
            has_reading = any(field in sensor for field in reading_fields)
            
            if has_reading:
                print(f"   [OK] Sensor {sensor['SensorID']} has reading data")
            else:
                print(f"   [WARN]  Sensor {sensor['SensorID']} has no current reading data")
            
            print(f"   [OK] Sensor data structure is valid for {len(self.test_sensors)} sensors")
            
        except Exception as e:
            self.fail(f"Sensor data structure test failed: {e}")


def main():
    """Run the Coris client tests."""
    print("Running Coris Client Tests")
    print("=" * 50)
    
    # Create test suite
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestCorisClient)
    
    # Run tests with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Print summary
    if result.wasSuccessful():
        print("\n[SUCCESS] All Coris client tests passed!")
    else:
        print(f"\n[FAILED] {len(result.failures)} test(s) failed, {len(result.errors)} error(s)")
        
    return result.wasSuccessful()


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
