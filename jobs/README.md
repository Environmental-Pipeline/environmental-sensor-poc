# Files to Support Cron Jobs

These files are copied into the Docker image and used to facilitate the cron jobs:

1. init: Initialize the database by pulling historical data for all Sensors.
2. pull: Pull current data from all Sensors. Process alerts. Save readings into the new-readings/ folder to await consolidation at the end of the day. See documentation for `EnvironmentData.get_current_readings` for more information.
3. consolidate: At the end of the day, consolidate new-readings/ data into the historical readings and create the various analytical tables. See documentation for `EnvironmentData.consolidate_readings` for more information.
4. export-daily: Export an incremental (delta-only) parquet file containing only readings added since the last export. Uses a high-water mark file (`data/daily_export_hwm.json`) to track the last exported `SensorReadingUTC` timestamp. Runs at 2:00 AM UTC, before the VM-level upload cron at 2:15 AM UTC.

## External Cron Job (VM-level)

There is an additional cron job that runs on the VM itself (not inside Docker):

```
15 2 * * * /usr/local/bin/upload-sensor-readings-parquet.sh
```

This job uploads the daily incremental parquet file (`daily_export.parquet`) to S3 and Microsoft Fabric. On the 1st of each month it also uploads a full snapshot of `sensor_readings.parquet` for recovery purposes. See [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md) for details on the upload script and deployment configuration.

