#!/usr/bin/env bash
set -euo pipefail

# === CONFIG ===
SRC="/opt/env-sensor-data/sensor_readings.parquet"
DATE_UTC="$(date -u +%Y-%m-%d)"
LOG="/home/aha48/sensor_readings_upload.log"

# S3 destination
AWS_PROFILE="env-sensors"
S3_DEST="s3://spinup-002f52-yale-env-sensor-poc-data/daily/sensor_readings_${DATE_UTC}.parquet"

# Fabric destination (service principal auth)
FABRIC_APP_ID="c8060d79-3927-46b2-943e-d13020cfcefe"
FABRIC_TENANT_ID="dd8cbebb-2139-4df8-b411-4e3e87abeb5c"
FABRIC_CERT="/home/aha48/fabric-service-principal-combined.pem"
FABRIC_DEST="https://edafileuploads.dfs.core.windows.net/cultural-heritage-environmental-monitoring-dev/Incoming/sensor_readings_${DATE_UTC}.parquet"

# === MAIN ===
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Starting upload..." >> "$LOG"

if [ ! -f "$SRC" ]; then
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] ERROR: file not found: $SRC" >> "$LOG"
  exit 1
fi

# Upload to S3
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Uploading to S3..." >> "$LOG"
aws s3 cp "$SRC" "$S3_DEST" --profile "$AWS_PROFILE" >> "$LOG" 2>&1
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] S3 upload complete." >> "$LOG"

# Authenticate to Fabric via service principal
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Authenticating to Fabric..." >> "$LOG"
azcopy login --service-principal --application-id "$FABRIC_APP_ID" --tenant-id "$FABRIC_TENANT_ID" --certificate-path "$FABRIC_CERT" >> "$LOG" 2>&1

# Upload to Fabric
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Uploading to Fabric..." >> "$LOG"
azcopy copy "$SRC" "$FABRIC_DEST" >> "$LOG" 2>&1
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Fabric upload complete." >> "$LOG"

echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] All uploads complete." >> "$LOG"
