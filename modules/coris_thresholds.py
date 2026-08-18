"""
Extract Coris alert rules and sensor assignments as reference tables for DM.

Thresholds live in the CriticalAlerts key of the /cats/user/ response, which
the pipeline already fetches on every pull and discards. This reads the same
endpoint and reshapes it into two CSVs delivered alongside the daily parquet.

Column names match the W5H "List of Fields" spec. Only environmental
conditions are included; device-health alerts (battery, offline) are excluded
by agreement with the reporting group.

Coris assigns alerts per measurement channel, not per logger, so a single
device has separate SensorIDs for temperature, humidity and light. SensorID is
therefore required in the assignments table: the 20-character SensorName alone
collapses those channels into duplicate rows.
"""
import os
import csv
from datetime import datetime, timezone

import requests

from modules.csc_filter import extract_building_code

CORIS_BASE = "https://cats.corismonitoring.com/api/cats/user/"

# Environmental conditions only. Device health (SensorBatteryLow, SensorMissing,
# Wet, LN2LevelLow, light-state) is deliberately excluded.
ENV_CONDITIONS = {'TooWarm', 'TooCold', 'HighHumidity', 'LowHumidity',
                  'LuxTooHigh', 'LuxTooLow'}
UPPER = {'TooWarm', 'HighHumidity', 'LuxTooHigh'}
LOWER = {'TooCold', 'LowHumidity', 'LuxTooLow'}
LEVEL_DESC = {1: 'Possible', 2: 'Definite', 3: 'Urgent'}


def _load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    return env


def _write_csv(path, rows):
    if not rows:
        return None
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return path


def build_threshold_tables(env_path='/src/.env'):
    """Fetch Coris config and return (rules, assignments) as lists of dicts."""
    env = _load_env(env_path)
    url = (f"{CORIS_BASE}?ApiKey={env['CORIS_API_KEY_PROJECT']}"
           f"&CatsUserID={env['CATS_USER_ID_PROJECT']}")
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Coris returned HTTP {r.status_code}")
    d = r.json()
    if not isinstance(d, dict) or 'CriticalAlerts' not in d:
        raise RuntimeError("Coris response missing CriticalAlerts")

    id2name = {s['SensorID']: s.get('SensorName') for s in d.get('Sensors', [])}
    csc_ids = {i for i, n in id2name.items()
               if n and extract_building_code(n) == 'CSC'}

    rules, assigns = [], []
    for a in d.get('CriticalAlerts', []):
        cond = a.get('CriticalAlertConditionType')
        if cond not in ENV_CONDITIONS:
            continue
        covered = set(a.get('CoveredSensorIDs') or [])
        disabled = set(a.get('DisabledSensorIDs') or [])
        csc_hit = (covered | disabled) & csc_ids
        if not csc_hit:
            continue

        aid = a.get('CriticalAlertID')
        name = a.get('CriticalAlertDescription')
        bound = 'Upper' if cond in UPPER else ('Lower' if cond in LOWER else None)
        active_sensors = len(csc_hit - disabled)

        for idx, level in enumerate(a.get('CriticalAlertLevels') or [], start=1):
            for c in (level.get('CriticalAlertLevelConditions') or [{}]):
                rules.append({
                    'CriticalAlertID': aid,
                    'Alert Name': name,
                    'Condition': cond,
                    'Level': idx,
                    'Level Description': LEVEL_DESC.get(idx),
                    'BoundType': bound,
                    'Thresholds (F)': c.get('ThresholdF'),
                    'Thresholds (C)': c.get('ThresholdC'),
                    'Thresholds (RH%)': c.get('ThresholdRh'),
                    'Timeout (min)': c.get('TimeoutMinutes'),
                    'Active': c.get('ConditionEnabled'),
                    '# Sensor': active_sensors,
                })

        for sid in sorted(csc_hit - disabled):
            assigns.append({
                'CriticalAlertID': aid,
                'SensorID': f'coris:{sid}',
                'Sensor': (id2name.get(sid) or '')[:20],
                'Alert Name': name,
            })

    return rules, assigns


def write_threshold_tables(data_path, env_path='/src/.env', as_of=None):
    """Write both CSVs into data_path with a UTC date stamp.

    Returns a list of written paths. Raises on API failure; the caller decides
    whether that should stop the export.
    """
    rules, assigns = build_threshold_tables(env_path)
    day = (as_of or datetime.now(timezone.utc)).strftime('%Y-%m-%d')
    written = []
    for name, rows in (('coris_alert_rules', rules),
                       ('coris_alert_sensor_assignments', assigns)):
        p = _write_csv(os.path.join(data_path, f"{name}_{day}.csv"), rows)
        if p:
            written.append(p)
    return written
