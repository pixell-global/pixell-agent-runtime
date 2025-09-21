# Pixell Ecosystem Architecture

## Overview

The Pixell ecosystem consists of three distinct layers, each with clear responsibilities and boundaries. Understanding these boundaries is crucial for building compatible components.

## The Three Layers

### 1. PAC (Pixell Agent Cloud) - Infrastructure Layer

**What it is**: The cloud infrastructure and orchestration layer

**Responsibilities**:
- Provisions and manages AWS Fargate tasks
- Allocates compute resources (CPU, RAM)
- Assigns network ports and IP addresses
- Manages load balancing and routing
- Handles scaling and failover
- Monitors resource usage and health
- Routes external traffic to appropriate Fargate tasks

**What PAC does NOT do**:
- Does not know about agent business logic
- Does not execute agent code directly
- Does not understand APKG format

**Key Point**: PAC manages HARDWARE and INFRASTRUCTURE

### 2. PAR (Pixell Agent Runtime) - Runtime Layer

**What it is**: The runtime environment that executes agent applications (like WSGI for Python web apps)

**Responsibilities**:
- Loads and extracts APKG files
- Provides the execution environment for agents
- Implements the standard agent interface/protocol
- Handles HTTP/gRPC server implementation
- Routes requests to appropriate agent methods
- Manages agent lifecycle (start, stop, reload)
- Provides standard services (logging, metrics, security)
- Implements A2A (agent-to-agent) communication

**What PAR does NOT do**:
- Does not implement business logic
- Does not know about specific agent functionality
- Does not manage infrastructure or ports

**Key Point**: PAR is the RUNTIME that executes agents

### 3. Agent Apps - Application Layer

**What it is**: The actual business logic and functionality

**Responsibilities**:
- Implements specific agent capabilities
- Processes requests and returns responses
- Maintains internal state if needed
- Focuses purely on business logic

**What Agent Apps do NOT do**:
- Do not start servers
- Do not manage ports or networking
- Do not handle HTTP/gRPC protocols directly
- Do not care about infrastructure

**Key Point**: Agents are pure BUSINESS LOGIC

## How They Work Together

```
┌─────────────────────────────────────────────────────────┐
│                     PAC (Cloud)                         │
│  "I manage infrastructure and route traffic"            │
│                                                         │
│  - Provisions Fargate task with 4 CPU, 8GB RAM        │
│  - Assigns port 80 for external traffic               │
│  - Routes traffic to Fargate task                     │
└────────────────────┬────────────────────────────────────┘
                     │
                     │ PAC starts PAR process on port 80
                     │
┌────────────────────▼────────────────────────────────────┐
│                     PAR (Runtime)                       │
│  "I load agents and handle requests"                   │
│                                                         │
│  - Receives HTTP requests on port 80                   │
│  - Loads agent APKGs                                   │
│  - Calls agent.execute() when request arrives          │
│  - Returns agent response to client                    │
└────────────────────┬────────────────────────────────────┘
                     │
                     │ PAR calls agent methods
                     │
┌────────────────────▼────────────────────────────────────┐
│                  Agent App                              │
│  "I execute Python code"                                │
│                                                         │
│  def execute(request):                                  │
│      code = request['code']                            │
│      result = run_python(code)                         │
│      return {"output": result}                         │
└─────────────────────────────────────────────────────────┘
```

## Port Management - Who Does What

**PAC decides**: "This Fargate task will listen on port 80"
**PAR implements**: "I'll start an HTTP server on whatever port I'm told"
**Agent doesn't care**: "I just process requests"

The flow:
1. PAC tells Fargate to expose port 80
2. PAC starts PAR process with environment variable or argument: `--port 80`
3. PAR reads this configuration and starts HTTP server on port 80
4. Agent has no idea what port is being used

## Real World Analogy

Think of it like a restaurant:

- **PAC** = The building owner (manages the physical space, utilities, address)
- **PAR** = The restaurant infrastructure (kitchen, tables, serving system)
- **Agent** = The chef's recipes (just the cooking logic)

The chef doesn't care about the restaurant's street address or table layout. They just cook when orders come in.

## Example: Python Agent

### What the Agent Should Look Like

```python
# agent.py
class PythonAgent:
    def __init__(self):
        self.sessions = {}
    
    def execute(self, request):
        """Execute Python code"""
        code = request.get('code')
        session_id = request.get('session_id', 'default')
        
        # Business logic only
        try:
            exec(code, self.sessions.setdefault(session_id, {}))
            return {"status": "success", "output": "Code executed"}
        except Exception as e:
            return {"status": "error", "error": str(e)}
    
    def get_info(self):
        """Return agent information"""
        return {
            "name": "python-agent",
            "version": "1.0.0",
            "capabilities": ["execute", "get_info"]
        }
```

### What PAR Does With It

```python
# Inside PAR (not agent code)
class Runtime:
    def __init__(self, port):
        self.port = port  # Provided by PAC
        self.app = FastAPI()
        
    def load_agent(self, agent_class):
        agent = agent_class()
        
        @self.app.post("/execute")
        def execute(request):
            return agent.execute(request)
        
        @self.app.get("/info")
        def info():
            return agent.get_info()
    
    def start(self):
        # PAR starts the server, not the agent
        uvicorn.run(self.app, port=self.port)
```

## Common Misconceptions

### ❌ Wrong: Agent starts its own server
```python
# WRONG - Agent should not do this
class PythonAgent:
    def start(self):
        app = FastAPI()
        uvicorn.run(app, port=8080)  # NO!
```

### ✅ Right: Agent just implements methods
```python
# RIGHT - Agent is just business logic
class PythonAgent:
    def execute(self, request):
        return {"result": "processed"}
```

### ❌ Wrong: PAR manages infrastructure
```python
# WRONG - PAR should not do this
class Runtime:
    def allocate_port(self):
        return find_free_port()  # NO! PAC does this
```

### ✅ Right: PAR uses what PAC provides
```python
# RIGHT - PAR uses provided configuration
class Runtime:
    def __init__(self, config):
        self.port = config['port']  # Provided by PAC
```

## Summary

1. **PAC** = Cloud infrastructure (WHERE things run)
2. **PAR** = Runtime environment (HOW things run)  
3. **Agent** = Business logic (WHAT runs)

Each layer has clear responsibilities and doesn't concern itself with the other layers' responsibilities. This separation makes the system flexible, scalable, and maintainable.