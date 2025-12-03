# Usage: python experiments/run_environment_data.py

# Clear prior data. 
import os, sys, shutil

# Add parent directory to Python path to import EnvironmentData.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Get the EnvironmentData class.
from EnvironmentData import EnvironmentData 

# The project adds to existing data so we need to clear that data to get a solid test from scratch.
if os.path.exists('./data'):
    shutil.rmtree('./data')
    os.makedirs('./data')

# Initialize EnvironmentData. This will run the historical data pull.
envdt = EnvironmentData(
    #days_back = 365 * 2,
    days_back = 7,
    coris_enabled = True,
    licor_enabled = True,
    conserv_enabled = True, 
    testing = True,
    #testing = False,
    # Since we are running from the experiments/ folder, we need to tell the class to use the parent directory as home.
    home_directory = "."
)

# To consolidate these into the database, run consolidate_readings.
envdt.consolidate_readings()

# When done, close the connection to the logs. 
envdt.close()
del envdt