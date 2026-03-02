# ruff: noqa: E402
# EnvironmentData is not in this folder, add its location to path so we can import it.
import sys
import os
sys.path.append('/src/')  # Docker
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # GitHub Actions

def read_env_variable(var_name):
    with open('.env') as f:
        for line in f:
            if line.startswith(var_name):
                return line.split('=', 1)[1].strip()

# use EnvironmentData to consolidate new and historical readings into one database.
from EnvironmentData import EnvironmentData
EnvironmentData(
    days_back = int(read_env_variable('DAYS_BACK')),
    testing = read_env_variable('TESTING').lower() == 'true',
    coris_enabled = read_env_variable('CORIS_ENABLED').lower() == 'true',
    conserv_enabled = read_env_variable('CONSERV_ENABLED').lower() == 'true',
    licor_enabled = read_env_variable('LICOR_ENABLED').lower() == 'true',
).consolidate_readings()

# ---------------------------------------------------------------------------
# Backfill weather for rows consolidated before archive data was available
# ---------------------------------------------------------------------------
import polars
from modules.weather_enrichment import enrich_sensors_with_weather

home_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data_path = os.path.join(home_directory, "data")
if os.path.exists("/src/data"):
    data_path = "/src/data"

SENSOR_READINGS = os.path.join(data_path, "sensor_readings.parquet")
COORDINATES_PATH = os.path.join(data_path, "building_coordinates.csv")
WEATHER_CACHE_DIR = "/opt/env-sensor-data/weather_cache/"
if os.path.exists("/src/data"):
    WEATHER_CACHE_DIR = os.path.join(data_path, "weather_cache")

if os.path.exists(SENSOR_READINGS) and os.path.exists(COORDINATES_PATH):
    df = polars.read_parquet(SENSOR_READINGS)

    if "weather_temp_f" in df.columns and "SensorName" in df.columns:
        # Identify rows with null weather and a valid 20-char sensor name
        backfill_mask = (
            polars.col("weather_temp_f").is_null()
            & polars.col("SensorName").is_not_null()
            & (polars.col("SensorName").str.len_chars() == 20)
        )

        to_enrich = df.filter(backfill_mask)

        if to_enrich.height > 0:
            print(f"[2-consolidate] Backfilling weather for {to_enrich.height} rows")

            # Partition: keep rows that don't need backfill intact
            df = df.with_row_index("_backfill_idx")
            to_enrich = df.filter(backfill_mask)
            already_ok = df.filter(backfill_mask.not_())

            # Save indices, strip weather columns, re-enrich
            idx_col = to_enrich.select("_backfill_idx")
            weather_cols = [c for c in to_enrich.columns if c.startswith("weather_")]
            to_enrich = to_enrich.drop(weather_cols + ["_backfill_idx"])

            enriched = enrich_sensors_with_weather(
                to_enrich,
                coordinates_path=COORDINATES_PATH,
                cache_dir=WEATHER_CACHE_DIR,
            )

            # Re-attach indices and recombine
            enriched = polars.concat([idx_col, enriched], how="horizontal")
            df = polars.concat([already_ok, enriched], how="diagonal_relaxed")
            df = df.sort("_backfill_idx").drop("_backfill_idx")
            df.write_parquet(SENSOR_READINGS)

            filled = enriched.filter(polars.col("weather_temp_f").is_not_null()).height
            print(f"[2-consolidate] Weather backfill complete: {filled}/{to_enrich.height} rows enriched")
        else:
            print("[2-consolidate] No rows need weather backfill")
    else:
        print("[2-consolidate] No weather columns or SensorName column found, skipping backfill")