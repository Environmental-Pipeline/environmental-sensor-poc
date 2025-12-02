#!/bin/bash
# Docker entrypoint for Environmental Sensor Pipeline
# Handles initialization, Jupyter, and scheduled data pulls

set -e

echo "========================================"
echo "Environmental Sensor Pipeline Starting"
echo "========================================"

# Create .env file from environment variables if it doesn't exist
if [ ! -f /src/.env ]; then
    echo "Creating .env file from environment variables..."
    cat > /src/.env << EOF
DAYS_BACK=${DAYS_BACK:-730}
TESTING=${TESTING:-False}
CORIS_ENABLED=${CORIS_ENABLED:-False}
CONSERV_ENABLED=${CONSERV_ENABLED:-False}
LICOR_ENABLED=${LICOR_ENABLED:-False}
CATS_USER_ID=${CATS_USER_ID:-}
CORIS_API_KEY=${CORIS_API_KEY:-}
CONSERV_API_KEYS=${CONSERV_API_KEYS:-}
LICOR_API_KEY=${LICOR_API_KEY:-}
EOF
    echo ".env file created"
fi

# Ensure data directory exists
mkdir -p /src/data

# Run initialization (historical data pull if data folder is empty)
echo "Running initialization..."
python /src/jobs/0-init.py

echo ""
echo "========================================"
echo "Starting services..."
echo "========================================"

# Start Jupyter in background
echo "Starting Jupyter Notebook on port 8888..."
jupyter notebook --ip=0.0.0.0 --port=8888 --no-browser --allow-root \
    --NotebookApp.token='' --NotebookApp.password='' \
    --notebook-dir=/src &

# Give Jupyter a moment to start
sleep 3

echo ""
echo "========================================"
echo "Services Running:"
echo "  - Jupyter: http://localhost:8888"
echo "  - Scheduler: Pull every 15 min, Consolidate at :45"
echo "========================================"
echo ""

# Run the Python scheduler (replaces cron)
# This will handle pulls every 15 minutes and consolidation at :45
exec python /src/jobs/scheduler.py
