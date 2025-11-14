"""
Test to verify that historical data (multiple days) is properly included 
in daily aggregation tables.

This test should run after test_conserv_integration to ensure data exists.

Usage:
    python test/test_multiple_days.py
    python test/run.py
"""

import polars as pl
import os
import sys


def test_multiple_days():
    """Test that daily tables include historical data spanning multiple days."""
    
    print("=" * 60)
    print("TESTING MULTIPLE DAYS IN DAILY TABLES")
    print("=" * 60)
    
    try:
        print("\n1. CHECKING QUERYUTC VALUES:")
        sensor_readings = pl.read_parquet('../data/sensor_readings.parquet')
        
        # Check QueryUTC distribution
        query_utc_stats = sensor_readings.select([
            pl.col('QueryUTC').is_null().sum().alias('null_queryutc'),
            pl.col('QueryUTC').is_not_null().sum().alias('not_null_queryutc'),
            pl.len().alias('total')
        ])
        
        print("   QueryUTC distribution:")
        for row in query_utc_stats.iter_rows(named=True):
            print(f"     NULL QueryUTC: {row['null_queryutc']} ({row['null_queryutc']/row['total']*100:.1f}%)")
            print(f"     NOT NULL QueryUTC: {row['not_null_queryutc']} ({row['not_null_queryutc']/row['total']*100:.1f}%)")
            print(f"     Total: {row['total']}")
            
        # Check dates for historical (null QueryUTC) vs current (non-null QueryUTC)
        print("\n   Date ranges by QueryUTC status:")
        
        # Check data types first
        print(f"   SensorReadingUTC dtype: {sensor_readings['SensorReadingUTC'].dtype}")
        
        # Historical data (QueryUTC is null)
        historical = sensor_readings.filter(pl.col('QueryUTC').is_null())
        if historical.shape[0] > 0:
            # Convert from epoch to datetime if needed
            if sensor_readings['SensorReadingUTC'].dtype == pl.Int64:
                hist_dates = historical.select((pl.col('SensorReadingUTC') * 1000000).cast(pl.Datetime('us')).dt.date().alias('date')).unique().sort('date')
            else:
                hist_dates = historical.select(pl.col('SensorReadingUTC').dt.date().alias('date')).unique().sort('date')
            
            print(f"     Historical data (QueryUTC=null): {historical.shape[0]} records")
            print(f"       Date range: {hist_dates['date'].min()} to {hist_dates['date'].max()}")
            print(f"       Unique dates: {len(hist_dates)}")
        
        # Current data (QueryUTC is not null)  
        current = sensor_readings.filter(pl.col('QueryUTC').is_not_null())
        if current.shape[0] > 0:
            # Convert from epoch to datetime if needed
            if sensor_readings['SensorReadingUTC'].dtype == pl.Int64:
                curr_dates = current.select((pl.col('SensorReadingUTC') * 1000000).cast(pl.Datetime('us')).dt.date().alias('date')).unique().sort('date')
            else:
                curr_dates = current.select(pl.col('SensorReadingUTC').dt.date().alias('date')).unique().sort('date')
                
            print(f"     Current data (QueryUTC!=null): {current.shape[0]} records")
            print(f"       Date range: {curr_dates['date'].min()} to {curr_dates['date'].max()}")
            print(f"       Unique dates: {len(curr_dates)}")

        print("\n2. TESTING THE PROBLEMATIC FILTER:")
        # This is the filter causing the issue:
        # (polars.col("SensorReadingUTC") - polars.col("QueryUTC")).abs() < 60 * 5
        
        # Show what happens with this filter
        print("   Original records:", sensor_readings.shape[0])
        
        filtered = sensor_readings.filter(
            (pl.col("SensorReadingUTC") - pl.col("QueryUTC")).abs() < 60 * 5
        )
        print("   After time filter:", filtered.shape[0])
        print("   Records removed:", sensor_readings.shape[0] - filtered.shape[0])
        
        # The issue is that historical data has NULL QueryUTC, so the math fails
        # Let's check what data remains after the filter
        if filtered.shape[0] > 0:
            if sensor_readings['SensorReadingUTC'].dtype == pl.Int64:
                filtered_dates = filtered.select((pl.col('SensorReadingUTC') * 1000000).cast(pl.Datetime('us')).dt.date().alias('date')).unique().sort('date')
            else:
                filtered_dates = filtered.select(pl.col('SensorReadingUTC').dt.date().alias('date')).unique().sort('date')
            print("   Date range after filter:", filtered_dates['date'].min(), "to", filtered_dates['date'].max())
            print("   Unique dates after filter:", len(filtered_dates))

        print("\n3. CHECKING DAILY TABLES:")
        
        # Check device_readings_daily
        device_daily = pl.read_parquet('../data/device_readings_daily.parquet')
        device_dates = device_daily['date'].unique().sort()
        print(f"   Device readings daily: {device_daily.shape[0]} records")
        print(f"   Date range: {device_dates.min()} to {device_dates.max()}")
        print(f"   Unique dates: {len(device_dates)}")
        
        # Check sensor_readings_daily  
        sensor_daily = pl.read_parquet('../data/sensor_readings_daily.parquet')
        sensor_dates = sensor_daily['date'].unique().sort()
        print(f"   Sensor readings daily: {sensor_daily.shape[0]} records")
        print(f"   Date range: {sensor_dates.min()} to {sensor_dates.max()}")
        print(f"   Unique dates: {len(sensor_dates)}")

        print("\n4. PROBLEM IDENTIFIED:")
        print("   The daily tables only include current data, not historical data")
        print("   This is because the filter removes all records with NULL QueryUTC")
        print("   Historical data has NULL QueryUTC, current data has non-NULL QueryUTC")
        
        # Verify the issue
        expected_days = len(hist_dates) + len(curr_dates) if historical.shape[0] > 0 and current.shape[0] > 0 else max(len(hist_dates) if historical.shape[0] > 0 else 0, len(curr_dates) if current.shape[0] > 0 else 0)
        actual_days = len(device_dates)
        
        print(f"\n5. TEST RESULTS:")
        print(f"   Expected unique dates in daily tables: {expected_days}")
        print(f"   Actual unique dates in daily tables: {actual_days}")
        
        if actual_days >= expected_days:
            print("   ✅ PASS: Daily tables include appropriate date range")
            return True
        else:
            print("   ❌ FAIL: Daily tables missing historical dates")
            print("   FIX NEEDED: Update filter in EnvironmentData.py daily table generation")
            return False
            
    except Exception as e:
        print(f"   Error in analysis: {e}")
        return False


if __name__ == "__main__":
    success = test_multiple_days()
    print("\n" + "=" * 60)
    if success:
        print("TEST PASSED")
    else:
        print("TEST FAILED")
        sys.exit(1)
    print("=" * 60)