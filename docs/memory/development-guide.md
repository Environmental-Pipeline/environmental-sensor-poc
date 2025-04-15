# Development Guide

## Environment Setup

### Local Python Development
1. **Prerequisites**
   - Python 3.8 or later
   - Git for version control
   - VS Code (recommended IDE)

2. **Virtual Environment Setup**
   ```bash
   # Create virtual environment
   python -m venv .venv
   
   # Activate virtual environment
   # On Windows:
   .venv/Scripts/activate
   # On Unix/MacOS:
   source .venv/bin/activate
   
   # Install dependencies
   pip install -r requirements.txt
   ```

3. **Environment Configuration**
   - Request .env file from project maintainer
   - Place .env file in project root
   - Key configurations in .env:
     * API credentials
     * DAYS_BACK setting
     * TESTING mode flag

### Docker Development
1. **Prerequisites**
   - Docker Desktop installed
   - .env file from project maintainer

2. **Building and Running**
   ```bash
   # Build Docker image
   docker build . -t environment
   
   # Run container
   docker run --name environment-run -p 8888:8888 environment
   ```

3. **Docker Interaction Methods**
   - Access Jupyter via http://127.0.0.1:8888
   - Interactive shell: `docker exec -it environment-run /bin/bash`
   - View logs: `tail -f data/EnvironmentData.log`
   - Manage via Docker Desktop UI

## Development Workflow

### 1. Code Organization
- Main logic in `EnvironmentData.py`
- Scheduled jobs in `jobs/cronjobs`
- Examples in Jupyter notebooks
- Tests in `test/` directory

### 2. Making Changes
1. **Code Modifications**
   - Update `EnvironmentData.py` for core functionality
   - Modify cronjobs for scheduling changes
   - Update tests for new features

2. **Documentation**
   - Update docstrings in Python code
   - Regenerate documentation: `pdoc --html EnvironmentData.py --force`
   - Update README.md for significant changes

3. **Testing**
   - Run test suite: `python test/run.py`
   - Use TESTING=True in .env for development
   - Verify in both Python and Docker environments

### 3. Adding New Features
1. **New Sensor Types**
   - Add to readings object in EnvironmentData class
   - Update data processing logic
   - Add corresponding tests

2. **New API Integration**
   - Extend initialize_database()
   - Modify get_current_readings()
   - Update ID column naming
   - Add API configuration to .env

## Debugging and Monitoring

### 1. Logging
- Check `data/EnvironmentData.log` for operation logs
- Docker logs available via `docker logs environment-run`
- Enable DEBUG level logging in development

### 2. Common Issues
1. **API Connection**
   - Verify API credentials in .env
   - Check network connectivity
   - Validate API endpoint status

2. **Data Processing**
   - Monitor parquet file sizes
   - Check for data consistency
   - Verify sensor readings timestamps

3. **Docker Issues**
   - Clear containers: `docker stop $(docker ps -a -q) && docker rm $(docker ps -a -q)`
   - Rebuild image after Dockerfile changes
   - Check port conflicts

## Best Practices

### 1. Code Style
- Follow PEP 8 guidelines
- Use type hints
- Maintain comprehensive docstrings
- Keep functions focused and modular

### 2. Testing
- Write tests for new features
- Use test mode for development
- Verify both success and failure cases
- Test in both environments (local and Docker)

### 3. Security
- Never commit .env file
- Use environment variables when possible
- Consider IP whitelisting for API access
- Regular security review of dependencies

### 4. Performance
- Monitor memory usage with large datasets
- Use Polars efficiently
- Consider data archival strategies
- Profile code for bottlenecks 