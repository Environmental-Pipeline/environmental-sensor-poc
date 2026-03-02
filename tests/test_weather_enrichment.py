# Tests for weather enrichment module
# Run with: pytest tests/test_weather_enrichment.py -v
# No real API calls are made - all external requests are mocked.

import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

import unittest
from unittest.mock import patch, MagicMock
import datetime
import json
import tempfile
import shutil
import polars
import logging

from modules import weather_enrichment


def create_test_logger():
    """Create a simple logger for tests."""
    logger = logging.getLogger("test_weather_enrichment")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        logger.addHandler(handler)
    return logger


def create_sample_sensor_data() -> polars.DataFrame:
    """Create sample sensor data with Yale-style SensorNames."""
    return polars.DataFrame({
        "SensorReadingUTC": [1700000000, 1700003600, 1700007200],
        "QueryUTC": [1700000000, 1700003600, 1700007200],
        "Source": ["Coris", "Coris", "Coris"],
        "DeviceID": ["coris:1", "coris:1", "coris:2"],
        "DeviceName": ["Device 1", "Device 1", "Device 2"],
        "SensorID": ["coris:1-1", "coris:1-1", "coris:2-1"],
        "SensorName": [
            "PYPM_15_01234AB001F",  # Building YPM
            "PYPM_15_01234AB001F",  # Building YPM
            "PKAHN14_01234AB001F",  # Building KAHN
        ],
        "SensorType": ["Temperature", "Temperature", "Temperature"],
        "Historical": [False, False, False],
        "SensorReadingF": [72.5, 73.0, 71.0],
    }).cast({
        "SensorReadingUTC": polars.Int64,
        "QueryUTC": polars.Int32,
        "SensorReadingF": polars.Float32,
    })


def create_mock_weather_response(num_hours: int = 3) -> dict:
    """Create a mock Open-Meteo API response."""
    base_time = "2023-11-14T"
    times = [f"{base_time}{h:02d}:00" for h in range(num_hours)]
    return {
        "hourly": {
            "time": times,
            "temperature_2m": [10.5 + i for i in range(num_hours)],
            "relative_humidity_2m": [65.0 + i for i in range(num_hours)],
            "precipitation": [0.0] * num_hours,
            "cloud_cover": [50.0 + i for i in range(num_hours)],
        }
    }


class TestExtractBuildingCode(unittest.TestCase):
    """Test building code extraction from SensorName."""

    def test_standard_3char_code(self):
        """YPM from PYPM_..."""
        result = weather_enrichment.extract_building_code("PYPM_15_01234AB001F")
        self.assertEqual(result, "YPM")

    def test_standard_4char_code(self):
        """KAHN from PKAHN..."""
        result = weather_enrichment.extract_building_code("PKAHN14_01234AB001F")
        self.assertEqual(result, "KAHN")

    def test_standard_3char_code_kgl(self):
        """KGL from LKGL_..."""
        result = weather_enrichment.extract_building_code("LKGL_15_01234AB001F")
        self.assertEqual(result, "KGL")

    def test_none_input(self):
        result = weather_enrichment.extract_building_code(None)
        self.assertIsNone(result)

    def test_short_string(self):
        result = weather_enrichment.extract_building_code("PYP")
        self.assertIsNone(result)

    def test_empty_string(self):
        result = weather_enrichment.extract_building_code("")
        self.assertIsNone(result)

    def test_all_underscores_returns_none(self):
        """Building code area all underscores should return None."""
        result = weather_enrichment.extract_building_code("P____15_01234AB001F")
        self.assertIsNone(result)


class TestExtractBuildingCodesColumn(unittest.TestCase):
    """Test adding building_code column to a DataFrame."""

    def test_adds_building_code_column(self):
        df = polars.DataFrame({
            "SensorName": ["PYPM_15_01234AB001F", "PKAHN14_01234AB001F"],
        })
        result = weather_enrichment.extract_building_codes_column(df)
        self.assertIn("building_code", result.columns)
        self.assertEqual(result["building_code"].to_list(), ["YPM", "KAHN"])

    def test_missing_sensor_name_raises(self):
        df = polars.DataFrame({"OtherColumn": ["abc"]})
        with self.assertRaises(ValueError):
            weather_enrichment.extract_building_codes_column(df)


class TestLoadBuildingCoordinates(unittest.TestCase):
    """Test loading building coordinates CSV."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.csv_path = os.path.join(self.tmpdir, "coords.csv")
        with open(self.csv_path, "w") as f:
            f.write("building_code,latitude,longitude,building_name\n")
            f.write("YPM,41.3163,-72.9223,Yale Peabody Museum\n")
            f.write("KAHN,41.3084,-72.9310,Kahn Building\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_loads_csv(self):
        df = weather_enrichment.load_building_coordinates(self.csv_path)
        self.assertEqual(df.shape[0], 2)
        self.assertIn("building_code", df.columns)
        self.assertIn("latitude", df.columns)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            weather_enrichment.load_building_coordinates("/nonexistent/path.csv")


class TestGetUniqueLocations(unittest.TestCase):
    """Test grouping buildings by unique lat/long."""

    def test_groups_shared_coordinates(self):
        coords = polars.DataFrame({
            "building_code": ["KAHN", "KHAN", "OYAG", "YPM"],
            "latitude": [41.3084, 41.3084, 41.3084, 41.3163],
            "longitude": [-72.9310, -72.9310, -72.9310, -72.9223],
            "building_name": ["Kahn", "Khan", "OYAG", "Peabody"],
        })
        codes = ["KAHN", "KHAN", "OYAG", "YPM"]
        locations = weather_enrichment.get_unique_locations(codes, coords)

        # Should have 2 unique locations
        self.assertEqual(len(locations), 2)

        # The shared location should have 3 buildings
        shared_key = (41.3084, -72.931)
        self.assertEqual(len(locations[shared_key]), 3)
        self.assertIn("KAHN", locations[shared_key])
        self.assertIn("KHAN", locations[shared_key])
        self.assertIn("OYAG", locations[shared_key])

    def test_skips_none_codes(self):
        coords = polars.DataFrame({
            "building_code": ["YPM"],
            "latitude": [41.3163],
            "longitude": [-72.9223],
            "building_name": ["Peabody"],
        })
        locations = weather_enrichment.get_unique_locations([None, "YPM"], coords)
        self.assertEqual(len(locations), 1)

    def test_skips_unmatched_codes(self):
        coords = polars.DataFrame({
            "building_code": ["YPM"],
            "latitude": [41.3163],
            "longitude": [-72.9223],
            "building_name": ["Peabody"],
        })
        locations = weather_enrichment.get_unique_locations(["ZZZZZ"], coords)
        self.assertEqual(len(locations), 0)


class TestCacheKey(unittest.TestCase):
    """Test cache key generation."""

    def test_deterministic(self):
        key1 = weather_enrichment._cache_key(41.3163, -72.9223, "2023-11-15", "2023-11-16")
        key2 = weather_enrichment._cache_key(41.3163, -72.9223, "2023-11-15", "2023-11-16")
        self.assertEqual(key1, key2)

    def test_different_for_different_inputs(self):
        key1 = weather_enrichment._cache_key(41.3163, -72.9223, "2023-11-15", "2023-11-16")
        key2 = weather_enrichment._cache_key(41.3084, -72.9310, "2023-11-15", "2023-11-16")
        self.assertNotEqual(key1, key2)


class TestFetchWeatherData(unittest.TestCase):
    """Test weather data fetching with mocked HTTP requests."""

    def setUp(self):
        self.logger = create_test_logger()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    @patch("modules.weather_enrichment.requests.get")
    def test_successful_fetch(self, mock_get):
        """Test successful API fetch returns DataFrame with expected columns."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = create_mock_weather_response()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = weather_enrichment.fetch_weather_data(
            latitude=41.3163,
            longitude=-72.9223,
            start_date="2023-11-14",
            end_date="2023-11-14",
            logger=self.logger,
        )

        self.assertIsNotNone(result)
        self.assertIn("weather_hour_utc", result.columns)
        self.assertIn("temperature_2m", result.columns)
        self.assertIn("relative_humidity_2m", result.columns)
        self.assertIn("precipitation", result.columns)
        self.assertIn("cloud_cover", result.columns)
        self.assertEqual(result.shape[0], 3)

    @patch("modules.weather_enrichment.requests.get")
    def test_caches_response(self, mock_get):
        """Test that fetched data is cached and reused."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = create_mock_weather_response()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        # First call: should hit API
        weather_enrichment.fetch_weather_data(
            latitude=41.3163,
            longitude=-72.9223,
            start_date="2023-11-14",
            end_date="2023-11-14",
            cache_dir=self.tmpdir,
            logger=self.logger,
        )
        self.assertEqual(mock_get.call_count, 1)

        # Second call: should use cache
        result = weather_enrichment.fetch_weather_data(
            latitude=41.3163,
            longitude=-72.9223,
            start_date="2023-11-14",
            end_date="2023-11-14",
            cache_dir=self.tmpdir,
            logger=self.logger,
        )
        # API should NOT be called again
        self.assertEqual(mock_get.call_count, 1)
        self.assertIsNotNone(result)

    @patch("modules.weather_enrichment.requests.get")
    def test_api_failure_returns_none(self, mock_get):
        """Test that API errors return None gracefully."""
        mock_get.side_effect = Exception("Network error")

        result = weather_enrichment.fetch_weather_data(
            latitude=41.3163,
            longitude=-72.9223,
            start_date="2023-11-14",
            end_date="2023-11-14",
            logger=self.logger,
        )

        self.assertIsNone(result)

    @patch("modules.weather_enrichment.requests.get")
    def test_bad_response_returns_none(self, mock_get):
        """Test that unexpected response structure returns None."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": "bad request"}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = weather_enrichment.fetch_weather_data(
            latitude=41.3163,
            longitude=-72.9223,
            start_date="2023-11-14",
            end_date="2023-11-14",
            logger=self.logger,
        )

        self.assertIsNone(result)


class TestFloorToHour(unittest.TestCase):
    """Test UTC timestamp flooring to nearest hour."""

    def test_already_on_hour(self):
        # 2023-11-14 12:00:00 UTC
        self.assertEqual(
            weather_enrichment._floor_to_hour(1699963200),
            1699963200
        )

    def test_mid_hour(self):
        # 2023-11-14 12:30:00 UTC -> 12:00:00
        self.assertEqual(
            weather_enrichment._floor_to_hour(1699963200 + 1800),
            1699963200
        )

    def test_end_of_hour(self):
        # 2023-11-14 12:59:59 UTC -> 12:00:00
        self.assertEqual(
            weather_enrichment._floor_to_hour(1699963200 + 3599),
            1699963200
        )


class TestEnrichSensorsWithWeather(unittest.TestCase):
    """Test the full enrichment pipeline with mocked API."""

    def setUp(self):
        self.logger = create_test_logger()
        self.tmpdir = tempfile.mkdtemp()
        self.cache_dir = os.path.join(self.tmpdir, "weather_cache")
        self.coords_path = os.path.join(self.tmpdir, "coords.csv")
        with open(self.coords_path, "w") as f:
            f.write("building_code,latitude,longitude,building_name\n")
            f.write("YPM,41.3163,-72.9223,Yale Peabody Museum\n")
            f.write("KAHN,41.3084,-72.9310,Kahn Building\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    @patch("modules.weather_enrichment.requests.get")
    @patch("modules.weather_enrichment.time.sleep")
    def test_full_enrichment(self, mock_sleep, mock_get):
        """Test that enrichment adds all weather columns."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        # Create response with enough hours to cover the test data range
        base_time = "2023-11-14T"
        times = [f"{base_time}{h:02d}:00" for h in range(24)]
        mock_response.json.return_value = {
            "hourly": {
                "time": times,
                "temperature_2m": [10.0 + i * 0.5 for i in range(24)],
                "relative_humidity_2m": [60.0 + i for i in range(24)],
                "dew_point_2m": [5.0 + i * 0.3 for i in range(24)],
                "apparent_temperature": [8.0 + i * 0.4 for i in range(24)],
                "precipitation": [0.0] * 24,
                "cloud_cover": [40.0 + i for i in range(24)],
                "weather_code": [0] * 12 + [3] * 12,
            }
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        sensors = create_sample_sensor_data()
        result = weather_enrichment.enrich_sensors_with_weather(
            sensors=sensors,
            coordinates_path=self.coords_path,
            cache_dir=self.cache_dir,
            logger=self.logger,
        )

        # Check weather columns are present (Fahrenheit, not Celsius)
        self.assertIn("weather_temp_f", result.columns)
        self.assertIn("weather_dew_point_f", result.columns)
        self.assertIn("weather_apparent_temp_f", result.columns)
        self.assertIn("weather_humidity_pct", result.columns)
        self.assertIn("weather_precip_mm", result.columns)
        self.assertIn("weather_cloud_cover_pct", result.columns)
        self.assertNotIn("weather_temp_c", result.columns)
        self.assertNotIn("weather_dew_point_c", result.columns)
        self.assertNotIn("weather_apparent_temp_c", result.columns)

        # Check Fahrenheit conversion: C values range 10.0-21.5, so F should be 50.0-70.7
        matched_rows = result.filter(polars.col("weather_temp_f").is_not_null())
        if matched_rows.shape[0] > 0:
            for temp_f in matched_rows["weather_temp_f"].to_list():
                self.assertGreaterEqual(temp_f, 50.0)
                self.assertLessEqual(temp_f, 71.0)

        # Check WMO description column
        self.assertIn("weather_wmo_description", result.columns)
        wmo_vals = result.filter(polars.col("weather_wmo_description").is_not_null())["weather_wmo_description"].to_list()
        self.assertTrue(all(v in ("Clear sky", "Overcast") for v in wmo_vals))

        # Check that temporary columns are cleaned up
        self.assertNotIn("building_code", result.columns)
        self.assertNotIn("_weather_hour_utc", result.columns)

        # Original data should be preserved
        self.assertEqual(result.shape[0], sensors.shape[0])
        self.assertIn("SensorReadingF", result.columns)

    @patch("modules.weather_enrichment.requests.get")
    @patch("modules.weather_enrichment.time.sleep")
    def test_empty_sensors(self, mock_sleep, mock_get):
        """Test enrichment on empty DataFrame."""
        empty = polars.DataFrame(schema={
            "SensorReadingUTC": polars.Int64,
            "SensorName": polars.Utf8,
        })
        result = weather_enrichment.enrich_sensors_with_weather(
            sensors=empty,
            coordinates_path=self.coords_path,
            logger=self.logger,
        )
        self.assertIn("weather_temp_f", result.columns)
        self.assertIn("weather_wmo_description", result.columns)
        self.assertTrue(result.is_empty())

    @patch("modules.weather_enrichment.requests.get")
    @patch("modules.weather_enrichment.time.sleep")
    def test_no_matching_buildings(self, mock_sleep, mock_get):
        """Test enrichment when sensor building codes don't match coordinates."""
        sensors = polars.DataFrame({
            "SensorReadingUTC": [1700000000],
            "SensorName": ["PZZZZ15_01234AB001F"],  # ZZZZ not in coords
        }).cast({"SensorReadingUTC": polars.Int64})

        result = weather_enrichment.enrich_sensors_with_weather(
            sensors=sensors,
            coordinates_path=self.coords_path,
            logger=self.logger,
        )
        self.assertIn("weather_temp_f", result.columns)
        self.assertIn("weather_wmo_description", result.columns)
        # All weather values should be null
        self.assertTrue(result["weather_temp_f"].is_null().all())

    def test_missing_sensor_name_column(self):
        """Test enrichment when SensorName column is missing."""
        sensors = polars.DataFrame({
            "SensorReadingUTC": [1700000000],
        }).cast({"SensorReadingUTC": polars.Int64})

        result = weather_enrichment.enrich_sensors_with_weather(
            sensors=sensors,
            coordinates_path=self.coords_path,
            logger=self.logger,
        )
        self.assertIn("weather_temp_f", result.columns)
        self.assertIn("weather_wmo_description", result.columns)
        self.assertTrue(result["weather_temp_f"].is_null().all())

    @patch("modules.weather_enrichment.requests.get")
    @patch("modules.weather_enrichment.time.sleep")
    def test_api_failure_graceful(self, mock_sleep, mock_get):
        """Test enrichment handles total API failure gracefully."""
        mock_get.side_effect = Exception("Network error")

        sensors = create_sample_sensor_data()
        result = weather_enrichment.enrich_sensors_with_weather(
            sensors=sensors,
            coordinates_path=self.coords_path,
            logger=self.logger,
        )

        # Should still have weather columns, all null
        self.assertIn("weather_temp_f", result.columns)
        self.assertIn("weather_wmo_description", result.columns)
        self.assertTrue(result["weather_temp_f"].is_null().all())
        # Original data preserved
        self.assertEqual(result.shape[0], sensors.shape[0])


class TestParseWeatherResponse(unittest.TestCase):
    """Test parsing of Open-Meteo API responses."""

    def test_parses_standard_response(self):
        data = create_mock_weather_response(num_hours=5)
        result = weather_enrichment._parse_weather_response(data)

        self.assertEqual(result.shape[0], 5)
        self.assertIn("weather_hour_utc", result.columns)
        self.assertIn("temperature_2m", result.columns)

    def test_handles_missing_param(self):
        """Test response where one weather parameter is missing."""
        data = {
            "hourly": {
                "time": ["2023-11-14T00:00", "2023-11-14T01:00"],
                "temperature_2m": [10.5, 11.0],
                # Missing: relative_humidity_2m, precipitation, cloud_cover
            }
        }
        result = weather_enrichment._parse_weather_response(data)
        self.assertEqual(result.shape[0], 2)
        # Missing params should be filled with None
        self.assertTrue(result["relative_humidity_2m"].is_null().all())


class TestForecastCacheKey(unittest.TestCase):
    """Test forecast cache key generation."""

    def test_deterministic(self):
        key1 = weather_enrichment._forecast_cache_key(41.3163, -72.9223, "2023-11-20")
        key2 = weather_enrichment._forecast_cache_key(41.3163, -72.9223, "2023-11-20")
        self.assertEqual(key1, key2)

    def test_different_dates(self):
        key1 = weather_enrichment._forecast_cache_key(41.3163, -72.9223, "2023-11-20")
        key2 = weather_enrichment._forecast_cache_key(41.3163, -72.9223, "2023-11-21")
        self.assertNotEqual(key1, key2)

    def test_different_from_archive_key(self):
        forecast_key = weather_enrichment._forecast_cache_key(41.3163, -72.9223, "2023-11-20")
        archive_key = weather_enrichment._cache_key(41.3163, -72.9223, "2023-11-20", "2023-11-20")
        self.assertNotEqual(forecast_key, archive_key)


class TestFetchForecastData(unittest.TestCase):
    """Test forecast data fetching with mocked HTTP requests."""

    def setUp(self):
        self.logger = create_test_logger()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    @patch("modules.weather_enrichment.requests.get")
    def test_successful_fetch(self, mock_get):
        """Test successful forecast API fetch returns DataFrame."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = create_mock_weather_response()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = weather_enrichment.fetch_forecast_data(
            latitude=41.3163,
            longitude=-72.9223,
            logger=self.logger,
        )

        self.assertIsNotNone(result)
        self.assertIn("weather_hour_utc", result.columns)
        self.assertIn("temperature_2m", result.columns)
        self.assertEqual(result.shape[0], 3)

        # Verify it called the forecast URL with correct parameters
        args, kwargs = mock_get.call_args
        self.assertEqual(args[0], weather_enrichment.OPEN_METEO_FORECAST_URL)
        self.assertEqual(kwargs["params"]["past_days"], 5)
        self.assertEqual(kwargs["params"]["forecast_days"], 0)

    @patch("modules.weather_enrichment.requests.get")
    def test_api_failure_returns_none(self, mock_get):
        """Test that forecast API errors return None gracefully."""
        mock_get.side_effect = Exception("Network error")

        result = weather_enrichment.fetch_forecast_data(
            latitude=41.3163,
            longitude=-72.9223,
            logger=self.logger,
        )

        self.assertIsNone(result)

    @patch("modules.weather_enrichment.requests.get")
    def test_caches_response(self, mock_get):
        """Test that forecast data is cached and reused."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = create_mock_weather_response()
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        # First call: hits API
        weather_enrichment.fetch_forecast_data(
            latitude=41.3163,
            longitude=-72.9223,
            cache_dir=self.tmpdir,
            logger=self.logger,
        )
        self.assertEqual(mock_get.call_count, 1)

        # Second call: uses cache
        result = weather_enrichment.fetch_forecast_data(
            latitude=41.3163,
            longitude=-72.9223,
            cache_dir=self.tmpdir,
            logger=self.logger,
        )
        self.assertEqual(mock_get.call_count, 1)
        self.assertIsNotNone(result)

    @patch("modules.weather_enrichment.requests.get")
    def test_bad_response_returns_none(self, mock_get):
        """Test that unexpected forecast response returns None."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": "bad request"}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = weather_enrichment.fetch_forecast_data(
            latitude=41.3163,
            longitude=-72.9223,
            logger=self.logger,
        )

        self.assertIsNone(result)


class TestFetchWeatherForDateRange(unittest.TestCase):
    """Test the smart date-range splitting between Archive and Forecast APIs."""

    def setUp(self):
        self.logger = create_test_logger()
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    @patch("modules.weather_enrichment.fetch_forecast_data")
    @patch("modules.weather_enrichment.fetch_weather_data")
    @patch("modules.weather_enrichment.datetime")
    def test_old_dates_use_archive_only(self, mock_dt, mock_archive, mock_forecast):
        """Dates older than 5 days should only use the Archive API."""
        mock_dt.date.today.return_value = datetime.date(2023, 11, 20)
        mock_dt.date.fromisoformat = datetime.date.fromisoformat
        mock_dt.timedelta = datetime.timedelta

        mock_archive.return_value = polars.DataFrame({
            "weather_hour_utc": [1699920000],
            "temperature_2m": [10.0],
        })

        result = weather_enrichment.fetch_weather_for_date_range(
            41.3163, -72.9223,
            "2023-11-01", "2023-11-10",
            logger=self.logger,
        )

        mock_archive.assert_called_once()
        mock_forecast.assert_not_called()
        self.assertIsNotNone(result)

    @patch("modules.weather_enrichment.fetch_forecast_data")
    @patch("modules.weather_enrichment.fetch_weather_data")
    @patch("modules.weather_enrichment.datetime")
    def test_recent_dates_use_forecast_only(self, mock_dt, mock_archive, mock_forecast):
        """Dates within 5 days should only use the Forecast API."""
        mock_dt.date.today.return_value = datetime.date(2023, 11, 20)
        mock_dt.date.fromisoformat = datetime.date.fromisoformat
        mock_dt.timedelta = datetime.timedelta

        mock_forecast.return_value = polars.DataFrame({
            "weather_hour_utc": [1700438400],
            "temperature_2m": [12.0],
        })

        result = weather_enrichment.fetch_weather_for_date_range(
            41.3163, -72.9223,
            "2023-11-16", "2023-11-20",
            logger=self.logger,
        )

        mock_archive.assert_not_called()
        mock_forecast.assert_called_once()
        self.assertIsNotNone(result)

    @patch("modules.weather_enrichment.fetch_forecast_data")
    @patch("modules.weather_enrichment.fetch_weather_data")
    @patch("modules.weather_enrichment.datetime")
    def test_spanning_dates_use_both(self, mock_dt, mock_archive, mock_forecast):
        """Date range spanning the cutoff should use both APIs."""
        mock_dt.date.today.return_value = datetime.date(2023, 11, 20)
        mock_dt.date.fromisoformat = datetime.date.fromisoformat
        mock_dt.timedelta = datetime.timedelta

        mock_archive.return_value = polars.DataFrame({
            "weather_hour_utc": [1699920000],
            "temperature_2m": [10.0],
        })
        mock_forecast.return_value = polars.DataFrame({
            "weather_hour_utc": [1700438400],
            "temperature_2m": [12.0],
        })

        result = weather_enrichment.fetch_weather_for_date_range(
            41.3163, -72.9223,
            "2023-11-10", "2023-11-20",
            logger=self.logger,
        )

        mock_archive.assert_called_once()
        mock_forecast.assert_called_once()
        self.assertIsNotNone(result)
        self.assertEqual(result.height, 2)

    @patch("modules.weather_enrichment.fetch_forecast_data")
    @patch("modules.weather_enrichment.fetch_weather_data")
    @patch("modules.weather_enrichment.datetime")
    def test_both_fail_returns_none(self, mock_dt, mock_archive, mock_forecast):
        """If both APIs fail, return None."""
        mock_dt.date.today.return_value = datetime.date(2023, 11, 20)
        mock_dt.date.fromisoformat = datetime.date.fromisoformat
        mock_dt.timedelta = datetime.timedelta

        mock_archive.return_value = None
        mock_forecast.return_value = None

        result = weather_enrichment.fetch_weather_for_date_range(
            41.3163, -72.9223,
            "2023-11-10", "2023-11-20",
            logger=self.logger,
        )

        self.assertIsNone(result)

    @patch("modules.weather_enrichment.fetch_forecast_data")
    @patch("modules.weather_enrichment.fetch_weather_data")
    @patch("modules.weather_enrichment.datetime")
    def test_archive_end_date_capped(self, mock_dt, mock_archive, mock_forecast):
        """Archive API end date should be capped at cutoff - 1 day."""
        mock_dt.date.today.return_value = datetime.date(2023, 11, 20)
        mock_dt.date.fromisoformat = datetime.date.fromisoformat
        mock_dt.timedelta = datetime.timedelta

        mock_archive.return_value = polars.DataFrame({
            "weather_hour_utc": [1699920000],
            "temperature_2m": [10.0],
        })
        mock_forecast.return_value = polars.DataFrame({
            "weather_hour_utc": [1700438400],
            "temperature_2m": [12.0],
        })

        weather_enrichment.fetch_weather_for_date_range(
            41.3163, -72.9223,
            "2023-11-10", "2023-11-20",
            logger=self.logger,
        )

        # Archive should be called with end_date = cutoff - 1 = 2023-11-14
        archive_call_args = mock_archive.call_args
        self.assertEqual(archive_call_args[0][3], "2023-11-14")


if __name__ == '__main__':
    unittest.main(verbosity=2)
