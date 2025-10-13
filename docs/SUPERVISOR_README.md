# Pixell Agent Runtime Supervisor

The Supervisor is a component of PAR (Pixell Agent Runtime) that enables running multiple agent instances on a single EC2 instance with proper isolation using Linux users.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│ EC2 Instance                                             │
│                                                           │
│ ┌───────────────────────────────────────────────────┐  │
│ │ Supervisor (port 9000)                            │  │
│ │ - FastAPI HTTP Server                             │  │
│ │ - SupervisorState                                 │  │
│ │ - LinuxUserManager                                │  │
│ │ - PortAllocator (8081-8100, 50052-50071, 3001-3020) │ │
│ │ - PackageDownloader                               │  │
│ │ - ProcessManager                                  │  │
│ └───────────────────────────────────────────────────┘  │
│                                                           │
│ ┌───────────────────────────────────────────────────┐  │
│ │ Agent Processes (isolated Linux users)            │  │
│ │                                                     │  │
│ │ agent_4906eeb7 (UID: 2001)                        │  │
│ │   ├─ Ports: REST=8081, A2A=50052, UI=3001        │  │
│ │   ├─ Home: /home/agent_4906eeb7/                 │  │
│ │   ├─ Venv: /home/agent_4906eeb7/venv/            │  │
│ │   └─ Process: PID 12345                          │  │
│ │                                                     │  │
│ │ agent_abc123de (UID: 2002)                        │  │
│ │   ├─ Ports: REST=8082, A2A=50053, UI=3002        │  │
│ │   └─ ...                                          │  │
│ └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## Features

- **Multi-Agent Hosting**: Run up to 20 agents per EC2 instance
- **Linux User Isolation**: Each agent runs as a dedicated Linux user
- **Port Management**: Automatic port allocation with conflict prevention
- **Package Caching**: S3/HTTPS downloads cached locally
- **Zero-Downtime Updates**: Rolling updates without service interruption
- **Health Monitoring**: Automatic health checks for all agents
- **Process Management**: Graceful shutdown with SIGTERM → SIGKILL fallback

## Installation

### Prerequisites

- Ubuntu 20.04+ or Amazon Linux 2023
- Python 3.11+
- Root access (for user management)
- AWS credentials (for S3 access)

### Quick Install

```bash
# Clone repository
git clone https://github.com/pixell-ai/pixell-agent-runtime.git
cd pixell-agent-runtime

# Run installation script
sudo ./scripts/install_supervisor.sh
```

The script will:
1. Install PAR to `/opt/pixell-agent-runtime`
2. Create required directories in `/var/lib/pixell/`
3. Install systemd service
4. Start supervisor on port 9000

### Manual Installation

```bash
# Install PAR
pip3 install -e .

# Create directories
sudo mkdir -p /var/lib/pixell/{packages,extracted,logs}

# Copy systemd service
sudo cp systemd/pixell-supervisor.service /etc/systemd/system/

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable pixell-supervisor
sudo systemctl start pixell-supervisor
```

## Usage

### Check Supervisor Health

```bash
curl http://localhost:9000/health
```

Response:
```json
{
  "status": "healthy",
  "service": "supervisor"
}
```

### Deploy an Agent

```bash
curl -X POST http://localhost:9000/agents/deploy \
  -H "Content-Type: application/json" \
  -d '{
    "agent_app_id": "4906eeb7",
    "deployment_id": "dep-123",
    "package_url": "s3://pixell-agent-packages/agent.apkg",
    "package_sha256": "abc123...",
    "max_package_size_mb": 100,
    "boot_budget_ms": 5000,
    "boot_hard_limit_multiplier": 2.0,
    "graceful_shutdown_timeout_sec": 30,
    "env": {
      "CUSTOM_VAR": "value"
    }
  }'
```

Response:
```json
{
  "agent_app_id": "4906eeb7",
  "deployment_id": "dep-123",
  "status": "running",
  "message": "Agent 4906eeb7 deployed successfully",
  "ports": {
    "rest": 8081,
    "a2a": 50052,
    "ui": 3001
  },
  "linux_user": "agent_4906eeb7",
  "pid": 12345,
  "created_at": "2025-10-12T20:00:00.000000"
}
```

### List All Agents

```bash
curl http://localhost:9000/agents
```

Response:
```json
[
  {
    "agent_app_id": "4906eeb7",
    "deployment_id": "dep-123",
    "status": "running",
    "ports": {
      "rest": 8081,
      "a2a": 50052,
      "ui": 3001
    },
    "linux_user": "agent_4906eeb7",
    "pid": 12345,
    "created_at": "2025-10-12T20:00:00.000000"
  }
]
```

### Get Agent Details

```bash
curl http://localhost:9000/agents/4906eeb7
```

### Update an Agent

```bash
curl -X POST http://localhost:9000/agents/update \
  -H "Content-Type: application/json" \
  -d '{
    "agent_app_id": "4906eeb7",
    "deployment_id": "dep-456",
    "package_url": "s3://pixell-agent-packages/agent-v2.apkg",
    "package_sha256": "def456..."
  }'
```

### Delete an Agent

```bash
curl -X DELETE "http://localhost:9000/agents/4906eeb7?force=false&cleanup_user=true"
```

Query parameters:
- `force` (default: false): Force kill process immediately
- `cleanup_user` (default: true): Delete Linux user and home directory

### Get Supervisor Status

```bash
curl http://localhost:9000/status
```

Response:
```json
{
  "service": "supervisor",
  "healthy": true,
  "total_agents": 3,
  "status_counts": {
    "running": 2,
    "starting": 1
  },
  "max_agents": 20,
  "available_slots": 17
}
```

## Configuration

### Environment Variables

Configure via systemd service file (`/etc/systemd/system/pixell-supervisor.service`):

- `SUPERVISOR_HOST`: Bind address (default: `0.0.0.0`)
- `SUPERVISOR_PORT`: HTTP port (default: `9000`)
- `PACKAGE_CACHE_DIR`: Package cache directory (default: `/var/lib/pixell/packages`)
- `PACKAGE_EXTRACT_DIR`: Extracted packages directory (default: `/var/lib/pixell/extracted`)
- `MAX_AGENTS`: Maximum agents per instance (default: `20`)
- `AWS_REGION`: AWS region for S3 (default: `us-east-2`)

### Port Ranges

- **REST**: 8081-8100 (20 ports)
- **A2A/gRPC**: 50052-50071 (20 ports)
- **UI**: 3001-3020 (20 ports)

Maximum 20 concurrent agents per instance.

## Operations

### View Logs

```bash
# Follow logs in real-time
sudo journalctl -u pixell-supervisor -f

# View last 100 lines
sudo journalctl -u pixell-supervisor -n 100

# View logs with timestamps
sudo journalctl -u pixell-supervisor --since "1 hour ago"
```

### Restart Supervisor

```bash
sudo systemctl restart pixell-supervisor
```

**Note**: Restarting the supervisor will kill all running agents. Use with caution in production.

### Stop Supervisor

```bash
sudo systemctl stop pixell-supervisor
```

### Check Service Status

```bash
sudo systemctl status pixell-supervisor
```

### Update Supervisor Code

```bash
# Pull latest code
cd /opt/pixell-agent-runtime
git pull

# Reinstall
pip3 install -e .

# Restart service
sudo systemctl restart pixell-supervisor
```

## Security

### Linux User Isolation

Each agent runs as a dedicated Linux user:
- Username: `agent_{first_8_chars_of_agent_app_id}`
- Home directory: `/home/agent_{id}/`
- Shell: `/bin/false` (no interactive login)
- Process limits: Configured via systemd

Agents cannot:
- Access other agents' files or processes
- Execute commands as other users
- Bind to already-allocated ports

### Systemd Hardening

The supervisor runs with security restrictions:
- `ProtectSystem=strict`: Read-only system directories
- `PrivateTmp=true`: Isolated /tmp directory
- `ReadWritePaths`: Only specified directories writable
- `LimitNOFILE`, `LimitNPROC`: Resource limits

### Package Validation

All packages are validated:
- SHA256 checksum verification
- Size limit enforcement (default: 100MB)
- Signature verification (if configured)

## Troubleshooting

### Supervisor Won't Start

Check logs:
```bash
sudo journalctl -u pixell-supervisor -n 50
```

Common issues:
- Port 9000 already in use: Change `SUPERVISOR_PORT`
- Permission errors: Ensure running as root
- Missing dependencies: Run `pip3 install -e .`

### Agent Deployment Fails

Check logs:
```bash
curl http://localhost:9000/agents/{agent_id}
```

Common issues:
- Package download fails: Check AWS credentials and S3 URL
- Port allocation fails: Max agents (20) reached
- Health check timeout: Increase `boot_budget_ms`

### Agent Not Responding

Check agent status:
```bash
# Via supervisor API
curl http://localhost:9000/agents/{agent_id}

# Check agent health directly
curl http://localhost:{rest_port}/agents/{agent_id}/health
```

Check process:
```bash
# List all agent processes
ps aux | grep "agent_"

# Check specific agent
sudo -u agent_{id} ps aux
```

### Out of Ports

```bash
curl http://localhost:9000/status
```

If `available_slots` is 0:
1. Delete unused agents
2. Increase max agents (requires code change)
3. Deploy to another EC2 instance

## Monitoring

### Metrics

The supervisor exposes metrics via the `/status` endpoint:
- Total agents
- Agents by status (running, starting, failed, etc.)
- Available capacity
- Port allocation

### Health Checks

Each agent is monitored:
- REST health endpoint: `GET /agents/{id}/health`
- Process status: Check if PID is alive
- Port availability: Verify ports are listening

### Alerts

Recommended CloudWatch alarms:
- Supervisor process not running
- High agent failure rate
- Disk space < 10% free
- Memory usage > 90%

## Performance

### Resource Usage

**Per Agent**:
- Memory: ~100-500MB (depends on agent code)
- CPU: <5% average
- Disk: ~50-200MB (package + venv)

**Supervisor**:
- Memory: ~100MB
- CPU: <5% average
- Disk: <100MB

### Limits

- **Max agents per instance**: 20 (configurable)
- **Max package size**: 100MB (configurable)
- **Boot timeout**: 5s * 2.0 multiplier = 10s (configurable)
- **Graceful shutdown timeout**: 30s (configurable)

### Scaling

To increase capacity:
1. Deploy more EC2 instances
2. Register instances with PAC control plane
3. PAC distributes agents across instances

## API Reference

See full API documentation: [API.md](./API.md)

### Endpoints

- `GET /health` - Supervisor health check
- `POST /agents/deploy` - Deploy new agent
- `POST /agents/update` - Update existing agent
- `DELETE /agents/{id}` - Delete agent
- `GET /agents/{id}` - Get agent details
- `GET /agents` - List all agents
- `GET /status` - Supervisor status

## Testing

### Unit Tests

```bash
# Run all supervisor tests
pytest tests/test_supervisor*.py -v

# Run specific test file
pytest tests/test_supervisor_state.py -v

# With coverage
pytest tests/test_supervisor*.py --cov=src/pixell_runtime/supervisor --cov-report=html
```

### Integration Tests

```bash
# Deploy test agent
curl -X POST http://localhost:9000/agents/deploy \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/deploy_request.json

# Verify health
curl http://localhost:8081/health

# Update agent
curl -X POST http://localhost:9000/agents/update \
  -H "Content-Type: application/json" \
  -d @tests/fixtures/update_request.json

# Delete agent
curl -X DELETE http://localhost:9000/agents/test-agent
```

## Development

### Local Development

```bash
# Install in development mode
pip3 install -e ".[dev]"

# Run supervisor locally (as root for user management)
sudo python3 -m pixell_runtime.supervisor

# Or use uvicorn directly
sudo uvicorn pixell_runtime.supervisor.server:app --host 0.0.0.0 --port 9000 --reload
```

### Adding New Features

1. Create branch: `git checkout -b feat/supervisor-xyz`
2. Implement changes in `src/pixell_runtime/supervisor/`
3. Add tests in `tests/test_supervisor_*.py`
4. Run tests: `pytest tests/test_supervisor*.py -v`
5. Create PR

### Code Structure

```
src/pixell_runtime/supervisor/
├── __init__.py           # Module exports
├── __main__.py           # CLI entrypoint
├── server.py             # FastAPI HTTP server
├── state.py              # SupervisorState (main orchestrator)
├── models.py             # Pydantic models
├── user_manager.py       # Linux user management
├── port_allocator.py     # Port allocation
├── package_downloader.py # S3/HTTPS package downloads
└── process_manager.py    # Process lifecycle management
```

## License

Copyright © 2025 Pixell AI. All rights reserved.

## Support

- Documentation: https://docs.pixell.ai/runtime/supervisor
- Issues: https://github.com/pixell-ai/pixell-agent-runtime/issues
- Email: support@pixell.ai
