# HOBOlink API Testing

This directory contains a test script to validate connectivity with the HOBOlink API before integrating it with the main EnvironmentData system.

## Setup Instructions

1. **Install Required Packages**

   Make sure you've activated your virtual environment (if using one), then install the required packages:

   ```bash
   pip install requests python-dotenv
   ```

2. **Configure Environment Variables**

   Copy the `test_hobolink_env` file to a new file named `.env` to run the test:

   ```bash
   cp test_hobolink_env .env
   ```

   OR, to keep your existing `.env` file intact, run the test with the specific env file:

   ```bash
   ENV_FILE=test_hobolink_env python test_hobolink_api.py
   ```

3. **Run the Test Script**

   ```bash
   python test_hobolink_api.py
   ```

## Expected Output

If the test is successful, you should see:
- Connection test results
- OAuth token retrieval confirmation
- Data retrieval summary for each logger
- Information about saved sample data files

Example output:
```
Testing HOBOlink API connection...
Testing OAuth token retrieval...
Successfully retrieved OAuth token.

Testing data retrieval for logger 20284065...
Requesting data for logger 20284065 from 2023-03-19 00:00:00 to 2023-03-20 00:00:00
Successfully retrieved data for logger 20284065
Found 288 total observations
Unique sensors: 2
Sensor types: Temperature, RH

Readings per sensor:
  - 20284065-1 (Temperature): 144 readings
  - 20284065-2 (RH): 144 readings

Sample reading:
  logger_sn: 20284065
  sensor_sn: 20284065-1
  timestamp: 2023-03-19 00:00:00Z
  data_type_id: 1
  si_value: 21.604418715577637
  si_unit: °C
  us_value: 70.88795368803974
  us_unit: °F
  scaled_value: 0.0
  scaled_unit: null
  sensor_key: 2867315
  sensor_measurement_type: Temperature
Saved sample data to hobolink_sample_20284065.json
```

## Sample Data Files

The test script creates JSON files (`hobolink_sample_{logger_id}.json`) containing raw sample data from each logger. You can examine these files to better understand the data structure before integrating with the main system.

## Troubleshooting

If you encounter errors:

1. **Authentication Failures**
   - Verify the client ID and secret are correct in the .env file
   - Check if there are API rate limits or access restrictions

2. **No Data Returned**
   - Verify the logger IDs are correct
   - Try increasing the `days_back` parameter if the loggers haven't reported data recently

3. **Connection Issues**
   - Check your internet connection
   - Verify that the HOBOlink API endpoint is accessible from your network

## Next Steps

After successfully running the test and understanding the data format:

1. Review the sample JSON files to understand the structure and available sensors
2. Plan the integration approach with the existing EnvironmentData system
3. Test data transformations to match the expected format 