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
                    'Critical_Alert_ID': aid,
                    'Alert_Name': name,
                    'Condition': cond,
                    'Level': idx,
                    'Level_Description': LEVEL_DESC.get(idx),
                    'Bound_Type': bound,
                    'Thresholds_F': c.get('ThresholdF'),
                    'Thresholds_C': c.get('ThresholdC'),
                    'Thresholds_RH_Percent': c.get('ThresholdRh'),
                    'Timeout_Min': c.get('TimeoutMinutes'),
                    'Active': c.get('ConditionEnabled'),
                    'Sensor_Number': active_sensors,
                })

        for sid in sorted(csc_hit - disabled):
            assigns.append({
                'Critical_Alert_ID': aid,
                'Sensor_ID': f'coris:{sid}',
                'Sensor': (id2name.get(sid) or '')[:20],
                'Alert_Name': name,
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


# ---------------------------------------------------------------------------
# Alert tickets (third file, requested by DM 2026-09-11)
# ---------------------------------------------------------------------------
import math
import json

TICKET_STATE_FILE = 'coris_tickets_state.json'


def _epoch(v):
    """Coris returns *UTC fields as int or string; normalize to int epoch or None."""
    if v in (None, '', 0, '0'):
        return None
    try:
        e = int(float(v))
        return e if e > 1e9 else None
    except (TypeError, ValueError):
        return None


def _iso(e):
    return datetime.fromtimestamp(e, timezone.utc).strftime('%Y-%m-%d %H:%M:%S') if e else ''


def build_ticket_table(env_path='/src/.env', since_utc=None, snapshot_utc=None):
    """Return ticket rows for CSC sensors on environmental alerts.

    since_utc: epoch; only tickets created, resolved, or updated after it are
    returned (delta). None returns every ticket Coris still holds (full).
    """
    env = _load_env(env_path)
    acct = env['CATS_USER_ID_PROJECT']
    url = f"{CORIS_BASE}?ApiKey={env['CORIS_API_KEY_PROJECT']}&CatsUserID={acct}"
    r = requests.get(url, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Coris returned HTTP {r.status_code}")
    d = r.json()
    tickets = d.get('Tickets') if isinstance(d, dict) else None
    if not isinstance(tickets, list):
        raise RuntimeError("Coris response missing Tickets list")

    alert_cond = {a.get('CriticalAlertID'): a.get('CriticalAlertConditionType')
                  for a in d.get('CriticalAlerts', [])}
    snap = snapshot_utc or int(datetime.now(timezone.utc).timestamp())
    snap_iso = _iso(snap)

    rows = []
    for t in tickets:
        name = t.get('SensorName') or ''
        if extract_building_code(name) != 'CSC':
            continue
        cond = alert_cond.get(t.get('CriticalAlertID'))
        if cond not in ENV_CONDITIONS:
            continue
        c = _epoch(t.get('CreatedUTC'))
        ls = _epoch(t.get('LevelStartedUTC'))
        res = _epoch(t.get('ResolvedUTC'))
        upd = _epoch(t.get('LastUpdatedUTC'))
        if since_utc is not None:
            latest = max((x for x in (c, res, upd) if x), default=0)
            if latest <= since_utc:
                continue
        dur = math.ceil((res - c) / 60) if (c and res and res >= c) else ''
        rows.append({
            'Ticket_ID': t.get('CriticalAlertTicketID'),
            'Critical_Alert_ID': t.get('CriticalAlertID'),
            'Alert_Description': t.get('CriticalAlertDescription'),
            'Condition': cond,
            'Sensor_ID': f"coris:{t.get('SensorID')}",
            'Coris_Sensor_ID': t.get('SensorID'),
            'Sensor_Name': name[:20],
            'Ticket_State': t.get('TicketState'),
            'Active_Level': t.get('TicketActiveLevel'),
            'Created_UTC': _iso(c),
            'Level_Started_UTC': _iso(ls),
            'Resolved_UTC': _iso(res),
            'Duration_Minutes': dur,
            'Alerts_Enabled': t.get('EnableAlerts'),
            'Coris_Account_ID': acct,
            'Snapshot_UTC': snap_iso,
        })
    rows.sort(key=lambda x: (x['Created_UTC'], x['Ticket_ID'] or 0))
    return rows, snap


def write_ticket_table(data_path, env_path='/src/.env', as_of=None, full=False):
    """Write coris_alert_tickets_YYYY-MM-DD.csv into data_path.

    Delta mode (default): tickets changed since the epoch in
    data_path/coris_tickets_state.json. First run, or full=True, writes every
    ticket. State is advanced only after a successful write. Returns the path
    or None if there was nothing to write.
    """
    state_path = os.path.join(data_path, TICKET_STATE_FILE)
    since = None
    if not full and os.path.exists(state_path):
        with open(state_path) as f:
            since = json.load(f).get('last_snapshot_utc')
    rows, snap = build_ticket_table(env_path, since_utc=since)
    day = (as_of or datetime.now(timezone.utc)).strftime('%Y-%m-%d')
    p = _write_csv(os.path.join(data_path, f"coris_alert_tickets_{day}.csv"), rows)
    with open(state_path, 'w') as f:
        json.dump({'last_snapshot_utc': snap, 'snapshot_iso': _iso(snap),
                   'mode': 'full' if since is None else 'delta',
                   'rows_written': len(rows)}, f, indent=2)
    return p
