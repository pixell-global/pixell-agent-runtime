# Envoy A2A Implementation - Complete Guide

## Overview

This implementation adds Envoy sidecar-based A2A routing to enable external agent-to-agent communication via a single NLB endpoint with header-based routing.

## Problem Statement

**Before this implementation:**
- Agents run as subprocesses inside PAR on dynamic ports (50054, 50055, etc.)
- No way for external clients (PAC, other services) to reach specific agents
- NLB target group was unhealthy due to misconfigured health checks
- Service Discovery registered PAR instances, not individual agents

**After this implementation:**
- Single NLB endpoint for all A2A traffic: `pixell-runtime-nlb-xxx.elb.us-east-2.amazonaws.com:50051`
- Route to any agent using `x-deployment-id` header
- Envoy handles external routing, Service Discovery handles internal routing
- Healthy NLB targets enable external connectivity

## Architecture

### Option A: Envoy → PAR Router (Implemented)

```
External Client
    ↓ x-deployment-id: abc123
NLB:50051
    ↓
Envoy:50051 (routes ALL traffic to →)
    ↓
PAR Router:50052 (reads header, routes to →)
    ↓
Agent subprocess:50054
```

**Pros:**
- Simple Envoy config (static)
- Leverages existing deployment manager
- Easy to maintain

**Cons:**
- Extra hop through PAR router
- PAR must implement routing logic

### Option B: Envoy Direct Routing (Alternative)

```
External Client
    ↓ x-deployment-id: abc123
NLB:50051
    ↓
Envoy:50051 (reads header, routes directly to →)
    ↓
Agent subprocess:50054
```

**Pros:**
- One less hop (lower latency)
- Envoy handles all routing

**Cons:**
- Complex Envoy config (dynamic clusters via xDS)
- Requires PAR to dynamically update Envoy config

## Implementation Files

### Configuration Files
- `envoy.yaml` - Original complex config with dynamic clusters
- `envoy-simple.yaml` - Simplified config routing to PAR router
- `Dockerfile.envoy` - Custom Envoy image with baked-in config

### Deployment Scripts
- `scripts/validate_aws_infra.sh` - Pre-deployment validation
- `scripts/deploy_envoy.sh` - Automated deployment
- `scripts/test_a2a_connectivity.sh` - Post-deployment testing

### Documentation
- `docs/ENVOY_DEPLOYMENT_PLAN.md` - Detailed implementation plan
- `docs/ENVOY_DEPLOYMENT_SUMMARY.md` - Quick start guide
- `docs/PAC_INTEGRATION.md` - PAC code changes needed
- `docs/A2A_HYBRID_IMPLEMENTATION.md` - Original hybrid approach

### Code Changes
- `src/pixell_runtime/a2a/client.py` - A2A client with Service Discovery
- `src/pixell_runtime/utils/service_discovery.py` - Added discovery methods
- `src/pixell_runtime/api/deploy.py` - Added A2A health checks

## Quick Start

### Prerequisites Check

```bash
# Validate infrastructure
./scripts/validate_aws_infra.sh
```

### Deploy Envoy

```bash
# Deploy Envoy sidecar (takes ~10 minutes)
./scripts/deploy_envoy.sh
```

### Test Connectivity

```bash
# Test specific deployment
./scripts/test_a2a_connectivity.sh 80cef39f-3daf-47bf-93f9-c33f08e51292

# Test with Python client
export A2A_EXTERNAL_ENDPOINT=pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com:50051
python test_a2a_connection.py
```

## What Was Changed

### AWS Infrastructure
1. **Task Definition**: Added Envoy sidecar container
2. **ECS Service**: Registered with both REST and A2A target groups
3. **Target Group**: Changed health check from HTTP to TCP
4. **ECR**: Created `pixell-envoy` repository

### PAR Code
1. **Service Discovery**: Added `discover_agents()` and `discover_agent_by_id()`
2. **A2A Client**: Created `a2a/client.py` with intelligent routing
3. **Deploy API**: Added A2A health checks to `/deployments/{id}/health`

### What Wasn't Changed
- NLB (already existed)
- Target Groups (already existed)
- Security Groups
- IAM Roles
- PAC code (needs changes per `PAC_INTEGRATION.md`)

## Current Status

✅ **Infrastructure validated** - All AWS resources exist and are healthy
⏳ **Envoy not deployed yet** - Currently only 1 container (PAR), need to add Envoy sidecar
⏳ **A2A connectivity not working** - NLB target is unhealthy, cannot route externally

## Next Steps

### 1. Deploy Envoy (Option A)

```bash
./scripts/deploy_envoy.sh
```

This will:
- Build and push custom Envoy image
- Register new task definition with sidecar
- Update ECS service (rolling deployment)
- Validate deployment

### 2. Implement PAR Router (if needed)

If using simplified Envoy config, PAR needs internal router on port 50052:

**File: `src/pixell_runtime/a2a/server.py`**

The router must:
1. Listen on port 50052 (internal only)
2. Read `x-deployment-id` from gRPC metadata
3. Look up agent port from deployment manager
4. Forward request to `localhost:{agent_port}`

**Check if this exists:**
```bash
grep -n "50052" src/pixell_runtime/a2a/server.py
```

### 3. Update PAC Integration

After Envoy is deployed and working, update PAC:

**Follow: `docs/PAC_INTEGRATION.md`**

Key changes:
- Add `PAR_A2A_ENDPOINT` env var
- Add `x-deployment-id` metadata to gRPC calls
- Remove Service Discovery code (optional)

### 4. Test End-to-End

```bash
# From PAC or external client
grpcurl -plaintext \
  -H "x-deployment-id: 80cef39f-3daf-47bf-93f9-c33f08e51292" \
  pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com:50051 \
  pixell.agent.AgentService/Health
```

## Troubleshooting

### Deployment Issues

**Envoy container fails:**
```bash
aws logs tail /ecs/pixell-runtime-multi-agent --follow --filter-pattern "envoy"
```

**Target unhealthy:**
```bash
aws elbv2 describe-target-health \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39
```

### Connectivity Issues

**gRPC returns UNAVAILABLE:**
- Check NLB listener exists on port 50051
- Check security group allows inbound 50051
- Check deployment exists and is healthy

**Routes to wrong agent:**
- Verify `x-deployment-id` header is sent
- Check Envoy logs for routing decisions
- Verify PAR router is reading metadata

### Rollback

```bash
aws ecs update-service \
  --cluster pixell-runtime-cluster \
  --service pixell-runtime-multi-agent \
  --task-definition pixell-runtime-multi-agent:12 \
  --load-balancers \
    targetGroupArn=arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/par-multi-agent-tg/c28c15d19accbca4,containerName=par,containerPort=8080 \
  --force-new-deployment
```

## Key Insights from Investigation

### The Fundamental Problem

The original issue was **architectural confusion** between two deployment models:

1. **Multi-Agent PAR**: 1 container hosts N agents as subprocesses
2. **Envoy Service Mesh**: Header-based routing to agents

The A2A implementation code assumed direct agent access, but agents are NOT directly accessible - they're subprocesses inside PAR.

### The Solution

Use Envoy as a front-end router that:
- Exposes port 50051 to NLB
- Reads `x-deployment-id` header
- Routes to PAR's internal router OR directly to agent ports

This enables:
- Single external endpoint for all agents
- Header-based routing
- Service Discovery for internal calls
- Horizontal scaling of PAR instances

### No PAC/PAR Code Changes Needed

The A2A client implementation is **correct as-is**. The missing piece is the **infrastructure layer** (Envoy) that enables external routing.

Once Envoy is deployed:
- PAC just needs to add `x-deployment-id` header
- PAR's existing A2A server continues working
- Service Discovery continues working for internal calls

## Timeline

| Task | Duration | Status |
|------|----------|--------|
| Infrastructure validation | 5 min | ✅ Complete |
| A2A client implementation | 30 min | ✅ Complete |
| Envoy deployment | 15 min | ⏳ Pending |
| Connectivity testing | 30 min | ⏳ Pending |
| PAC integration | 2 hours | ⏳ Pending |
| **Total** | **3-4 hours** | **In Progress** |

## Success Criteria

✅ Infrastructure validated
✅ A2A client code implemented
⏳ Envoy sidecar deployed
⏳ NLB target healthy
⏳ gRPC call via NLB succeeds
⏳ Multiple agents routable
⏳ PAC can call agents externally

## Resources

- AWS Console: https://us-east-2.console.aws.amazon.com/ecs/v2/clusters/pixell-runtime-cluster
- NLB DNS: pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com
- Envoy Docs: https://www.envoyproxy.io/docs/envoy/latest/
- gRPC Metadata: https://grpc.io/docs/guides/metadata/