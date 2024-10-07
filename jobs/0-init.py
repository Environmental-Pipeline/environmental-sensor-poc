# EnvironmentData is not in this folder, add its location to path so we can import it.
import sys
sys.path.append('/src/')

# use EnvironmentData to initialize the database.
from EnvironmentData import EnvironmentData
api = EnvironmentData(CatsUserID = 2496, testing = True) # this will initialize the database.
