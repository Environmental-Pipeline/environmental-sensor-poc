"""
Hourly freshness check (pipeline Phase 3). Runs inside sensorpull-run via
scripts/run_freshness_check.sh on the host.

Alerts once per incident, and again on recovery, when:
  - a Coris account or Conserv customer has had no new reading for 2 hours, or
    has been failing for 2 hours without ever having produced readings;
  - no pull has updated source_status.json for 2 hours (pulls not running);
  - new tracebacks appeared in cron-errors.log since the last check.
Prints one line per event. Exit 1 when there is something to email, 0 when
not, 2 if the check itself failed.
"""
import json
import os
import re
import sys
import time

D = os.environ.get("DATA_DIR", "/src/data")
STALE_S = 2 * 3600
STATUS = os.path.join(D, "source_status.json")
STATE = os.path.join(D, "freshness_alert_state.json")
CRON_LOG = os.path.join(D, "cron-errors.log")
CRON_STATUS = os.path.join(D, "cron_errors_status.json")
LABELS = {"coris:3219": "Coris 3219 (Project)", "coris:2496": "Coris 2496 (Peabody)",
          "coris:3088": "Coris 3088 (Libraries)", "conserv:307": "Conserv 307", "conserv:333": "Conserv 333"}


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def save(path, obj):
    with open(path + ".tmp", "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    os.replace(path + ".tmp", path)


def when(ts):
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


def dur(sec):
    sec = int(sec)
    return f"{sec // 3600}h {sec % 3600 // 60:02d}m"


def main(now):
    state = load(STATE, {})
    open_ = state.setdefault("open", {})
    events = []

    problems = {}
    status = load(STATUS, None)
    if status is None:
        problems["pipeline"] = "Pipeline: source_status.json is missing; pulls may not be running"
    else:
        age = now - int(status.get("updated_utc", 0))
        if age > STALE_S:
            problems["pipeline"] = f"Pipeline: no pull has reported for {dur(age)} (last {when(status['updated_utc'])})"
        for key, s in status.get("sources", {}).items():
            name = LABELS.get(key, key)
            newest, err = s.get("newest_reading_utc"), s.get("current_error")
            if newest and now - newest > STALE_S:
                problems[key] = (f"{name}: no new reading for {dur(now - newest)} (last reading {when(newest)})"
                                 + (f". Current error: {err}" if err else ""))
            elif not newest and err and now - int(s.get("error_since_utc") or now) > STALE_S:
                problems[key] = f"{name}: failing for {dur(now - s['error_since_utc'])}: {err}"

    for key, msg in sorted(problems.items()):
        if key not in open_:
            events.append(f"ALERT {msg}")
            open_[key] = {"since": now, "msg": msg}
    for key in sorted(list(open_)):
        if key not in problems:
            events.append(f"RECOVERED {open_[key]['msg'].split(':')[0]} (alerted {when(open_[key]['since'])})")
            del open_[key]

    # cron-errors.log: count tracebacks appended since the last check.
    cron = load(CRON_STATUS, {})
    size = os.path.getsize(CRON_LOG) if os.path.exists(CRON_LOG) else 0
    if "cron_offset" not in state:
        state["cron_offset"] = size          # first run: start from now, not from history
    off = state["cron_offset"] if state["cron_offset"] <= size else 0
    new_tb, last_err = 0, None
    if size > off:
        with open(CRON_LOG, "rb") as f:
            f.seek(off)
            chunk = f.read().decode("utf-8", "replace").replace("\r", "\n")
        new_tb = chunk.count("Traceback (most recent call last)")
        errs = [ln.strip() for ln in chunk.split("\n") if re.match(r"^[A-Za-z_.]*(Error|Exception)\b", ln.strip())]
        last_err = errs[-1][:300] if errs else None
    state["cron_offset"] = size
    cron.update({"checked_utc": now, "new_tracebacks": new_tb})
    if new_tb:
        cron["last_traceback_utc"] = now
        cron["last_error"] = last_err
        events.append(f"CRON ERRORS {new_tb} new traceback(s) in cron-errors.log since the last check. Last: {last_err}")

    save(STATE, state)
    save(CRON_STATUS, cron)
    for e in events:
        print(e)
    still = [v["msg"] for k, v in sorted(open_.items())]
    if events and still:
        print("")
        print("Still open: " + " | ".join(still))
    return 1 if events else 0


if __name__ == "__main__":
    try:
        sys.exit(main(int(os.environ.get("CHECK_NOW") or time.time())))
    except SystemExit:
        raise
    except Exception as exc:
        print(f"freshness check failed: {exc!r}")
        sys.exit(2)
