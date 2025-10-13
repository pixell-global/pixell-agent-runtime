# PAR/PAC API Contract Fixes

**Date**: 2025-10-12
**Status**: ✅ Complete - All 111 Tests Passing

---

## Summary

Fixed PAR supervisor API to match PAC database schema and API contract requirements. All field names now align with PAC's expectations.

---

## Changes Made

### 1. DeployRequest Model (models.py:27-45)

**Added Required Fields:**
- ✅ `version: str` - Package version (required by PAC)
- ✅ `org_id: str` - Organization ID (required by PAC)

**Before:**
```python
class DeployRequest(BaseModel):
    agent_app_id: str
    deployment_id: str
    package_url: str
    # ... other fields
```

**After:**
```python
class DeployRequest(BaseModel):
    agent_app_id: str
    deployment_id: str
    package_url: str
    version: str  # Required by PAC
    org_id: str   # Required by PAC
    # ... other fields
```

---

### 2. UpdateRequest Model (models.py:48-65)

**Changed:**
- ✅ Made `agent_app_id` optional (set from URL path)
- ✅ Added `version: Optional[str]` field

**Before:**
```python
class UpdateRequest(BaseModel):
    agent_app_id: str  # Required
    deployment_id: str
    package_url: str
```

**After:**
```python
class UpdateRequest(BaseModel):
    agent_app_id: Optional[str] = None  # Set from URL path
    deployment_id: str
    package_url: str
    version: Optional[str] = None  # PAC field
```

---

### 3. AgentStatusResponse Model (NEW - models.py:76-90)

**Added New Model:**
- ✅ Created `AgentStatusResponse` for detailed status endpoint
- ✅ Uses `process_id` instead of `pid` (PAC expectation)
- ✅ Includes metrics: uptime, memory, CPU, health

**New Model:**
```python
class AgentStatusResponse(BaseModel):
    agent_app_id: str
    status: str
    process_id: Optional[int] = None  # PAC expects 'process_id'
    uptime_seconds: int = 0
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    ports: Ports
    health: Dict[str, bool]
```

---

### 4. API Endpoints (server.py)

**Changed Endpoint Paths:**

| Old Path | New Path | Method | Notes |
|----------|----------|--------|-------|
| `/agents/deploy` | `/agents` | POST | Deploy new agent |
| `/agents/update` | `/agents/{agent_app_id}` | PUT | Update agent |
| N/A | `/agents/{agent_app_id}/status` | GET | **NEW**: Detailed status |

**Updated Health Endpoint:**
- ✅ Changed response format to match PAC contract
- ✅ Added capacity metrics, system resources

**Before:**
```json
{
  "status": "healthy",
  "service": "supervisor"
}
```

**After (PAC Contract):**
```json
{
  "status": "healthy",
  "agents_running": 5,
  "capacity": {
    "current": 5,
    "max": 20,
    "available": 15
  },
  "disk_free_gb": 45.2,
  "memory_free_mb": 2048,
  "cpu_load": [1.2, 1.5, 1.8]
}
```

---

### 5. Server Endpoint Changes (server.py)

#### Deploy Endpoint (Line 125)
```python
@app.post("/agents", response_model=DeployResponse, status_code=201)
async def deploy_agent(request: DeployRequest):
    # Changed from /agents/deploy to /agents
```

#### Update Endpoint (Line 124)
```python
@app.put("/agents/{agent_app_id}", response_model=DeployResponse)
async def update_agent(agent_app_id: str, request: UpdateRequest):
    # Changed from POST /agents/update to PUT /agents/{id}
    # agent_app_id comes from URL path
    request.agent_app_id = agent_app_id  # Set from path
```

#### Status Endpoint (Line 269) - NEW
```python
@app.get("/agents/{agent_app_id}/status", response_model=AgentStatusResponse)
async def get_agent_status(agent_app_id: str):
    # NEW endpoint for detailed status with metrics
    # Returns process_id, uptime, memory, CPU, health
```

#### Health Endpoint (Line 62)
```python
@app.get("/health")
async def health():
    # Updated to return PAC-expected format
    # Includes capacity, system metrics
```

---

## Field Name Mappings

### PAC Database → PAR API

| PAC Field | PAR Field | Status | Notes |
|-----------|-----------|--------|-------|
| `agent_app_id` | `agent_app_id` | ✅ Correct | Used consistently |
| `linux_user` | `linux_user` | ✅ Correct | Already correct |
| `rest_port` | `ports.rest` | ✅ Correct | Already correct |
| `a2a_port` | `ports.a2a` | ✅ Correct | Already correct |
| `ui_port` | `ports.ui` | ✅ Correct | Already correct |
| N/A (process ID) | `process_id` | ✅ Fixed | Status endpoint only |

**Key Point**: PAR uses `pid` internally but returns `process_id` in status endpoint to match PAC expectations.

---

## Test Updates

Updated all test fixtures to include new required fields:

### Test Fixtures Changed:
1. ✅ `tests/test_supervisor_state.py` - Added `version` and `org_id` to `deploy_request` fixture
2. ✅ `tests/test_supervisor_models.py` - Updated `test_deploy_request_minimal` and `test_deploy_request_full`
3. ✅ `tests/test_supervisor_integration.py` - Updated endpoint path assertions and model validation tests
4. ✅ `tests/test_supervisor_server.py` - Updated all endpoint tests for new paths

### Test Results:
```bash
$ pytest tests/test_supervisor*.py -v

======================= 111 passed, 40 warnings in 3.10s =======================

✅ All tests passing!
```

---

## API Contract Compliance

### Deploy Request (PAC → PAR)
```json
{
  "agent_app_id": "abc-123",
  "deployment_id": "deploy-789",
  "package_url": "s3://bucket/package.apkg",
  "version": "1.0.0",      ← REQUIRED
  "org_id": "org-123"      ← REQUIRED
}
```

### Deploy Response (PAR → PAC)
```json
{
  "agent_app_id": "abc-123",
  "status": "deploying",
  "ports": {
    "rest": 8081,
    "a2a": 50052,
    "ui": 3001
  },
  "linux_user": "agent_abc123de"
}
```

### Status Response (PAR → PAC)
```json
{
  "agent_app_id": "abc-123",
  "status": "running",
  "process_id": 12345,     ← Uses 'process_id', not 'pid'
  "uptime_seconds": 3600,
  "memory_mb": 256,
  "cpu_percent": 5.2,
  "ports": {
    "rest": 8081,
    "a2a": 50052,
    "ui": 3001
  },
  "health": {
    "rest": true,
    "a2a": true,
    "ui": true
  }
}
```

---

## Files Modified

| File | Lines Changed | Description |
|------|---------------|-------------|
| `src/pixell_runtime/supervisor/models.py` | ~30 | Added fields, new model |
| `src/pixell_runtime/supervisor/server.py` | ~150 | Updated endpoints, added status endpoint |
| `src/pixell_runtime/supervisor/__init__.py` | ~5 | Export AgentStatusResponse |
| `tests/test_supervisor_state.py` | ~10 | Updated fixtures |
| `tests/test_supervisor_models.py` | ~15 | Updated tests |
| `tests/test_supervisor_integration.py` | ~20 | Updated endpoint assertions |
| `tests/test_supervisor_server.py` | ~40 | Updated all endpoint tests |

**Total**: ~270 lines changed across 7 files

---

## Verification Checklist

- [x] DeployRequest accepts `version` and `org_id` fields
- [x] UpdateRequest has `agent_app_id` as optional (set from URL)
- [x] Status endpoint returns `process_id` (not `pid`)
- [x] Endpoint paths match PAC expectations:
  - [x] `POST /agents` (deploy)
  - [x] `PUT /agents/{id}` (update)
  - [x] `GET /agents/{id}/status` (status)
  - [x] `DELETE /agents/{id}` (delete)
- [x] Health endpoint returns capacity and system metrics
- [x] Port fields use correct names: `rest`, `a2a`, `ui`
- [x] All 111 tests passing
- [x] No field name mismatches with PAC database schema

---

## Next Steps

### For Deployment:
1. Deploy updated PAR to EC2 instance
2. Test with PAC integration
3. Verify field names match in production

### For Monitoring:
1. Check PAC logs for any field validation errors
2. Monitor database inserts to `ec2_agent_deployments` table
3. Verify all columns populated correctly

---

## References

- **PAC Database Schema**: `/Users/syum/dev/pixell-agent-cloud/database/migrations/006_ec2_multi_agent.sql`
- **PAR API Contract**: `/Users/syum/dev/pixell-agent-cloud/docs/PAR_API_CONTRACT.md`
- **Supervisor README**: `/Users/syum/dev/pixell-agent-runtime/docs/SUPERVISOR_README.md`

---

**Status**: ✅ Complete
**Tests**: ✅ 111/111 Passing
**Ready for Deployment**: ✅ Yes
