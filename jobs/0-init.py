# EnvironmentData is not in this folder, add its location to path so we can import it.
import sys
sys.path.append('/src/')

# use EnvironmentData to initialize the database.
from EnvironmentData import EnvironmentData
api = EnvironmentData(EnvironmentData(
    CatsUserID = 2496, 
    days_back = int(365 * 2),
    out_of_scope = ['-80', 'Cryo tank', 'Water'], 
    testing = False
))
