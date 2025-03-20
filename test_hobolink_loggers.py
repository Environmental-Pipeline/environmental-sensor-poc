import requests
import datetime
import json
import os
import sys
from dotenv import load_dotenv
import pandas as pd
from collections import defaultdict

# Load environment variables from specified file or default .env
env_file = os.getenv('ENV_FILE', '.env')
print(f"Loading environment from: {env_file}")
load_dotenv(env_file)

# Configure these values from environment variables
CLIENT_ID = os.getenv('HOBOLINK_CLIENT_ID')
CLIENT_SECRET = os.getenv('HOBOLINK_CLIENT_SECRET')
USER_ID = os.getenv('HOBOLINK_USER_ID')

# All loggers from documentation 
ALL_LOGGERS = [
    {"id": "22202142", "name": "RX Station 1"},
    {"id": "22202141", "name": "RX Station 2"},
    {"id": "10740550", "name": "C149 X-Ray"},
    {"id": "10889153", "name": "C145 Structural"},
    {"id": "10889154", "name": "C107 Enviro Chamber"},
    {"id": "20284064", "name": "C137 Raman"},
    {"id": "20284065", "name": "C134"},
    {"id": "20447203", "name": "LML Cold Room"},
    {"id": "22040302", "name": "MX TRH 3"},
    {"id": "22040303", "name": "MX TRH 1"},
    {"id": "22040304", "name": "MX TRH 2"},
    {"id": "22040305", "name": "MX TRH 4"},
    {"id": "22194277", "name": "Temp RH Light 1"},
    {"id": "22194278", "name": "Temp RH Light 2"}
]

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

def get_historical_readings(logger_id, start_date, end_date, token=None):
    """Get historical readings from a specific logger within a date range."""
    # Get OAuth token if not provided
    if not token:
        token = get_hobolink_token()
        if not token:
            return None
    
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
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error getting data: {e}")
        return None

def check_logger_activity(logger_id, token=None, periods=None):
    """Check if a logger has data in different time periods."""
    # Default time periods to check: last day, last week, last month, last year
    if periods is None:
        now = datetime.datetime.now(datetime.timezone.utc)
        periods = [
            {"name": "Last 24 hours", "start": now - datetime.timedelta(days=1), "end": now},
            {"name": "Last 7 days", "start": now - datetime.timedelta(days=7), "end": now},
            {"name": "Last 30 days", "start": now - datetime.timedelta(days=30), "end": now},
            {"name": "Last 90 days", "start": now - datetime.timedelta(days=90), "end": now},
            {"name": "Last year", "start": now - datetime.timedelta(days=365), "end": now}
        ]
    
    results = {"logger_id": logger_id, "periods": []}
    token = token or get_hobolink_token()
    
    if not token:
        return {"logger_id": logger_id, "error": "Failed to get auth token"}
    
    for period in periods:
        data = get_historical_readings(
            logger_id, 
            period["start"], 
            period["end"], 
            token=token
        )
        
        if data and "observation_list" in data:
            observations = data["observation_list"]
            
            # Count readings by sensor
            sensor_data = defaultdict(int)
            for obs in observations:
                sensor_id = obs.get("sensor_sn", "unknown")
                sensor_data[sensor_id] += 1
            
            # Get latest data point timestamp
            latest_timestamp = None
            if observations:
                timestamps = [obs.get("timestamp") for obs in observations if "timestamp" in obs]
                if timestamps:
                    latest_timestamp = max(timestamps)
            
            period_result = {
                "period_name": period["name"],
                "observations_count": len(observations),
                "sensors": dict(sensor_data),
                "latest_timestamp": latest_timestamp
            }
        else:
            period_result = {
                "period_name": period["name"],
                "observations_count": 0,
                "sensors": {},
                "latest_timestamp": None
            }
        
        results["periods"].append(period_result)
    
    return results

def analyze_all_loggers():
    """Analyze data availability for all loggers."""
    token = get_hobolink_token()
    if not token:
        print("Failed to get authentication token.")
        return
    
    results = []
    
    for logger in ALL_LOGGERS:
        logger_id = logger["id"]
        logger_name = logger["name"]
        
        print(f"\nAnalyzing logger {logger_id} ({logger_name})...")
        
        result = check_logger_activity(logger_id, token=token)
        result["logger_name"] = logger_name
        results.append(result)
        
        # Save individual logger report to file
        with open(f"logger_analysis_{logger_id}.json", "w") as f:
            json.dump(result, f, indent=2)
    
    # Generate a summary report
    summary = []
    for result in results:
        logger_summary = {
            "logger_id": result["logger_id"],
            "logger_name": result["logger_name"]
        }
        
        for period in result["periods"]:
            period_name = period["period_name"].replace(" ", "_").lower()
            logger_summary[f"readings_{period_name}"] = period["observations_count"]
            logger_summary[f"latest_{period_name}"] = period["latest_timestamp"]
        
        summary.append(logger_summary)
    
    # Save as both CSV and JSON
    pd.DataFrame(summary).to_csv("logger_summary.csv", index=False)
    with open("logger_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    
    print("\nAnalysis complete. Results saved to:")
    print("- Individual logger reports: logger_analysis_*.json")
    print("- Summary report: logger_summary.csv and logger_summary.json")
    
    return results

def analyze_specific_loggers(logger_ids):
    """Analyze specific loggers only."""
    token = get_hobolink_token()
    if not token:
        print("Failed to get authentication token.")
        return
    
    results = []
    logger_map = {logger["id"]: logger["name"] for logger in ALL_LOGGERS}
    
    for logger_id in logger_ids:
        logger_name = logger_map.get(logger_id, f"Unknown Logger {logger_id}")
        
        print(f"\nAnalyzing logger {logger_id} ({logger_name})...")
        
        result = check_logger_activity(logger_id, token=token)
        result["logger_name"] = logger_name
        results.append(result)
        
        # Save individual logger report to file
        with open(f"logger_analysis_{logger_id}.json", "w") as f:
            json.dump(result, f, indent=2)
    
    # Generate a summary report for these loggers
    summary = []
    for result in results:
        logger_summary = {
            "logger_id": result["logger_id"],
            "logger_name": result["logger_name"]
        }
        
        for period in result["periods"]:
            period_name = period["period_name"].replace(" ", "_").lower()
            logger_summary[f"readings_{period_name}"] = period["observations_count"]
            logger_summary[f"latest_{period_name}"] = period["latest_timestamp"]
        
        summary.append(logger_summary)
    
    # Save as both CSV and JSON
    pd.DataFrame(summary).to_csv("logger_summary.csv", index=False)
    with open("logger_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    
    print("\nAnalysis complete. Results saved to:")
    print("- Individual logger reports: logger_analysis_*.json")
    print("- Summary report: logger_summary.csv and logger_summary.json")
    
    return results

def main():
    """Main function to analyze HOBOlink loggers."""
    # Check if specific loggers are given
    if len(sys.argv) > 1:
        loggers_to_check = sys.argv[1].split(',')
        print(f"Analyzing specific loggers: {', '.join(loggers_to_check)}")
        analyze_specific_loggers(loggers_to_check)
    else:
        print("Analyzing all loggers from the documentation...")
        analyze_all_loggers()

if __name__ == "__main__":
    main() 