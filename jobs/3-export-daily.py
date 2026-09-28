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
from modules.csc_filter import split_csc_rows, summarize_excluded_by_sensor, load_export_allowlists
from modules.coris_thresholds import write_threshold_tables, write_ticket_table

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
DAILY_EXPORT_STAGING = os.path.join(data_path, "daily_export_staging.parquet")
HIGH_WATER_MARK = os.path.join(data_path, "daily_export_hwm.json")
COORDINATES     = os.path.join(home_directory, "data", "building_coordinates.csv")
WEATHER_CACHE   = os.path.join(data_path, "weather_cache")

EXCLUDED_SUMMARY = os.path.join(data_path, "daily_export_excluded_summary.csv")
# Column names, order and dtypes DM receives. The export refuses to write a file
# that differs, so a schema change can never reach DM by accident.
SCHEMA_CONTRACT = os.path.join(data_path, "export_schema_contract.json")

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
# last_ingested_utc drives the export: every row whose IngestedUTC is past it has
# not been delivered yet, whatever its reading time (backfills included).
# last_exported_utc (max reading time delivered) is kept so an older image can
# still run after a rollback, and so consolidation can migrate an old master.
def read_high_water_marks() -> tuple:
    """Return (last_exported_utc, last_ingested_utc); 0 for any missing mark."""
    if os.path.exists(HIGH_WATER_MARK):
        with open(HIGH_WATER_MARK) as f:
            mark = json.load(f)
        return int(mark.get("last_exported_utc", 0)), int(mark.get("last_ingested_utc", 0))
    return 0, 0


def write_high_water_marks(last_utc: int, last_ingested: int) -> None:
    """Persist both marks atomically."""
    tmp = HIGH_WATER_MARK + ".tmp"
    with open(tmp, "w") as f:
        json.dump({
            "last_exported_utc": int(last_utc),
            "last_ingested_utc": int(last_ingested),
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, f, indent=2)
    os.replace(tmp, HIGH_WATER_MARK)


def _fail(msg: str) -> None:
    """Log to stderr (cron-errors.log) and exit non-zero without writing outputs."""
    print(f"[3-export-daily] ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def _schema_of(frame: polars.DataFrame) -> list:
    return [[name, str(dtype)] for name, dtype in frame.schema.items()]


def check_schema_contract(frames: dict) -> None:
    """Refuse to export if any output frame differs from the saved contract."""
    if not os.path.exists(SCHEMA_CONTRACT):
        ref = next(iter(frames.values()))
        with open(SCHEMA_CONTRACT, "w") as f:
            json.dump(_schema_of(ref), f, indent=1)
        print(f"[3-export-daily] WARNING: no schema contract found; created {SCHEMA_CONTRACT} from this export.")
    with open(SCHEMA_CONTRACT) as f:
        contract = json.load(f)
    for label, frame in frames.items():
        got = _schema_of(frame)
        if got != contract:
            missing = [c for c in contract if c not in got]
            extra = [c for c in got if c not in contract]
            _fail(f"{label} export schema differs from contract; nothing written, HWM unchanged. "
                  f"missing/changed={missing} unexpected/changed={extra}")


def retire_previous_outputs() -> None:
    """
    Move yesterday's export files aside before building today's. If this run
    fails, the 02:15 upload finds no file and withholds the heartbeat, instead of
    shipping yesterday's rows again under today's filename.
    """
    for path in (DAILY_EXPORT, DAILY_EXPORT_STAGING):
        if os.path.exists(path):
            os.replace(path, path + ".prev")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
FLOAT64_WEATHER_COLS = [
    'weather_cloud_cover_pct', 'weather_humidity_pct',
    'weather_wind_direction_deg', 'weather_wmo_code',
]


def _cast_weather(frame: polars.DataFrame) -> polars.DataFrame:
    # Enforce consistent types for weather columns to prevent schema mismatches across daily files
    for col in FLOAT64_WEATHER_COLS:
        if col in frame.columns:
            frame = frame.with_columns(polars.col(col).cast(polars.Float64))
    return frame


def export_daily() -> None:
    if not os.path.exists(SENSOR_READINGS):
        print(f"[3-export-daily] sensor_readings.parquet not found at {SENSOR_READINGS}, skipping.")
        return

    retire_previous_outputs()

    hwm, ingest_mark = read_high_water_marks()
    print(f"[3-export-daily] Marks: last_exported_utc={hwm} "
          f"({time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(hwm)) if hwm else 'none'}), "
          f"last_ingested_utc={ingest_mark}")

    df = polars.read_parquet(SENSOR_READINGS)

    if "IngestedUTC" in df.columns:
        delta = df.filter(polars.col("IngestedUTC") > ingest_mark)
        new_ingest_mark = (delta.select(polars.col("IngestedUTC").max()).item()
                           if delta.height else ingest_mark)
        print(f"[3-export-daily] Selecting by ingestion time: {delta.height} rows ingested since {ingest_mark}")
    else:
        # Master not yet migrated (no consolidation has run on this image): fall back
        # to reading time. The next consolidation migrates using last_exported_utc.
        delta = df.filter(polars.col("SensorReadingUTC") > hwm)
        new_ingest_mark = ingest_mark
        print("[3-export-daily] WARNING: master has no IngestedUTC yet; selecting by reading time.")
    delta = delta.drop("IngestedUTC", strict=False)

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

    # Coris alert tickets (delta since last export; full history on first run).
    try:
        tpath = write_ticket_table(data_path)
        print(f"[3-export-daily] Wrote {tpath}" if tpath
              else "[3-export-daily] No ticket changes since last export")
    except Exception as exc:
        print(f"[3-export-daily] ERROR: ticket extract failed: {exc}")

    if delta.height == 0:
        print("[3-export-daily] No new rows since last export. Writing empty parquet files.")
        empty = _cast_weather(delta)
        check_schema_contract({"prod": empty, "staging": empty})
        empty.write_parquet(DAILY_EXPORT)
        empty.write_parquet(DAILY_EXPORT_STAGING)
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

    new_hwm = max(hwm, int(delta.select(polars.col("SensorReadingUTC").max()).item()))

    # CSC export filter. Always split and always write the excluded summary
    # CSV so the per-day review file is produced regardless of flag state.
    # The flag only controls whether the upload uses the filtered or
    # unfiltered frame.
    prod_codes, staging_codes = load_export_allowlists()
    print(f"[3-export-daily] Export allowlists: prod={sorted(prod_codes)} staging={sorted(staging_codes)}")
    included, excluded = split_csc_rows(delta, allowed=prod_codes)
    included_staging, _ = split_csc_rows(delta, allowed=staging_codes)
    excluded_summary = summarize_excluded_by_sensor(excluded)
    excluded_summary.write_csv(EXCLUDED_SUMMARY)
    print(
        f"[3-export-daily] CSC filter split: {included.height} included, "
        f"{excluded.height} excluded ({excluded_summary.height} unique excluded sensors). "
        f"Wrote summary to {EXCLUDED_SUMMARY}."
    )
    if CSC_FILTER_ENABLED:
        print("[3-export-daily] CSC_FILTER_ENABLED=true. Exporting filtered frames (prod and staging).")
        staging = included_staging
        delta = included
    else:
        print("[3-export-daily] CSC_FILTER_ENABLED=false. Exporting unfiltered frame (shadow mode).")
        staging = delta

    delta = _cast_weather(delta)
    staging = _cast_weather(staging)
    check_schema_contract({"prod": delta, "staging": staging})

    delta.write_parquet(DAILY_EXPORT)
    staging.write_parquet(DAILY_EXPORT_STAGING)
    write_high_water_marks(new_hwm, new_ingest_mark)

    print(f"[3-export-daily] Exported {delta.height} rows to {DAILY_EXPORT} (prod)")
    print(f"[3-export-daily] Exported {staging.height} rows to {DAILY_EXPORT_STAGING} (staging: dev/tst)")
    print(f"[3-export-daily] New marks: last_exported_utc={new_hwm} "
          f"({time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(new_hwm))}), last_ingested_utc={new_ingest_mark}")


if __name__ == "__main__":
    export_daily()
