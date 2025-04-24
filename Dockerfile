FROM python:3.12
RUN apt-get update && apt-get install -y cron

WORKDIR /src

COPY requirements.txt  ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY EnvironmentData.py weather_api.py .env examples-cron.ipynb examples-analysis.ipynb ./
COPY jobs jobs/

COPY jobs/cronjobs /etc/cron.d/cronjobs
RUN chmod 0644 /etc/cron.d/cronjobs
RUN crontab /etc/cron.d/cronjobs

# Expose Jupyter port
EXPOSE 8888

# Start init script first, then Jupyter in background, then cron in foreground
CMD python3 jobs/0-init.py && \
    jupyter notebook --ip 0.0.0.0 --port 8888 --no-browser --allow-root & \
    cron -f