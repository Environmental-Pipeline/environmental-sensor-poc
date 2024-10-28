# Files to Support Cron Jobs

These files are copied into the Docker image and used to facilitate the cron jobs: 

1. init: Initialize the database by pulling historical data for all Sensors.
2. pull: Pull current data from all Sensors. Process alerts. Save readings into the new-readings/ folder to await consolidation at the end of the day. See documentation for `EnvironmentData.get_current_readings` for more information.
3. consolidate: At the end of the day, consolidate new-readings/ data into the historical readings and create the various analytical tables. See documentation for `EnvironmentData.consolidate_readings` for more information.

