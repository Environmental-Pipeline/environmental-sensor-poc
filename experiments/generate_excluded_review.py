"""
Generate a per-sensor excluded review CSV for unit reviewer.

This is the simulation driver: it reads the current sensor_readings.parquet,
runs the CSC filter, summarizes the excluded set to one row per unique sensor,
and writes a CSV.

Usage (inside the Docker container):
    docker exec sensorpull-run python3 /src/experiments/generate_excluded_review.py

Output:
    /src/data/excluded_review_YYYY-MM-DD.csv

When the filter is wired into jobs/3-export-daily.py for go-live, this script
becomes redundant. Keep it around as a useful ad-hoc tool.
"""

# ruff: noqa: E402
import sys
import os
import time

# Mirror the import pattern used by jobs/*.py
sys.path.append("/src/")
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import polars

from modules.csc_filter import split_csc_rows, summarize_excluded_by_sensor


# Match jobs/3-export-daily.py's path resolution
home_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
data_path = os.path.join(home_directory, "data")
if os.path.exists("/src/data"):
    data_path = "/src/data"

SENSOR_READINGS = os.path.join(data_path, "sensor_readings.parquet")

today = time.strftime("%Y-%m-%d", time.gmtime())
OUT_CSV = os.path.join(data_path, f"excluded_review_{today}.csv")


def main() -> None:
    if not os.path.exists(SENSOR_READINGS):
        print(f"[generate_excluded_review] {SENSOR_READINGS} not found, aborting.")
        sys.exit(1)

    print(f"[generate_excluded_review] Reading {SENSOR_READINGS}")
    df = polars.read_parquet(SENSOR_READINGS)
    print(f"[generate_excluded_review] Total rows: {df.height:,}")

    included, excluded = split_csc_rows(df)
    print(f"[generate_excluded_review] Included (CSC): {included.height:,} rows")
    print(f"[generate_excluded_review] Excluded:       {excluded.height:,} rows")

    summary = summarize_excluded_by_sensor(excluded)
    print(f"[generate_excluded_review] Unique excluded sensors: {summary.height}")

    # Counts by reason for sanity
    by_reason = (
        summary.group_by("excluded_reason")
        .agg(polars.len().alias("sensors"))
        .sort("excluded_reason")
    )
    print()
    print("Breakdown by exclusion reason:")
    print(by_reason)

    # Top building codes in the excluded set, for spot-check
    by_code = (
        summary.filter(polars.col("excluded_reason") == "non_csc_building")
        .group_by("parsed_building_code")
        .agg(polars.len().alias("sensors"))
        .sort("sensors", descending=True)
    )
    print()
    print("Excluded sensors by parsed building code:")
    print(by_code)

    summary.write_csv(OUT_CSV)
    print()
    print(f"[generate_excluded_review] Wrote {OUT_CSV}")


if __name__ == "__main__":
    main()
