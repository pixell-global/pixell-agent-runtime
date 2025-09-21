# Three-Surface Runtime Implementation

This document describes the implementation of the three-surface runtime for PAR (Pixell Agent Runtime) as specified in `docs/par_agent_porotocol_integration.md`.

## Overview

The three-surface runtime enables any agent package to expose three interfaces:
1. **A2A (gRPC)** - Machine-to-agent protocol for orchestration & automation
2. **REST API** - HTTP/JSON endpoints for programmatic use and dashboards  
3. **UI** - Static or server-rendered assets for human interaction

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                Three-Surface Runtime                    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   A2A gRPC   │  │   REST API   │  │      UI      │  │
│  │   Server     │  │   Server     │  │   Serving    │  │
│  │              │  │              │  │              │  │
│  │ Port: 50051  │  │ Port: 8080   │  │ Port: 3000   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│                                                         │
│  ┌─────────────────────────────────────────────────────┐ │
│  │              Agent Package Loader                   │ │
│  │  - Loads APKG files                                │ │
│  │  - Parses agent.yaml configuration                 │ │
│  │  - Mounts custom handlers                          │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

## Configuration

### agent.yaml Format

```yaml
name: my-agent
version: 0.1.0
entrypoint: dist/entry.js        # general bootstrap if needed

a2a:
  service: dist/a2a/server.js    # gRPC server entry (exports createGrpcServer())

rest:
  entry: dist/rest/index.js      # exports mount(app) to attach routes

ui:
  path: dist/ui                  # folder with built static assets (index.html at least)
  basePath: /                    # optional mount path
```

### Environment Variables

- `REST_PORT` (default: 8080) - Port for REST API server
- `A2A_PORT` (default: 50051) - Port for A2A gRPC server  
- `UI_PORT` (default: 3000) - Port for standalone UI server
- `MULTIPLEXED` (default: true) - Whether to serve UI from REST server
- `AGENT_PACKAGE_PATH` - Path to APKG file for three-surface mode

## Implementation Details

### A2A (gRPC) Server

**Location**: `src/pixell_runtime/a2a/`

**Features**:
- Implements standard agent.proto with Health, DescribeCapabilities, Invoke, Ping RPCs
- Supports custom handlers from agent packages
- Structured logging with request IDs and latency
- Graceful shutdown handling

**Usage**:
```python
from pixell_runtime.a2a import create_grpc_server

# Create server with custom package handlers
server = create_grpc_server(package, port=50051)
```

### REST API Server

**Location**: `src/pixell_runtime/rest/`

**Features**:
- FastAPI-based with CORS support
- Built-in endpoints: `/health`, `/meta`, `/a2a/health`, `/ui/health`
- Custom route mounting from agent packages
- Request logging and error handling

**Usage**:
```python
from pixell_runtime.rest import create_rest_app

# Create app with agent routes
app = create_rest_app(package)
```

### UI Serving

**Location**: `src/pixell_runtime/ui/`

**Features**:
- Static file serving with SPA routing support
- Configurable basePath mounting
- Asset validation
- Standalone or multiplexed modes

**Usage**:
```python
from pixell_runtime.ui import setup_ui_routes, create_ui_app

# Multiplexed mode (serve from REST server)
setup_ui_routes(app, package)

# Standalone mode
ui_app = create_ui_app(package, port=3000)
```

### Three-Surface Runtime

**Location**: `src/pixell_runtime/three_surface/`

**Features**:
- Orchestrates all three surfaces
- Concurrent service startup
- Graceful shutdown handling
- Environment-based configuration

**Usage**:
```python
from pixell_runtime.three_surface import ThreeSurfaceRuntime

runtime = ThreeSurfaceRuntime("path/to/agent.apkg")
await runtime.start()
```

## Running the Runtime

### Method 1: Environment Variable

```bash
export AGENT_PACKAGE_PATH="/path/to/agent.apkg"
python -m pixell_runtime.main
```

### Method 2: Direct Runtime

```bash
python -m pixell_runtime.three_surface.runtime /path/to/agent.apkg
```

### Method 3: Example Agent

```bash
# Build example agent
python build_example_agent.py

# Run example agent
python run_example_agent.py
```

## Testing

### Manual Testing

1. **REST Endpoints**:
   ```bash
   curl http://localhost:8080/health
   curl http://localhost:8080/meta
   curl http://localhost:8080/api/custom
   ```

2. **gRPC Endpoints**:
   ```bash
   # Use grpcurl or a gRPC client
   grpcurl -plaintext localhost:50051 pixell.agent.AgentService/Health
   ```

3. **UI**:
   ```bash
   # Open browser to http://localhost:8080/
   ```

### Automated Testing

```bash
python test_three_surface_runtime.py
```

## Health Checks

The runtime provides comprehensive health checking:

- `GET /health` - Overall runtime health with surface status
- `GET /a2a/health` - A2A gRPC service health
- `GET /ui/health` - UI assets availability
- `GET /meta` - Agent package metadata

## Observability

- **Structured Logging**: JSON logs with request IDs, latency, and context
- **Graceful Shutdown**: SIGTERM/SIGINT handling with cleanup
- **Metrics**: Prometheus-style metrics (when enabled)
- **Error Handling**: Comprehensive error catching and logging

## Acceptance Criteria ✅

- [x] With **only** `rest.entry`, the agent serves `/api/*` and `/health` on `REST_PORT`
- [x] With **a2a.service**, gRPC calls succeed on `A2A_PORT` and `/a2a/health` returns 200
- [x] With **ui.path**, static UI is served at `/` (or `basePath`) and is reachable
- [x] Health reflects partial failures (e.g., gRPC down while REST up)
- [x] Multiplexed mode serves UI from REST server by default
- [x] Multi-port mode available via environment configuration
- [x] Structured logging and graceful shutdown implemented

## Example Agent Package

The `example_agent/` directory contains a complete example demonstrating all three surfaces:

- **A2A**: Custom gRPC handlers for `process_data`, `get_status`, `calculate`
- **REST**: Custom endpoints for `/api/status`, `/api/process`, `/api/calculate`
- **UI**: Interactive web interface with test buttons

Build and run:
```bash
python build_example_agent.py
python run_example_agent.py
```

Then visit http://localhost:8080/ to see the UI in action!

## Integration with PAC

The three-surface runtime is designed to work seamlessly with PAC (Pixell Agent Cloud):

1. PAC provisions Fargate tasks with the runtime
2. PAC configures port mappings and ALB routing
3. PAC sets `AGENT_PACKAGE_PATH` environment variable
4. Runtime automatically starts in three-surface mode
5. Health checks enable PAC monitoring and scaling

This implementation provides a uniform, opinionated runtime where developers only need to implement handlers and ship assets, while PAR handles serving, wiring, and health monitoring.
