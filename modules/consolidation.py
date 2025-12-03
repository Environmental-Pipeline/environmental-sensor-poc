"""
Consolidation module for exporting data to Excel and other formats.

Test with `pytest tests/test_EnvironmentData.py`
"""

import shutil
import datetime
import polars
import openpyxl
import logging
from typing import Dict, List, Callable

from modules import validation


def relocate(data: polars.DataFrame, columns: list) -> polars.DataFrame:
    """
    Helper function for relocating columns to the front of a DataFrame.

    Parameters
    ----------
    data : polars.DataFrame
        DataFrame to relocate columns in.

    columns : list[str]
        Columns to move to the front.

    Returns
    -------
    polars.DataFrame : DataFrame with columns relocated.
    """
    columns = [x for x in columns if x in data.columns]
    data = data[columns + [x for x in data.columns if x not in columns]]
    return data


def update_lookups(
    data_path: str,
    acceptable_range: Dict[str, List],
    logger: logging.Logger,
    error_callback: Callable[[str, bool], None] = None,
) -> None:
    """
    Build (or re-build) the lookup tables used for analytical queries using the consolidated parquet files: sensors, devices, and utcs.
    
    Parameters
    ----------
    data_path : str
        Path to the data directory containing parquet files.
    acceptable_range : Dict[str, List]
        Dictionary of reading types to acceptable ranges.
    logger : logging.Logger
        Logger instance for logging messages.
    error_callback : Callable[[str, bool], None], optional
        Callback function for error handling.
    """

    # We need some manual fixes to reformat invalid names.
    name_overrides = {
        "980D Unnamed Temp Sensor": "Temp Unnamed Unnamed_980D",
        "980D Unnamed Humid Sensor": "RH Unnamed Unnamed_980D",
    }

    building_name_map = {
        "ESC": "Environmental Science Center",
        "YPM": "Yale Peabody Museum",
        "KGL": "Kline Geology Laboratory",
        "CSC": "Collection Studies Center (West Campus)",
    }

    # Sensor Info - only include sensor metadata columns (not reading-specific columns)
    # This ensures we get truly unique sensors, not one row per reading
    columns_to_read = [
        "SensorName",
        "SensorID",
        "DeviceID",
        "DeviceName",
        "SensorType",
        "Source",
    ]
        
    sensors_data = polars.read_parquet(
        f"{data_path}/sensor_readings.parquet",
        columns=columns_to_read,
    )
    sensors_data = sensors_data.unique().to_dicts()
    sensors = []
    for sensor in sensors_data:

        sensorname = sensor["SensorName"]
        # Skip processing if SensorName is None/null
        if sensorname is None:
            continue
            
        if sensorname in name_overrides:
            sensorname = name_overrides[sensorname]

        # Fix sensor names with problematic prefixes by removing everything up to and including the first space
        if "__" in sensorname and " " in sensorname:
            sensorname = sensorname[sensorname.index(" ") + 1:]

        # Fix sensor names with " - no comm" suffix
        if " - no comm" in sensorname:
            sensorname = sensorname.replace(" - no comm", "")

        if "floator" in sensorname.lower():
            info = sensorname.strip().split("_")
        else:
            info = sensorname.strip().split(" ")
            info = info[0:-1] + info[-1].split("_")
        
        # Clean up empty strings from consecutive separators (e.g., multiple underscores)
        info = [part for part in info if part.strip() != ""]

        # If the cardinal direction is included, there will be 4 pieces of info.
        if len(info) == 5:
            sensors.append(
                {
                    "Source": sensor["Source"],
                    "SensorName": sensorname,
                    "DeviceSerialFromName": info[4],
                    "SensorType": sensor["SensorType"],
                    "SensorID": sensor["SensorID"],
                    "DeviceID": sensor["DeviceID"],
                    "DeviceName": sensor["DeviceName"],
                    "BuildingID": info[1],
                    "Building": (
                        building_name_map[info[1]]
                        if info[1] in building_name_map
                        else "Unknown"
                    ),
                    "Room": info[2].replace("_", ""),
                    "CardinalDirection": info[3],
                }
            )
            if info[1] not in building_name_map:
                logger.warning(
                    f"Building ID {info[1]} not found in building_name_map for sensor {sensorname}"
                )

        elif len(info) == 4:

            sensors.append(
                {
                    "Source": sensor["Source"],
                    "SensorName": sensorname,
                    "DeviceSerialFromName": info[3],
                    "SensorType": sensor["SensorType"],
                    "SensorID": sensor["SensorID"],
                    "DeviceID": sensor["DeviceID"],
                    "DeviceName": sensor["DeviceName"],
                    "BuildingID": info[1],
                    "Building": (
                        building_name_map[info[1]]
                        if info[1] in building_name_map
                        else "Unknown"
                    ),
                    "Room": info[2].replace("_", ""),
                    "CardinalDirection": "Not Indicated",
                }
            )
            if info[1] not in building_name_map:
                logger.warning(
                    f"Building ID {info[1]} not found in building_name_map for sensor {sensorname}"
                )

        # Floaters are len 3.
        elif len(info) == 3:
            sensors.append(
                {
                    "Source": sensor["Source"],
                    "SensorName": sensorname,
                    "DeviceSerialFromName": info[2],
                    "SensorType": sensor["SensorType"],
                    "SensorID": sensor["SensorID"],
                    "DeviceID": sensor["DeviceID"],
                    "DeviceName": sensor["DeviceName"],
                    "BuildingID": "FLOATER",
                    "Building": (
                        building_name_map[info[1]]
                        if info[1] in building_name_map
                        else "Unknown"
                    ),
                    "Room": "FLOATER",
                    "CardinalDirection": None,
                }
            )
        else:
            # Handle malformed sensor names gracefully with NAs
            logger.warning(
                f"Malformed SensorName format: {sensorname}. Info: {info}. "
                f"Adding with NA values for parsed fields. "
                f"Valid formats: "
                f"'Temp ESC Room101 North_1234' (5 parts with cardinal direction), "
                f"'RH YPM Gallery_2567' (4 parts without cardinal direction), "
                f"'Temp_Floater ESC_3890' (3 parts for floaters)"
            )
            sensors.append(
                {
                    "Source": sensor["Source"],
                    "SensorName": sensorname,
                    "DeviceSerialFromName": None,
                    "SensorType": sensor["SensorType"],
                    "SensorID": sensor["SensorID"], 
                    "DeviceID": sensor["DeviceID"],
                    "DeviceName": sensor["DeviceName"],
                    "BuildingID": None,
                    "Building": None,
                    "Room": None, 
                    "CardinalDirection": None,
                }
            )

    # Write the table to a file.
    sensors = polars.DataFrame(sensors, infer_schema_length=None).unique()
    sensors = sensors.sort(
        ["BuildingID", "Building", "Room", "DeviceSerialFromName", "SensorName"]
    )
    sensors = validation.clean_validate_sensors(
        sensors=sensors, 
        acceptable_range=acceptable_range,
        logger=logger,
        step="update_lookups",
        error_callback=error_callback
    )
    sensors = relocate(
        sensors,
        ["Source", "SensorID", "SensorName", "SensorType", "DeviceID", "DeviceSerialFromName", "BuildingID", "Building", "Room", "CardinalDirection"],
    )
    sensors.write_parquet(f"{data_path}/sensors.parquet")

    # It will be helpful to have the Building, Room, and CardinalDirection appended to Devices.
    # Check for a valid mapping.
    device_info_from_sensors = sensors.select(
        [
            "DeviceID",
            "BuildingID",
            "Building",
            "Room",
            "CardinalDirection",
            "DeviceSerialFromName",
        ]
    ).unique()

    # Check for inconsistent DeviceID mappings (same DeviceID with different Building/Room/CardinalDirection)
    # Group by DeviceID and check if there are multiple unique combinations of Building/Room/CardinalDirection
    device_consistency_check = device_info_from_sensors.group_by("DeviceID").agg([
        polars.col("BuildingID").n_unique().alias("unique_buildings"),
        polars.col("Room").n_unique().alias("unique_rooms"), 
        polars.col("CardinalDirection").n_unique().alias("unique_directions")
    ])
    
    inconsistent_devices = device_consistency_check.filter(
        (polars.col("unique_buildings") > 1) |
        (polars.col("unique_rooms") > 1) |
        (polars.col("unique_directions") > 1)
    )

    if inconsistent_devices.shape[0] > 0:
        # Get the actual inconsistent mappings for logging
        bad_device_ids = inconsistent_devices.select("DeviceID").to_series().to_list()
        bad_values = device_info_from_sensors.filter(
            polars.col("DeviceID").is_in(bad_device_ids)
        ).sort("DeviceID")
        
        logger.warning(
            f"DeviceID has inconsistent Building/Room/CardinalDirection mappings. Using first occurrence: \n{bad_values}"
        )
        
        # Keep only the first occurrence of each DeviceID to maintain consistency
        device_info_from_sensors = device_info_from_sensors.group_by("DeviceID").first()
    else:
        # If all mappings are consistent, we can still have multiple rows per DeviceID (for different sensor types)
        # Just keep one representative row per DeviceID since Building/Room/CardinalDirection should be the same
        device_info_from_sensors = device_info_from_sensors.group_by("DeviceID").first()

    # Device Info.
    devices = polars.read_parquet(
        f"{data_path}/sensor_readings.parquet",
        columns=["Source", "DeviceID", "DeviceName"],
    ).unique()
    devices = devices.join(
        device_info_from_sensors, how="left", on="DeviceID"
    ).sort(["BuildingID", "Room", "DeviceName"])
    
    # Get sensor information for each device from the sensors lookup table
    # Use unique() to avoid duplicates before joining into comma-separated lists
    device_sensors_lookup = sensors.group_by("DeviceID").agg([
        polars.col("SensorID").unique().sort().str.join(", ").alias("SensorIDs"),
        polars.col("SensorName").unique().sort().str.join(", ").alias("SensorNames"),
        polars.col("SensorType").unique().sort().str.join(", ").alias("SensorTypes")
    ])
    
    # Join sensor information to devices
    devices = devices.join(device_sensors_lookup, how="left", on="DeviceID")
    
    devices = relocate(
        devices,
        ["Source", "DeviceID", "DeviceName", "SensorIDs", "SensorNames", "SensorTypes", "DeviceSerialFromName", "BuildingID", "Building", "Room", "CardinalDirection"],
    )
    devices.write_parquet(f"{data_path}/devices.parquet")

    # UTC info.
    # Get all UTC timestamps from sensor_readings: both SensorReadingUTC and QueryUTC
    # sensor_readings is the primary source since all other data derives from it
    
    # Read both SensorReadingUTC and QueryUTC columns
    utc_data = polars.read_parquet(
        f"{data_path}/sensor_readings.parquet", 
        columns=["SensorReadingUTC", "QueryUTC"]
    )
    
    # Get SensorReadingUTC values (remove nulls)
    sensor_reading_utcs = (
        utc_data.filter(polars.col("SensorReadingUTC").is_not_null())
        ["SensorReadingUTC"]
        .unique()
        .to_list()
    )
    
    # Get QueryUTC values (remove nulls) 
    query_utcs = (
        utc_data.filter(polars.col("QueryUTC").is_not_null())
        ["QueryUTC"]
        .unique()
        .to_list()
    )
    
    # Combine all UTC timestamps and get unique values
    utc_timestamps = list(set(sensor_reading_utcs + query_utcs))

    utcs = polars.DataFrame(
        {
            "UTC": utc_timestamps,
            "datetime_utc": [
                datetime.datetime.fromtimestamp(x) for x in utc_timestamps
            ],
        }
    )

    # Convert to EST and round to seconds.
    utcs = utcs.with_columns(
        polars.col("datetime_utc")
        .dt.convert_time_zone("America/New_York")
        .dt.round("1s")
        .alias("datetime_est")
    )

    # Extract all the date parts.
    utcs = utcs.with_columns(
        polars.col("datetime_est").dt.date().alias("date"),
        polars.col("datetime_est").dt.time().alias("time"),
        polars.col("datetime_est").dt.year().alias("year"),
        polars.col("datetime_est").dt.month().alias("month"),
        (polars.col("datetime_est").dt.strftime("%A")).alias("day_of_week"),
        polars.col("datetime_est")
        .dt.weekday()
        .alias("day_of_week_monday1_sunday7"),
        polars.col("datetime_est").dt.hour().alias("hour_24"),
        (polars.col("datetime_est").dt.hour() % 12).alias("hour_12"),
        polars.col("datetime_est").dt.strftime("%p").alias("am_pm"),
    )

    # Write the table to a file.
    utcs.write_parquet(f"{data_path}/utcs.parquet")


def export_to_excel(data_path: str, home_directory: str, logger) -> None:
    """
    Export consolidated data to Excel using the template at templates/consolidated-data-template.xlsx.
    Fills in sheets: sensors, devices, device_readings_daily, and device_readings_last1000.
    Saves the result to data/consolidated-data.xlsx.

    Parameters
    ----------
    data_path : str
        Path to the data directory containing parquet files.
    home_directory : str
        Base directory for finding the template file.
    logger : logging.Logger
        Logger instance for logging messages.
    """
    template_path = f"{home_directory}/templates/consolidated-data-template.xlsx"
    output_path = f"{data_path}/consolidated-data.xlsx"
    
    # Copy template to output location
    shutil.copy(template_path, output_path)
    
    # Load the workbook
    wb = openpyxl.load_workbook(output_path)
    
    # Load the data
    sensors = polars.read_parquet(f"{data_path}/sensors.parquet")
    devices = polars.read_parquet(f"{data_path}/devices.parquet")
    utcs = polars.read_parquet(f"{data_path}/utcs.parquet")
    device_readings_daily = polars.read_parquet(f"{data_path}/device_readings_daily.parquet")
    device_readings = polars.read_parquet(f"{data_path}/device_readings.parquet")
    
    # Get last 1000 device readings (most recent) and join with utcs for datetime_est
    device_readings_last1000 = (
        device_readings.sort("SensorReadingUTC", descending=True).head(1000)
        .join(utcs.select(["UTC", "datetime_est"]), left_on="SensorReadingUTC", right_on="UTC", how="left")
        .rename({"datetime_est": "reading_datetime_est"})
        .join(utcs.select(["UTC", "datetime_est"]), left_on="QueryUTC", right_on="UTC", how="left")
        .rename({"datetime_est": "query_datetime_est"})
    )
    
    # Map sheet names to data
    sheet_data = {
        "sensors": sensors,
        "devices": devices,
        "device_readings_daily": device_readings_daily,
        "device_readings_last1000": device_readings_last1000,
    }
    
    for sheet_name, df in sheet_data.items():
        if sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            
            # Write column headers in row 1
            for col_idx, col_name in enumerate(df.columns, start=1):
                ws.cell(row=1, column=col_idx, value=col_name)
            
            # Write data starting from row 2
            for row_idx, row in enumerate(df.iter_rows(named=True), start=2):
                for col_idx, col_name in enumerate(df.columns, start=1):
                    value = row[col_name]
                    # Convert polars types to Python native types for Excel
                    if value is not None:
                        if hasattr(value, 'item'):
                            value = value.item()
                        # Strip timezone info from datetime values (Excel doesn't support timezones)
                        if hasattr(value, 'tzinfo') and value.tzinfo is not None:
                            value = value.replace(tzinfo=None)
                    ws.cell(row=row_idx, column=col_idx, value=value)
    
    # Save the workbook
    wb.save(output_path)
    wb.close()
    
    logger.info(f"Exported data to {output_path}")


def build_devices(
    data: polars.DataFrame,
    acceptable_range: Dict[str, List],
    error_callback: Callable[[str, bool], None] = None,
) -> polars.DataFrame:
    """
    Reformat the sensor data to create a DataFrame of devices.
    Each Device can have multiple sensors.
    In some cases it is easier to work with data indexed by Device with multiple types of readings (Temperature, Humidity)
    in the same row instead of Sensors which only have one type of reading per row.

    Parameters
    ----------
    data : polars.DataFrame
        DataFrame containing the sensor data.
    acceptable_range : Dict[str, List]
        Dictionary of acceptable ranges for each sensor type.
    error_callback : Callable[[str, bool], None], optional
        Callback function for error handling. Takes error message and raise_exception boolean.

    Returns
    -------
    polars.DataFrame: DataFrame containing the devices data.
    """

    # Get the data for each of the selected sensors.
    devices = None
    data = data.filter(polars.col("DeviceID").is_null().not_())
    for reading in acceptable_range:
        idt = data.filter(polars.col(reading).is_null().not_()).select(
            ["Source", "DeviceID", "SensorReadingUTC", "QueryUTC", "Historical", reading]
        )
        if isinstance(devices, polars.DataFrame):
            devices = devices.join(
                idt,
                how="full",
                on=["Source", "DeviceID", "SensorReadingUTC", "QueryUTC"],
            )
        else:
            devices = idt
        del idt, reading

    # this will result in columns like DeviceID_right when there is not a perfect match.
    # coalesce to a single column.
    cols_Source = [x for x in devices.columns if "Source" in x]
    cols_DeviceID = [x for x in devices.columns if "DeviceID" in x]
    cols_SensorReadingUTC = [x for x in devices.columns if "SensorReadingUTC" in x]
    cols_QueryUTC = [x for x in devices.columns if "QueryUTC" in x]
    cols_Historical = [x for x in devices.columns if "Historical" in x]

    devices = devices.with_columns(
        polars.coalesce(cols_Source).alias("Source")
    )
    devices = devices.with_columns(
        polars.coalesce(cols_DeviceID).alias("DeviceID")
    )
    devices = devices.with_columns(
        polars.coalesce(cols_SensorReadingUTC).alias("SensorReadingUTC")
    )
    devices = devices.with_columns(polars.coalesce(cols_QueryUTC).alias("QueryUTC"))
    devices = devices.with_columns(polars.coalesce(cols_Historical).alias("Historical"))

    devices = devices.drop(
        [
            x
            for x in cols_Source + cols_DeviceID + cols_SensorReadingUTC + cols_QueryUTC + cols_Historical
            if x not in ["Source", "DeviceID", "SensorReadingUTC", "QueryUTC", "Historical"]
        ]
    )

    # If a device has multiple names, error out:
    device_names = (
        data.filter(polars.col("DeviceID").is_null().not_())
        .select(["DeviceID", "DeviceName"])
        .unique()
    )
    if device_names.shape[0] != device_names["DeviceName"].unique().shape[0]:
        if error_callback:
            error_callback(
                "DeviceName to DeviceID is not a 1-1 mapping.",
                True,
            )
        else:
            raise ValueError("DeviceName to DeviceID is not a 1-1 mapping.")

    # Attach the device name.
    devices = devices.join(device_names, how="left", on="DeviceID")

    # Get sensor information for each device
    device_sensors = (
        data.filter(polars.col("DeviceID").is_null().not_())
        .select(["DeviceID", "SensorID", "SensorName", "SensorType"])
        .unique()
        .group_by("DeviceID")
        .agg([
            polars.col("SensorID").str.join(", ").alias("Sensors"),
            polars.col("SensorName").str.join(", ").alias("SensorNames"), 
            polars.col("SensorType").str.join(", ").alias("SensorTypes")
        ])
    )
    
    # Join sensor information to devices
    devices = devices.join(device_sensors, how="left", on="DeviceID")

    # Rearrange columns.
    devices = devices.select(
        ["Source", "DeviceID", "DeviceName", "Sensors", "SensorNames", "SensorTypes", "SensorReadingUTC", "QueryUTC", "Historical"]
        + list(acceptable_range.keys())
    )

    # Return the data.
    return devices

def update_cubes(
    data_path: str,
    acceptable_range: Dict[str, List],
    logger: logging.Logger,
) -> None:
    """
    Build (or re-build) the cubed tables using the consolidated parquet files.
    Cubes contain aggregated measures by day and device/sensor to facilitate faster queries on smaller files.
    In the future, cubes can be modified to include different aggregation levels and dimensions.

    Parameters
    ----------
    data_path : str
        Path to the data directory containing parquet files.
    acceptable_range : Dict[str, List]
        Dictionary of acceptable ranges for each sensor type.
    logger : logging.Logger
        Logger instance for logging messages.
    """

    sumcols = list(acceptable_range.keys())
    utcs = polars.read_parquet(f"{data_path}/utcs.parquet")

    sensor_readings = polars.read_parquet(
        f"{data_path}/sensor_readings.parquet"
    )
    sensor_readings = sensor_readings.filter(
        polars.col("QueryUTC").is_null() | 
        ((polars.col("SensorReadingUTC") - polars.col("QueryUTC")).abs() < 60 * 5)
    )  # include historical data (QueryUTC=null) and recent readings within 5min window
    sensor_readings = sensor_readings.join(
        utcs[["UTC", "date"]],
        how="left",
        left_on="SensorReadingUTC",
        right_on="UTC",
    )

    sensor_readings_daily = (
        sensor_readings.group_by(["Source", "date", "SensorID"])
        .agg(
            [
                polars.len().alias("row_count"),
                polars.col(sumcols).sum().name.suffix("_sum"),
                polars.col(sumcols).min().name.suffix("_min"),
                polars.col(sumcols).max().name.suffix("_max"),
            ]
        )
        .sort(["Source", "date", "SensorID"])
    )

    sensor_readings_daily.write_parquet(
        f"{data_path}/sensor_readings_daily.parquet"
    )

    device_readings = polars.read_parquet(
        f"{data_path}/device_readings.parquet"
    )
    device_readings = device_readings.filter(
        polars.col("QueryUTC").is_null() | 
        ((polars.col("SensorReadingUTC") - polars.col("QueryUTC")).abs() < 60 * 5)
    )  # include historical data (QueryUTC=null) and recent readings within 5min window
    device_readings = device_readings.join(
        utcs[["UTC", "date"]],
        how="left",
        left_on="SensorReadingUTC",
        right_on="UTC",
    )

    device_readings_daily = (
        device_readings.group_by(["Source", "date", "DeviceID"])
        .agg(
            [
                polars.len().alias("row_count"),
                polars.col(sumcols).sum().name.suffix("_sum"),
                polars.col(sumcols).min().name.suffix("_min"),
                polars.col(sumcols).max().name.suffix("_max"),
            ]
        )
        .sort(["Source", "date", "DeviceID"])
    )

    device_readings_daily.write_parquet(
        f"{data_path}/device_readings_daily.parquet"
    )

    logger.info("Updated cubes: sensor_readings_daily and device_readings_daily")
