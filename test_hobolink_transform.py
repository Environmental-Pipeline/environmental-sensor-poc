import json
import os
import datetime
import polars as pl
import numpy as np
from dotenv import load_dotenv

# Load environment variables
env_file = os.getenv('ENV_FILE', '.env')
print(f"Loading environment from: {env_file}")
load_dotenv(env_file)

# Function to convert ISO timestamp to Unix timestamp (seconds since epoch)
def iso_to_unix_timestamp(iso_timestamp):
    # Remove the Z suffix indicating UTC
    timestamp_str = iso_timestamp.rstrip('Z')
    # Parse the ISO format
    dt = datetime.datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
    # Set UTC timezone and convert to Unix timestamp
    dt = dt.replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp())

def get_current_utc():
    """Get current UTC timestamp in seconds since epoch."""
    return int(datetime.datetime.now(datetime.timezone.utc).timestamp())

def transform_hobolink_to_coris_format(hobolink_data, logger_mapping):
    """
    Transform HOBOlink data to match the CORIS data format expected by EnvironmentData
    
    Parameters:
    -----------
    hobolink_data : dict
        Raw JSON response from HOBOlink API
    logger_mapping : dict
        Dictionary mapping logger IDs to device names
    
    Returns:
    --------
    dict:
        Dictionary containing readings by sensor type, transformed to match CORIS format
    """
    
    # Check if we have data to transform
    if 'observation_list' not in hobolink_data or not hobolink_data['observation_list']:
        print("No observations to transform")
        return {}
    
    # Initialize dataframes for each reading type
    readings_by_type = {
        'SensorReadingF': [],  # Temperature
        'SensorReadingRh': []  # Humidity
    }

    current_utc = get_current_utc()
    
    # Group observations by sensor and reading type
    for obs in hobolink_data['observation_list']:
        # Extract key information
        logger_id = obs['logger_sn']
        sensor_id = obs['sensor_sn']  # like "20284065-1"
        measurement_type = obs['sensor_measurement_type']
        unix_timestamp = iso_to_unix_timestamp(obs['timestamp'])
        
        # Determine reading type and value
        # HOBOlink provides both Celsius and Fahrenheit, but CORIS uses Fahrenheit
        if measurement_type == 'Temperature':
            reading_type = 'SensorReadingF'
            reading_value = float(obs['us_value'])  # Fahrenheit value
        elif measurement_type == 'RH':
            reading_type = 'SensorReadingRh'
            reading_value = float(obs['si_value'])  # RH value is the same in SI and US
        else:
            # Skip other sensor types
            continue
        
        # Create a unique identifier for the sensor
        # For CORIS compatibility, we'll use SensorID_HOBOlink
        unique_id = f"HOBOlink_{sensor_id}"
        
        # Get device name from mapping
        device_name = logger_mapping.get(logger_id, f"HOBOlink Logger {logger_id}")
        
        # Create a record that matches the expected CORIS format
        record = {
            'SensorID_HOBOlink': sensor_id,
            'SensorReadingUTC': unix_timestamp,
            reading_type: reading_value,
            'QueryUTC': current_utc,
            'DeviceName': device_name,
            'SensorName': f"{device_name} {measurement_type} {sensor_id.split('-')[1]}",
            'SensorType': measurement_type,
            'DeviceID_HOBOlink': logger_id,
        }
        
        # Add to the appropriate list
        readings_by_type[reading_type].append(record)
    
    # Convert lists to polars DataFrames with the right types
    transformed_data = {}
    
    for reading_type, readings in readings_by_type.items():
        if readings:
            # Create DataFrame
            df = pl.DataFrame(readings)
            
            # Cast to appropriate types
            df = df.with_columns([
                pl.col('SensorReadingUTC').cast(pl.Int64),
                pl.col('QueryUTC').cast(pl.Int64),
                pl.col(reading_type).cast(pl.Float32),
            ])
            
            transformed_data[reading_type] = df
            
    return transformed_data

def save_sample_transformed_data(transformed_data):
    """Save sample transformed data for inspection"""
    os.makedirs('./data/hobolink_transformed', exist_ok=True)
    
    for reading_type, df in transformed_data.items():
        if df is not None and not df.is_empty():
            # Save as parquet
            df.write_parquet(f'./data/hobolink_transformed/{reading_type}_sample.parquet')
            # Also save as CSV for easy viewing
            df.write_csv(f'./data/hobolink_transformed/{reading_type}_sample.csv')

def create_merged_schema():
    """Create a schema that merges CORIS and HOBOlink data"""
    # Core schema that both systems share
    schema = {
        # Core fields
        'SensorReadingUTC': pl.Int64,
        'QueryUTC': pl.Int64,
        'DeviceName': pl.Utf8,
        'SensorName': pl.Utf8,
        'SensorType': pl.Utf8,
        
        # Reading fields
        'SensorReadingF': pl.Float32,
        'SensorReadingRh': pl.Float32,
        
        # CORIS specific fields
        'SensorID_Coris': pl.Int32,
        'DeviceID_Coris': pl.Int32,
        
        # HOBOlink specific fields
        'SensorID_HOBOlink': pl.Utf8,
        'DeviceID_HOBOlink': pl.Utf8,
    }
    
    return schema

def main():
    # Logger mapping from documentation
    logger_mapping = {
        "22202142": "RX Station 1",
        "22202141": "RX Station 2",
        "10740550": "C149 X-Ray",
        "10889153": "C145 Structural",
        "10889154": "C107 Enviro Chamber",
        "20284064": "C137 Raman",
        "20284065": "C134",
        "20447203": "LML Cold Room",
        "22040302": "MX TRH 3",
        "22040303": "MX TRH 1",
        "22040304": "MX TRH 2",
        "22040305": "MX TRH 4",
        "22194277": "Temp RH Light 1",
        "22194278": "Temp RH Light 2"
    }
    
    print("Testing HOBOlink data transformation")
    
    # Load sample data from previous tests
    sample_files = []
    for file in os.listdir("."):
        if file.startswith("hobolink_sample_") and file.endswith(".json"):
            sample_files.append(file)
    
    if not sample_files:
        print("No sample HOBOlink data found. Run test_hobolink_api.py first.")
        return
    
    print(f"Found {len(sample_files)} sample files: {', '.join(sample_files)}")
    
    # Process each sample file
    for sample_file in sample_files:
        print(f"\nProcessing {sample_file}...")
        logger_id = sample_file.replace("hobolink_sample_", "").replace(".json", "")
        
        with open(sample_file, 'r') as f:
            hobolink_data = json.load(f)
        
        # Transform the data
        transformed_data = transform_hobolink_to_coris_format(hobolink_data, logger_mapping)
        
        # Print summary for each reading type
        for reading_type, df in transformed_data.items():
            if df is not None and not df.is_empty():
                print(f"  {reading_type}: {df.shape[0]} readings transformed")
                print(f"  Sample:")
                print(df.head(2))
        
        # Save the transformed data
        save_sample_transformed_data(transformed_data)
    
    # Display the merged schema
    print("\nMerged Schema for CORIS and HOBOlink data:")
    schema = create_merged_schema()
    for field, dtype in schema.items():
        print(f"  {field}: {dtype}")
    
    print("\nTransformation test complete. Transformed data saved to ./data/hobolink_transformed/")

if __name__ == "__main__":
    main() 