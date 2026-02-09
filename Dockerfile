FROM python:3
RUN apt-get update && apt-get install -y cron

WORKDIR /src

COPY requirements-docker.txt ./
COPY requirements.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements-docker.txt

# Copy necessary files. We leave out 1-examples-pull-data.ipynb since it could interfere with cron jobs.
# To use 2-examples-analysis.ipynb, wait until the first consolidation (after 10 minutes) has run to ensure data files exist.
COPY EnvironmentData.py .env ./
COPY experiments/2-examples-analysis.ipynb experiments/
COPY clients clients/
COPY modules modules/
COPY data data/
COPY templates templates/
COPY jobs jobs/

COPY jobs/cronjobs /etc/cron.d/cronjobs
RUN chmod 0644 /etc/cron.d/cronjobs
RUN crontab /etc/cron.d/cronjobs

# Expose Jupyter port
EXPOSE 8888

# Run init script, start cron, monitor the log file.
CMD jupyter notebook --ip 0.0.0.0 --port 8888 --no-browser --allow-root & \
    python3 jobs/0-init.py && \
    cron && \
    crontab -l && \    
    tail -f data/EnvironmentData.log