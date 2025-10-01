# Envoy A2A Deployment - Quick Start Guide

## What This Deployment Does

Enables external A2A (agent-to-agent) communication by adding an Envoy sidecar that routes gRPC requests based on the `x-deployment-id` header.

**Before:** Cannot reach agents externally via NLB
**After:** Single NLB endpoint routes to any agent using header-based routing

---

## Prerequisites

1. **Tools Required:**
   - AWS CLI configured
   - Docker installed and running
   - `jq` for JSON parsing
   - `grpcurl` (optional, for testing): `brew install grpcurl`

2. **AWS Resources Verified:**
   - Run validation script first:
   ```bash
   ./scripts/validate_aws_infra.sh
   ```

---

## Deployment Steps

### Step 1: Validate Infrastructure

```bash
cd /Users/syum/dev/pixell-agent-runtime
./scripts/validate_aws_infra.sh
```

**Expected:** All checks pass (green ✓)

### Step 2: Deploy Envoy Sidecar

```bash
./scripts/deploy_envoy.sh
```

This script will:
1. Build custom Envoy Docker image with config
2. Push image to ECR
3. Update NLB target group health check
4. Register new ECS task definition with Envoy sidecar
5. Update ECS service (triggers rolling deployment)
6. Wait for deployment to stabilize (~5-10 minutes)
7. Verify target health

**Expected:** Deployment completes with healthy targets

### Step 3: Test Connectivity

```bash
./scripts/test_a2a_connectivity.sh <deployment-id>

# Example:
./scripts/test_a2a_connectivity.sh 80cef39f-3daf-47bf-93f9-c33f08e51292
```

**Expected:** All tests pass

---

## What Gets Changed

### AWS Resources

| Resource | Change | Impact |
|----------|--------|--------|
| **Task Definition** | Add Envoy sidecar container | New revision registered |
| **ECS Service** | Register with 2 target groups | Rolling deployment triggered |
| **Target Group Health Check** | Change from HTTP to TCP | More reliable health checks |
| **ECR** | New `pixell-envoy` repository | Stores custom Envoy image |

### No Changes To

- NLB (already exists)
- Target Groups (already exist)
- Security Groups
- IAM Roles
- Service Discovery
- PAC code (will need updates later per PAC_INTEGRATION.md)

---

## Architecture After Deployment

```
┌─────────────────────────────────────────────────────────────┐
│  External Client (PAC)                                       │
│  Sends: x-deployment-id: 80cef39f-3daf-47bf-93f9-c33f08e51292│
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
                ┌──────────────────────┐
                │   NLB (Port 50051)   │
                │  DNS: pixell-runtime │
                │  -nlb-xxx.elb...     │
                └──────────┬───────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
    ┌─────────────────┐       ┌─────────────────┐
    │   ECS Task 1    │       │   ECS Task 2    │
    │  ┌───────────┐  │       │  ┌───────────┐  │
    │  │   Envoy   │  │       │  │   Envoy   │  │
    │  │  :50051   │◄─┼───────┼──┤  :50051   │  │
    │  └─────┬─────┘  │       │  └─────┬─────┘  │
    │        │        │       │        │        │
    │  ┌─────▼─────┐  │       │  ┌─────▼─────┐  │
    │  │    PAR    │  │       │  │    PAR    │  │
    │  │  :50052   │  │       │  │  :50052   │  │
    │  │  (router) │  │       │  │  (router) │  │
    │  └─────┬─────┘  │       │  └─────┬─────┘  │
    │        │        │       │        │        │
    │  ┌─────▼──────┐ │       │  ┌─────▼──────┐ │
    │  │ Agent 1    │ │       │  │ Agent 3    │ │
    │  │ :50054     │ │       │  │ :50054     │ │
    │  ├────────────┤ │       │  ├────────────┤ │
    │  │ Agent 2    │ │       │  │ Agent 4    │ │
    │  │ :50055     │ │       │  │ :50055     │ │
    │  └────────────┘ │       │  └────────────┘ │
    └─────────────────┘       └─────────────────┘
```

**Key Points:**
- NLB load balances across multiple PAR tasks
- Each task has Envoy sidecar listening on port 50051
- Envoy reads `x-deployment-id` header
- Envoy routes to PAR's internal router (port 50052)
- PAR router forwards to correct agent subprocess

---

## Testing Checklist

After deployment, verify:

- [ ] **Envoy Running:** Check CloudWatch Logs for `/ecs/pixell-runtime-multi-agent/envoy`
- [ ] **Target Health:** NLB target group shows "healthy"
- [ ] **gRPC Call:** `grpcurl` test succeeds with correct deployment ID
- [ ] **Python Client:** `test_a2a_connection.py` passes
- [ ] **Service Discovery:** Still works for internal calls
- [ ] **Multiple Agents:** Can route to different agents via single endpoint

---

## Troubleshooting

### Issue: Envoy container fails health check

**Check logs:**
```bash
aws logs tail /ecs/pixell-runtime-multi-agent --follow --filter-pattern "envoy"
```

**Common causes:**
- Envoy config syntax error → Check `envoy.yaml`
- Port conflict → Verify port mappings
- Image pull error → Check ECR permissions

### Issue: NLB target unhealthy

**Check target health:**
```bash
aws elbv2 describe-target-health \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39
```

**Common causes:**
- Health check misconfigured → Should be TCP on port 50051
- Security group blocking traffic → Check NLB → Task rules
- Task not registered → Check service `loadBalancers` config

### Issue: gRPC call returns UNAVAILABLE

**Check:**
1. NLB DNS resolves: `nslookup pixell-runtime-nlb-xxx.elb.us-east-2.amazonaws.com`
2. Port 50051 reachable: `nc -zv <nlb-dns> 50051`
3. Deployment exists: `curl http://par-alb/deployments/{id}/health`
4. Header sent: Verify `x-deployment-id` in gRPC metadata

### Issue: Routes to wrong agent

**This indicates PAR router needs implementation.**

PAR's internal router on port 50052 must:
1. Read `x-deployment-id` from gRPC metadata
2. Look up agent's port from deployment manager
3. Forward request to `localhost:{agent_port}`

**Current State:** This may not be implemented yet. Check `src/pixell_runtime/a2a/server.py`.

---

## Rollback Plan

If deployment fails or causes issues:

```bash
# Revert to previous task definition (without Envoy)
aws ecs update-service \
  --cluster pixell-runtime-cluster \
  --service pixell-runtime-multi-agent \
  --task-definition pixell-runtime-multi-agent:12 \
  --load-balancers \
    targetGroupArn=arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/par-multi-agent-tg/c28c15d19accbca4,containerName=par,containerPort=8080 \
  --force-new-deployment

# Wait for rollback to complete
aws ecs wait services-stable \
  --cluster pixell-runtime-cluster \
  --services pixell-runtime-multi-agent
```

---

## Next Steps After Successful Deployment

1. **Update PAC Integration:**
   - Follow `docs/PAC_INTEGRATION.md`
   - Add `PAR_A2A_ENDPOINT` environment variable
   - Update gRPC calls to include `x-deployment-id` header

2. **Monitor Production:**
   - Set up CloudWatch alarms for Envoy health
   - Monitor A2A latency metrics
   - Track error rates in Envoy stats

3. **Optional Optimizations:**
   - Remove Service Discovery from PAC (simplification)
   - Implement advanced load balancing in Envoy
   - Add rate limiting and circuit breakers

---

## Key Differences from Original Plan

The deployment plan has been **simplified** from the original `A2A_HYBRID_IMPLEMENTATION.md`:

### Original Plan (Complex)
- Envoy dynamically manages clusters via xDS API
- Each agent gets its own Envoy cluster
- Complex dynamic configuration

### Simplified Plan (Implemented)
- Envoy forwards ALL traffic to PAR router (port 50052)
- PAR router handles deployment_id → agent_port mapping
- Static Envoy configuration

**Why simplified?**
- Easier to deploy and maintain
- Leverages existing deployment manager
- No need for complex xDS configuration
- Still achieves the same routing goal

---

## Files Created

| File | Purpose |
|------|---------|
| `docs/ENVOY_DEPLOYMENT_PLAN.md` | Detailed implementation plan |
| `docs/ENVOY_DEPLOYMENT_SUMMARY.md` | This quick start guide |
| `Dockerfile.envoy` | Custom Envoy image with config |
| `envoy-simple.yaml` | Simplified Envoy configuration |
| `scripts/deploy_envoy.sh` | Automated deployment script |
| `scripts/validate_aws_infra.sh` | Pre-deployment validation |
| `scripts/test_a2a_connectivity.sh` | Post-deployment testing |

---

## Success Criteria

✅ Envoy container running and healthy
✅ NLB target group shows "healthy"
✅ gRPC call via NLB returns response
✅ Can route to multiple agents via single endpoint
✅ Service Discovery still works for internal calls
✅ No disruption to existing REST API traffic

---

## Questions?

- Review detailed plan: `docs/ENVOY_DEPLOYMENT_PLAN.md`
- Check PAC integration: `docs/PAC_INTEGRATION.md`
- Check A2A implementation: `docs/A2A_HYBRID_IMPLEMENTATION.md`