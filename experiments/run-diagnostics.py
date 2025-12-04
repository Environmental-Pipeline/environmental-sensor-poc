# Run this script to regenerate diagnostic files
# Make sure to activate the virtual environment first: .\.venv\Scripts\Activate.ps1
# Usage: python experiments/run-diagnostics.py

import polars
import os
import sys

# Add parent directory to path so we can import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import validation
from EnvironmentData import EnvironmentData

# Use absolute path relative to script location
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
data_path = os.path.join(project_root, "data")

# Load sensor readings
sensors = polars.read_parquet(f"{data_path}/sensor_readings.parquet")

# Define acceptable ranges (empty = no alerts)
acceptable_range = {"SensorReadingF": [], "SensorReadingRh": []}

# Create a minimal EnvironmentData instance to run validation
env = EnvironmentData(
    data_path=data_path,
    days_back=1,
    coris_enabled=True,
    licor_enabled=True,
    conserv_enabled=True,
    testing=True,
    home_directory=project_root
)

# Run full validation to get results (including sensor name consistency check)
validation_results = env.validate_sensors(
    sensors=sensors,
    step="manual_diagnostics",
    return_results=True
)
env.close()

# Generate diagnostics report with validation results
validation.generate_validation_results(
    sensors=sensors,
    validation_results=validation_results,
    acceptable_range=acceptable_range,
    data_path=data_path,
    logger=None,
    step="manual_run"
)

print("Done! Check data/validation-results.csv and data/validation-detail.csv")