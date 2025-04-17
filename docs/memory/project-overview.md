# Environmental Sensor POC - Project Overview

## Project Purpose
This project serves as a proof-of-concept for collecting, storing, and analyzing environmental sensor data from various sources, with the primary integration being the Coris API. The system is designed to monitor temperature and humidity readings from multiple sensors.

## Core Features
1. **Data Collection**
   - Periodic sensor data collection via Coris API
   - Support for multiple sensor types (Temperature, Humidity)
   - Historical data import capabilities
   
2. **Data Management**
   - Efficient data storage using Polars dataframes
   - Separate tables for different sensor types
   - Regular data consolidation
   
3. **Deployment Options**
   - Python local development setup
   - Containerized deployment using Docker
   - Automated data collection using cron jobs

## Technical Stack
- **Language**: Python 3.8+
- **Data Processing**: Polars (chosen for performance and memory efficiency)
- **Containerization**: Docker
- **Documentation**: pdoc
- **Scheduling**: cron jobs
- **Data Storage**: Parquet files
- **Development Tools**: Jupyter Notebook

## Key Components
1. **EnvironmentData Class**
   - Core class handling data operations
   - Manages sensor readings and device information
   - Handles API interactions and data processing

2. **Automated Jobs**
   - `get_current_readings`: Runs every minute
   - `consolidate_readings`: Runs every 10 minutes

3. **Configuration**
   - Environment variables for API keys and settings
   - Configurable historical data range
   - Testing mode support

## Security Considerations
- API keys stored in untracked .env file
- Container-based security considerations documented
- Recommendation for cloud-based job scheduling for enhanced security

## Scalability Features
- Polars framework for efficient data processing
- Modular design for adding new API sources
- Separate storage for different sensor types to prevent sparse tables 