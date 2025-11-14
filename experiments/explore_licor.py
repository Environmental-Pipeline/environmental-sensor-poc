#!/usr/bin/env python3
"""
LI-COR API Explorer

Create formatted output to understand the data available via the LI-COR API.

Usage:
    # Sample run (first 3 devices only)
    python experiments/explore_licor.py
    
    # Full run (all devices - comprehensive analysis)
    python experiments/explore_licor.py --all-devices

Arguments:
    --all-devices    Run comprehensive analysis on all available devices
    --days-back N    Number of days of historical data to pull (default: 364, max: 364)

Features:
    - Pulls historical data based on days-back parameter
    - Exports clean CSV with device metadata and human-readable timestamps
    - Analyzes device properties (logging states, product codes, alarm states)
    - Sample mode: 3 devices for quick testing
    - All devices mode: Complete analysis of all available devices

Requirements:
    - LICOR_API_KEY environment variable must be set
    - Createsn samples/ directory for storing API responses and CSV exports
"""

import os
import sys
import logging
import json
import argparse

# Add parent directory to path to import licor_client
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clients.licor_client import LicorClient


def diagnose_api_endpoints_unlimited(client, save_samples: bool = True, all_devices_mode: bool = False):
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
        print("LI-COR API ENDPOINT DIAGNOSTICS (UNLIMITED)")
        print("=" * 60)
        
        # 1. Devices endpoint
        print("\n1. DEVICES ENDPOINT")
        print("-" * 30)
        print("Endpoint: GET /devices?includeSensors=true")
        
        devices_response = client.get_devices()
        
        # Save devices response to file
        if save_samples and samples_dir:
            devices_file = os.path.join(samples_dir, "licor_devices_response.json")
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
            
            # Collect ALL sensors from all devices (all measurement types)
            for device in devices:
                device_serial = device.get("deviceSerialNumber")
                if "sensors" in device:
                    for sensor in device["sensors"]:
                        candidate_sensors.append({
                            'device': device,
                            'sensor': sensor,
                            'has_latest': sensor.get("latest") is not None
                        })
            
            # Sort by preference: sensors with latest data first
            candidate_sensors.sort(key=lambda x: x['has_latest'], reverse=True)
            
            print(f"Found {len(candidate_sensors)} total sensors across all measurement types")
            
            # Try ALL sensors from selected devices
            successful_responses = []
            successful_sensors = set()  # Track unique sensors we've processed
            attempts = 0
            
            # Check for all devices mode
            if all_devices_mode:
                # Process ALL sensors from ALL devices
                max_attempts = len(candidate_sensors)
                print(f"ALL_DEVICES mode: Will attempt to get data from ALL {len(candidate_sensors)} sensors")
            else:
                # Process sensors from first 3 devices only
                target_devices = set()
                limited_sensors = []
                for candidate in candidate_sensors:
                    device_serial = candidate['device'].get("deviceSerialNumber")
                    if len(target_devices) < 3:
                        target_devices.add(device_serial)
                    if device_serial in target_devices:
                        limited_sensors.append(candidate)
                candidate_sensors = limited_sensors
                max_attempts = len(candidate_sensors)
                print(f"Sample mode: Will get data from ALL sensors in first 3 devices ({len(candidate_sensors)} sensors)")
            
            # Get date range using DAYS_BACK environment variable (like other parts of the system)
            # Note: LI-COR API limits historical data to less than 1 year maximum
            days_back_env = int(os.getenv('DAYS_BACK', 364))
            days_back = min(days_back_env, 364)  # Cap at 364 days to be safe with API limitation
            
            if days_back_env > 364:
                print(f"WARNING: DAYS_BACK={days_back_env} exceeds LI-COR API 1-year limit")
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
                # Process all sensors in the list (no early stopping based on device count)
                    
                attempts += 1
                target_device = candidate['device']
                target_sensor = candidate['sensor']
                
                device_serial = target_device.get("deviceSerialNumber")
                sensor_serial = target_sensor.get("sensorSerialNumber")
                measurement_type = target_sensor.get("measurementType")
                units = target_sensor.get("units")
                latest = target_sensor.get("latest")
                
                # Process every sensor (no skipping by device)
                sensor_key = f"{device_serial}/{sensor_serial}"
                if sensor_key in successful_sensors:
                    continue  # Skip if we already processed this specific sensor
                
                print(f"\nAttempt {attempts}: Testing device {device_serial}, sensor {sensor_serial}")
                print(f"  Sensor: {measurement_type}, {units}, latest: {latest}")
                print(f"  Progress: {len(successful_sensors)} sensors processed")
                
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
                            successful_responses.append(data_response)
                            successful_sensors.add(sensor_key)
                            
                            # Save successful data response to file
                            if save_samples and samples_dir:
                                data_file = os.path.join(samples_dir, f"licor_{days_back}day_data_{device_serial}_{sensor_serial}.json")
                                try:
                                    with open(data_file, 'w', encoding='utf-8') as f:
                                        json.dump(data_response, f, indent=2, default=str)
                                    print(f"  ✓ Saved {days_back}-day data response to: {data_file}")
                                except Exception as e:
                                    print(f"  ✗ Failed to save {days_back}-day data response: {e}")
                            
                            # Analyze the response structure (show brief summary for multiple devices)
                            if isinstance(data_response, dict) and "sensors" in data_response:
                                for sensor_resp in data_response["sensors"]:
                                    if "data" in sensor_resp:
                                        total_records = 0
                                        for measurement in sensor_resp["data"]:
                                            if isinstance(measurement, dict) and "records" in measurement:
                                                records_count = len(measurement["records"])
                                                total_records += records_count
                                        print(f"    Device {device_serial}: {total_records} total records found")
                                        
                                        # Show sample record for first successful device only
                                        if len(successful_responses) == 1:
                                            sensor_data = sensor_resp["data"]
                                            if sensor_data and isinstance(sensor_data[0], dict) and "records" in sensor_data[0]:
                                                records = sensor_data[0]["records"]
                                                if records:
                                                    first_ts = records[0][0] / 1000
                                                    first_date = datetime.datetime.fromtimestamp(first_ts, datetime.timezone.utc)
                                                    print(f"    Sample record: {records[0]} ({first_date.strftime('%Y-%m-%d %H:%M:%S')} UTC)")
                            
                            # Continue to next device instead of breaking
                        else:
                            print(f"  ✗ No data found for {device_serial}/{sensor_serial}")
                            if isinstance(data_response, dict) and "message" in data_response:
                                print(f"    Message: {data_response['message']}")
                    else:
                        print(f"  ✗ No data returned for {device_serial}/{sensor_serial}")
                        
                except Exception as e:
                    print(f"  ✗ Error querying {device_serial}/{sensor_serial}: {str(e)}")
            
            if not successful_responses:
                print(f"\n✗ No sensors returned {days_back}-day historical data after {attempts} attempts")
            else:
                print(f"\n✓ Successfully found {days_back}-day historical data from {len(successful_sensors)} sensor(s) after {attempts} attempt(s)")
                
                # Generate clean CSV of readings with human-readable timestamps
                if successful_responses and save_samples and samples_dir:
                    print(f"\n3. GENERATING CLEAN CSV EXPORT")
                    print("-" * 30)
                    
                    try:
                        csv_data = []
                        
                        # Extract readings from all successful responses
                        for successful_response in successful_responses:
                            # Get moreResults flag from the response (indicates if data is paginated/incomplete)
                            more_results = successful_response.get("moreResults", False)
                            
                            if "sensors" in successful_response:
                                for sensor_info in successful_response["sensors"]:
                                    sensor_serial = sensor_info.get("sensorSerialNumber")
                                    
                                    # Find the device that contains this sensor by searching through all devices
                                    device_serial = None
                                    device_info = {
                                        "device_name": None,
                                        "product_code": None, 
                                        "unit_system": None,
                                        "logging_state": None,
                                        "alarmed": None,
                                        "last_connection_time": None
                                    }
                                    
                                    # Search through devices to find which one contains this sensor
                                    for device in devices:
                                        device_sensors = device.get("sensors", [])
                                        for device_sensor in device_sensors:
                                            if device_sensor.get("sensorSerialNumber") == sensor_serial:
                                                # Found the device that contains this sensor
                                                device_serial = device.get("deviceSerialNumber")
                                                device_info = {
                                                    "device_name": device.get("deviceName"),
                                                    "product_code": device.get("productCode"),
                                                    "unit_system": device.get("unitSystem"), 
                                                    "logging_state": device.get("loggingState"),
                                                    "alarmed": device.get("alarmed"),
                                                    "last_connection_time": device.get("lastConnectionTime")
                                                }
                                                break
                                        if device_info["device_name"] is not None:  # Found it
                                            break
                                    
                                    for measurement_data in sensor_info.get("data", []):
                                        measurement_type = measurement_data.get("measurementType")
                                        units = measurement_data.get("units")
                                        records = measurement_data.get("records", [])
                                        
                                        for record in records:
                                            if len(record) >= 2:
                                                timestamp_ms = record[0]
                                                value = record[1]
                                                
                                                # Convert timestamp to human-readable format
                                                timestamp_sec = timestamp_ms / 1000
                                                readable_time = datetime.datetime.fromtimestamp(timestamp_sec, datetime.timezone.utc)
                                                
                                                csv_data.append({
                                                    "timestamp_utc": readable_time.strftime('%Y-%m-%d %H:%M:%S'),
                                                    "timestamp_ms": int(timestamp_ms),
                                                    "device_serial": device_serial,
                                                    "device_name": device_info["device_name"],
                                                    "product_code": device_info["product_code"],
                                                    "unit_system": device_info["unit_system"],
                                                    "logging_state": device_info["logging_state"],
                                                    "alarmed": device_info["alarmed"],
                                                    "last_connection_time": device_info["last_connection_time"],
                                                    "sensor_serial": sensor_serial,
                                                    "measurement_type": measurement_type,
                                                    "value": value,
                                                    "units": units,
                                                    "data_type": measurement_data.get("dataType"),
                                                    "total_records": sensor_info.get("totalRecords"),
                                                    "latest_timestamp": sensor_info.get("latestTimestamp"),
                                                    "incomplete_results": more_results
                                                })
                        
                        # Write CSV file
                        if csv_data:
                            import csv
                            csv_file = os.path.join(samples_dir, f"licor_{days_back}day_readings.csv")
                            
                            with open(csv_file, 'w', newline='', encoding='utf-8') as f:
                                if csv_data:  # Check if we have data
                                    fieldnames = csv_data[0].keys()
                                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                                    writer.writeheader()
                                    writer.writerows(csv_data)
                            
                            print(f"✓ Generated clean CSV with {len(csv_data)} readings: {csv_file}")
                            
                            # Show sample of CSV data
                            print(f"CSV columns: {', '.join(csv_data[0].keys())}")
                            print(f"Sample readings:")
                            for i, row in enumerate(csv_data[:3]):  # Show first 3 rows
                                print(f"  [{i+1}] {row['timestamp_utc']} | {row['device_name']} ({row['product_code']}) | {row['measurement_type']}: {row['value']} {row['units']}")
                            if len(csv_data) > 3:
                                print(f"  ... and {len(csv_data) - 3} more readings")
                            
                            # Analyze device properties from the CSV data
                            print(f"\n4. DEVICE PROPERTY ANALYSIS")
                            print("-" * 30)
                            
                            # Get unique values for each device property
                            unique_devices = set()
                            unique_logging_states = set()
                            unique_product_codes = set()
                            unique_unit_systems = set()
                            unique_alarmed_states = set()
                            
                            for row in csv_data:
                                unique_devices.add(f"{row['device_name']} ({row['device_serial']})")
                                unique_logging_states.add(row['logging_state'])
                                unique_product_codes.add(row['product_code'])
                                unique_unit_systems.add(row['unit_system'])
                                unique_alarmed_states.add(str(row['alarmed']))
                            
                            print(f"Devices with data ({len(unique_devices)}):")
                            for device in sorted(d for d in unique_devices if d is not None):
                                print(f"  - {device}")
                            
                            print(f"\nLogging States found ({len(unique_logging_states)}):")
                            for state in sorted(s for s in unique_logging_states if s is not None):
                                count = sum(1 for row in csv_data if row['logging_state'] == state)
                                print(f"  - {state}: {count:,} readings")
                            # Show None count separately if any
                            none_count = sum(1 for row in csv_data if row['logging_state'] is None)
                            if none_count > 0:
                                print(f"  - None/Missing: {none_count:,} readings")
                            
                            print(f"\nProduct Codes found ({len(unique_product_codes)}):")
                            for code in sorted(c for c in unique_product_codes if c is not None):
                                count = sum(1 for row in csv_data if row['product_code'] == code)
                                print(f"  - {code}: {count:,} readings")
                            # Show None count separately if any
                            none_count = sum(1 for row in csv_data if row['product_code'] is None)
                            if none_count > 0:
                                print(f"  - None/Missing: {none_count:,} readings")
                            
                            print(f"\nUnit Systems found ({len(unique_unit_systems)}):")
                            for system in sorted(s for s in unique_unit_systems if s is not None):
                                count = sum(1 for row in csv_data if row['unit_system'] == system)
                                print(f"  - {system}: {count:,} readings")
                            # Show None count separately if any
                            none_count = sum(1 for row in csv_data if row['unit_system'] is None)
                            if none_count > 0:
                                print(f"  - None/Missing: {none_count:,} readings")
                            
                            print(f"\nAlarm States found ({len(unique_alarmed_states)}):")
                            for alarmed in sorted(a for a in unique_alarmed_states if a != 'None'):
                                count = sum(1 for row in csv_data if str(row['alarmed']) == alarmed)
                                print(f"  - {alarmed}: {count:,} readings")
                            # Show None count separately if any
                            none_count = sum(1 for row in csv_data if row['alarmed'] is None)
                            if none_count > 0:
                                print(f"  - None/Missing: {none_count:,} readings")
                        else:
                            print("✗ No readings found to export to CSV")
                    
                    except Exception as e:
                        print(f"✗ Error generating CSV: {str(e)}")
                        import traceback
                        traceback.print_exc()
        
        else:
            print("No devices found or invalid response format")
            
        print(f"\n{'=' * 60}")
        print(f"{days_back}-DAY HISTORICAL DIAGNOSTICS COMPLETE")
        print(f"{'=' * 60}")
        
        # List saved files
        if save_samples and samples_dir:
            print(f"\nSaved sample files:")
            try:
                json_files = []
                csv_files = []
                
                for file in os.listdir(samples_dir):
                    if file.startswith('licor_'):
                        file_path = os.path.join(samples_dir, file)
                        file_size = os.path.getsize(file_path)
                        if file.endswith('.json'):
                            json_files.append(f"  - {file} ({file_size} bytes)")
                        elif file.endswith('.csv'):
                            csv_files.append(f"  - {file} ({file_size} bytes)")
                        else:
                            print(f"  - {file} ({file_size} bytes)")
                
                # Display JSON files first, then CSV files
                for json_file in json_files:
                    print(json_file)
                for csv_file in csv_files:
                    print(csv_file)
                    
            except Exception as e:
                print(f"Error listing saved files: {e}")
        
    except Exception as e:
        print(f"Unlimited diagnostic error: {str(e)}")
        import traceback
        traceback.print_exc()


def main():
    """Run LI-COR API exploration with historical data pulls."""
    parser = argparse.ArgumentParser(description='LI-COR API Explorer')
    parser.add_argument('--all-devices', action='store_true',
                       help='Run comprehensive analysis on all available devices')
    parser.add_argument('--days-back', type=int, default=364,
                       help='Number of days of historical data to pull (default: 364, max: 364)')
    
    args = parser.parse_args()
    
    # Cap days at API limit and fallback to environment variable if not provided
    if args.days_back == 364:  # Default value, check environment
        days_back_env = int(os.getenv('DAYS_BACK', 364))
        days_back = min(days_back_env, 364)
    else:
        days_back = min(args.days_back, 364)  # Cap at API limit
    
    all_devices_mode = args.all_devices
    mode_text = "ALL DEVICES" if all_devices_mode else f"{days_back}-DAY"
    print("=" * 60)
    print(f"LI-COR API EXPLORER ({mode_text} HISTORICAL DATA)")
    print("=" * 60)
    
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s: %(message)s'
    )
    
    try:
        # Create client
        print("Creating LI-COR client...")
        client = LicorClient()
        
        # Run historical diagnostics with sample saving
        print(f"Running {days_back}-day historical data exploration...")
        diagnose_api_endpoints_unlimited(client, save_samples=True, all_devices_mode=all_devices_mode)
        
        print("\n" + "=" * 60)
        print(f"{days_back}-DAY EXPLORATION COMPLETE!")
        print("=" * 60)
        print("Check the samples/ directory for saved API responses")
        
    except Exception as e:
        print(f"✗ Error during exploration: {e}")
        print("Make sure LICOR_API_KEY environment variable is set")
        return 1
    
    return 0


if __name__ == "__main__":
    exit(main())