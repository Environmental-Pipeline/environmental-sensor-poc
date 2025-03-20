import os
import requests
import datetime
import json
import polars as pl
from dotenv import load_dotenv

# Load environment variables
env_file = os.getenv('ENV_FILE', 'test_hobolink_env')  # Default to test_hobolink_env instead of .env
print(f"Loading environment from: {env_file}")
load_dotenv(env_file)

# Configuration
OUTPUT_DIR = './data/combined_test'
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Read API credentials
CORIS_API_KEY = os.getenv('CORIS_API_KEY')
CORIS_USER_ID = int(os.getenv('CATS_USER_ID', '2496'))  # Default from the code

HOBOLINK_CLIENT_ID = os.getenv('HOBOLINK_CLIENT_ID')
HOBOLINK_CLIENT_SECRET = os.getenv('HOBOLINK_CLIENT_SECRET')
HOBOLINK_USER_ID = os.getenv('HOBOLINK_USER_ID')

# Print credential status for debugging
print(f"CORIS API Key available: {CORIS_API_KEY is not None}")
print(f"HOBOLINK Client ID available: {HOBOLINK_CLIENT_ID is not None}")
print(f"HOBOLINK Client Secret available: {HOBOLINK_CLIENT_SECRET is not None}")
print(f"HOBOLINK User ID available: {HOBOLINK_USER_ID is not None}")

# Logger/sensor selections for test (use at least one known good from each source)
HOBOLINK_TEST_LOGGERS = ['20284065']  # C134 logger
CORIS_TEST_SENSORS = []  # Will be populated dynamically

# Reading types we care about
READING_TYPES = {
    'SensorReadingF': [],
    'SensorReadingRh': []
}

# Logger name mapping
LOGGER_MAPPING = {
    "20284065": "C134",
    "20447203": "LML Cold Room",
    "22040302": "MX TRH 3"
}

def get_current_utc():
    """Get current UTC timestamp in seconds since epoch."""
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp())

def iso_to_unix_timestamp(iso_timestamp):
    """Convert ISO timestamp to Unix timestamp (seconds since epoch)."""
    # Remove the Z suffix indicating UTC
    timestamp_str = iso_timestamp.rstrip('Z')
    # Parse the ISO format
    dt = datetime.datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
    # Set UTC timezone and convert to Unix timestamp
    dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())

# ===== CORIS API Functions =====

def get_coris_sensors():
    """Get the list of CORIS sensors."""
    current_utc = get_current_utc()
    url = f'https://cats.corismonitoring.com/api/cats/user/?ApiKey={CORIS_API_KEY}&CatsUserID={CORIS_USER_ID}'
    
    print(f"Fetching CORIS sensors list...")
    response = requests.get(url)
    
    if not response.ok:
        print(f"Error getting sensors: {response.json()}")
        return None
    
    sensors = pl.DataFrame(response.json()['Sensors'])
    
    # Rename ID fields to indicate source
    sensors = sensors.rename({'SensorID': 'SensorID_Coris', 'DeviceDevID': 'DeviceID_Coris'})
    
    # Attach the query UTC
    sensors = sensors.with_columns(pl.lit(current_utc).alias('QueryUTC'))
    
    # Select a few sensors for testing (one temperature, one humidity)
    global CORIS_TEST_SENSORS
    temp_sensors = sensors.filter(pl.col('SensorReadingF').is_not_null())
    humidity_sensors = sensors.filter(pl.col('SensorReadingRh').is_not_null())
    
    # Pick one of each type if available
    if not temp_sensors.is_empty():
        CORIS_TEST_SENSORS.append(temp_sensors['SensorID_Coris'][0])
    if not humidity_sensors.is_empty():
        # Avoid adding the same sensor twice if it has both readings
        if humidity_sensors['SensorID_Coris'][0] not in CORIS_TEST_SENSORS:
            CORIS_TEST_SENSORS.append(humidity_sensors['SensorID_Coris'][0])
    
    print(f"Selected {len(CORIS_TEST_SENSORS)} CORIS sensors for testing: {CORIS_TEST_SENSORS}")
    
    return sensors

def get_coris_historical_data(sensors, days_back=30):
    """Get historical data for selected CORIS sensors."""
    print(f"Fetching CORIS historical data for {len(CORIS_TEST_SENSORS)} sensors...")
    
    current_utc = get_current_utc()
    start_utc = current_utc - days_back * 24 * 60 * 60
    
    readings = []
    
    for reading_type in READING_TYPES:
        # Filter sensors that have this reading type
        relevant_sensors = sensors.filter(pl.col(reading_type).is_not_null())
        relevant_sensors = relevant_sensors.filter(pl.col('SensorID_Coris').is_in(CORIS_TEST_SENSORS))
        
        for sensor_row in relevant_sensors.iter_rows(named=True):
            sensor_id = sensor_row['SensorID_Coris']
            print(f"  Getting {reading_type} for sensor {sensor_id}...")
            
            url = '&'.join([
                f'https://cats.corismonitoring.com/api/sensor/historical/?ApiKey={CORIS_API_KEY}',
                f'SensorID={sensor_id}',
                f'ReadingType={reading_type}',
                f'StartUTC={start_utc}',
                f'EndUTC={current_utc}',
                f'MinReadingSpacing=600',  # every 10 minutes
                f'RequestedOutputFormat=raw'
            ])
            
            response = requests.get(url)
            
            if not response.ok:
                print(f"Error getting historical {reading_type} for {sensor_id}: {response.json()}")
                continue
            
            # Data comes in as comma separated values with no header
            data = pl.read_csv(response.content, has_header=False)
            
            if data.is_empty():
                print(f"  No data returned for sensor {sensor_id}")
                continue
                
            data.columns = ['SensorReadingUTC', reading_type]
            
            # Add sensor metadata from the sensors dataframe
            data = data.with_columns(pl.lit(sensor_id).alias('SensorID_Coris'))
            
            for col in ['SensorName', 'DeviceName', 'DeviceID_Coris', 'SensorType']:
                if col in sensor_row:
                    data = data.with_columns(pl.lit(sensor_row[col]).alias(col))
            
            # Add source identifier
            data = data.with_columns(pl.lit('CORIS').alias('Source'))
            
            # Make sure datatypes match what's expected
            if 'SensorReadingUTC' in data.columns:
                data = data.with_columns(pl.col('SensorReadingUTC').cast(pl.Int64))
            
            if reading_type in data.columns:
                data = data.with_columns(pl.col(reading_type).cast(pl.Float32))
            
            # Add to the readings list
            readings.append(data)
            print(f"  Added {data.shape[0]} readings for sensor {sensor_id}")
    
    # Combine all readings
    if not readings:
        print("No CORIS data found")
        return None
    
    combined = pl.concat(readings, how='diagonal')
    print(f"Total CORIS readings: {combined.shape[0]}")
    return combined

# ===== HOBOlink API Functions =====

def get_hobolink_token():
    """Get OAuth token from HOBOlink API."""
    if not HOBOLINK_CLIENT_ID or not HOBOLINK_CLIENT_SECRET:
        raise ValueError("HOBOlink API credentials are missing. Check environment variables.")
        
    token_url = 'https://webservice.hobolink.com/ws/auth/token'
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
    }
    data = {
        'grant_type': 'client_credentials',
        'client_id': HOBOLINK_CLIENT_ID,
        'client_secret': HOBOLINK_CLIENT_SECRET
    }
    
    print("Getting HOBOlink authentication token...")
    print(f"Using client ID: {HOBOLINK_CLIENT_ID[:5]}... (partially hidden)")
    
    try:
        response = requests.post(token_url, headers=headers, data=data)
        response.raise_for_status()  # Raise exception for non-200 responses
        
        token_data = response.json()
        print("Successfully obtained HOBOlink authentication token")
        return token_data['access_token']
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error during HOBOlink authentication: {e}")
        print(f"Response status code: {response.status_code}")
        print(f"Response body: {response.text}")
    except requests.exceptions.ConnectionError as e:
        print(f"Connection Error during HOBOlink authentication: {e}")
    except requests.exceptions.Timeout as e:
        print(f"Timeout Error during HOBOlink authentication: {e}")
    except requests.exceptions.RequestException as e:
        print(f"Request Exception during HOBOlink authentication: {e}")
    except ValueError as e:
        print(f"JSON parsing error: {e}")
        print(f"Response content: {response.text}")
    
    raise RuntimeError("Failed to authenticate with HOBOlink API. See logs for details.")

def get_hobolink_data(days_back=30):
    """Get historical data from HOBOlink API."""
    print(f"Fetching HOBOlink historical data for {len(HOBOLINK_TEST_LOGGERS)} loggers...")
    
    token = get_hobolink_token()
    
    # Calculate date range
    end_date = datetime.datetime.now(datetime.timezone.utc)
    start_date = end_date - datetime.timedelta(days=days_back)
    
    start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
    end_date_str = end_date.strftime('%Y-%m-%d %H:%M:%S')
    
    readings_by_type = {
        'SensorReadingF': [],
        'SensorReadingRh': []
    }
    current_utc = get_current_utc()
    
    for logger_id in HOBOLINK_TEST_LOGGERS:
        print(f"  Getting data for logger {logger_id}...")
        
        url = f'https://webservice.hobolink.com/ws/data/file/JSON/user/{HOBOLINK_USER_ID}'
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json'
        }
        params = {
            'loggers': logger_id,
            'start_date_time': start_date_str,
            'end_date_time': end_date_str
        }
        
        print(f"  API request: {url}")
        print(f"  Parameters: loggers={logger_id}, date range: {start_date_str} to {end_date_str}")
        
        try:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            if 'observation_list' not in data or not data['observation_list']:
                print(f"  No observations found for logger {logger_id}")
                continue
                
            observations = data['observation_list']
            print(f"  Found {len(observations)} observations for logger {logger_id}")
            
            # Process and transform observations
            for obs in observations:
                # Extract key information
                sensor_id = obs['sensor_sn']  # like "20284065-1"
                measurement_type = obs['sensor_measurement_type']
                unix_timestamp = iso_to_unix_timestamp(obs['timestamp'])
                
                # Determine reading type and value
                if measurement_type == 'Temperature':
                    reading_type = 'SensorReadingF'
                    reading_value = float(obs['us_value'])  # Fahrenheit value
                elif measurement_type == 'RH':
                    reading_type = 'SensorReadingRh'
                    reading_value = float(obs['si_value'])  # RH value
                else:
                    # Skip other sensor types
                    continue
                
                # Get device name from mapping
                device_name = LOGGER_MAPPING.get(logger_id, f"HOBOlink Logger {logger_id}")
                
                # Create a record that matches the expected format
                record = {
                    'SensorID_HOBOlink': sensor_id,
                    'SensorReadingUTC': unix_timestamp,
                    reading_type: reading_value,
                    'QueryUTC': current_utc,
                    'DeviceName': device_name,
                    'SensorName': f"{device_name} {measurement_type} {sensor_id.split('-')[1]}",
                    'SensorType': measurement_type,
                    'DeviceID_HOBOlink': logger_id,
                    'Source': 'HOBOlink'
                }
                
                # Add to the appropriate list
                readings_by_type[reading_type].append(record)
            
        except Exception as e:
            print(f"  Error getting data for logger {logger_id}: {e}")
            continue
    
    # Convert to DataFrames
    dataframes = []
    
    for reading_type, readings in readings_by_type.items():
        if readings:
            df = pl.DataFrame(readings)
            
            # Cast to appropriate types
            df = df.with_columns([
                pl.col('SensorReadingUTC').cast(pl.Int64),
                pl.col('QueryUTC').cast(pl.Int64),
                pl.col(reading_type).cast(pl.Float32),
            ])
            
            dataframes.append(df)
            print(f"  Created DataFrame with {df.shape[0]} {reading_type} readings")
    
    # Combine all readings
    if not dataframes:
        print("No HOBOlink data found")
        return None
        
    combined = pl.concat(dataframes, how='diagonal')
    print(f"Total HOBOlink readings: {combined.shape[0]}")
    return combined

def combine_data(coris_data, hobolink_data):
    """Combine CORIS and HOBOlink data into a unified format."""
    if coris_data is None and hobolink_data is None:
        print("No data to combine")
        return None
    
    print("Combining CORIS and HOBOlink data...")
    
    # Create empty dicts for missing data sources
    if coris_data is None:
        coris_data = pl.DataFrame()
    
    if hobolink_data is None:
        hobolink_data = pl.DataFrame()
    
    # Find common columns for the combined schema
    all_columns = set(coris_data.columns).union(set(hobolink_data.columns))
    
    # Define column order with most important columns first
    core_columns = [
        'Source', 'SensorReadingUTC', 'QueryUTC', 
        'SensorID_Coris', 'SensorID_HOBOlink',
        'DeviceID_Coris', 'DeviceID_HOBOlink',
        'DeviceName', 'SensorName', 'SensorType'
    ]
    
    # Add reading columns
    reading_columns = [col for col in READING_TYPES.keys() if col in all_columns]
    
    # Add any remaining columns
    remaining_columns = [col for col in all_columns if col not in core_columns and col not in reading_columns]
    
    # Final column order
    ordered_columns = core_columns + reading_columns + remaining_columns
    
    # Filter to keep only columns that exist in the data
    coris_columns = [col for col in ordered_columns if col in coris_data.columns]
    hobolink_columns = [col for col in ordered_columns if col in hobolink_data.columns]
    
    # Add missing columns to each DataFrame with null values
    for col in ordered_columns:
        if col not in coris_data.columns and not coris_data.is_empty():
            if col in ['SensorReadingF', 'SensorReadingRh']:
                coris_data = coris_data.with_columns(pl.lit(None).cast(pl.Float32).alias(col))
            elif col in ['SensorReadingUTC', 'QueryUTC']:
                coris_data = coris_data.with_columns(pl.lit(None).cast(pl.Int64).alias(col))
            else:
                coris_data = coris_data.with_columns(pl.lit(None).alias(col))
        
        if col not in hobolink_data.columns and not hobolink_data.is_empty():
            if col in ['SensorReadingF', 'SensorReadingRh']:
                hobolink_data = hobolink_data.with_columns(pl.lit(None).cast(pl.Float32).alias(col))
            elif col in ['SensorReadingUTC', 'QueryUTC']:
                hobolink_data = hobolink_data.with_columns(pl.lit(None).cast(pl.Int64).alias(col))
            else:
                hobolink_data = hobolink_data.with_columns(pl.lit(None).alias(col))
    
    # Ensure data types match between the two DataFrames
    for col in ordered_columns:
        if col in coris_data.columns and col in hobolink_data.columns:
            if not coris_data.is_empty() and not hobolink_data.is_empty():
                # Get the "stronger" type between the two
                if col in ['SensorReadingF', 'SensorReadingRh']:
                    target_type = pl.Float32
                elif col in ['SensorReadingUTC', 'QueryUTC']:
                    target_type = pl.Int64
                elif col in ['SensorID_Coris', 'DeviceID_Coris']:
                    target_type = pl.Int32
                else:
                    target_type = pl.Utf8
                
                # Cast both to that type
                coris_data = coris_data.with_columns(pl.col(col).cast(target_type))
                hobolink_data = hobolink_data.with_columns(pl.col(col).cast(target_type))
    
    # Combine the DataFrames
    combined_data = pl.concat([coris_data, hobolink_data], how='diagonal')
    
    # Re-order columns to match our schema
    final_columns = [col for col in ordered_columns if col in combined_data.columns]
    combined_data = combined_data.select(final_columns)
    
    print(f"Combined data: {combined_data.shape[0]} rows, {combined_data.shape[1]} columns")
    
    return combined_data

def main():
    """Main function to test combined data ingestion from CORIS and HOBOlink."""
    print("Testing combined CORIS and HOBOlink data ingestion")
    
    try:
        # Step 1: Get CORIS sensors
        coris_sensors = get_coris_sensors()
        
        # Step 2: Get historical data from both sources (using a 30-day window)
        coris_data = None
        hobolink_data = None
        
        if coris_sensors is not None:
            coris_data = get_coris_historical_data(coris_sensors, days_back=30)
        
        hobolink_data = get_hobolink_data(days_back=30)
        
        # Step 3: Combine the data
        combined_data = combine_data(coris_data, hobolink_data)
        
        if combined_data is not None and not combined_data.is_empty():
            # Save as parquet
            output_file = f"{OUTPUT_DIR}/combined_sensor_readings.parquet"
            combined_data.write_parquet(output_file)
            print(f"Combined data saved to {output_file}")
            
            # Also save as CSV for easy inspection
            csv_file = f"{OUTPUT_DIR}/combined_sensor_readings.csv"
            combined_data.write_csv(csv_file)
            print(f"CSV version saved to {csv_file}")
            
            # Print sample rows
            print("\nSample of combined data:")
            print(combined_data.head(5))
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        print("Test failed due to the above error.")
        return 1
    
    return 0

if __name__ == "__main__":
    main() 