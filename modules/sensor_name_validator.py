"""
# Sensor Name Validator Module

This module provides validation functions for Yale sensor naming convention.
Sensor names must be exactly 20 characters and follow the format:

Position: 1----5----0----5----0
Format:   CBBBBBFFRRRRRSSPCCCF

| Position | Length | Content | Valid Values |
|----------|--------|---------|--------------|
| 1 | 1 | Collection Unit | L, P, B, A, G, I, S |
| 2-6 | 5 | Building Code | Left-aligned, padded with '_' |
| 7-8 | 2 | Floor | 01, 02, LL, RF, B1, B2, etc. |
| 9-13 | 5 | Room Number | Alphanumeric, 5 chars |
| 14-15 | 2 | Room Section | N, S, E, W, NE, NW, SE, SW, CT, etc. or __ |
| 16 | 1 | Position | Any letter/number or _ |
| 17-19 | 3 | Shelf/Cabinet | 3 chars or ___ |
| 20 | 1 | Floater Flag | F (floater) or _ (fixed) |

## Commands:
- Test with `pytest tests/test_sensor_name_validator.py`

## Functions:
- is_valid_sensor_name: Validate a single sensor name
- filter_invalid_sensors: Filter a DataFrame to remove invalid sensor names
- generate_rejected_sensors_report: Create a CSV report of rejected sensors
"""

import datetime
import polars
import logging
from typing import Tuple, List, Optional


# Valid collection units (first character)
VALID_COLLECTION_UNITS = {'L', 'P', 'B', 'A', 'G', 'I', 'S'}

# Known building codes (positions 2-6, without padding)
KNOWN_BUILDINGS = {'YPM', 'KGL', 'ESC', 'CSC', 'BRBL', 'BARCH', 'YCBA', 'FAST'}

# Valid room sections (positions 14-15) - must be exactly 2 characters
VALID_SECTIONS = {
    'N_', 'S_', 'E_', 'W_',  # Cardinal directions (single char, right-padded)
    'NE', 'NW', 'SE', 'SW',  # Diagonal directions
    'CE', 'CW', 'CT', 'NC', 'SC', 'EC', 'WC',  # Center-based
    '__',  # None specified
}

# Generate numeric bays 01-28
for i in range(1, 29):
    VALID_SECTIONS.add(f'{i:02d}')

# Generate letter bays _A through _G
for letter in 'ABCDEFG':
    VALID_SECTIONS.add(f'_{letter}')

# Generate directional bays N1-N8, S1-S8
for direction in ['N', 'S']:
    for i in range(1, 9):
        VALID_SECTIONS.add(f'{direction}{i}')


def is_valid_sensor_name(name: str) -> Tuple[bool, List[str]]:
    """
    Validate a sensor name against the Yale naming convention.

    The name must be exactly 20 characters following the format:
    CBBBBBFFRRRRRSSPCCCF

    Args:
        name: The sensor name to validate

    Returns:
        Tuple of (is_valid, list_of_errors)
        - is_valid: True if the name conforms to convention
        - list_of_errors: List of validation error messages (empty if valid)
    """
    errors = []

    # Handle None or non-string input
    if name is None:
        return False, ["Sensor name is None"]

    if not isinstance(name, str):
        return False, [f"Sensor name is not a string: {type(name).__name__}"]

    # Check length - must be exactly 20 characters
    if len(name) != 20:
        errors.append(f"Length is {len(name)}, must be exactly 20 characters")
        # Cannot validate structure if wrong length
        return False, errors

    # Position 1: Collection Unit
    collection_unit = name[0]
    if collection_unit not in VALID_COLLECTION_UNITS:
        errors.append(f"Invalid collection unit '{collection_unit}' at position 1. Valid: {', '.join(sorted(VALID_COLLECTION_UNITS))}")

    # Positions 2-6: Building Code (left-aligned, padded with '_')
    building_code_raw = name[1:6]
    building_code = building_code_raw.rstrip('_')
    if not building_code:
        errors.append("Building code at positions 2-6 is empty (all underscores)")
    elif building_code not in KNOWN_BUILDINGS:
        # This is a warning, not a hard failure - building might be valid but unknown
        # For now, we'll allow it as long as it's alphanumeric and left-padded correctly
        if not building_code.isalnum():
            errors.append(f"Building code '{building_code}' contains invalid characters")
        # Check that building code is left-aligned (no leading underscores after first char)
        if building_code_raw.lstrip('_') != building_code_raw.rstrip('_').lstrip('_'):
            # This means there are underscores before the building code starts
            pass  # We'll be lenient here - just check format

    # Positions 7-8: Floor
    floor = name[6:8]
    valid_floor_patterns = [
        # Numeric floors (01-99)
        floor.isdigit() and len(floor) == 2,
        # Special floors
        floor in {'LL', 'RF', 'B1', 'B2', 'B3', 'B4', 'G1', 'G2', 'M1', 'M2', 'LG', 'UG', 'AT', 'BM'},
    ]
    if not any(valid_floor_patterns):
        errors.append(f"Invalid floor '{floor}' at positions 7-8. Expected numeric (01-99) or special (LL, RF, B1, B2, etc.)")

    # Positions 9-13: Room Number (5 alphanumeric characters)
    room_number = name[8:13]
    if not all(c.isalnum() or c == '_' for c in room_number):
        errors.append(f"Room number '{room_number}' at positions 9-13 contains invalid characters")

    # Positions 14-15: Room Section
    room_section = name[13:15]
    if room_section not in VALID_SECTIONS:
        errors.append(f"Invalid room section '{room_section}' at positions 14-15. Valid: N, S, E, W, NE, NW, SE, SW, CT, __, etc.")

    # Position 16: Position indicator (any alphanumeric or _)
    position = name[15]
    if not (position.isalnum() or position == '_'):
        errors.append(f"Invalid position indicator '{position}' at position 16")

    # Positions 17-19: Shelf/Cabinet (3 characters)
    shelf = name[16:19]
    if not all(c.isalnum() or c == '_' for c in shelf):
        errors.append(f"Shelf/cabinet '{shelf}' at positions 17-19 contains invalid characters")

    # Position 20: Floater Flag
    floater_flag = name[19]
    if floater_flag not in {'F', '_'}:
        errors.append(f"Invalid floater flag '{floater_flag}' at position 20. Must be 'F' or '_'")

    return len(errors) == 0, errors


def _get_validation_name(source: str, sensor_name: Optional[str], device_name: Optional[str]) -> Optional[str]:
    """
    Extract the name to validate based on sensor source.

    - Conserv: Use DeviceName (exactly 20 chars, Yale convention)
    - Coris: Use first 20 characters of SensorName
    - LI-COR: Returns None (excluded from validation)

    Args:
        source: The sensor source ('Conserv', 'Coris', 'LI-COR')
        sensor_name: Value from SensorName column
        device_name: Value from DeviceName column

    Returns:
        The name string to validate, or None if source should be excluded
    """
    if source == "Conserv":
        return device_name
    elif source == "Coris":
        if sensor_name and isinstance(sensor_name, str) and len(sensor_name) >= 20:
            return sensor_name[:20]
        return sensor_name
    else:
        # LI-COR and any unknown sources are excluded
        return None


def filter_invalid_sensors(
    df: polars.DataFrame,
    logger: Optional[logging.Logger] = None
) -> Tuple[polars.DataFrame, polars.DataFrame]:
    """
    Filter a DataFrame to separate valid and invalid sensor names.

    Validation logic per source:
    - Conserv: Validate DeviceName (exactly 20 chars)
    - Coris: Validate first 20 chars of SensorName
    - LI-COR: Exclude entirely (treat as invalid)

    Args:
        df: DataFrame containing sensor data
        logger: Optional logger for status messages

    Returns:
        Tuple of (valid_df, invalid_df)
        - valid_df: DataFrame with only valid sensor names
        - invalid_df: DataFrame with invalid sensor names (includes LI-COR)
    """
    if df.is_empty():
        return df, polars.DataFrame()

    if "Source" not in df.columns:
        if logger:
            logger.warning("Column 'Source' not found in DataFrame, cannot validate by source")
        return df, polars.DataFrame()

    valid_frames = []
    invalid_frames = []

    # Process each source separately
    sources = df.select("Source").unique()["Source"].to_list()

    for source in sources:
        source_df = df.filter(polars.col("Source") == source)

        if source == "LI-COR":
            # LI-COR is excluded entirely
            if logger:
                logger.info(f"LI-COR: excluding {source_df.shape[0]} readings (source disabled)")
            invalid_frames.append(source_df)
            continue

        if source == "Conserv":
            if "DeviceName" not in df.columns:
                if logger:
                    logger.warning("Column 'DeviceName' not found - cannot validate Conserv sensors")
                invalid_frames.append(source_df)
                continue
            # Validate DeviceName for Conserv
            unique_pairs = source_df.select("DeviceName").unique()["DeviceName"].to_list()
            valid_names = set()
            invalid_names = set()
            for name in unique_pairs:
                is_valid, _ = is_valid_sensor_name(name)
                if is_valid:
                    valid_names.add(name)
                else:
                    invalid_names.add(name)
            if valid_names:
                valid_frames.append(source_df.filter(polars.col("DeviceName").is_in(list(valid_names))))
            if invalid_names:
                invalid_frames.append(source_df.filter(polars.col("DeviceName").is_in(list(invalid_names))))
            if logger:
                logger.info(f"Conserv: {len(valid_names)} valid, {len(invalid_names)} invalid unique DeviceNames")

        elif source == "Coris":
            if "SensorName" not in df.columns:
                if logger:
                    logger.warning("Column 'SensorName' not found - cannot validate Coris sensors")
                invalid_frames.append(source_df)
                continue
            # Validate first 20 chars of SensorName for Coris
            unique_names = source_df.select("SensorName").unique()["SensorName"].to_list()
            valid_names = set()
            invalid_names = set()
            for name in unique_names:
                name_to_validate = name[:20] if (name and isinstance(name, str) and len(name) >= 20) else name
                is_valid, _ = is_valid_sensor_name(name_to_validate)
                if is_valid:
                    valid_names.add(name)
                else:
                    invalid_names.add(name)
            if valid_names:
                valid_frames.append(source_df.filter(polars.col("SensorName").is_in(list(valid_names))))
            if invalid_names:
                invalid_frames.append(source_df.filter(polars.col("SensorName").is_in(list(invalid_names))))
            if logger:
                logger.info(f"Coris: {len(valid_names)} valid, {len(invalid_names)} invalid unique SensorNames (first 20 chars)")

        else:
            # Unknown source - treat as invalid
            if logger:
                logger.warning(f"Unknown source '{source}': excluding {source_df.shape[0]} readings")
            invalid_frames.append(source_df)

    # Combine frames
    valid_df = polars.concat(valid_frames, how="diagonal") if valid_frames else polars.DataFrame()
    invalid_df = polars.concat(invalid_frames, how="diagonal") if invalid_frames else polars.DataFrame()

    if logger:
        logger.info(f"Sensor name validation total: {valid_df.shape[0]} valid rows, {invalid_df.shape[0]} invalid rows")

    return valid_df, invalid_df


def generate_rejected_sensors_report(
    invalid_df: polars.DataFrame,
    data_path: str,
    logger: Optional[logging.Logger] = None
) -> Optional[str]:
    """
    Generate a CSV report of rejected sensors with validation details.

    The report includes:
    - SensorName: the non-conforming name
    - SensorID: the sensor ID
    - DeviceID: the device ID
    - Source: which API it came from (Coris, Conserv, LI-COR)
    - ValidationErrors: what specifically was wrong with the name
    - SampleCount: how many readings were rejected
    - DateRejected: the date of rejection

    Args:
        invalid_df: DataFrame containing rejected sensor readings
        data_path: Path to save the CSV report
        logger: Optional logger for status messages

    Returns:
        Path to the created CSV file, or None if no rejected sensors
    """
    if invalid_df.is_empty():
        if logger:
            logger.info("No rejected sensors to report")
        return None

    # Get unique sensors with their metadata
    sensor_columns = ["SensorName", "DeviceName", "SensorID", "DeviceID", "Source"]
    available_columns = [col for col in sensor_columns if col in invalid_df.columns]

    if not available_columns:
        if logger:
            logger.warning("Cannot generate rejected sensors report - missing required columns")
        return None

    # Aggregate by unique sensor to get sample counts
    unique_sensors = (
        invalid_df
        .group_by(available_columns)
        .agg(polars.len().alias("SampleCount"))
    )

    # Add validation errors for each sensor, using the correct column per source
    validation_errors_list = []
    for row in unique_sensors.iter_rows(named=True):
        source = row.get("Source", "")
        if source == "LI-COR":
            validation_errors_list.append("LI-COR source excluded from validation")
        elif source == "Conserv":
            device_name = row.get("DeviceName")
            _, errors = is_valid_sensor_name(device_name)
            validation_errors_list.append("; ".join(errors) if errors else "Unknown error")
        elif source == "Coris":
            sensor_name = row.get("SensorName")
            name_to_validate = sensor_name[:20] if (sensor_name and isinstance(sensor_name, str) and len(sensor_name) >= 20) else sensor_name
            _, errors = is_valid_sensor_name(name_to_validate)
            validation_errors_list.append("; ".join(errors) if errors else "Unknown error")
        else:
            validation_errors_list.append(f"Unknown source: {source}")

    unique_sensors = unique_sensors.with_columns(
        polars.Series("ValidationErrors", validation_errors_list)
    )

    # Add rejection date
    today = datetime.date.today().isoformat()
    unique_sensors = unique_sensors.with_columns(
        polars.lit(today).alias("DateRejected")
    )

    # Reorder columns for the report
    report_columns = ["SensorName", "DeviceName", "SensorID", "DeviceID", "Source", "ValidationErrors", "SampleCount", "DateRejected"]
    report_columns = [col for col in report_columns if col in unique_sensors.columns]
    unique_sensors = unique_sensors.select(report_columns)

    # Generate filename with date
    csv_path = f"{data_path}/rejected_sensors_{today}.csv"
    unique_sensors.write_csv(csv_path)

    if logger:
        logger.info(f"Wrote rejected sensors report: {csv_path} ({unique_sensors.shape[0]} sensors, {invalid_df.shape[0]} readings)")

    return csv_path
