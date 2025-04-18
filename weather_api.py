import aiohttp
import asyncio
import logging
from datetime import datetime, timedelta
import pytz # Required for timezone handling
import random # Added for jitter

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Open-Meteo API endpoint for historical data
HISTORICAL_API_URL = "https://archive-api.open-meteo.com/v1/archive"

# Default timeout for API requests (seconds)
DEFAULT_TIMEOUT_SECONDS = 30

MAX_CONCURRENT_REQUESTS = 3

# Retry parameters for rate limiting
MAX_RETRIES = 5
INITIAL_BACKOFF_DELAY = 1 # seconds
BACKOFF_FACTOR = 2

async def get_historical_weather(latitude: float, longitude: float, timestamp_unix: int) -> dict | None:
    """Fetches historical weather data for a specific location and time using Open-Meteo,
       with exponential backoff for rate limiting errors.

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

    # Create a timeout object once, outside the loop
    timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)

    for attempt in range(MAX_RETRIES + 1):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(HISTORICAL_API_URL, params=params) as response:
                    # Check for 429 explicitly *before* raise_for_status for retry logic
                    if response.status == 429:
                        if attempt < MAX_RETRIES:
                            delay = INITIAL_BACKOFF_DELAY * (BACKOFF_FACTOR ** attempt)
                            jitter = random.uniform(0, delay * 0.1) # Add 10% jitter
                            wait_time = delay + jitter
                            logging.warning(f"Rate limited (429) on attempt {attempt + 1}/{MAX_RETRIES + 1} for {latitude},{longitude} on {target_date_str}. Retrying in {wait_time:.2f} seconds...")
                            await asyncio.sleep(wait_time)
                            continue # Go to the next attempt
                        else:
                            logging.error(f"Rate limited (429) on final attempt {attempt + 1}/{MAX_RETRIES + 1} for {latitude},{longitude} on {target_date_str}. Giving up.")
                            return None # Exhausted retries

                    response.raise_for_status() # Raise other HTTP errors (e.g., 400, 500)
                    data = await response.json()
                    logging.info(f"Successfully fetched Open-Meteo data for {latitude},{longitude} on {target_date_str}")

                    # --- Successful data processing ---
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
                        dt_point_utc = datetime.fromisoformat(time_str).replace(tzinfo=pytz.utc)
                        time_diff = abs((dt_point_utc - dt_utc).total_seconds())

                        if time_diff < min_diff:
                            min_diff = time_diff
                            if temps[i] is not None and humidity[i] is not None and weather_codes[i] is not None:
                                closest_match = {
                                    'outdoor_temperature_f': temps[i],
                                    'outdoor_humidity': humidity[i],
                                    'weather_condition_code': weather_codes[i],
                                    'weather_timestamp_utc': int(dt_point_utc.timestamp())
                                }
                            else:
                                logging.warning(f"Found null weather data point at closest time {time_str} for {latitude},{longitude}")
                                closest_match = None

                    if closest_match and min_diff <= 1800:
                        logging.info(f"Found closest weather match for {latitude},{longitude} at {datetime.utcfromtimestamp(closest_match['weather_timestamp_utc'])}")
                        return closest_match # Success! Exit function.
                    else:
                        logging.warning(f"No suitable hourly weather data point found within 30 minutes for {latitude},{longitude} at {dt_utc}")
                        return None # No suitable match found, exit function.
                    # --- End successful data processing ---

        except aiohttp.ClientResponseError as e:
             # Handle non-429 errors (like 400 Bad Request) immediately, no retry needed
            logging.error(f"HTTP error (non-429) fetching Open-Meteo data for {latitude},{longitude}: {e.status} - {e.message}")
            if e.status == 400:
                 logging.error("Bad Request (400): Check date range or coordinates for Open-Meteo request.")
            return None # Exit function on non-429 ClientResponseError
        except aiohttp.ClientError as e:
            # Handle other client errors (timeouts, connection issues) - could potentially retry these too, but keeping simple for now
            logging.error(f"Client error fetching Open-Meteo data for {latitude},{longitude}: {e}")
            # Optional: Add retry logic here if needed for specific ClientErrors
            if attempt < MAX_RETRIES:
                 delay = INITIAL_BACKOFF_DELAY * (BACKOFF_FACTOR ** attempt)
                 jitter = random.uniform(0, delay * 0.1)
                 wait_time = delay + jitter
                 logging.warning(f"Client error on attempt {attempt + 1}/{MAX_RETRIES + 1}. Retrying in {wait_time:.2f} seconds...")
                 await asyncio.sleep(wait_time)
                 continue
            else:
                logging.error(f"Client error on final attempt {attempt + 1}/{MAX_RETRIES + 1}. Giving up.")
                return None # Exit function on final ClientError
        except Exception as e:
            # Catch-all for unexpected errors
            logging.error(f"An unexpected error occurred on attempt {attempt + 1} fetching Open-Meteo data for {latitude},{longitude}: {e}")
            # Optional: Add retry logic here if deemed safe for generic exceptions
            if attempt < MAX_RETRIES:
                delay = INITIAL_BACKOFF_DELAY * (BACKOFF_FACTOR ** attempt)
                jitter = random.uniform(0, delay * 0.1)
                wait_time = delay + jitter
                logging.warning(f"Unexpected error on attempt {attempt + 1}/{MAX_RETRIES + 1}. Retrying in {wait_time:.2f} seconds...")
                await asyncio.sleep(wait_time)
                continue
            else:
                logging.error(f"Unexpected error on final attempt {attempt + 1}/{MAX_RETRIES + 1}. Giving up.")
                return None # Exit function on final unexpected error

    # This part should technically not be reached if logic is correct, but as a fallback:
    logging.error(f"Exhausted all attempts for {latitude},{longitude} on {target_date_str} without success or explicit failure.")
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