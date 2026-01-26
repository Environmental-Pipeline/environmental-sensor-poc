# Files to Support Cron Jobs

These files are copied into the Docker image and used to facilitate the cron jobs:

1. init: Initialize the database by pulling historical data for all Sensors.
2. pull: Pull current data from all Sensors. Process alerts. Save readings into the new-readings/ folder to await consolidation at the end of the day. See documentation for `EnvironmentData.get_current_readings` for more information.
3. consolidate: At the end of the day, consolidate new-readings/ data into the historical readings and create the various analytical tables. See documentation for `EnvironmentData.consolidate_readings` for more information.

## External Cron Job (VM-level)

There is an additional cron job that runs on the VM itself (not inside Docker):

```
15 2 * * * /usr/local/bin/upload-sensor-readings-parquet.sh
```

This job uploads the daily parquet file to S3 and Microsoft Fabric. See [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md) for details on the upload script and deployment configuration.

