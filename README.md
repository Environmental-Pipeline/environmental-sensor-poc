# Environmental Sensor Proof-of-Concept

## Common Commands

```bash
.venv/Scripts/activate
python ingest_all_sources.py
```

## Python Setup

You may want to first get the project running in Python, in order to better understand it.

Virtual environment are used to prevent conflicts with other Python projects. 

_Commands should be run from the project root, possibly by using the terminal after opening the project root in VS Code.__

* Install Python 3.8 or later.
* Initialize the virtual environment by running `python -m venv .venv`, wait until it finishes, and then `.venv/Scripts/activate` (on Windows).
* Install the necessary packages into the environment with `pip install -r requirements.txt`.
* Request the .env file from the developer and place it in the project root.
* You can now run code. To view examples, run `jupyter notebook` to start Jupyter Notebook and open `examples-cron.ipynb` and `examples-analysis.ipynb`.


## Docker Setup

To use Docker, you'll need to use Docker Desktop to build the image and start a container. 

_Commands should be run from the project root, possibly by using the terminal after opening the project root in VS Code._

* Install Docker Desktop from https://docs.docker.com/engine/install/. 
* Run Docker Desktop to start the docker daemon running in the background. 
* Request the .env file from the developer and place it in the project root.
* Build the Docker image by running `docker build . -t sensorpull`. This will read the Dockerfile and use it to build an image, which we'll use to initiate a container later on.
* Initiate the container with `docker run --name sensorpull-run -p 8888:8888 sensorpull`.
* Once it is running, there are a few ways you can interact with it:
    - Logs will print to terminal. At the start of every minute, you'll see it run `get_current_readings`. Every ten minutes, it'll run `consolidate_readings`.
    - The terminal will print a Jupyter URL you can access, starting with http://127.0.0.1:8888. Ctrl + click it to open an interactive Jupyter Notebook. You can then open examples.ipynb and execute code. The data may look the same, but it's actually reading from data populated by cron!
    - Make sure you aren't running any other jupyter notebooks or jupyter will say you have the wrong token.
    - You can open an interactive session in a new terminal with `docker exec -it sensorpull-run /bin/bash` and watch the logs with `tail -f data/EnvironmentData.log`, or run any other Linux command in the container. 
    - Open the container in Docker Desktop and navigate to Files > src to view the project files like `sensor_readings.parquet` and `device_readings.parquet`.
* When you are done, remove any running containers by clicking the trash button on Docker Desktop. You can also do it with the CLI: `docker stop $(docker ps -a -q)` and then `docker rm $(docker ps -a -q)`.
* Docker has been set up with `testing = False` and `days_back = int(365 * 2)` (2 years). If you want to change this for testing purposes, modify the settings at .env, rebuild the image, and re-run the container. 

To modify the behavior of the container:

* .env contains variables that can be used for testing or to modify settings:
  - **Existing Coris settings:**
    - `CORIS_API_KEY`: Your Coris API key
    - `CATS_USER_ID`: Your Coris user ID
    - `DAYS_BACK`: How many days of historical data to pull during initialization
    - `TESTING`: Use =True to run a test that only pulls data for 6 sensors
  - **New Conserv multi-tenant settings:**
    - `CONSERV_ENABLED`: Set to "True" to enable Conserv API integration
    - `RUN_WINDOW_HOURS`: Hours of data to pull per run (default: 24)
    - `CONSERV_API_KEY_1545`: API key for Conserv customer 1545
    - `CONSERV_API_KEY_333`: API key for Conserv customer 333
    - `CONSERV_API_KEY_307`: API key for Conserv customer 307
    - `CONSERV_API_KEY_2671`: API key for Conserv customer 2671
    - `CONSERV_API_KEY_1696`: API key for Conserv customer 1696

* **Container execution modes:**
  - **Default mode**: Runs unified data ingestion once (`ingest_all_sources.py`) - ideal for cron scheduling
  - **Development mode**: Set `JUPYTER_MODE=true` to enable Jupyter + continuous monitoring
  
* jobs/cronjobs defines how frequently the unified ingestion runs. The default is hourly, but can be adjusted using https://crontab.guru/ for cron expressions.

## Other Commands

* To update documentation at html/EnvironmentData.html, run `pdoc --html EnvironmentData.py --force`

## APIs

**Coris** 

The project reads `SensorType` = "Temperature", "Humidity" from the Coris API. New types can be brought in by adding a new entry to the `readings` object in the `EnvironmentData` class. 

* Individual sensors only make one type of reading, so data is stored as one table per sensor type to prevent excessively repetitive or sparse tables. 
* The `cats/user` endpoint is used to get the readings for all sensors, as well as the list of sensor ids. Example: https://cats.corismonitoring.com/api/cats/user/?ApiKey={mykey}&CatsUserID={myid}.
* The `sensor/historical` endpoint is used to get historical readings for a single sensor and type. Example: https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={mykey}&SensorID={sensor_id}&ReadingType={readingtype}&StartUTC={start_utc}&EndUTC={current_utc}&MinReadingSpacing=600&RequestedOutputFormat=raw
    - You can remove StartUTC and/or EndUTC to get the full historical data (this should be confirmed with the API provider though). 

**Conserv (Multi-Tenant)**

The project also integrates with the Conserv API as a second data source, supporting multiple customer tenants.

* **Multi-tenant architecture**: Processes data from 5 different Conserv customers (API keys) in a single container run
* **Export-based workflow**: Uses export → status polling → download pattern due to API design
* **Data format**: CSV with columns: Sensor Name, Time, Temperature (°C), Humidity (%)
* **Schema mapping**: Automatically converts °C→°F and maps to existing `SensorReadingF`/`SensorReadingRh` columns
* **7-day limitation**: API limits exports to 7-day windows, automatically chunked for historical data
* **API Endpoints**:
  - `POST /v1/sensors/export` - Launch export job
  - `GET /v1/sensors/export/{uuid}/status` - Poll export status
  - `POST /v1/sensors/export/{uuid}/download` - Get download URL
* **Error handling**: Individual customer failures don't break the entire process ("No data found" handled gracefully)
* **New schema columns**: `SensorID_Conserv`, `customer_id`, `source` (preserves backward compatibility)

**Hobolink Sensors**
* **API Endpoints**:
  - GET https://api.licor.cloud/v2/devices?includeSensors=true Identify available sensors.
  - GET https://api.licor.cloud/v2/data?deviceSerialNumber=X&sensorSerialNumber=Y&startTime=Z&endTime=W Get sensor data.
* **Data format**: JSON. Query devices then loop over devices and sensors to get readings. Run `python test/explore_hobolink.py` to save sample output to `samples/`.
* Only allows data within the last year. 
* The data API response has a property "moreResults" that is always False. The code will error out if it is ever true, in which case this would need to be handled. It isn't possible to handle it now since I can't find any data that has it true, so I'm not sure what that data would look like. It is either an unused property or only comes into play when there is too much data in one record, which didn't happen when I searched the max duration (one year) across all sensors.
* Most readings come in Fahrenheit, but some do come through in Celsius, in which case we convert to Fahrenheit for consistency.
* The same Sensor number can be used in multiple devices, so we create a combo ID of device and sensor serial numbers for Sensor ID.


**Unified Data Integration**

Both APIs are processed through a unified entry point (`ingest_all_sources.py`) that:
* Pulls 24-hour data from Coris API (existing functionality preserved)
* Pulls 24-hour data from all 5 Conserv customers  
* Merges and consolidates data maintaining schema compatibility
* Completes entire process in <15 minutes
* Handles individual API/customer failures gracefully

**Adding New APIs**

Other APIs can be implemented following the established pattern:
1. Create dedicated API client class (see `conserv_client.py` as example)
2. Extend `EnvironmentData.get_current_readings()` to include new source
3. Create schema transformation method to map to existing column structure
4. Add new identifier columns (e.g., `SensorID_NewAPI`) while preserving existing ones
5. Update unified entry point script to include new source
6. Add comprehensive unit tests for the new integration


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

