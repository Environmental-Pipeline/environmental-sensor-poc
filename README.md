# Environmental Sensor Proof-of-Concept

_Commands below should be run from the project root, ideally by using Terminal > New Terminal after opening the project root in VS Code._

## Installation

**Configuration**

Request that the developer share the contents of their `.env` file via https://onetimesecret.com, create a new `.env` file and paste the contents into it. An example is provided at `.evn-example`.

**Python**

* Install Python 3.8 or later.
* Initialize the virtual environment by running the code below. 

_Virtual environment are used to prevent conflicts with other Python projects. All Python commands in this repo will require the environment to be activated first. VS Code will usually do this automatically when opening a new terminal._

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements-dev.txt
```

**Docker**

* Install Docker Desktop from https://docs.docker.com/engine/install/. 

## Repo Organization

- root: 
    - `EnvironmentData.py` is the main class for running combined data pulls. It also pulls from `clients/` and `modules/`.
    - `Dockerfile` for creating the container and running repeated daily pulls.
    - `requirements.txt` required Python package dependencies. 
    - `requirements-dev.txt` dependencies for the development environment.
    - `requirements-docker.txt` dependencies for the Docker environment.
    - `.env-example` an example of a .env file. 
- .github: copilot instructions and GitHub actions (workflows). 
- clients: Python classes for interacting with the various data sources.
- docs: Additional documentation for the repo and for Python classes. Commands to update documentation are at the top of each file.
- experiments: Code examples for working with the classes and data.
- jobs: Files for the Docker container to call during initialization and then scheduled via cron.
- modules: Other Python modules used by EnvironmentData.
- templates: Excel templates for operations that create Excel output. Excel output can be modified by changing these templates. 
- tests: Automated tests, see the README.md there for more information about automated testing.
- unused: Old code files that are no longer actively used.

Detailed interactive code documentation is available at `docs/index.html`. To update this, run `& ".venv\Scripts\Activate.ps1"; pdoc EnvironmentData.py clients/ modules -o docs/`.

## How It Works

- Instantiate the EnvironmentData class with `api = EnvironmentData()`, this will read information from `.env` and run the historical data pull if the data folder is empty. The data folder name will vary based on how we are running the code (local/Docker or via different GitHub actions). 
- The `get_current_readings` method is called to get new readings. 
- The `consolidate_readings` method will combine historical and new readings into analytical tables that are then explored using `duckdb` or any other technology that can explore `parquet` files (we use parquet to keep data files small and fast). 
- `consolidate_readings` will also create diagnostic files `validation-results.csv` (overall data validation checks like missing values, etc.), `validation-detail.parquet` (data gap events where time between readings exceeded the expected duration), `alerts.txt` (readings outside the expected range, will not be created if there are no alerts), and `consolidated-data-sample.xlsx` (an excel file with a sample of the data).

## How To Run

There are 3 ways to running this tool:

**Direct**

Test and experiment with the code.

* Run `jupyter notebook` in the terminal to open Jupyter. 
* Open `experiments/1-examples-pull-data.ipynb` and choose Kernel > Restart Kernel and Clear Output of All Cells to reset the notebook. Then, run each code block to see what it does and understand how to work with the EnvironmentData class. 
* Open `experiments/2-examples-analysis.ipynb` to learn how to interact with the database to perform certain queries. You must run examples-pull-data first. You can also provide this file to an AI agent to help it understand how to work with the data. 

**Docker**

Use cron to pull new data every 15 minutes. This gets a bit complex since it uses a container, but it can be run locally or pushed to a cloud server for constant monitoring. 

* Run Docker Desktop (open via Windows menu) to start the docker daemon running in the background (takes ~30 seconds to load). 
* Build the Docker image by running `docker build . -t sensorpull`. This may take a few minutes, especially for installing duckdb so do this when you head off to lunch. 
* If you have Jupyter running, close it. Multiple Jupyter sessions can cause problems. 
* Initiate the container with this command which will clear old containers that might otherwise cause errors: 

```bash
docker stop $(docker ps -a -q)
docker rm $(docker ps -a -q)
docker run --name sensorpull-run -p 8888:8888 sensorpull
```

Once it is running:

* The container will initialize with the historical data pull and will then get current readings every 15 minutes (the frequency used by Conserv). Each hour, it will consolidate new readings to update analytical tables starting at the 45 minute mark to hopefully be finished by the start of each hour.  
* The terminal will print a Jupyter URL you can access, starting with http://127.0.0.1:8888. Ctrl + click it to open Jupyter inside the container. You can then open the `data/` folder to see what files have been generated, or open `2-examples-analysis.ipynb` to explore the data pulled by the container. _Make sure you aren't running any other Jupyter notebooks or Jupyter will say you have the wrong token._
* Logs will print to terminal. Every 15 minutes, you'll see it run `get_current_readings`. Every 45 minutes, it'll run `consolidate_readings`.
* To open a new interactions terminal session in the container, run `docker exec -it sensorpull-run /bin/bash`. You can then run linux commands inside the terminal (for example, watch the logs with `tail -f data/EnvironmentData.log`). 
* You can also open the container in Docker Desktop and navigate to `Files > src/data` to view the project files like `sensor_readings.parquet` and `device_readings.parquet`.
* To run a second jupyter notebook from your machine, use `jupyter notebook --port 8889` so it doesn't conflict with the container which uses port 8888.
* If you close a terminal while the container is running, it may continue in the background. To be sure a container has stopped, use Docker Desktop or restart your computer. 

**GitHub Actions**

We attempted to set up GitHub actions to periodically pull data and save it to the repository. It didn't quite work due to GitHub's limits on actions for free accounts, but it may still be useful.

Once the action finishes, you can checkout and pull the appropriate branch to get the new data, or open the branch in GitHub and download data from there.

**[Clear Data & Run Init, Wait, Pull, Consolidate](https://github.com/Environmental-Pipeline/environmental-sensor-poc/actions/workflows/single-run-test-branch.yml)**

Manually-triggered test run. Go to `Run workflow` and select the `test` branch, with data in the `data-test` folder. Change the settings using the [test environment](https://github.com/Environmental-Pipeline/environmental-sensor-poc/settings/environments/10337000551/edit).

This action will delete the data folder from the `test` branch, run the data pipeline using the selected environment variables (using init, then waiting 15 minutes for new readings and running get_current_readings and consolidate_readings), and update the repository with the new data in the `data-test/` folder.

**[Scheduled Pull on Runner Branch](https://github.com/Environmental-Pipeline/environmental-sensor-poc/actions/workflows/scheduled-runner.yml)**

Runs every 15 minutes to pull the latest readings using the [runner environment](https://github.com/Environmental-Pipeline/environmental-sensor-poc/settings/environments/10400235755/edit) and commit them to the `runner` branch, with data in the `data-runner` folder. To disable it, set the environment variable `RUNNER_ACTIVE` to "False" in the [runner environment](https://github.com/Environmental-Pipeline/environmental-sensor-poc/settings/environments/10400235755/edit).

_This action has been disabled because it exceeded the limits on GitHub free accounts and was erroring out. It can now be triggered manually on the `runner` branch._


## APIs

See detailed documentation at the top of each client file:
* `clients/conserv_client.py`
* `clients/coris_client.py`
* `clients/licor_client.py`

General notes:

* CORIS and LI-COR pull historical data in 15-minute increments by default. Conserv is more flexible, but we've set it to also use 15 minutes to keep data aligned between different sources. 

**Adding New APIs**

Other APIs can be implemented following the established pattern:
1. Create a dedicated API client. See `licor_client.py` as probably the best example (coris was the first so has no transformation function) or `conserv_client.py` if the API sends back tabular data for devices and not the typical data by sensor as that data will need to be transformed to a sensor-level format.
2. Extend `EnvironmentData.initialize_database()` and `EnvironmentData.get_current_readings()` to include new source.
3. Create a schema transformation method to map to existing column structure. See `EnvironmentData.validate_sensors()` or `experiments/1-examples-pull-data.ipynb` (data is printed at the end) for the expected format.
4. Add unit tests for the new integration (see examples in the `tests/` folder).

## Testing

The project includes automated tests to validate API clients and data processing functionality.

**Running Tests**

```bash
.venv/Scripts/activate # Activate virtual environment
python tests/run.py # Run all tests
python -m pytest tests/test_licor.py -v # Or run an individual test file
```

To run a test with code coverage including creating a report at `htmlcov/index.html`:

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
git pull
git merge main --no-edit
git push
git checkout test
git pull
git merge main --no-edit
git push
git checkout main
```
