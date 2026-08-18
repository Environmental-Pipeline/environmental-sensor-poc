# ruff: noqa: E402
# EnvironmentData is not in this folder, add its location to path so we can import it.
import sys
import os
sys.path.append('/src/')  # Docker
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # GitHub Actions

import time
import json
import polars

from modules.weather_enrichment import enrich_sensors_with_weather
from modules.csc_filter import split_csc_rows, summarize_excluded_by_sensor
from modules.coris_thresholds import write_threshold_tables

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# In Docker the working directory is /src/ so ./data/ resolves to /src/data/.
# The data_path convention matches EnvironmentData's default.
home_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data_path = os.path.join(home_directory, "data")
if os.path.exists("/src/data"):
    data_path = "/src/data"  # prefer Docker path when available

SENSOR_READINGS = os.path.join(data_path, "sensor_readings.parquet")
DAILY_EXPORT    = os.path.join(data_path, "daily_export.parquet")
HIGH_WATER_MARK = os.path.join(data_path, "daily_export_hwm.json")
COORDINATES     = os.path.join(home_directory, "data", "building_coordinates.csv")
WEATHER_CACHE   = os.path.join(data_path, "weather_cache")

EXCLUDED_SUMMARY = os.path.join(data_path, "daily_export_excluded_summary.csv")

# Feature flag for CSC-only export. When false (default), the daily export
# contains all rows that pass name validation, unchanged from prior behavior.
# When true, only rows where the parsed building code is "CSC" are exported.
# Enablement is resolved by _resolve_csc_filter_enabled() below: it auto-enables
# on/after the go-live date in UTC. CSC_FILTER_ENABLED=true/false overrides the
# date, and touching /src/data/CSC_FILTER_OFF force-disables it with no recreate.
def _resolve_csc_filter_enabled():
    # Auto-enable the CSC export filter on/after go-live without a manual flag flip.
    # Precedence: kill file > explicit env override > go-live date (all UTC).
    from datetime import datetime, timezone, date
    CSC_GO_LIVE = date(2026, 6, 15)
    CSC_KILL_FILE = "/src/data/CSC_FILTER_OFF"
    if os.path.exists(CSC_KILL_FILE):
        return False
    override = os.environ.get("CSC_FILTER_ENABLED", "").strip().lower()
    if override in ("true", "1", "yes"):
        return True
    if override in ("false", "0", "no"):
        return False
    return datetime.now(timezone.utc).date() >= CSC_GO_LIVE


CSC_FILTER_ENABLED = _resolve_csc_filter_enabled()

# ---------------------------------------------------------------------------
# High-water mark helpers
# ---------------------------------------------------------------------------
def read_high_water_mark() -> int:
    """Return the last exported SensorReadingUTC, or 0 if no mark exists."""
    if os.path.exists(HIGH_WATER_MARK):
        with open(HIGH_WATER_MARK) as f:
            mark = json.load(f)
        return int(mark.get("last_exported_utc", 0))
    return 0


def write_high_water_mark(last_utc: int) -> None:
    """Persist the high-water mark to disk."""
    with open(HIGH_WATER_MARK, "w") as f:
        json.dump({
            "last_exported_utc": last_utc,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, f, indent=2)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def export_daily() -> None:
    if not os.path.exists(SENSOR_READINGS):
        print(f"[3-export-daily] sensor_readings.parquet not found at {SENSOR_READINGS}, skipping.")
        return

    hwm = read_high_water_mark()
    print(f"[3-export-daily] High-water mark: {hwm}  ({time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(hwm)) if hwm else 'none'})")

    df = polars.read_parquet(SENSOR_READINGS)

    # Filter to rows newer than the high-water mark
    delta = df.filter(polars.col("SensorReadingUTC") > hwm)

    # Coris threshold reference tables, delivered alongside the parquet.
    # A vendor API failure must not take down the export, so this is isolated.
    try:
        written = write_threshold_tables(data_path)
        for path in written:
            print(f"[3-export-daily] Wrote {path}")
        if not written:
            print("[3-export-daily] WARNING: threshold extract returned no rows")
    except Exception as exc:
        print(f"[3-export-daily] ERROR: threshold extract failed: {exc}")

    if delta.height == 0:
        print("[3-export-daily] No new rows since last export. Writing empty parquet.")
        # Write an empty parquet so the upload script has a valid file
        delta.write_parquet(DAILY_EXPORT)
        return

    # Re-enrich rows that have null weather columns so the daily export
    # contains weather data even for recently-collected readings.
    if "weather_temp_f" in delta.columns and os.path.exists(COORDINATES):
        null_weather = delta.filter(polars.col("weather_temp_f").is_null())
        if null_weather.height > 0:
            print(f"[3-export-daily] Re-enriching {null_weather.height} rows with null weather data")
            # Drop existing weather columns before re-enrichment so they get repopulated
            weather_cols = [c for c in null_weather.columns if c.startswith("weather_")]
            has_weather = delta.filter(polars.col("weather_temp_f").is_not_null())
            null_weather = null_weather.drop(weather_cols)
            null_weather = enrich_sensors_with_weather(
                sensors=null_weather,
                coordinates_path=COORDINATES,
                cache_dir=WEATHER_CACHE,
            )
            delta = polars.concat([has_weather, null_weather], how="diagonal_relaxed")

    new_hwm = delta.select(polars.col("SensorReadingUTC").max()).item()

    # CSC export filter. Always split and always write the excluded summary
    # CSV so the per-day review file is produced regardless of flag state.
    # The flag only controls whether the upload uses the filtered or
    # unfiltered frame. Note: new_hwm is computed on the full delta above
    # so the high-water mark tracks processed rows, not exported rows.
    included, excluded = split_csc_rows(delta)
    excluded_summary = summarize_excluded_by_sensor(excluded)
    excluded_summary.write_csv(EXCLUDED_SUMMARY)
    print(
        f"[3-export-daily] CSC filter split: {included.height} included, "
        f"{excluded.height} excluded ({excluded_summary.height} unique excluded sensors). "
        f"Wrote summary to {EXCLUDED_SUMMARY}."
    )
    if CSC_FILTER_ENABLED:
        print("[3-export-daily] CSC_FILTER_ENABLED=true. Exporting filtered (CSC-only) frame.")
        delta = included
    else:
        print("[3-export-daily] CSC_FILTER_ENABLED=false. Exporting unfiltered frame (shadow mode).")

    # Enforce consistent types for weather columns to prevent schema mismatches across daily files
    FLOAT64_WEATHER_COLS = [
        'weather_cloud_cover_pct', 'weather_humidity_pct',
        'weather_wind_direction_deg', 'weather_wmo_code',
    ]
    for col in FLOAT64_WEATHER_COLS:
        if col in delta.columns:
            delta = delta.with_columns(polars.col(col).cast(polars.Float64))

    delta.write_parquet(DAILY_EXPORT)
    write_high_water_mark(int(new_hwm))

    print(f"[3-export-daily] Exported {delta.height} rows to {DAILY_EXPORT}")
    print(f"[3-export-daily] New high-water mark: {new_hwm}  ({time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(new_hwm))})")


if __name__ == "__main__":
    export_daily()
