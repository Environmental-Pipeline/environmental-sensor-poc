# Environmental Sensor Monitoring System

A Python-based system for collecting, processing, and analyzing environmental sensor data from multiple sources including CORIS and HOBOlink.

## Features

- **Multi-source Data Collection**: Integrates with both CORIS and HOBOlink data sources
- **Unified Data Format**: Standardizes data from different sources into a consistent format
- **Automated Data Processing**: Handles data validation, cleaning, and consolidation
- **Flexible Storage Options**: Supports storing data in Parquet and CSV formats
- **Configurable**: Easy configuration through environment variables

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/environmental-sensor-poc.git
cd environmental-sensor-poc
```

2. Install required dependencies:
```bash
pip install -r requirements.txt
```

3. Set up your environment variables in a `.env` file:
```
# CORIS API credentials
CATS_USER_ID=your_cats_user_id

# HOBOlink API credentials
HOBOLINK_CLIENT_ID=your_client_id
HOBOLINK_CLIENT_SECRET=your_client_secret
HOBOLINK_USER_ID=your_user_id
HOBOLINK_LOGGERS=logger1_id,logger2_id,logger3_id

# HOBOlink configuration
HOBOLINK_ENABLED=True  # Set to False to disable HOBOlink data collection
```

## Usage

### Basic Usage

```python
from EnvironmentData import EnvironmentData

# Initialize the environment data handler
env_data = EnvironmentData()

# Get current sensor readings
readings = env_data.get_current_readings()

# Process and consolidate readings
env_data.consolidate_readings()
```

### Testing HOBOlink Integration

A test script is provided to verify HOBOlink integration:

```bash
python test_hobolink_integration.py
```

This will test the connection to HOBOlink and verify that data can be retrieved.

## Data Structure

The system uses a unified data format for all sensor readings with the following key fields:

- `SensorReadingUTC`: UTC timestamp of the reading
- `SensorReadingF`: Temperature reading in Fahrenheit
- `SensorReadingRh`: Relative humidity reading
- `QueryUTC`: UTC timestamp when the data was retrieved
- `Source`: Indicates the data source ("CORIS" or "HOBOlink")
- `SensorType`: Type of sensor (e.g., "Temperature", "Humidity")

## Documentation

For more detailed information about the HOBOlink integration, see [HOBOLINK_INTEGRATION.md](HOBOLINK_INTEGRATION.md).

## License

This project is licensed under the MIT License - see the LICENSE file for details.

