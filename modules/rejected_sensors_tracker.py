"""
# Rejected Sensors Tracker Module

Persistent tracking of sensors rejected by the Yale naming convention validator.
Distinguishes between "expected exclusions" (floaters, exhibitions, etc.) and
"needs attention" (fixable issues like wrong length, invalid characters).

Tracking CSV columns:
- Source: Coris or Conserv
- SensorID: Unique sensor identifier
- SensorName: The failing name (DeviceName for Conserv, first 20 chars for Coris)
- Category: expected_exclusion or needs_attention
- SubCategory: Classification detail (floater, wrong_length, etc.)
- Reason: Validation error message
- FirstSeenUTC: Unix timestamp when first detected
- LastSeenUTC: Unix timestamp of most recent detection
- Status: active or fixed
- FixedUTC: Unix timestamp when marked fixed (null if active)

## Commands:
- Test with `pytest tests/test_rejected_sensors_tracker.py`
"""

import os
import time
import polars
import logging
import re
from typing import Tuple, List, Optional

from modules.sensor_name_validator import is_valid_sensor_name, _normalize_name


TRACKING_COLUMNS = [
    "Source",
    "SensorID",
    "SensorName",
    "Category",
    "SubCategory",
    "Reason",
    "FirstSeenUTC",
    "LastSeenUTC",
    "Status",
    "FixedUTC",
]

TRACKING_SCHEMA = {
    "Source": polars.String,
    "SensorID": polars.String,
    "SensorName": polars.String,
    "Category": polars.String,
    "SubCategory": polars.String,
    "Reason": polars.String,
    "FirstSeenUTC": polars.Int64,
    "LastSeenUTC": polars.Int64,
    "Status": polars.String,
    "FixedUTC": polars.Int64,
}

# Conserv ID pattern: c followed by 6 digits (e.g., c007167, c008398)
_CONSERV_ID_PATTERN = re.compile(r"^c\d{6}$")


def classify_invalid_sensor(
    source: str,
    sensor_name: str,
    device_name: str,
    validation_errors: List[str],
) -> Tuple[str, str]:
    """
    Classify an invalid sensor into category and subcategory.

    Args:
        source: The sensor source ('Conserv', 'Coris', etc.)
        sensor_name: The SensorName column value
        device_name: The DeviceName column value
        validation_errors: List of validation error strings from is_valid_sensor_name

    Returns:
        Tuple of (category, subcategory)
    """
    # The "display name" is what we check for content-based patterns.
    # For Conserv, DeviceName is the name validated against the convention.
    # For Coris, the first 20 chars of SensorName are validated.
    name_to_check = device_name if source == "Conserv" else sensor_name
    name_lower = (name_to_check or "").lower()
    device_lower = (device_name or "").lower()

    # --- expected_exclusion checks ---

    # floater: SensorName contains "floater" (case-insensitive)
    sensor_lower = (sensor_name or "").lower()
    if "floater" in sensor_lower:
        return "expected_exclusion", "floater"

    # exhibition: DeviceName starts with *Exh* or Exh (case-insensitive)
    if device_name and (device_name.startswith("*Exh*") or device_name.startswith("Exh")):
        return "expected_exclusion", "exhibition"

    # obsolete: DeviceName starts with lowercase 'o' followed by uppercase letter
    if device_name and len(device_name) >= 2 and device_name[0] == "o" and device_name[1].isupper():
        return "expected_exclusion", "obsolete"

    # unassigned: DeviceName is just a Conserv ID pattern (e.g., c007167)
    if device_name and _CONSERV_ID_PATTERN.match(device_name.strip()):
        return "expected_exclusion", "unassigned"

    # inactive: SensorName contains "inactive" (case-insensitive)
    if "inactive" in sensor_lower:
        return "expected_exclusion", "inactive"

    # placeholder: SensorName contains "unnamed", "replacement", "no data" (case-insensitive)
    for keyword in ["unnamed", "replacement", "no data"]:
        if keyword in sensor_lower:
            return "expected_exclusion", "placeholder"

    # --- needs_attention checks ---
    errors_joined = " ".join(validation_errors).lower()

    # wrong_length: Length is not 20
    if any("length" in e.lower() and "20" in e for e in validation_errors):
        return "needs_attention", "wrong_length"

    # invalid_collection_unit: First character not in valid set
    if any("collection unit" in e.lower() for e in validation_errors):
        return "needs_attention", "invalid_collection_unit"

    # invalid_characters: Contains ?, * or other non-alphanumeric/underscore
    # Check the actual name for problematic characters
    check_name = name_to_check or ""
    has_invalid_chars = bool(re.search(r"[?*]", check_name)) or any(
        "invalid characters" in e.lower() for e in validation_errors
    )
    if has_invalid_chars:
        return "needs_attention", "invalid_characters"

    # leading_trailing_space: Name has leading or trailing whitespace
    if name_to_check and (name_to_check != name_to_check.strip()):
        return "needs_attention", "leading_trailing_space"

    # other: Any other validation failure
    return "needs_attention", "other"


def _get_display_name(source: str, sensor_name: Optional[str], device_name: Optional[str]) -> str:
    """
    Get the display name for tracking purposes.
    - Conserv: DeviceName
    - Coris: first 20 chars of SensorName
    """
    if source == "Conserv":
        return device_name or ""
    elif source == "Coris":
        if sensor_name and isinstance(sensor_name, str):
            return sensor_name[:20]
        return sensor_name or ""
    return sensor_name or ""


def _get_validation_errors(source: str, sensor_name: Optional[str], device_name: Optional[str]) -> List[str]:
    """
    Run validation on the appropriate name and return error list.
    """
    if source == "Conserv":
        _, errors = is_valid_sensor_name(_normalize_name(device_name))
    elif source == "Coris":
        name_to_validate = sensor_name
        if sensor_name and isinstance(sensor_name, str) and len(sensor_name) >= 20:
            name_to_validate = sensor_name[:20]
        _, errors = is_valid_sensor_name(_normalize_name(name_to_validate))
    else:
        errors = [f"Unknown source: {source}"]
    return errors


def _load_tracking_file(tracking_file_path: str) -> polars.DataFrame:
    """Load existing tracking CSV or return empty DataFrame with correct schema."""
    if os.path.exists(tracking_file_path):
        df = polars.read_csv(tracking_file_path)
        # Ensure correct types
        cast_map = {}
        for col, dtype in TRACKING_SCHEMA.items():
            if col in df.columns and df[col].dtype != dtype:
                cast_map[col] = dtype
        if cast_map:
            df = df.cast(cast_map, strict=False)
        return df

    return polars.DataFrame(schema=TRACKING_SCHEMA)


def update_tracking_file(
    invalid_df: polars.DataFrame,
    tracking_file_path: str,
    logger: Optional[logging.Logger] = None,
) -> Tuple[int, int, int]:
    """
    Update the persistent tracking CSV with current invalid sensors.

    Logic:
    1. Load existing tracking file (or create empty if doesn't exist)
    2. Get current invalid sensors from invalid_df
    3. For sensors in tracking file but NOT in current invalid:
       - Mark as Status='fixed', set FixedUTC=now
    4. For sensors in current invalid but NOT in tracking file:
       - Add new row with Status='active', FirstSeenUTC=now, LastSeenUTC=now
    5. For sensors in BOTH:
       - Update LastSeenUTC=now, keep Status='active'
    6. Save updated tracking file

    Args:
        invalid_df: DataFrame of currently invalid sensor readings
        tracking_file_path: Path to the persistent tracking CSV
        logger: Optional logger

    Returns:
        Tuple of (new_count, fixed_count, still_active_count)
    """
    now_utc = int(time.time())
    existing = _load_tracking_file(tracking_file_path)

    # Build current invalid sensor set from invalid_df
    # Group by Source + SensorID to get unique sensors
    if invalid_df.is_empty() or "Source" not in invalid_df.columns or "SensorID" not in invalid_df.columns:
        # No current invalid sensors — mark all existing active as fixed
        if existing.is_empty():
            return 0, 0, 0

        fixed_count = existing.filter(polars.col("Status") == "active").shape[0]
        if fixed_count > 0:
            existing = existing.with_columns(
                polars.when(polars.col("Status") == "active")
                .then(polars.lit("fixed"))
                .otherwise(polars.col("Status"))
                .alias("Status"),
                polars.when(polars.col("Status") == "active")
                .then(polars.lit(now_utc))
                .otherwise(polars.col("FixedUTC"))
                .alias("FixedUTC"),
            )
        existing.write_csv(tracking_file_path)
        if logger:
            logger.info(f"Rejected sensors tracking: 0 new, {fixed_count} fixed, 0 still active")
        return 0, fixed_count, 0

    # Get unique current invalid sensors with metadata
    # We need Source, SensorID, SensorName, DeviceName per unique sensor
    group_cols = ["Source", "SensorID"]
    meta_cols = [c for c in ["SensorName", "DeviceName"] if c in invalid_df.columns]

    current_sensors = (
        invalid_df
        .group_by(group_cols)
        .agg([polars.col(c).first().alias(c) for c in meta_cols])
    )

    # Build classification for each current invalid sensor
    new_rows = []
    for row in current_sensors.iter_rows(named=True):
        source = row["Source"]
        sensor_id = row["SensorID"]
        sensor_name = row.get("SensorName", "")
        device_name = row.get("DeviceName", "")

        display_name = _get_display_name(source, sensor_name, device_name)
        errors = _get_validation_errors(source, sensor_name, device_name)
        category, subcategory = classify_invalid_sensor(source, sensor_name, device_name, errors)
        reason = "; ".join(errors) if errors else "Unknown error"

        new_rows.append({
            "Source": source,
            "SensorID": sensor_id,
            "SensorName": display_name,
            "Category": category,
            "SubCategory": subcategory,
            "Reason": reason,
        })

    current_df = polars.DataFrame(new_rows, schema={
        "Source": polars.String,
        "SensorID": polars.String,
        "SensorName": polars.String,
        "Category": polars.String,
        "SubCategory": polars.String,
        "Reason": polars.String,
    })

    # Create keys for matching: Source + SensorID
    current_keys = set()
    for row in current_df.iter_rows(named=True):
        current_keys.add((row["Source"], row["SensorID"]))

    existing_active_keys = set()
    if not existing.is_empty():
        for row in existing.filter(polars.col("Status") == "active").iter_rows(named=True):
            existing_active_keys.add((row["Source"], row["SensorID"]))

    existing_all_keys = set()
    if not existing.is_empty():
        for row in existing.iter_rows(named=True):
            existing_all_keys.add((row["Source"], row["SensorID"]))

    # Sensors to mark as fixed: in existing active but NOT in current
    keys_to_fix = existing_active_keys - current_keys

    # Sensors that are new: in current but NOT in existing (at all)
    keys_new = current_keys - existing_all_keys

    # Sensors still active: in both current and existing
    keys_still_active = current_keys & existing_all_keys

    # Build updated tracking DataFrame
    result_rows = []

    # Process existing rows
    if not existing.is_empty():
        for row in existing.iter_rows(named=True):
            key = (row["Source"], row["SensorID"])
            if key in keys_to_fix:
                # Mark as fixed
                result_rows.append({
                    **row,
                    "Status": "fixed",
                    "FixedUTC": now_utc,
                })
            elif key in keys_still_active:
                # Update LastSeenUTC, ensure active, update classification
                # Find the current row for updated classification
                current_row = None
                for cr in current_df.iter_rows(named=True):
                    if (cr["Source"], cr["SensorID"]) == key:
                        current_row = cr
                        break
                result_rows.append({
                    **row,
                    "SensorName": current_row["SensorName"] if current_row else row["SensorName"],
                    "Category": current_row["Category"] if current_row else row["Category"],
                    "SubCategory": current_row["SubCategory"] if current_row else row["SubCategory"],
                    "Reason": current_row["Reason"] if current_row else row["Reason"],
                    "LastSeenUTC": now_utc,
                    "Status": "active",
                    "FixedUTC": None,
                })
            else:
                # Already fixed or not relevant — keep as-is
                result_rows.append(row)

    # Add new sensors
    for row in current_df.iter_rows(named=True):
        key = (row["Source"], row["SensorID"])
        if key in keys_new:
            result_rows.append({
                "Source": row["Source"],
                "SensorID": row["SensorID"],
                "SensorName": row["SensorName"],
                "Category": row["Category"],
                "SubCategory": row["SubCategory"],
                "Reason": row["Reason"],
                "FirstSeenUTC": now_utc,
                "LastSeenUTC": now_utc,
                "Status": "active",
                "FixedUTC": None,
            })

    # Build final DataFrame
    if result_rows:
        result_df = polars.DataFrame(result_rows, schema=TRACKING_SCHEMA)
    else:
        result_df = polars.DataFrame(schema=TRACKING_SCHEMA)

    # Ensure directory exists
    tracking_dir = os.path.dirname(tracking_file_path)
    if tracking_dir:
        os.makedirs(tracking_dir, exist_ok=True)

    result_df.write_csv(tracking_file_path)

    new_count = len(keys_new)
    fixed_count = len(keys_to_fix)
    still_active_count = len(keys_still_active) + new_count  # new sensors are also active

    if logger:
        logger.info(
            f"Rejected sensors tracking updated: {new_count} new, "
            f"{fixed_count} fixed, {still_active_count} active"
        )

    return new_count, fixed_count, still_active_count


def generate_needs_attention_report(
    tracking_file_path: str,
    output_path: str,
    logger: Optional[logging.Logger] = None,
) -> Optional[str]:
    """
    Generate a CSV of only active 'needs_attention' sensors for sharing with units.
    Sorted by Source, then SubCategory, then SensorName.

    Args:
        tracking_file_path: Path to the persistent tracking CSV
        output_path: Path to write the needs-attention report CSV
        logger: Optional logger

    Returns:
        Path to generated file, or None if no needs_attention sensors
    """
    if not os.path.exists(tracking_file_path):
        if logger:
            logger.info("No tracking file found, skipping needs-attention report")
        return None

    tracking = _load_tracking_file(tracking_file_path)

    if tracking.is_empty():
        if logger:
            logger.info("Tracking file is empty, skipping needs-attention report")
        return None

    # Filter: active + needs_attention
    needs_attention = tracking.filter(
        (polars.col("Status") == "active") & (polars.col("Category") == "needs_attention")
    )

    if needs_attention.is_empty():
        if logger:
            logger.info("No active needs_attention sensors to report")
        return None

    # Deduplicate by SensorName: sensors with both Temperature and RH have
    # different SensorIDs but the same display name. Keep the most recent LastSeenUTC.
    needs_attention = (
        needs_attention
        .sort("LastSeenUTC", descending=True)
        .unique(subset=["SensorName"], keep="first")
    )

    # Sort by Source, SubCategory, SensorName
    needs_attention = needs_attention.sort(["Source", "SubCategory", "SensorName"])

    # Ensure output directory exists
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    needs_attention.write_csv(output_path)

    if logger:
        logger.info(
            f"Needs-attention report: {needs_attention.shape[0]} sensors written to {output_path}"
        )

    return output_path


def generate_summary(tracking_file_path: str) -> dict:
    """
    Return summary stats from the tracking file.

    Returns dict with:
        - total_tracked: total rows in tracking file
        - total_active: rows with Status='active'
        - total_fixed: rows with Status='fixed'
        - by_category: dict of category -> count (active only)
        - by_subcategory: dict of subcategory -> count (active only)
        - newly_fixed: count of rows fixed (Status='fixed' with FixedUTC not null)
    """
    summary = {
        "total_tracked": 0,
        "total_active": 0,
        "total_fixed": 0,
        "by_category": {},
        "by_subcategory": {},
        "newly_fixed": 0,
    }

    if not os.path.exists(tracking_file_path):
        return summary

    tracking = _load_tracking_file(tracking_file_path)

    if tracking.is_empty():
        return summary

    summary["total_tracked"] = tracking.shape[0]
    summary["total_active"] = tracking.filter(polars.col("Status") == "active").shape[0]
    summary["total_fixed"] = tracking.filter(polars.col("Status") == "fixed").shape[0]

    # By category (active only)
    active = tracking.filter(polars.col("Status") == "active")
    if not active.is_empty():
        cat_counts = active.group_by("Category").agg(polars.len().alias("count"))
        for row in cat_counts.iter_rows(named=True):
            summary["by_category"][row["Category"]] = row["count"]

        sub_counts = active.group_by("SubCategory").agg(polars.len().alias("count"))
        for row in sub_counts.iter_rows(named=True):
            summary["by_subcategory"][row["SubCategory"]] = row["count"]

    # Newly fixed: rows with Status='fixed' and FixedUTC not null
    fixed_with_utc = tracking.filter(
        (polars.col("Status") == "fixed") & (polars.col("FixedUTC").is_not_null())
    )
    summary["newly_fixed"] = fixed_with_utc.shape[0]

    return summary
