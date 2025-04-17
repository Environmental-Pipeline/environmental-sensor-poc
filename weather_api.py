import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta
import pytz # Required for timezone handling

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Open-Meteo API endpoint for historical data
HISTORICAL_API_URL = "https://archive-api.open-meteo.com/v1/archive"

# Default timeout for API requests (seconds)
DEFAULT_TIMEOUT_SECONDS = 30

async def get_historical_weather(latitude: float, longitude: float, timestamp_unix: int) -> dict | None:
    """Fetches historical weather data for a specific location and time using Open-Meteo.

    Args:
        latitude: Latitude of the location.
        longitude: Longitude of the location.
        timestamp_unix: Unix timestamp (UTC) for the desired time.

    Returns:
        A dictionary containing weather data (temp, humidity, weather code, timestamp)
        closest to the requested timestamp if successful, None otherwise.
    """
    # Convert Unix timestamp to datetime object (UTC)
    dt_utc = datetime.utcfromtimestamp(timestamp_unix).replace(tzinfo=pytz.utc)
    target_date_str = dt_utc.strftime('%Y-%m-%d')

    params = {
        'latitude': latitude,
        'longitude': longitude,
        'start_date': target_date_str,
        'end_date': target_date_str,
        'hourly': 'temperature_2m,relativehumidity_2m,weathercode',
        'temperature_unit': 'fahrenheit', # Request temperature in Fahrenheit
        'timezone': 'UTC' # Ensure timestamps are in UTC
    }

    try:
        # Create a timeout object
        timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(HISTORICAL_API_URL, params=params) as response:
                response.raise_for_status() # Raise HTTP errors
                data = await response.json()
                logging.info(f"Successfully fetched Open-Meteo data for {latitude},{longitude} on {target_date_str}")

                # Check if hourly data is present
                if not data or 'hourly' not in data or not all(k in data['hourly'] for k in ['time', 'temperature_2m', 'relativehumidity_2m', 'weathercode']):
                    logging.warning(f"Incomplete or missing hourly data in Open-Meteo response for {latitude},{longitude} on {target_date_str}")
                    return None

                hourly_data = data['hourly']
                times = hourly_data['time']
                temps = hourly_data['temperature_2m']
                humidity = hourly_data['relativehumidity_2m']
                weather_codes = hourly_data['weathercode']

                # Find the closest hourly data point to the requested timestamp
                closest_match = None
                min_diff = float('inf')

                for i, time_str in enumerate(times):
                    # Open-Meteo times are ISO8601 strings
                    dt_point_utc = datetime.fromisoformat(time_str).replace(tzinfo=pytz.utc)
                    time_diff = abs((dt_point_utc - dt_utc).total_seconds())

                    if time_diff < min_diff:
                        min_diff = time_diff
                        # Check if data points are valid (not None)
                        if temps[i] is not None and humidity[i] is not None and weather_codes[i] is not None:
                             closest_match = {
                                'outdoor_temperature_f': temps[i],
                                'outdoor_humidity': humidity[i],
                                'weather_condition_code': weather_codes[i], # WMO weather code
                                # Note: We return the timestamp of the weather data point, not the requested one
                                'weather_timestamp_utc': int(dt_point_utc.timestamp())
                            }
                        else:
                            # Handle potential null values in the data for the closest time
                            logging.warning(f"Found null weather data point at closest time {time_str} for {latitude},{longitude}")
                            closest_match = None # Reset if data is incomplete for this point

                # Allow a maximum difference (e.g., 30 minutes) to consider it a valid match
                if closest_match and min_diff <= 1800: # 30 minutes * 60 seconds/minute
                    logging.info(f"Found closest weather match for {latitude},{longitude} at {datetime.utcfromtimestamp(closest_match['weather_timestamp_utc'])}")
                    return closest_match
                else:
                    logging.warning(f"No suitable hourly weather data point found within 30 minutes for {latitude},{longitude} at {dt_utc}")
                    return None

    except aiohttp.ClientResponseError as e:
        logging.error(f"HTTP error fetching Open-Meteo data for {latitude},{longitude}: {e.status} - {e.message}")
        # Check for specific errors, e.g., 400 Bad Request might indicate invalid date range
        if e.status == 400:
             logging.error("Bad Request (400): Check date range or coordinates for Open-Meteo request.")
        return None
    except aiohttp.ClientError as e:
        logging.error(f"Client error fetching Open-Meteo data for {latitude},{longitude}: {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred fetching Open-Meteo data for {latitude},{longitude}: {e}")
        return None

# Example usage (for testing)
async def main():
    # Example coordinates (New York City)
    lat = 40.7128
    lon = -74.0060
    # Example timestamp (use a specific past date/time)
    test_dt = datetime(2023, 10, 26, 15, 30, 0, tzinfo=pytz.utc) # October 26, 2023, 3:30 PM UTC
    timestamp = int(test_dt.timestamp())

    weather_data = await get_historical_weather(lat, lon, timestamp)
    if weather_data:
        print("Fetched weather data:")
        print(weather_data)
    else:
        print("Failed to fetch weather data.")

if __name__ == "__main__":
    asyncio.run(main()) 