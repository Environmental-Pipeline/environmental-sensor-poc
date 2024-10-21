FROM python:3
RUN apt-get update && apt-get install -y cron

WORKDIR /src

COPY requirements.txt  ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY EnvironmentData.py .env examples-cron.ipynb examples-analysis.ipynb ./
COPY jobs jobs/

COPY jobs/cronjobs /etc/cron.d/cronjobs
RUN chmod 0644 /etc/cron.d/cronjobs
RUN crontab /etc/cron.d/cronjobs

# Expose Jupyter port
EXPOSE 8888

# run init script, start cron, monitor the log file.
CMD jupyter notebook --ip 0.0.0.0 --port 8888 --no-browser --allow-root & \
    python3 jobs/0-init.py && \
    cron && \
    crontab -l && \    
    tail -f data/EnvironmentData.log