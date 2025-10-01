# Subprocess Isolation Fix Plan

## Problem Statement

Agent subprocesses are failing to start with error:
```
/tmp/pixell-runtime/venvs/.../bin/python: No module named pixell_runtime
```

**Root Cause**: The subprocess tries to run `-m pixell_runtime` using the venv's Python, but `pixell_runtime` (PAR's code) is not installed in the agent's venv.

## Current Architecture

```
┌─────────────────────────────────────────────────────────┐
│ PAR Main Process                                        │
│                                                          │
│  ┌──────────────┐     ┌──────────────┐                 │
│  │ REST API     │     │ A2A Router   │                 │
│  │ :8000        │     │ :50051       │                 │
│  └──────────────┘     └──────────────┘                 │
│                              │                          │
│                              │ Forwards gRPC            │
│                              │ requests to agents       │
└──────────────────────────────┼──────────────────────────┘
                               │
                ┌──────────────┴──────────────┐
                │                             │
     ┌──────────▼───────────┐    ┌───────────▼──────────┐
     │ Agent 1 Subprocess   │    │ Agent 2 Subprocess   │
     │ :50052 (A2A)         │    │ :50053 (A2A)         │
     │ :8001 (REST)         │    │ :8002 (REST)         │
     │                      │    │                      │
     │ Uses venv Python     │    │ Uses venv Python     │
     │ with agent deps      │    │ with agent deps      │
     └──────────────────────┘    └──────────────────────┘
```

## What Works Currently

1. ✅ Deployment manager downloads package
2. ✅ Package loader creates venv with agent dependencies
3. ✅ Venv path is stored in deployment record
4. ✅ Subprocess runner is instantiated with venv path

## What's Broken

**subprocess_runner.py** tries to run:
```python
cmd = [
    "/tmp/venvs/.../bin/python",  # Venv Python
    "-m", "pixell_runtime",         # ❌ Not in venv!
    "--package-path", "...",
    "--rest-port", "8001",
    "--a2a-port", "50052",
    "--ui-port", "3000",
    "--multiplexed"
]
```

This fails because `pixell_runtime` module doesn't exist in the agent's venv.

## Solution Architecture

### Option 1: Create Standalone Agent Runner Script ✅ RECOMMENDED

Create a small Python script that gets installed into each venv and knows how to start the agent's servers.

**Advantages**:
- Clean separation: agents don't need PAR's code
- Agent venvs are truly isolated
- Simple to understand and debug

**Files to Create**:
1. `src/pixell_runtime/agent_entrypoint.py` - Standalone script to start agent servers
2. Update `agents/loader.py` to install this script into each venv

**Files to Modify**:
1. `three_surface/subprocess_runner.py` - Run the entrypoint script instead of `-m pixell_runtime`

### Option 2: Use PAR's Python with Extended PYTHONPATH ❌ NOT RECOMMENDED

Run subprocess with PAR's Python but inject agent's venv into PYTHONPATH.

**Disadvantages**:
- Agent's dependencies might conflict with PAR's
- Defeats the purpose of isolation
- Complex PYTHONPATH manipulation

## Detailed Fix Plan (Option 1)

### Step 1: Create Agent Entrypoint Script

**File**: `src/pixell_runtime/agent_entrypoint.py`

```python
#!/usr/bin/env python3
"""
Standalone entrypoint for running an agent in an isolated subprocess.

This script is installed into each agent's venv and starts the agent's
gRPC and REST servers without requiring PAR's runtime code.
"""

import asyncio
import os
import sys
from pathlib import Path


async def start_agent():
    """Start the agent's gRPC and REST servers."""

    # Get configuration from environment
    package_path = os.environ.get("AGENT_PACKAGE_PATH")
    rest_port = int(os.environ.get("REST_PORT", "8080"))
    a2a_port = int(os.environ.get("A2A_PORT", "50051"))
    ui_port = int(os.environ.get("UI_PORT", "3000"))
    multiplexed = os.environ.get("MULTIPLEXED", "true").lower() == "true"
    base_path = os.environ.get("BASE_PATH", "/")

    if not package_path:
        raise RuntimeError("AGENT_PACKAGE_PATH environment variable required")

    # Add package to sys.path for imports
    sys.path.insert(0, package_path)

    # Load agent manifest
    import yaml
    manifest_path = Path(package_path) / "agent.yaml"
    with open(manifest_path) as f:
        manifest = yaml.safe_load(f)

    # Start gRPC server if configured
    grpc_task = None
    if manifest.get("a2a"):
        a2a_service = manifest["a2a"].get("service")
        if a2a_service:
            module_path, class_name = a2a_service.rsplit(":", 1)

            # Import the agent's gRPC service
            module = __import__(module_path, fromlist=[class_name])
            service_class = getattr(module, class_name)

            # Start gRPC server
            import grpc
            from concurrent import futures

            server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

            # Instantiate and add service
            service_instance = service_class()
            # Add to server using the agent's add_servicer_to_server function
            # This assumes the agent provides a standard gRPC service
            # We'll need to import the agent's pb2_grpc module

            server.add_insecure_port(f"[::]:{a2a_port}")
            server.start()

            print(f"Started A2A gRPC server on port {a2a_port}")
            grpc_task = asyncio.create_task(_wait_for_grpc(server))

    # Start REST server if configured
    rest_task = None
    if manifest.get("rest"):
        rest_entry = manifest["rest"].get("entry")
        if rest_entry:
            module_path, app_name = rest_entry.rsplit(":", 1)

            # Import the agent's FastAPI app
            module = __import__(module_path, fromlist=[app_name])
            app = getattr(module, app_name)

            # Start uvicorn server
            import uvicorn

            config = uvicorn.Config(
                app,
                host="0.0.0.0",
                port=rest_port,
                log_config=None,
                access_log=False,
            )

            server = uvicorn.Server(config)
            print(f"Started REST server on port {rest_port}")
            rest_task = asyncio.create_task(server.serve())

    # Wait for both servers
    tasks = [t for t in [grpc_task, rest_task] if t]
    if tasks:
        await asyncio.gather(*tasks)


async def _wait_for_grpc(server):
    """Wait for gRPC server to stop."""
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(start_agent())
```

### Step 2: Modify Package Loader to Install Entrypoint

**File**: `src/pixell_runtime/agents/loader.py`

In the `_ensure_venv()` method, after installing dependencies:

```python
# After: pip install -r requirements.txt

# Install agent entrypoint script into venv
entrypoint_script = Path(__file__).parent.parent / "agent_entrypoint.py"
pip_install_entrypoint = subprocess.run(
    [str(pip_path), "install", "pyyaml", "uvicorn", "fastapi", "grpcio"],
    capture_output=True,
    text=True,
    timeout=60
)

# Copy entrypoint script to venv bin directory
import shutil
dest_script = venv_path / "bin" / "agent_entrypoint.py"
shutil.copy(entrypoint_script, dest_script)
dest_script.chmod(0o755)
```

### Step 3: Modify Subprocess Runner

**File**: `src/pixell_runtime/three_surface/subprocess_runner.py`

Change the command from:

```python
cmd = [
    str(venv_python),
    "-m", "pixell_runtime",  # ❌ OLD
    ...
]
```

To:

```python
cmd = [
    str(venv_python),
    str(venv_path / "bin" / "agent_entrypoint.py"),  # ✅ NEW
]

env = {
    **subprocess.os.environ,
    "AGENT_PACKAGE_PATH": self.package.path,
    "REST_PORT": str(self.rest_port),
    "A2A_PORT": str(self.a2a_port),
    "UI_PORT": str(self.ui_port),
    "MULTIPLEXED": "true" if self.multiplexed else "false",
    "BASE_PATH": f"/agents/{deployment_id}" if deployment_id else "/",
}
```

## Alternative Simpler Approach (Even Better)

Actually, we can make this even simpler by using Python's `-c` flag to run inline code:

**File**: `src/pixell_runtime/three_surface/subprocess_runner.py`

```python
# Build inline Python code to start agent servers
agent_code = f'''
import sys
sys.path.insert(0, "{self.package.path}")

# Import PAR's server creation functions
# (These need to be in the venv or we need a different approach)
from pixell_runtime.a2a.server import create_grpc_server, start_grpc_server
from pixell_runtime.rest.server import create_rest_app

# Load package manifest
import yaml
from pathlib import Path
manifest_path = Path("{self.package.path}") / "agent.yaml"
with open(manifest_path) as f:
    manifest = yaml.safe_load(f)

# Create minimal package object
class Package:
    path = "{self.package.path}"
    manifest = type('Manifest', (), manifest)()

package = Package()

# Start servers
import asyncio

async def run():
    server = create_grpc_server(package, {self.a2a_port})
    await server.start()
    await server.wait_for_termination()

asyncio.run(run())
'''

cmd = [
    str(venv_python),
    "-c",
    agent_code
]
```

❌ **This won't work either** because it still tries to import `pixell_runtime` modules!

## The Real Solution: Install pixell_runtime Into Venv

After deep analysis, the **simplest and cleanest solution** is:

### Install PAR's runtime code into each agent's venv

**File**: `src/pixell_runtime/agents/loader.py`

In `_ensure_venv()`, after installing agent dependencies:

```python
# Install pixell_runtime itself into the venv
# Use the same source that PAR is using
par_source_dir = Path(__file__).parent.parent.parent  # Go up to src/
pip_install_par = subprocess.run(
    [str(pip_path), "install", "-e", str(par_source_dir)],
    capture_output=True,
    text=True,
    timeout=120
)

if pip_install_par.returncode != 0:
    logger.error("Failed to install pixell_runtime in venv",
                venv=venv_name,
                error=pip_install_par.stderr)
    shutil.rmtree(venv_path)
    raise PackageLoadError(f"Failed to install pixell_runtime: {pip_install_par.stderr}")

logger.info("Installed pixell_runtime in venv", venv=venv_name)
```

## Files That Need to Be Modified

### Only 1 File:

1. **`src/pixell_runtime/agents/loader.py`** - Add pixell_runtime installation to venv

That's it. No other changes needed.

## Why This Is The Right Solution

1. ✅ Minimal changes (only 1 file)
2. ✅ Agent venvs are still isolated (each has its own grpcio, fastapi, etc.)
3. ✅ Reuses existing ThreeSurfaceRuntime code (no duplication)
4. ✅ The subprocess command works as-is
5. ✅ Each agent gets the same PAR version consistency

## Trade-offs

- **Disk space**: Each venv will have PAR installed (~50MB extra per venv)
- **Build time**: +10-20s per venv for installing PAR

These trade-offs are acceptable for correctness and simplicity.

## Testing Plan

1. Modify `agents/loader.py` to install pixell_runtime in venv
2. Redeploy PAR to ECS
3. Trigger a new deployment
4. Verify subprocess starts successfully
5. Test A2A connectivity

## Success Criteria

- ✅ Agent subprocess starts without "No module named pixell_runtime" error
- ✅ Agent responds to health checks on REST port
- ✅ Agent responds to A2A gRPC requests
- ✅ No conflicts between agent's dependencies and PAR's dependencies
