# Clear prior data. 
import os
import shutil
from EnvironmentData import EnvironmentData 

# The project adds to existing data so we need to clear that data to get a solid test from scratch.
if os.path.exists('data'):
    shutil.rmtree('data')
    os.makedirs('data')

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