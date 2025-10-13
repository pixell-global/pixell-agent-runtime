# PAR Deployment Architecture Fix

## The Problem

The deployment script was **architecturally confused** about who deploys what.

### Root Cause

You're running **OLD architecture** (multi-agent PAR) but the script tried to deploy **NEW architecture** (per-agent containers):

**Current Production (OLD):**
```json
{
  "service": "pixell-runtime-multi-agent",
  "environment": {
    "RUNTIME_MODE": "multi-agent",  // One PAR runs many agents
    "MAX_AGENTS": "20",             // Subprocess model
    "NO AGENT_APP_ID": true         // Multi-agent, no single ID
  }
}
```

**Script Was Trying (NEW):**
```json
{
  "service": "per-agent-service",
  "environment": {
    "AGENT_APP_ID": "???",          // Required per agent
    "PACKAGE_URL": "???",           // Required per agent
    "One agent per container": true
  }
}
```

**The Mismatch:**
- Script tried to update old service with new task definition
- New task definition requires AGENT_APP_ID (one per agent)
- But there's no single AGENT_APP_ID for a generic runtime!

---

## The Solution

### Correct Separation of Concerns

| Component | Responsibility |
|-----------|---------------|
| **PAR deploy script** | Build and push **GENERIC** runtime image to ECR |
| **PAC (control plane)** | Create per-agent ECS services with AGENT_APP_ID |
| **ECS** | Run containers with agent-specific env vars |
| **PAR runtime** | Read AGENT_APP_ID at boot, download APKG, run agent |

### Architecture Flow

```
┌─────────────────────────────────────┐
│   scripts/deploy_par.sh             │
│   (Builds GENERIC image)            │
│   - No AGENT_APP_ID needed          │
│   - No PACKAGE_URL needed           │
│   - Just builds runtime code        │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   ECR: pixell-agent-runtime         │
│   (Generic image for ANY agent)     │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   PAC API (Control Plane)           │
│   POST /deployments                 │
│   {                                 │
│     "agentAppId": "python-executor",│
│     "packageUrl": "s3://..."        │
│   }                                 │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   PAC creates ECS service           │
│   - Service: agent-python-executor  │
│   - Image: pixell-agent-runtime:tag │
│   - Env: AGENT_APP_ID=python-exec   │
│         PACKAGE_URL=s3://...        │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   ECS launches container            │
│   Container reads AGENT_APP_ID      │
│   Downloads APKG                    │
│   Runs agent                        │
└─────────────────────────────────────┘
```

---

## What Changed in deploy_par.sh

### Before (WRONG)
```bash
# Tried to do everything
./scripts/deploy_par.sh
# 1. Build image ✓
# 2. Push to ECR ✓
# 3. Update ECS service ✗ (requires AGENT_APP_ID - doesn't make sense!)
```

### After (CORRECT)
```bash
# Only builds generic runtime
./scripts/deploy_par.sh
# 1. Build generic image ✓
# 2. Push to ECR ✓
# Done! No AGENT_APP_ID needed.

# Agent deployment happens via PAC:
curl -X POST https://pac-api/deployments \
  -d '{"agentAppId":"python-executor","packageUrl":"s3://..."}'
```

### Key Changes

1. **Removed `update_service()` function** - Not PAR's job
2. **Removed `--update-only` flag** - Not PAR's job
3. **Default action is now `push`** - Build + push, nothing else
4. **No AGENT_APP_ID/PACKAGE_URL required** - These are per-agent, set by PAC

---

## Why AGENT_APP_ID is NOT Needed at Build Time

### Generic Runtime Image
The PAR image is **generic** - it can run ANY agent:

```dockerfile
# Dockerfile builds generic runtime
FROM python:3.11-slim
COPY src/ ./src/
RUN pip install -e .
# No AGENT_APP_ID here!
CMD ["python", "-m", "pixell_runtime"]
```

### Runtime Reads AGENT_APP_ID
The container reads `AGENT_APP_ID` **at startup**:

```python
# src/pixell_runtime/three_surface/runtime.py
class ThreeSurfaceRuntime:
    def __init__(self):
        # Read from environment at RUNTIME
        config = RuntimeConfig()
        self.agent_app_id = config.agent_app_id  # From env

        # Download agent package
        package_url = os.getenv("PACKAGE_URL")
        self.package = download_apkg(package_url)
```

### PAC Sets AGENT_APP_ID Per Service

PAC creates separate ECS services for each agent:

```python
# In PAC (control plane)
def deploy_agent(agent_app_id: str, package_url: str):
    # Create ECS service with agent-specific env
    ecs.create_service(
        cluster='pixell-runtime-cluster',
        serviceName=f'agent-{agent_app_id}',
        taskDefinition='pixell-agent-runtime:latest',
        environment=[
            {'name': 'AGENT_APP_ID', 'value': agent_app_id},
            {'name': 'PACKAGE_URL', 'value': package_url},
        ]
    )
```

---

## Current State

### What Works Now

✅ **Build generic PAR image:**
```bash
./scripts/deploy_par.sh --build-only
# No env vars needed
```

✅ **Build and push to ECR:**
```bash
./scripts/deploy_par.sh
# No AGENT_APP_ID needed!
```

### What's Still OLD Architecture

❌ **Production ECS service:**
- Service: `pixell-runtime-multi-agent`
- Mode: Multi-agent subprocess model
- To migrate: Need PAC to create new per-agent services

---

## Migration Path

### Phase 1: Build New Runtime (DONE ✅)
- [x] New PAR code supports single-agent mode
- [x] AGENT_APP_ID validation at runtime
- [x] PACKAGE_URL download support
- [x] Graceful shutdown, retry backoff, etc.
- [x] Deploy script builds generic image

### Phase 2: PAC Integration (TODO)
- [ ] PAC API creates per-agent ECS services
- [ ] PAC injects AGENT_APP_ID per service
- [ ] PAC manages service discovery (Cloud Map)
- [ ] PAC manages ALB/NLB routing rules

### Phase 3: Migrate Production (TODO)
- [ ] Create new per-agent services via PAC
- [ ] Route traffic to new services
- [ ] Decomission old multi-agent service

---

## FAQ

### Q: Why doesn't deploy_par.sh need AGENT_APP_ID?

**A:** Because it builds a **generic runtime image**, not an agent-specific image. The AGENT_APP_ID is injected by PAC when creating the ECS service.

### Q: How do I deploy a specific agent then?

**A:** Through PAC's API:
```bash
curl -X POST https://pac-api/deployments \
  -H "Content-Type: application/json" \
  -d '{
    "agentAppId": "python-executor",
    "packageUrl": "s3://pixell-agent-packages/python-executor.apkg"
  }'
```

### Q: What about the old pixell-runtime-multi-agent service?

**A:** It's running the OLD architecture (multi-agent subprocess model). It will be deprecated once PAC creates the new per-agent services.

### Q: Can I still test locally?

**A:** Yes:
```bash
export AGENT_APP_ID=test-agent
export PACKAGE_URL=s3://pixell-agent-packages/test.apkg
par run /path/to/local/package.apkg
```

---

## Summary

**Root Cause:** Script tried to update ECS services (control-plane work) when it should only build images (data-plane work).

**Fix:** Script now only builds and pushes the generic PAR runtime image. No AGENT_APP_ID needed.

**Next Step:** PAC must create per-agent ECS services with AGENT_APP_ID environment variables.

**Result:** Clear separation of concerns between PAR (data-plane) and PAC (control-plane).
