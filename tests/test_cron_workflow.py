"""
Test file that replicates the 1-examples-cron.ipynb notebook functionality.
This script tests the complete cron workflow including initialization, 
current readings retrieval, and consolidation.
"""

import os
import shutil
import polars
from EnvironmentData import EnvironmentData 


def clear_data_directory():
    """Clear prior data to ensure clean test run."""
    print("Clearing data directory...")
    if os.path.exists('data'):
        shutil.rmtree('data')
        os.makedirs('data')
    print("Data directory cleared.")


def initialize_environment_data():
    """Initialize EnvironmentData with historical data pull."""
    print("Initializing EnvironmentData...")
    envdt = EnvironmentData(
        CatsUserID=2496, 
        days_back=365 * 2,
        out_of_scope=['-80', 'Cryo tank', 'Water'],
        coris_enabled=True,
        licor_enabled=True,
        conserv_enabled=False,  # out of scope for this project
        testing=True
    )
    print("EnvironmentData initialized.")
    return envdt


def display_log_info():
    """Display first 10 lines of the log file."""
    print("\nDisplaying log information:")
    try:
        with open('data/EnvironmentData.log', 'r') as file:
            for i, line in enumerate(file.read().splitlines()[:10]):
                print(f"  {i+1}: {line}")
    except FileNotFoundError:
        print("  Log file not found.")


def check_historical_data():
    """Check the historical sensor readings data."""
    print("\nChecking historical data...")
    
    # Check Coris data
    try:
        coris_data = polars.read_parquet('data/sensor_readings.parquet').filter(
            polars.col("Source") == "Coris"
        ).head()
        print(f"Coris data shape: {coris_data.shape}")
        print("Sample Coris data:")
        print(coris_data)
    except Exception as e:
        print(f"Error reading Coris data: {e}")
    
    # Check LI-COR data
    try:
        licor_data = polars.read_parquet('data/sensor_readings.parquet').filter(
            polars.col("Source") == "LI-COR"
        )
        print(f"\nLI-COR data shape: {licor_data.shape}")
        print("Sample LI-COR data:")
        print(licor_data)
    except Exception as e:
        print(f"Error reading LI-COR data: {e}")


def get_current_readings(envdt):
    """Get current readings and check the new-readings folder."""
    print("\nGetting current readings...")
    envdt.get_current_readings()
    
    # Check new-readings folder
    new_readings_files = os.listdir('data/new-readings')
    if new_readings_files:
        filename = new_readings_files[0]
        print(f"New readings file created: {filename}")
        
        # Sample the data
        try:
            sample_data = polars.read_parquet(f'data/new-readings/{filename}').sample(5)
            print("Sample of new readings:")
            print(sample_data)
        except Exception as e:
            print(f"Error reading new readings: {e}")
    else:
        print("No new readings files found.")


def consolidate_readings(envdt):
    """Consolidate readings and generate analytical tables."""
    print("\nConsolidating readings...")
    envdt.consolidate_readings()
    
    # Check that new-readings files are cleared
    new_readings_files = os.listdir('data/new-readings')
    print(f"New-readings files after consolidation: {new_readings_files}")
    if not new_readings_files:
        print("✓ New-readings directory is empty (as expected)")
    else:
        print("✗ New-readings directory still has files")


def check_analytical_tables():
    """Check all the generated analytical tables."""
    print("\nChecking analytical tables...")
    
    tables_to_check = [
        'sensor_readings.parquet',
        'device_readings.parquet', 
        'sensors.parquet',
        'devices.parquet',
        'utcs.parquet',
        'sensor_readings_daily.parquet',
        'device_readings_daily.parquet'
    ]
    
    for table_name in tables_to_check:
        try:
            table_path = f'data/{table_name}'
            if os.path.exists(table_path):
                data = polars.read_parquet(table_path)
                print(f"\n{table_name}:")
                print(f"  Shape: {data.shape}")
                print("  Sample data:")
                print(data.head())
            else:
                print(f"\n{table_name}: File not found")
        except Exception as e:
            print(f"\n{table_name}: Error reading - {e}")


def check_recent_vs_historical_data():
    """Check difference between historical and recent (cron) data."""
    print("\nChecking historical vs recent data...")
    try:
        # Check recent sensor readings (should have more fields)
        sensor_readings = polars.read_parquet('data/sensor_readings.parquet')
        print("Recent sensor readings (tail):")
        print(sensor_readings.tail())
        
        # Note: The notebook shows using duckdb but we'll use polars for consistency
        # Filter for historical data (QueryUTC is null)
        historical_device_readings = polars.read_parquet('data/device_readings.parquet').filter(
            polars.col("QueryUTC").is_null()
        ).head(10)
        print(f"\nHistorical device readings (QueryUTC is null): {historical_device_readings.shape[0]} rows")
        if historical_device_readings.shape[0] > 0:
            print(historical_device_readings)
        
    except Exception as e:
        print(f"Error checking recent vs historical data: {e}")


def run_complete_test():
    """Run the complete cron workflow test."""
    print("=" * 50)
    print("STARTING COMPLETE CRON WORKFLOW TEST")
    print("=" * 50)
    
    try:
        # Step 1: Clear data and initialize
        clear_data_directory()
        envdt = initialize_environment_data()
        
        # Step 2: Check initialization results
        display_log_info()
        check_historical_data()
        
        # Step 3: Get current readings
        get_current_readings(envdt)
        
        # Step 4: Consolidate readings
        consolidate_readings(envdt)
        
        # Step 5: Close the connection
        print("\nClosing EnvironmentData connection...")
        envdt.close()
        print("Connection closed.")
        
        # Step 6: Check all analytical tables
        check_analytical_tables()
        check_recent_vs_historical_data()
        
        print("\n" + "=" * 50)
        print("CRON WORKFLOW TEST COMPLETED SUCCESSFULLY")
        print("=" * 50)
        
    except Exception as e:
        print(f"\nERROR during test execution: {e}")
        print("=" * 50)
        print("CRON WORKFLOW TEST FAILED")
        print("=" * 50)
        raise


if __name__ == "__main__":
    run_complete_test()