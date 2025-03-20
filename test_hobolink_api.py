import requests
import datetime
import json
import os
import sys
from dotenv import load_dotenv

# Load environment variables from specified file or default .env
env_file = os.getenv('ENV_FILE', '.env')
print(f"Loading environment from: {env_file}")
load_dotenv(env_file)

# Configure these values from environment variables
CLIENT_ID = os.getenv('HOBOLINK_CLIENT_ID')
CLIENT_SECRET = os.getenv('HOBOLINK_CLIENT_SECRET')
USER_ID = os.getenv('HOBOLINK_USER_ID')
LOGGERS = os.getenv('HOBOLINK_LOGGERS', '20284065').split(',')  # Default to the primary logger if not specified

def get_current_utc():
    """Get current UTC timestamp in seconds since epoch."""
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp())

def get_hobolink_token():
    """Get OAuth token from HOBOlink API."""
    token_url = 'https://webservice.hobolink.com/ws/auth/token'
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
    }
    data = {
        'grant_type': 'client_credentials',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET
    }
    
    # Make the token request
    response = requests.post(token_url, headers=headers, data=data)
    
    if response.status_code != 200:
        print(f"Error getting token: {response.status_code}")
        print(response.text)
        return None
    
    # Parse and return the token
    token_data = response.json()
    return token_data['access_token']

def get_historical_readings(logger_id, days_back=30):
    """Get historical readings from a specific logger."""
    # Get OAuth token
    token = get_hobolink_token()
    if not token:
        return None
    
    # Calculate date range
    end_date = datetime.datetime.now(datetime.timezone.utc)
    start_date = end_date - datetime.timedelta(days=days_back)
    
    # Format dates for HOBOlink API
    start_date_str = start_date.strftime('%Y-%m-%d %H:%M:%S')
    end_date_str = end_date.strftime('%Y-%m-%d %H:%M:%S')
    
    # Prepare API request
    url = f'https://webservice.hobolink.com/ws/data/file/JSON/user/{USER_ID}'
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json'
    }
    params = {
        'loggers': logger_id,
        'start_date_time': start_date_str,
        'end_date_time': end_date_str
    }
    
    print(f"Requesting data for logger {logger_id} from {start_date_str} to {end_date_str}")
    
    # Make the request
    response = requests.get(url, headers=headers, params=params)
    
    if response.status_code != 200:
        print(f"Error getting data: {response.status_code}")
        print(response.text)
        return None
    
    return response.json()

def print_sensor_summary(data):
    """Print a summary of sensors and readings."""
    if not data or 'observation_list' not in data:
        print("No data or invalid response format")
        return
    
    observations = data['observation_list']
    if not observations:
        print("No observations found in the data")
        return
    
    # Count readings by sensor type
    sensor_counts = {}
    sensor_types = set()
    sensors = set()
    
    for obs in observations:
        sensor_sn = obs.get('sensor_sn', 'unknown')
        sensor_type = obs.get('sensor_measurement_type', 'unknown')
        
        sensors.add(sensor_sn)
        sensor_types.add(sensor_type)
        
        key = f"{sensor_sn} ({sensor_type})"
        sensor_counts[key] = sensor_counts.get(key, 0) + 1
    
    # Print summary
    print(f"Found {len(observations)} total observations")
    print(f"Unique sensors: {len(sensors)}")
    print(f"Sensor types: {', '.join(sensor_types)}")
    print("\nReadings per sensor:")
    for sensor, count in sensor_counts.items():
        print(f"  - {sensor}: {count} readings")
    
    # Print a sample reading
    if observations:
        print("\nSample reading:")
        sample = observations[0]
        for key, value in sample.items():
            print(f"  {key}: {value}")

def test_connection():
    """Test the connection to HOBOlink API."""
    print("Testing HOBOlink API connection...")
    
    # Check environment variables
    if not CLIENT_ID or not CLIENT_SECRET or not USER_ID:
        print("Error: Missing required environment variables.")
        print(f"CLIENT_ID: {'Set' if CLIENT_ID else 'Missing'}")
        print(f"CLIENT_SECRET: {'Set' if CLIENT_SECRET else 'Missing'}")
        print(f"USER_ID: {'Set' if USER_ID else 'Missing'}")
        return False
    
    # Test token retrieval
    print("Testing OAuth token retrieval...")
    token = get_hobolink_token()
    if not token:
        print("Failed to retrieve OAuth token.")
        return False
    
    print("Successfully retrieved OAuth token.")
    return True

def main():
    """Main function to test the HOBOlink API."""
    # Parse command line arguments for days
    days_back = 30  # Default to 30 days for more historical data
    if len(sys.argv) > 1:
        try:
            days_back = int(sys.argv[1])
            print(f"Using {days_back} days of historical data")
        except ValueError:
            print(f"Invalid days value: {sys.argv[1]}, using default of {days_back}")
    
    # Test connection
    if not test_connection():
        return
    
    # Test data retrieval for each logger
    for logger_id in LOGGERS:
        print(f"\nTesting data retrieval for logger {logger_id}...")
        data = get_historical_readings(logger_id, days_back=days_back)
        
        if data:
            print(f"Successfully retrieved data for logger {logger_id}")
            print_sensor_summary(data)
            
            # Save sample data to file for inspection
            sample_file = f"hobolink_sample_{logger_id}.json"
            with open(sample_file, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"Saved sample data to {sample_file}")
        else:
            print(f"Failed to retrieve data for logger {logger_id}")

if __name__ == "__main__":
    main() 