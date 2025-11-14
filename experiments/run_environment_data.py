# Clear prior data. 
import os
import sys
import shutil

# Add parent directory to Python path to import EnvironmentData
# Get the current working directory and go up one level to the project root
current_dir = os.getcwd()
if 'experiments' in current_dir:
    # If we're in experiments folder, go up one level
    parent_dir = os.path.dirname(current_dir)
else:
    # If we're already in project root, use current directory
    parent_dir = current_dir

sys.path.append(parent_dir)

from EnvironmentData import EnvironmentData 

# The project adds to existing data so we need to clear that data to get a solid test from scratch.
data_path = os.path.join(parent_dir, 'data')
if os.path.exists(data_path):
    shutil.rmtree(data_path)
    os.makedirs(data_path)

# Initialize EnvironmentData. This will run the historical data pull.
envdt = EnvironmentData(
    CatsUserID = 2496, 
    days_back = 365 * 2,
    out_of_scope = ['-80', 'Cryo tank', 'Water'],
    coris_enabled = True,
    licor_enabled = True,
    conserv_enabled = False, # out of scope for this project (just adding LI-COR). 
    testing = True
)