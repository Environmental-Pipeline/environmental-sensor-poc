# Deployment Guide

This document describes the deployment configuration for the Environmental Sensor POC.

## VM Setup

The application runs on a **Spinup Ubuntu** VM. The main application runs inside a Docker container, with an additional upload script running directly on the VM.

## Upload Script

The upload script is located at:

```
/usr/local/bin/upload-sensor-readings-parquet.sh
```

This script uploads the daily parquet file to both S3 and Microsoft Fabric. The source script is maintained in `scripts/upload-sensor-readings-parquet.sh` in this repository.

### Cron Schedule

The upload runs daily at 2:15 AM (after the consolidate job completes):

```
15 2 * * * /usr/local/bin/upload-sensor-readings-parquet.sh
```

This cron job runs on the VM itself, not inside the Docker container.

## Upload Destinations

### AWS S3

- **Bucket**: `s3://spinup-002f52-yale-env-sensor-poc-data/`

### Microsoft Fabric

- **Destination**: `https://edafileuploads.dfs.core.windows.net/cultural-heritage-environmental-monitoring-dev/Incoming/`

## Service Principal Configuration (Microsoft Fabric)

Authentication to Microsoft Fabric uses a service principal with certificate-based authentication.

| Configuration | Value |
|---------------|-------|
| App ID | `c8060d79-3927-46b2-943e-d13020cfcefe` |
| Tenant ID | `dd8cbebb-2139-4df8-b411-4e3e87abeb5c` |
| Certificate Path | `/home/aha48/fabric-service-principal-combined.pem` |
