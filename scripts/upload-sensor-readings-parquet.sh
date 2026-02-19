#!/usr/bin/env bash
set -euo pipefail

# === CONFIG ===
DATA_DIR="/opt/env-sensor-data"
DAILY_SRC="${DATA_DIR}/daily_export.parquet"
FULL_SRC="${DATA_DIR}/sensor_readings.parquet"
DATE_UTC="$(date -u +%Y-%m-%d)"
DAY_OF_MONTH="$(date -u +%d)"
LOG="/home/aha48/sensor_readings_upload.log"

# S3 destination
AWS_PROFILE="env-sensors"
S3_BUCKET="s3://spinup-002f52-yale-env-sensor-poc-data"

# Fabric destination (service principal auth)
FABRIC_APP_ID="c8060d79-3927-46b2-943e-d13020cfcefe"
FABRIC_TENANT_ID="dd8cbebb-2139-4df8-b411-4e3e87abeb5c"
FABRIC_CERT="/home/aha48/fabric-service-principal-combined.pem"
FABRIC_BASE="https://edafileuploads.dfs.core.windows.net/cultural-heritage-environmental-monitoring-dev/Incoming"

# ---------------------------------------------------------------------------
# Helper: upload a file to both S3 and Fabric
# ---------------------------------------------------------------------------
upload_file() {
  local src_file="$1"
  local s3_dest="$2"
  local fabric_dest="$3"
  local label="$4"

  if [ ! -f "$src_file" ]; then
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] ERROR: ${label} file not found: ${src_file}" >> "$LOG"
    return 1
  fi

  # Upload to S3
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Uploading ${label} to S3: ${s3_dest}" >> "$LOG"
  aws s3 cp "$src_file" "$s3_dest" --profile "$AWS_PROFILE" >> "$LOG" 2>&1
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] S3 ${label} upload complete." >> "$LOG"

  # Upload to Fabric
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Uploading ${label} to Fabric: ${fabric_dest}" >> "$LOG"
  azcopy copy "$src_file" "$fabric_dest" >> "$LOG" 2>&1
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Fabric ${label} upload complete." >> "$LOG"
}

# === MAIN ===
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Starting upload..." >> "$LOG"

# Authenticate to Fabric via service principal (once, up front)
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Authenticating to Fabric..." >> "$LOG"
azcopy login --service-principal --application-id "$FABRIC_APP_ID" --tenant-id "$FABRIC_TENANT_ID" --certificate-path "$FABRIC_CERT" >> "$LOG" 2>&1

# --- Daily incremental upload (delta only) ---
upload_file \
  "$DAILY_SRC" \
  "${S3_BUCKET}/daily/sensor_readings_${DATE_UTC}.parquet" \
  "${FABRIC_BASE}/sensor_readings_${DATE_UTC}.parquet" \
  "daily-incremental"

# --- Monthly full snapshot (1st of the month) ---
if [ "$DAY_OF_MONTH" = "01" ]; then
  MONTH_UTC="$(date -u +%Y-%m)"
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] First of month — uploading full snapshot..." >> "$LOG"
  upload_file \
    "$FULL_SRC" \
    "${S3_BUCKET}/monthly/sensor_readings_full_${MONTH_UTC}.parquet" \
    "${FABRIC_BASE}/sensor_readings_full_${MONTH_UTC}.parquet" \
    "monthly-full-snapshot"
fi

echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] All uploads complete." >> "$LOG"
