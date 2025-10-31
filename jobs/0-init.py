# ruff: noqa: E402
# EnvironmentData is not in this folder, add its location to path so we can import it.
import sys
sys.path.append('/src/')

def read_env_variable(var_name):
    with open('.env') as f:
        for line in f:
            if line.startswith(var_name):
                return line.split('=', 1)[1].strip()

# use EnvironmentData to initialize the database.
from EnvironmentData import EnvironmentData
api = EnvironmentData(
    CatsUserID = 2496, 
    out_of_scope = ['-80', 'Cryo tank', 'Water'], 
    days_back = int(read_env_variable('DAYS_BACK')),
    testing = read_env_variable('TESTING') == 'True'
)
