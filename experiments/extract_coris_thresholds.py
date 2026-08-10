import sys, os, json, csv, time
sys.path.insert(0, '/src'); os.chdir('/src')
import requests
from datetime import datetime, timezone
from modules.csc_filter import extract_building_code

OUT = '/src/data/threshold_scratch'
os.makedirs(OUT, exist_ok=True)

def load_env(p='/src/.env'):
    e = {}
    for l in open(p):
        l = l.strip()
        if '=' in l and not l.startswith('#'):
            k, v = l.split('=', 1); e[k.strip()] = v.strip()
    return e

env = load_env()
url = (f"https://cats.corismonitoring.com/api/cats/user/"
       f"?ApiKey={env['CORIS_API_KEY_PROJECT']}&CatsUserID={env['CATS_USER_ID_PROJECT']}")
r = requests.get(url, timeout=60)
if r.status_code != 200:
    raise SystemExit(f"HTTP {r.status_code} — aborting, no files written")
d = r.json()
if not isinstance(d, dict) or 'CriticalAlerts' not in d:
    raise SystemExit(f"unexpected shape, keys={list(d)[:5]} — aborting")

snapshot = datetime.now(timezone.utc)
snap_iso = snapshot.strftime('%Y-%m-%dT%H:%M:%SZ')

sensors = d.get('Sensors', [])
alerts  = d.get('CriticalAlerts', [])
id2name = {s['SensorID']: s.get('SensorName') for s in sensors}
csc_ids = {i for i, n in id2name.items() if n and extract_building_code(n) == 'CSC'}

# --- field-name diagnostics: confirm assumptions instead of producing silent nulls
print("=== structure check ===")
a0 = alerts[0]
print("alert keys:", sorted(a0.keys()))
lv = (a0.get('CriticalAlertLevels') or [])
if lv:
    print("level keys:", sorted(lv[0].keys()))
    cond = (lv[0].get('CriticalAlertLevelConditions') or [])
    if cond:
        print("condition keys:", sorted(cond[0].keys()))
    else:
        print("!! no CriticalAlertLevelConditions on first level")
else:
    print("!! no CriticalAlertLevels on first alert")
print()

rules, assigns = [], []
skipped_no_csc = 0

for a in alerts:
    covered  = set(a.get('CoveredSensorIDs') or [])
    disabled = set(a.get('DisabledSensorIDs') or [])
    csc_hit  = (covered | disabled) & csc_ids
    if not csc_hit:
        skipped_no_csc += 1
        continue

    aid = a.get('CriticalAlertID')
    for idx, level in enumerate(a.get('CriticalAlertLevels') or [], start=1):
        conds = level.get('CriticalAlertLevelConditions') or []
        if not conds:
            rules.append({
                'CriticalAlertID': aid,
                'CriticalAlertDescription': a.get('CriticalAlertDescription'),
                'CriticalAlertConditionType': a.get('CriticalAlertConditionType'),
                'CatsGroupID': a.get('CatsGroupID'),
                'CatsGroupName': a.get('CatsGroupName'),
                'TempPref': a.get('CriticalAlertTempPref'),
                'LevelNumber': idx,
                'RepeatIntervalMinutes': level.get('LevelRepeatNotificationIntervalMinutes'),
                'ConditionType': None, 'ConditionEnabled': None, 'TimeoutMinutes': None,
                'ThresholdF': None, 'ThresholdC': None, 'ThresholdRh': None,
                'SnapshotUTC': snap_iso,
            })
            continue
        for c in conds:
            rules.append({
                'CriticalAlertID': aid,
                'CriticalAlertDescription': a.get('CriticalAlertDescription'),
                'CriticalAlertConditionType': a.get('CriticalAlertConditionType'),
                'CatsGroupID': a.get('CatsGroupID'),
                'CatsGroupName': a.get('CatsGroupName'),
                'TempPref': a.get('CriticalAlertTempPref'),
                'LevelNumber': idx,
                'RepeatIntervalMinutes': level.get('LevelRepeatNotificationIntervalMinutes'),
                'ConditionType': c.get('ConditionType'),
                'ConditionEnabled': c.get('ConditionEnabled'),
                'TimeoutMinutes': c.get('TimeoutMinutes'),
                'ThresholdF': c.get('ThresholdF'),
                'ThresholdC': c.get('ThresholdC'),
                'ThresholdRh': c.get('ThresholdRh'), 'ThresholdLux': c.get('ThresholdLux'), 'ThresholdBatteryPercentage': c.get('ThresholdBatteryPercentage'), 'ThresholdOther': (';'.join(f'{k}={c[k]}' for k in sorted(c) if k.startswith('Threshold') and c[k] is not None and k not in ('ThresholdF','ThresholdC','ThresholdRh','ThresholdLux','ThresholdBatteryPercentage')) or None),
                'SnapshotUTC': snap_iso,
            })

    for sid in sorted(csc_hit):
        assigns.append({
            'CriticalAlertID': aid,
            'SensorID': f'coris:{sid}',
            'SensorName': (id2name.get(sid) or '')[:20], 'CorisSensorName': id2name.get(sid),
            'IsDisabled': sid in disabled,
            'SnapshotUTC': snap_iso,
        })

def write(path, rows):
    if not rows:
        print(f"!! no rows for {path}, not written"); return
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    print(f"wrote {path}  ({len(rows)} rows)")

write(f'{OUT}/coris_alert_rules.csv', rules)
write(f'{OUT}/coris_alert_sensor_assignments.csv', assigns)

print(f"\nalerts total {len(alerts)}, CSC-touching {len(alerts)-skipped_no_csc}, skipped {skipped_no_csc}")
print(f"distinct CSC sensors assigned: {len({r['SensorID'] for r in assigns})}")
print(f"of which disabled: {len({r['SensorID'] for r in assigns if r['IsDisabled']})}")
print(f"threshold values present: F={sum(1 for r in rules if r['ThresholdF'] is not None)}"
      f"  C={sum(1 for r in rules if r['ThresholdC'] is not None)}"
      f"  Rh={sum(1 for r in rules if r['ThresholdRh'] is not None)}")
