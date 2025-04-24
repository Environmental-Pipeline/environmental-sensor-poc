# generate_analysis_plots.py
import polars as pl
import matplotlib.pyplot as plt
import seaborn as sns
import datetime
import pytz # For timezone awareness

# --- Configuration ---
INPUT_FILE = "data/sensor_readings_last_year_with_weather.parquet"
OUTPUT_PLOT_TEMP_SAMPLED = "temp_vs_outdoor_sampled.png"
OUTPUT_PLOT_TEMP_HOURLY = "temp_vs_outdoor_hourly_avg.png"
OUTPUT_PLOT_HUMID_HOURLY = "humidity_vs_outdoor_hourly_avg.png"
OUTPUT_PLOT_TEMP_DIST = "temp_by_weather.png"
OUTPUT_PLOT_HUMID_DIST = "humidity_by_weather.png"

# Define a simple mapping for common WMO weather codes (expand as needed)
# Ref: https://open-meteo.com/en/docs/historical-weather-api
WMO_CODE_MAP = {
    0: 'Clear', 1: 'Mainly Clear', 2: 'Partly Cloudy', 3: 'Overcast',
    45: 'Fog', 48: 'Depositing Rime Fog',
    51: 'Light Drizzle', 53: 'Mod. Drizzle', 55: 'Dense Drizzle',
    56: 'Light Freezing Drizzle', 57: 'Dense Freezing Drizzle',
    61: 'Slight Rain', 63: 'Mod. Rain', 65: 'Heavy Rain',
    66: 'Light Freezing Rain', 67: 'Heavy Freezing Rain',
    71: 'Slight Snow', 73: 'Mod. Snow', 75: 'Heavy Snow', 77: 'Snow Grains',
    80: 'Slight Rain Showers', 81: 'Mod. Rain Showers', 82: 'Violent Rain Showers',
    85: 'Slight Snow Showers', 86: 'Heavy Snow Showers',
    95: 'Thunderstorm', 96: 'Thunderstorm+Hail', 99: 'Thunderstorm+Heavy Hail'
}

def map_weather_code(code):
    """Maps WMO code to a descriptive string."""
    return WMO_CODE_MAP.get(code, f'Code {code}') # Return code if not in map

def main():
    print(f"--- Starting Analysis Plot Generation ---")
    print(f"Loading data from: {INPUT_FILE}")

    try:
        df = pl.read_parquet(INPUT_FILE)
        if df.is_empty():
            print("Error: Input file is empty. Cannot generate plots.")
            return
        print(f"Loaded {df.height} rows.")
    except Exception as e:
        print(f"Error loading file {INPUT_FILE}: {e}")
        return

    # --- Preprocessing ---
    print("Preprocessing data...")
    try:
        # Convert timestamps to datetime objects (assuming UTC)
        df = df.with_columns([
            pl.from_epoch("SensorReadingUTC", time_unit="s").alias("datetime_utc"),
            pl.when(pl.col("weather_timestamp_utc").is_not_null()) # Handle potential nulls from join
              .then(pl.from_epoch("weather_timestamp_utc", time_unit="s"))
              .otherwise(None)
              .alias("weather_datetime_utc")
        ])

        # Extract hour for hourly aggregation
        df = df.with_columns(
            pl.col("datetime_utc").dt.hour().alias("hour")
        )

        # Map weather codes to names
        df = df.with_columns(
            pl.col("weather_condition_code")
              .map_elements(map_weather_code, return_dtype=pl.String)
              .alias("weather_condition_name")
        )

        # Check if essential columns exist after potential filtering/joins
        required_cols = ["datetime_utc", "SensorReadingF", "SensorReadingRh",
                         "weather_datetime_utc", "outdoor_temperature_f", "outdoor_humidity",
                         "weather_condition_name", "hour"]
        missing_cols = [col for col in required_cols if col not in df.columns or df[col].is_null().all()]
        if missing_cols:
            print(f"Warning: Missing or entirely null columns needed for plotting: {missing_cols}. Some plots may fail or be empty.")

    except Exception as e:
        print(f"Error during preprocessing: {e}")
        return

    # --- Generate Plots ---
    sns.set_theme(style="whitegrid")
    plt.style.use('seaborn-v0_8-whitegrid') # Use a seaborn style

    # 1. Sampled Time Series Indoor vs. Outdoor Temperature
    try:
        print(f"Generating plot 1: {OUTPUT_PLOT_TEMP_SAMPLED}")
        # --- Filter specifically for non-null Temperature data --- 
        df_temp = df.filter(pl.col("SensorReadingF").is_not_null() & (pl.col("SensorReadingF") >= 0))
        if df_temp.is_empty():
            print("Skipping plot 1: No valid temperature data found after filtering.")
        else:
            # Sample data for performance if large (e.g., every 1000th point)
            sample_fraction = 1000 / df_temp.height if df_temp.height > 1000 else 1.0
            df_sampled = df_temp.sample(fraction=sample_fraction) if sample_fraction < 1.0 else df_temp
            df_sampled = df_sampled.sort("datetime_utc") # Sort for line plot

            plt.figure(figsize=(15, 6))
            # Use the filtered df_sampled for both lines now
            sns.lineplot(data=df_sampled.to_pandas(), x="datetime_utc", y="SensorReadingF", label="Indoor Temp (°F)", color="blue")
            sns.lineplot(data=df_sampled.to_pandas(), x="datetime_utc", y="outdoor_temperature_f", label="Outdoor Temp (°F)", color="orange", linestyle="--")
            plt.title("Indoor vs. Outdoor Temperature (Sampled, Filtered)")
            plt.xlabel("Time (UTC)")
            plt.ylabel("Temperature (°F)")
            plt.legend()
            plt.xticks(rotation=45)
            plt.tight_layout()
            plt.savefig(OUTPUT_PLOT_TEMP_SAMPLED)
            plt.close()
            print(f"Saved: {OUTPUT_PLOT_TEMP_SAMPLED}")
    except Exception as e:
        print(f"Error generating plot 1: {e}")

    # 2. Average Hourly Indoor vs. Outdoor Temperature
    try:
        print(f"Generating plot 2: {OUTPUT_PLOT_TEMP_HOURLY}")
        # --- Filter for valid temp data before aggregating ---
        df_temp_valid = df.filter(pl.col("SensorReadingF").is_not_null() & (pl.col("SensorReadingF") >= 0))
        if df_temp_valid.is_empty():
             print("Skipping plot 2: No valid temperature data found for hourly aggregation.")
        else:
            df_hourly_temp = df_temp_valid.group_by("hour").agg([
                pl.mean("SensorReadingF").alias("avg_indoor_temp"),
                pl.mean("outdoor_temperature_f").alias("avg_outdoor_temp") # Outdoor temp from corresponding rows
            ]).sort("hour")

            plt.figure(figsize=(10, 5))
            sns.lineplot(data=df_hourly_temp.to_pandas(), x="hour", y="avg_indoor_temp", label="Avg Indoor Temp (°F)", marker='o')
            sns.lineplot(data=df_hourly_temp.to_pandas(), x="hour", y="avg_outdoor_temp", label="Avg Outdoor Temp (°F)", marker='x', linestyle="--")
            plt.title("Average Hourly Indoor vs. Outdoor Temperature")
            plt.xlabel("Hour of Day (UTC)")
            plt.ylabel("Average Temperature (°F)")
            plt.xticks(range(0, 24))
            plt.legend()
            plt.tight_layout()
            plt.savefig(OUTPUT_PLOT_TEMP_HOURLY)
            plt.close()
            print(f"Saved: {OUTPUT_PLOT_TEMP_HOURLY}")
    except Exception as e:
        print(f"Error generating plot 2: {e}")

    # 3. Average Hourly Indoor vs. Outdoor Humidity
    try:
        print(f"Generating plot 3: {OUTPUT_PLOT_HUMID_HOURLY}")
        # --- Filter for valid humidity data before aggregating ---
        df_humid_valid = df.filter(pl.col("SensorReadingRh").is_not_null() & (pl.col("SensorReadingRh") >= 0))
        if df_humid_valid.is_empty():
             print("Skipping plot 3: No valid humidity data found for hourly aggregation.")
        else:
            df_hourly_humid = df_humid_valid.group_by("hour").agg([
                pl.mean("SensorReadingRh").alias("avg_indoor_humid"),
                pl.mean("outdoor_humidity").alias("avg_outdoor_humid") # Outdoor humid from corresponding rows
            ]).sort("hour")

            plt.figure(figsize=(10, 5))
            sns.lineplot(data=df_hourly_humid.to_pandas(), x="hour", y="avg_indoor_humid", label="Avg Indoor Humidity (%)", marker='o')
            sns.lineplot(data=df_hourly_humid.to_pandas(), x="hour", y="avg_outdoor_humid", label="Avg Outdoor Humidity (%)", marker='x', linestyle="--")
            plt.title("Average Hourly Indoor vs. Outdoor Humidity")
            plt.xlabel("Hour of Day (UTC)")
            plt.ylabel("Average Relative Humidity (%)")
            plt.xticks(range(0, 24))
            plt.legend()
            plt.tight_layout()
            plt.savefig(OUTPUT_PLOT_HUMID_HOURLY)
            plt.close()
            print(f"Saved: {OUTPUT_PLOT_HUMID_HOURLY}")
    except Exception as e:
        print(f"Error generating plot 3: {e}")


    # 4. Indoor Temperature Distribution by Weather Condition
    try:
        print(f"Generating plot 4: {OUTPUT_PLOT_TEMP_DIST}")
        # --- Filter for valid temp data ---
        df_temp_valid_weather = df.filter(pl.col("SensorReadingF").is_not_null() & (pl.col("SensorReadingF") >= 0))
        if df_temp_valid_weather.is_empty():
            print("Skipping plot 4: No valid temperature data found for weather distribution.")
        else:
            # Get top N weather conditions by frequency for clarity, based on temp data rows
            top_conditions = df_temp_valid_weather['weather_condition_name'].value_counts().sort('count', descending=True).head(10)['weather_condition_name'].to_list()
            df_filtered_weather_temp = df_temp_valid_weather.filter(pl.col('weather_condition_name').is_in(top_conditions))

            plt.figure(figsize=(12, 6))
            sns.boxplot(data=df_filtered_weather_temp.to_pandas(), x="weather_condition_name", y="SensorReadingF")
            plt.title("Indoor Temperature Distribution by Outdoor Weather Condition (Top 10)")
            plt.xlabel("Outdoor Weather Condition")
            plt.ylabel("Indoor Temperature (°F)")
            plt.xticks(rotation=45, ha='right')
            plt.tight_layout()
            plt.savefig(OUTPUT_PLOT_TEMP_DIST)
            plt.close()
            print(f"Saved: {OUTPUT_PLOT_TEMP_DIST}")
    except Exception as e:
        print(f"Error generating plot 4: {e}")


    # 5. Indoor Humidity Distribution by Weather Condition
    try:
        print(f"Generating plot 5: {OUTPUT_PLOT_HUMID_DIST}")
        # --- Filter for valid humidity data ---
        df_humid_valid_weather = df.filter(pl.col("SensorReadingRh").is_not_null() & (pl.col("SensorReadingRh") >= 0))
        if df_humid_valid_weather.is_empty():
            print(f"Skipping plot 5: No valid humidity data found for weather distribution.")
        else:
            # Get top N weather conditions by frequency for clarity, based on humidity data rows
            top_conditions = df_humid_valid_weather['weather_condition_name'].value_counts().sort('count', descending=True).head(10)['weather_condition_name'].to_list()
            df_filtered_weather_humid = df_humid_valid_weather.filter(pl.col('weather_condition_name').is_in(top_conditions))

            plt.figure(figsize=(12, 6))
            sns.boxplot(data=df_filtered_weather_humid.to_pandas(), x="weather_condition_name", y="SensorReadingRh")
            plt.title("Indoor Humidity Distribution by Outdoor Weather Condition (Top 10)")
            plt.xlabel("Outdoor Weather Condition")
            plt.ylabel("Indoor Relative Humidity (%)")
            plt.xticks(rotation=45, ha='right')
            plt.tight_layout()
            plt.savefig(OUTPUT_PLOT_HUMID_DIST)
            plt.close()
            print(f"Saved: {OUTPUT_PLOT_HUMID_DIST}")
    except Exception as e:
        print(f"Error generating plot 5: {e}")

    print("--- Analysis Plot Generation Complete ---")

if __name__ == "__main__":
    # Note: Ensure you have polars, matplotlib, and seaborn installed
    # pip install polars matplotlib seaborn pytz
    main() 