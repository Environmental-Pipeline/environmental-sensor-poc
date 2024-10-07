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
* Start the image by running the app via `docker run --name environment-run --env-file .env environment`.
* Once it is running, you can open an interactive session in a new terminal with `docker exec -it environment-run /bin/bash` and watch the logs with `tail data/EnvironmentData.log`
* When you are done, remove any running containers by clicking the trash button on Docker Desktop. You can also do it with the CLI: `docker stop $(docker ps -a -q)` and then `docker rm $(docker ps -a -q)`.

## Design Notes

**cron and API key security**

* The typical method of giving keys to containers is through environment variables. cron jobs do not have access to environment variables, so there is no fully secure way to run a cron job that uses key-based authentication. 
* The method I selected is to copy an untracked .env file into the container. This prevents the key from being saved in the GitHub repo. But anyone who gains access to the container itself would have access to the key. The odds of this happening are low, and the risk is also low if the key only allows reading data, not editing it. 
* Security can be enhanced by only allowing the key to be used from white-listed IP addresses. 
* Best practice would be to use a cloud service to schedule jobs instead of cron because cloud jobs can be set up using environment variables. 

