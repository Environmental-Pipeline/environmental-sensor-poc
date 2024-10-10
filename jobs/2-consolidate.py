# EnvironmentData is not in this folder, add its location to path so we can import it.
import sys
sys.path.append('/src/')

# use EnvironmentData to consolidate new and historical readings into one database.
from EnvironmentData import EnvironmentData
EnvironmentData(CatsUserID = 2496, out_of_scope = ['-80', 'Cryo tank', 'Water'], testing = True).consolidate_readings()