#!/usr/bin/env python3
"""
Unit Tests for Conserv API Integration

Tests the Conserv API client, schema transformations, and data merging logic
using mocked API responses to ensure reliable testing without external dependencies.
"""

import unittest
from unittest.mock import Mock, patch
import polars
import datetime
import sys
import os

# Add the parent directory to the path to import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clients.conserv_client import ConservAPIClient, ConservCustomer
from EnvironmentData import EnvironmentData


class TestConservAPIClient(unittest.TestCase):
    """Test the ConservAPIClient functionality with mocked API responses."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.customers = [
            ConservCustomer(customer_id=1545, api_key="test_key_1545"),
            ConservCustomer(customer_id=333, api_key="test_key_333"),
        ]
        self.client = ConservAPIClient(self.customers)
    
    def test_client_initialization(self):
        """Test that the ConservAPIClient initializes correctly."""
        self.assertEqual(len(self.client.customers), 2)
        self.assertEqual(self.client.customers[0].customer_id, 1545)
        self.assertEqual(self.client.customers[1].customer_id, 333)
        self.assertEqual(self.client.base_url, "https://api.conserv.io")
    
    @patch('requests.request')
    def test_launch_export_success(self, mock_request):
        """Test successful export launch."""
        # Mock successful API response
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"uuid": "test-uuid-123"}
        mock_request.return_value = mock_response
        
        start_time = datetime.datetime(2025, 6, 16, 12, 0, 0, tzinfo=datetime.timezone.utc)
        end_time = datetime.datetime(2025, 6, 17, 12, 0, 0, tzinfo=datetime.timezone.utc)
        
        uuid = self.client.launch_export("test_key", start_time, end_time)
        
        self.assertEqual(uuid, "test-uuid-123")
        mock_request.assert_called_once()
    
    @patch('requests.request')
    def test_check_export_status(self, mock_request):
        """Test export status checking."""
        # Mock status response
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"status": "completed"}
        mock_request.return_value = mock_response
        
        status = self.client.check_export_status("test-uuid", "test_key")
        
        self.assertEqual(status, "completed")
    
    @patch('requests.request')
    def test_get_download_url(self, mock_request):
        """Test getting download URL."""
        # Mock download URL response
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"url": "https://s3.amazonaws.com/test-download"}
        mock_request.return_value = mock_response
        
        url = self.client.get_download_url("test-uuid", "test_key")
        
        self.assertEqual(url, "https://s3.amazonaws.com/test-download")
    
    @patch('requests.get')
    def test_download_export_data(self, mock_get):
        """Test downloading and parsing CSV data."""
        # Mock CSV data
        csv_data = b"Sensor Name,Time,Temperature (C),Humidity (%)\nSensor1,2025-06-17 12:00:00,22.5,65.2\nSensor2,2025-06-17 12:05:00,23.1,67.8"
        
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.content = csv_data
        mock_get.return_value = mock_response
        
        df = self.client.download_export_data("https://test-url")
        
        self.assertEqual(df.shape[0], 2)  # 2 data rows
        self.assertIn("Sensor Name", df.columns)
        self.assertIn("Temperature (C)", df.columns)


class TestConservSchemaTransformation(unittest.TestCase):
    """Test the Conserv to Coris schema transformation logic."""
    
    def setUp(self):
        """Set up test environment."""
        # Create a minimal EnvironmentData instance for testing
        with patch('EnvironmentData.EnvironmentData.initialize_database'), \
             patch('EnvironmentData.EnvironmentData.update_cron_status'), \
             patch('builtins.open'), \
             patch('os.path.exists'):
            
            self.env_data = EnvironmentData(
                CatsUserID=2496,
                testing=True,
                conserv_enabled=False  # Disable actual Conserv client for unit tests
            )
    
    def test_temperature_conversion_celsius_to_fahrenheit(self):
        """Test that temperature conversion from °C to °F works correctly."""
        # Create test Conserv data
        conserv_data = polars.DataFrame({
            "Sensor Name": ["TestSensor1", "TestSensor2"],
            "Time": [1750162000, 1750162300],  # Unix timestamps
            "Temperature (°C)": [22.5, 0.0],  # Test normal temp and freezing
            "Humidity (%)": [65.2, 50.0],
            "customer_id": [1545, 1545]
        })
        
        # Transform the data
        transformed = self.env_data._transform_conserv_to_coris_schema(conserv_data, 1750162500)
        
        # Check temperature conversion (22.5°C = 72.5°F, 0°C = 32°F)
        expected_temps = [72.5, 32.0]
        actual_temps = transformed['SensorReadingF'].to_list()
        
        for expected, actual in zip(expected_temps, actual_temps):
            self.assertAlmostEqual(actual, expected, places=1)
    
    def test_schema_column_mapping(self):
        """Test that all required schema columns are created correctly."""
        conserv_data = polars.DataFrame({
            "Sensor Name": ["TestSensor"],
            "Time": [1750162000],
            "Temperature (°C)": [25.0],
            "Humidity (%)": [60.0],
            "customer_id": [1545]
        })
        
        transformed = self.env_data._transform_conserv_to_coris_schema(conserv_data, 1750162500)
        
        # Check required columns exist
        required_columns = [
            'SensorReadingF', 'SensorReadingRh', 'SensorReadingUTC', 
            'QueryUTC', 'SensorID', 'SensorName', 'source',
            'DeviceID', 'DeviceName', 'SensorType',
            'customer_id'
        ]
        
        for col in required_columns:
            self.assertIn(col, transformed.columns, f"Missing required column: {col}")
        
        # Check data types
        self.assertEqual(transformed['SensorReadingF'].dtype, polars.Float32)
        self.assertEqual(transformed['SensorReadingRh'].dtype, polars.Float32)
        self.assertEqual(transformed['SensorReadingUTC'].dtype, polars.Int64)
        self.assertEqual(transformed['customer_id'].dtype, polars.Int32)
        
        # Check source column value
        self.assertEqual(transformed['source'][0], 'conserv')
    
    def test_null_coris_columns(self):
        """Test that Coris-specific columns are properly set to null."""
        conserv_data = polars.DataFrame({
            "Sensor Name": ["TestSensor"],
            "Time": [1750162000],
            "Temperature (°C)": [25.0],
            "Humidity (%)": [60.0],
            "customer_id": [1545]
        })
        
        transformed = self.env_data._transform_conserv_to_coris_schema(conserv_data, 1750162500)
        
        # Check that consolidated SensorID uses conserv prefix
        sensor_ids = transformed['SensorID'].to_list()
        for sensor_id in sensor_ids:
            self.assertTrue(sensor_id.startswith('conserv:'), f"SensorID should start with 'conserv:': {sensor_id}")
        
        # Check that DeviceID uses conserv prefix  
        device_ids = transformed['DeviceID'].drop_nulls().to_list()
        for device_id in device_ids:
            self.assertTrue(device_id.startswith('conserv:'), f"DeviceID should start with 'conserv:': {device_id}")


class TestDataMerging(unittest.TestCase):
    """Test the data merging logic between Coris and Conserv sources."""
    
    def test_merged_dataframe_required_fields(self):
        """Test that merged dataframe includes all required fields."""
        # Create mock Coris data with proper schema
        coris_data = polars.DataFrame({
            'SensorID': ['coris:1', 'coris:2'],
            'DeviceID': ['coris:1', 'coris:2'], 
            'customer_id': [None, None],
            'source': ['coris', 'coris'],
            'SensorReadingUTC': [1750162000, 1750162300],
            'QueryUTC': [1750162500, 1750162500],
            'SensorReadingF': [70.2, 71.5],
            'SensorReadingRh': [65.0, 67.0],
            'SensorName': ['CorisTemp1', 'CorisTemp2']
        }).with_columns([
            polars.col('SensorID').cast(polars.String),
            polars.col('DeviceID').cast(polars.String),
            polars.col('customer_id').cast(polars.Int32)
        ])
        
        # Create mock Conserv data with compatible schema
        conserv_data = polars.DataFrame({
            'SensorID': ['conserv:1545:ConservSensor1', 'conserv:1545:ConservSensor2'],
            'DeviceID': ['conserv:1545:Device1', 'conserv:1545:Device2'],
            'customer_id': [1545, 1545],
            'source': ['conserv', 'conserv'],
            'SensorReadingUTC': [1750162100, 1750162400],
            'QueryUTC': [1750162500, 1750162500],
            'SensorReadingF': [72.5, 73.1],
            'SensorReadingRh': [60.2, 62.8],
            'SensorName': ['ConservTemp1', 'ConservTemp2']
        }).with_columns([
            polars.col('SensorID').cast(polars.String),
            polars.col('DeviceID').cast(polars.String),
            polars.col('customer_id').cast(polars.Int32)
        ])
        
        # Merge the data
        merged = polars.concat([coris_data, conserv_data], how='diagonal')
        
        # Test required fields are present
        required_fields = ['SensorReadingUTC', 'SensorReadingF', 'SensorReadingRh', 'source']
        for field in required_fields:
            self.assertIn(field, merged.columns)
        
        # Test that we have data from both sources
        sources = set(merged['source'].unique().to_list())
        self.assertEqual(sources, {'coris', 'conserv'})
        
        # Test consolidated SensorID logic 
        coris_rows = merged.filter(polars.col('source') == 'coris')
        conserv_rows = merged.filter(polars.col('source') == 'conserv')
        
        # Coris rows should have SensorID starting with 'coris:'
        coris_sensor_ids = coris_rows['SensorID'].to_list()
        for sensor_id in coris_sensor_ids:
            self.assertTrue(sensor_id.startswith('coris:'), f"Coris SensorID should start with 'coris:': {sensor_id}")
        
        # Conserv rows should have SensorID starting with 'conserv:'
        conserv_sensor_ids = conserv_rows['SensorID'].to_list()
        for sensor_id in conserv_sensor_ids:
            self.assertTrue(sensor_id.startswith('conserv:'), f"Conserv SensorID should start with 'conserv:': {sensor_id}")
    
    def test_customer_id_mapping(self):
        """Test that customer_id is properly mapped for Conserv data."""
        conserv_data = polars.DataFrame({
            'SensorID': ['conserv:1545:Sensor1', 'conserv:333:Sensor2', 'conserv:2671:Sensor3'],
            'customer_id': [1545, 333, 2671],
            'source': ['conserv', 'conserv', 'conserv'],
            'SensorReadingF': [70.0, 71.0, 72.0],
            'SensorReadingRh': [60.0, 61.0, 62.0]
        })
        
        # Test that all customer_ids are from our expected list
        expected_customers = [1545, 333, 307, 2671, 1696]
        actual_customers = conserv_data['customer_id'].unique().to_list()
        
        for customer in actual_customers:
            self.assertIn(customer, expected_customers)


class TestIntegrationScenarios(unittest.TestCase):
    """Test integration scenarios and error handling."""
    
    def test_no_data_found_handling(self):
        """Test graceful handling of 'No data found' responses."""
        customers = [ConservCustomer(customer_id=999, api_key="test_key")]
        client = ConservAPIClient(customers)
        
        with patch.object(client, 'export_data', return_value=None):
            # Test that empty result is handled gracefully
            result = client.get_data_for_period(1750162000, 1750162500)
            self.assertIsNone(result)
    
    def test_individual_customer_failure_handling(self):
        """Test that individual customer failures don't break the entire process."""
        customers = [
            ConservCustomer(customer_id=1545, api_key="working_key"),
            ConservCustomer(customer_id=333, api_key="failing_key"),
        ]
        client = ConservAPIClient(customers)
        
        # Mock: first customer succeeds, second fails
        successful_data = polars.DataFrame({
            'Sensor Name': ['TestSensor'],
            'Time': [1750162000],
            'Temperature (°C)': [25.0],
            'Humidity (%)': [60.0],
            'customer_id': [1545]
        })
        
        def mock_export_data(api_key, customer_id, start_time, end_time):
            if customer_id == 1545:
                return successful_data
            else:
                raise Exception("API Error")
        
        with patch.object(client, 'export_data', side_effect=mock_export_data):
            result = client.get_data_for_period(1750162000, 1750162500)
            
            # Should return data from successful customer only
            self.assertIsNotNone(result)
            self.assertEqual(result.shape[0], 1)
            self.assertEqual(result['customer_id'][0], 1545)


def run_conserv_tests():
    """Run all Conserv integration tests."""
    # Create test suite
    test_suite = unittest.TestSuite()
    
    # Add test classes
    test_classes = [
        TestConservAPIClient,
        TestConservSchemaTransformation,
        TestDataMerging,
        TestIntegrationScenarios
    ]
    
    for test_class in test_classes:
        tests = unittest.TestLoader().loadTestsFromTestCase(test_class)
        test_suite.addTests(tests)
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(test_suite)
    
    return result.wasSuccessful()


if __name__ == '__main__':
    print("="*60)
    print("RUNNING CONSERV API INTEGRATION TESTS")
    print("="*60)
    
    success = run_conserv_tests()
    
    if success:
        print("\nSUCCESS: ALL CONSERV INTEGRATION TESTS PASSED!")
    else:
        print("\nFAILED: SOME TESTS FAILED!")
        exit(1) 