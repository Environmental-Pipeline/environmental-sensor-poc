FROM python:3
RUN apt-get update && apt-get install -y cron

WORKDIR /src

COPY requirements.txt  ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

COPY EnvironmentData.py coris_client.py conserv_client.py ingest_all_sources.py .env examples-cron.ipynb examples-analysis.ipynb ./
COPY jobs jobs/

COPY jobs/cronjobs /etc/cron.d/cronjobs
RUN chmod 0644 /etc/cron.d/cronjobs
RUN crontab /etc/cron.d/cronjobs

# Expose Jupyter port
EXPOSE 8888

# Create unified entry point that can run in different modes
# Default: run unified ingestion script once (for cron)
# Alternative: set JUPYTER_MODE=true for development/monitoring mode
CMD if [ "$JUPYTER_MODE" = "true" ]; then \
        jupyter notebook --ip 0.0.0.0 --port 8888 --no-browser --allow-root & \
        python3 jobs/0-init.py && \
        cron && \
        crontab -l && \
        tail -f data/EnvironmentData.log; \
    else \
        python3 ingest_all_sources.py; \
    fi