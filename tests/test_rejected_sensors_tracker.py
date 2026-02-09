# Tests for rejected sensors tracker
# Run with: python tests/test_rejected_sensors_tracker.py
# Or: pytest tests/test_rejected_sensors_tracker.py -v

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import unittest
import tempfile
import time
import polars
import logging

from modules import rejected_sensors_tracker


def create_test_logger():
    """Create a simple logger for tests that captures output."""
    logger = logging.getLogger("test_rejected_sensors_tracker")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        logger.addHandler(handler)
    return logger


class TestClassifyInvalidSensor(unittest.TestCase):
    """Test the classify_invalid_sensor function for all subcategories."""

    # --- expected_exclusion subcategories ---

    def test_floater_in_sensor_name(self):
        """Sensors with 'floater' in SensorName are expected_exclusion/floater."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Coris",
            sensor_name="RH_FLOATER_849259 dead batt",
            device_name="Some Device",
            validation_errors=["Length is 27, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "expected_exclusion")
        self.assertEqual(sub, "floater")

    def test_floater_case_insensitive(self):
        """Floater detection is case-insensitive."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Conserv",
            sensor_name="Temp Floater 3_980C",
            device_name="Bad Name",
            validation_errors=["Length is 8, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "expected_exclusion")
        self.assertEqual(sub, "floater")

    def test_exhibition_device_name(self):
        """Sensors with DeviceName starting with 'Exh' are expected_exclusion/exhibition."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Conserv",
            sensor_name="conserv:307:c000172:Temperature",
            device_name="Exh Gallery West",
            validation_errors=["Length is 16, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "expected_exclusion")
        self.assertEqual(sub, "exhibition")

    def test_exhibition_with_asterisks_and_wrong_length(self):
        """Exhibition sensor with *Exh* prefix and wrong length is still exhibition."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Conserv",
            sensor_name="conserv:307:c000172:Temperature",
            device_name="*Exh*AOYAG_0100137___NaD_",
            validation_errors=["Length is 25, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "expected_exclusion")
        self.assertEqual(sub, "exhibition")

    def test_exhibition_with_asterisks_variant(self):
        """Another *Exh* prefixed sensor classified as exhibition."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Conserv",
            sensor_name="conserv:308:c000200:Temperature",
            device_name="*Exh*AOYAG_01ScHall_c009148",
            validation_errors=["Length is 27, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "expected_exclusion")
        self.assertEqual(sub, "exhibition")

    def test_obsolete_device_name(self):
        """Sensors with DeviceName starting with 'o' + uppercase are expected_exclusion/obsolete."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Conserv",
            sensor_name="conserv:308:c000200:Temperature",
            device_name="oAOYAG",
            validation_errors=["Length is 6, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "expected_exclusion")
        self.assertEqual(sub, "obsolete")

    def test_obsolete_with_wrong_length(self):
        """Obsolete sensor with wrong length is still classified as obsolete."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Conserv",
            sensor_name="conserv:308:c000200:Temperature",
            device_name="oAOYAG_0100137___GRD_",
            validation_errors=["Length is 21, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "expected_exclusion")
        self.assertEqual(sub, "obsolete")

    def test_obsolete_not_lowercase_second(self):
        """'o' followed by lowercase is NOT obsolete."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Conserv",
            sensor_name="conserv:308:c000200:Temperature",
            device_name="oabcde",
            validation_errors=["Length is 6, must be exactly 20 characters"],
        )
        # Should NOT be obsolete since second char is lowercase
        self.assertNotEqual(sub, "obsolete")

    def test_unassigned_conserv_id(self):
        """DeviceName matching Conserv ID pattern is expected_exclusion/unassigned."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Conserv",
            sensor_name="conserv:307:c007167:Temperature",
            device_name="c007167",
            validation_errors=["Length is 7, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "expected_exclusion")
        self.assertEqual(sub, "unassigned")

    def test_unassigned_another_id(self):
        """Another Conserv ID pattern."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Conserv",
            sensor_name="conserv:308:c008398:RH",
            device_name="c008398",
            validation_errors=["Length is 7, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "expected_exclusion")
        self.assertEqual(sub, "unassigned")

    def test_inactive_in_sensor_name(self):
        """Sensors with 'inactive' in SensorName are expected_exclusion/inactive."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Coris",
            sensor_name="Inactive_00cd92",
            device_name="Some Device",
            validation_errors=["Length is 15, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "expected_exclusion")
        self.assertEqual(sub, "inactive")

    def test_placeholder_unnamed(self):
        """Sensors with 'unnamed' in SensorName are expected_exclusion/placeholder."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Coris",
            sensor_name="980D Unnamed Humid Sensor",
            device_name="Some Device",
            validation_errors=["Length is 24, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "expected_exclusion")
        self.assertEqual(sub, "placeholder")

    def test_placeholder_replacement(self):
        """Sensors with 'replacement' in SensorName are expected_exclusion/placeholder."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Coris",
            sensor_name="Replacement A616",
            device_name="Some Device",
            validation_errors=["Length is 16, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "expected_exclusion")
        self.assertEqual(sub, "placeholder")

    def test_placeholder_no_data(self):
        """Sensors with 'no data' in SensorName are expected_exclusion/placeholder."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Coris",
            sensor_name="Sensor No Data Available",
            device_name="Some Device",
            validation_errors=["Length is 24, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "expected_exclusion")
        self.assertEqual(sub, "placeholder")

    # --- needs_attention subcategories ---

    def test_wrong_length(self):
        """Sensors with length errors are needs_attention/wrong_length."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Conserv",
            sensor_name="conserv:307:c000172:Temperature",
            device_name="Short Name",
            validation_errors=["Length is 10, must be exactly 20 characters"],
        )
        self.assertEqual(cat, "needs_attention")
        self.assertEqual(sub, "wrong_length")

    def test_invalid_collection_unit(self):
        """Sensors with collection unit errors are needs_attention/invalid_collection_unit."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Coris",
            sensor_name="XYPM__0100104SET____ RH YPM 104",
            device_name="YPM 104",
            validation_errors=["Invalid collection unit 'X' at position 1. Valid: A, B, G, I, L, P, S"],
        )
        self.assertEqual(cat, "needs_attention")
        self.assertEqual(sub, "invalid_collection_unit")

    def test_invalid_characters(self):
        """Sensors with invalid characters are needs_attention/invalid_characters."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Coris",
            sensor_name="PYPM??0100104SET____ RH YPM 104",
            device_name="YPM 104",
            validation_errors=["Building code '??010' contains invalid characters"],
        )
        self.assertEqual(cat, "needs_attention")
        self.assertEqual(sub, "invalid_characters")

    def test_invalid_characters_question_mark(self):
        """Names with ? are needs_attention/invalid_characters."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Conserv",
            sensor_name="conserv:307:c000172:Temperature",
            device_name="PYPM??0100104SET____",
            validation_errors=[],
        )
        self.assertEqual(cat, "needs_attention")
        self.assertEqual(sub, "invalid_characters")

    def test_leading_trailing_space(self):
        """Names with leading/trailing whitespace are needs_attention/leading_trailing_space."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Conserv",
            sensor_name="conserv:307:c000172:Temperature",
            device_name=" PYPM__0100104SET___ ",
            validation_errors=["Some other error"],
        )
        self.assertEqual(cat, "needs_attention")
        self.assertEqual(sub, "leading_trailing_space")

    def test_other_fallback(self):
        """Unknown validation failures fall back to needs_attention/other."""
        cat, sub = rejected_sensors_tracker.classify_invalid_sensor(
            source="Coris",
            sensor_name="PYPM__0100104SET____some_extra_stuff",
            device_name="Some Device",
            validation_errors=["Some unknown error"],
        )
        self.assertEqual(cat, "needs_attention")
        self.assertEqual(sub, "other")


class TestUpdateTrackingFile(unittest.TestCase):
    """Test the update_tracking_file function."""

    @classmethod
    def setUpClass(cls):
        cls.logger = create_test_logger()

    def _make_invalid_df(self, sensors):
        """Helper to build an invalid_df from a list of (source, sensor_id, sensor_name, device_name) tuples."""
        return polars.DataFrame({
            "Source": [s[0] for s in sensors],
            "SensorID": [s[1] for s in sensors],
            "SensorName": [s[2] for s in sensors],
            "DeviceName": [s[3] for s in sensors],
            "SensorReadingUTC": [int(time.time())] * len(sensors),
            "SensorReadingF": [72.0] * len(sensors),
        })

    def test_new_sensor_detection(self):
        """New invalid sensors are added with Status='active'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            invalid_df = self._make_invalid_df([
                ("Conserv", "s1", "conserv:307:c000172:Temperature", "Short Name"),
                ("Coris", "s2", "bad_name_not_valid", "Some Device"),
            ])

            new_count, fixed_count, active_count = rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            self.assertEqual(new_count, 2)
            self.assertEqual(fixed_count, 0)
            self.assertEqual(active_count, 2)

            # Verify file contents
            tracking = polars.read_csv(tracking_path)
            self.assertEqual(tracking.shape[0], 2)
            self.assertTrue(all(s == "active" for s in tracking["Status"].to_list()))

    def test_fixed_sensor_detection(self):
        """Sensors that were active but no longer invalid get marked as fixed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            # First run: add 2 sensors
            invalid_df = self._make_invalid_df([
                ("Conserv", "s1", "conserv:307:c000172:Temperature", "Short Name"),
                ("Coris", "s2", "bad_name_not_valid", "Some Device"),
            ])
            rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            # Second run: only s1 still invalid (s2 is fixed)
            invalid_df2 = self._make_invalid_df([
                ("Conserv", "s1", "conserv:307:c000172:Temperature", "Short Name"),
            ])
            new_count, fixed_count, active_count = rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df2,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            self.assertEqual(new_count, 0)
            self.assertEqual(fixed_count, 1)
            self.assertEqual(active_count, 1)

            # Verify file
            tracking = polars.read_csv(tracking_path)
            self.assertEqual(tracking.shape[0], 2)
            fixed_rows = tracking.filter(polars.col("Status") == "fixed")
            self.assertEqual(fixed_rows.shape[0], 1)
            self.assertEqual(fixed_rows["SensorID"].to_list()[0], "s2")
            self.assertIsNotNone(fixed_rows["FixedUTC"].to_list()[0])

    def test_still_active_sensor_update(self):
        """Sensors still invalid get their LastSeenUTC updated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            # First run
            invalid_df = self._make_invalid_df([
                ("Conserv", "s1", "conserv:307:c000172:Temperature", "Short Name"),
            ])
            rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            # Read FirstSeenUTC
            tracking1 = polars.read_csv(tracking_path)
            first_seen = tracking1["FirstSeenUTC"].to_list()[0]

            # Small delay to get different timestamp
            time.sleep(1)

            # Second run with same sensor
            invalid_df2 = self._make_invalid_df([
                ("Conserv", "s1", "conserv:307:c000172:Temperature", "Short Name"),
            ])
            new_count, fixed_count, active_count = rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df2,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            self.assertEqual(new_count, 0)
            self.assertEqual(fixed_count, 0)
            self.assertEqual(active_count, 1)

            # Verify: FirstSeenUTC preserved, LastSeenUTC updated
            tracking2 = polars.read_csv(tracking_path)
            self.assertEqual(tracking2["FirstSeenUTC"].to_list()[0], first_seen)
            self.assertGreaterEqual(tracking2["LastSeenUTC"].to_list()[0], first_seen)
            self.assertEqual(tracking2["Status"].to_list()[0], "active")

    def test_empty_invalid_df(self):
        """Empty invalid_df marks all active as fixed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            # First run: add sensor
            invalid_df = self._make_invalid_df([
                ("Conserv", "s1", "conserv:307:c000172:Temperature", "Short Name"),
            ])
            rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            # Second run: empty invalid_df
            empty_df = polars.DataFrame({
                "Source": [],
                "SensorID": [],
                "SensorName": [],
                "DeviceName": [],
            })
            new_count, fixed_count, active_count = rejected_sensors_tracker.update_tracking_file(
                invalid_df=empty_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            self.assertEqual(new_count, 0)
            self.assertEqual(fixed_count, 1)
            self.assertEqual(active_count, 0)

    def test_no_existing_file(self):
        """First run with no existing tracking file creates it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            self.assertFalse(os.path.exists(tracking_path))

            invalid_df = self._make_invalid_df([
                ("Conserv", "s1", "conserv:307:c000172:Temperature", "Short Name"),
            ])
            new_count, fixed_count, active_count = rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            self.assertTrue(os.path.exists(tracking_path))
            self.assertEqual(new_count, 1)
            self.assertEqual(fixed_count, 0)

    def test_all_fixed_scenario(self):
        """All sensors get fixed between runs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            # Run 1: two sensors
            invalid_df = self._make_invalid_df([
                ("Conserv", "s1", "conserv:307:c000172:Temperature", "Short Name"),
                ("Coris", "s2", "bad_name_not_valid", "Some Device"),
            ])
            rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            # Run 2: completely different sensors (old ones fixed)
            invalid_df2 = self._make_invalid_df([
                ("Conserv", "s3", "conserv:309:c000300:Temperature", "Another Bad"),
            ])
            new_count, fixed_count, active_count = rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df2,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            self.assertEqual(new_count, 1)
            self.assertEqual(fixed_count, 2)
            self.assertEqual(active_count, 1)  # only the new one

            tracking = polars.read_csv(tracking_path)
            self.assertEqual(tracking.shape[0], 3)
            self.assertEqual(
                tracking.filter(polars.col("Status") == "fixed").shape[0], 2
            )
            self.assertEqual(
                tracking.filter(polars.col("Status") == "active").shape[0], 1
            )

    def test_classification_persisted(self):
        """Classification (category/subcategory) is correctly persisted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            invalid_df = self._make_invalid_df([
                ("Coris", "s1", "RH_FLOATER_849259 dead batt", "Some Device"),
                ("Conserv", "s2", "conserv:307:c007167:Temperature", "c007167"),
                ("Conserv", "s3", "conserv:308:c000200:Temperature", "Short Name"),
            ])
            rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            tracking = polars.read_csv(tracking_path)

            # Floater -> expected_exclusion
            floater = tracking.filter(polars.col("SensorID") == "s1")
            self.assertEqual(floater["Category"].to_list()[0], "expected_exclusion")
            self.assertEqual(floater["SubCategory"].to_list()[0], "floater")

            # Conserv ID -> expected_exclusion/unassigned
            unassigned = tracking.filter(polars.col("SensorID") == "s2")
            self.assertEqual(unassigned["Category"].to_list()[0], "expected_exclusion")
            self.assertEqual(unassigned["SubCategory"].to_list()[0], "unassigned")

            # Short name -> needs_attention/wrong_length
            wrong = tracking.filter(polars.col("SensorID") == "s3")
            self.assertEqual(wrong["Category"].to_list()[0], "needs_attention")
            self.assertEqual(wrong["SubCategory"].to_list()[0], "wrong_length")

    def test_multiple_readings_per_sensor(self):
        """Multiple readings from the same sensor ID are grouped correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            # Same sensor ID appears multiple times (multiple readings)
            invalid_df = polars.DataFrame({
                "Source": ["Conserv", "Conserv", "Conserv"],
                "SensorID": ["s1", "s1", "s1"],
                "SensorName": ["conserv:307:c000172:Temperature"] * 3,
                "DeviceName": ["Short Name"] * 3,
                "SensorReadingUTC": [1000, 2000, 3000],
                "SensorReadingF": [72.0, 73.0, 74.0],
            })

            new_count, fixed_count, active_count = rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            # Should only create one tracking row
            self.assertEqual(new_count, 1)
            tracking = polars.read_csv(tracking_path)
            self.assertEqual(tracking.shape[0], 1)


class TestGenerateNeedsAttentionReport(unittest.TestCase):
    """Test the generate_needs_attention_report function."""

    @classmethod
    def setUpClass(cls):
        cls.logger = create_test_logger()

    def test_generates_report(self):
        """Report includes only active needs_attention sensors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")
            output_path = os.path.join(tmpdir, "needs_attention.csv")

            # Create tracking file with mixed data
            tracking = polars.DataFrame({
                "Source": ["Conserv", "Coris", "Conserv", "Coris"],
                "SensorID": ["s1", "s2", "s3", "s4"],
                "SensorName": ["Short", "bad_name", "c007167", "floater"],
                "Category": ["needs_attention", "needs_attention", "expected_exclusion", "expected_exclusion"],
                "SubCategory": ["wrong_length", "invalid_collection_unit", "unassigned", "floater"],
                "Reason": ["Length is 5", "Invalid collection unit", "Conserv ID", "Contains floater"],
                "FirstSeenUTC": [1000, 1000, 1000, 1000],
                "LastSeenUTC": [2000, 2000, 2000, 2000],
                "Status": ["active", "active", "active", "active"],
                "FixedUTC": [None, None, None, None],
            })
            tracking.write_csv(tracking_path)

            result = rejected_sensors_tracker.generate_needs_attention_report(
                tracking_file_path=tracking_path,
                output_path=output_path,
                logger=self.logger,
            )

            self.assertIsNotNone(result)
            self.assertTrue(os.path.exists(output_path))

            report = polars.read_csv(output_path)
            # Only needs_attention + active
            self.assertEqual(report.shape[0], 2)
            self.assertTrue(all(c == "needs_attention" for c in report["Category"].to_list()))

    def test_report_sorted_correctly(self):
        """Report is sorted by Source, SubCategory, SensorName."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")
            output_path = os.path.join(tmpdir, "needs_attention.csv")

            tracking = polars.DataFrame({
                "Source": ["Coris", "Conserv", "Coris", "Conserv"],
                "SensorID": ["s1", "s2", "s3", "s4"],
                "SensorName": ["Z_name", "A_name", "A_name2", "B_name"],
                "Category": ["needs_attention"] * 4,
                "SubCategory": ["wrong_length", "wrong_length", "invalid_characters", "wrong_length"],
                "Reason": ["err"] * 4,
                "FirstSeenUTC": [1000] * 4,
                "LastSeenUTC": [2000] * 4,
                "Status": ["active"] * 4,
                "FixedUTC": [None] * 4,
            })
            tracking.write_csv(tracking_path)

            rejected_sensors_tracker.generate_needs_attention_report(
                tracking_file_path=tracking_path,
                output_path=output_path,
                logger=self.logger,
            )

            report = polars.read_csv(output_path)
            sources = report["Source"].to_list()
            subcats = report["SubCategory"].to_list()

            # Conserv should come before Coris
            conserv_indices = [i for i, s in enumerate(sources) if s == "Conserv"]
            coris_indices = [i for i, s in enumerate(sources) if s == "Coris"]
            self.assertTrue(max(conserv_indices) < min(coris_indices))

    def test_excludes_fixed_sensors(self):
        """Fixed sensors are not included in the report."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")
            output_path = os.path.join(tmpdir, "needs_attention.csv")

            tracking = polars.DataFrame({
                "Source": ["Conserv", "Conserv"],
                "SensorID": ["s1", "s2"],
                "SensorName": ["Short", "Another"],
                "Category": ["needs_attention", "needs_attention"],
                "SubCategory": ["wrong_length", "wrong_length"],
                "Reason": ["Length is 5", "Length is 7"],
                "FirstSeenUTC": [1000, 1000],
                "LastSeenUTC": [2000, 2000],
                "Status": ["active", "fixed"],
                "FixedUTC": [None, 3000],
            })
            tracking.write_csv(tracking_path)

            rejected_sensors_tracker.generate_needs_attention_report(
                tracking_file_path=tracking_path,
                output_path=output_path,
                logger=self.logger,
            )

            report = polars.read_csv(output_path)
            self.assertEqual(report.shape[0], 1)
            self.assertEqual(report["SensorID"].to_list()[0], "s1")

    def test_no_tracking_file(self):
        """Returns None when tracking file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = rejected_sensors_tracker.generate_needs_attention_report(
                tracking_file_path=os.path.join(tmpdir, "nonexistent.csv"),
                output_path=os.path.join(tmpdir, "output.csv"),
                logger=self.logger,
            )
            self.assertIsNone(result)

    def test_no_needs_attention_sensors(self):
        """Returns None when all active sensors are expected_exclusion."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")
            output_path = os.path.join(tmpdir, "needs_attention.csv")

            tracking = polars.DataFrame({
                "Source": ["Conserv"],
                "SensorID": ["s1"],
                "SensorName": ["c007167"],
                "Category": ["expected_exclusion"],
                "SubCategory": ["unassigned"],
                "Reason": ["Conserv ID pattern"],
                "FirstSeenUTC": [1000],
                "LastSeenUTC": [2000],
                "Status": ["active"],
                "FixedUTC": [None],
            })
            tracking.write_csv(tracking_path)

            result = rejected_sensors_tracker.generate_needs_attention_report(
                tracking_file_path=tracking_path,
                output_path=output_path,
                logger=self.logger,
            )
            self.assertIsNone(result)

    def test_report_deduplicates_by_sensor_name(self):
        """Report deduplicates by SensorName, keeping most recent LastSeenUTC."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")
            output_path = os.path.join(tmpdir, "needs_attention.csv")

            # Two SensorIDs with same SensorName (Temperature + RH for same device)
            tracking = polars.DataFrame({
                "Source": ["Conserv", "Conserv", "Conserv"],
                "SensorID": ["s1-temp", "s1-rh", "s2-temp"],
                "SensorName": ["Short Name", "Short Name", "Other Name"],
                "Category": ["needs_attention", "needs_attention", "needs_attention"],
                "SubCategory": ["wrong_length", "wrong_length", "wrong_length"],
                "Reason": ["Length is 10", "Length is 10", "Length is 10"],
                "FirstSeenUTC": [1000, 1000, 1000],
                "LastSeenUTC": [2000, 3000, 2000],
                "Status": ["active", "active", "active"],
                "FixedUTC": [None, None, None],
            })
            tracking.write_csv(tracking_path)

            rejected_sensors_tracker.generate_needs_attention_report(
                tracking_file_path=tracking_path,
                output_path=output_path,
                logger=self.logger,
            )

            report = polars.read_csv(output_path)
            # "Short Name" should appear only once, "Other Name" once = 2 total
            self.assertEqual(report.shape[0], 2)
            sensor_names = report["SensorName"].to_list()
            self.assertEqual(len(set(sensor_names)), 2)
            self.assertIn("Short Name", sensor_names)
            self.assertIn("Other Name", sensor_names)

            # The kept row for "Short Name" should have the most recent LastSeenUTC
            short_row = report.filter(polars.col("SensorName") == "Short Name")
            self.assertEqual(short_row["LastSeenUTC"].to_list()[0], 3000)

    def test_empty_tracking_file(self):
        """Returns None for empty tracking file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")
            output_path = os.path.join(tmpdir, "needs_attention.csv")

            empty = polars.DataFrame(schema=rejected_sensors_tracker.TRACKING_SCHEMA)
            empty.write_csv(tracking_path)

            result = rejected_sensors_tracker.generate_needs_attention_report(
                tracking_file_path=tracking_path,
                output_path=output_path,
                logger=self.logger,
            )
            self.assertIsNone(result)


class TestGenerateSummary(unittest.TestCase):
    """Test the generate_summary function."""

    def test_summary_with_mixed_data(self):
        """Summary returns correct counts for mixed active/fixed data."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            tracking = polars.DataFrame({
                "Source": ["Conserv", "Coris", "Conserv", "Coris"],
                "SensorID": ["s1", "s2", "s3", "s4"],
                "SensorName": ["Short", "bad_name", "c007167", "floater_device"],
                "Category": ["needs_attention", "needs_attention", "expected_exclusion", "expected_exclusion"],
                "SubCategory": ["wrong_length", "invalid_collection_unit", "unassigned", "floater"],
                "Reason": ["Length is 5", "Invalid unit", "Conserv ID", "Floater keyword"],
                "FirstSeenUTC": [1000, 1000, 1000, 1000],
                "LastSeenUTC": [2000, 2000, 2000, 2000],
                "Status": ["active", "fixed", "active", "active"],
                "FixedUTC": [None, 3000, None, None],
            })
            tracking.write_csv(tracking_path)

            summary = rejected_sensors_tracker.generate_summary(tracking_path)

            self.assertEqual(summary["total_tracked"], 4)
            self.assertEqual(summary["total_active"], 3)
            self.assertEqual(summary["total_fixed"], 1)
            self.assertEqual(summary["by_category"]["needs_attention"], 1)
            self.assertEqual(summary["by_category"]["expected_exclusion"], 2)
            self.assertEqual(summary["by_subcategory"]["wrong_length"], 1)
            self.assertEqual(summary["by_subcategory"]["unassigned"], 1)
            self.assertEqual(summary["by_subcategory"]["floater"], 1)
            self.assertEqual(summary["newly_fixed"], 1)

    def test_summary_no_file(self):
        """Summary returns zeros when file doesn't exist."""
        summary = rejected_sensors_tracker.generate_summary("/nonexistent/path.csv")
        self.assertEqual(summary["total_tracked"], 0)
        self.assertEqual(summary["total_active"], 0)
        self.assertEqual(summary["total_fixed"], 0)
        self.assertEqual(summary["by_category"], {})
        self.assertEqual(summary["by_subcategory"], {})

    def test_summary_empty_file(self):
        """Summary returns zeros for empty tracking file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")
            empty = polars.DataFrame(schema=rejected_sensors_tracker.TRACKING_SCHEMA)
            empty.write_csv(tracking_path)

            summary = rejected_sensors_tracker.generate_summary(tracking_path)
            self.assertEqual(summary["total_tracked"], 0)
            self.assertEqual(summary["total_active"], 0)

    def test_summary_all_fixed(self):
        """Summary when all sensors are fixed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            tracking = polars.DataFrame({
                "Source": ["Conserv", "Coris"],
                "SensorID": ["s1", "s2"],
                "SensorName": ["Short", "bad"],
                "Category": ["needs_attention", "needs_attention"],
                "SubCategory": ["wrong_length", "wrong_length"],
                "Reason": ["err", "err"],
                "FirstSeenUTC": [1000, 1000],
                "LastSeenUTC": [2000, 2000],
                "Status": ["fixed", "fixed"],
                "FixedUTC": [3000, 3000],
            })
            tracking.write_csv(tracking_path)

            summary = rejected_sensors_tracker.generate_summary(tracking_path)
            self.assertEqual(summary["total_tracked"], 2)
            self.assertEqual(summary["total_active"], 0)
            self.assertEqual(summary["total_fixed"], 2)
            self.assertEqual(summary["by_category"], {})
            self.assertEqual(summary["newly_fixed"], 2)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases for the tracking system."""

    @classmethod
    def setUpClass(cls):
        cls.logger = create_test_logger()

    def test_empty_invalid_df_no_existing_file(self):
        """Empty invalid_df with no existing file returns all zeros."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            empty_df = polars.DataFrame({
                "Source": [],
                "SensorID": [],
                "SensorName": [],
                "DeviceName": [],
            })
            new_count, fixed_count, active_count = rejected_sensors_tracker.update_tracking_file(
                invalid_df=empty_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            self.assertEqual(new_count, 0)
            self.assertEqual(fixed_count, 0)
            self.assertEqual(active_count, 0)

    def test_sensor_reappears_after_fix(self):
        """A sensor that was fixed can reappear as a new entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            # Run 1: sensor appears
            invalid_df = polars.DataFrame({
                "Source": ["Conserv"],
                "SensorID": ["s1"],
                "SensorName": ["conserv:307:c000172:Temperature"],
                "DeviceName": ["Short Name"],
                "SensorReadingUTC": [1000],
            })
            rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            # Run 2: sensor disappears (fixed)
            empty_df = polars.DataFrame({
                "Source": [],
                "SensorID": [],
                "SensorName": [],
                "DeviceName": [],
            })
            rejected_sensors_tracker.update_tracking_file(
                invalid_df=empty_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            tracking = polars.read_csv(tracking_path)
            self.assertEqual(tracking.filter(polars.col("Status") == "fixed").shape[0], 1)

            # Run 3: sensor reappears — should update existing row back to active
            rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            tracking = polars.read_csv(tracking_path)
            # The sensor is in existing_all_keys but not in existing_active_keys,
            # so it goes to keys_still_active and gets updated back to active
            s1 = tracking.filter(polars.col("SensorID") == "s1")
            self.assertEqual(s1.shape[0], 1)
            self.assertEqual(s1["Status"].to_list()[0], "active")

    def test_display_name_conserv(self):
        """Conserv sensors use DeviceName as SensorName in tracking."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            invalid_df = polars.DataFrame({
                "Source": ["Conserv"],
                "SensorID": ["s1"],
                "SensorName": ["conserv:307:c000172:Temperature"],
                "DeviceName": ["Bad Device Name"],
                "SensorReadingUTC": [1000],
            })
            rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            tracking = polars.read_csv(tracking_path)
            # SensorName in tracking should be DeviceName for Conserv
            self.assertEqual(tracking["SensorName"].to_list()[0], "Bad Device Name")

    def test_display_name_coris_first_20(self):
        """Coris sensors use first 20 chars of SensorName in tracking."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tracking_path = os.path.join(tmpdir, "tracking.csv")

            long_name = "0bad_name_not_valid_ some suffix here that is very long"
            invalid_df = polars.DataFrame({
                "Source": ["Coris"],
                "SensorID": ["s1"],
                "SensorName": [long_name],
                "DeviceName": ["Some Device"],
                "SensorReadingUTC": [1000],
            })
            rejected_sensors_tracker.update_tracking_file(
                invalid_df=invalid_df,
                tracking_file_path=tracking_path,
                logger=self.logger,
            )

            tracking = polars.read_csv(tracking_path)
            # SensorName in tracking should be first 20 chars for Coris
            self.assertEqual(tracking["SensorName"].to_list()[0], long_name[:20])


if __name__ == "__main__":
    unittest.main()
