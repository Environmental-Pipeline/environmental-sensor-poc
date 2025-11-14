# ruff: noqa: E402
# EnvironmentData is not in this folder, add its location to path so we can import it.
import sys
sys.path.append('/src/')

def read_env_variable(var_name):
    with open('.env') as f:
        for line in f:
            if line.startswith(var_name):
                return line.split('=', 1)[1].strip()

# use EnvironmentData to consolidate new and historical readings into one database.
from EnvironmentData import EnvironmentData
EnvironmentData(
    CatsUserID = 2496, 
    out_of_scope = ['-80', 'Cryo tank', 'Water'], 
    days_back = int(read_env_variable('DAYS_BACK')),
    testing = read_env_variable('TESTING').lower() == 'true',
    coris_enabled = read_env_variable('CORIS_ENABLED').lower() == 'true',
    conserv_enabled = read_env_variable('CONSERV_ENABLED').lower() == 'true',
    licor_enabled = read_env_variable('LICOR_ENABLED').lower() == 'true',
).consolidate_readings()