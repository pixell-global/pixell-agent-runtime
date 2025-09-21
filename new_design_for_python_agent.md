# New Design for Python Agent - PAR-Compatible Architecture

## Overview

The current Python agent has a client/server split that doesn't align well with the PAR (Process-per-Agent Runtime) model. This document proposes a redesigned architecture that maintains all the agent's capabilities while running as a single, self-contained process.

## PAR Architecture Context

### ✅ Correct Architecture
- **Multiple PAR instances** run within one Fargate task
- Each PAR instance hosts **exactly one** APKG file
- Each agent runs in complete isolation
- A **supervisor/router** process manages PAR instances

### Deployment Architecture

```
┌─────────────────────────────────────────────────┐
│          Fargate Task (4 vCPU, 8GB RAM)         │
├─────────────────────────────────────────────────┤
│                                                 │
│  ┌─────────────────────────────────────────┐   │
│  │    Agent Router/Supervisor Process       │   │
│  │    (Port 80/443 - Public facing)         │   │
│  └─────────────┬───────────────────────────┘   │
│                │                                │
│    ┌───────────┴───────────┬──────────┐        │
│    ▼                       ▼          ▼        │
│ ┌──────────────┐  ┌──────────────┐  ┌────┐    │
│ │ PAR Instance │  │ PAR Instance │  │... │    │
│ │ Agent A      │  │ Agent B      │  │    │    │
│ │ Port: 8001   │  │ Port: 8002   │  │    │    │
│ │ CPU: 0.25    │  │ CPU: 0.5     │  │    │    │
│ │ RAM: 512MB   │  │ RAM: 1GB     │  │    │    │
│ └──────────────┘  └──────────────┘  └────┘    │
│                                                 │
└─────────────────────────────────────────────────┘
```

### Key Architecture Principles

1. **Process Isolation**: Each agent runs in its own OS process
2. **Resource Limits**: Each PAR instance has defined CPU/RAM limits
3. **Port Assignment**: Each agent gets a unique port (8001, 8002, etc.)
4. **Supervisor Routing**: All external traffic goes through supervisor
5. **A2A Communication**: Agents can communicate directly via gRPC

## Current Architecture Issues

1. **Unnecessary Split**: The agent is split into:
   - A gRPC server (`main.py`) that runs separately
   - An adapter (`pixell_adapter.py`) that connects to the server
   - This creates unnecessary complexity and network overhead

2. **PAR Incompatibility**: PAR expects agents to be self-contained processes that:
   - Start on a designated port
   - Handle requests directly
   - Don't require external services

## Proposed Architecture

### Single Process Design

```
PAR Worker Process (Single APKG Instance)
├── FastAPI HTTP Server (Port 8001)
├── gRPC Server (Port 18001)
└── Python Execution Engine
    ├── Session Manager
    ├── Code Analyzer
    └── Direct Executor (no Docker)
```

### How It Fits in PAR Architecture

```
Supervisor (Port 80/443)
    │
    ├─→ PAR Instance: Python Agent (Port 8001)
    │   ├── HTTP: /exports/execute
    │   ├── HTTP: /exports/get_info
    │   └── gRPC: localhost:18001 (for A2A)
    │
    └─→ PAR Instance: Another Agent (Port 8002)
        ├── HTTP endpoints
        └── gRPC: localhost:18002
```

### Key Changes

1. **Unified Entry Point**
   - Combine HTTP and gRPC servers in one process
   - Start both when the agent loads
   - No client/server split

2. **Direct Execution** (for PAR mode)
   - Execute Python code directly in the process
   - Use subprocess or restricted exec() for isolation
   - Keep Docker as optional for high-security mode

3. **Dual Protocol Support**
   - HTTP endpoints for PAR compatibility
   - gRPC for high-performance A2A calls
   - Same execution engine serves both

## New File Structure

```
pixell-python-agent/
├── agent.yaml                    # Updated with new structure
├── src/
│   ├── __init__.py
│   ├── main.py                  # New unified entry point
│   ├── servers/
│   │   ├── __init__.py
│   │   ├── http_server.py      # FastAPI endpoints
│   │   └── grpc_server.py      # gRPC service
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── executor.py          # Unified executor
│   │   ├── direct_executor.py   # Direct Python execution
│   │   ├── docker_executor.py   # Docker-based (optional)
│   │   ├── patterns.py          # Code analysis
│   │   └── context.py           # Execution context
│   ├── sessions/
│   │   ├── __init__.py
│   │   └── manager.py           # Session state
│   ├── a2a/
│   │   ├── __init__.py
│   │   ├── python_agent.proto   # Keep existing proto
│   │   └── client.py            # A2A client for calling others
│   └── utils/
│       ├── __init__.py
│       └── security.py          # Sandbox utilities
```

## Implementation Details

### 1. New main.py

```python
"""Unified entry point for PAR-compatible Python agent."""

import asyncio
import os
from concurrent import futures

import grpc
import uvicorn
from fastapi import FastAPI

from .servers.http_server import create_http_app
from .servers.grpc_server import PythonAgentServicer
from .engine.executor import ExecutionEngine
from .sessions.manager import SessionManager
from .a2a import python_agent_pb2_grpc


async def main():
    """
    Main entry point for Python agent.
    The agent simply starts its servers and lets PAR handle:
    - Port assignment (PAR will provide the port)
    - Routing (PAR supervisor handles incoming requests)
    - Service discovery (PAR knows where all agents are)
    """
    # Initialize shared components
    session_manager = SessionManager()
    execution_engine = ExecutionEngine(session_manager)
    
    # Create HTTP server (FastAPI)
    app = create_http_app(execution_engine, session_manager)
    
    # Note: PAR will tell us which port to use when it starts this process
    # The agent doesn't need to know or care about port assignment
    # That's PAR's responsibility
    
    print("Python Agent starting...")
    
    # Just start the server - PAR handles the rest
    await app.start()


if __name__ == "__main__":
    # This will be invoked by PAR when loading the APKG
    asyncio.run(main())
```

### 2. HTTP Server (FastAPI)

```python
"""HTTP server for PAR compatibility."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class ExecuteRequest(BaseModel):
    code: str
    session_id: str = "default"
    timeout: int = 60


def create_http_app(execution_engine, session_manager):
    app = FastAPI(title="Python Agent")
    
    @app.get("/health")
    async def health():
        return {"status": "healthy", "sessions": len(session_manager.sessions)}
    
    @app.post("/exports/execute")
    async def execute(request: ExecuteRequest):
        result = await execution_engine.execute(
            code=request.code,
            session_id=request.session_id,
            timeout=request.timeout
        )
        return result.to_dict()
    
    @app.post("/exports/get_info")
    async def get_info():
        return {
            "name": "pixell-python-agent",
            "version": "2.0.0",
            "capabilities": ["direct-execution", "session-management", "a2a-grpc"]
        }
    
    return app
```

### 3. Unified Executor

```python
"""Unified execution engine supporting multiple modes."""

import os
from .direct_executor import DirectExecutor
from .docker_executor import DockerExecutor


class ExecutionEngine:
    def __init__(self, session_manager):
        self.session_manager = session_manager
        
        # Choose executor based on environment
        if os.environ.get("PAR_MODE") == "worker":
            # In PAR, use direct execution
            self.executor = DirectExecutor()
        else:
            # Standalone mode can use Docker
            self.executor = DockerExecutor()
    
    async def execute(self, code, session_id, timeout=60, **kwargs):
        # Get session
        session = self.session_manager.get_session(session_id)
        
        # Analyze code patterns
        patterns = self._analyze_patterns(code)
        
        # Execute with appropriate resource tier
        result = await self.executor.execute(
            code=code,
            context=session.get_context(),
            timeout=timeout,
            patterns=patterns
        )
        
        # Update session state
        if result.success:
            session.update(code, result)
        
        return result
```

### 4. Direct Executor (New)

```python
"""Direct Python execution for PAR environments."""

import asyncio
import sys
import io
import traceback
import resource
import signal
from contextlib import contextmanager


class DirectExecutor:
    """Execute Python code directly with safety measures."""
    
    async def execute(self, code, context, timeout, **kwargs):
        # Set resource limits
        with self._resource_limits():
            return await self._execute_sandboxed(code, context, timeout)
    
    @contextmanager
    def _resource_limits(self):
        """Apply resource limits for safety."""
        # Limit memory (1GB)
        resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
        
        # Limit CPU time
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
        
        yield
        
    async def _execute_sandboxed(self, code, context, timeout):
        """Execute code with timeout and output capture."""
        # Create globals with context
        globals_dict = {"__builtins__": __builtins__}
        globals_dict.update(context)
        
        # Capture output
        stdout = io.StringIO()
        stderr = io.StringIO()
        
        # Execute with timeout
        try:
            with self._capture_output(stdout, stderr):
                exec(code, globals_dict)
            
            return ExecutionResult(
                success=True,
                stdout=stdout.getvalue(),
                stderr=stderr.getvalue(),
                results=self._extract_results(globals_dict, context)
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                error=str(e),
                stdout=stdout.getvalue(),
                stderr=stderr.getvalue() + traceback.format_exc()
            )
```

## Benefits of New Design

1. **PAR Compatibility**
   - Single process per agent
   - No external dependencies
   - Direct port binding

2. **Performance**
   - No internal network calls
   - Shared memory for session state
   - Direct execution path

3. **Flexibility**
   - Supports both HTTP and gRPC
   - Can switch execution modes
   - Maintains all original features

4. **Simplicity**
   - No client/server split
   - Easier to deploy
   - Single configuration

## Migration Path

1. **Phase 1**: Create new unified main.py
2. **Phase 2**: Refactor executors for dual-mode
3. **Phase 3**: Update agent.yaml configuration
4. **Phase 4**: Test in PAR environment
5. **Phase 5**: Add Docker mode as optional

## Configuration Changes

### agent.yaml
```yaml
version: "2.0.0"
name: "pixell-python-agent"
entrypoint: "src.main:main"  # Single entry point

# Runtime modes
modes:
  par:
    executor: "direct"
    security: "sandbox"
  standalone:
    executor: "docker"
    security: "container"

# Export both protocols
exports:
  - name: "execute"
    handler: "http"
    path: "/exports/execute"
  - name: "execute_grpc"
    handler: "grpc"
    service: "PythonAgent"
```

## Security Considerations

1. **Direct Execution Safety**
   - Resource limits (memory, CPU)
   - Restricted imports
   - No filesystem access by default
   - Process isolation via subprocess

2. **Optional Security Levels**
   - Level 1: Direct with restrictions
   - Level 2: Subprocess isolation
   - Level 3: Docker containers

## Building an APKG for PAR

To create a PAR-compatible APKG:

1. **Structure**: Follow the single-process design
2. **Entry Point**: Use the unified main.py
3. **Dependencies**: Include all Python dependencies in requirements.txt
4. **Manifest**: Update agent.yaml with proper entrypoint

### Example Build Script

```bash
#!/bin/bash
# build_apkg.sh

# Create temp directory
mkdir -p build/pixell-python-agent

# Copy source files
cp -r src/ build/pixell-python-agent/
cp agent.yaml build/pixell-python-agent/
cp requirements.txt build/pixell-python-agent/

# Create APKG
cd build
tar -czf pixell-python-agent-2.0.0.apkg pixell-python-agent/

# Sign the package (if required)
# sha256sum pixell-python-agent-2.0.0.apkg > pixell-python-agent-2.0.0.apkg.sha256

echo "APKG created: pixell-python-agent-2.0.0.apkg"
```

## Conclusion

This redesign makes the Python agent truly compatible with PAR while maintaining all its advanced features. The unified architecture is simpler, more efficient, and better aligned with the PAR philosophy of self-contained agent processes. 

Key takeaways for agent developers:
- **One Process**: Each agent must be a single, self-contained process
- **Dual Protocol**: Support HTTP for PAR routing and gRPC for A2A
- **Direct Execution**: Use the runtime's resources directly
- **PAR Environment**: Respect PAR environment variables and conventions