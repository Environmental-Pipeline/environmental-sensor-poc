# ruff: noqa: E402
# EnvironmentData is not in this folder, add its location to path so we can import it.
import sys
import os
sys.path.append('/src/')  # Docker
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # GitHub Actions

import time
import json
import polars

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

    if delta.height == 0:
        print("[3-export-daily] No new rows since last export. Writing empty parquet.")
        # Write an empty parquet so the upload script has a valid file
        delta.write_parquet(DAILY_EXPORT)
        return

    new_hwm = delta.select(polars.col("SensorReadingUTC").max()).item()

    delta.write_parquet(DAILY_EXPORT)
    write_high_water_mark(int(new_hwm))

    print(f"[3-export-daily] Exported {delta.height} rows to {DAILY_EXPORT}")
    print(f"[3-export-daily] New high-water mark: {new_hwm}  ({time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(new_hwm))})")


if __name__ == "__main__":
    export_daily()
