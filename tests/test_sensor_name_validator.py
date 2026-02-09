# Tests for sensor name validator
# Run with: python tests/test_sensor_name_validator.py
# Or: pytest tests/test_sensor_name_validator.py -v

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import unittest
import tempfile
import polars
import logging

from modules import sensor_name_validator


# Create a simple logger for tests
def create_test_logger():
    """Create a simple logger for tests that captures output."""
    logger = logging.getLogger("test_sensor_name_validator")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        logger.addHandler(handler)
    return logger


class TestIsValidSensorName(unittest.TestCase):
    """Test the is_valid_sensor_name function with various inputs."""

    def test_valid_names(self):
        """Test that valid sensor names pass validation."""
        valid_names = [
            # Peabody, YPM, Floor 1, Room 104, SE, Top position
            "PYPM__0100104SET____",
            # Library, Beinecke, Floor 2, Room 203, West (W_ is proper 2-char section)
            "LBRBL_0200203W______",
            # Peabody, YPM, Floor 3, Room 302, no section, floater
            "PYPM__0300302______F",
            # Art Gallery, YCBA, Floor 1, Room 101, NE corner
            "AYCBA_0100101NE_____",
            # British Art, YCBA, Basement 1, Room 001, Center Top
            "BYCBA_B1B0001CT_____",
            # Shared Spaces, KGL, Lower Level, Room A0123, no section
            "SKGL__LLA0123_______",
            # IPCH, ESC, Floor 10, Room 99999, SW, position A, shelf 123
            "IESC__1099999SWA123_",
            # Art Gallery alternate, FAST, Roof, Room 00001
            "GFAST_RF00001_______",
        ]

        for name in valid_names:
            is_valid, errors = sensor_name_validator.is_valid_sensor_name(name)
            self.assertTrue(is_valid, f"Expected valid: '{name}', but got errors: {errors}")
            self.assertEqual(errors, [], f"Expected no errors for '{name}', got: {errors}")

    def test_invalid_length_too_short(self):
        """Test that names that are too short fail validation."""
        short_names = [
            "PYPM__01001",  # 11 chars
            "LBRBL_02",     # 8 chars
            "",             # empty
            "A",            # 1 char
        ]

        for name in short_names:
            is_valid, errors = sensor_name_validator.is_valid_sensor_name(name)
            self.assertFalse(is_valid, f"Expected invalid (too short): '{name}'")
            self.assertTrue(any("Length" in e or "exactly 20" in e for e in errors),
                          f"Expected length error for '{name}', got: {errors}")

    def test_invalid_length_too_long(self):
        """Test that names that are too long fail validation."""
        long_names = [
            "PYPM__0100104SET_____",   # 21 chars
            "LBRBL_0200203W________",  # 22 chars
            "PYPM__0100104SET____ Temp YPM 104_D444",  # Valid prefix but extra text
            "PYPM__0300302SET____ Temp YPM 302 west_D0AA",  # Another edge case
        ]

        for name in long_names:
            is_valid, errors = sensor_name_validator.is_valid_sensor_name(name)
            self.assertFalse(is_valid, f"Expected invalid (too long): '{name}'")
            self.assertTrue(any("Length" in e or "exactly 20" in e for e in errors),
                          f"Expected length error for '{name}', got: {errors}")

    def test_invalid_collection_unit(self):
        """Test that invalid collection units fail validation."""
        invalid_collection_names = [
            "XYPM__0100104SET____",  # X is not valid
            "1YPM__0100104SET____",  # digit not valid
            " YPM__0100104SET____",  # space not valid
            "aYPM__0100104SET____",  # lowercase not valid
        ]

        for name in invalid_collection_names:
            is_valid, errors = sensor_name_validator.is_valid_sensor_name(name)
            self.assertFalse(is_valid, f"Expected invalid (collection unit): '{name}'")
            self.assertTrue(any("collection unit" in e.lower() for e in errors),
                          f"Expected collection unit error for '{name}', got: {errors}")

    def test_nonstandard_floor_now_valid(self):
        """Test that non-standard floor codes pass with relaxed validation."""
        names_with_nonstandard_floors = [
            "PYPM__XX00104SET____",  # XX - alphanumeric, now valid
            "PYPM__AB00104SET____",  # AB - alphanumeric, now valid
            "PYPM__0000104SET____",  # 00 - numeric, valid
            "PYPM__ZZ00104SET____",  # ZZ - alphanumeric, now valid
        ]

        for name in names_with_nonstandard_floors:
            is_valid, errors = sensor_name_validator.is_valid_sensor_name(name)
            self.assertTrue(is_valid, f"Expected valid (relaxed floor): '{name}', but got errors: {errors}")

    def test_nonstandard_floater_flag_now_valid(self):
        """Test that non-standard floater flags pass with relaxed validation."""
        names_with_nonstandard_flags = [
            "PYPM__0100104SET___X",  # X - alphanumeric, now valid
            "PYPM__0100104SET___1",  # digit, now valid
            "PYPM__0100104SET___f",  # lowercase f, now valid
            "PYPM__0100104SET___Z",  # Z - alphanumeric, now valid
        ]

        for name in names_with_nonstandard_flags:
            is_valid, errors = sensor_name_validator.is_valid_sensor_name(name)
            self.assertTrue(is_valid, f"Expected valid (relaxed floater): '{name}', but got errors: {errors}")

    def test_real_invalid_examples(self):
        """Test with real invalid examples from the data."""
        invalid_examples = [
            ("conserv:307:c000172:Temperature", "API ID format"),
            ("RH_FLOATER_849259 dead batt", "Informal naming"),
            ("980D Unnamed Humid Sensor", "Informal naming"),
            ("Temp Floater 3_980C", "Informal naming"),
            ("Replacement A616", "Informal naming"),
            ("Inactive_00cd92", "Informal naming"),
            ("RX Station 1_RH", "Device name format"),
            ("C145 Structural_Temperature", "Device name format"),
        ]

        for name, reason in invalid_examples:
            is_valid, errors = sensor_name_validator.is_valid_sensor_name(name)
            self.assertFalse(is_valid, f"Expected invalid ({reason}): '{name}'")
            self.assertTrue(len(errors) > 0, f"Expected errors for '{name}'")

    def test_none_input(self):
        """Test that None input fails validation."""
        is_valid, errors = sensor_name_validator.is_valid_sensor_name(None)
        self.assertFalse(is_valid)
        self.assertTrue(any("None" in e for e in errors))

    def test_non_string_input(self):
        """Test that non-string input fails validation."""
        non_strings = [123, 12.34, [], {}, True]

        for value in non_strings:
            is_valid, errors = sensor_name_validator.is_valid_sensor_name(value)
            self.assertFalse(is_valid, f"Expected invalid for type: {type(value)}")
            self.assertTrue(any("not a string" in e for e in errors),
                          f"Expected type error for {value}, got: {errors}")


class TestFilterInvalidSensors(unittest.TestCase):
    """Test the filter_invalid_sensors function with per-source validation."""

    @classmethod
    def setUpClass(cls):
        cls.logger = create_test_logger()

    def test_conserv_validates_device_name(self):
        """Test that Conserv sensors are validated using DeviceName column."""
        df = polars.DataFrame({
            "SensorName": [
                "conserv:307:c000172:Temperature",  # API format (not Yale convention)
                "conserv:307:c000173:RH",            # API format (not Yale convention)
            ],
            "DeviceName": [
                "PYPM__0100104SET____",  # valid Yale convention
                "Bad Device Name",       # invalid
            ],
            "SensorID": ["s1", "s2"],
            "Source": ["Conserv", "Conserv"],
            "Value": [1.0, 2.0],
        })

        valid_df, invalid_df = sensor_name_validator.filter_invalid_sensors(
            df=df, logger=self.logger
        )

        self.assertEqual(valid_df.shape[0], 1)
        self.assertEqual(invalid_df.shape[0], 1)
        self.assertEqual(valid_df["DeviceName"].to_list(), ["PYPM__0100104SET____"])
        self.assertEqual(invalid_df["DeviceName"].to_list(), ["Bad Device Name"])

    def test_coris_validates_first_20_chars_of_sensor_name(self):
        """Test that Coris sensors validate the first 20 chars of SensorName."""
        df = polars.DataFrame({
            "SensorName": [
                "PYPM__0100104SET____ RH YPM 104_D444",  # first 20 chars valid
                "0bad_name_not_valid_ some suffix here",   # first 20 chars invalid ('0' not valid collection unit)
                "LBRBL_0200203W______ Temp BRBL 203",     # first 20 chars valid
            ],
            "DeviceName": [
                "YPM 104 D444",
                "Some Device",
                "BRBL 203",
            ],
            "SensorID": ["s1", "s2", "s3"],
            "Source": ["Coris", "Coris", "Coris"],
            "Value": [1.0, 2.0, 3.0],
        })

        valid_df, invalid_df = sensor_name_validator.filter_invalid_sensors(
            df=df, logger=self.logger
        )

        self.assertEqual(valid_df.shape[0], 2)
        self.assertEqual(invalid_df.shape[0], 1)
        valid_names = valid_df["SensorName"].to_list()
        self.assertIn("PYPM__0100104SET____ RH YPM 104_D444", valid_names)
        self.assertIn("LBRBL_0200203W______ Temp BRBL 203", valid_names)

    def test_licor_excluded_entirely(self):
        """Test that LI-COR sensors are excluded entirely (all treated as invalid)."""
        df = polars.DataFrame({
            "SensorName": [
                "RX Station 1_RH",
                "RX Station 2_Temperature",
            ],
            "DeviceName": [
                "RX Station 1",
                "RX Station 2",
            ],
            "SensorID": ["s1", "s2"],
            "Source": ["LI-COR", "LI-COR"],
            "Value": [1.0, 2.0],
        })

        valid_df, invalid_df = sensor_name_validator.filter_invalid_sensors(
            df=df, logger=self.logger
        )

        self.assertEqual(valid_df.shape[0], 0)
        self.assertEqual(invalid_df.shape[0], 2)

    def test_mixed_sources(self):
        """Test filter with a mix of Conserv, Coris, and LI-COR sensors."""
        df = polars.DataFrame({
            "SensorName": [
                "conserv:307:c000172:Temperature",            # Conserv - validated via DeviceName
                "PYPM__0100104SET____ RH YPM 104_D444",      # Coris - first 20 valid
                "RX Station 1_RH",                            # LI-COR - excluded
                "conserv:308:c000200:RH",                     # Conserv - validated via DeviceName
                "0bad_coris_name_____ some suffix RH",        # Coris - first 20 invalid ('0' not valid collection unit)
            ],
            "DeviceName": [
                "PYPM__0300302______F",  # valid for Conserv
                "YPM 104 D444",          # irrelevant for Coris
                "RX Station 1",          # irrelevant for LI-COR
                "Bad Conserv Name",      # invalid for Conserv
                "Some Device",           # irrelevant for Coris
            ],
            "SensorID": ["s1", "s2", "s3", "s4", "s5"],
            "Source": ["Conserv", "Coris", "LI-COR", "Conserv", "Coris"],
            "Value": [1.0, 2.0, 3.0, 4.0, 5.0],
        })

        valid_df, invalid_df = sensor_name_validator.filter_invalid_sensors(
            df=df, logger=self.logger
        )

        # Valid: 1 Conserv (valid DeviceName) + 1 Coris (valid first 20)
        self.assertEqual(valid_df.shape[0], 2)
        # Invalid: 1 Conserv (bad DeviceName) + 1 Coris (bad first 20) + 1 LI-COR (excluded)
        self.assertEqual(invalid_df.shape[0], 3)

        valid_sources = valid_df["Source"].to_list()
        self.assertIn("Conserv", valid_sources)
        self.assertIn("Coris", valid_sources)
        self.assertNotIn("LI-COR", valid_sources)

    def test_filter_empty_dataframe(self):
        """Test filter with empty DataFrame."""
        df = polars.DataFrame({"SensorName": [], "Source": [], "DeviceName": [], "Value": []})

        valid_df, invalid_df = sensor_name_validator.filter_invalid_sensors(
            df=df, logger=self.logger
        )

        self.assertEqual(valid_df.shape[0], 0)
        self.assertEqual(invalid_df.shape[0], 0)

    def test_filter_missing_source_column(self):
        """Test filter with missing Source column returns original as valid."""
        df = polars.DataFrame({
            "SensorName": ["PYPM__0100104SET____", "invalid"],
            "Value": [1.0, 2.0],
        })

        valid_df, invalid_df = sensor_name_validator.filter_invalid_sensors(
            df=df, logger=self.logger
        )

        # Should return original df as valid when Source column is missing
        self.assertEqual(valid_df.shape[0], 2)
        self.assertEqual(invalid_df.shape[0], 0)

    def test_conserv_all_valid(self):
        """Test filter when all Conserv sensors have valid DeviceNames."""
        df = polars.DataFrame({
            "SensorName": [
                "conserv:307:c000172:Temperature",
                "conserv:308:c000173:RH",
            ],
            "DeviceName": [
                "PYPM__0100104SET____",
                "LBRBL_0200203W______",
            ],
            "SensorID": ["s1", "s2"],
            "Source": ["Conserv", "Conserv"],
            "Value": [1.0, 2.0],
        })

        valid_df, invalid_df = sensor_name_validator.filter_invalid_sensors(
            df=df, logger=self.logger
        )

        self.assertEqual(valid_df.shape[0], 2)
        self.assertEqual(invalid_df.shape[0], 0)

    def test_coris_all_invalid(self):
        """Test filter when all Coris sensors have invalid SensorNames."""
        df = polars.DataFrame({
            "SensorName": [
                "invalid_sensor_1_pad",
                "invalid_sensor_2_pad",
            ],
            "DeviceName": ["d1", "d2"],
            "SensorID": ["s1", "s2"],
            "Source": ["Coris", "Coris"],
            "Value": [1.0, 2.0],
        })

        valid_df, invalid_df = sensor_name_validator.filter_invalid_sensors(
            df=df, logger=self.logger
        )

        self.assertEqual(valid_df.shape[0], 0)
        self.assertEqual(invalid_df.shape[0], 2)


class TestGenerateRejectedSensorsReport(unittest.TestCase):
    """Test the generate_rejected_sensors_report function."""

    @classmethod
    def setUpClass(cls):
        cls.logger = create_test_logger()

    def test_generate_report_creates_csv(self):
        """Test that report generates a CSV file."""
        df = polars.DataFrame({
            "SensorName": ["invalid_1", "invalid_1", "invalid_2"],  # 2 unique sensors
            "DeviceName": ["dev_1", "dev_1", "dev_2"],
            "SensorID": ["s1", "s1", "s2"],
            "DeviceID": ["d1", "d1", "d2"],
            "Source": ["Coris", "Coris", "Conserv"],
            "Value": [1.0, 2.0, 3.0],
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = sensor_name_validator.generate_rejected_sensors_report(
                invalid_df=df,
                data_path=tmpdir,
                logger=self.logger
            )

            self.assertIsNotNone(csv_path)
            self.assertTrue(os.path.exists(csv_path))

            # Read and verify the CSV
            report = polars.read_csv(csv_path)
            self.assertEqual(report.shape[0], 2)  # 2 unique sensors
            self.assertIn("SensorName", report.columns)
            self.assertIn("DeviceName", report.columns)
            self.assertIn("ValidationErrors", report.columns)
            self.assertIn("SampleCount", report.columns)
            self.assertIn("DateRejected", report.columns)

    def test_generate_report_empty_df(self):
        """Test that empty DataFrame returns None."""
        df = polars.DataFrame({
            "SensorName": [],
            "SensorID": [],
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = sensor_name_validator.generate_rejected_sensors_report(
                invalid_df=df,
                data_path=tmpdir,
                logger=self.logger
            )

            self.assertIsNone(csv_path)

    def test_generate_report_sample_counts(self):
        """Test that sample counts are correct."""
        df = polars.DataFrame({
            "SensorName": ["invalid_1", "invalid_1", "invalid_1", "invalid_2"],
            "DeviceName": ["dev_1", "dev_1", "dev_1", "dev_2"],
            "SensorID": ["s1", "s1", "s1", "s2"],
            "DeviceID": ["d1", "d1", "d1", "d2"],
            "Source": ["Coris", "Coris", "Coris", "Conserv"],
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = sensor_name_validator.generate_rejected_sensors_report(
                invalid_df=df,
                data_path=tmpdir,
                logger=self.logger
            )

            report = polars.read_csv(csv_path)

            # Check sample counts
            sensor_1_row = report.filter(polars.col("SensorName") == "invalid_1")
            sensor_2_row = report.filter(polars.col("SensorName") == "invalid_2")

            self.assertEqual(sensor_1_row["SampleCount"].to_list()[0], 3)
            self.assertEqual(sensor_2_row["SampleCount"].to_list()[0], 1)

    def test_generate_report_licor_error_message(self):
        """Test that LI-COR sensors get appropriate error message in report."""
        df = polars.DataFrame({
            "SensorName": ["RX Station 1_RH"],
            "DeviceName": ["RX Station 1"],
            "SensorID": ["s1"],
            "DeviceID": ["d1"],
            "Source": ["LI-COR"],
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = sensor_name_validator.generate_rejected_sensors_report(
                invalid_df=df,
                data_path=tmpdir,
                logger=self.logger
            )

            report = polars.read_csv(csv_path)
            self.assertEqual(report.shape[0], 1)
            self.assertIn("LI-COR", report["ValidationErrors"].to_list()[0])


class TestRelaxedValidation(unittest.TestCase):
    """Test relaxed validation rules for positions 7-20."""

    def test_any_alphanumeric_section_codes(self):
        """Test that any alphanumeric/underscore section codes pass."""
        for section in ['NE', 'SW', 'CT', '__', 'ZZ', 'Q1', '99', 'XY']:
            name = f"PYPM__0100104{section}_____"
            is_valid, errors = sensor_name_validator.is_valid_sensor_name(name)
            self.assertTrue(is_valid, f"Expected valid for section '{section}': {errors}")

    def test_any_alphanumeric_floor_codes(self):
        """Test that any alphanumeric/underscore floor codes pass."""
        for floor in ['01', 'LL', 'RF', 'XX', 'ZZ', '__', 'Q9', 'AB']:
            name = f"PYPM__{floor}00104_______"
            is_valid, errors = sensor_name_validator.is_valid_sensor_name(name)
            self.assertTrue(is_valid, f"Expected valid for floor '{floor}': {errors}")

    def test_any_alphanumeric_floater_flag(self):
        """Test that any alphanumeric/underscore floater flag passes."""
        for flag in ['F', '_', 'X', '1', 'f', 'Z']:
            name = f"PYPM__0100104______{flag}"
            is_valid, errors = sensor_name_validator.is_valid_sensor_name(name)
            self.assertTrue(is_valid, f"Expected valid for floater flag '{flag}': {errors}")


class TestNameNormalization(unittest.TestCase):
    """Test space-to-underscore normalization in the filter pipeline."""

    @classmethod
    def setUpClass(cls):
        cls.logger = create_test_logger()

    def test_normalize_name_replaces_spaces(self):
        """Test that _normalize_name replaces spaces with underscores."""
        self.assertEqual(sensor_name_validator._normalize_name("PYPM  0100104SE ____"), "PYPM__0100104SE_____")
        self.assertEqual(sensor_name_validator._normalize_name("no spaces here_____"), "no spaces here_____".replace(' ', '_'))

    def test_normalize_name_none(self):
        """Test that _normalize_name handles None."""
        self.assertIsNone(sensor_name_validator._normalize_name(None))

    def test_conserv_with_spaces_passes_after_normalization(self):
        """Test that Conserv DeviceNames with spaces pass validation after normalization."""
        df = polars.DataFrame({
            "SensorName": ["conserv:307:c000172:Temperature"],
            "DeviceName": ["PYPM  0100104SET    "],  # spaces where underscores should be
            "SensorID": ["s1"],
            "Source": ["Conserv"],
            "Value": [1.0],
        })

        valid_df, invalid_df = sensor_name_validator.filter_invalid_sensors(
            df=df, logger=self.logger
        )

        self.assertEqual(valid_df.shape[0], 1)
        self.assertEqual(invalid_df.shape[0], 0)

    def test_coris_with_spaces_passes_after_normalization(self):
        """Test that Coris SensorNames with spaces in first 20 chars pass after normalization."""
        df = polars.DataFrame({
            "SensorName": ["PYPM  0100104SET     RH YPM 104_D444"],  # spaces in first 20
            "DeviceName": ["YPM 104 D444"],
            "SensorID": ["s1"],
            "Source": ["Coris"],
            "Value": [1.0],
        })

        valid_df, invalid_df = sensor_name_validator.filter_invalid_sensors(
            df=df, logger=self.logger
        )

        self.assertEqual(valid_df.shape[0], 1)
        self.assertEqual(invalid_df.shape[0], 0)


if __name__ == "__main__":
    unittest.main()
