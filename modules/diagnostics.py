"""
# Diagnostics Module

This module provides diagnostics and validation functions for environmental sensor data.
It detects data gaps, alerts, and generates comprehensive diagnostic reports.

## Commands:
- Test with `pytest tests/test_EnvironmentData.py`

## Functions:
- detect_data_gaps: Identify gaps in sensor data beyond expected intervals
- detect_alerts: Find readings outside acceptable thresholds
- generate_diagnostics_report: Create CSV reports for validation results, gaps, and alerts
- utc_to_est_string: Convert UTC timestamp to human-readable EST string
"""

import os
import datetime
import zoneinfo
import polars
import logging
from typing import Optional


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


def generate_diagnostics_report(
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
                "details": f"Found {row['total_alerts']} readings outside acceptable thresholds in {row['Source']} data. {row['sensors_with_alerts']} sensors triggered alerts. Check alerts.csv for details."
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
    
    # ============ WRITE COMBINED VALIDATION EVENTS CSV (gaps + alerts) ============
    events_csv_path = f"{data_path}/alerts.csv"
    event_rows = []
    
    # Add gap events
    if not gaps.is_empty():
        for row in gaps.iter_rows(named=True):
            event_rows.append({
                "event": "DATA_GAP",
                "Source": row.get("Source"),
                "SensorID": row.get("SensorID"),
                "SensorName": row.get("SensorName"),
                "event_utc": row.get("gap_start_utc"),
                "event_datetime_est": utc_to_est_string(row.get("gap_start_utc")),
                "event_end_utc": row.get("gap_end_utc"),
                "event_end_datetime_est": utc_to_est_string(row.get("gap_end_utc")),
                "gap_minutes": row.get("gap_minutes"),
                "reading_type": None,
                "reading_value": None,
                "threshold_min": None,
                "threshold_max": None,
                "detected_utc": run_utc,
                "detected_datetime_est": run_datetime_est,
            })
    
    # Add alert events
    if not alerts.is_empty():
        for row in alerts.iter_rows(named=True):
            event_rows.append({
                "event": row.get("alert_type"),
                "Source": row.get("Source"),
                "SensorID": row.get("SensorID"),
                "SensorName": row.get("SensorName"),
                "event_utc": row.get("SensorReadingUTC"),
                "event_datetime_est": utc_to_est_string(row.get("SensorReadingUTC")),
                "event_end_utc": None,
                "event_end_datetime_est": None,
                "gap_minutes": None,
                "reading_type": row.get("reading_type"),
                "reading_value": row.get("reading_value"),
                "threshold_min": row.get("threshold_min"),
                "threshold_max": row.get("threshold_max"),
                "detected_utc": run_utc,
                "detected_datetime_est": run_datetime_est,
            })
    
    if event_rows:
        events_df = polars.DataFrame(event_rows)
        
        # Reorder columns for readability
        events_df = events_df.select([
            "event", "Source", "SensorID", "SensorName",
            "event_utc", "event_datetime_est", 
            "event_end_utc", "event_end_datetime_est",
            "gap_minutes", "reading_type", "reading_value", 
            "threshold_min", "threshold_max",
            "detected_utc", "detected_datetime_est"
        ])
        
        if os.path.exists(events_csv_path):
            existing_events = polars.read_csv(events_csv_path)
            events_df = polars.concat([existing_events, events_df], how="diagonal")
        
        events_df.write_csv(events_csv_path)
        if logger:
            logger.info(f"Wrote {len(event_rows)} validation events to {events_csv_path}")
