# Tests for validation functions with invalid data
# Run with: python tests/test_validation.py
# Or: pytest tests/test_validation.py -v

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import unittest
import polars
import logging
import datetime

from modules import validation


# Create a simple logger for tests
def create_test_logger():
    """Create a simple logger for tests that captures output."""
    logger = logging.getLogger("test_validation")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        logger.addHandler(handler)
    return logger


# Default acceptable ranges for validation tests
DEFAULT_ACCEPTABLE_RANGE = {
    "SensorReadingF": [32, 100],
    "SensorReadingRh": [20, 80],
}


def create_valid_sensor_data() -> polars.DataFrame:
    """Create a valid sensor DataFrame for testing."""
    return polars.DataFrame({
        "SensorReadingUTC": [1700000000, 1700000900, 1700001800],  # 15-min intervals
        "QueryUTC": [1700000000, 1700000900, 1700001800],
        "Source": ["Coris", "Coris", "Coris"],
        "DeviceID": ["coris:123", "coris:123", "coris:123"],
        "DeviceName": ["Device 1", "Device 1", "Device 1"],
        "SensorID": ["coris:123-1", "coris:123-1", "coris:123-1"],
        "SensorName": ["Temp Sensor 1", "Temp Sensor 1", "Temp Sensor 1"],
        "SensorType": ["Temperature", "Temperature", "Temperature"],
        "Historical": [False, False, False],
        "SensorReadingF": [72.5, 73.0, 72.8],
        "SensorReadingRh": [45.0, 46.0, 45.5],
    }).cast({
        "SensorReadingUTC": polars.Int64,
        "QueryUTC": polars.Int32,
        "SensorReadingF": polars.Float32,
        "SensorReadingRh": polars.Float32,
    })


class TestValidateSensorsWithInvalidData(unittest.TestCase):
    """Test validate_sensors with various types of invalid data.
    
    These tests call diagnostics.validate_sensors() directly with dummy data.
    No API calls are made - all data is synthetic.
    """
    
    @classmethod
    def setUpClass(cls):
        """Set up test environment with a simple logger."""
        cls.logger = create_test_logger()
        cls.acceptable_range = DEFAULT_ACCEPTABLE_RANGE

    def test_missing_required_columns(self):
        """Test that validation detects missing required columns."""
        # Create data missing some columns but keeping SensorID and SensorReadingUTC
        # so later checks don't fail (they need these columns to run)
        invalid_data = polars.DataFrame({
            "SensorReadingUTC": [1700000000],
            "QueryUTC": [1700000000],
            "Source": ["Coris"],
            "DeviceID": ["coris:123"],
            "DeviceName": ["Device 1"],
            "SensorID": ["coris:123-1"],
            "SensorName": ["Sensor 1"],
            # Missing: SensorType, Historical
            "SensorReadingF": [72.5],
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
        })
        
        results = validation.validate_sensors(
            sensors=invalid_data,
            acceptable_range=self.acceptable_range,
            logger=self.logger,
            step="test_missing_columns",
        )
        
        # Find the required_columns_present result
        columns_result = next(r for r in results if r["test_name"] == "required_columns_present")
        
        self.assertEqual(columns_result["result"], "FAIL")
        self.assertIn("missing required columns", columns_result["details"].lower())
        # Check that the specific missing columns are mentioned
        self.assertIn("SensorType", columns_result["details"])
        self.assertIn("Historical", columns_result["details"])

    def test_wrong_column_data_types(self):
        """Test that validation detects wrong column data types."""
        # Create data with wrong types (SensorReadingUTC as String instead of Int64)
        invalid_data = polars.DataFrame({
            "SensorReadingUTC": ["1700000000"],  # Should be Int64, not String
            "QueryUTC": [1700000000],
            "Source": ["Coris"],
            "DeviceID": ["coris:123"],
            "DeviceName": ["Device 1"],
            "SensorID": ["coris:123-1"],
            "SensorName": ["Sensor 1"],
            "SensorType": ["Temperature"],
            "Historical": [False],
            "SensorReadingF": [72.5],
        }).cast({
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
        })
        
        results = validation.validate_sensors(
            sensors=invalid_data,
            acceptable_range=self.acceptable_range,
            logger=self.logger,
            step="test_wrong_types",
        )
        
        # Find the column_data_types result
        types_result = next(r for r in results if r["test_name"] == "column_data_types")
        
        self.assertEqual(types_result["result"], "FAIL")
        self.assertIn("SensorReadingUTC", types_result["details"])
        self.assertIn("data type mismatch", types_result["details"].lower())

    def test_duplicate_readings(self):
        """Test that validation detects duplicate sensor readings (same sensor + timestamp)."""
        # Create data with duplicate readings
        duplicate_data = polars.DataFrame({
            "SensorReadingUTC": [1700000000, 1700000000, 1700000900],  # First two are duplicates
            "QueryUTC": [1700000000, 1700000000, 1700000900],
            "Source": ["Coris", "Coris", "Coris"],
            "DeviceID": ["coris:123", "coris:123", "coris:123"],
            "DeviceName": ["Device 1", "Device 1", "Device 1"],
            "SensorID": ["coris:123-1", "coris:123-1", "coris:123-1"],  # Same sensor ID
            "SensorName": ["Sensor 1", "Sensor 1", "Sensor 1"],
            "SensorType": ["Temperature", "Temperature", "Temperature"],
            "Historical": [False, False, False],
            "SensorReadingF": [72.5, 73.0, 72.8],  # Different values, same timestamp
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
        })
        
        results = validation.validate_sensors(
            sensors=duplicate_data,
            acceptable_range=self.acceptable_range,
            logger=self.logger,
            step="test_duplicates",
        )
        
        # Find the no_duplicate_readings result
        dup_result = next(r for r in results if r["test_name"] == "no_duplicate_readings")
        
        self.assertEqual(dup_result["result"], "FAIL")
        self.assertIn("duplicate readings", dup_result["details"].lower())

    def test_sensor_name_inconsistency(self):
        """Test that validation detects sensors with multiple different names."""
        # Create data where same SensorID has different names
        inconsistent_data = polars.DataFrame({
            "SensorReadingUTC": [1700000000, 1700000900],
            "QueryUTC": [1700000000, 1700000900],
            "Source": ["Coris", "Coris"],
            "DeviceID": ["coris:123", "coris:123"],
            "DeviceName": ["Device 1", "Device 1"],
            "SensorID": ["coris:123-1", "coris:123-1"],  # Same sensor ID
            "SensorName": ["Sensor 1", "Sensor 1 Renamed"],  # Different names!
            "SensorType": ["Temperature", "Temperature"],
            "Historical": [False, False],
            "SensorReadingF": [72.5, 73.0],
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
        })
        
        results = validation.validate_sensors(
            sensors=inconsistent_data,
            acceptable_range=self.acceptable_range,
            logger=self.logger,
            step="test_name_inconsistency",
        )
        
        # Find the sensor_name_consistency result
        name_result = next(r for r in results if r["test_name"] == "sensor_name_consistency")
        
        self.assertEqual(name_result["result"], "FAIL")
        self.assertIn("inconsistent names", name_result["details"].lower())
        self.assertIn("coris:123-1", name_result["details"])

    def test_reading_interval_gaps(self):
        """Test that validation detects gaps >15 minutes between consecutive readings."""
        # Create data with a 30-minute gap
        gap_data = polars.DataFrame({
            "SensorReadingUTC": [1700000000, 1700001800],  # 30-min gap (1800 seconds)
            "QueryUTC": [1700000000, 1700001800],
            "Source": ["Coris", "Coris"],
            "DeviceID": ["coris:123", "coris:123"],
            "DeviceName": ["Device 1", "Device 1"],
            "SensorID": ["coris:123-1", "coris:123-1"],
            "SensorName": ["Sensor 1", "Sensor 1"],
            "SensorType": ["Temperature", "Temperature"],
            "Historical": [False, False],
            "SensorReadingF": [72.5, 73.0],
            "SensorReadingUTC_SecondsFromPrior": [None, 1800],  # 30 minutes = 1800 seconds
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
            "SensorReadingUTC_SecondsFromPrior": polars.Int64,
        })
        
        results = validation.validate_sensors(
            sensors=gap_data,
            acceptable_range=self.acceptable_range,
            logger=self.logger,
            step="test_intervals",
        )
        
        # Find the reading_interval_check result
        interval_result = next(r for r in results if r["test_name"] == "reading_interval_check")
        
        self.assertEqual(interval_result["result"], "WARN")
        self.assertIn("more than 15 minutes", interval_result["details"])

    def test_missing_sensors_from_historical(self):
        """Test that validation detects sensors missing from current data that existed in historical."""
        current_data = polars.DataFrame({
            "SensorReadingUTC": [1700000000],
            "QueryUTC": [1700000000],
            "Source": ["Coris"],
            "DeviceID": ["coris:123"],
            "DeviceName": ["Device 1"],
            "SensorID": ["coris:123-1"],  # Only one sensor
            "SensorName": ["Sensor 1"],
            "SensorType": ["Temperature"],
            "Historical": [False],
            "SensorReadingF": [72.5],
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
        })
        
        # Historical has an additional sensor that's now missing
        historical_data = polars.DataFrame({
            "SensorReadingUTC": [1699900000, 1699900000],
            "QueryUTC": [1699900000, 1699900000],
            "Source": ["Coris", "Coris"],
            "DeviceID": ["coris:123", "coris:456"],
            "DeviceName": ["Device 1", "Device 2"],
            "SensorID": ["coris:123-1", "coris:456-1"],  # Two sensors, one will be "missing"
            "SensorName": ["Sensor 1", "Sensor 2 (Missing)"],
            "SensorType": ["Temperature", "Temperature"],
            "Historical": [True, True],
            "SensorReadingF": [72.0, 71.0],
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
        })
        
        results = validation.validate_sensors(
            sensors=current_data,
            historical=historical_data,
            acceptable_range=self.acceptable_range,
            logger=self.logger,
            step="test_missing_sensors",
        )
        
        # Find the no_missing_sensors result
        missing_result = next(r for r in results if r["test_name"] == "no_missing_sensors")
        
        self.assertEqual(missing_result["result"], "WARN")
        self.assertIn("missing from current data", missing_result["details"])
        self.assertIn("coris:456-1", missing_result["details"])

    def test_all_null_values_warning(self):
        """Test that validation warns when rows have all null values in required columns."""
        # Create data with a row that has all nulls in the key columns
        null_data = polars.DataFrame({
            "SensorReadingUTC": [1700000000, None],
            "QueryUTC": [1700000000, None],
            "Source": ["Coris", None],
            "DeviceID": ["coris:123", None],
            "DeviceName": ["Device 1", None],
            "SensorID": ["coris:123-1", None],
            "SensorName": ["Sensor 1", None],
            "SensorType": ["Temperature", None],
            "Historical": [False, None],
            "SensorReadingF": [72.5, None],
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
        })
        
        results = validation.validate_sensors(
            sensors=null_data,
            acceptable_range=self.acceptable_range,
            logger=self.logger,
            step="test_null_values",
        )
        
        # Find the non_null_values result
        null_result = next(r for r in results if r["test_name"] == "non_null_values")
        
        self.assertEqual(null_result["result"], "WARN")
        self.assertIn("all null values", null_result["details"].lower())


class TestDiagnosticsDataGaps(unittest.TestCase):
    """Test detect_data_gaps function with various gap scenarios."""
    
    def test_detects_30_minute_gap(self):
        """Test that a 30-minute gap is detected (beyond 15-minute threshold)."""
        data = polars.DataFrame({
            "SensorReadingUTC": [1700000000, 1700001800],  # 30-min gap
            "Source": ["Coris", "Coris"],
            "SensorID": ["sensor-1", "sensor-1"],
            "SensorName": ["Sensor 1", "Sensor 1"],
        }).cast({"SensorReadingUTC": polars.Int64})
        
        gaps = validation.detect_data_gaps(data, expected_interval_minutes=15)
        
        self.assertEqual(gaps.shape[0], 1)
        self.assertEqual(gaps["gap_minutes"][0], 30.0)

    def test_no_gaps_within_threshold(self):
        """Test that readings within 15-minute intervals don't trigger gaps."""
        data = polars.DataFrame({
            "SensorReadingUTC": [1700000000, 1700000900, 1700001800],  # 15-min intervals
            "Source": ["Coris", "Coris", "Coris"],
            "SensorID": ["sensor-1", "sensor-1", "sensor-1"],
            "SensorName": ["Sensor 1", "Sensor 1", "Sensor 1"],
        }).cast({"SensorReadingUTC": polars.Int64})
        
        gaps = validation.detect_data_gaps(data, expected_interval_minutes=15)
        
        self.assertEqual(gaps.shape[0], 0)

    def test_multiple_gaps_multiple_sensors(self):
        """Test detection of multiple gaps across different sensors."""
        data = polars.DataFrame({
            "SensorReadingUTC": [
                1700000000, 1700003600,  # Sensor 1: 60-min gap
                1700000000, 1700001800,  # Sensor 2: 30-min gap
            ],
            "Source": ["Coris", "Coris", "Coris", "Coris"],
            "SensorID": ["sensor-1", "sensor-1", "sensor-2", "sensor-2"],
            "SensorName": ["Sensor 1", "Sensor 1", "Sensor 2", "Sensor 2"],
        }).cast({"SensorReadingUTC": polars.Int64})
        
        gaps = validation.detect_data_gaps(data, expected_interval_minutes=15)
        
        self.assertEqual(gaps.shape[0], 2)
        # Check both gaps are detected with correct durations
        gap_minutes = sorted(gaps["gap_minutes"].to_list())
        self.assertEqual(gap_minutes, [30.0, 60.0])

    def test_empty_dataframe_returns_empty(self):
        """Test that empty input returns empty gaps DataFrame."""
        empty_data = polars.DataFrame(schema={
            "SensorReadingUTC": polars.Int64,
            "Source": polars.String,
            "SensorID": polars.String,
            "SensorName": polars.String,
        })
        
        gaps = validation.detect_data_gaps(empty_data)
        
        self.assertTrue(gaps.is_empty())


class TestDiagnosticsAlerts(unittest.TestCase):
    """Test detect_alerts function with various threshold violations."""
    
    def test_detects_temperature_below_min(self):
        """Test detection of temperature below minimum threshold."""
        data = polars.DataFrame({
            "SensorReadingUTC": [1700000000],
            "Source": ["Coris"],
            "SensorID": ["sensor-1"],
            "SensorName": ["Sensor 1"],
            "SensorReadingF": [25.0],  # Below 32F threshold
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "SensorReadingF": polars.Float32,
        })
        
        acceptable_range = {"SensorReadingF": [32, 100]}  # Min 32F, Max 100F
        
        alerts = validation.detect_alerts(data, acceptable_range)
        
        self.assertEqual(alerts.shape[0], 1)
        self.assertEqual(alerts["alert_type"][0], "BELOW_MIN")
        self.assertEqual(alerts["reading_value"][0], 25.0)

    def test_detects_temperature_above_max(self):
        """Test detection of temperature above maximum threshold."""
        data = polars.DataFrame({
            "SensorReadingUTC": [1700000000],
            "Source": ["Coris"],
            "SensorID": ["sensor-1"],
            "SensorName": ["Sensor 1"],
            "SensorReadingF": [110.0],  # Above 100F threshold
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "SensorReadingF": polars.Float32,
        })
        
        acceptable_range = {"SensorReadingF": [32, 100]}
        
        alerts = validation.detect_alerts(data, acceptable_range)
        
        self.assertEqual(alerts.shape[0], 1)
        self.assertEqual(alerts["alert_type"][0], "ABOVE_MAX")

    def test_detects_humidity_out_of_range(self):
        """Test detection of humidity outside acceptable range."""
        data = polars.DataFrame({
            "SensorReadingUTC": [1700000000, 1700000900],
            "Source": ["Coris", "Coris"],
            "SensorID": ["sensor-1", "sensor-2"],
            "SensorName": ["Sensor 1", "Sensor 2"],
            "SensorReadingRh": [15.0, 95.0],  # One below, one above
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "SensorReadingRh": polars.Float32,
        })
        
        acceptable_range = {"SensorReadingRh": [20, 80]}  # 20-80% RH
        
        alerts = validation.detect_alerts(data, acceptable_range)
        
        self.assertEqual(alerts.shape[0], 2)
        alert_types = set(alerts["alert_type"].to_list())
        self.assertEqual(alert_types, {"BELOW_MIN", "ABOVE_MAX"})

    def test_no_alerts_when_within_range(self):
        """Test that no alerts are generated when readings are within acceptable range."""
        data = polars.DataFrame({
            "SensorReadingUTC": [1700000000],
            "Source": ["Coris"],
            "SensorID": ["sensor-1"],
            "SensorName": ["Sensor 1"],
            "SensorReadingF": [72.0],  # Within 32-100F range
            "SensorReadingRh": [50.0],  # Within 20-80% range
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "SensorReadingF": polars.Float32,
            "SensorReadingRh": polars.Float32,
        })
        
        acceptable_range = {"SensorReadingF": [32, 100], "SensorReadingRh": [20, 80]}
        
        alerts = validation.detect_alerts(data, acceptable_range)
        
        self.assertTrue(alerts.is_empty())

    def test_no_alerts_with_empty_thresholds(self):
        """Test that no alerts are generated when thresholds are empty."""
        data = polars.DataFrame({
            "SensorReadingUTC": [1700000000],
            "Source": ["Coris"],
            "SensorID": ["sensor-1"],
            "SensorName": ["Sensor 1"],
            "SensorReadingF": [150.0],  # Would be an alert if thresholds were set
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "SensorReadingF": polars.Float32,
        })
        
        acceptable_range = {"SensorReadingF": []}  # No thresholds
        
        alerts = validation.detect_alerts(data, acceptable_range)
        
        self.assertTrue(alerts.is_empty())

    def test_empty_dataframe_returns_empty(self):
        """Test that empty input returns empty alerts DataFrame."""
        empty_data = polars.DataFrame(schema={
            "SensorReadingUTC": polars.Int64,
            "Source": polars.String,
            "SensorID": polars.String,
            "SensorName": polars.String,
            "SensorReadingF": polars.Float32,
        })
        
        acceptable_range = {"SensorReadingF": [32, 100]}
        
        alerts = validation.detect_alerts(empty_data, acceptable_range)
        
        self.assertTrue(alerts.is_empty())


class TestUtcToEstString(unittest.TestCase):
    """Test UTC to EST string conversion."""
    
    def test_converts_utc_to_est(self):
        """Test basic UTC to EST conversion."""
        # Known UTC timestamp: 2024-01-15 12:00:00 UTC
        utc_timestamp = 1705320000
        
        result = validation.utc_to_est_string(utc_timestamp)
        
        self.assertIn("2024-01-15", result)
        self.assertIn("EST", result)

    def test_handles_none_input(self):
        """Test that None input returns None."""
        result = validation.utc_to_est_string(None)
        
        self.assertIsNone(result)


class TestValidateSensorsEdgeCases(unittest.TestCase):
    """Test edge cases and pass scenarios in validate_sensors."""
    
    @classmethod
    def setUpClass(cls):
        cls.logger = create_test_logger()
    
    def test_none_acceptable_range_defaults_to_empty(self):
        """Test that passing None for acceptable_range uses empty defaults."""
        data = create_valid_sensor_data()
        
        # Should not raise - None acceptable_range should be handled
        results = validation.validate_sensors(
            sensors=data,
            acceptable_range=None,  # Test the None case
            logger=self.logger,
            step="test_none_range",
        )
        
        self.assertIsInstance(results, list)
        self.assertTrue(len(results) > 0)
    
    def test_none_historical_defaults_to_empty(self):
        """Test that passing None for historical uses empty DataFrame."""
        data = create_valid_sensor_data()
        
        results = validation.validate_sensors(
            sensors=data,
            historical=None,  # Test the None case
            acceptable_range=DEFAULT_ACCEPTABLE_RANGE,
            logger=self.logger,
            step="test_none_historical",
        )
        
        self.assertIsInstance(results, list)
        # Should not have no_missing_sensors test since historical is empty
        test_names = [r["test_name"] for r in results]
        self.assertNotIn("no_missing_sensors", test_names)
    
    def test_reading_interval_pass_with_logger(self):
        """Test that reading_interval_check logs on PASS (covers line 232)."""
        # Data with SecondsFromPrior column that passes (< 15 minutes)
        data = polars.DataFrame({
            "SensorReadingUTC": [1700000000, 1700000600],
            "QueryUTC": [1700000000, 1700000600],
            "Source": ["Coris", "Coris"],
            "DeviceID": ["coris:123", "coris:123"],
            "DeviceName": ["Device 1", "Device 1"],
            "SensorID": ["coris:123-1", "coris:123-1"],
            "SensorName": ["Sensor 1", "Sensor 1"],
            "SensorType": ["Temperature", "Temperature"],
            "Historical": [False, False],
            "SensorReadingF": [72.5, 73.0],
            "SensorReadingUTC_SecondsFromPrior": [None, 600],  # 10 minutes, passes check
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
            "SensorReadingUTC_SecondsFromPrior": polars.Int64,
        })
        
        results = validation.validate_sensors(
            sensors=data,
            acceptable_range=DEFAULT_ACCEPTABLE_RANGE,
            logger=self.logger,
            step="test_interval_pass",
        )
        
        interval_result = next(r for r in results if r["test_name"] == "reading_interval_check")
        self.assertEqual(interval_result["result"], "PASS")
        self.assertIn("within 15 minutes", interval_result["details"])
    
    def test_no_missing_sensors_pass_with_logger(self):
        """Test that no_missing_sensors check logs on PASS (covers line 252-253)."""
        current_data = polars.DataFrame({
            "SensorReadingUTC": [1700000000],
            "QueryUTC": [1700000000],
            "Source": ["Coris"],
            "DeviceID": ["coris:123"],
            "DeviceName": ["Device 1"],
            "SensorID": ["coris:123-1"],
            "SensorName": ["Sensor 1"],
            "SensorType": ["Temperature"],
            "Historical": [False],
            "SensorReadingF": [72.5],
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
        })
        
        # Historical has the same sensor - nothing missing
        historical_data = polars.DataFrame({
            "SensorReadingUTC": [1699900000],
            "QueryUTC": [1699900000],
            "Source": ["Coris"],
            "DeviceID": ["coris:123"],
            "DeviceName": ["Device 1"],
            "SensorID": ["coris:123-1"],  # Same sensor ID
            "SensorName": ["Sensor 1"],
            "SensorType": ["Temperature"],
            "Historical": [True],
            "SensorReadingF": [72.0],
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
        })
        
        results = validation.validate_sensors(
            sensors=current_data,
            historical=historical_data,
            acceptable_range=DEFAULT_ACCEPTABLE_RANGE,
            logger=self.logger,
            step="test_no_missing",
        )
        
        missing_result = next(r for r in results if r["test_name"] == "no_missing_sensors")
        self.assertEqual(missing_result["result"], "PASS")
        self.assertIn("All sensors from historical data are still present", missing_result["details"])
    
    def test_no_available_columns_for_null_check(self):
        """Test behavior when no expected columns are present for null check (covers line 153)."""
        # DataFrame with none of the expected columns
        weird_data = polars.DataFrame({
            "SomeOtherColumn": ["value1", "value2"],
            "AnotherColumn": [1, 2],
        })
        
        results = validation.validate_sensors(
            sensors=weird_data,
            acceptable_range=DEFAULT_ACCEPTABLE_RANGE,
            logger=self.logger,
            step="test_no_cols",
        )
        
        # Should still get non_null_values result with missing_count = 0
        null_result = next(r for r in results if r["test_name"] == "non_null_values")
        self.assertEqual(null_result["result"], "PASS")


class TestDetectAlertsEdgeCases(unittest.TestCase):
    """Test edge cases in detect_alerts."""
    
    def test_reading_type_not_in_columns(self):
        """Test that detect_alerts skips reading types not in the DataFrame (covers line 363)."""
        # Data without SensorReadingRh column
        data = polars.DataFrame({
            "SensorReadingUTC": [1700000000],
            "Source": ["Coris"],
            "SensorID": ["sensor-1"],
            "SensorName": ["Sensor 1"],
            "SensorReadingF": [72.0],  # Only has F, not Rh
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "SensorReadingF": polars.Float32,
        })
        
        # Request alerts for both F and Rh, but Rh isn't in data
        acceptable_range = {
            "SensorReadingF": [32, 100],
            "SensorReadingRh": [20, 80],  # This column doesn't exist in data
        }
        
        alerts = validation.detect_alerts(data, acceptable_range)
        
        # Should complete without error and return empty (all readings within range)
        self.assertTrue(alerts.is_empty())


class TestGenerateDiagnosticsReport(unittest.TestCase):
    """Test the generate_validation_results function."""
    
    @classmethod
    def setUpClass(cls):
        cls.logger = create_test_logger()
        cls.test_data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data_diagnostics_test')
        if os.path.exists(cls.test_data_path):
            import shutil
            shutil.rmtree(cls.test_data_path)
        os.makedirs(cls.test_data_path, exist_ok=True)
    
    @classmethod
    def tearDownClass(cls):
        """Clean up test data directory."""
        if os.path.exists(cls.test_data_path):
            import shutil
            shutil.rmtree(cls.test_data_path)
    
    def setUp(self):
        """Clear CSV files before each test."""
        for f in ["validation-results.csv", "validation-detail.csv"]:
            path = os.path.join(self.test_data_path, f)
            if os.path.exists(path):
                os.remove(path)
    
    def test_generates_report_with_no_issues(self):
        """Test report generation with clean data (no gaps, no alerts)."""
        data = polars.DataFrame({
            "SensorReadingUTC": [1700000000, 1700000900],  # 15-min interval, no gap
            "QueryUTC": [1700000000, 1700000900],
            "Source": ["Coris", "Coris"],
            "DeviceID": ["coris:123", "coris:123"],
            "DeviceName": ["Device 1", "Device 1"],
            "SensorID": ["coris:123-1", "coris:123-1"],
            "SensorName": ["Sensor 1", "Sensor 1"],
            "SensorType": ["Temperature", "Temperature"],
            "Historical": [False, False],
            "SensorReadingF": [72.5, 73.0],  # Within range
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
        })
        
        validation_results = []
        
        validation.generate_validation_results(
            sensors=data,
            validation_results=validation_results,
            acceptable_range=DEFAULT_ACCEPTABLE_RANGE,
            data_path=self.test_data_path,
            logger=self.logger,
            step="test_clean",
        )
        
        # Check validation CSV was created
        self.assertTrue(os.path.exists(os.path.join(self.test_data_path, "validation-results.csv")))
        
        # Should have results for data_gaps and alerts
        test_names = [r["test_name"] for r in validation_results]
        self.assertTrue(any("data_gaps" in name for name in test_names))
        self.assertTrue(any("alerts" in name for name in test_names))
    
    def test_generates_report_with_gaps(self):
        """Test report generation with data gaps."""
        data = polars.DataFrame({
            "SensorReadingUTC": [1700000000, 1700003600],  # 60-min gap
            "QueryUTC": [1700000000, 1700003600],
            "Source": ["Coris", "Coris"],
            "DeviceID": ["coris:123", "coris:123"],
            "DeviceName": ["Device 1", "Device 1"],
            "SensorID": ["coris:123-1", "coris:123-1"],
            "SensorName": ["Sensor 1", "Sensor 1"],
            "SensorType": ["Temperature", "Temperature"],
            "Historical": [False, False],
            "SensorReadingF": [72.5, 73.0],
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
        })
        
        validation_results = []
        
        validation.generate_validation_results(
            sensors=data,
            validation_results=validation_results,
            acceptable_range=DEFAULT_ACCEPTABLE_RANGE,
            data_path=self.test_data_path,
            logger=self.logger,
            step="test_gaps",
        )
        
        # Check validation-detail CSV was created with gap events
        detail_path = os.path.join(self.test_data_path, "validation-detail.csv")
        self.assertTrue(os.path.exists(detail_path))
        
        detail_df = polars.read_csv(detail_path)
        self.assertTrue(detail_df.filter(polars.col("event") == "DATA_GAP").shape[0] > 0)
    
    def test_generates_report_with_alerts(self):
        """Test report generation with threshold alerts - alerts are tracked in validation_results but not written to validation-detail.csv."""
        data = polars.DataFrame({
            "SensorReadingUTC": [1700000000, 1700000900],
            "QueryUTC": [1700000000, 1700000900],
            "Source": ["Coris", "Coris"],
            "DeviceID": ["coris:123", "coris:123"],
            "DeviceName": ["Device 1", "Device 1"],
            "SensorID": ["coris:123-1", "coris:123-1"],
            "SensorName": ["Sensor 1", "Sensor 1"],
            "SensorType": ["Temperature", "Temperature"],
            "Historical": [False, False],
            "SensorReadingF": [25.0, 110.0],  # Below min, above max
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
        })
        
        validation_results = []
        
        validation.generate_validation_results(
            sensors=data,
            validation_results=validation_results,
            acceptable_range=DEFAULT_ACCEPTABLE_RANGE,
            data_path=self.test_data_path,
            logger=self.logger,
            step="test_alerts",
        )
        
        # Since there are no gaps, validation-detail.csv should not exist
        # But alerts should still be tracked in validation_results
        alert_results = [r for r in validation_results if "alerts" in r["test_name"]]
        self.assertTrue(len(alert_results) > 0)
    
    def test_appends_to_existing_csv(self):
        """Test that report appends to existing CSV files."""
        data = create_valid_sensor_data()
        
        # Run first report
        validation_results1 = []
        validation.generate_validation_results(
            sensors=data,
            validation_results=validation_results1,
            acceptable_range=DEFAULT_ACCEPTABLE_RANGE,
            data_path=self.test_data_path,
            logger=self.logger,
            step="run1",
        )
        
        first_count = polars.read_csv(
            os.path.join(self.test_data_path, "validation-results.csv")
        ).shape[0]
        
        # Run second report
        validation_results2 = []
        validation.generate_validation_results(
            sensors=data,
            validation_results=validation_results2,
            acceptable_range=DEFAULT_ACCEPTABLE_RANGE,
            data_path=self.test_data_path,
            logger=self.logger,
            step="run2",
        )
        
        second_count = polars.read_csv(
            os.path.join(self.test_data_path, "validation-results.csv")
        ).shape[0]
        
        # Should have more rows after second run
        self.assertGreater(second_count, first_count)

    def test_appends_events_to_existing_detail_csv(self):
        """Test that validation-detail.csv appends events when run multiple times (covers line 588)."""
        # Data with gaps (60-min gap will generate DATA_GAP events)
        data_with_gaps = polars.DataFrame({
            "SensorReadingUTC": [1700000000, 1700003600],  # 60-min gap
            "QueryUTC": [1700000000, 1700003600],
            "Source": ["Coris", "Coris"],
            "DeviceID": ["coris:123", "coris:123"],
            "DeviceName": ["Device 1", "Device 1"],
            "SensorID": ["coris:123-1", "coris:123-1"],
            "SensorName": ["Sensor 1", "Sensor 1"],
            "SensorType": ["Temperature", "Temperature"],
            "Historical": [False, False],
            "SensorReadingF": [72.5, 73.0],  # Within range, but has gap
        }).cast({
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "SensorReadingF": polars.Float32,
        })
        
        # First run - creates validation-detail.csv
        validation_results1 = []
        validation.generate_validation_results(
            sensors=data_with_gaps,
            validation_results=validation_results1,
            acceptable_range=DEFAULT_ACCEPTABLE_RANGE,
            data_path=self.test_data_path,
            logger=self.logger,
            step="run1",
        )
        
        detail_path = os.path.join(self.test_data_path, "validation-detail.csv")
        self.assertTrue(os.path.exists(detail_path))
        first_count = polars.read_csv(detail_path).shape[0]
        
        # Second run - should append to existing validation-detail.csv
        validation_results2 = []
        validation.generate_validation_results(
            sensors=data_with_gaps,
            validation_results=validation_results2,
            acceptable_range=DEFAULT_ACCEPTABLE_RANGE,
            data_path=self.test_data_path,
            logger=self.logger,
            step="run2",
        )
        
        second_count = polars.read_csv(detail_path).shape[0]
        
        # Should have doubled the events
        self.assertEqual(second_count, first_count * 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
