# Environmental Sensor Data Pipeline
# Compatible with Windows Docker Desktop and Yale Spinup VMs
#
# Build: docker build -t sensorpull .
# Run:   docker run --name sensorpull -p 8888:8888 --env-file .env sensorpull
#
# For Yale Spinup Windows VMs, ensure Docker uses a non-conflicting IP range.
# See: https://yaleits.atlassian.net/wiki/spaces/spinup/pages/470417931

FROM python:3.12-slim

# Set environment variables for Python
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /src

# Install dependencies first for better layer caching
COPY requirements-docker.txt ./
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir -r requirements-docker.txt

# Copy application code
COPY EnvironmentData.py ./
COPY clients/ clients/
COPY modules/ modules/
COPY jobs/ jobs/

# Copy analysis notebook for interactive exploration
COPY experiments/2-examples-analysis.ipynb experiments/

# Create data directory
RUN mkdir -p data

# Expose Jupyter port
EXPOSE 8888

# Health check - verify Python and data directory
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os; assert os.path.isdir('/src/data')" || exit 1

# Entrypoint script handles initialization and scheduling
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

ENTRYPOINT ["/docker-entrypoint.sh"]
