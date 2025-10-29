#!/usr/bin/env python3
"""
Hobolink API Explorer

This script runs comprehensive diagnostics on the Hobolink API endpoints
and saves sample responses for analysis and development purposes.
Now includes unlimited historical data pulls without date restrictions.

Usage:
    python test/explore_hobolink.py

Requirements:
    - HOBOLINK_API_KEY environment variable must be set
    - Creates samples/ directory for storing API responses
"""

import os
import sys
import logging
import json

# Add parent directory to path to import hobolink_client
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hobolink_client import create_hobolink_client_from_env


def diagnose_api_endpoints_unlimited(client, save_samples: bool = True):
    """
    Enhanced diagnostic function that pulls all available historical data
    without date restrictions to see what the API returns.
    """
    # Create samples directory if saving is enabled
    samples_dir = None
    if save_samples:
        samples_dir = os.path.join(os.getcwd(), "samples")
        os.makedirs(samples_dir, exist_ok=True)
        print(f"Samples will be saved to: {samples_dir}")
    
    try:
        print("=" * 60)
        print("HOBOLINK API ENDPOINT DIAGNOSTICS (UNLIMITED)")
        print("=" * 60)
        
        # 1. Devices endpoint
        print("\n1. DEVICES ENDPOINT")
        print("-" * 30)
        print("Endpoint: GET /devices?includeSensors=true")
        
        devices_response = client.get_devices(include_sensors=True)
        
        # Save devices response to file
        if save_samples and samples_dir:
            devices_file = os.path.join(samples_dir, "hobolink_devices_response.json")
            try:
                with open(devices_file, 'w', encoding='utf-8') as f:
                    json.dump(devices_response, f, indent=2, default=str)
                print(f"✓ Saved devices response to: {devices_file}")
            except Exception as e:
                print(f"✗ Failed to save devices response: {e}")
        
        print(f"Raw devices response type: {type(devices_response)}")
        if isinstance(devices_response, dict):
            print(f"Raw devices response keys: {list(devices_response.keys())}")
        
        # Handle different response formats
        devices = None
        if isinstance(devices_response, list) and len(devices_response) > 0:
            devices = devices_response
        elif isinstance(devices_response, dict) and "devices" in devices_response:
            devices = devices_response["devices"]
            print(f"Found devices array with {len(devices)} items")
        
        if devices and len(devices) > 0:
            first_device = devices[0]
            print(f"Response type: {type(devices)} with {len(devices)} devices")
            print(f"First device keys: {list(first_device.keys())}")
            print(f"First device sample:")
            
            # Print key device info
            for key in ["deviceSerialNumber", "deviceName", "productCode", "unitSystem", "loggingState"]:
                if key in first_device:
                    print(f"  {key}: {first_device[key]}")
            
            # Show sensors structure
            if "sensors" in first_device and first_device["sensors"]:
                sensors = first_device["sensors"]
                print(f"  sensors: array with {len(sensors)} items")
                if len(sensors) > 0:
                    first_sensor = sensors[0]
                    print(f"    First sensor keys: {list(first_sensor.keys())}")
                    for sensor_key in ["sensorSerialNumber", "measurementType", "units", "latest"]:
                        if sensor_key in first_sensor:
                            print(f"    {sensor_key}: {first_sensor[sensor_key]}")
            
            # 2. Data endpoint - TRY VERY WIDE DATE RANGES
            print(f"\n2. DATA ENDPOINT (MAXIMUM DATE RANGE)")
            print("-" * 30)
            print("Endpoint: GET /data?deviceSerialNumber=X&sensorSerialNumber=Y&startTime=Z&endTime=W")
            print("*** TESTING MAXIMUM POSSIBLE DATE RANGES ***")
            
            # Find temperature or humidity sensors and try them until we get data
            candidate_sensors = []
            
            # Collect all temperature/humidity sensors from all devices
            for device in devices:
                device_serial = device.get("deviceSerialNumber")
                if "sensors" in device:
                    for sensor in device["sensors"]:
                        measurement_type = sensor.get("measurementType", "").lower()
                        if measurement_type in ["temperature", "temp", "rh", "humidity", "relative humidity"]:
                            candidate_sensors.append({
                                'device': device,
                                'sensor': sensor,
                                'has_latest': sensor.get("latest") is not None
                            })
            
            # Sort by preference: sensors with latest data first
            candidate_sensors.sort(key=lambda x: x['has_latest'], reverse=True)
            
            print(f"Found {len(candidate_sensors)} candidate temperature/humidity sensors")
            
            # Try sensors until we find one with actual data - using DAYS_BACK from .env
            successful_response = None
            attempts = 0
            max_attempts = min(10, len(candidate_sensors))  # Try up to 10 sensors
            
            # Get date range using DAYS_BACK environment variable (like other parts of the system)
            # Note: Hobolink API limits historical data to less than 1 year maximum
            days_back_env = int(os.getenv('DAYS_BACK', 364))
            days_back = min(days_back_env, 364)  # Cap at 364 days to be safe with API limitation
            
            if days_back_env > 364:
                print(f"WARNING: DAYS_BACK={days_back_env} exceeds Hobolink API 1-year limit")
                print(f"Using maximum safe value: {days_back} days")
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc)
            end_time = now
            start_time = now - datetime.timedelta(days=days_back)
            
            end_time_ms = int(end_time.timestamp() * 1000)
            start_time_ms = int(start_time.timestamp() * 1000)
            
            print(f"Using DAYS_BACK={days_back} (from .env)")
            print(f"Date range: {start_time.strftime('%Y-%m-%d %H:%M:%S')} to {end_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            
            for candidate in candidate_sensors[:max_attempts]:
                attempts += 1
                target_device = candidate['device']
                target_sensor = candidate['sensor']
                
                device_serial = target_device.get("deviceSerialNumber")
                sensor_serial = target_sensor.get("sensorSerialNumber")
                measurement_type = target_sensor.get("measurementType")
                units = target_sensor.get("units")
                latest = target_sensor.get("latest")
                
                print(f"\nAttempt {attempts}: Testing device {device_serial}, sensor {sensor_serial}")
                print(f"  Sensor: {measurement_type}, {units}, latest: {latest}")
                
                try:
                    print(f"  Making {days_back}-day historical data request...")
                    print(f"  Using client method (with automatic date validation)...")
                    
                    # Use the client's get_sensor_data method which has built-in validation
                    data_response = client.get_sensor_data(
                        device_serial=device_serial,
                        sensor_serial=sensor_serial,
                        start_time_ms=start_time_ms,
                        end_time_ms=end_time_ms
                    )
                    
                    if data_response:
                        
                        # Check if we got actual data
                        has_data = False
                        if isinstance(data_response, dict):
                            if "sensors" in data_response and data_response["sensors"]:
                                has_data = True
                            elif "data" in data_response and data_response["data"]:
                                has_data = True
                            elif data_response.get("message") != "No sensor found":
                                has_data = True
                        
                        if has_data:
                            # Check for pagination warning in exploration
                            if isinstance(data_response, dict) and data_response.get("moreResults") is True:
                                print(f"  ⚠️  WARNING: moreResults=true detected for {device_serial}/{sensor_serial}")
                                print(f"  ⚠️  This indicates paginated/truncated results - data may be incomplete!")
                                print(f"  ⚠️  Pagination handling is not currently implemented!")
                            
                            print(f"  ✓ SUCCESS! Found {days_back}-day historical data for {device_serial}/{sensor_serial}")
                            successful_response = data_response
                            
                            # Save successful data response to file
                            if save_samples and samples_dir:
                                data_file = os.path.join(samples_dir, f"hobolink_{days_back}day_data_{device_serial}_{sensor_serial}.json")
                                try:
                                    with open(data_file, 'w', encoding='utf-8') as f:
                                        json.dump(data_response, f, indent=2, default=str)
                                    print(f"  ✓ Saved {days_back}-day data response to: {data_file}")
                                except Exception as e:
                                    print(f"  ✗ Failed to save {days_back}-day data response: {e}")
                            
                            # Analyze the response structure
                            print(f"  {days_back}-day data response type: {type(data_response)}")
                            if isinstance(data_response, dict):
                                print(f"  {days_back}-day data response keys: {list(data_response.keys())}")
                                
                                # Show message if present
                                if "message" in data_response:
                                    print(f"    message: {data_response['message']}")
                                
                                # Look for data patterns and count records
                                for key in ["sensors", "data", "readings", "measurements", "results"]:
                                    if key in data_response:
                                        data_content = data_response[key]
                                        print(f"    {key}: {type(data_content)}")
                                        
                                        if isinstance(data_content, list):
                                            print(f"      Length: {len(data_content)}")
                                            if len(data_content) > 0:
                                                first_item = data_content[0]
                                                print(f"      First item type: {type(first_item)}")
                                                if isinstance(first_item, dict):
                                                    print(f"      First item keys: {list(first_item.keys())}")
                                                    
                                                    # Count total records in the date range
                                                    if key == "sensors" and "data" in first_item:
                                                        sensor_data = first_item["data"]
                                                        print(f"        sensor.data: {type(sensor_data)} with {len(sensor_data) if isinstance(sensor_data, list) else 'N/A'} items")
                                                        if isinstance(sensor_data, list) and len(sensor_data) > 0:
                                                            total_records = 0
                                                            for measurement in sensor_data:
                                                                if isinstance(measurement, dict) and "records" in measurement:
                                                                    records_count = len(measurement["records"])
                                                                    total_records += records_count
                                                                    print(f"          {measurement.get('measurementType', 'unknown')}: {records_count} records")
                                                            print(f"          TOTAL {days_back}-DAY RECORDS: {total_records}")
                                                            
                                                            # Show sample of first and last records with dates
                                                            if sensor_data and isinstance(sensor_data[0], dict) and "records" in sensor_data[0]:
                                                                records = sensor_data[0]["records"]
                                                                if records:
                                                                    # Convert timestamps to readable dates
                                                                    first_ts = records[0][0] / 1000
                                                                    first_date = datetime.datetime.fromtimestamp(first_ts, datetime.timezone.utc)
                                                                    print(f"          First record: {records[0]} ({first_date.strftime('%Y-%m-%d %H:%M:%S')} UTC)")
                                                                    if len(records) > 1:
                                                                        last_ts = records[-1][0] / 1000
                                                                        last_date = datetime.datetime.fromtimestamp(last_ts, datetime.timezone.utc)
                                                                        print(f"          Last record: {records[-1]} ({last_date.strftime('%Y-%m-%d %H:%M:%S')} UTC)")
                                                else:
                                                    print(f"      Sample item: {first_item}")
                                        else:
                                            print(f"      Content: {data_content}")
                            
                            break  # Found successful response, stop trying
                        else:
                            print(f"  ✗ No data found for {device_serial}/{sensor_serial}")
                            if isinstance(data_response, dict) and "message" in data_response:
                                print(f"    Message: {data_response['message']}")
                    else:
                        print(f"  ✗ No data returned for {device_serial}/{sensor_serial}")
                        
                except Exception as e:
                    print(f"  ✗ Error querying {device_serial}/{sensor_serial}: {str(e)}")
            
            if not successful_response:
                print(f"\n✗ No sensors returned {days_back}-day historical data after {attempts} attempts")
            else:
                print(f"\n✓ Successfully found {days_back}-day historical data after {attempts} attempt(s)")
        
        else:
            print("No devices found or invalid response format")
            
        print(f"\n{'=' * 60}")
        print(f"{days_back}-DAY HISTORICAL DIAGNOSTICS COMPLETE")
        print(f"{'=' * 60}")
        
        # List saved files
        if save_samples and samples_dir:
            print(f"\nSaved sample files:")
            try:
                for file in os.listdir(samples_dir):
                    if file.startswith('hobolink_'):
                        file_path = os.path.join(samples_dir, file)
                        file_size = os.path.getsize(file_path)
                        print(f"  - {file} ({file_size} bytes)")
            except Exception as e:
                print(f"Error listing saved files: {e}")
        
    except Exception as e:
        print(f"Unlimited diagnostic error: {str(e)}")
        import traceback
        traceback.print_exc()


def main():
    """Run Hobolink API exploration with DAYS_BACK historical data pulls."""
    days_back_env = int(os.getenv('DAYS_BACK', 364))
    days_back = min(days_back_env, 364)  # Cap at API limit
    print("=" * 60)
    print(f"HOBOLINK API EXPLORER ({days_back}-DAY HISTORICAL DATA)")
    print("=" * 60)
    
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s: %(message)s'
    )
    
    try:
        # Create client
        print("Creating Hobolink client...")
        client = create_hobolink_client_from_env()
        
        # Run historical diagnostics with sample saving
        print(f"Running {days_back}-day historical data exploration...")
        diagnose_api_endpoints_unlimited(client, save_samples=True)
        
        print("\n" + "=" * 60)
        print(f"{days_back}-DAY EXPLORATION COMPLETE!")
        print("=" * 60)
        print("Check the samples/ directory for saved API responses")
        
    except Exception as e:
        print(f"✗ Error during exploration: {e}")
        print("Make sure HOBOLINK_API_KEY environment variable is set")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())