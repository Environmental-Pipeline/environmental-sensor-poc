# Technical Architecture

## System Components

### 1. Data Collection Layer
- **Coris API Integration**
  - Endpoints:
    - `cats/user`: Retrieves all sensor readings and sensor IDs
    - `sensor/historical`: Fetches historical data for specific sensors
  - Authentication via API key
  - Configurable data retrieval intervals

### 2. Data Storage Layer
- **File-based Storage**
  - Primary format: Parquet files
  - Separate tables per sensor type
  - Files:
    - `sensor_readings.parquet`: Current sensor data
    - `device_readings.parquet`: Device-specific information

### 3. Processing Layer (EnvironmentData Class)
- **Core Operations**
  - Database initialization
  - Current readings retrieval
  - Data consolidation
  - Historical data management
- **Data Processing**
  - Polars-based data manipulation
  - Type-specific data handling
  - Error handling and logging

### 4. Scheduling Layer
- **Cron Jobs**
  - Minute-level sensor reading collection
  - 10-minute data consolidation
  - Configurable through `jobs/cronjobs`

### 5. Development Environment
- **Local Setup**
  - Python virtual environment
  - Package management via requirements.txt
  - Jupyter Notebook for analysis

### 6. Container Environment
- **Docker Configuration**
  - Custom image build
  - Port mapping (8888:8888)
  - Volume management
  - Environment variable handling

## Data Flow
1. Cron triggers data collection job
2. EnvironmentData class fetches data from Coris API
3. Raw data processed using Polars
4. Processed data stored in Parquet files
5. Data consolidated at regular intervals
6. Analysis available through Jupyter notebooks

## Configuration Management
- **.env File**
  - API credentials
  - Historical data range
  - Testing mode settings
- **Docker Environment**
  - Container-specific settings
  - Port mappings
  - Volume configurations

## Monitoring and Logging
- **Log Files**
  - Location: `data/EnvironmentData.log`
  - Captures operational events
  - Error tracking
  - Performance metrics

## Extension Points
1. **Additional APIs**
   - Extensible API integration in EnvironmentData class
   - Modular sensor type handling
   
2. **Data Processing**
   - Customizable consolidation logic
   - Flexible data transformation pipelines

3. **Storage Options**
   - Adaptable storage backend
   - Support for different file formats 