# Environmental Sensor Proof-of-Concept

_Commands below should be run from the project root, ideally by using Terminal > New Terminal after opening the project root in VS Code._

## How It Works

The pipeline runs four scheduled jobs to collect, process, and deliver environmental sensor data:

1. **Pull** (every 15 min): Fetches readings from Coris (2 accounts: Peabody + Libraries) and Conserv (5 customer accounts) APIs.
2. **Consolidate** (hourly at :45): Combines new readings with historical data, validates sensor names against the 20-char Yale convention, filters non-conforming sensors to `rejected_sensors_tracking.csv`, and enriches data with outdoor weather from the Open-Meteo API using building coordinates.
3. **Export** (daily 2:00 AM UTC): Generates an incremental delta parquet file using high water mark tracking.
4. **Upload** (daily 2:15 AM UTC, host cron): Pushes daily export to Microsoft Fabric and AWS S3 via `azcopy` and `aws` CLI.

## Installation

**Configuration**

Request that the developer share the contents of their `.env` file via https://onetimesecret.com, create a new `.env` file and paste the contents into it. An example is provided at `.env-example`.

**Python**

* Install Python 3.8 or later.
* Initialize the virtual environment by running the code below.

_Virtual environments are used to prevent conflicts with other Python projects. All Python commands in this repo will require the environment to be activated first. VS Code will usually do this automatically when opening a new terminal._

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
    - `DISASTER_RECOVERY.md` disaster recovery and deployment guide.
- .github: copilot instructions and GitHub actions (workflows).
- clients: Python classes for interacting with the various data sources.
- data:
    - `building_coordinates.csv` building lat/long used for weather enrichment.
- docs: Additional documentation for the repo and for Python classes. Commands to update documentation are at the top of each file.
- experiments: Code examples for working with the classes and data.
- jobs: Files for the Docker container to call during initialization and then scheduled via cron.
    - `jobs/1-pull.py` pulls readings from Coris and Conserv APIs.
    - `jobs/2-consolidate.py` consolidates, validates, and weather-enriches readings.
    - `jobs/3-export-daily.py` generates incremental daily export with high water mark.
- modules: Other Python modules used by EnvironmentData.
    - `modules/consolidation.py` consolidation logic, lookup tables, cubes.
    - `modules/weather_enrichment.py` enriches sensor data with Open-Meteo outdoor weather by building location.
    - `modules/sensor_name_validator.py` validates sensor names against the 20-char Yale convention, filters non-conforming sensors.
    - `modules/rejected_sensors_tracker.py` tracks rejected sensors over time, generates needs_attention reports.
- scripts:
    - `scripts/upload-sensor-readings-parquet.sh` uploads daily export to Fabric and S3.
- templates: Excel templates for operations that create Excel output. Excel output can be modified by changing these templates.
- tests: Automated tests, see the README.md there for more information about automated testing.
- unused: Old code files that are no longer actively used.

Detailed interactive code documentation is available at `docs/index.html`. To update this, run `& ".venv\Scripts\Activate.ps1"; pdoc EnvironmentData.py clients/ modules -o docs/`.

## Production Deployment

The pipeline runs on a Yale Spinup VM:

- **Server**: `spinup-002f53.spinup.yale.edu` (10.5.203.61), Ubuntu 22.04, t3a.medium
- **Containers**: Two Docker containers run on the host:
  - `sensorpull-run` — the data pipeline (this repo)
  - `sensor-dashboard` — QA monitoring dashboard (separate repo)
- Both containers use `--restart unless-stopped`
- **Access**: Requires Yale VPN for SSH

### Cron Schedule

| Schedule | Job | Description |
|----------|-----|-------------|
| `*/15 * * * *` | `jobs/1-pull.py` | Pull from Coris and Conserv APIs |
| `45 * * * *` | `jobs/2-consolidate.py` | Consolidate, validate, weather enrich |
| `0 2 * * *` | `jobs/3-export-daily.py` | Generate incremental daily export |
| `15 2 * * *` (host) | `scripts/upload-sensor-readings-parquet.sh` | Upload to Fabric and S3 |

## Data Sources

| Source | Identifier | Sensors |
|--------|-----------|---------|
| Coris Peabody | `CATS_USER_ID=2496` | Temperature, humidity |
| Coris Yale Libraries | `CATS_USER_ID_LIBRARIES=3088` | Lux |
| Conserv | 5 customer accounts | Temperature, humidity |
| Open-Meteo | Archive + Forecast APIs | Outdoor weather data |

## Output Schema

The main output is `sensor_readings.parquet` with 29 columns:

**Core**: `SensorReadingUTC`, `QueryUTC`, `Source`, `DeviceID`, `DeviceName`, `SensorID`, `SensorName`, `SensorType`

**Readings**: `SensorReadingF`, `SensorReadingRh`, `SensorReadingLux`

**Metadata**: `SensorReadingUTC_SecondsFromPrior`, `Historical`

**Weather** (16 columns): `weather_temp_f`, `weather_humidity_pct`, `weather_dew_point_f`, `weather_apparent_temp_f`, pressure, precipitation, rain, snowfall, cloud cover, wind speed, wind direction, solar radiation, WMO weather code, WMO weather description

## Sensor Name Validation

All sensor names must be exactly 20 characters following the Yale naming convention:

- **Position 1**: Collection unit (`P`, `L`, `B`, `A`, `I`, `S`)
- **Positions 2-6**: Building code (`YPM`, `KGL`, `ESC`, `CSC`, `BARCH`, `YCBA`, `KAHN`, `OYAG`, `FAST`, `SH`)

Non-conforming sensors are filtered out during consolidation and tracked in `rejected_sensors_tracking.csv`. Use the validator tool at https://env-sensor-tools.yalepages.org to check names.

## How To Run

There are 3 ways to run this tool:

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
* Start the container:

```bash
docker run -d --name sensorpull-run --restart unless-stopped -p 8888:8888 sensorpull
```

Once it is running:

* The container will initialize with the historical data pull and will then get current readings every 15 minutes (the frequency used by Conserv). Each hour, it will consolidate new readings to update analytical tables starting at the 45 minute mark to hopefully be finished by the start of each hour.
* The terminal will print a Jupyter URL you can access, starting with http://127.0.0.1:8888. Ctrl + click it to open Jupyter inside the container. You can then open the `data/` folder to see what files have been generated, or open `2-examples-analysis.ipynb` to explore the data pulled by the container. _Make sure you aren't running any other Jupyter notebooks or Jupyter will say you have the wrong token._
* Logs will print to terminal. Every 15 minutes, you'll see it run `get_current_readings`. Every 45 minutes, it'll run `consolidate_readings`.
* To open an interactive terminal session in the container, run `docker exec -it sensorpull-run /bin/bash`. You can then run linux commands inside the terminal (for example, watch the logs with `tail -f data/EnvironmentData.log`).
* You can also open the container in Docker Desktop and navigate to `Files > src/data` to view the project files like `sensor_readings.parquet` and `device_readings.parquet`.
* To run a second jupyter notebook from your machine, use `jupyter notebook --port 8889` so it doesn't conflict with the container which uses port 8888.
* To view logs from a detached container, use `docker logs -f sensorpull-run`.

**GitHub Actions**

We attempted to set up GitHub actions to periodically pull data and save it to the repository. It didn't quite work due to GitHub's limits on actions for free accounts, but it may still be useful.

Once the action finishes, you can checkout and pull the appropriate branch to get the new data, or open the branch in GitHub and download data from there.

**[Clear Data & Run Init, Wait, Pull, Consolidate](https://github.com/Environmental-Pipeline/environmental-sensor-poc/actions/workflows/single-run-test-branch.yml)**

Manually-triggered test run. Go to `Run workflow` and select the `test` branch, with data in the `data-test` folder. Change the settings using the [GitHub test environment](https://github.com/Environmental-Pipeline/environmental-sensor-poc/settings/environments/10337000551/edit).

This action will delete the data folder from the `test` branch, run the data pipeline using the selected environment variables (using init, then waiting 15 minutes for new readings and running get_current_readings and consolidate_readings), and update the repository with the new data in the `data-test/` folder.

**[Scheduled Pull on Runner Branch](https://github.com/Environmental-Pipeline/environmental-sensor-poc/actions/workflows/scheduled-runner.yml)**

Runs every 15 minutes to pull the latest readings using the [GitHub runner environment](https://github.com/Environmental-Pipeline/environmental-sensor-poc/settings/environments/10400235755/edit) and commit them to the `runner` branch, with data in the `data-runner` folder. To disable it, set the environment variable `RUNNER_ACTIVE` to "False" in the [GitHub runner environment](https://github.com/Environmental-Pipeline/environmental-sensor-poc/settings/environments/10400235755/edit).

_This action has been disabled because it exceeded the limits on GitHub free accounts and was erroring out. It can now be triggered manually on the `runner` branch._

## APIs

See detailed documentation at the top of each client file:
* [clients/conserv_client.py](https://github.com/Environmental-Pipeline/environmental-sensor-poc/blob/main/clients/conserv_client.py)
* [clients/coris_client.py](https://github.com/Environmental-Pipeline/environmental-sensor-poc/blob/main/clients/coris_client.py)
* [clients/licor_client.py](https://github.com/Environmental-Pipeline/environmental-sensor-poc/blob/main/clients/licor_client.py)

General notes:

* CORIS and LI-COR pull historical data in 15-minute increments by default. Conserv is more flexible, but we've set it to also use 15 minutes to keep data aligned between different sources.

**Adding New APIs**

Other APIs can be implemented following the established pattern:
1. Create a dedicated API client. See [clients/licor_client.py](https://github.com/Environmental-Pipeline/environmental-sensor-poc/blob/main/clients/licor_client.py) as probably the best example (coris was the first so has no transformation function) or [clients/conserv_client.py](https://github.com/Environmental-Pipeline/environmental-sensor-poc/blob/main/clients/conserv_client.py) if the API sends back tabular data for devices and not the typical data by sensor as that data will need to be transformed to a sensor-level format.
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

## Related Resources

- **QA Dashboard repo**: `git@github.com:aha124/env-sensor-dashboard.git`
- **Sensor Name Validator**: https://env-sensor-tools.yalepages.org
- **DR document**: `DISASTER_RECOVERY.md` in this repo
- **Fabric landing zone**: `cultural-heritage-environmental-monitoring-dev/Incoming/`
- **S3 bucket**: `s3://spinup-002f52-yale-env-sensor-poc-data/`

## Design Notes

**cron and API key security**

* The typical method of giving keys to containers is through environment variables. cron jobs do not have access to environment variables, so there is no fully secure way to run a cron job that uses key-based authentication.
* The method I selected is to copy an untracked .env file into the container. This prevents the key from being saved in the GitHub repo. But anyone who gains access to the container itself would have access to the key. The odds of this happening are low, and the risk is also low if the key only allows reading data, not editing it.
* Security can be enhanced by only allowing the key to be used from white-listed IP addresses.
* Consider using a cloud service to schedule jobs instead of cron because cloud jobs can be set up using environment variables.

**polars vs pandas**

Polars/parquet was selected as our data framework because it is faster and more memory efficient, and will therefore help the project scale better. Parquet files can be interacted with efficiently using SQL syntax in DuckDB.

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
