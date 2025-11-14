# Environmental Sensor Proof-of-Concept

## Installation

**Configuration**

* Request the .env file from the developer and place it in the project root. Use https://onetimesecret.com to share the contents, create a new .env file and paste the contents into it.

* You can enable/disable different APIs (Conserv, Coris, LI-COR) by changing the enable setting in .env. _Conserv does not currently work with any script except `ingest_all_sources.py`._ You can also enable TESTING mode (pulls limited data for faster testing) and set DAYS_BACK which determines how many days back the historical data pull will attempt.

**Python**

* Install Python 3.8 or later.
* Initialize the virtual environment by running the code below. Virtual environment are used to prevent conflicts with other Python projects. Commands should be run from the project root, ideally by using the terminal after opening the project root in VS Code.

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
```

**Docker**

* Install Docker Desktop from https://docs.docker.com/engine/install/. 

## Repo Organization

- root: 
    - `EnvironmentData.py` is the main class for running combined data pulls.
    - `Dockerfile` for creating the container and running repeated daily pulls.
    - `requirements.txt` listing Python package dependencies.
- clients: Python classes for interacting with the various data sources.
- docs: Additional documentation for the repo and for Python classes. 
- experiments: Code examples for working with the classes and data. 
- jobs: Files for the Docker container to call during initialization and then scheduled via cron
- tests: Automated tests, see the README.md there for more information about automated testing.
- unused: Old code files that are no longer actively used. 

## How To

There are 2 primary ways of running this script: Direct, and via Docker. Using **Docker** will set up a cron task to pull data every minute, and you can use Jupyter to interact with the data pulled by and into the Docker container. For **Direct**, you will run code in Jupyter notebooks and it will only pull data once.

**Direct**

* Run `jupyter notebook` in the terminal to open Jupyter. You'll see two notebooks.
* Open `1-examples-cron.ipynb` and choose Kernel > Restart Kernel and Clear Output of All Cells to reset the notebook. Then, run each code block to see what it does and understand how to work with the EnvironmentData class. 
* Open `2-examples-analysis.ipynb` to learn how to interact with the database to perform certain queries. You can also provide this file to an AI agent to help it understand how to work with the data. 

**Docker**

* Run Docker Desktop (open via Windows menu) to start the docker daemon running in the background (takes ~30 seconds to load). 
* Build the Docker image by running `docker build . -t sensorpull`. This may take a few minutes, especially for installing duckdb so do this when you head off to lunch. 
* Initiate the container with `docker run --name sensorpull-run -p 8888:8888 sensorpull`. You will get an error if you have run this container already and not deleted it. If you get an error, open Docker Desktop and delete the container. 
* Once it is running:
    - The terminal will print a Jupyter URL you can access, starting with http://127.0.0.1:8888. Ctrl + click it to open Jupyter inside the container. You can then open the `data/` folder to see what files have been generated, or open `2-examples-analysis.ipynb` to explore the data pulled by the container. _Make sure you aren't running any other jupyter notebooks or jupyter will say you have the wrong token._
    - Logs will print to terminal. At the start of every minute, you'll see it run `get_current_readings`. Every ten minutes, it'll run `consolidate_readings`.    
    - This one is technical, but if you want you can open a new terminal and run `docker exec -it sensorpull-run /bin/bash` to get an interactive session where you can run linux commands inside the terminal (for example, watch the logs with `tail -f data/EnvironmentData.log`). 
    - You can also open the container in Docker Desktop and navigate to `Files > src/data` to view the project files like `sensor_readings.parquet` and `device_readings.parquet`.
* When you are done, remove any running containers by clicking the trash button on Docker Desktop. You can also do it with the command below:

```bash
docker stop $(docker ps -a -q)
docker rm $(docker ps -a -q)
```

## APIs

See documentation in each client file:
- `modules/licor_client.py`

**Coris** 

The project reads `SensorType` = "Temperature", "Humidity" from the Coris API. New types can be brought in by adding a new entry to the `readings` object in the `EnvironmentData` class. 

* Individual sensors only make one type of reading, so data is stored as one table per sensor type to prevent excessively repetitive or sparse tables. 
* The `cats/user` endpoint is used to get the readings for all sensors, as well as the list of sensor ids. Example: https://cats.corismonitoring.com/api/cats/user/?ApiKey={mykey}&CatsUserID={myid}.
* The `sensor/historical` endpoint is used to get historical readings for a single sensor and type. Example: https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={mykey}&SensorID={sensor_id}&ReadingType={readingtype}&StartUTC={start_utc}&EndUTC={current_utc}&MinReadingSpacing=600&RequestedOutputFormat=raw
    - You can remove StartUTC and/or EndUTC to get the full historical data (this should be confirmed with the API provider though). 

**Conserv (Multi-Tenant)**

The Conserv API supports multiple customer tenants. _Conserv does not currently work with any script except `ingest_all_sources.py`, so it won't work with Docker or the Jupyter notebooks._

* Multi-tenant architecture: Processes data from 5 different Conserv customers (API keys) in a single container run
* Export-based workflow: Uses export → status polling → download pattern due to API design
* Data format: CSV with columns: Sensor Name, Time, Temperature (°C), Humidity (%)
* Schema mapping: Automatically converts °C→°F and maps to existing `SensorReadingF`/`SensorReadingRh` columns
* 7-day limitation: API limits exports to 7-day windows, automatically chunked for historical data
* API Endpoints:
  - `POST /v1/sensors/export` - Launch export job
  - `GET /v1/sensors/export/{uuid}/status` - Poll export status
  - `POST /v1/sensors/export/{uuid}/download` - Get download URL
* Error handling: Individual customer failures don't break the entire process ("No data found" handled gracefully)
* New schema columns: `SensorID_Conserv`, `customer_id`, `source` (preserves backward compatibility)

**Adding New APIs**

Other APIs can be implemented following the established pattern:
1. Create dedicated API client class (see `conserv_client.py` as example)
2. Extend `EnvironmentData.get_current_readings()` and `EnvironmentData.initialize_database()` to include new source
3. Create schema transformation method to map to existing column structure
4. Add unit tests for the new integration

## Testing

The project includes automated tests to validate API clients and data processing functionality.

**Running Tests**

```bash
# Activate virtual environment first
.venv/Scripts/activate

# Install pytest
pip install pytest

# Run all tests
python test/run.py

# Or run individual test files
python -m pytest test/test_licor.py -v
python -m pytest test/ -v
```

**Test Coverage**

Tests are located in the `./test` directory and cover:
- API client functionality (LI-COR, Coris, Conserv)
- Data transformation and schema validation
- Error handling and edge cases
- Integration testing with sample data

**Writing Tests**

When adding new functionality:
1. Create test files in `./test` directory following the `test_*.py` naming convention
2. Use existing test files as templates for API client testing
3. Include both unit tests and integration tests where applicable
4. Test error conditions and edge cases

## Other Commands

* All APIs including Conserv are processed through a unified entry point (`ingest_all_sources.py`) that:
  - Pulls 24-hour data from Coris API (existing functionality preserved)
  - Pulls 24-hour data from all 5 Conserv customers  
  - Merges and consolidates data maintaining schema compatibility
  - Completes entire process in <15 minutes
  - Handles individual API/customer failures gracefully

## Design Notes

**cron and API key security**

* The typical method of giving keys to containers is through environment variables. cron jobs do not have access to environment variables, so there is no fully secure way to run a cron job that uses key-based authentication. 
* The method I selected is to copy an untracked .env file into the container. This prevents the key from being saved in the GitHub repo. But anyone who gains access to the container itself would have access to the key. The odds of this happening are low, and the risk is also low if the key only allows reading data, not editing it. 
* Security can be enhanced by only allowing the key to be used from white-listed IP addresses. 
* Consider using a cloud service to schedule jobs instead of cron because cloud jobs can be set up using environment variables. 

**polars vs pandas**

Polars/parquet was selected as our data framework because it is faster and more memory efficient, and will therefore help the project scale better. Parquet files can be interacted with effeciently using SQL syntax in DuckDB.

**EnvironmentData Documentation**

See ./html/EnvironmentData.html for the EnvironmentData class documentation. To update this documentation, run `pdoc EnvironmentData.py -o docs/ --no-search` after installing [pandoc](https://pypi.org/project/pdoc/).

**Automated Testing**

Tests are saved in ./test and can be run with `python test/run.py`. See the [Testing](#testing) section above for more details on running and writing tests.

