# Development Guide

## Environment Setup

### Local Development
1. **Python Environment**
   ```bash
   # Install Python 3.8 or later
   python -m venv .venv
   .venv/Scripts/activate  # Windows
   pip install -r requirements.txt
   ```

2. **Configuration**
   - Request and place `.env` file in project root
   - Key settings:
     - `DAYS_BACK`: Historical data range
     - `TESTING`: Enable/disable test mode

### Docker Development
1. **Prerequisites**
   - Install Docker Desktop
   - Ensure Docker daemon is running

2. **Build and Run**
   ```bash
   docker build . -t environment
   docker run --name environment-run -p 8888:8888 environment
   ```

3. **Container Interaction**
   - Access Jupyter: http://127.0.0.1:8888
   - Interactive shell: `docker exec -it environment-run /bin/bash`
   - View logs: `tail -f data/EnvironmentData.log`

## Project Structure
```
├── data/                 # Data storage and logs
├── docs/                 # Project documentation
├── html/                 # Generated documentation
├── jobs/                 # Cron job configurations
├── test/                 # Test files
├── .env                 # Environment configuration
├── Dockerfile           # Container configuration
├── EnvironmentData.py   # Core class implementation
├── requirements.txt     # Python dependencies
└── README.md           # Project overview
```

## Development Workflow

### 1. Code Organization
- Core logic in `EnvironmentData.py`
- Tests in `test/` directory
- Examples in Jupyter notebooks

### 2. Testing
- Run tests: `python test/run.py`
- Test mode available via `.env`
- Example notebooks for manual testing

### 3. Documentation
- Update API docs: `pdoc --html EnvironmentData.py --force`
- Review generated docs in `html/EnvironmentData.html`

### 4. Data Management
- Data stored in Parquet format
- Separate tables by sensor type
- Regular consolidation via cron

## Best Practices

### 1. Code Style
- Follow Python PEP 8 guidelines
- Document functions and classes
- Use type hints where applicable

### 2. Security
- Never commit `.env` file
- Use environment variables when possible
- Consider IP whitelisting for API access

### 3. Performance
- Utilize Polars for data operations
- Monitor memory usage
- Optimize data consolidation

### 4. Error Handling
- Implement comprehensive logging
- Handle API failures gracefully
- Validate data integrity

## Troubleshooting

### Common Issues
1. **API Connection**
   - Verify API key in `.env`
   - Check network connectivity
   - Confirm API endpoint status

2. **Docker**
   - Ensure ports are available
   - Check container logs
   - Verify volume mounts

3. **Data Processing**
   - Monitor memory usage
   - Check log files
   - Validate data formats

### Debug Tools
- Jupyter notebooks for interactive debugging
- Container logs
- Application logs in `data/EnvironmentData.log`

## Deployment

### 1. Production Considerations
- Secure API key management
- Resource monitoring
- Backup strategy

### 2. Scaling
- Monitor data volume
- Adjust consolidation frequency
- Consider cloud deployment

### 3. Maintenance
- Regular dependency updates
- Log rotation
- Performance monitoring 