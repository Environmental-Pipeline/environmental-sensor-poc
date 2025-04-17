# Environmental Sensor Proof-of-Concept

## Python Setup

You may want to first get the project running in Python, in order to better understand it.

Virtual environment are used to prevent conflicts with other Python projects. 

_Commands should be run from the project root, possibly by using the terminal after opening the project root in VS Code.__

* Install Python 3.8 or later.
* Initialize the virtual environment by running `python -m venv .venv`, wait until it finishes, and then `.venv/Scripts/activate` (on Windows).
* Install the necessary packages (including for Coris API and weather enrichment) into the environment with `pip install -r requirements.txt`.
* Request the `.env` file from the developer (or create one based on the requirements below) and place it in the project root.
* You can now run code. To view examples, run `jupyter notebook` to start Jupyter Notebook and open `examples-cron.ipynb` and `examples-analysis.ipynb`.


## Docker Setup

To use Docker, you'll need to use Docker Desktop to build the image and start a container. 

_Commands should be run from the project root, possibly by using the terminal after opening the project root in VS Code._

* Install Docker Desktop from https://docs.docker.com/engine/install/.
* Run Docker Desktop to start the docker daemon running in the background. 
* Ensure you have a `.env` file in the project root containing the necessary variables (see below).
* Build the Docker image by running `docker build . -t environment`. This will read the Dockerfile and use it to build an image, which we'll use to initiate a container later on.
* Initiate the container with `docker run --name environment-run -p 8888:8888 environment`.
* Once it is running, there are a few ways you can interact with it:
    - **Logs:** Container logs will print to the terminal where you ran `docker run`. At the start of every minute, you'll see it run `get_current_readings`. Every ten minutes, it'll run `consolidate_readings`. Daily at 1:00 AM (server time), it will run the weather enrichment process.
    - **Jupyter:** The terminal will print a Jupyter URL you can access, starting with http://127.0.0.1:8888. Ctrl + click it to open an interactive Jupyter Notebook. You can then open `examples-analysis.ipynb` and execute code using the latest data.
    - **Shell:** You can open an interactive session in a new terminal with `docker exec -it environment-run /bin/bash`.
    - **Files:** Use the shell or Docker Desktop's file browser to view the project files inside the container (under `/src/`). Key data files include `data/sensor_readings.parquet`, `data/device_readings.parquet`, `data/sensors.parquet`, `data/devices.parquet`, `data/utcs.parquet`, and the enriched `data/sensor_readings_with_weather.parquet`.
* When you are done, remove any running containers by clicking the trash button on Docker Desktop or using the CLI: `docker stop environment-run` and then `docker rm environment-run`.
* The initial run of the container will perform a potentially lengthy historical data fetch from the Coris API based on `DAYS_BACK` in `.env`. Subsequent runs will start much faster.

## Configuration (`.env` File)

An untracked `.env` file in the project root is used for configuration and secrets:

*   **`CORIS_API_KEY` (Required):** Your API key for the Coris Monitoring service.
*   **`CATS_USER_ID` (Required):** Your User ID for the Coris Monitoring service.
*   `DAYS_BACK` (Optional, Default: 730): How many days of historical Coris data to pull during the initial database setup.
*   `TESTING` (Optional, Default: False): Use `True` to run initialization and tests with a smaller subset of sensors (faster, fewer API calls).

## Weather Data Enrichment

This project includes a process to enrich the collected sensor data with historical weather information obtained from the free [Open-Meteo API](https://open-meteo.com/).

*   **Process:** A daily cron job (`jobs/3-enrich.py`) runs inside the Docker container.
*   **Data Fetched:** It fetches hourly historical weather data (temperature, humidity, weather code) corresponding to the timestamps of the sensor readings.
*   **Location:** Currently, weather data is fetched for a fixed location representing the Yale Peabody Museum (approx. lat 41.3157, lon -72.9211).
*   **Matching:** Weather data is merged with sensor data by finding the closest hourly weather reading within a 30-minute tolerance window of each sensor reading timestamp.
*   **Output:** The enriched data is saved to `data/sensor_readings_with_weather.parquet`.

## Other Commands

* To update documentation at html/EnvironmentData.html, run `pdoc --html EnvironmentData.py --force`

## APIs

**Coris** 

The project reads `SensorType` = "Temperature", "Humidity" from the Coris API. New types can be brought in by adding a new entry to the `readings` object in the `EnvironmentData` class. 

* Individual sensors only make one type of reading, so data is stored as one table per sensor type to prevent excessively repetitive or sparse tables. 
* The `cats/user` endpoint is used to get the readings for all sensors, as well as the list of sensor ids. Example: https://cats.corismonitoring.com/api/cats/user/?ApiKey={mykey}&CatsUserID={myid}.
* The `sensor/historical` endpoint is used to get historical readings for a single sensor and type. Example: https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={mykey}&SensorID={sensor_id}&ReadingType={readingtype}&StartUTC={start_utc}&EndUTC={current_utc}&MinReadingSpacing=600&RequestedOutputFormat=raw
    - You can remove StartUTC and/or EndUTC to get the full historical data (this should be confirmed with the API provider though). 

**Importing Other APIs**

Other APIs can be implemented to replace or supplement Coris by expanding `EnvironmentData.initialize_database()` and `EnvironmentData.get_current_readings()` to read sensors from the new API.

Take note of the IDs SensorID_Coris and DeviceID_Coris. You'll need to rename these to be more general, or add new ID columns for each new API.


## Design Notes

**cron and API key security**

* The typical method of giving keys to containers is through environment variables. The standard `cron` daemon does not easily inherit the container's environment variables. 
* The method selected here is to copy an untracked `.env` file into the container during the build process. The Python scripts run by cron then load this file using `python-dotenv`. This prevents the key from being saved in the GitHub repo. However, anyone who gains access to the running container filesystem would have access to the key. The risk is mitigated if the key only allows reading data.
* Security can be enhanced by only allowing the key to be used from white-listed IP addresses (if supported by the API provider).
* Best practice for cloud deployments would often involve using managed secret stores and cloud-native job scheduling services that handle environment variables securely.

**polars vs pandas**

Polars was selected as our data framework because it is faster and more memory efficient, and will therefore help the project scale better.

**SSL Verification Workaround**

*   API calls to the Coris Monitoring service (`cats.corismonitoring.com`) currently have SSL certificate verification disabled (`verify=False` in `requests.get`). This is a workaround for potential issues with local/container SSL certificate stores. In a production environment, it's recommended to resolve the underlying certificate issue rather than disabling verification.

**EnvironmentData Documentation**

See ./html/EnvironmentData.html for the EnvironmentData class documentation.

**Automated Testing**

Tests are saved in ./test and can by run with `python test/run.py`.

