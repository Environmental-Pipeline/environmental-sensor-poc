#!/usr/bin/env bash
# Daily completeness check + email alert on failure.
# Runs scripts/completeness_check.py inside sensorpull-run, always logs,
# and emails on any non-zero exit (a flagged gap, or the check failing to run).
# Pass a YYYY-MM-DD arg to test against a historical day (e.g. 2026-03-12).
set -u
export PATH=/usr/local/bin:/usr/bin:/bin:$PATH

ALERT_TO='anthony.arbaiza@yale.edu'
ALERT_FROM='env-sensor-pipeline@yale.edu'
RELAY='smtp://mail.yale.edu:25'
CHECK='/home/aha48/environmental-sensor-poc/scripts/completeness_check.py'
LOG='/home/aha48/completeness.log'

ts() { date -u '+%Y-%m-%d %H:%M:%S UTC'; }

docker cp "$CHECK" sensorpull-run:/tmp/cc.py >/dev/null 2>&1
out="$(docker exec -w /src sensorpull-run python3 /tmp/cc.py "$@" 2>&1)"
rc=$?

{ echo "[$(ts)] exit=$rc"; echo "$out"; echo; } >> "$LOG"

if [ "$rc" -ne 0 ]; then
  msg="$(mktemp)"
  {
    printf 'From: %s\r\n' "$ALERT_FROM"
    printf 'To: %s\r\n' "$ALERT_TO"
    printf 'Subject: [env-sensor] completeness check ALERT\r\n'
    printf '\r\n'
    printf 'The daily completeness check flagged a problem at %s on %s.\r\n\r\n' "$(ts)" "$(hostname)"
    printf '%s\r\n' "$out"
  } > "$msg"
  curl -s --url "$RELAY" --mail-from "$ALERT_FROM" --mail-rcpt "$ALERT_TO" --upload-file "$msg"
  crc=$?
  rm -f "$msg"
  if [ "$crc" -eq 0 ]; then
    echo "[$(ts)] alert email sent to $ALERT_TO" >> "$LOG"
  else
    echo "[$(ts)] alert email FAILED (curl rc=$crc)" >> "$LOG"
  fi
fi
