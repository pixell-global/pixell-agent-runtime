# Pixell Runtime - Architecture & Implementation Steps

## Architecture Overview

### System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                        Pixell Runtime (PAR)                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │   FastAPI    │  │   Package    │  │      Registry         │ │
│  │  Application │  │   Manager    │  │      Client           │ │
│  │              │  │              │  │                       │ │
│  │  - Routes    │  │  - Discovery │  │  - S3/HTTPS Pull     │ │
│  │  - Auth      │  │  - Loading   │  │  - SHA256 Verify     │ │
│  │  - Metrics   │  │  - Mounting  │  │  - Signature Check   │ │
│  └──────────────┘  └──────────────┘  └───────────────────────┘ │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │    Agent     │  │   Runtime    │  │    Observability      │ │
│  │   Router     │  │   Context    │  │                       │ │
│  │              │  │              │  │  - Prometheus Metrics │ │
│  │  - Invoke    │  │  - In-proc   │  │  - JSON Logging      │ │
│  │  - Private   │  │  - Lifecycle │  │  - Usage Metering    │ │
│  └──────────────┘  └──────────────┘  └───────────────────────┘ │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **Package Discovery**: Environment variables or registry index → Package URLs
2. **Package Loading**: Download APKG → Verify → Extract → Parse agent.yaml
3. **Agent Mounting**: Create route mappings → Initialize agent context
4. **Request Handling**: HTTP POST → Auth → Route → Invoke → Response
5. **Monitoring**: Metrics collection → Prometheus export

## Implementation Steps

### Step 1: Project Setup and Dependencies

**Goal**: Create project structure with dependency management

```bash
# Create project structure
mkdir -p src/pixell_runtime/{core,api,registry,agents,metrics,utils}
mkdir -p tests/{unit,integration}
mkdir -p scripts
mkdir -p docs

# Create pyproject.toml for modern Python packaging
```

**Files to create**:
- `pyproject.toml` - Project metadata and dependencies
- `src/pixell_runtime/__init__.py` - Package init
- `requirements.txt` - Pinned dependencies
- `requirements-dev.txt` - Development dependencies

### Step 2: Core Models and Configuration

**Goal**: Define data models and configuration schema

**Files to create**:
- `src/pixell_runtime/core/models.py` - Pydantic models for Agent, Package, etc.
- `src/pixell_runtime/core/config.py` - Configuration management
- `src/pixell_runtime/core/exceptions.py` - Custom exceptions

### Step 3: Registry Client Implementation

**Goal**: Implement package discovery and download functionality

**Files to create**:
- `src/pixell_runtime/registry/client.py` - S3/HTTPS client
- `src/pixell_runtime/registry/validator.py` - SHA256 and signature validation
- `src/pixell_runtime/registry/cache.py` - Local package cache

### Step 4: Package Manager

**Goal**: Handle APKG loading, parsing, and lifecycle

**Files to create**:
- `src/pixell_runtime/core/package_manager.py` - Package discovery and loading
- `src/pixell_runtime/core/agent_loader.py` - Dynamic agent loading
- `src/pixell_runtime/core/agent_registry.py` - In-memory agent registry

### Step 5: FastAPI Application Skeleton

**Goal**: Create basic API structure with health endpoint

**Files to create**:
- `src/pixell_runtime/main.py` - FastAPI app entry point
- `src/pixell_runtime/api/health.py` - Health check endpoint
- `src/pixell_runtime/api/middleware.py` - Auth and metrics middleware

### Step 6: Agent Routing and Invocation

**Goal**: Implement agent invocation endpoints

**Files to create**:
- `src/pixell_runtime/api/agents.py` - Agent invocation routes
- `src/pixell_runtime/core/runtime.py` - Runtime context and in-proc calls
- `src/pixell_runtime/core/router.py` - Agent routing logic

### Step 7: Management API

**Goal**: Add runtime management endpoints

**Files to create**:
- `src/pixell_runtime/api/management.py` - Management endpoints
- `src/pixell_runtime/core/lifecycle.py` - Graceful shutdown handling

### Step 8: Observability

**Goal**: Add metrics, logging, and usage tracking

**Files to create**:
- `src/pixell_runtime/metrics/prometheus.py` - Prometheus metrics
- `src/pixell_runtime/metrics/usage.py` - Usage metering
- `src/pixell_runtime/utils/logging.py` - Structured logging

### Step 9: Authentication and Security

**Goal**: Implement OIDC auth and RBAC

**Files to create**:
- `src/pixell_runtime/api/auth.py` - OIDC token validation
- `src/pixell_runtime/core/rbac.py` - Role-based access control

### Step 10: Testing and Validation

**Goal**: Comprehensive test suite

**Files to create**:
- `tests/conftest.py` - Test fixtures
- `tests/unit/test_package_manager.py` - Unit tests
- `tests/integration/test_api.py` - Integration tests
- `scripts/test_build.sh` - Build verification script

## Build Verification Commands

Each step should be verified with:

```bash
# Check syntax
python -m py_compile src/pixell_runtime/**/*.py

# Run type checking (once mypy is configured)
mypy src/

# Run tests
pytest

# Start development server
uvicorn src.pixell_runtime.main:app --reload
```

## Development Timeline

1. **Week 1**: Steps 1-3 (Setup, Models, Registry Client)
2. **Week 2**: Steps 4-6 (Package Manager, FastAPI, Agent Routing)
3. **Week 3**: Steps 7-8 (Management API, Observability)
4. **Week 4**: Steps 9-10 (Security, Testing)

## Key Implementation Considerations

1. **Error Handling**: Every external call (S3, package loading) must have proper error handling
2. **Concurrency**: Use asyncio for I/O operations, consider threading for CPU-bound package loading
3. **Memory Management**: Implement package unloading to prevent memory leaks
4. **Security**: Sandbox package imports, validate all inputs, scrub logs
5. **Performance**: Cache loaded packages, use connection pooling for S3
6. **Graceful Degradation**: Runtime should start even if some packages fail to load