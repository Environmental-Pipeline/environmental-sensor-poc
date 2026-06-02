# ruff: noqa: E402
# EnvironmentData is not in this folder, add its location to path so we can import it.
import sys
import os
sys.path.append('/src/')  # Docker
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # GitHub Actions

import polars
from modules.weather_enrichment import enrich_sensors_with_weather

def read_env_variable(var_name):
    with open('.env') as f:
        for line in f:
            if line.startswith(var_name):
                return line.split('=', 1)[1].strip()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
home_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data_path = os.path.join(home_directory, "data")
if os.path.exists("/src/data"):
    data_path = "/src/data"

SENSOR_READINGS = os.path.join(data_path, "sensor_readings.parquet")
COORDINATES     = os.path.join(home_directory, "data", "building_coordinates.csv")
WEATHER_CACHE   = os.path.join(data_path, "weather_cache")

# use EnvironmentData to consolidate new and historical readings into one database.
from EnvironmentData import EnvironmentData
_env = EnvironmentData(
    days_back = int(read_env_variable('DAYS_BACK')),
    testing = read_env_variable('TESTING').lower() == 'true',
    coris_enabled = read_env_variable('CORIS_ENABLED').lower() == 'true',
    conserv_enabled = read_env_variable('CONSERV_ENABLED').lower() == 'true',
    licor_enabled = read_env_variable('LICOR_ENABLED').lower() == 'true',
)
_env.backfill_conserv_gaps()
_env.consolidate_readings()

# ---------------------------------------------------------------------------
# Backfill weather data for rows that previously had nulls
# ---------------------------------------------------------------------------
# After consolidation, re-enrich rows in sensor_readings.parquet where weather
# columns are null but the sensor has a valid 20-char name. This fills in
# weather retroactively as archive data becomes available.
if os.path.exists(SENSOR_READINGS) and os.path.exists(COORDINATES):
    df = polars.read_parquet(SENSOR_READINGS)

    if "weather_temp_f" in df.columns and "SensorName" in df.columns:
        # Only backfill rows with null weather AND a valid 20-char SensorName
        null_mask = polars.col("weather_temp_f").is_null()
        valid_name = polars.col("SensorName").str.len_chars() >= 20
        needs_backfill = df.filter(null_mask & valid_name)

        if needs_backfill.height > 0:
            print(f"[2-consolidate] Backfilling weather for {needs_backfill.height} rows")

            already_filled = df.filter(~(null_mask & valid_name))

            # Drop existing weather columns before re-enrichment
            weather_cols = [c for c in needs_backfill.columns if c.startswith("weather_")]
            needs_backfill = needs_backfill.drop(weather_cols)

            needs_backfill = enrich_sensors_with_weather(
                sensors=needs_backfill,
                coordinates_path=COORDINATES,
                cache_dir=WEATHER_CACHE,
            )

            df = polars.concat([already_filled, needs_backfill], how="diagonal_relaxed")
            df = df.sort("SensorReadingUTC")
            df.write_parquet(SENSOR_READINGS)
            filled = df.filter(polars.col("weather_temp_f").is_not_null()).height
            print(f"[2-consolidate] Backfill complete. {filled}/{df.height} rows now have weather data")
        else:
            print("[2-consolidate] No rows need weather backfill")