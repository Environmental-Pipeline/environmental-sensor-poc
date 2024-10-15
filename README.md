# Environmental Sensor Proof-of-Concept


## Local Setup

If you just want to run Docker, you don't need to set up Python, and vice-versa. 

**Python Setup**

Virtual environment are used to prevent conflicts with other Python projects. It is assumed that the user has Python 3.8 or later installed. Commands should be run from the project root, possibly by using the terminal after opening the project root in VS Code.

* To initialize the virtual environment, run `python -m venv .venv`, wait until it finishes, and then `.venv/Scripts/activate` (on Windows).
* Install the necessary packages into the environment with `pip install -r requirements.txt`.
* Request the .env file from the developer and place it in the project root.
* Run `jupyter notebook` in a terminal start Jupyter Notebook and use `example.ipynb` to run code.

Python files can then be run in Python using your preferred IDE. See example.ipynb, scratch/scratch.py, etc. Make sure to select the python.exe interpreter at .venv/Scripts/python.exe.

**Docker Setup**

Commands should be run from the project root, possibly by using the terminal after opening the project root in VS Code.

* Install Docker Desktop from https://docs.docker.com/engine/install/. 
* Run Docker Desktop to start the docker daemon running in the background. 
* Request the .env file from the developer and place it in the project root.
* Build the Docker image by running `docker build . -t environment`. This will read the Dockerfile and use it to build an image, which we'll use to initiate a container later on.
* Initiate the container with `docker run --name environment-run -p 8888:8888 environment`.
* Once it is running, there are a few ways you can interact with it:
    - Logs will print to terminal. At the start of every minute, you'll see it run `get_current_readings`. Every ten minutes, it'll run `consolidate_readings`.
    - The terminal will print a Jupyter URL you can access, starting with http://127.0.0.1:8888. Ctrl + click it to open an interactive Jupyter Notebook. You can then open examples.ipynb and execute code. The data may look the same, but it's actually reading from data populated by cron!
    - You can open an interactive session in a new terminal with `docker exec -it environment-run /bin/bash` and watch the logs with `tail data/EnvironmentData.log`, or run any other Linux command in the container. 
    - Open the container in Docker Desktop and navigate to Files > src to view the project files like `sensor_readings.parquet` and `device_readings.parquet`.
* When you are done, remove any running containers by clicking the trash button on Docker Desktop. You can also do it with the CLI: `docker stop $(docker ps -a -q)` and then `docker rm $(docker ps -a -q)`.

## APIs

**Coris** 

The project reads `SensorType` = "Temperature", "Humidity" from the Coris API. New types can be brought in by adding a new entry to the `readings` object in the `EnvironmentData` class. 

* Individual sensors only make one type of reading, so data is stored as one table per sensor type to prevent excessively repetitive or sparse tables. 
* The `cats/user` endpoint is used to get the readings for all sensors, as well as the list of sensor ids. Example: https://cats.corismonitoring.com/api/cats/user/?ApiKey={mykey}&CatsUserID={myid}.
* The `sensor/historical` endpoint is used to get historical readings for a single sensor and type. Example: https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={mykey}&SensorID={sensor_id}&ReadingType={readingtype}&StartUTC={start_utc}&EndUTC={current_utc}&MinReadingSpacing=600&RequestedOutputFormat=raw
    - You can remove StartUTC and/or EndUTC to get the full historical data (this should be confirmed with the API provider though). 

**Importing Other APIs**

Other APIs can be implemented to replace or supplement Coris by expanding `EnvironmentData.initialize_database()` and `EnvironmentData.get_current_readings()` to read sensors from the new API.


## Design Notes

**cron and API key security**

* The typical method of giving keys to containers is through environment variables. cron jobs do not have access to environment variables, so there is no fully secure way to run a cron job that uses key-based authentication. 
* The method I selected is to copy an untracked .env file into the container. This prevents the key from being saved in the GitHub repo. But anyone who gains access to the container itself would have access to the key. The odds of this happening are low, and the risk is also low if the key only allows reading data, not editing it. 
* Security can be enhanced by only allowing the key to be used from white-listed IP addresses. 
* Best practice would be to use a cloud service to schedule jobs instead of cron because cloud jobs can be set up using environment variables. 

**polars vs pandas**

Polars was selected as our data framework because it is faster and more memory efficient, and will therefore help the project scale better.