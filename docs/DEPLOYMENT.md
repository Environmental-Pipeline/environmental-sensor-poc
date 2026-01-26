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
| App ID | *(configured in environment)* |
| Tenant ID | *(configured in environment)* |
| Certificate Path | *(configured on VM)* |

The service principal credentials should be configured as environment variables or in a secure configuration file on the VM.
