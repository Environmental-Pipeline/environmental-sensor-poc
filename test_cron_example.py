#!/usr/bin/env python3

"""
Script that replicates the examples-cron.ipynb notebook workflow.
This will help identify any errors in the consolidation process.
"""

import os
import sys
import shutil
import polars
from pathlib import Path

# Add current directory to Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from EnvironmentData import EnvironmentData

def replicate_cron_example():
    """Replicate the workflow from examples-cron.ipynb"""
    
    print("🔄 Replicating examples-cron.ipynb workflow...")
    
    # Step 1: Initialize the Database
    print("\n📊 Step 1: Initialize Database")
    print("=" * 50)
    
    # Clear prior data (but handle locked files gracefully)
    if os.path.exists('data'):
        print("🧹 Clearing existing data directory...")
        try:
            shutil.rmtree('data')
            os.makedirs('data')
        except PermissionError as e:
            print(f"⚠️ Could not fully clear data directory (files may be in use): {e}")
            print("   Continuing with existing data directory...")
        except Exception as e:
            print(f"⚠️ Error clearing data directory: {e}")
            print("   Continuing with existing data directory...")
    
    try:
        # Initialize EnvironmentData - this will run the historical data pull
        print("🚀 Initializing EnvironmentData...")
        envdt = EnvironmentData(
            CatsUserID=2496, 
            days_back=365 * 2,  # 2 years of historical data
            out_of_scope=['-80', 'Cryo tank', 'Water'],
            coris_enabled=False,
            hobolink_enabled=True,
            conserv_enabled=False,  # out of scope for this project
            testing=True
        )
        print("✅ EnvironmentData initialized successfully!")
        
        # Show some log entries
        print("\n📋 Log entries:")
        try:
            with open('data/EnvironmentData.log', 'r') as file:
                log_lines = file.read().splitlines()[:10]
                for line in log_lines:
                    print(f"   {line}")
        except FileNotFoundError:
            print("   ⚠️ Log file not found")
        
        # Check initial data
        print("\n📊 Initial historical data:")
        try:
            initial_data = polars.read_parquet('data/sensor_readings.parquet')
            print(f"   Rows: {initial_data.shape[0]:,}")
            print(f"   Columns: {initial_data.shape[1]}")
            print(f"   Columns: {list(initial_data.columns)}")
            print("\n   Sample data:")
            print(initial_data.head())
        except Exception as e:
            print(f"   ❌ Error reading initial data: {e}")
            return False
            
    except Exception as e:
        print(f"❌ Failed to initialize EnvironmentData: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Step 2: Get Current Readings
    print("\n📡 Step 2: Get Current Readings")
    print("=" * 50)
    
    try:
        print("🔄 Fetching current readings...")
        envdt.get_current_readings()
        print("✅ Current readings fetched successfully!")
        
        # Check new-readings data
        print("\n📂 New readings files:")
        new_readings_files = os.listdir('data/new-readings')
        if new_readings_files:
            filename = new_readings_files[0]
            print(f"   File: {filename}")
            
            try:
                current_data = polars.read_parquet(f'data/new-readings/{filename}')
                print(f"   Rows: {current_data.shape[0]}")
                print(f"   Columns: {current_data.shape[1]}")
                print(f"   Columns: {list(current_data.columns)}")
                print("\n   Sample current readings:")
                if current_data.shape[0] > 0:
                    sample_size = min(5, current_data.shape[0])
                    print(current_data.sample(sample_size))
                else:
                    print("   No current readings data")
            except Exception as e:
                print(f"   ❌ Error reading current readings: {e}")
        else:
            print("   ⚠️ No new-readings files found")
            
    except Exception as e:
        print(f"❌ Failed to get current readings: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Step 3: Consolidate Readings
    print("\n🔄 Step 3: Consolidate Readings")
    print("=" * 50)
    
    try:
        print("🔄 Consolidating readings...")
        envdt.consolidate_readings()
        print("✅ Readings consolidated successfully!")
        
        # Check that new-readings files are cleaned up
        print("\n🧹 New-readings cleanup:")
        remaining_files = os.listdir('data/new-readings')
        print(f"   Remaining files: {remaining_files}")
        if len(remaining_files) == 0:
            print("   ✅ New-readings files successfully cleaned up")
        else:
            print("   ⚠️ Some new-readings files remain")
            
    except Exception as e:
        print(f"❌ Failed to consolidate readings: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Step 4: Check Analytical Tables
    print("\n📊 Step 4: Check Analytical Tables")
    print("=" * 50)
    
    analytical_files = [
        'sensor_readings.parquet',
        'device_readings.parquet', 
        'sensors.parquet',
        'devices.parquet',
        'utcs.parquet',
        'sensor_readings_daily.parquet',
        'device_readings_daily.parquet'
    ]
    
    for file in analytical_files:
        file_path = f'data/{file}'
        try:
            if os.path.exists(file_path):
                data = polars.read_parquet(file_path)
                print(f"✅ {file}: {data.shape[0]:,} rows, {data.shape[1]} columns")
                
                # Show sample for main tables
                if file in ['sensor_readings.parquet', 'device_readings.parquet']:
                    print(f"   Sample {file}:")
                    print(data.head(3))
                    print()
            else:
                print(f"❌ {file}: Not found")
        except Exception as e:
            print(f"❌ {file}: Error reading - {e}")
    
    # Step 5: Close connection
    print("\n🔒 Step 5: Close Connection")
    print("=" * 50)
    
    try:
        envdt.close()
        print("✅ EnvironmentData connection closed successfully!")
    except Exception as e:
        print(f"⚠️ Warning during close: {e}")
    
    print("\n🎉 Cron example workflow completed successfully!")
    return True

if __name__ == "__main__":
    success = replicate_cron_example()
    if success:
        print("\n✨ All steps completed successfully!")
        sys.exit(0)
    else:
        print("\n💥 Workflow failed - check errors above")
        sys.exit(1)