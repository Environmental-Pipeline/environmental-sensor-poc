"""
CSC export filter.

Splits a sensor-readings DataFrame into rows that should be exported to
Fabric/S3 (sensors physically housed in the Collection Studies Center, building
code "CSC") and rows that should be excluded.

The filter operates on the parsed building code from SensorName, using the
existing `extract_building_code` helper from `modules.weather_enrichment`.
It does NOT inspect Source/account/customer; the rule is purely name-based,
because unit naming convention is the source of truth for "this sensor lives
in CSC".

Intended for use at export time (jobs/3-export-daily.py). Ingestion is not
affected — other units' sensors continue to be collected and stored in the
master parquet, they just don't make it into the daily export delivered to
the Data Management team.

Exclusion reasons:
  - "non_csc_building"  : building code parsed and is not "CSC"
  - "unparseable_name"  : SensorName too short or otherwise can't yield a code
"""

from typing import Tuple

import polars

from modules.weather_enrichment import extract_building_code


CSC_BUILDING_CODE = "CSC"


def split_csc_rows(
    df: polars.DataFrame,
) -> Tuple[polars.DataFrame, polars.DataFrame]:
    """
    Split the input DataFrame into (included, excluded).

    `included` rows have parsed building code == "CSC" and are otherwise
    unchanged from the input (no added columns).

    `excluded` rows are everything else, with two added columns:
      - parsed_building_code : str | None
      - excluded_reason      : "non_csc_building" or "unparseable_name"

    Parameters
    ----------
    df : polars.DataFrame
        Must contain a "SensorName" column.

    Returns
    -------
    (included, excluded) : tuple of polars.DataFrame
    """
    if "SensorName" not in df.columns:
        raise ValueError("Input DataFrame is missing required column 'SensorName'")

    annotated = df.with_columns(
        polars.col("SensorName")
        .map_elements(extract_building_code, return_dtype=polars.Utf8)
        .alias("parsed_building_code")
    )

    is_csc = polars.col("parsed_building_code") == CSC_BUILDING_CODE

    included = (
        annotated
        .filter(is_csc)
        .drop("parsed_building_code")
    )

    excluded = (
        annotated
        .filter(~is_csc.fill_null(False))  # not CSC, treats NULL as not-CSC
        .with_columns(
            polars.when(polars.col("parsed_building_code").is_null())
            .then(polars.lit("unparseable_name"))
            .otherwise(polars.lit("non_csc_building"))
            .alias("excluded_reason")
        )
    )

    return included, excluded


def summarize_excluded_by_sensor(excluded: polars.DataFrame) -> polars.DataFrame:
    """
    Reduce a row-level excluded DataFrame to one row per unique sensor, with
    review-friendly columns. This is what gets handed to the unit reviewer.

    Output columns:
      - SensorName
      - Source
      - parsed_building_code
      - excluded_reason
      - DeviceName
      - row_count
      - first_seen_utc
      - last_seen_utc
      - sample_temp_f
      - sample_humidity_pct
      - sample_lux
    """
    if excluded.height == 0:
        return polars.DataFrame(schema={
            "SensorName": polars.Utf8,
            "Source": polars.Utf8,
            "parsed_building_code": polars.Utf8,
            "excluded_reason": polars.Utf8,
            "DeviceName": polars.Utf8,
            "row_count": polars.UInt32,
            "first_seen_utc": polars.Datetime,
            "last_seen_utc": polars.Datetime,
            "sample_temp_f": polars.Float64,
            "sample_humidity_pct": polars.Float64,
            "sample_lux": polars.Float64,
        })

    # Convert epoch-seconds timestamp to datetime for summary readability.
    df = excluded
    if df.schema.get("SensorReadingUTC") == polars.Int64:
        df = df.with_columns(
            polars.from_epoch(polars.col("SensorReadingUTC"), time_unit="s")
            .alias("_ts")
        )
    else:
        df = df.with_columns(polars.col("SensorReadingUTC").alias("_ts"))

    summary = (
        df.group_by(["SensorName", "Source", "parsed_building_code", "excluded_reason"])
        .agg([
            polars.col("DeviceName").first().alias("DeviceName"),
            polars.len().alias("row_count"),
            polars.col("_ts").min().alias("first_seen_utc"),
            polars.col("_ts").max().alias("last_seen_utc"),
            polars.col("SensorReadingF").drop_nulls().first().alias("sample_temp_f"),
            polars.col("SensorReadingRh").drop_nulls().first().alias("sample_humidity_pct"),
            polars.col("SensorReadingLux").drop_nulls().first().alias("sample_lux"),
        ])
        .sort(["excluded_reason", "parsed_building_code", "SensorName"])
    )

    return summary
