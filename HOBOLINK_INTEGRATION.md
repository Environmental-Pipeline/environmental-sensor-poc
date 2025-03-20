# HOBOlink Integration for Environmental Sensor Monitoring

This document outlines the integration of HOBOlink data with the existing CORIS-based environmental sensor monitoring system.

## Completed Implementation

The following components have been implemented:

1. **Environment Setup**
   - Added environment variables for HOBOlink credentials in `.env` file
   - Updated the `EnvironmentData` class to load and validate these credentials
   - Added configuration options to enable/disable HOBOlink integration

2. **API Authentication Layer**
   - Implemented OAuth2 token management for HOBOlink API
   - Added automatic token refresh mechanism
   - Implemented robust error handling for authentication failures

3. **Data Retrieval**
   - Added methods to fetch data from HOBOlink loggers
   - Implemented transformation of HOBOlink data to match CORIS format
   - Added source identification to distinguish between data sources

4. **Data Processing**
   - Updated data validation to handle both CORIS and HOBOlink data
   - Modified aggregation methods to correctly process combined data
   - Ensured consistent data types across all sources

## Validation

The integration has been tested using:

1. **Direct API Testing**
   - Verified authentication with HOBOlink API
   - Retrieved data from multiple HOBOlink loggers
   - Confirmed data transformation and formatting

2. **Combined Data Processing**
   - Validated that both CORIS and HOBOlink data can be combined
   - Ensured proper source identification in the combined dataset
   - Verified that consolidated data can be saved as both Parquet and CSV

## Configuration

HOBOlink integration can be configured using the following environment variables:

```
# HOBOlink API credentials
HOBOLINK_CLIENT_ID=your_client_id
HOBOLINK_CLIENT_SECRET=your_client_secret
HOBOLINK_USER_ID=your_user_id
HOBOLINK_LOGGERS=logger1_id,logger2_id,logger3_id

# HOBOlink configuration
HOBOLINK_ENABLED=True  # Set to False to disable HOBOlink data collection
```

The integration can also be enabled/disabled programmatically by passing `hobolink_enabled=True|False` when initializing the `EnvironmentData` class.

## Usage

To use the HOBOlink integration:

1. Set up the environment variables as shown above
2. Initialize the `EnvironmentData` class:

```python
from EnvironmentData import EnvironmentData

# Initialize with HOBOlink enabled
env_data = EnvironmentData(
    CatsUserID=your_cats_id,
    hobolink_enabled=True
)

# Get current readings from both CORIS and HOBOlink
readings = env_data.get_current_readings()

# Consolidate readings from both sources
env_data.consolidate_readings()
```

## Data Format

The combined data includes the following source identification fields:

- `Source`: Indicates the data source ("CORIS" or "HOBOlink")
- `SensorID_Coris`: ID for CORIS sensors (null for HOBOlink data)
- `DeviceID_Coris`: Device ID for CORIS devices (null for HOBOlink data)
- `SensorID_HOBOlink`: ID for HOBOlink sensors (null for CORIS data)
- `DeviceID_HOBOlink`: Logger ID for HOBOlink devices (null for CORIS data)

All sensor readings are stored with a consistent format regardless of source, with readings stored in:

- `SensorReadingF`: Temperature readings in Fahrenheit
- `SensorReadingRh`: Relative humidity readings 