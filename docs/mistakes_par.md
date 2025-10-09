# PAR Implementation Gaps and Potential Mistakes

This document identifies gaps, inconsistencies, and potential mistakes in the PAR implementation when compared against `implementation_steps.md` and `request_response_trace.md`.

## Critical Gaps

### 1. **PACKAGE_URL Environment Variable Not Implemented** ✅ FIXED
**Status**: Implemented  
**Spec**: `request_response_trace.md` line 12, 34, 144  
**Implementation**: 
- `ThreeSurfaceRuntime` now reads `PACKAGE_URL` env var and downloads APKG from s3:// or https://
- Supports optional `PACKAGE_SHA256` for validation
- Configurable `MAX_PACKAGE_SIZE_MB` (default 100MB)
- URL validation blocks file:// and other insecure protocols
- Automatic cleanup of downloaded packages on shutdown
- Tests added for HTTPS, S3, SHA256 validation, and security checks

**Files Modified**:
- `src/pixell_runtime/three_surface/runtime.py` - Added download logic and validation
- `tests/test_package_url.py` - Comprehensive test coverage

### 2. **AGENT_APP_ID Not Validated as Required** ✅ FIXED
**Status**: Implemented  
**Spec**: `implementation_steps.md` line 24: "Hard-fail if `AGENT_APP_ID` missing or empty"  
**Implementation**:
- `ThreeSurfaceRuntime` now validates `AGENT_APP_ID` on initialization
- Exits with code 1 if missing or empty/whitespace-only
- Stores `agent_app_id` as runtime attribute
- Binds to logging context for correlation
- `DEPLOYMENT_ID` remains optional but recommended
- Comprehensive test coverage (18 tests)

**Files Modified**:
- `src/pixell_runtime/three_surface/runtime.py` - Added validation in `__init__`
- `tests/test_agent_app_id_validation.py` - 18 comprehensive tests
- `tests/conftest.py` - Auto-fixture to set default AGENT_APP_ID for tests

### 3. **No Config Validation on Startup (Step 0)** ✅ FIXED
**Status**: Implemented  
**Spec**: `implementation_steps.md` Step 0 - Config parsing and validation  
**Implementation**:
- Created `RuntimeConfig` class with comprehensive validation
- Validates all environment variables on startup
- Fails fast with clear error messages
- Collects multiple errors before exiting
- Comprehensive test coverage (46 tests)

**Validations Implemented**:
- ✅ `AGENT_APP_ID` required (non-empty, non-whitespace)
- ✅ Ports numeric and valid (1-65535, no port 0, no conflicts)
- ✅ `AWS_REGION` format validation (warns on invalid format)
- ✅ `S3_BUCKET` name validation (3-63 chars, valid characters)
- ✅ `PACKAGE_URL` protocol validation (https:// or s3:// only)
- ✅ `PACKAGE_SHA256` format validation (64 hex characters)
- ✅ `MAX_PACKAGE_SIZE_MB` numeric and positive
- ✅ `BASE_PATH` normalization and validation
- ✅ `MULTIPLEXED` boolean parsing
- ✅ Port conflict detection

**Files Modified**:
- `src/pixell_runtime/core/runtime_config.py` - New comprehensive config class
- `src/pixell_runtime/three_surface/runtime.py` - Integrated RuntimeConfig
- `tests/test_runtime_config.py` - 46 comprehensive tests
- `tests/test_package_url.py` - Fixed SHA256 test to use valid format

### 4. **Wheelhouse Cache Not Implemented** ✅ FIXED
**Status**: Fully implemented  
**Spec**: `implementation_steps.md` line 91, `request_response_trace.md` line 190  
**Implementation**:
- Created `WheelhouseManager` class for comprehensive wheelhouse management
- Integrated with `PackageLoader` for automatic wheelhouse usage
- Supports both online (with PyPI fallback) and offline modes
- Comprehensive test coverage (40 tests: 32 unit + 8 integration)

**Features Implemented**:
- ✅ Wheelhouse directory validation
- ✅ Automatic detection from `WHEELHOUSE_DIR` env var
- ✅ Wheel file discovery and package name extraction
- ✅ Pip install argument generation (--find-links, optional --no-index)
- ✅ Package download to wheelhouse (pip download)
- ✅ Cache info and statistics (size, package count, etc.)
- ✅ Cache clearing functionality
- ✅ Graceful fallback when wheelhouse unavailable
- ✅ Online mode (PyPI fallback) by default
- ✅ Package name normalization (underscore -> hyphen)
- ✅ Multiple package versions support
- ✅ Permission error handling

**Benefits**:
- Faster cold starts (cached wheels)
- Reduced network failures during pip install
- Optional offline install capability
- Better visibility into cached packages

**Files Modified**:
- `src/pixell_runtime/core/wheelhouse.py` - New comprehensive wheelhouse manager
- `src/pixell_runtime/agents/loader.py` - Integrated WheelhouseManager
- `tests/test_wheelhouse.py` - 32 comprehensive unit tests
- `tests/test_wheelhouse_integration.py` - 8 integration tests with PackageLoader

---

## Architectural Inconsistencies

### 5. **Runtime Still Has DeploymentManager** ✅ FIXED
**Status**: Control-plane code removed from data-plane execution path  
**Spec**: PAR should NOT manage deployments, only execute them  
**Implementation**:
- Removed DeploymentManager imports from runtime entry points
- Marked deploy/manager.py as DEPRECATED with clear warnings
- Updated __main__.py to only support single-agent execution
- Removed multi-agent server mode from default execution path
- Comprehensive test coverage (20 tests)

**Changes Made**:
- ✅ Removed DeploymentManager import from `__main__.py`
- ✅ Removed `par status` command (control-plane functionality)
- ✅ Removed default server mode (multi-agent management)
- ✅ PAR now only runs with `par run <package>` or `AGENT_PACKAGE_PATH` env var
- ✅ Marked `deploy/manager.py` as DEPRECATED with warnings
- ✅ Updated `deploy/__init__.py` to clearly mark control-plane vs data-plane code
- ✅ ThreeSurfaceRuntime does not import or use DeploymentManager
- ✅ PackageLoader does not import or use DeploymentManager

**Architecture Enforcement**:
- PAR is now strictly a single-agent runtime
- All deployment management moved to PAC (Pixell Agent Cloud)
- Only data-plane code (PackageLoader, fetch) used in runtime path
- Control-plane code (DeploymentManager, API routes) marked as legacy

**Files Modified**:
- `src/pixell_runtime/__main__.py` - Removed DeploymentManager, server mode
- `src/pixell_runtime/deploy/__init__.py` - Added deprecation warnings
- `src/pixell_runtime/deploy/manager.py` - Added deprecation header
- `tests/test_no_deployment_manager.py` - 20 comprehensive tests

**Test Coverage**:
- No DeploymentManager imports in runtime path
- No deployment API routes in runtime
- No multi-deployment state management
- No port allocation logic (control-plane)
- No service discovery registration (control-plane)
- Single-agent execution model enforced
- Environment variable configuration only
- Forbidden imports prevented

### 6. **Subprocess Runner Pattern Still Exists** ✅ FIXED
**Status**: Marked as deprecated and not used in runtime path  
**Spec**: In new architecture, each agent runs in its own container, not as subprocess  
**Implementation**:
- Marked `SubprocessAgentRunner` as DEPRECATED with comprehensive warnings
- Documented old vs new execution models
- Verified not imported or used in runtime execution path
- Comprehensive test coverage (19 tests)

**Old Model (Deprecated)**:
- PAR spawns agents as subprocesses
- Each subprocess has its own venv
- PAR manages multiple agent processes
- Process lifecycle management in PAR

**New Model (Current)**:
- Each agent runs in its own container/ECS task
- PAR is the container entrypoint
- Runs one agent per container directly in-process
- ECS/Kubernetes manages container lifecycle
- No subprocess spawning needed

**Changes Made**:
- ✅ Added comprehensive deprecation warning to `subprocess_runner.py`
- ✅ Documented old vs new execution models
- ✅ Marked subprocess_runner usage in DeploymentManager as deprecated
- ✅ Verified ThreeSurfaceRuntime doesn't import SubprocessAgentRunner
- ✅ Verified __main__.py doesn't use subprocess pattern
- ✅ Verified PackageLoader doesn't spawn subprocesses

**Architecture Enforcement**:
- Container-based execution model
- Direct in-process agent execution
- No process management in runtime
- No subprocess spawning for agents
- Venv isolation without subprocess

**Files Modified**:
- `src/pixell_runtime/three_surface/subprocess_runner.py` - Added deprecation warnings
- `src/pixell_runtime/deploy/manager.py` - Marked subprocess_runner as deprecated
- `tests/test_no_subprocess_runner.py` - 19 comprehensive tests

**Test Coverage**:
- SubprocessAgentRunner not imported in runtime
- Runtime uses direct execution, not subprocess
- No process management in runtime
- Container execution model enforced
- Single agent per process
- No subprocess wait patterns
- No log forwarding from subprocess
- Deprecated code properly marked

---

## Missing Features from Spec

### 7. **No Boot Time Budget Enforcement** ⚠️ LOW
**Status**: Partially implemented  
**Spec**: `implementation_steps.md` line 176 - "soft budget to alert on regressions"  
**Current**: Boot metrics exist but no enforcement or alerting

**Fix Required**:
Add `BOOT_BUDGET_MS` check that logs WARNING (already done in Step 9) but also consider exiting if boot is extremely slow (e.g., 10x budget).

### 8. **No Retry Backoff Circuit Breaker** ⚠️ MEDIUM
**Status**: Missing  
**Spec**: `implementation_steps.md` line 164 - "sleep/backoff before exit to avoid hot-restart loops"  
**Current**: Runtime exits immediately on failure

**Impact**:
- ECS might restart container in tight loop
- Wastes resources
- Makes debugging harder

**Fix Required**:
```python
# On unrecoverable error:
retry_count = int(os.getenv("BOOT_RETRY_COUNT", "0"))
if retry_count > 3:
    sleep_sec = min(60, 2 ** retry_count)
    logger.warning(f"Boot failed, backing off {sleep_sec}s before exit")
    time.sleep(sleep_sec)
os.environ["BOOT_RETRY_COUNT"] = str(retry_count + 1)
sys.exit(1)
```

### 9. **No SHA256 Validation in Runtime** ✅ FIXED
**Status**: Implemented as part of PACKAGE_URL support  
**Spec**: `request_response_trace.md` line 148, 171  
**Implementation**: Runtime now reads `PACKAGE_SHA256` env var and passes it to `fetch_package_to_path` for validation

### 10. **No Max Package Size Enforcement** ✅ FIXED
**Status**: Implemented as part of PACKAGE_URL support  
**Spec**: `implementation_steps.md` line 60 - "Enforce a max package size"  
**Implementation**: Runtime now reads `MAX_PACKAGE_SIZE_MB` env var (default 100MB) and passes it to `fetch_package_to_path`

---

## Testing Gaps

### 11. **No End-to-End Container Test** ⚠️ HIGH
**Status**: Missing  
**Spec**: `implementation_steps.md` line 195 - "run runtime container with a sample APKG"  
**Current**: All tests run in-process, none test actual container execution

**Impact**:
- Can't verify container entrypoint works
- Can't test actual ECS deployment scenario
- Environment variable handling might break in container

**Fix Required**:
Add Docker-based integration test:
```bash
docker build -t par:test .
docker run -e AGENT_APP_ID=test -e PACKAGE_URL=s3://... par:test
```

### 12. **No Network Isolation Test** ⚠️ MEDIUM
**Status**: Missing  
**Spec**: `implementation_steps.md` line 196 - "ensure no cloud/AWS calls beyond S3 GetObject"  
**Current**: `test_no_control_plane_operations` mocks boto3 but doesn't test network

**Fix Required**:
Add test that runs runtime in network-restricted environment and verifies only S3 GetObject is called.

---

## Documentation Gaps

### 13. **No Dockerfile or Container Entrypoint** ⚠️ CRITICAL
**Status**: Missing  
**Spec**: PAR must run as container in ECS  
**Current**: No Dockerfile, no entrypoint script

**Impact**:
- Cannot deploy to ECS
- No clear way to run PAR as container

**Fix Required**:
Create:
- `Dockerfile` with Python base image
- `entrypoint.sh` that:
  1. Validates env vars
  2. Downloads APKG if PACKAGE_URL provided
  3. Starts ThreeSurfaceRuntime
  4. Handles signals gracefully

### 14. **No IAM Role Documentation** ⚠️ MEDIUM
**Status**: Missing  
**Spec**: `request_response_trace.md` line 169 - "runtime role must allow S3 GetObject"  
**Current**: No documentation of required IAM permissions

**Fix Required**:
Document minimum IAM policy:
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["s3:GetObject"],
    "Resource": ["arn:aws:s3:::pixell-agent-packages/*"]
  }]
}
```

### 15. **No Health Check Configuration Guide** ⚠️ LOW
**Status**: Incomplete  
**Spec**: `request_response_trace.md` line 75 - "ALB health check path must match"  
**Current**: `/health` endpoint exists but no documentation of ALB configuration

**Fix Required**:
Document ALB target group health check settings:
- Path: `/health`
- Interval: 30s
- Timeout: 5s
- Healthy threshold: 2
- Unhealthy threshold: 3

---

## Environment Variable Inconsistencies

### 16. **Inconsistent Env Var Names** ⚠️ LOW
**Status**: Minor inconsistencies  
**Spec**: `request_response_trace.md` line 33-34  
**Issues**:
- `MULTIPLEXED` not in spec (should it be `UI_MULTIPLEXED`?)
- `PAR_INSTALL_SELF_IN_VENV` not in spec
- `BOOT_TEST_DELAY_MS` is test-only but not clearly marked

**Fix Required**:
- Prefix test-only vars with `TEST_` or `DEBUG_`
- Document all env vars in one place
- Ensure consistency with PAC env vars

### 17. **Missing AWS_REGION Usage** ⚠️ LOW
**Status**: Env var read but not used  
**Spec**: `request_response_trace.md` line 34 - Runtime should use `AWS_REGION`  
**Current**: boto3 might use it implicitly, but not explicitly configured

**Fix Required**:
```python
aws_region = os.getenv("AWS_REGION", "us-east-2")
boto3.setup_default_session(region_name=aws_region)
```

---

## Performance and Reliability

### 18. **No Graceful Shutdown Implementation** ⚠️ MEDIUM
**Status**: Partial  
**Spec**: Runtime should handle SIGTERM gracefully for ECS task draining  
**Current**: Signal handlers exist but don't wait for in-flight requests

**Impact**:
- Requests might be dropped during deployment
- gRPC streams might be cut abruptly

**Fix Required**:
Implement proper graceful shutdown:
1. Stop accepting new requests
2. Wait for in-flight requests (with timeout)
3. Close gRPC streams gracefully
4. Exit

### 19. **No Health Check Timeout** ⚠️ LOW
**Status**: Missing  
**Spec**: Runtime should mark unhealthy if startup takes too long  
**Current**: Runtime waits indefinitely for readiness

**Fix Required**:
Add `STARTUP_TIMEOUT_SEC` (default 300) and exit if not ready in time.

### 20. **No Memory Limit Awareness** ⚠️ LOW
**Status**: Missing  
**Spec**: Runtime should respect container memory limits  
**Current**: No checks for memory usage

**Fix Required**:
- Check available memory before venv creation
- Fail fast if insufficient memory
- Log memory usage at key phases

---

## Security Concerns

### 21. **No Input Validation on PACKAGE_URL** ✅ FIXED
**Status**: Implemented  
**Implementation**: 
- `_validate_package_url()` method blocks file:// URLs (SSRF protection)
- Only allows s3:// and https:// protocols
- Validates S3 URLs against expected bucket (with warning, not hard fail)
- Tests added for all validation scenarios

### 22. **No Manifest Signature Verification** ⚠️ LOW
**Status**: Missing  
**Spec**: Optional but recommended for production  
**Current**: Manifest loaded without signature check

**Fix Required**:
Consider adding manifest signature verification for production deployments.

---

## Summary

### Critical (Must Fix Before Production)
1. ✅ **FIXED** - PACKAGE_URL environment variable implementation
2. ✅ **FIXED** - AGENT_APP_ID validation
3. ✅ **FIXED** - Config validation on startup (ports, AWS_REGION, S3_BUCKET, etc.)
4. ⚠️ Dockerfile and container entrypoint
5. ⚠️ End-to-end container test

### High Priority (Should Fix Soon)
6. ✅ **FIXED** - Remove DeploymentManager from runtime
7. ✅ **FIXED** - Remove subprocess runner pattern
8. ⚠️ Network isolation test
9. ⚠️ Retry backoff circuit breaker
10. ✅ **FIXED** - Wheelhouse cache implementation
11. ✅ **FIXED** - SHA256 validation in runtime

### Medium Priority (Nice to Have)
12. ✅ **FIXED** - Max package size configuration
13. ⚠️ Graceful shutdown
14. ⚠️ IAM role documentation
15. ✅ **FIXED** - Input validation on PACKAGE_URL

### Low Priority (Future Improvements)
16. ⚠️ Boot time budget enforcement (partially done)
17. ⚠️ Health check configuration guide
18. ⚠️ Memory limit awareness
19. ⚠️ Env var naming consistency

---

## Recommendations

1. ✅ **COMPLETED**: Implement PACKAGE_URL support - this was blocking ECS deployment
2. **Phase 1 (Next)**: Add config validation and fail-fast behavior (AGENT_APP_ID, ports, etc.)
3. **Phase 2**: Create Dockerfile and container tests
4. **Phase 3**: Clean up architectural inconsistencies (DeploymentManager, subprocess runner)
5. **Phase 4**: Add production hardening (graceful shutdown, retry backoff)

## What Was Fixed

### PACKAGE_URL Implementation
- ✅ Runtime now reads `PACKAGE_URL` from environment
- ✅ Downloads APKGs from s3:// or https:// URLs
- ✅ SHA256 validation via `PACKAGE_SHA256` env var
- ✅ Configurable max size via `MAX_PACKAGE_SIZE_MB` env var (default 100MB)
- ✅ URL validation blocks file:// and other insecure protocols
- ✅ S3 bucket validation (warns if not expected bucket)
- ✅ Automatic cleanup of downloaded packages on shutdown
- ✅ Comprehensive test coverage (11 tests)
- ✅ Retries with exponential backoff (via existing fetch.py)
- ✅ Size and timeout limits enforced

### Security Improvements
- ✅ SSRF protection: file:// URLs blocked
- ✅ Protocol whitelist: only s3:// and https:// allowed
- ✅ SHA256 integrity validation

### Configuration
- ✅ `PACKAGE_URL` - s3:// or https:// URL to APKG
- ✅ `PACKAGE_SHA256` - Optional SHA256 checksum for validation
- ✅ `MAX_PACKAGE_SIZE_MB` - Maximum package size (default 100MB)
- ✅ `S3_BUCKET` - Expected S3 bucket name (default pixell-agent-packages)

## Testing Strategy

After fixes, verify with:
1. Local container run with PACKAGE_URL pointing to S3
2. ECS task definition with all env vars
3. ALB health check integration
4. gRPC health check via NLB
5. Zero-downtime deployment test
6. Failure scenario tests (bad APKG, network timeout, etc.)
