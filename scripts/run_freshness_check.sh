#!/usr/bin/env bash
# Hourly per-account freshness check + email (pipeline Phase 3).
# Runs scripts/freshness_check.py inside sensorpull-run, always logs, and emails
# when it reports an alert, a recovery, or new tracebacks (exit 1), or fails (2).
set -u
export PATH=/usr/local/bin:/usr/bin:/bin:$PATH

ALERT_TO='anthony.arbaiza@yale.edu'
ALERT_FROM='env-sensor-pipeline@yale.edu'
RELAY='smtp://mail.yale.edu:25'
CHECK='/home/aha48/environmental-sensor-poc/scripts/freshness_check.py'
LOG='/home/aha48/freshness.log'

ts() { date -u '+%Y-%m-%d %H:%M:%S UTC'; }

docker cp "$CHECK" sensorpull-run:/tmp/freshness_check.py >/dev/null 2>&1
out="$(docker exec -w /src ${CHECK_NOW:+-e CHECK_NOW=$CHECK_NOW} sensorpull-run python3 /tmp/freshness_check.py 2>&1)"
rc=$?

{ echo "[$(ts)] exit=$rc"; [ -n "$out" ] && echo "$out"; } >> "$LOG"

if [ "$rc" -ne 0 ]; then
  if echo "$out" | grep -q '^ALERT\|^CRON ERRORS'; then subj='ALERT'
  elif [ "$rc" -eq 1 ]; then subj='recovered'
  else subj='check failed'; fi
  msg="$(mktemp)"
  {
    printf 'From: %s\r\n' "$ALERT_FROM"
    printf 'To: %s\r\n' "$ALERT_TO"
    printf 'Subject: [env-sensor] freshness %s\r\n' "$subj"
    printf '\r\n'
    printf 'The hourly freshness check on %s at %s:\r\n\r\n' "$(hostname)" "$(ts)"
    printf '%s\r\n' "$out"
  } > "$msg"
  curl -s --url "$RELAY" --mail-from "$ALERT_FROM" --mail-rcpt "$ALERT_TO" --upload-file "$msg"
  crc=$?
  rm -f "$msg"
  if [ "$crc" -eq 0 ]; then
    echo "[$(ts)] email sent to $ALERT_TO" >> "$LOG"
  else
    echo "[$(ts)] email FAILED (curl rc=$crc)" >> "$LOG"
  fi
fi
