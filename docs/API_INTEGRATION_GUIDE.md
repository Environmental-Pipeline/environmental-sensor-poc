# API Integration Handoff Guide

**Environmental Sensor Data Platform - Adding New API Sources**

## Overview

This system integrates multiple environmental sensor APIs (currently Coris and Conserv) into a unified data pipeline. This guide provides step-by-step instructions for adding new API sources while maintaining backward compatibility and data integrity.

## Architecture Pattern

The system follows a **multi-source, unified schema** pattern:
- Each API source has its own client class and data transformation logic
- All data is mapped to a common schema for downstream compatibility  
- A unified entry point processes all sources in a single container run
- Individual source failures don't break the entire pipeline

## Integration Checklist

### **STEP 1: API Client Development** 🔌
- [ ] Create dedicated client class in new file `{api_name}_client.py`
- [ ] Implement authentication (API keys, tokens, etc.)
- [ ] Handle API-specific patterns (direct calls, export/download, etc.)
- [ ] Add comprehensive error handling and logging
- [ ] Support timeouts and rate limiting
- [ ] Test with mocked responses first

**Example pattern:**
```python
class NewAPIClient:
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url
        self.logger = logging.getLogger(f'{__name__}.NewAPIClient')
    
    def get_historical_data(self, start_time, end_time):
        # Implement API-specific data retrieval
        pass
```

### **STEP 2: Schema Mapping** 📊
- [ ] **CRITICAL**: Preserve existing schema columns (`SensorReadingF`, `SensorReadingRh`, etc.)
- [ ] Add new identifier column: `SensorID_{APIName}` (nullable)
- [ ] Add `source` column value for the new API
- [ ] Handle unit conversions (temperature, time zones, etc.)
- [ ] Create transformation method: `_transform_{api}_to_coris_schema()`

**Required schema columns:**
- `SensorReadingF` (Float32, °F) - Temperature 
- `SensorReadingRh` (Float32, %) - Humidity
- `SensorReadingUTC` (Int64) - Unix timestamp
- `QueryUTC` (Int64) - Query timestamp
- `SensorID_{APIName}` (String, nullable) - New API sensor ID
- `source` (String) - Source identifier
- `SensorName` (String) - Human-readable sensor name

### **STEP 3: Multi-Tenant Support (if applicable)** 🏢
- [ ] Support multiple customer API keys if needed
- [ ] Add `customer_id` column for customer data separation
- [ ] Handle individual customer failures gracefully
- [ ] Configuration via environment variables

**Pattern for multi-tenant:**
```python
# In .env
NEW_API_ENABLED=True
NEW_API_KEY_CUSTOMER1=...
NEW_API_KEY_CUSTOMER2=...

# In client
customers = [
    NewAPICustomer(customer_id=1, api_key=env['NEW_API_KEY_CUSTOMER1']),
    NewAPICustomer(customer_id=2, api_key=env['NEW_API_KEY_CUSTOMER2'])
]
```

### **STEP 4: EnvironmentData Integration** 🔧
- [ ] **SAFELY** extend `EnvironmentData.__init__()`:
  - Add `{api_name}_enabled: bool = False` parameter
  - Initialize new API client conditionally
  - **PRESERVE** all existing initialization logic

- [ ] **SAFELY** extend `get_current_readings()`:
  - Add new API data pull after existing sources
  - Transform data using schema mapping method
  - Merge with existing data before consolidation
  - **PRESERVE** existing Coris data flow unchanged

- [ ] **SAFELY** extend data validation:
  - Update `clean_validate_sensors()` with new column types
  - Ensure union logic works (one ID populated per row)

### **STEP 5: Unified Entry Point** 🚀
- [ ] Update `ingest_all_sources.py` configuration reading
- [ ] Test new API integration in unified script
- [ ] Ensure <15 minute completion time maintained
- [ ] Verify graceful failure handling

### **STEP 6: Container Integration** 🐳
- [ ] Update `Dockerfile` to copy new client file
- [ ] Add new environment variables to Docker setup
- [ ] Update `requirements.txt` if new dependencies needed
- [ ] Test Docker build and container execution

### **STEP 7: Comprehensive Testing** 🧪
- [ ] **MANDATORY**: Verify all existing tests still pass
- [ ] Create unit tests for new API client (with mocked responses)
- [ ] Test schema transformation logic
- [ ] Test data merging with existing sources
- [ ] Test individual failure scenarios
- [ ] Integration test: full data flow end-to-end

**Test template:**
```python
# test/test_{api_name}_integration.py
class TestNewAPIClient(unittest.TestCase):
    @patch('requests.get')
    def test_api_client_success(self, mock_get):
        # Mock API response
        # Test client functionality
        pass
        
    def test_schema_transformation(self):
        # Test data mapping to common schema
        pass
        
    def test_failure_handling(self):
        # Test graceful error handling
        pass
```

### **STEP 8: Documentation Updates** 📚
- [ ] Update main `README.md` with new API details
- [ ] Document new environment variables
- [ ] Update Docker setup instructions
- [ ] Add API-specific notes and limitations
- [ ] Update this guide for future integrations

## Safety Protocols

### **Critical Rules:**
1. **NEVER** modify existing data files directly
2. **ALWAYS** preserve existing Coris functionality unchanged
3. **ALWAYS** test in isolation before integration
4. **NEVER** change existing column names or data types
5. **ALWAYS** ensure existing tests pass after each change
6. **NEVER** commit API keys to version control

### **Data Schema Compatibility:**
- Downstream DBT models and Grafana dashboards depend on exact schema
- Any schema changes must be **additive only** (new nullable columns)
- Temperature must always be in °F (convert if needed)
- Time must always be Unix UTC timestamps
- Preserve all 66 existing Coris columns

### **Rollback Preparedness:**
- Document all changes for quick rollback
- Maintain existing functionality as fallback
- Test thoroughly in staging environment first

## Common Patterns

### **API Authentication Patterns:**
- **Header-based**: `headers = {'Authorization': f'Bearer {token}'}`
- **Query parameter**: `params = {'api_key': api_key}`
- **Custom header**: `headers = {'x-api-key': api_key}`

### **Data Retrieval Patterns:**
- **Direct API**: Single request returns data
- **Export/Download**: Request export → poll status → download
- **Pagination**: Multiple requests with offset/cursor
- **Streaming**: WebSocket or Server-Sent Events

### **Error Handling Patterns:**
```python
try:
    data = api_call()
    if data is None:
        logger.warning("No data available")
        return None
except requests.exceptions.RequestException as e:
    logger.error(f"API request failed: {e}")
    return None  # Graceful failure
```

## Testing Strategies

### **Mock API Responses:**
- Create realistic test data matching API format
- Test success cases, empty responses, and error conditions
- Use `unittest.mock.patch` for external API calls

### **Schema Validation:**
- Verify all required columns exist
- Check data types match specification
- Test union logic (only one ID populated per row)
- Validate temperature conversions and time mapping

### **Integration Testing:**
- Test full pipeline: API → transformation → merge → Parquet
- Verify existing tests continue to pass
- Test container execution in both development and production modes

## Troubleshooting

### **Common Issues:**
- **Schema mismatch**: Ensure all DataFrames have compatible types for merging
- **Authentication errors**: Verify API keys and permissions
- **Timeout issues**: Implement appropriate timeouts and retries
- **Data type conflicts**: Use explicit casting in schema transformation
- **Memory issues**: Process data in chunks for large datasets

### **Debugging Tips:**
- Enable debug logging for detailed API interaction logs
- Use Jupyter notebooks for interactive testing and data exploration
- Test with small date ranges first before full historical backfill
- Verify API responses manually using tools like Postman or curl

## Success Criteria

- [ ] All existing tests pass
- [ ] New API data successfully integrated
- [ ] Container completes full cycle in <15 minutes
- [ ] Downstream tools (DBT/Grafana) continue working unchanged
- [ ] Comprehensive test coverage for new functionality
- [ ] Documentation updated and comprehensive

---

**Contact**: For questions or issues during integration, consult the project maintainer. Never contact API vendors directly - all vendor communication should go through the project owner. 