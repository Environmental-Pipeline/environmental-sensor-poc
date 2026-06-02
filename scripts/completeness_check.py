#!/usr/bin/env python3
"""Daily completeness check. Run AFTER consolidation.
Alerts (exit 1 + stderr) if any source's most-recent complete day collapses
vs its recent baseline. Pass a YYYY-MM-DD arg to test a historical day."""
import sys, datetime as dt
import polars as pl

PARQUET = "/src/data/sensor_readings.parquet"
BASELINE_DAYS = 7
MIN_FRACTION = 0.5   # alert below 50% of baseline median

df = (pl.read_parquet(PARQUET, columns=["Source", "SensorID", "SensorReadingUTC"])
        .with_columns(pl.from_epoch("SensorReadingUTC", time_unit="s").dt.date().alias("day")))

target = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else dt.date.today() - dt.timedelta(days=1)
base_lo = target - dt.timedelta(days=BASELINE_DAYS)

problems = []
for source in sorted(df["Source"].unique().to_list()):
    daily = (df.filter(pl.col("Source") == source)
               .group_by("day").agg(pl.col("SensorID").n_unique().alias("sensors")))
    by_day = dict(zip(daily["day"].to_list(), daily["sensors"].to_list()))
    target_n = by_day.get(target, 0)
    base = [by_day.get(base_lo + dt.timedelta(days=i), 0) for i in range(BASELINE_DAYS)]
    base = [v for v in base if v > 0]
    if not base:
        continue  # no baseline -> skip rather than false-alarm
    baseline = sorted(base)[len(base) // 2]
    if target_n < MIN_FRACTION * baseline:
        problems.append(f"{source}: {target} had {target_n} sensors vs baseline {baseline} "
                        f"({target_n / baseline:.0%} of normal)")

if problems:
    for p in problems:
        print("COMPLETENESS ALERT:", p, file=sys.stderr)
    sys.exit(1)
print(f"Completeness check passed for {target}.")
