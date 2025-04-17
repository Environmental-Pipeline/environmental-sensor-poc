# Environmental Sensor Data Analysis Report: Weather Enrichment Findings

**Date:** April 17, 2025
**Author:** Anthony (Prepared with AI Assistant)

## 1. Introduction

This report summarizes the analysis of environmental sensor data collected from the Yale Peabody Museum, enriched with corresponding historical weather data. The goal was to understand the relationship between indoor environmental conditions (temperature, humidity) and external weather patterns.

Data was collected using Coris sensors, processed using Polars, and enriched using the Open-Meteo API. Visualizations were generated using Matplotlib and Seaborn within a Jupyter Notebook environment.

**Key Findings Summary:**
*   Indoor temperature appears highly stable and well-regulated, showing minimal correlation with outdoor temperature fluctuations.
*   Indoor humidity shows more variability and appears influenced by outdoor weather conditions (lower during cold/snowy periods, higher during rain/overcast).
*   Significant data gaps exist in the analyzed period, requiring further investigation.

## 2. Methodology

*   **Sensor Data:** Temperature and Humidity readings were collected via the Coris API and stored in `data/sensor_readings.parquet`.
*   **Weather Data:** Historical hourly weather data (temperature, humidity, weather code) for the sensor location (approximated as Yale Peabody Museum, Lat: 41.3157, Lon: -72.9211) was fetched from the Open-Meteo API.
*   **Enrichment:** A daily automated process merges the weather data with the sensor readings based on the closest timestamp (within a 30-minute tolerance). The enriched data is stored in `data/sensor_readings_with_weather.parquet`.
*   **Analysis:** The enriched data was loaded into a Jupyter Notebook using the Polars library. Timestamps were converted to datetime objects for analysis. Plots were generated using Matplotlib and Seaborn. Basic outlier filtering was applied (indoor temperature < 0°F removed).

## 3. Analysis & Results

### 3.1. Indoor vs. Outdoor Temperature

The following plots compare the indoor temperature readings from the sensors against the outdoor temperature obtained from the weather API over the available time period.

**(Note: Plots should be placed in the same directory as this report or paths updated.)**

**Figure 1: Indoor vs. Outdoor Temperature (Sampled)**
![Indoor vs. Outdoor Temperature (Sampled)](temp_vs_outdoor_sampled.png)

**Figure 2: Average Hourly Indoor vs. Outdoor Temperature**
![Average Hourly Indoor vs. Outdoor Temperature](temp_vs_outdoor_hourly_avg.png)

**Observations & Analysis:**
*   **Stability:** The average indoor temperature (blue line, Figure 2) remains remarkably stable, consistently hovering around 68-70°F during the periods with data.
*   **Outdoor Fluctuation:** The outdoor temperature (orange dashed line) exhibits significant seasonal and daily fluctuations, as expected.
*   **Lack of Correlation:** There is little apparent correlation between the short-term fluctuations in outdoor temperature and the stable indoor temperature, strongly suggesting effective HVAC climate control within the monitored spaces.
*   **Data Gap:** A significant gap in data is visible from approximately mid-2023 to early 2025 in both plots. This indicates missing sensor readings or failed weather data joins during this period. The straight lines connecting the data across the gap are artifacts of the plotting library linking the last known point before the gap to the first known point after it.

### 3.2. Indoor vs. Outdoor Humidity

This plot compares the average hourly indoor relative humidity from sensors with the outdoor relative humidity from the weather API.

**(Note: Plot should be placed in the same directory as this report or path updated.)**

**Figure 3: Average Hourly Indoor vs. Outdoor Humidity**
![Average Hourly Indoor vs. Outdoor Humidity](humidity_vs_outdoor_hourly_avg.png)

**Observations & Analysis:**
*   **Volatility:** Similar to temperature, outdoor humidity (green dashed line) shows high volatility.
*   **Indoor Trend:** Average indoor humidity (blue line) is significantly more stable than outdoor humidity but shows more variation than indoor temperature, generally ranging between 40% and 60%.
*   **Weaker Correlation:** While not as tightly controlled as temperature, the indoor humidity does not directly mirror outdoor spikes. However, there might be underlying seasonal trends (further analysis needed).
*   **Data Gap:** The same data gap observed in the temperature plots is present here.

### 3.3. Indoor Conditions vs. Outdoor Weather Type

These plots show the distribution of indoor temperature and humidity based on the classified outdoor weather condition reported by the Open-Meteo API.

**(Note: Plots should be placed in the same directory as this report or paths updated.)**

**Figure 4: Indoor Temperature Distribution by Outdoor Weather Condition**
![Indoor Temperature Distribution by Outdoor Weather Condition](temp_by_weather.png)

**Figure 5: Indoor Humidity Distribution by Outdoor Weather Condition**
![Indoor Humidity Distribution by Outdoor Weather Condition](humidity_by_weather.png)

**Observations & Analysis:**
*   **Temperature Distribution:** The median indoor temperature is extremely consistent (around 70°F) across all reported outdoor weather conditions (clear, rain, snow, etc.). The interquartile range (the box) is also very similar. This reinforces the conclusion that indoor temperature is actively controlled and largely independent of outdoor weather type.
*   **Humidity Distribution:** Indoor humidity shows a noticeable pattern related to outdoor conditions. The median indoor humidity is lowest during snow conditions and highest during periods of rain and drizzle. This suggests that while not perfectly correlated, the ambient outdoor moisture levels do influence the indoor humidity levels observed by the sensors.

## 4. Conclusions & Next Steps

*   The enrichment of sensor data with external weather data provides valuable context for analysis.
*   The monitored indoor spaces exhibit strong temperature control, maintaining a consistent ~70°F regardless of outdoor temperature or weather type.
*   Indoor humidity is less tightly controlled and shows some correlation with outdoor weather patterns (generally lower during dry/cold conditions, higher during wet conditions).
*   A significant gap exists in the current dataset (approx. Aug 2023 - Jan 2025), which needs investigation. Was data collection interrupted, or was there an issue with the enrichment process during this period?
*   Outliers exist in the raw sensor data (particularly very low temperature readings) that were filtered for these plots but warrant further investigation into sensor health or calibration.

**Next Steps:**
*   Investigate the cause of the large data gap.
*   Implement data quality checks to flag or handle sensor outliers more systematically.
*   Refine analysis, potentially calculating indoor/outdoor differentials or exploring time lags.
*   Investigate adding location mapping for sensors if they are deployed in different buildings/locations in the future.
*   Integrate weather data caching to improve enrichment performance and reliability. 