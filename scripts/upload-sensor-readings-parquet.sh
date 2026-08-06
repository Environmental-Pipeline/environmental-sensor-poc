#!/usr/bin/env bash
set -euo pipefail

# === CONFIG ===
DATA_DIR="/opt/env-sensor-data"
DAILY_SRC="${DATA_DIR}/daily_export.parquet"
DATE_UTC="$(date -u +%Y-%m-%d)"
LOG="/home/aha48/sensor_readings_upload.log"

# S3 destination
AWS_PROFILE="env-sensors"
S3_BUCKET="s3://spinup-002f52-yale-env-sensor-poc-data"

# Fabric destination (service principal auth)
FABRIC_APP_ID="c8060d79-3927-46b2-943e-d13020cfcefe"
FABRIC_TENANT_ID="dd8cbebb-2139-4df8-b411-4e3e87abeb5c"
FABRIC_CERT="/home/aha48/fabric-service-principal-combined.pem"

# Fabric landing areas. DEV listed first so that if TEST has issues,
# the DEV flow (which Power BI depends on) is already done before we
# touch TEST. Add or remove entries here to change destinations.
FABRIC_BASES=(
  "https://edafileuploads.dfs.core.windows.net/cultural-heritage-environmental-monitoring-dev/Incoming"
  "https://edafileuploads.dfs.core.windows.net/cultural-heritage-environmental-monitoring-tst/Incoming"
)

# Add the PROD landing area on/after the go-live delivery date (first PROD drop is
# 2026-06-16, carrying the 15th's data). DEV/TST stay enabled as a safety net.
# Touch ${DATA_DIR}/PROD_UPLOAD_OFF to disable the PROD upload without editing this file.
PROD_BASE="https://edafileuploads.dfs.core.windows.net/cultural-heritage-environmental-monitoring-prd/Incoming"
PROD_START_YMD=20260616
PROD_KILL_FILE="${DATA_DIR}/PROD_UPLOAD_OFF"
if [[ ! -f "$PROD_KILL_FILE" ]] && (( $(date -u +%Y%m%d) >= PROD_START_YMD )); then
  FABRIC_BASES+=( "$PROD_BASE" )
fi

# ---------------------------------------------------------------------------
# Helper: upload a file to S3 and to every configured Fabric landing area
# ---------------------------------------------------------------------------
upload_file() {
  local src_file="$1"
  local s3_dest="$2"
  local dest_filename="$3"
  local label="$4"

  if [ ! -f "$src_file" ]; then
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] ERROR: ${label} file not found: ${src_file}" >> "$LOG"
    return 1
  fi

  # Upload to S3
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Uploading ${label} to S3: ${s3_dest}" >> "$LOG"
  if aws s3 cp "$src_file" "$s3_dest" --profile "$AWS_PROFILE" >> "$LOG" 2>&1; then
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] S3 ${label} upload complete." >> "$LOG"
    RESULT_S3="ok"
  else
    RESULT_S3="failed"
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] ERROR: S3 ${label} upload failed." >> "$LOG"
    # Don't return; still try Fabric. S3 failure shouldn't block Fabric uploads.
  fi

  # Upload to every Fabric landing area. Track per-destination success.
  local fabric_failures=0
  local fabric_attempts=0
  for base in "${FABRIC_BASES[@]}"; do
    fabric_attempts=$((fabric_attempts + 1))
    local fabric_dest="${base}/${dest_filename}"
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Uploading ${label} to Fabric: ${fabric_dest}" >> "$LOG"
    local area
    area="$(echo "$base" | sed -E 's#.*monitoring-([a-z]+)/Incoming#\1#')"
    if azcopy copy "$src_file" "$fabric_dest" >> "$LOG" 2>&1; then
      echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Fabric ${label} upload complete: ${fabric_dest}" >> "$LOG"
      RESULT_FABRIC="${RESULT_FABRIC}${RESULT_FABRIC:+,}\"${area}\":\"ok\""
    else
      RESULT_FABRIC="${RESULT_FABRIC}${RESULT_FABRIC:+,}\"${area}\":\"failed\""
      echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] ERROR: Fabric ${label} upload failed: ${fabric_dest}" >> "$LOG"
      fabric_failures=$((fabric_failures + 1))
    fi
  done

  # Exit nonzero only if every Fabric destination failed.
  if [ "$fabric_failures" -gt 0 ] && [ "$fabric_failures" -eq "$fabric_attempts" ]; then
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] ERROR: All ${fabric_attempts} Fabric destinations failed for ${label}." >> "$LOG"
    return 1
  elif [ "$fabric_failures" -gt 0 ]; then
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] WARNING: ${fabric_failures} of ${fabric_attempts} Fabric destinations failed for ${label}." >> "$LOG"
  fi
}

# === MAIN ===
RESULT_S3="not_attempted"
RESULT_FABRIC=""
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Starting upload..." >> "$LOG"
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Fabric destinations: ${#FABRIC_BASES[@]}" >> "$LOG"

# Authenticate to Fabric via service principal (once, up front)
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Authenticating to Fabric..." >> "$LOG"
azcopy login --service-principal --application-id "$FABRIC_APP_ID" --tenant-id "$FABRIC_TENANT_ID" --certificate-path "$FABRIC_CERT" >> "$LOG" 2>&1

# --- Daily incremental upload (delta only) ---
delivery_ok=0
if upload_file \
  "$DAILY_SRC" \
  "${S3_BUCKET}/daily/sensor_readings_${DATE_UTC}.parquet" \
  "sensor_readings_${DATE_UTC}.parquet" \
  "daily-incremental"; then
  delivery_ok=1
fi

echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] All uploads complete." >> "$LOG"

# --- Delivery status for the dashboard ---------------------------------------
# Written on every run, success or failure. The dashboard mounts DATA_DIR
# read-only and cannot see this log, so this file is the only way delivery
# state reaches it. Absence of this file means the upload never ran.
STATUS_FILE="${DATA_DIR}/delivery_status.json"
_bytes=0
[ -f "$DAILY_SRC" ] && _bytes=$(stat -c%s "$DAILY_SRC")
cat > "${STATUS_FILE}.tmp" << JSONEOF
{
  "run_utc": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "delivery_ok": ${delivery_ok},
  "filename": "sensor_readings_${DATE_UTC}.parquet",
  "source_file": "${DAILY_SRC}",
  "bytes": ${_bytes},
  "s3": "${RESULT_S3}",
  "fabric": {${RESULT_FABRIC}}
}
JSONEOF
mv "${STATUS_FILE}.tmp" "$STATUS_FILE"
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Wrote ${STATUS_FILE}" >> "$LOG"

# --- Off-box heartbeat -------------------------------------------------------
# Ping only when the delivery actually succeeded. Silence is the alert: if this
# ping stops arriving, healthchecks.io notifies from outside Yale, which still
# works when the VM is unreachable. See /opt/env-sensor-data/.healthcheck
HC_CONF="${DATA_DIR}/.healthcheck"
if [ "$delivery_ok" -eq 1 ] && [ -r "$HC_CONF" ]; then
  # shellcheck disable=SC1090
  . "$HC_CONF"
  if [ -n "${HEALTHCHECK_URL:-}" ]; then
    if curl -fsS -m 15 --retry 3 -o /dev/null "$HEALTHCHECK_URL"; then
      echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Heartbeat sent." >> "$LOG"
    else
      echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] WARNING: heartbeat ping failed." >> "$LOG"
    fi
  fi
elif [ "$delivery_ok" -ne 1 ]; then
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Delivery failed; heartbeat withheld." >> "$LOG"
else
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] WARNING: delivery succeeded but $HC_CONF is missing or unreadable; heartbeat NOT sent." >> "$LOG"
fi
