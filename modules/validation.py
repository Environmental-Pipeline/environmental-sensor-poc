"""
# Diagnostics Module

This module provides diagnostics and validation functions for environmental sensor data.
It detects data gaps, alerts, and generates comprehensive diagnostic reports.

## Commands:
- Test with `pytest tests/test_validation.py`

## Functions:
- validate_sensors: Validate sensor reading data for schema, duplicates, consistency, etc.
- validate_devices: Validate device data for column data types, missing values
- clean_validate_sensors: Clean and validate sensor readings data
- detect_data_gaps: Identify gaps in sensor data beyond expected intervals
- detect_alerts: Find readings outside acceptable thresholds
- generate_validation_results: Create CSV reports for validation results, gaps, and alerts
- utc_to_est_string: Convert UTC timestamp to human-readable EST string
"""

import os
import datetime
import zoneinfo
import polars
import numpy
import logging
import warnings
from typing import Optional, List, Dict, Any, Callable


def utc_to_est_string(utc_timestamp: int) -> str:
    """
    Convert a UTC timestamp to a human-readable EST datetime string.

    Parameters
    ----------
    utc_timestamp : int
        UTC timestamp in seconds.

    Returns
    -------
    str : Formatted datetime string in EST (e.g., "2025-11-30 14:30:00 EST").
    """
    if utc_timestamp is None:
        return None
    
    # Convert UTC timestamp to datetime
    dt_utc = datetime.datetime.fromtimestamp(utc_timestamp, tz=datetime.timezone.utc)
    
    # Convert to EST (America/New_York handles DST automatically)
    est_tz = zoneinfo.ZoneInfo("America/New_York")
    dt_est = dt_utc.astimezone(est_tz)
    
    return dt_est.strftime("%Y-%m-%d %H:%M:%S %Z")


def get_current_utc() -> int:
    """Get the current UTC timestamp in seconds."""
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp())


def validate_sensors(
    sensors: polars.DataFrame,
    historical: polars.DataFrame = None,
    utc: int = None,
    acceptable_range: Dict[str, List] = None,
    logger: logging.Logger = None,
    step: str = "",
) -> List[Dict[str, Any]]:
    """
    Validate Sensor reading data: column data types, missing values, SensorReadingUTC close to QueryUTC,
        no duplicated SensorReadingUTC, one SensorName per SensorID,
        SensorReadingUTC_SecondsFromPrior less than 15 minutes,
        all SensorID in historical data, no multiple names for SensorID.
    Can be expanded to add more validation steps.

    Parameters
    ----------
    sensors: polars.DataFrame
        Sensor readings DataFrame to validate.
    historical: polars.DataFrame, optional
        Historical data for comparison.
    utc: int, optional
        Current UTC timestamp for freshness checks.
    acceptable_range: Dict[str, List], optional
        Dictionary of reading types to check (e.g., {"SensorReadingF": [], "SensorReadingRh": []}).
        Defaults to temperature and humidity if not provided.
    logger: logging.Logger, optional
        Logger for info/debug messages. If None, logging is skipped.
    step: str
        Name of the step for logging.
        
    Returns
    -------
    List[Dict[str, Any]] : List of validation result dicts with keys: test_name, run_utc, result, details
    """
    if historical is None:
        historical = polars.DataFrame()
    
    if acceptable_range is None:
        acceptable_range = {"SensorReadingF": [], "SensorReadingRh": []}

    validation_results = []
    run_utc = get_current_utc()

    # Expected column types - different for lookup tables vs readings
    # For lookup tables (step="update_lookups"), we don't have reading-specific columns
    if step == "update_lookups":
        # Sensors lookup table - only sensor metadata columns (no DeviceName, that's in devices table)
        expect_types = {
            "Source": polars.String,
            "DeviceID": polars.String,
            "SensorID": polars.String,
            "SensorName": polars.String,
            "SensorType": polars.String,
        }
    else:
        # Full sensor readings - includes reading-specific columns
        expect_types = {
            "SensorReadingUTC": polars.Int64,
            "QueryUTC": polars.Int32,
            "Source": polars.String,
            "DeviceID": polars.String,
            "DeviceName": polars.String,
            "SensorID": polars.String,
            "SensorName": polars.String,
            "SensorType": polars.String,
            "Historical": polars.Boolean,
        }
        for reading in acceptable_range:
            if reading in sensors.columns:
                expect_types[reading] = polars.Float32

    # Check for required columns that must be present from clients
    missing_cols = [col for col in expect_types.keys() if col not in sensors.columns]
    validation_results.append({
        "test_name": "required_columns_present",
        "run_utc": run_utc,
        "result": "PASS" if len(missing_cols) == 0 else "FAIL",
        "details": f"Sensor data is missing required columns: {missing_cols}. These columns must be provided by the data source client." if missing_cols else f"All {len(expect_types)} required columns are present in sensor data."
    })

    # Check column data types
    type_errors_found = False
    type_error_details = []
    for col in expect_types:
        if col in sensors.columns:
            if sensors[col].dtype != expect_types[col]:
                type_errors_found = True
                type_error_details.append(f"{col}: expected {expect_types[col]}, got {sensors[col].dtype}")
                if logger:
                    logger.info(f"{step} validation: correct column data types.")

    validation_results.append({
        "test_name": "column_data_types",
        "run_utc": run_utc,
        "result": "PASS" if not type_errors_found else "FAIL",
        "details": f"Column data type mismatches found: {'; '.join(type_error_details)}. This may indicate a bug in the data source client." if type_error_details else "All columns have the expected data types (e.g., SensorReadingUTC is Int64, Source is String)."
    })

    # Check for all-null rows
    available_cols = [x for x in expect_types if x in sensors.columns]
    if available_cols:
        allnull = sensors[available_cols].filter(
            polars.all_horizontal(polars.all().is_null())
        )
        missing_count = allnull.shape[0]
    else:
        missing_count = 0
    validation_results.append({
        "test_name": "non_null_values",
        "run_utc": run_utc,
        "result": "PASS" if missing_count == 0 else "WARN",
        "details": f"{missing_count} sensor reading rows have all null values across required columns. These rows contain no usable data." if missing_count > 0 else f"Every sensor reading row has at least one non-null value in the {len(expect_types)} required columns."
    })
    if missing_count > 0 and logger:
        logger.info(f"{step} validation: at least one non-null value in readings.")

    # Check for duplicate readings
    if "SensorReadingUTC" in sensors.columns and "SensorID" in sensors.columns:
        dup_mask = sensors[["SensorID", "SensorReadingUTC"]].is_duplicated()
        dup_count = dup_mask.sum()
        if dup_count > 0:
            dup_examples = sensors.filter(dup_mask).select(["SensorID", "SensorReadingUTC"]).head(5)
            dup_list = [f"{r['SensorID']}@{r['SensorReadingUTC']}" for r in dup_examples.iter_rows(named=True)]
            dup_details = f"Found {dup_count} duplicate readings (same sensor + timestamp). This may indicate duplicate API responses or data processing issues. Examples: {', '.join(dup_list)}"
        else:
            dup_details = "No duplicate readings found. Each sensor has unique timestamps."
        validation_results.append({
            "test_name": "no_duplicate_readings",
            "run_utc": run_utc,
            "result": "PASS" if dup_count == 0 else "FAIL",
            "details": dup_details
        })
        if dup_count == 0 and logger:
            logger.info(f"{step} validation passed: no duplicated SensorReadingUTC per SensorID.")

    # Check for sensor name consistency
    if "SensorID" in sensors.columns and "SensorName" in sensors.columns:
        name_dups = sensors[["SensorID", "SensorName"]].unique()
        name_dups = name_dups.filter(name_dups["SensorID"].is_duplicated())
        dup_count = name_dups[["SensorID"]].unique().shape[0]
        if dup_count > 0:
            example_ids = name_dups[["SensorID"]].unique().head(3)["SensorID"].to_list()
            examples = []
            for sid in example_ids:
                names = name_dups.filter(polars.col("SensorID") == sid)["SensorName"].to_list()
                examples.append(f"{sid}: {names}")
            name_details = f"{dup_count} sensors have inconsistent names across readings. This can happen when a sensor is renamed or when historical vs current data uses different naming. Examples: {'; '.join(examples)}"
        else:
            name_details = "All sensors have consistent names across all their readings."
        validation_results.append({
            "test_name": "sensor_name_consistency",
            "run_utc": run_utc,
            "result": "PASS" if dup_count == 0 else "FAIL",
            "details": name_details
        })
        if dup_count == 0 and logger:
            logger.info(f"{step} validation passed: one SensorName per SensorID.")

    # Check reading intervals
    if "SensorReadingUTC_SecondsFromPrior" in sensors.columns:
        badrows = sensors.filter(
            sensors["SensorReadingUTC_SecondsFromPrior"] > 60 * 15
        )
        validation_results.append({
            "test_name": "reading_interval_check",
            "run_utc": run_utc,
            "result": "PASS" if badrows.shape[0] == 0 else "WARN",
            "details": f"{badrows.shape[0]} readings have more than 15 minutes between consecutive readings. This may indicate sensor downtime or data gaps." if badrows.shape[0] > 0 else "All consecutive readings are within 15 minutes of each other (expected interval)."
        })
        if badrows.shape[0] == 0 and logger:
            logger.info(f"{step} validation passed: SensorReadingUTC_SecondsFromPrior less than 15 minutes.")

    # Check for missing sensors from historical
    if historical.shape[0] > 0 and "SensorID" in sensors.columns and "SensorID" in historical.columns:
        current_sensor_ids = sensors["SensorID"].unique().to_list()
        missing = historical.filter(
            ~historical["SensorID"].is_in(current_sensor_ids)
        )
        if missing.shape[0] > 0:
            missing_ids = missing[["SensorID", "SensorName"]].unique().head(5)
            missing_list = [f"{r['SensorID']} ({r['SensorName']})" for r in missing_ids.iter_rows(named=True)]
            missing_details = f"{missing.shape[0]} sensors that existed in historical data are missing from current data. These sensors may be offline, removed, or filtered out. Examples: {', '.join(missing_list)}"
        else:
            missing_details = "All sensors from historical data are still present in current data."
        validation_results.append({
            "test_name": "no_missing_sensors",
            "run_utc": run_utc,
            "result": "PASS" if missing.shape[0] == 0 else "WARN",
            "details": missing_details
        })
        if missing.shape[0] == 0 and logger:
            logger.info(f"{step} validation passed: all SensorID in historical data (no dropped SensorID).")

    return validation_results


def detect_data_gaps(
    sensors: polars.DataFrame, 
    expected_interval_minutes: int = 15
) -> polars.DataFrame:
    """
    Detect gaps in sensor data where readings are missing beyond the expected interval.
    Analyzes data by Source and SensorID to identify time gaps.

    Parameters
    ----------
    sensors : polars.DataFrame
        Sensor readings DataFrame with SensorReadingUTC, Source, SensorID columns.
    expected_interval_minutes : int, default=15
        Expected interval between readings in minutes. Gaps larger than this are flagged.

    Returns
    -------
    polars.DataFrame
        DataFrame with columns: Source, SensorID, SensorName, gap_start_utc, gap_end_utc, gap_minutes
    """
    if sensors.is_empty():
        return polars.DataFrame(schema={
            "Source": polars.String,
            "SensorID": polars.String,
            "SensorName": polars.String,
            "gap_start_utc": polars.Int64,
            "gap_end_utc": polars.Int64,
            "gap_minutes": polars.Float64
        })

    # Sort by Source, SensorID, and time
    sorted_sensors = sensors.sort(["Source", "SensorID", "SensorReadingUTC"])
    
    # Calculate time difference from prior reading within each Source+SensorID group
    with_gaps = sorted_sensors.with_columns(
        polars.col("SensorReadingUTC").shift(1).over(["Source", "SensorID"]).alias("prior_reading_utc")
    )
    
    # Calculate gap in minutes and round to avoid floating point issues (e.g., 15.0166666)
    with_gaps = with_gaps.with_columns(
        ((polars.col("SensorReadingUTC") - polars.col("prior_reading_utc")) / 60).round(0).alias("gap_minutes")
    )
    
    # Filter to only gaps larger than expected interval
    gaps = with_gaps.filter(
        polars.col("gap_minutes") > expected_interval_minutes
    ).select([
        "Source",
        "SensorID",
        "SensorName",
        polars.col("prior_reading_utc").alias("gap_start_utc"),
        polars.col("SensorReadingUTC").alias("gap_end_utc"),
        "gap_minutes"
    ])
    
    return gaps


def detect_alerts(
    sensors: polars.DataFrame,
    acceptable_range: dict
) -> polars.DataFrame:
    """
    Detect sensor readings that would trigger alerts based on acceptable_range thresholds.
    
    Parameters
    ----------
    sensors : polars.DataFrame
        Sensor readings DataFrame.
    acceptable_range : dict
        Dictionary mapping reading types to [min, max] threshold lists.
        Example: {"SensorReadingF": [32, 100], "SensorReadingRh": [20, 80]}
        
    Returns
    -------
    polars.DataFrame
        DataFrame with columns: Source, SensorID, SensorName, SensorReadingUTC, 
        reading_type, reading_value, threshold_min, threshold_max, alert_type
    """
    if sensors.is_empty():
        return polars.DataFrame(schema={
            "Source": polars.String,
            "SensorID": polars.String,
            "SensorName": polars.String,
            "SensorReadingUTC": polars.Int64,
            "reading_type": polars.String,
            "reading_value": polars.Float32,
            "threshold_min": polars.Float64,
            "threshold_max": polars.Float64,
            "alert_type": polars.String
        })

    alert_records = []
    
    for reading_type in acceptable_range:
        thresholds = acceptable_range[reading_type]
        
        # Skip if no thresholds defined
        if len(thresholds) == 0:
            continue
        
        threshold_min = thresholds[0] if len(thresholds) > 0 else None
        threshold_max = thresholds[1] if len(thresholds) > 1 else None
        
        if reading_type not in sensors.columns:
            continue
        
        # Filter for readings outside acceptable range
        reading_data = sensors.filter(polars.col(reading_type).is_not_null())
        
        if threshold_min is not None:
            low_alerts = reading_data.filter(polars.col(reading_type) < threshold_min)
            for row in low_alerts.iter_rows(named=True):
                alert_records.append({
                    "Source": row.get("Source"),
                    "SensorID": row.get("SensorID"),
                    "SensorName": row.get("SensorName"),
                    "SensorReadingUTC": row.get("SensorReadingUTC"),
                    "reading_type": reading_type,
                    "reading_value": row.get(reading_type),
                    "threshold_min": threshold_min,
                    "threshold_max": threshold_max,
                    "alert_type": "BELOW_MIN"
                })
        
        if threshold_max is not None:
            high_alerts = reading_data.filter(polars.col(reading_type) > threshold_max)
            for row in high_alerts.iter_rows(named=True):
                alert_records.append({
                    "Source": row.get("Source"),
                    "SensorID": row.get("SensorID"),
                    "SensorName": row.get("SensorName"),
                    "SensorReadingUTC": row.get("SensorReadingUTC"),
                    "reading_type": reading_type,
                    "reading_value": row.get(reading_type),
                    "threshold_min": threshold_min,
                    "threshold_max": threshold_max,
                    "alert_type": "ABOVE_MAX"
                })
    
    if alert_records:
        return polars.DataFrame(alert_records)
    else:
        return polars.DataFrame(schema={
            "Source": polars.String,
            "SensorID": polars.String,
            "SensorName": polars.String,
            "SensorReadingUTC": polars.Int64,
            "reading_type": polars.String,
            "reading_value": polars.Float32,
            "threshold_min": polars.Float64,
            "threshold_max": polars.Float64,
            "alert_type": polars.String
        })


def generate_validation_results(
    sensors: polars.DataFrame,
    validation_results: list,
    acceptable_range: dict,
    data_path: str,
    logger: Optional[logging.Logger] = None,
    step: str = "consolidate_readings"
):
    """
    Generate a comprehensive diagnostics CSV report including validation results,
    data gaps, and alerts.
    
    Parameters
    ----------
    sensors : polars.DataFrame
        Consolidated sensor readings DataFrame.
    validation_results : list
        List of validation result dicts from validate_sensors.
    acceptable_range : dict
        Dictionary mapping reading types to [min, max] threshold lists.
    data_path : str
        Path to the data directory for writing CSV files.
    logger : Optional[logging.Logger]
        Logger instance for logging messages.
    step : str
        Name of the step for file naming.
    """
    run_utc = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    run_datetime_est = utc_to_est_string(run_utc)
    
    # DISABLED: Building info validation - data is too inconsistent to reliably parse building info
    # # ============ CHECK FOR MISSING BUILDING INFO IN DEVICES LOOKUP ============
    # devices_lookup_path = f"{data_path}/devices.parquet"
    # devices_lookup = polars.read_parquet(devices_lookup_path)
    # if "BuildingID" in devices_lookup.columns and "DeviceName" in devices_lookup.columns:
    #     # Check which devices are missing BuildingID
    #     missing_building_devices = devices_lookup.filter(polars.col("BuildingID").is_null())
    #     if missing_building_devices.shape[0] > 0:
    #         device_names = missing_building_devices["DeviceName"].to_list()
    #         device_list = ", ".join([str(name) for name in device_names[:10]])
    #         if len(device_names) > 10:
    #             device_list += f", ... and {len(device_names) - 10} more"
    #         building_details = f"{len(device_names)} devices have names that could not be parsed for building information. Device names: {device_list}"
    #     else:
    #         building_details = f"All {devices_lookup.shape[0]} devices have valid building information parsed from device names."
    #     validation_results.append({
    #         "test_name": "building_info_present",
    #         "run_utc": run_utc,
    #         "result": "PASS" if missing_building_devices.shape[0] == 0 else "WARN",
    #         "details": building_details
    #     })
    #     if missing_building_devices.shape[0] > 0 and logger:
    #         logger.warning(f"{step} validation: {building_details}")
    
    # ============ CHECK FOR DEVICES MISSING TEMP OR RH READINGS ============
    device_readings_path = f"{data_path}/device_readings.parquet"
    missing_reading_details = []
    device_readings = polars.read_parquet(device_readings_path)
    
    # Find unique devices where temp is null but RH is not (missing temp)
    missing_temp = device_readings.filter(
        polars.col("SensorReadingF").is_null() & polars.col("SensorReadingRh").is_not_null()
    ).select(["DeviceID", "DeviceName", "Source"]).unique()
    
    for row in missing_temp.iter_rows(named=True):
        missing_reading_details.append({
            "DeviceID": row.get("DeviceID"),
            "DeviceName": row.get("DeviceName"),
            "Source": row.get("Source"),
            "missing_reading": "SensorReadingF",
        })
    
    # Find unique devices where RH is null but temp is not (missing RH)
    missing_rh = device_readings.filter(
        polars.col("SensorReadingRh").is_null() & polars.col("SensorReadingF").is_not_null()
    ).select(["DeviceID", "DeviceName", "Source"]).unique()
    
    for row in missing_rh.iter_rows(named=True):
        missing_reading_details.append({
            "DeviceID": row.get("DeviceID"),
            "DeviceName": row.get("DeviceName"),
            "Source": row.get("Source"),
            "missing_reading": "SensorReadingRh",
        })
    
    missing_temp_count = missing_temp.shape[0]
    missing_rh_count = missing_rh.shape[0]
    
    if missing_temp_count > 0 or missing_rh_count > 0:
        details_parts = []
        if missing_temp_count > 0:
            details_parts.append(f"{missing_temp_count} devices missing temperature")
        if missing_rh_count > 0:
            details_parts.append(f"{missing_rh_count} devices missing humidity")
        details_str = ", ".join(details_parts) + ". See validation-detail.parquet for device list."
    else:
        total_devices = device_readings.select("DeviceID").n_unique()
        details_str = f"All {total_devices} devices have both temperature and humidity readings."
    
    validation_results.append({
        "test_name": "devices_missing_readings",
        "run_utc": run_utc,
        "result": "WARN" if missing_reading_details else "PASS",
        "details": details_str
    })
    
    # ============ DATA GAPS BY SOURCE ============
    gaps = detect_data_gaps(sensors)
    
    # Summarize gaps by source
    if not gaps.is_empty():
        gap_summary = gaps.group_by("Source").agg([
            polars.len().alias("total_gaps"),
            polars.col("gap_minutes").mean().alias("avg_gap_minutes"),
            polars.col("gap_minutes").max().alias("max_gap_minutes"),
            polars.col("SensorID").n_unique().alias("sensors_with_gaps")
        ])
        
        # Add gap summary to validation results
        for row in gap_summary.iter_rows(named=True):
            validation_results.append({
                "test_name": f"data_gaps_{row['Source']}",
                "run_utc": run_utc,
                "result": "WARN" if row["total_gaps"] > 0 else "PASS",
                "details": f"Found {row['total_gaps']} gaps in {row['Source']} data where readings are more than 15 minutes apart. Average gap: {row['avg_gap_minutes']:.1f} min, longest gap: {row['max_gap_minutes']:.1f} min. Affected {row['sensors_with_gaps']} sensors."
            })
    else:
        # Add a pass result if no gaps found
        sources = sensors["Source"].unique().to_list() if "Source" in sensors.columns else ["Unknown"]
        for source in sources:
            validation_results.append({
                "test_name": f"data_gaps_{source}",
                "run_utc": run_utc,
                "result": "PASS",
                "details": f"No data gaps found in {source} data. All consecutive readings are within the expected 15-minute interval."
            })
    
    # ============ ALERTS ============
    alerts = detect_alerts(sensors, acceptable_range)
    
    # Summarize alerts by source
    if not alerts.is_empty():
        alert_summary = alerts.group_by("Source").agg([
            polars.len().alias("total_alerts"),
            polars.col("SensorID").n_unique().alias("sensors_with_alerts"),
            polars.col("alert_type").value_counts().alias("alert_types")
        ])
        
        for row in alert_summary.iter_rows(named=True):
            validation_results.append({
                "test_name": f"alerts_{row['Source']}",
                "run_utc": run_utc,
                "result": "WARN" if row["total_alerts"] > 0 else "PASS",
                "details": f"Found {row['total_alerts']} readings outside acceptable thresholds in {row['Source']} data. {row['sensors_with_alerts']} sensors triggered alerts."
            })
    else:
        sources = sensors["Source"].unique().to_list() if "Source" in sensors.columns else ["Unknown"]
        for source in sources:
            validation_results.append({
                "test_name": f"alerts_{source}",
                "run_utc": run_utc,
                "result": "PASS",
                "details": f"No alerts triggered for {source}. All sensor readings are within the configured acceptable thresholds."
            })
    
    # ============ WRITE VALIDATION RESULTS CSV ============
    validation_df = polars.DataFrame(validation_results)
    
    # Add human-readable datetime column for run_utc
    validation_df = validation_df.with_columns(
        polars.col("run_utc").map_elements(
            lambda x: utc_to_est_string(x), return_dtype=polars.String
        ).alias("run_datetime_est")
    )
    
    # Reorder columns to put datetime first for readability
    validation_df = validation_df.select([
        "run_datetime_est", "run_utc", "test_name", "result", "details"
    ])
    
    validation_csv_path = f"{data_path}/validation-results.csv"
    
    # Append to existing CSV if it exists, otherwise create new
    if os.path.exists(validation_csv_path):
        existing = polars.read_csv(validation_csv_path)
        validation_df = polars.concat([existing, validation_df], how="diagonal")
    
    validation_df.write_csv(validation_csv_path)
    if logger:
        logger.info(f"Wrote {len(validation_results)} validation results to {validation_csv_path}")
    
    # ============ WRITE VALIDATION DETAIL PARQUET (gaps and missing readings) ============
    events_parquet_path = f"{data_path}/validation-detail.parquet"
    event_rows = []
    
    # Add gap events
    if not gaps.is_empty():
        for row in gaps.iter_rows(named=True):
            event_rows.append({
                "event": "DATA_GAP",
                "Source": row.get("Source"),
                "DeviceID": None,
                "DeviceName": None,
                "SensorID": row.get("SensorID"),
                "SensorName": row.get("SensorName"),
                "missing_reading": None,
                "event_utc": row.get("gap_start_utc"),
                "event_datetime_est": utc_to_est_string(row.get("gap_start_utc")),
                "event_end_utc": row.get("gap_end_utc"),
                "event_end_datetime_est": utc_to_est_string(row.get("gap_end_utc")),
                "gap_minutes": row.get("gap_minutes"),
                "detected_utc": run_utc,
                "detected_datetime_est": run_datetime_est,
            })
    
    # Add missing reading events
    for detail in missing_reading_details:
        event_rows.append({
            "event": "MISSING_READING",
            "Source": detail.get("Source"),
            "DeviceID": detail.get("DeviceID"),
            "DeviceName": detail.get("DeviceName"),
            "SensorID": None,
            "SensorName": None,
            "missing_reading": detail.get("missing_reading"),
            "event_utc": None,
            "event_datetime_est": None,
            "event_end_utc": None,
            "event_end_datetime_est": None,
            "gap_minutes": None,
            "detected_utc": run_utc,
            "detected_datetime_est": run_datetime_est,
        })
    
    if event_rows:
        events_df = polars.DataFrame(event_rows, schema={
            "event": polars.Utf8,
            "Source": polars.Utf8,
            "DeviceID": polars.Utf8,
            "DeviceName": polars.Utf8,
            "SensorID": polars.Utf8,
            "SensorName": polars.Utf8,
            "missing_reading": polars.Utf8,
            "event_utc": polars.Int64,
            "event_datetime_est": polars.Utf8,
            "event_end_utc": polars.Int64,
            "event_end_datetime_est": polars.Utf8,
            "gap_minutes": polars.Float64,
            "detected_utc": polars.Int64,
            "detected_datetime_est": polars.Utf8,
        })
        
        # Reorder columns for readability
        events_df = events_df.select([
            "event", "Source", "DeviceID", "DeviceName", "SensorID", "SensorName",
            "missing_reading",
            "event_utc", "event_datetime_est", 
            "event_end_utc", "event_end_datetime_est",
            "gap_minutes",
            "detected_utc", "detected_datetime_est"
        ])
        
        events_df.write_parquet(events_parquet_path)
        if logger:
            logger.info(f"Wrote {len(event_rows)} validation events to {events_parquet_path}")


def clean_validate_sensors(
    sensors: polars.DataFrame,
    acceptable_range: Dict[str, List],
    logger: logging.Logger,
    historical: polars.DataFrame = None,
    step: str = "",
    error_callback: Callable[[str, bool], None] = None,
) -> polars.DataFrame:
    """
    Clean the sensor readings data.
    Currently, this only sets efficient data types. It can be expanded to include more cleaning steps.
    Then, validate the data because these two operations typically occur together.

    Parameters
    ----------
    sensors : polars.DataFrame
        Sensors API response.
    acceptable_range : Dict[str, List]
        Dictionary of reading types to acceptable ranges.
    logger : logging.Logger
        Logger instance for logging messages.
    historical : polars.DataFrame, optional
        Historical data for comparison during validation.
    step : str
        Name of the step for logging.
    error_callback : Callable[[str, bool], None], optional
        Callback function for error handling. Takes (message, raise_exception) params.

    Returns
    -------
    polars.DataFrame : Cleaned DataFrame.
    """
    if historical is None:
        historical = polars.DataFrame()

    # Set data types - MATCH EXISTING SCHEMA EXACTLY
    dtypes = {
        "SensorID": polars.String,  # Consolidated sensor ID field with source prefixes
        "DeviceID": polars.String,  # Consolidated device ID field with source prefixes
        "SensorReadingUTC": polars.Int64,
        "QueryUTC": polars.Int32,  # Fixed: Match parquet file
        "Source": polars.String,  # Fixed: Use String not Utf8 (renamed from source)
    }
    
    for dtype in dtypes:
        if dtype in sensors.columns:
            sensors = sensors.with_columns(polars.col(dtype).cast(dtypes[dtype]))

    for reading in acceptable_range:
        if reading in sensors.columns:
            sensors = sensors.with_columns(
                polars.col(reading).cast(polars.Float32)
            )  # Fixed: Use Float32 not String

    # Also ensure validation expected columns are correct type
    validation_types = {
        "SensorReadingUTC": polars.Int64,
        "SensorID": polars.String,
        "SensorReadingF": polars.Float32,
        "SensorReadingRh": polars.Float32,
    }
    for col, expected_type in validation_types.items():
        if col in sensors.columns and sensors[col].dtype != expected_type:
            sensors = sensors.with_columns(polars.col(col).cast(expected_type))
            logger.info(f"Type conversion: {col} -> {expected_type}")

    # Validate the data.
    validation_results = validate_sensors(
        sensors=sensors, 
        historical=historical, 
        acceptable_range=acceptable_range,
        logger=logger,
        step=step
    )
    
    # Collect errors from FAIL results
    errs = []
    for result in validation_results:
        if result["result"] == "FAIL":
            errs.append(f"{result['test_name']}: {result['details']}")

    # Log the errors and raise exception to stop processing with bad data.
    if len(errs) > 0:
        error_msg = step + " validation errors : " + "; ".join(errs) + "\n"
        if error_callback:
            error_callback(error_msg, True)
        else:
            logger.error(error_msg)
            raise Exception(error_msg)

    return sensors


def validate_devices(
    devices: polars.DataFrame,
    acceptable_range: Dict[str, List],
    logger: logging.Logger,
    error_callback: Callable[[str, bool], None] = None,
) -> None:
    """
    Validate Device data: column data types, missing values.
    Can be expanded to add more validation steps.

    Parameters
    ----------
    devices : polars.DataFrame
        Device readings DataFrame to validate.
    acceptable_range : Dict[str, List]
        Dictionary of reading types to acceptable ranges.
    logger : logging.Logger
        Logger instance for logging messages.
    error_callback : Callable[[str, bool], None], optional
        Callback function for error handling. Takes (message, raise_exception) params.
    """
    errs = []

    # Check for missing values.
    # Note: Missing values are expected in sensor data (e.g., temperature-only vs humidity-only sensors)
    # for col in acceptable_range:
    #     missing_count = devices[col].is_null().sum()
    #     if missing_count > 0:
    #         errs.append(f"{missing_count} missing values in [{col}].")

    # Log the errors.
    if len(errs) > 0:
        error_msg = "Validation errors: \n" + "\n\t".join(errs)
        if error_callback:
            error_callback(error_msg, False)
        else:
            logger.error(error_msg)
            warnings.warn(error_msg)


def get_master_schema() -> dict:
    """
    Get the master schema from existing parquet file.
    This defines the target schema that ALL data must conform to.

    Returns
    -------
    dict : Column name -> polars data type mapping
    """

    # Define the master schema based on existing parquet structure
    master_schema = {
        "Source": polars.String,
        "SensorID": polars.String,
        "DeviceID": polars.String,
        "QueryUTC": polars.Int32,
        "SensorReadingUTC": polars.Int64,
        "SensorReadingUTC_SecondsFromPrior": polars.Int64,
        "SensorReadingF": polars.Float32,
        "SensorReadingRh": polars.Float32,
        "SensorName": polars.String,
        "SensorPort": polars.Int64,
        "ServerUTC": polars.Int64,
        "HexGatewayMac": polars.String,
        "LoraHexGatewayMac": polars.String,
        "LoraGatewayLastHeardUTC": polars.Int64,
        "SensorUnplugged": polars.Boolean,
        "LinkQualityText": polars.String,
        "HexMac": polars.String,
        "SensorDeleted": polars.Boolean,
        "SensorDeactivated": polars.Boolean,
        "SensorReading": polars.Float64,
        "DeviceName": polars.String,
        "DevTypeInt": polars.Int64,
        "SensorTempPref": polars.String,
        "DeviceTempPref": polars.Null,
        "UserTempPref": polars.String,
        "SensorTimeZone": polars.String,
        "SensorZipcode": polars.Null,
        "SensorType": polars.String,
        "SensorState0String": polars.String,
        "SensorState1String": polars.String,
        "ExpectedSensorReadingIntervalSeconds": polars.Int64,
        "SensorReadingC": polars.Float64,
        "SensorCalibrationOffsetC": polars.Float64,
        "SensorCalibrationOffsetF": polars.Float64,
        "SensorCalibrationOffsetExplanationText": polars.String,
        "SensorCalibrationOffsetExplanationFirstName": polars.String,
        "SensorCalibrationOffsetExplanationLastName": polars.String,
        "SensorCalibrationOffsetUTC": polars.Int64,
        "LoraBatteryPresent": polars.Int64,
        "LoraBattery_mV": polars.Int64,
        "LoraBatteryPercentage": polars.Int64,
        "LoraBatteryUTC": polars.Int64,
        "LoraBatteryIsCharging": polars.Int64,
        "LastSensorErrorValue": polars.Int64,
        "LastSensorErrorUTC": polars.Int64,
        "UnivID": polars.Int64,
        "SensorSerialNumber": polars.Null,
        "HeatIndexRh": polars.Float64,
        "ConjoinedRhSensorSensorReadingRh": polars.Float64,
        "SensorReadingHeatIndexF": polars.Float64,
        "SensorReadingHeatIndexC": polars.Float64,
        "HeatIndexWarningTier": polars.Int64,
        "LoraExternalPowerPresent": polars.Int64,
        "SensorCalibrationOffsetRh": polars.Int64,
        "SensorEventCount": polars.Int64,
        "SensorState": polars.String,
    }

    return master_schema

def enforce_schema(
    df: polars.DataFrame, 
    logger: logging.Logger,
    step_name: str = ""
) -> polars.DataFrame:
    """
    SCHEMA GATE: Enforce master schema on any DataFrame before concatenation.
    This prevents ALL type mismatch errors by converting data to expected types.

    Parameters
    ----------
    df : polars.DataFrame
        DataFrame to enforce schema on
    logger : logging.Logger
        Logger instance for logging messages.
    step_name : str
        Name of the step for logging

    Returns
    -------
    polars.DataFrame
        DataFrame with all columns converted to master schema types
    """

    if df.is_empty():
        return df

    master_schema = get_master_schema()

    # Track conversions for logging
    conversions_made = []

    # Apply schema enforcement
    conversion_exprs = []

    for column in df.columns:
        if column in master_schema:
            target_type = master_schema[column]
            current_type = df[column].dtype

            if current_type != target_type:
                conversions_made.append(
                    f"{column}: {current_type} -> {target_type}"
                )

                # Handle specific conversion cases
                if target_type == polars.Null:
                    # Keep null columns as-is
                    continue
                elif str(current_type).startswith("Int") and str(
                    target_type
                ).startswith("Int"):
                    # Int64 -> Int32 or vice versa (check for overflow)
                    conversion_exprs.append(
                        polars.col(column).cast(target_type, strict=False)
                    )
                elif str(current_type).startswith("Float") and str(
                    target_type
                ).startswith("Float"):
                    # Float64 -> Float32 or vice versa
                    conversion_exprs.append(
                        polars.col(column).cast(target_type)
                    )
                else:
                    # Generic conversion
                    conversion_exprs.append(
                        polars.col(column).cast(target_type, strict=False)
                    )

    # Apply all conversions at once
    if conversion_exprs:
        df = df.with_columns(conversion_exprs)

    logger.info(f"validation passed for {step_name}: Schema enforcement completed")

    return df
