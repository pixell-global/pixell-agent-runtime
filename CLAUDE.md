# Pixell Agent Runtime (PAR) - AI Agent Guide

## Quick Reference

### Production EC2 Instance
- **Instance ID**: `i-0df57d61c09d02b00`
- **Region**: `us-east-2`
- **Public URL**: `https://par.pixell.global`
- **Supervisor Port**: `9000`

### Deploy PAR to EC2
```bash
cd /Users/syum/dev/pixell-agent-runtime
./deploy_ec2_par.sh i-0df57d61c09d02b00
```

This script will:
1. Build a wheel from local source
2. Upload to S3
3. Install into EC2's virtualenv at `/opt/pixell-agent-runtime/venv/`
4. Restart the `par-supervisor` systemd service
5. Verify the installation

### Deploy an Agent (APKG)
```bash
cd /path/to/agent
pixell build                           # Creates agent.apkg
pixell deploy -f agent-x.x.x.apkg      # Deploys to production
```

## Architecture Overview

```
PAR (Pixell Agent Runtime)
├── Supervisor (port 9000)           # Manages agent lifecycle
├── gRPC Gateway (port 50051)        # Routes A2A requests
└── Agent Processes                  # Each agent runs on allocated ports
    ├── A2A Port (60000-60199)       # A2A JSON-RPC over HTTP
    ├── REST Port (63000-63199)      # REST API
    └── UI Port (65000-65199)        # Web UI (if applicable)
```

## Key Files

| File | Purpose |
|------|---------|
| `deploy_ec2_par.sh` | **Deploy PAR to EC2** - Use this for runtime updates |
| `src/pixell_runtime/supervisor/` | Supervisor that manages agents |
| `src/pixell_runtime/three_surface/runtime.py` | Agent runtime (REST, A2A, UI surfaces) |
| `src/pixell_runtime/a2a/http_wrapper.py` | A2A HTTP wrapper for handlers dict pattern |
| `pyproject.toml` | Version and dependencies |

## A2A Streaming Support

When an agent's `create_service()` returns `streaming_handlers`, the A2A HTTP wrapper enables SSE streaming:

```python
# In agent's par_adapter.py
def create_service():
    return {
        "custom_handlers": {
            "chat": handle_chat_request,
        },
        "streaming_handlers": {           # <-- Enables streaming
            "chat": stream_chat_request,  # Returns AsyncGenerator
        }
    }
```

The agent card at `/.well-known/agent.json` will show `"streaming": true` when streaming handlers are provided.

## EC2 Setup Details

On the EC2 instance:
- **OS**: Amazon Linux 2023
- **Python**: 3.11 in virtualenv at `/opt/pixell-agent-runtime/venv/`
- **Service**: `par-supervisor.service` (systemd)
- **Config**: `/etc/par-supervisor.conf`
- **Logs**: `sudo journalctl -u par-supervisor -f`

## Common Operations

### Check supervisor status on EC2
```bash
aws ssm start-session --target i-0df57d61c09d02b00 --region us-east-2
sudo systemctl status par-supervisor
sudo journalctl -u par-supervisor -f
```

### Verify agent card
```bash
curl -s "https://par.pixell.global/agents/{agent-id}/a2a/.well-known/agent.json"
```

### Test A2A message/send
```bash
curl -X POST "https://par.pixell.global/agents/{agent-id}/a2a" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send","params":{"message":{"role":"user","parts":[{"type":"text","text":"Hello"}]}}}'
```

## Troubleshooting

### 502 Bad Gateway
1. Check if supervisor is running: `systemctl status par-supervisor`
2. Check if agent is deployed: `curl http://localhost:9000/agents`
3. Check logs: `journalctl -u par-supervisor -n 100`

### Agent not starting
1. Check extracted files: `ls /var/lib/pixell/extracted/{agent-id}/`
2. Check agent logs in supervisor output
3. Verify package was uploaded correctly to S3

### Streaming not working
1. Verify `streaming_handlers` is returned from `create_service()`
2. Check agent card shows `"streaming": true`
3. Use `/a2a/stream` endpoint for SSE streaming

## IMPORTANT: Testing Deployments

**ALWAYS test agent deployments using `pixell build` and `pixell deploy` commands - NEVER bypass PAC by calling PAR supervisor API directly.**

When testing deployment changes (e.g., short_id routing, socket paths, etc.):
```bash
cd /path/to/test-agent
pixell build                           # Build the APKG
pixell deploy -f agent-x.x.x.apkg      # Deploy through PAC pipeline
```

This ensures the full deployment flow through PAC is tested, including:
- Database interactions
- Short ID derivation
- S3 uploads
- PAR deployment requests

**DO NOT** call `POST http://localhost:9000/agents` directly - this bypasses PAC and won't test the actual production flow.
