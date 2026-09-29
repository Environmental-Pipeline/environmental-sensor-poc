# ruff: noqa: E402
# Hourly: re-pull Coris readings for any account that has fallen more than 2 hours
# behind (Phase 2 of the hardening plan). A lock keeps runs from overlapping; a
# long gap on account 3219 takes about 18 minutes per day of data.
import sys
import os
import fcntl
sys.path.append('/src/')  # Docker
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def read_env_variable(var_name):
    with open('.env') as f:
        for line in f:
            if line.startswith(var_name):
                return line.split('=', 1)[1].strip()


data_path = "/src/data" if os.path.exists("/src/data") else os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
lock = open(os.path.join(data_path, ".coris_backfill.lock"), "w")
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    print("[4-backfill-coris] previous run still in progress; exiting.")
    sys.exit(0)

from EnvironmentData import EnvironmentData
_env = EnvironmentData(
    days_back=int(read_env_variable('DAYS_BACK')),
    testing=read_env_variable('TESTING').lower() == 'true',
    coris_enabled=read_env_variable('CORIS_ENABLED').lower() == 'true',
    conserv_enabled=read_env_variable('CONSERV_ENABLED').lower() == 'true',
    licor_enabled=read_env_variable('LICOR_ENABLED').lower() == 'true',
)
_env.backfill_coris_gaps()
