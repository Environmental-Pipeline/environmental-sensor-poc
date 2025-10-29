#!/usr/bin/env python3
"""
Test suite for Hobolink API client integration.

This module contains comprehensive tests for the HobolinkClient class,
including API connectivity, data retrieval, transformation, and schema compatibility.
"""

import unittest
import logging
import datetime
from unittest.mock import patch, MagicMock
import polars as pl

# Set up test logging to be less verbose
logging.basicConfig(level=logging.WARNING)

try:
    from hobolink_client import HobolinkClient, create_hobolink_client_from_env
    HOBOLINK_AVAILABLE = True
except ImportError as e:
    print(f"Hobolink client not available: {e}")
    HOBOLINK_AVAILABLE = False


@unittest.skipUnless(HOBOLINK_AVAILABLE, "Hobolink client not available")
class TestHobolinkClient(unittest.TestCase):
    """Test cases for HobolinkClient functionality."""
    
    @classmethod
    def setUpClass(cls):
        """Set up test fixtures for the entire test class."""
        cls.logger = logging.getLogger(__name__)
        try:
            cls.client = create_hobolink_client_from_env(logger=cls.logger)
            cls.client_available = True
        except Exception as e:
            cls.logger.warning(f"Could not create Hobolink client: {e}")
            cls.client_available = False
    
    def setUp(self):
        """Set up test fixtures for each test method."""
        if not self.client_available:
            self.skipTest("Hobolink client not available - check HOBOLINK_API_KEY environment variable")
    
    def test_api_connection(self):
        """Test basic API connectivity."""
        self.assertTrue(self.client.test_api_connection(), 
                       "API connection should be successful")
    
    def test_get_devices_as_dataframe(self):
        """Test device and sensor discovery functionality."""
        devices_df = self.client.get_devices_as_dataframe(testing=True, testing_device_limit=2)
        
        # Verify DataFrame structure
        self.assertIsInstance(devices_df, pl.DataFrame, 
                            "Should return a Polars DataFrame")
        self.assertGreater(len(devices_df), 0, 
                          "Should retrieve at least one device/sensor combination")
        
        # Verify required columns exist
        required_columns = [
            'DeviceID_Hobolink', 'DeviceName', 'SensorID_Hobolink', 
            'SensorName', 'SensorType', 'source'
        ]
        for col in required_columns:
            self.assertIn(col, devices_df.columns, 
                         f"DataFrame should contain column '{col}'")
        
        # Verify source column is correct
        sources = devices_df['source'].unique().to_list()
        self.assertEqual(sources, ['hobolink'], 
                        "All records should have source='hobolink'")
        
        # Verify we have actual sensor data
        sensor_data = devices_df.filter(pl.col('SensorID_Hobolink').is_not_null())
        self.assertGreater(len(sensor_data), 0, 
                          "Should have sensors with valid IDs")
    
    def test_get_current_readings(self):
        """Test current readings retrieval and formatting."""
        current_readings = self.client.get_current_readings(testing=True, testing_device_limit=2)
        
        # Verify DataFrame structure
        self.assertIsInstance(current_readings, pl.DataFrame, 
                            "Should return a Polars DataFrame")
        
        if len(current_readings) > 0:
            # Verify required columns exist
            required_columns = [
                'source', 'SensorID_Hobolink', 'DeviceName', 'SensorType', 'SensorReading'
            ]
            for col in required_columns:
                self.assertIn(col, current_readings.columns, 
                             f"DataFrame should contain column '{col}'")
            
            # Verify source column is correct
            sources = current_readings['source'].unique().to_list()
            self.assertEqual(sources, ['hobolink'], 
                            "All records should have source='hobolink'")
            
            # Check that we have actual readings
            non_null_readings = current_readings.filter(
                pl.col('SensorReading').is_not_null()
            )
            self.assertGreater(len(non_null_readings), 0, 
                              "Should have at least one sensor with a current reading")
            
            # Verify sensor type mapping works
            rh_sensors = current_readings.filter(
                pl.col('SensorType').str.to_lowercase().str.contains('rh|humidity')
            )
            if len(rh_sensors) > 0:
                self.assertIn('SensorReadingRh', current_readings.columns,
                             "Should have SensorReadingRh column for humidity sensors")
    
    def test_get_historical_data_bulk_basic(self):
        """Test basic historical data retrieval functionality."""
        # Use a 24-hour range for testing
        end_time = datetime.datetime.now(datetime.timezone.utc)
        start_time = end_time - datetime.timedelta(hours=24)
        
        end_utc = int(end_time.timestamp())
        start_utc = int(start_time.timestamp())
        
        # Use actual available data but limit for reasonable test time
        historical_data_list = self.client.get_historical_data_bulk(
            start_utc=start_utc,
            end_utc=end_utc,
            testing=True,
            testing_device_limit=3,  # Allow up to 3 devices
            testing_sensors_per_device=3  # Allow up to 3 sensors per device
        )
        
        # Verify return type
        self.assertIsInstance(historical_data_list, list, 
                             "Should return a list of DataFrames")
        
        # If we got data, verify its structure
        if len(historical_data_list) > 0:
            for df in historical_data_list:
                self.assertIsInstance(df, pl.DataFrame, 
                                    "Each item should be a Polars DataFrame")
                
                # Verify required columns
                required_columns = ['SensorReadingUTC', 'SensorID_Hobolink', 'SensorType', 'source']
                for col in required_columns:
                    self.assertIn(col, df.columns, 
                                 f"Historical DataFrame should contain column '{col}'")
                
                # Verify source
                sources = df['source'].unique().to_list()
                self.assertEqual(sources, ['hobolink'], 
                                "All historical records should have source='hobolink'")
                
                # Verify timeframe - all timestamps should be within requested range
                timestamps = df['SensorReadingUTC'].to_list()
                for timestamp in timestamps:
                    self.assertGreaterEqual(timestamp, start_utc, 
                                          f"Timestamp {timestamp} should be >= start time {start_utc}")
                    self.assertLessEqual(timestamp, end_utc, 
                                       f"Timestamp {timestamp} should be <= end time {end_utc}")
                
                # Log timeframe validation results for debugging
                if timestamps:
                    min_timestamp = min(timestamps)
                    max_timestamp = max(timestamps)
                    print(f"Timeframe validation: requested [{start_utc}, {end_utc}], "
                          f"got [{min_timestamp}, {max_timestamp}]")
    
    def test_transform_to_standardized_schema(self):
        """Test data transformation to standardized schema."""
        # Create mock raw data that matches Hobolink API response format
        mock_raw_data = {
            "moreResults": False,
            "sensors": [{
                "totalRecords": 3,
                "sensorSerialNumber": "test-sensor-1",
                "latestTimestamp": 1761768000000,
                "data": [{
                    "measurementType": "RH",
                    "dataType": "CURRENT",
                    "units": "%",
                    "records": [
                        [1761684300000, 45.5],
                        [1761685200000, 46.2],
                        [1761686100000, 44.8]
                    ]
                }]
            }]
        }
        
        # Test transformation
        transformed = self.client.transform_to_standardized_schema(mock_raw_data)
        
        # Verify transformation results
        self.assertIsInstance(transformed, pl.DataFrame, 
                            "Should return a Polars DataFrame")
        self.assertEqual(len(transformed), 3, 
                        "Should have 3 transformed readings")
        
        # Verify required columns
        required_columns = [
            'SensorReadingUTC', 'SensorID_Hobolink', 'SensorType', 
            'source', 'SensorReadingRh'
        ]
        for col in required_columns:
            self.assertIn(col, transformed.columns, 
                         f"Transformed DataFrame should contain column '{col}'")
        
        # Verify data values
        self.assertEqual(transformed['source'].unique().to_list(), ['hobolink'])
        self.assertEqual(transformed['SensorType'].unique().to_list(), ['RH'])
        self.assertEqual(transformed['SensorID_Hobolink'].unique().to_list(), ['test-sensor-1'])
        
        # Verify timestamp conversion (ms to seconds)
        expected_timestamps = [1761684300, 1761685200, 1761686100]
        actual_timestamps = transformed['SensorReadingUTC'].to_list()
        self.assertEqual(actual_timestamps, expected_timestamps, 
                        "Timestamps should be converted from milliseconds to seconds")
        
        # Verify RH values (with float precision tolerance)
        expected_rh_values = [45.5, 46.2, 44.8]
        actual_rh_values = transformed['SensorReadingRh'].to_list()
        for expected, actual in zip(expected_rh_values, actual_rh_values):
            self.assertAlmostEqual(actual, expected, places=1, 
                                 msg="RH values should be preserved correctly")
    
    def test_transform_temperature_sensors(self):
        """Test transformation of temperature sensor data."""
        # Test Fahrenheit temperature
        mock_raw_data_f = {
            "sensors": [{
                "sensorSerialNumber": "temp-sensor-f",
                "data": [{
                    "measurementType": "Temperature",
                    "units": "°F",
                    "records": [[1761684300000, 72.5]]
                }]
            }]
        }
        
        transformed_f = self.client.transform_to_standardized_schema(mock_raw_data_f)
        self.assertIn('SensorReadingF', transformed_f.columns)
        self.assertAlmostEqual(transformed_f['SensorReadingF'].to_list()[0], 72.5, places=1)
        
        # Test Celsius temperature
        mock_raw_data_c = {
            "sensors": [{
                "sensorSerialNumber": "temp-sensor-c",
                "data": [{
                    "measurementType": "Temperature",
                    "units": "°C",
                    "records": [[1761684300000, 22.5]]
                }]
            }]
        }
        
        transformed_c = self.client.transform_to_standardized_schema(mock_raw_data_c)
        self.assertIn('SensorReadingC', transformed_c.columns)
        self.assertAlmostEqual(transformed_c['SensorReadingC'].to_list()[0], 22.5, places=1)
    
    def test_timeframe_validation(self):
        """Test that returned data respects the requested timeframe."""
        # Create mock data with timestamps both inside and outside a time window
        now = datetime.datetime.now(datetime.timezone.utc)
        window_start = now - datetime.timedelta(hours=2)
        window_end = now - datetime.timedelta(hours=1) 
        
        # Timestamps: before window, in window, after window
        before_window_ts = int((window_start - datetime.timedelta(minutes=30)).timestamp() * 1000)
        in_window_ts = int((window_start + datetime.timedelta(minutes=30)).timestamp() * 1000)
        after_window_ts = int((window_end + datetime.timedelta(minutes=30)).timestamp() * 1000)
        
        # Mock data that includes timestamps outside the requested range
        mock_data_mixed = {
            "sensors": [{
                "sensorSerialNumber": "test-sensor-timeframe",
                "data": [{
                    "measurementType": "Temperature", 
                    "units": "°F",
                    "records": [
                        [before_window_ts, 70.0],  # Before window - should be filtered
                        [in_window_ts, 72.0],      # In window - should be kept
                        [after_window_ts, 74.0]    # After window - should be filtered  
                    ]
                }]
            }]
        }
        
        # Transform the data
        transformed = self.client.transform_to_standardized_schema(mock_data_mixed)
        
        # All transformed timestamps should be in seconds (converted from ms)
        expected_before = before_window_ts // 1000
        expected_in = in_window_ts // 1000  
        expected_after = after_window_ts // 1000
        
        timestamps = transformed['SensorReadingUTC'].to_list()
        
        # Verify we got all three timestamps (transformation doesn't filter by time)
        self.assertEqual(len(timestamps), 3, "Should have 3 timestamp records")
        self.assertIn(expected_before, timestamps, "Should include before-window timestamp")
        self.assertIn(expected_in, timestamps, "Should include in-window timestamp") 
        self.assertIn(expected_after, timestamps, "Should include after-window timestamp")
        
        # Now test what would happen if we filter by timeframe
        window_start_utc = int(window_start.timestamp())
        window_end_utc = int(window_end.timestamp())
        
        # Filter to simulate what the API should return
        filtered_df = transformed.filter(
            (pl.col('SensorReadingUTC') >= window_start_utc) & 
            (pl.col('SensorReadingUTC') <= window_end_utc)
        )
        
        # Should only have the in-window timestamp
        filtered_timestamps = filtered_df['SensorReadingUTC'].to_list()
        self.assertEqual(len(filtered_timestamps), 1, "Should have 1 timestamp in window")
        self.assertEqual(filtered_timestamps[0], expected_in, "Should only have in-window timestamp")

    def test_empty_response_handling(self):
        """Test handling of empty API responses."""
        # Test with empty sensors array
        empty_response = {"sensors": []}
        transformed = self.client.transform_to_standardized_schema(empty_response)
        self.assertTrue(transformed.is_empty(), "Should return empty DataFrame for empty response")
        
        # Test with no sensors key
        no_sensors_response = {"moreResults": False}
        transformed = self.client.transform_to_standardized_schema(no_sensors_response)
        self.assertTrue(transformed.is_empty(), "Should return empty DataFrame when no sensors key")
    
    def test_client_creation_from_env(self):
        """Test client creation from environment variables."""
        # This test assumes HOBOLINK_API_KEY is set
        client = create_hobolink_client_from_env()
        self.assertIsInstance(client, HobolinkClient, 
                            "Should create a HobolinkClient instance")
        self.assertTrue(hasattr(client, 'api_key'), 
                       "Client should have api_key attribute")
        self.assertEqual(client.base_url, "https://api.licor.cloud/v2", 
                        "Client should have correct base URL")


class TestHobolinkIntegration(unittest.TestCase):
    """Integration tests for Hobolink client with real API calls."""
    
    @classmethod
    def setUpClass(cls):
        """Set up integration test fixtures."""
        cls.logger = logging.getLogger(__name__)
        try:
            cls.client = create_hobolink_client_from_env(logger=cls.logger)
            cls.integration_available = True
        except Exception as e:
            cls.logger.warning(f"Integration tests not available: {e}")
            cls.integration_available = False
    
    def setUp(self):
        """Set up integration test fixtures for each test method."""
        if not self.integration_available:
            self.skipTest("Integration tests not available - check HOBOLINK_API_KEY")
    
    def test_end_to_end_data_pipeline(self):
        """Test complete data pipeline from API to standardized format."""
        # Step 1: Get devices
        devices_df = self.client.get_devices_as_dataframe(testing=True, testing_device_limit=1)
        self.assertGreater(len(devices_df), 0, "Should retrieve devices")
        
        # Step 2: Get current readings
        current_df = self.client.get_current_readings(testing=True, testing_device_limit=1)
        # Note: Current readings might be empty if no sensors have latest values
        
        # Step 3: Try to get some historical data
        end_time = datetime.datetime.now(datetime.timezone.utc)
        start_time = end_time - datetime.timedelta(hours=48)  # Wider range for integration test
        
        # Use reasonable limits for integration testing but allow multiple sensors
        historical_list = self.client.get_historical_data_bulk(
            start_utc=int(start_time.timestamp()),
            end_utc=int(end_time.timestamp()),
            testing=True,
            testing_device_limit=2,  # Test with up to 2 devices
            testing_sensors_per_device=3  # Test with up to 3 sensors per device
        )
        
        # Verify we can process the pipeline without errors
        # (Data availability depends on sensor activity)
        self.assertIsInstance(historical_list, list, 
                             "Historical data should return a list")
        
        # If we got historical data, verify it's properly formatted
        if len(historical_list) > 0:
            combined_df = pl.concat(historical_list, how="diagonal")
            self.assertGreater(len(combined_df), 0, 
                              "Combined historical data should have readings")
            self.assertIn('source', combined_df.columns)
            self.assertEqual(combined_df['source'].unique().to_list(), ['hobolink'])
            
            # Verify timeframe for integration test
            start_utc = int(start_time.timestamp())
            end_utc = int(end_time.timestamp())
            timestamps = combined_df['SensorReadingUTC'].to_list()
            
            for timestamp in timestamps:
                self.assertGreaterEqual(timestamp, start_utc,
                                      f"Integration test: timestamp {timestamp} should be >= {start_utc}")
                self.assertLessEqual(timestamp, end_utc,
                                   f"Integration test: timestamp {timestamp} should be <= {end_utc}")
            
            # Log integration timeframe results
            if timestamps:
                min_ts = min(timestamps)
                max_ts = max(timestamps)
                print(f"Integration timeframe validation: requested [{start_utc}, {end_utc}], "
                      f"received [{min_ts}, {max_ts}]")


if __name__ == '__main__':
    # Configure test runner
    unittest.main(verbosity=2, buffer=True)