"""
Weather Enrichment Module

Enriches sensor readings with outdoor weather data from the Open-Meteo Archive API.
Weather data is joined to sensor readings by building location and hour (floored UTC).

Building codes are extracted from SensorName positions 2-5 (stripping underscores),
then mapped to lat/long via building_coordinates.csv. Multiple buildings sharing
the same coordinates are grouped to minimize API calls.

Fetched weather data is cached locally to avoid redundant API requests.

## Usage:
    from modules import weather_enrichment
    enriched_df = weather_enrichment.enrich_sensors_with_weather(
        sensors=sensor_df,
        coordinates_path="data/building_coordinates.csv",
        cache_dir="/opt/env-sensor-data/weather_cache/"
    )

## Commands:
- Test with `pytest tests/test_weather_enrichment.py -v`
"""

import os
import json
import time
import hashlib
import logging
import datetime
import polars
import requests
from typing import Optional


# Open-Meteo API endpoints
OPEN_METEO_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Number of days before today where Archive API data becomes available.
# Dates more recent than this threshold use the Forecast API instead.
ARCHIVE_AVAILABILITY_DAYS = 5

# Weather parameters to fetch
WEATHER_PARAMS = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "apparent_temperature",
    "pressure_msl",
    "surface_pressure",
    "precipitation",
    "rain",
    "snowfall",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "shortwave_radiation",
    "direct_radiation",
    "weather_code",
]

# Mapping from Open-Meteo parameter names to output column names
WEATHER_COLUMN_MAP = {
    "temperature_2m": "weather_temp_f",
    "relative_humidity_2m": "weather_humidity_pct",
    "dew_point_2m": "weather_dew_point_f",
    "apparent_temperature": "weather_apparent_temp_f",
    "pressure_msl": "weather_pressure_msl_hpa",
    "surface_pressure": "weather_pressure_surface_hpa",
    "precipitation": "weather_precip_mm",
    "rain": "weather_rain_mm",
    "snowfall": "weather_snowfall_cm",
    "cloud_cover": "weather_cloud_cover_pct",
    "wind_speed_10m": "weather_wind_speed_kmh",
    "wind_direction_10m": "weather_wind_direction_deg",
    "shortwave_radiation": "weather_shortwave_rad_wm2",
    "direct_radiation": "weather_direct_rad_wm2",
    "weather_code": "weather_wmo_code",
}

# Columns that need Celsius-to-Fahrenheit conversion: F = (C * 9/5) + 32
CELSIUS_TO_FAHRENHEIT_COLUMNS = [
    "weather_temp_f",
    "weather_dew_point_f",
    "weather_apparent_temp_f",
]

# WMO Weather interpretation codes (WMO 4677)
WMO_CODE_DESCRIPTIONS = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}

def load_building_coordinates(coordinates_path: str) -> polars.DataFrame:

    """
    Load building coordinates from a CSV file.

    Parameters
    ----------
    coordinates_path : str
        Path to the building_coordinates.csv file.

    Returns
    -------
    polars.DataFrame
        DataFrame with columns: building_code, latitude, longitude, building_name
    """
    if not os.path.exists(coordinates_path):
        raise FileNotFoundError(
            f"Building coordinates file not found: {coordinates_path}"
        )

    return polars.read_csv(coordinates_path)


def extract_building_code(sensor_name: str) -> Optional[str]:
    """
    Extract building code from a SensorName string.

    The building code occupies positions 2-5 (1-indexed), with underscores
    used as padding for shorter codes. Strip underscores to get the actual code.

    Parameters
    ----------
    sensor_name : str
        Full sensor name (e.g., "PYPM_15_01234AB001F")

    Returns
    -------
    str or None
        Building code (e.g., "YPM") or None if sensor name is too short.
    """
    if sensor_name is None or len(sensor_name) < 5:
        return None
    # Positions 2-6 (0-indexed: chars at index 1,2,3,4,5)
    raw = sensor_name[1:6]
    return raw.strip("_") or None


def extract_building_codes_column(df: polars.DataFrame) -> polars.DataFrame:
    """
    Add a 'building_code' column to a sensor DataFrame by extracting from SensorName.

    Parameters
    ----------
    df : polars.DataFrame
        Sensor readings DataFrame with a 'SensorName' column.

    Returns
    -------
    polars.DataFrame
        Input DataFrame with an added 'building_code' column.
    """
    if "SensorName" not in df.columns:
        raise ValueError("DataFrame must have a 'SensorName' column")

    return df.with_columns(
        polars.col("SensorName")
        .map_elements(extract_building_code, return_dtype=polars.Utf8)
        .alias("building_code")
    )


def get_unique_locations(
    building_codes: list,
    coordinates: polars.DataFrame,
) -> dict:
    """
    Map building codes to unique lat/long pairs, grouping buildings that share coordinates.

    Parameters
    ----------
    building_codes : list
        List of building codes found in sensor data.
    coordinates : polars.DataFrame
        Building coordinates DataFrame.

    Returns
    -------
    dict
        Keys are (latitude, longitude) tuples, values are lists of building codes
        at that location. Only includes locations that match building_codes.
    """
    locations = {}
    for code in set(building_codes):
        if code is None:
            continue
        match = coordinates.filter(polars.col("building_code") == code)
        if match.is_empty():
            continue
        lat = round(float(match["latitude"][0]), 4)
        lon = round(float(match["longitude"][0]), 4)
        key = (lat, lon)
        if key not in locations:
            locations[key] = []
        locations[key].append(code)
    return locations


def _cache_key(lat: float, lon: float, start_date: str, end_date: str) -> str:
    """Generate a deterministic cache filename for a weather request."""
    raw = f"{lat:.4f}_{lon:.4f}_{start_date}_{end_date}"
    h = hashlib.md5(raw.encode()).hexdigest()[:12]
    return f"weather_{lat:.4f}_{lon:.4f}_{start_date}_{end_date}_{h}.json"


def fetch_weather_data(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    cache_dir: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
    api_url: Optional[str] = None,
    extra_params: Optional[dict] = None,
) -> Optional[polars.DataFrame]:
    """
    Fetch hourly weather data from an Open-Meteo API endpoint for a single location.

    Parameters
    ----------
    latitude : float
        Location latitude.
    longitude : float
        Location longitude.
    start_date : str
        Start date in YYYY-MM-DD format.
    end_date : str
        End date in YYYY-MM-DD format.
    cache_dir : str, optional
        Directory to cache weather responses. If None, no caching.
    logger : logging.Logger, optional
        Logger instance.
    api_url : str, optional
        Open-Meteo API endpoint URL. Defaults to the Archive API.
    extra_params : dict, optional
        Additional query parameters (e.g. past_days, forecast_days for the Forecast API).

    Returns
    -------
    polars.DataFrame or None
        Hourly weather data with columns: time, temperature_2m,
        relative_humidity_2m, precipitation, cloud_cover.
        Returns None on API failure.
    """
    if api_url is None:
        api_url = OPEN_METEO_ARCHIVE_URL

    # Check cache first
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(
            cache_dir, _cache_key(latitude, longitude, start_date, end_date)
        )
        if os.path.exists(cache_file):
            if logger:
                logger.info(
                    f"Weather cache hit: {latitude},{longitude} "
                    f"({start_date} to {end_date})"
                )
            with open(cache_file, "r") as f:
                cached = json.load(f)
            return _parse_weather_response(cached)

    # Make API request
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": start_date,
        "end_date": end_date,
        "hourly": ",".join(WEATHER_PARAMS),
        "timezone": "UTC",
    }
    if extra_params:
        params.update(extra_params)

    if logger:
        logger.info(
            f"Fetching weather: {latitude},{longitude} "
            f"({start_date} to {end_date}) via {api_url}"
        )

    try:
        response = requests.get(api_url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        if logger:
            logger.warning(f"Weather API request failed: {e}")
        return None

    # Validate response structure
    if "hourly" not in data or "time" not in data.get("hourly", {}):
        if logger:
            logger.warning(
                f"Unexpected weather API response for {latitude},{longitude}"
            )
        return None

    # Cache the response
    if cache_dir:
        with open(cache_file, "w") as f:
            json.dump(data, f)
        if logger:
            logger.info(f"Weather data cached: {cache_file}")

    return _parse_weather_response(data)


def fetch_weather_for_range(
    latitude: float,
    longitude: float,
    start_date: str,
    end_date: str,
    cache_dir: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> Optional[polars.DataFrame]:
    """
    Fetch hourly weather data, automatically choosing Archive or Forecast API.

    Dates older than ARCHIVE_AVAILABILITY_DAYS use the Archive API.
    More recent dates use the Forecast API with past_days/forecast_days
    parameters so that recently-collected sensor readings get weather data
    instead of nulls.

    Parameters
    ----------
    latitude, longitude, start_date, end_date, cache_dir, logger
        Same as fetch_weather_data.

    Returns
    -------
    polars.DataFrame or None
        Combined hourly weather data from both APIs, or None on total failure.
    """
    today = datetime.date.today()
    archive_cutoff = today - datetime.timedelta(days=ARCHIVE_AVAILABILITY_DAYS)

    start_dt = datetime.date.fromisoformat(start_date)
    end_dt = datetime.date.fromisoformat(end_date)

    parts = []

    # --- Archive portion: dates strictly before the cutoff ---
    if start_dt <= archive_cutoff:
        archive_end = min(end_dt, archive_cutoff)
        archive_df = fetch_weather_data(
            latitude=latitude,
            longitude=longitude,
            start_date=start_dt.isoformat(),
            end_date=archive_end.isoformat(),
            cache_dir=cache_dir,
            logger=logger,
            api_url=OPEN_METEO_ARCHIVE_URL,
        )
        if archive_df is not None:
            parts.append(archive_df)

    # --- Forecast portion: dates after the cutoff ---
    if end_dt > archive_cutoff:
        forecast_start = max(start_dt, archive_cutoff + datetime.timedelta(days=1))
        forecast_df = fetch_weather_data(
            latitude=latitude,
            longitude=longitude,
            start_date=forecast_start.isoformat(),
            end_date=end_dt.isoformat(),
            cache_dir=cache_dir,
            logger=logger,
            api_url=OPEN_METEO_FORECAST_URL,
            # past_days/forecast_days removed - conflicts with start_date/end_date
        )
        if forecast_df is not None:
            parts.append(forecast_df)

    if not parts:
        return None

    combined = polars.concat(parts)
    # De-duplicate in case archive and forecast overlap at the boundary
    combined = combined.unique(subset=["weather_hour_utc"], keep="first")
    return combined


def _parse_weather_response(data: dict) -> polars.DataFrame:
    """
    Parse Open-Meteo API JSON response into a polars DataFrame.

    Parameters
    ----------
    data : dict
        Raw API response JSON.

    Returns
    -------
    polars.DataFrame
        Hourly weather data.
    """
    hourly = data["hourly"]
    df_dict = {"time": hourly["time"]}
    for param in WEATHER_PARAMS:
        df_dict[param] = hourly.get(param, [None] * len(hourly["time"]))

    df = polars.DataFrame(df_dict)

    # Parse time strings to datetime, then extract UTC hour timestamp
    df = df.with_columns(
        polars.col("time")
        .str.strptime(polars.Datetime, "%Y-%m-%dT%H:%M")
        .alias("weather_datetime")
    )

    # Convert datetime to UTC epoch (seconds) for joining
    df = df.with_columns(
        (polars.col("weather_datetime").dt.epoch("s")).cast(polars.Int64).alias("weather_hour_utc")
    )

    return df


def _floor_to_hour(utc_timestamp: int) -> int:
    """Floor a UTC timestamp (seconds) to the start of its hour."""
    return utc_timestamp - (utc_timestamp % 3600)


def enrich_sensors_with_weather(
    sensors: polars.DataFrame,
    coordinates_path: str,
    cache_dir: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> polars.DataFrame:
    """
    Enrich sensor readings with outdoor weather data.

    Joins weather data to sensor readings by building location and hour
    (SensorReadingUTC floored to the nearest hour).

    Parameters
    ----------
    sensors : polars.DataFrame
        Sensor readings DataFrame. Must have 'SensorName' and 'SensorReadingUTC' columns.
    coordinates_path : str
        Path to building_coordinates.csv.
    cache_dir : str, optional
        Directory for caching weather API responses.
        Defaults to None (no caching).
    logger : logging.Logger, optional
        Logger instance for status messages.

    Returns
    -------
    polars.DataFrame
        Input DataFrame with added weather columns:
        weather_temp_f, weather_humidity_pct, weather_precip_mm, weather_cloud_cover_pct
    """
    if sensors.is_empty():
        # Return with empty weather columns
        for col_name in WEATHER_COLUMN_MAP.values():
            sensors = sensors.with_columns(
                polars.lit(None, dtype=polars.Float64).alias(col_name)
            )
        sensors = sensors.with_columns(
            polars.lit(None, dtype=polars.Utf8).alias("weather_wmo_description")
        )
        return sensors

    if "SensorName" not in sensors.columns or "SensorReadingUTC" not in sensors.columns:
        if logger:
            logger.warning(
                "Weather enrichment skipped: missing SensorName or SensorReadingUTC columns"
            )
        for col_name in WEATHER_COLUMN_MAP.values():
            sensors = sensors.with_columns(
                polars.lit(None, dtype=polars.Float64).alias(col_name)
            )
        sensors = sensors.with_columns(
            polars.lit(None, dtype=polars.Utf8).alias("weather_wmo_description")
        )
        return sensors

    # Load building coordinates
    coordinates = load_building_coordinates(coordinates_path)

    # Extract building codes from sensor names
    sensors = extract_building_codes_column(sensors)

    # Find unique building codes in the data
    building_codes = (
        sensors.select("building_code")
        .unique()
        .to_series()
        .drop_nulls()
        .to_list()
    )

    if not building_codes:
        if logger:
            logger.warning("No building codes found in sensor data")
        sensors = sensors.drop("building_code")
        for col_name in WEATHER_COLUMN_MAP.values():
            sensors = sensors.with_columns(
                polars.lit(None, dtype=polars.Float64).alias(col_name)
            )
        sensors = sensors.with_columns(
            polars.lit(None, dtype=polars.Utf8).alias("weather_wmo_description")
        )
        return sensors

    # Group by unique lat/long to minimize API calls
    locations = get_unique_locations(building_codes, coordinates)

    if logger:
        logger.info(
            f"Weather enrichment: {len(building_codes)} building codes "
            f"mapped to {len(locations)} unique locations"
        )

    # Determine date range from sensor data
    min_utc = sensors["SensorReadingUTC"].min()
    max_utc = sensors["SensorReadingUTC"].max()
    start_date = datetime.datetime.fromtimestamp(min_utc, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    end_date = datetime.datetime.fromtimestamp(max_utc, tz=datetime.timezone.utc).strftime("%Y-%m-%d")

    # Add hour-floored UTC column for joining
    sensors = sensors.with_columns(
        (polars.col("SensorReadingUTC") - (polars.col("SensorReadingUTC") % 3600))
        .alias("_weather_hour_utc")
    )

    # Fetch weather for each unique location and build a lookup table
    weather_lookup_parts = []
    for (lat, lon), codes in locations.items():
        weather_df = fetch_weather_for_range(
            latitude=lat,
            longitude=lon,
            start_date=start_date,
            end_date=end_date,
            cache_dir=cache_dir,
            logger=logger,
        )

        if weather_df is None:
            if logger:
                logger.warning(
                    f"No weather data for location ({lat}, {lon}), "
                    f"buildings: {codes}"
                )
            continue

        # Create a row for each building code at this location
        for code in codes:
            # Build column selections dynamically from WEATHER_COLUMN_MAP
            weather_cols = [polars.col("weather_hour_utc")]
            for param, col_name in WEATHER_COLUMN_MAP.items():
                if param in weather_df.columns:
                    weather_cols.append(polars.col(param).alias(col_name))
            location_weather = weather_df.select(weather_cols).with_columns(
                polars.lit(code).alias("building_code")
            )
            weather_lookup_parts.append(location_weather)

        # Rate-limit to be respectful to the free API
        time.sleep(0.25)

    if not weather_lookup_parts:
        if logger:
            logger.warning("No weather data fetched for any location")
        sensors = sensors.drop(["building_code", "_weather_hour_utc"])
        for col_name in WEATHER_COLUMN_MAP.values():
            sensors = sensors.with_columns(
                polars.lit(None, dtype=polars.Float64).alias(col_name)
            )
        sensors = sensors.with_columns(
            polars.lit(None, dtype=polars.Utf8).alias("weather_wmo_description")
        )
        return sensors

    # Combine all weather data into one lookup table
    weather_lookup = polars.concat(weather_lookup_parts)

    # Join sensor data with weather data on building_code + hour
    sensors = sensors.join(
        weather_lookup,
        left_on=["building_code", "_weather_hour_utc"],
        right_on=["building_code", "weather_hour_utc"],
        how="left",
    )

    # Clean up temporary columns
    sensors = sensors.drop(["building_code", "_weather_hour_utc"])

    # Convert Celsius columns to Fahrenheit: F = (C * 9/5) + 32
    conversion_exprs = []
    for col_name in CELSIUS_TO_FAHRENHEIT_COLUMNS:
        if col_name in sensors.columns:
            conversion_exprs.append(
                (polars.col(col_name) * 9 / 5 + 32).alias(col_name)
            )
    if conversion_exprs:
        sensors = sensors.with_columns(conversion_exprs)

    # Map WMO weather codes to human-readable descriptions
    if "weather_wmo_code" in sensors.columns:
        sensors = sensors.with_columns(
            polars.col("weather_wmo_code")
            .cast(polars.Int64, strict=False)
            .map_elements(
                lambda code: WMO_CODE_DESCRIPTIONS.get(code) if code is not None else None,
                return_dtype=polars.Utf8,
            )
            .alias("weather_wmo_description")
        )

    if logger:
        matched = sensors.filter(polars.col("weather_temp_f").is_not_null()).shape[0]
        total = sensors.shape[0]
        logger.info(
            f"Weather enrichment complete: {matched}/{total} readings matched "
            f"({matched/total*100:.1f}%)"
        )

    return sensors
