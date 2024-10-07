FROM python:3
RUN apt-get update && apt-get install -y cron

WORKDIR /src

COPY requirements.txt  ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY EnvironmentData.py .env ./
COPY jobs jobs/

COPY jobs/cronjobs /etc/cron.d/cronjobs
RUN chmod 0644 /etc/cron.d/cronjobs
RUN crontab /etc/cron.d/cronjobs

# run init script, start cron, monitor the log file.
CMD python3 jobs/0-init.py && \
    cron && \
    crontab -l && \
    tail -f data/EnvironmentData.log