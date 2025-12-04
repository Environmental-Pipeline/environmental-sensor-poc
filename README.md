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
pip install -r requirements-dev.txt
```

**Docker**

* Install Docker Desktop from https://docs.docker.com/engine/install/. 

## Repo Organization

- root: 
    - `EnvironmentData.py` is the main class for running combined data pulls.
    - `Dockerfile` for creating the container and running repeated daily pulls.
    - `requirements.txt` listing Python package dependencies.
- clients: Python classes for interacting with the various data sources.
- modules: Other Python modules used by EnvironmentData.
- docs: Additional documentation for the repo and for Python classes. Commands to update documentation are at the top of each file.
- experiments: Code examples for working with the classes and data.
- jobs: Files for the Docker container to call during initialization and then scheduled via cron.
- tests: Automated tests, see the README.md there for more information about automated testing.
- unused: Old code files that are no longer actively used.

## How It Works

- Instantiate the EnvironmentData class with `api = EnvironmentData()`, this will read information from `.env` and run the historical data pull if the data folder is empty (data folder name will vary based on how we are running the code). 
- Then, the `get_current_readings` method is called to get new readings. 
- The `consolidate_readings` method will combine historical and new readings into analytical tables that are then explored using `duckdb` or any other technology that can explore `parquet` files. 
- `consolidate_readings` will also create diagnostic files `validation-results.csv` (overall data validation checks like missing values, etc.), `validation-detail.csv` (data gap events where time between readings exceeded the expected duration) and `alerts.csv` (readings outside the expected range, will not be created if there are no alerts).

## How To Run

There are 3 ways to running this tool: Direct, via GitHub Actions, and via Docker. Using **Docker** will set up a cron task to pull data every minute, and you can use Jupyter to interact with the data pulled by and into the Docker container. For **Direct**, you will run code in Jupyter notebooks and it will only pull data once.

**Direct**

* Run `jupyter notebook` in the terminal to open Jupyter. You'll see two notebooks.
* Open `1-examples-cron.ipynb` and choose Kernel > Restart Kernel and Clear Output of All Cells to reset the notebook. Then, run each code block to see what it does and understand how to work with the EnvironmentData class. 
* Open `2-examples-analysis.ipynb` to learn how to interact with the database to perform certain queries. You can also provide this file to an AI agent to help it understand how to work with the data. 

**GitHub Actions**

_Runner_

A GitHub action has been set up at [Scheduled Sensor Data Pull](https://github.com/Environmental-Pipeline/environmental-sensor-poc/actions/workflows/scheduled-runner.yml) which runs every 15 minutes to pull the latest readings using the [runner environment](https://github.com/Environmental-Pipeline/environmental-sensor-poc/settings/environments/10400235755/edit) and commit them to the `runner` branch, with data in the `data-runner` folder. To disable it, set the environment variable `RUNNER_ACTIVE` to "False" in the [runner environment](https://github.com/Environmental-Pipeline/environmental-sensor-poc/settings/environments/10400235755/edit).


_Sensor Data Pull_

A GitHub action has been set up at [Sensor Data Pull](https://github.com/Environmental-Pipeline/environmental-sensor-poc/actions/workflows/single-run-test-branch.yml). To use it, go to `Run workflow` and select the `test` branch, with data in the `data-test` folder. Change the settings using the [test environment](https://github.com/Environmental-Pipeline/environmental-sensor-poc/settings/environments/10337000551/edit).

This action will delete the data folder from the selected branch, run the data pipeline using the selected environment variables (using init, then waiting 15 minutes for new readings and running get_current_readings and consolidate_readings), and update the repository with the new data in the `data/` folder. 

You can then checkout and pull the branch to get the new data. 

**Docker**

* Run Docker Desktop (open via Windows menu) to start the docker daemon running in the background (takes ~30 seconds to load). 
* Build the Docker image by running `docker build . -t sensorpull`. This may take a few minutes, especially for installing duckdb so do this when you head off to lunch. 
* Initiate the container with `docker run --name sensorpull-run -p 8888:8888 sensorpull`. You will get an error if you have run this container already and not deleted it. If you get an error, open Docker Desktop and delete the container. 
* Once it is running:
    - The terminal will print a Jupyter URL you can access, starting with http://127.0.0.1:8888. Ctrl + click it to open Jupyter inside the container. You can then open the `data/` folder to see what files have been generated, or open `2-examples-analysis.ipynb` to explore the data pulled by the container. _Make sure you aren't running any other jupyter notebooks or jupyter will say you have the wrong token._
    - Logs will print to terminal. At the start of every minute, you'll see it run `get_current_readings`. Every ten minutes, it'll run `consolidate_readings`.    
    - This one is technical, but if you want you can open a new terminal and run `docker exec -it sensorpull-run /bin/bash` to get an interactive session where you can run linux commands inside the terminal (for example, watch the logs with `tail -f data/EnvironmentData.log`). 
    - You can also open the container in Docker Desktop and navigate to `Files > src/data` to view the project files like `sensor_readings.parquet` and `device_readings.parquet`.
    - The container will initialize with the historical data pull and will then get current readings every 15 minutes (the frequency used by conserv). Each hour, it will consolidate new readings to update analytical tables starting at the 45 minute mark to hopefully be finished by the start of each hour.
* To run a second jupyter notebook from your machine, use `jupyter notebook --port 8889` so it doesn't conflict with the container which uses port 8888.
* When you are done, remove any running containers by clicking the trash button on Docker Desktop. You can also do it with the command below:

```bash
docker stop $(docker ps -a -q)
docker rm $(docker ps -a -q)
```

Or use this command to remove containers and start a clean one:
```bash
docker stop $(docker ps -a -q)
docker rm $(docker ps -a -q)
docker run --name sensorpull-run -p 8888:8888 sensorpull
```

## APIs

See documentation at the top of each client file:
- `clients/conserv_client.py`
- `clients/coris_client.py`
- `clients/licor_client.py`

General notes:
- CORIS and LI-COR pull historical data in 15-minute increments. Conserv takes an argument which we set to 15 minutes in order to keep data aligned between different sources. 

**Adding New APIs**

Other APIs can be implemented following the established pattern:
1. Create dedicated API client class. See `licor_client.py` as probably the best example (coris was the first so has no transformation function) or `conserv_client.py` if the API sends back tabular data for devices and not the typical data by sensor as that data will need to be transformed to a sensor-level format.
2. Extend `EnvironmentData.initialize_database()` and `EnvironmentData.get_current_readings()` to include new source (see the code there currently).
3. Create a schema transformation method to map to existing column structure. See `EnvironmentData.validate_sensors()` or `experimentsexperiments\1-examples-cron.ipynb` (data is printed at the end) for the expected format.
4. Add unit tests for the new integration (see examples in the `tests/` folder).

## Testing

The project includes automated tests to validate API clients and data processing functionality.

**Running Tests**

```bash
# Activate virtual environment first
.venv/Scripts/activate

# Install pytest
pip install pytest

# Run all tests
python tests/run.py

# Or run individual test files
python -m pytest tests/test_licor.py -v
```

To run a test with code coverage including creating a report at `htmlcov/index.html`.

```bash
coverage html --include="modules/*"
```

## Design Notes

**cron and API key security**

* The typical method of giving keys to containers is through environment variables. cron jobs do not have access to environment variables, so there is no fully secure way to run a cron job that uses key-based authentication. 
* The method I selected is to copy an untracked .env file into the container. This prevents the key from being saved in the GitHub repo. But anyone who gains access to the container itself would have access to the key. The odds of this happening are low, and the risk is also low if the key only allows reading data, not editing it. 
* Security can be enhanced by only allowing the key to be used from white-listed IP addresses. 
* Consider using a cloud service to schedule jobs instead of cron because cloud jobs can be set up using environment variables. 

**polars vs pandas**

Polars/parquet was selected as our data framework because it is faster and more memory efficient, and will therefore help the project scale better. Parquet files can be interacted with effeciently using SQL syntax in DuckDB.

**helpful commands**

Quickly update runner and test branches:

```bash
git checkout runner
git merge main --no-edit
git push
git checkout test
git merge main --no-edit
git push
```
