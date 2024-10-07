# EnvironmentData is not in this folder, add its location to path so we can import it.
import sys
sys.path.append('/src/')

# use EnvironmentData to pull and save current readings. 
from EnvironmentData import EnvironmentData
EnvironmentData(CatsUserID = 2496, testing = True).get_current_status()

