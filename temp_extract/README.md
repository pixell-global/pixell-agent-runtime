# Pixell Python Agent

High-performance Python execution agent with A2A (Agent-to-Agent) protocol support, designed to rival and exceed ChatGPT's Code Interpreter capabilities.

## What This Agent Does

The Pixell Python Agent provides secure, sandboxed Python code execution as a service to other agents in the Pixell ecosystem. Think of it as ChatGPT's Code Interpreter, but faster, more powerful, and designed for agent-to-agent collaboration.

## Features

- **Lightning-fast execution**: <100ms container startup, <150ms to first output
- **A2A Protocol**: Native gRPC interface for seamless agent-to-agent communication
- **Smart error recovery**: Pattern-based error detection with automatic retries
- **Resource tiers**: Small (1CPU/2GB), Medium (2CPU/4GB), Large (4CPU/16GB)
- **Session management**: Stateful execution with 90-minute TTL
- **Real-time streaming**: Bidirectional gRPC streams for progressive results
- **Security**: Container isolation, no network access, read-only filesystem

## Performance Targets

- Cold Start: <100ms
- A2A Latency: <1ms local, <10ms remote
- Throughput: 1000+ executions/second/node
- Memory: 50MB base + data
- p99 Latency: <200ms for simple operations

## Quick Start

### Installation via Pixell Kit

```bash
# Install from registry (when published)
pixell install pixell-python-agent

# Or install from local package
pixell install pixell-python-agent.apkg
```

### Building from Source

```bash
# Clone repository
git clone https://github.com/pixell/pixell-python-agent.git
cd pixell-python-agent

# Install dependencies
pip install -r requirements.txt

# Build everything (protobuf, Docker image, tests, package)
./build.sh
```

### Running the Agent

#### As a Pixell Agent (Recommended)

```bash
# Run via Pixell Kit
echo '{"action": "execute", "code": "print(\"Hello from Python!\")"}' | pixell run pixell-python-agent

# Or use the agent in another agent
pixell list --search "python"  # Find the agent
```

#### As a Standalone A2A Service

```bash
# Start with Unix socket (recommended for local)
python -m src.main

# Start with TCP
python -m src.main --tcp --port 50051

# Enable debug logging
python -m src.main --debug
```

### A2A Integration Example

```python
import grpc
from src.a2a import python_agent_pb2, python_agent_pb2_grpc

# Connect to Python agent
channel = grpc.insecure_channel('unix:///tmp/pixell-python-agent-50051.sock')
stub = python_agent_pb2_grpc.PythonAgentStub(channel)

# Execute code
request = python_agent_pb2.ExecuteRequest(
    code="import pandas as pd\ndf = pd.DataFrame({'a': [1,2,3]})\nprint(df.sum())",
    session_id="test-session",
    resource_tier=python_agent_pb2.MEDIUM
)

response = stub.Execute(request)
print(response.stdout)
```

## How Other Agents Use This

When other agents need to execute Python code, they can call this agent via:

1. **Pixell Kit Integration**: Use standard Pixell agent invocation
2. **Direct A2A Protocol**: Connect via gRPC for low-latency communication
3. **MCP Server**: Use Model Context Protocol for LLM integration

Example from another agent:
```python
# Your agent needs to analyze data
response = pixell.invoke("pixell-python-agent", {
    "action": "execute",
    "code": "df = pd.read_csv('data.csv'); summary = df.describe()",
    "session_id": "analysis-123"
})
```

## Architecture

```
Other Agents ──┬──> Pixell Kit Adapter ──┐
               │                          ├──> Python Agent
Core Agent ────┴──> A2A Protocol ────────┘    ├── U-P-E-E Engine
                                               │   ├── Understand: Code analysis
                                               │   ├── Plan: Resource allocation
                                               │   ├── Execute: Container isolation
                                               │   └── Evaluate: Error recovery
                                               ├── Session Manager (in-memory)
                                               └── Container Pool (pre-warmed)
```

## Development

### Running Tests

```bash
pytest tests/
```

### Code Quality

```bash
# Linting
ruff check .

# Type checking
mypy src/

# Formatting
black src/
```

## License

MIT License - see LICENSE file for details.