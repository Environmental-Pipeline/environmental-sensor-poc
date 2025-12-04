# Check date ranges in parquet files from data-runner folder
import polars as pl
from datetime import datetime
import pytz

data_path = "data-runner"

def utc_to_est(utc_timestamp):
    """Convert UTC timestamp to EST datetime string."""
    if utc_timestamp is None:
        return None
    est = pytz.timezone('US/Eastern')
    dt = datetime.utcfromtimestamp(utc_timestamp).replace(tzinfo=pytz.UTC)
    return dt.astimezone(est).strftime('%Y-%m-%d %H:%M:%S %Z')

# Check sensor_readings.parquet
print("=" * 60)
print("sensor_readings.parquet")
print("=" * 60)
try:
    df = pl.read_parquet(f"{data_path}/sensor_readings.parquet")
    print(f"Shape: {df.shape}")
    print(f"Columns: {df.columns}")
    
    # Find timestamp column
    ts_cols = [c for c in df.columns if 'time' in c.lower() or 'utc' in c.lower() or 'date' in c.lower()]
    print(f"Timestamp columns found: {ts_cols}")
    
    for col in ts_cols:
        min_val = df[col].min()
        max_val = df[col].max()
        print(f"\n{col}:")
        print(f"  Min (UTC): {min_val}")
        print(f"  Max (UTC): {max_val}")
        if isinstance(min_val, (int, float)) and min_val is not None:
            print(f"  Min (EST): {utc_to_est(min_val)}")
            print(f"  Max (EST): {utc_to_est(max_val)}")
except Exception as e:
    print(f"Error: {e}")

# Check device_readings.parquet
print("\n" + "=" * 60)
print("device_readings.parquet")
print("=" * 60)
try:
    df = pl.read_parquet(f"{data_path}/device_readings.parquet")
    print(f"Shape: {df.shape}")
    print(f"Columns: {df.columns}")
    
    ts_cols = [c for c in df.columns if 'time' in c.lower() or 'utc' in c.lower() or 'date' in c.lower()]
    print(f"Timestamp columns found: {ts_cols}")
    
    for col in ts_cols:
        min_val = df[col].min()
        max_val = df[col].max()
        print(f"\n{col}:")
        print(f"  Min (UTC): {min_val}")
        print(f"  Max (UTC): {max_val}")
        if isinstance(min_val, (int, float)) and min_val is not None:
            print(f"  Min (EST): {utc_to_est(min_val)}")
            print(f"  Max (EST): {utc_to_est(max_val)}")
except Exception as e:
    print(f"Error: {e}")

# Check device_readings_daily.parquet (just dates)
print("\n" + "=" * 60)
print("device_readings_daily.parquet (dates only)")
print("=" * 60)
try:
    df = pl.read_parquet(f"{data_path}/device_readings_daily.parquet")
    print(f"Shape: {df.shape}")
    print(f"Columns: {df.columns}")
    
    date_cols = [c for c in df.columns if 'date' in c.lower()]
    print(f"Date columns found: {date_cols}")
    
    for col in date_cols:
        min_val = df[col].min()
        max_val = df[col].max()
        print(f"\n{col}:")
        print(f"  Min: {min_val}")
        print(f"  Max: {max_val}")
except Exception as e:
    print(f"Error: {e}")
