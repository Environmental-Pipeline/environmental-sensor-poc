```python
python -m venv .venv
venv/Scripts/activate
```

https://cats.corismonitoring.com/api/cats/user/?ApiKey=XXXX&CatsUserID=2496

https://cats.corismonitoring.com/api/sensor/historical/?ApiKey=XXXX&SensorID=21381&ReadingType=SensorReadingF&StartUTC=1718424000&EndUTC=1718596800&MinReadingSpacing=43200&RequestedOutputFormat=raw

## Docker Commands

* Install Docker Desktop from https://docs.docker.com/engine/install/. 
* Run Docker Desktop to start the utility running in the background. 
* Open this project in VS Code. Use the VS Code terminal to run these commands.
* Build the Docker image by running `docker build . -t environment`. This will read the file Dockerfile and use it to build an image, which we'll use to initiate a container later on.
* Start the image by running the app via `docker run -d --name environment-run --env-file .env environment`.
* Once it is running, you can open an interactive session in a new terminal with `docker exec -it environment-run /bin/bash` and watch the logs with `tail data/EnvironmentData.log`
* When you are done, remove any running containers by clicking the trash button on Docker Desktop. You can also do it with the CLI: `docker stop $(docker ps -a -q)` and then `docker rm $(docker ps -a -q)`.
