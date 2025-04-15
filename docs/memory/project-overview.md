# Environmental Sensor POC - Project Overview

## Project Purpose
This project serves as a proof-of-concept for environmental sensor data collection and analysis. It focuses on gathering temperature and humidity data from sensors through the Coris API, storing it efficiently, and providing analysis capabilities.

## Core Components
1. **Data Collection**
   - Uses Coris API for sensor data retrieval
   - Supports Temperature and Humidity sensor types
   - Implements both real-time and historical data collection

2. **Data Storage**
   - Uses Polars (chosen over Pandas for better performance and memory efficiency)
   - Separate tables per sensor type to prevent sparse data
   - Parquet file format for data persistence

3. **Scheduling System**
   - Cron-based scheduling for regular data collection
   - Two main scheduled tasks:
     * get_current_readings: Runs every minute
     * consolidate_readings: Runs every 10 minutes

4. **Deployment Options**
   - Python local development environment
   - Containerized deployment using Docker

## Technical Stack
- Python 3.8+
- Polars for data processing
- Docker for containerization
- Jupyter Notebooks for analysis and examples
- Environment variables for configuration

## Key Features
1. Automated data collection from Coris API
2. Historical data backfilling capability
3. Configurable testing mode
4. Documentation generation using pdoc
5. Automated testing framework
6. Flexible API integration design

## Security Considerations
- API keys managed through .env files
- Known limitation with cron jobs and environment variables
- Recommendation for cloud-based job scheduling for enhanced security
- IP whitelisting capability for API access

## Scalability Features
- Polars framework for efficient data processing
- Separate tables per sensor type
- Configurable data retention period
- Docker containerization for deployment flexibility 