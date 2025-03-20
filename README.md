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

3. Set up your environment variables by copying and editing the sample .env file:
```bash
cp sample.env .env
# Edit .env with your credentials and settings
```

The .env file should contain your API credentials and configuration settings:
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

### About HOBOlink Logger IDs

The `HOBOLINK_LOGGERS` setting specifies which HOBOlink loggers to retrieve data from. This is necessary because:

1. Not all loggers may have current data
2. You may only be interested in specific loggers for your application
3. Specifying loggers explicitly makes the API calls more efficient

Logger IDs are numeric identifiers (e.g., 20284065, 20447203) that uniquely identify each data logger in the HOBOlink system. You can find available logger IDs in your HOBOlink account or by running the test script:

```bash
python test_hobolink_integration.py
```

## Python Setup

Virtual environments are used to prevent conflicts with other Python projects.

_Commands should be run from the project root, possibly by using the terminal after opening the project root in VS Code._

* Install Python 3.8 or later.
* Initialize the virtual environment by running `python -m venv .venv`, wait until it finishes, and then `.venv/Scripts/activate` (on Windows).
* Install the necessary packages into the environment with `pip install -r requirements.txt`.
* Copy sample.env to .env and edit with your credentials.
* You can now run code. To view examples, run `jupyter notebook` to start Jupyter Notebook and open `examples-cron.ipynb` and `examples-analysis.ipynb`.

## Docker Setup

To use Docker, you'll need to use Docker Desktop to build the image and start a container. 

_Commands should be run from the project root, possibly by using the terminal after opening the project root in VS Code._

* Install Docker Desktop from https://docs.docker.com/engine/install/. 
* Run Docker Desktop to start the docker daemon running in the background. 
* Request the .env file from the developer and place it in the project root.
* Build the Docker image by running `docker build . -t environment`. This will read the Dockerfile and use it to build an image, which we'll use to initiate a container later on.
* Initiate the container with `docker run --name environment-run -p 8888:8888 environment`.
* Once it is running, there are a few ways you can interact with it:
    - Logs will print to terminal. At the start of every minute, you'll see it run `get_current_readings`. Every ten minutes, it'll run `consolidate_readings`.
    - The terminal will print a Jupyter URL you can access, starting with http://127.0.0.1:8888. Ctrl + click it to open an interactive Jupyter Notebook. You can then open examples.ipynb and execute code. The data may look the same, but it's actually reading from data populated by cron!
    - You can open an interactive session in a new terminal with `docker exec -it environment-run /bin/bash` and watch the logs with `tail -f data/EnvironmentData.log`, or run any other Linux command in the container. 
    - Open the container in Docker Desktop and navigate to Files > src to view the project files like `sensor_readings.parquet` and `device_readings.parquet`.
* When you are done, remove any running containers by clicking the trash button on Docker Desktop. You can also do it with the CLI: `docker stop $(docker ps -a -q)` and then `docker rm $(docker ps -a -q)`.
* Docker has been set up with `testing = False` and `days_back = int(365 * 2)` (2 years). If you want to change this for testing purposes, modify the settings at .env, rebuild the image, and re-run the container. 

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

## Other Commands

* To update documentation at html/EnvironmentData.html, run `pdoc --html EnvironmentData.py --force`

## APIs

**CORIS** 

The project reads `SensorType` = "Temperature", "Humidity" from the CORIS API. New types can be brought in by adding a new entry to the `readings` object in the `EnvironmentData` class. 

* Individual sensors only make one type of reading, so data is stored as one table per sensor type to prevent excessively repetitive or sparse tables. 
* The `cats/user` endpoint is used to get the readings for all sensors, as well as the list of sensor ids. Example: https://cats.corismonitoring.com/api/cats/user/?ApiKey={mykey}&CatsUserID={myid}.
* The `sensor/historical` endpoint is used to get historical readings for a single sensor and type. Example: https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={mykey}&SensorID={sensor_id}&ReadingType={readingtype}&StartUTC={start_utc}&EndUTC={current_utc}&MinReadingSpacing=600&RequestedOutputFormat=raw
    - You can remove StartUTC and/or EndUTC to get the full historical data (this should be confirmed with the API provider though). 

**HOBOlink**

The HOBOlink integration allows reading data from Onset HOBOlink loggers. See [HOBOLINK_INTEGRATION.md](HOBOLINK_INTEGRATION.md) for details.

## Design Notes

**cron and API key security**

* The typical method of giving keys to containers is through environment variables. cron jobs do not have access to environment variables, so there is no fully secure way to run a cron job that uses key-based authentication. 
* The method I selected is to copy an untracked .env file into the container. This prevents the key from being saved in the GitHub repo. But anyone who gains access to the container itself would have access to the key. The odds of this happening are low, and the risk is also low if the key only allows reading data, not editing it. 
* Security can be enhanced by only allowing the key to be used from white-listed IP addresses. 
* Best practice would be to use a cloud service to schedule jobs instead of cron because cloud jobs can be set up using environment variables. 

**polars vs pandas**

Polars was selected as our data framework because it is faster and more memory efficient, and will therefore help the project scale better.

**EnvironmentData Documentation**

See ./html/EnvironmentData.html for the EnvironmentData class documentation.

**Automated Testing**

Tests are saved in ./test and can by run with `python test/run.py`.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

