# Envoy A2A Deployment Implementation Plan

## Overview

Deploy Envoy-based A2A routing architecture to enable external agent-to-agent communication via NLB with header-based routing.

**Goal:** Enable PAC and external clients to call any agent via single NLB endpoint using `x-deployment-id` header routing.

---

## Current State vs Target State

### Current State
```
PAC/External → ❌ Cannot reach agents
                  ↓
                  NLB (50051) → Target Group (unhealthy)
                                ↓
                                PAR at 10.0.1.133:50051 (no Envoy)
                                ↓
                                Agents on localhost:50054, 50055, etc.
```

### Target State
```
PAC/External → NLB (50051) + x-deployment-id header
               ↓
               PAR Task (10.0.1.133)
               ├─ Envoy Sidecar (0.0.0.0:50051) ← NLB routes here
               │  ↓ reads x-deployment-id header
               │  ↓ routes to localhost:{agent_port}
               └─ PAR + Agents
                  ├─ Agent 80cef... (localhost:50054)
                  └─ Agent another (localhost:50055)
```

---

## Prerequisites Check

Before starting, verify these resources exist:

```bash
# 1. NLB exists
aws elbv2 describe-load-balancers \
  --names pixell-runtime-nlb \
  --query 'LoadBalancers[0].{DNS:DNSName,ARN:LoadBalancerArn}'

# Expected: pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com

# 2. A2A Target Group exists
aws elbv2 describe-target-groups \
  --names pixell-runtime-a2a-tg \
  --query 'TargetGroups[0].{ARN:TargetGroupArn,Port:Port,HealthCheckPath:HealthCheckPath}'

# Expected: Port 50051, HealthCheck on port 8080

# 3. ECS Cluster and Services exist
aws ecs describe-services \
  --cluster pixell-runtime-cluster \
  --services pixell-runtime-multi-agent \
  --query 'services[0].{Name:serviceName,DesiredCount:desiredCount}'

# 4. Task execution and task roles exist
aws iam get-role --role-name pixell-runtime-execution-role
aws iam get-role --role-name pixell-runtime-task-role
```

---

## Phase 1: Update Task Definition with Envoy Sidecar

### Step 1.1: Review Current Task Definition

```bash
# Get current task definition
aws ecs describe-task-definition \
  --task-definition pixell-runtime-multi-agent:12 \
  --query 'taskDefinition.containerDefinitions[*].{Name:name,Image:image,Essential:essential}' \
  --output table

# Expected: Only 'par' container
```

### Step 1.2: Create New Task Definition with Envoy

The task definition `deploy/ecs-task-definition-envoy.json` already exists but is NOT used. We need to:

**Option A: Update existing family** (Recommended)
```bash
# Register new revision of pixell-runtime-multi-agent with Envoy
# First, update the file to match multi-agent service

cat > /tmp/par-multi-agent-envoy.json <<'EOF'
{
  "family": "pixell-runtime-multi-agent",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "2048",
  "memory": "4096",
  "executionRoleArn": "arn:aws:iam::636212886452:role/pixell-runtime-execution-role",
  "taskRoleArn": "arn:aws:iam::636212886452:role/pixell-runtime-task-role",
  "containerDefinitions": [
    {
      "name": "envoy",
      "image": "envoyproxy/envoy:v1.29-latest",
      "essential": true,
      "user": "0",
      "environment": [
        {
          "name": "ENVOY_UID",
          "value": "0"
        }
      ],
      "portMappings": [
        {
          "containerPort": 50051,
          "hostPort": 50051,
          "protocol": "tcp"
        },
        {
          "containerPort": 9901,
          "hostPort": 9901,
          "protocol": "tcp"
        }
      ],
      "command": [
        "/usr/local/bin/envoy",
        "-c",
        "/etc/envoy/envoy.yaml",
        "--log-level",
        "info"
      ],
      "mountPoints": [
        {
          "sourceVolume": "envoy-config",
          "containerPath": "/etc/envoy",
          "readOnly": true
        }
      ],
      "healthCheck": {
        "command": [
          "CMD-SHELL",
          "curl -s http://localhost:9901/ready | grep -q LIVE || exit 1"
        ],
        "interval": 10,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 15
      },
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/pixell-runtime-multi-agent",
          "awslogs-create-group": "true",
          "awslogs-region": "us-east-2",
          "awslogs-stream-prefix": "envoy"
        }
      }
    },
    {
      "name": "par",
      "image": "636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-runtime-multi-agent:latest",
      "essential": true,
      "dependsOn": [
        {
          "containerName": "envoy",
          "condition": "HEALTHY"
        }
      ],
      "environment": [
        {
          "name": "RUNTIME_MODE",
          "value": "multi-agent"
        },
        {
          "name": "PORT",
          "value": "8080"
        },
        {
          "name": "A2A_PORT",
          "value": "50051"
        },
        {
          "name": "ADMIN_PORT",
          "value": "9090"
        },
        {
          "name": "MAX_AGENTS",
          "value": "20"
        },
        {
          "name": "SERVICE_DISCOVERY_NAMESPACE",
          "value": "pixell-runtime.local"
        },
        {
          "name": "SERVICE_DISCOVERY_SERVICE",
          "value": "agents"
        },
        {
          "name": "ENVOY_ADMIN_URL",
          "value": "http://localhost:9901"
        },
        {
          "name": "A2A_EXTERNAL_ENDPOINT",
          "value": "pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com:50051"
        }
      ],
      "portMappings": [
        {
          "containerPort": 8080,
          "hostPort": 8080,
          "protocol": "tcp"
        },
        {
          "containerPort": 9090,
          "hostPort": 9090,
          "protocol": "tcp"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/pixell-runtime-multi-agent",
          "awslogs-create-group": "true",
          "awslogs-region": "us-east-2",
          "awslogs-stream-prefix": "par"
        }
      },
      "healthCheck": {
        "command": [
          "CMD-SHELL",
          "curl -f http://localhost:8080/health || exit 1"
        ],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 60
      }
    }
  ],
  "volumes": [
    {
      "name": "envoy-config",
      "dockerVolumeConfiguration": {
        "driver": "local",
        "scope": "task",
        "autoprovision": true,
        "driverOpts": {
          "type": "tmpfs",
          "device": "tmpfs"
        }
      }
    }
  ]
}
EOF

# Register new task definition
aws ecs register-task-definition --cli-input-json file:///tmp/par-multi-agent-envoy.json
```

**⚠️ PROBLEM:** Envoy config needs to be in the container image or mounted from S3/EFS.

### Step 1.3: Build Docker Image with Envoy Config

**Better approach:** Build custom Docker image with Envoy config baked in.

```dockerfile
# Dockerfile.envoy
FROM envoyproxy/envoy:v1.29-latest

# Copy envoy configuration
COPY envoy.yaml /etc/envoy/envoy.yaml

# Expose ports
EXPOSE 50051 9901

# Run envoy
CMD ["/usr/local/bin/envoy", "-c", "/etc/envoy/envoy.yaml", "--log-level", "info"]
```

Build and push:
```bash
cd /Users/syum/dev/pixell-agent-runtime

# Build Envoy image
docker build -f Dockerfile.envoy -t pixell-envoy:latest .

# Tag and push to ECR
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin 636212886452.dkr.ecr.us-east-2.amazonaws.com

docker tag pixell-envoy:latest 636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-envoy:latest
docker push 636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-envoy:latest
```

### Step 1.4: Update Task Definition with Custom Envoy Image

Update the task definition JSON to use custom image:
```json
"image": "636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-envoy:latest"
```

### Step 1.5: Register Task Definition

```bash
# After updating the JSON file
aws ecs register-task-definition --cli-input-json file:///tmp/par-multi-agent-envoy.json

# Note the new revision number (e.g., :13)
```

---

## Phase 2: Update Target Group Health Check

The A2A target group health check is currently incorrect:
- **Current:** Checks `/agents/d7e18412.../a2a/health` on port `8080`
- **Problem:** This deployment ID is hardcoded and may not exist
- **Fix:** Use generic health check or Envoy's admin endpoint

### Step 2.1: Update Target Group Health Check

```bash
# Option 1: Check Envoy /ready endpoint on port 9901
aws elbv2 modify-target-group \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39 \
  --health-check-protocol HTTP \
  --health-check-port 9901 \
  --health-check-path /ready \
  --health-check-interval-seconds 30 \
  --health-check-timeout-seconds 5 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3

# Option 2: TCP health check on port 50051 (simpler)
aws elbv2 modify-target-group \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39 \
  --health-check-protocol TCP \
  --health-check-interval-seconds 30 \
  --health-check-timeout-seconds 10 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3
```

**Recommendation:** Use TCP health check (Option 2) for simplicity.

---

## Phase 3: Update ECS Service with Multiple Target Groups

The service currently only registers with REST API target group. Add A2A target group.

### Step 3.1: Update Service Configuration

```bash
# Update service to register with BOTH target groups
aws ecs update-service \
  --cluster pixell-runtime-cluster \
  --service pixell-runtime-multi-agent \
  --task-definition pixell-runtime-multi-agent:13 \
  --load-balancers \
    targetGroupArn=arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/par-multi-agent-tg/c28c15d19accbca4,containerName=par,containerPort=8080 \
    targetGroupArn=arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39,containerName=envoy,containerPort=50051 \
  --force-new-deployment
```

**Note:** This will trigger a new deployment with the Envoy-enabled task definition.

---

## Phase 4: Configure NLB Listener

Verify NLB has listener on port 50051 routing to A2A target group.

```bash
# List NLB listeners
aws elbv2 describe-listeners \
  --load-balancer-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:loadbalancer/net/pixell-runtime-nlb/eb1b66efdcfd482c \
  --query 'Listeners[*].{Port:Port,Protocol:Protocol,TargetGroup:DefaultActions[0].TargetGroupArn}' \
  --output table

# If listener doesn't exist, create it:
aws elbv2 create-listener \
  --load-balancer-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:loadbalancer/net/pixell-runtime-nlb/eb1b66efdcfd482c \
  --protocol TCP \
  --port 50051 \
  --default-actions Type=forward,TargetGroupArn=arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39
```

---

## Phase 5: Update PAR Code to Register Agents with Envoy

PAR needs to dynamically add Envoy clusters when agents start.

### Step 5.1: Verify Envoy Manager Integration

Check that deployment manager calls Envoy manager:

```bash
# This should already be in the code, but verify:
grep -r "envoy_manager" /Users/syum/dev/pixell-agent-runtime/src/pixell_runtime/deploy/
```

### Step 5.2: Update Envoy Config to Support Dynamic Clusters

Current `envoy.yaml` has empty clusters. We need to use Envoy's xDS API or file-based dynamic config.

**Simpler approach:** Use static routing with catch-all and let PAR handle routing internally.

**Alternative:** Use Envoy's [Cluster Discovery Service (CDS)](https://www.envoyproxy.io/docs/envoy/latest/configuration/upstream/cluster_manager/cds).

For now, let's use a **simpler hybrid approach:**

1. Envoy exposes port 50051 to NLB
2. Envoy forwards ALL requests to PAR's internal gRPC server (localhost:50052)
3. PAR's gRPC server reads `x-deployment-id` and proxies to correct agent port

Update `envoy.yaml`:
```yaml
static_resources:
  listeners:
  - name: grpc_listener
    address:
      socket_address:
        address: 0.0.0.0
        port_value: 50051
    filter_chains:
    - filters:
      - name: envoy.filters.network.http_connection_manager
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
          stat_prefix: grpc
          codec_type: AUTO
          route_config:
            name: local_route
            virtual_hosts:
            - name: grpc_backend
              domains: ["*"]
              routes:
              - match:
                  prefix: "/"
                  grpc: {}
                route:
                  cluster: par_router
                  timeout: 300s
          http_filters:
          - name: envoy.filters.http.router
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router

  clusters:
  - name: par_router
    type: STATIC
    connect_timeout: 5s
    load_assignment:
      cluster_name: par_router
      endpoints:
      - lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: 127.0.0.1
                port_value: 50052
    http2_protocol_options: {}

admin:
  address:
    socket_address:
      address: 0.0.0.0
      port_value: 9901
```

This forwards all traffic to PAR's router on localhost:50052.

---

## Phase 6: Update PAR A2A Server to Act as Router

PAR needs to listen on port 50052 (internal) and route based on `x-deployment-id` header.

### Step 6.1: Update A2A Server to Read Metadata and Route

This requires code changes to `src/pixell_runtime/a2a/server.py` to:
1. Read `x-deployment-id` from gRPC metadata
2. Look up agent's A2A port from deployment manager
3. Forward request to agent's localhost:{port}

**Note:** This is a code change in PAR, so it needs implementation.

---

## Phase 7: Testing Plan

### Test 7.1: Verify Envoy is Running

```bash
# Get task ID after deployment
TASK_ARN=$(aws ecs list-tasks \
  --cluster pixell-runtime-cluster \
  --service-name pixell-runtime-multi-agent \
  --query 'taskArns[0]' \
  --output text)

echo "Task ARN: $TASK_ARN"

# Get task IP
TASK_IP=$(aws ecs describe-tasks \
  --cluster pixell-runtime-cluster \
  --tasks $TASK_ARN \
  --query 'tasks[0].containers[?name==`par`].networkInterfaces[0].privateIpv4Address' \
  --output text)

echo "Task IP: $TASK_IP"

# Check Envoy admin endpoint (from within VPC)
# You'll need to use AWS Session Manager or a bastion host
aws ssm start-session --target <ec2-instance-id>

# Then from within VPC:
curl http://$TASK_IP:9901/ready
# Expected: LIVE

curl http://$TASK_IP:9901/stats
# Should show Envoy metrics
```

### Test 7.2: Verify NLB Target Health

```bash
aws elbv2 describe-target-health \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39

# Expected: State: healthy
```

### Test 7.3: Test A2A Connectivity via NLB (from local machine)

```bash
# Use grpcurl to test
grpcurl -plaintext \
  -H "x-deployment-id: 80cef39f-3daf-47bf-93f9-c33f08e51292" \
  pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com:50051 \
  pixell.agent.AgentService/Health

# Expected: {"ok": true, "message": "...", "timestamp": ...}
```

### Test 7.4: Test A2A Connectivity via Python Client

```bash
cd /Users/syum/dev/pixell-agent-runtime

# Set external endpoint
export A2A_EXTERNAL_ENDPOINT=pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com:50051

# Run test
python test_a2a_connection.py
```

### Test 7.5: Test Agent-to-Agent Communication (within VPC)

From within a PAR container:
```bash
# Test Service Discovery
python3 -c "
from pixell_runtime.utils.service_discovery import get_service_discovery_client
client = get_service_discovery_client()
agents = client.discover_agents()
print(f'Found {len(agents)} agents:', agents)
"

# Test A2A client (internal)
python3 -c "
import asyncio
from pixell_runtime.a2a.client import get_a2a_client

async def test():
    client = get_a2a_client(prefer_internal=True)
    result = await client.health_check('80cef39f-3daf-47bf-93f9-c33f08e51292')
    print('Health check result:', result)

asyncio.run(test())
"
```

### Test 7.6: Verify Routing with Different Agents

Deploy another agent and test routing to both:

```bash
# Deploy second agent via PAC
# Then test both:

grpcurl -plaintext \
  -H "x-deployment-id: <agent-1-id>" \
  pixell-runtime-nlb-xxx.elb.us-east-2.amazonaws.com:50051 \
  pixell.agent.AgentService/Health

grpcurl -plaintext \
  -H "x-deployment-id: <agent-2-id>" \
  pixell-runtime-nlb-xxx.elb.us-east-2.amazonaws.com:50051 \
  pixell.agent.AgentService/Health

# Both should return different responses
```

---

## Phase 8: Rollback Plan

If deployment fails:

### Step 8.1: Revert Service to Previous Task Definition

```bash
aws ecs update-service \
  --cluster pixell-runtime-cluster \
  --service pixell-runtime-multi-agent \
  --task-definition pixell-runtime-multi-agent:12 \
  --load-balancers \
    targetGroupArn=arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/par-multi-agent-tg/c28c15d19accbca4,containerName=par,containerPort=8080 \
  --force-new-deployment
```

### Step 8.2: Monitor Service Stability

```bash
aws ecs describe-services \
  --cluster pixell-runtime-cluster \
  --services pixell-runtime-multi-agent \
  --query 'services[0].{DesiredCount:desiredCount,RunningCount:runningCount,Status:status}'

# Wait for RunningCount == DesiredCount
```

---

## Execution Checklist

### Pre-Deployment
- [ ] Verify NLB and target groups exist
- [ ] Verify ECS cluster and service exist
- [ ] Review current task definition
- [ ] Review `envoy.yaml` configuration
- [ ] Create ECR repository for Envoy image (if needed)

### Deployment Steps
- [ ] Phase 1: Build and push Envoy Docker image
- [ ] Phase 2: Update A2A target group health check (TCP)
- [ ] Phase 3: Register new task definition with Envoy sidecar
- [ ] Phase 4: Update ECS service with new task definition and both target groups
- [ ] Phase 5: Wait for deployment to complete (~5 minutes)
- [ ] Phase 6: Verify NLB listener exists on port 50051

### Testing
- [ ] Test 7.1: Verify Envoy is running via admin endpoint
- [ ] Test 7.2: Verify NLB target health is "healthy"
- [ ] Test 7.3: Test A2A connectivity via NLB with grpcurl
- [ ] Test 7.4: Test A2A connectivity via Python client
- [ ] Test 7.5: Test internal Service Discovery routing
- [ ] Test 7.6: Test routing to multiple agents

### Post-Deployment
- [ ] Monitor CloudWatch logs for errors
- [ ] Monitor Envoy metrics at :9901/stats
- [ ] Test end-to-end PAC → PAR → Agent flow
- [ ] Update PAC with `PAR_A2A_ENDPOINT` environment variable
- [ ] Document any issues or deviations from plan

---

## Troubleshooting Guide

### Issue: Envoy container fails health check

**Check:**
```bash
aws logs tail /ecs/pixell-runtime-multi-agent --follow --filter-pattern "envoy"
```

**Common causes:**
- Envoy config syntax error
- Port 50051 or 9901 already in use
- Missing volume mount for config

### Issue: NLB target unhealthy

**Check:**
```bash
aws elbv2 describe-target-health \
  --target-group-arn arn:aws:...pixell-runtime-a2a-tg/... \
  --query 'TargetHealthDescriptions[*].{IP:Target.Id,State:TargetHealth.State,Reason:TargetHealth.Reason}'
```

**Common causes:**
- Health check path/port incorrect
- Security group blocking NLB → task traffic
- Task not registered with target group

### Issue: gRPC call returns UNAVAILABLE

**Check:**
- NLB listener exists on port 50051
- Security group allows inbound 50051
- Agent actually exists (check deployment health)

### Issue: Routing to wrong agent

**Check:**
- `x-deployment-id` header is being sent
- Envoy routing config matches header
- PAR router is reading metadata correctly

---

## Estimated Timeline

| Phase | Duration | Notes |
|-------|----------|-------|
| Pre-work (ECR setup, build image) | 30 min | One-time |
| Phase 1-4 (AWS config changes) | 20 min | Automated |
| Phase 5 (Service deployment) | 10 min | Automated, wait time |
| Phase 6 (Code changes if needed) | 2 hours | If routing needs changes |
| Phase 7 (Testing) | 45 min | Thorough validation |
| **Total** | **4 hours** | With code changes |
| **Total (no code)** | **2 hours** | If routing works as-is |

---

## Success Criteria

✅ Envoy container running and healthy
✅ NLB target group shows "healthy" status
✅ gRPC call via NLB returns successful response
✅ Multiple agents routable via single NLB endpoint
✅ Service Discovery still works for internal calls
✅ PAC can call agents via NLB with `x-deployment-id` header

---

## Next Steps After Deployment

1. Update PAC code per `docs/PAC_INTEGRATION.md`
2. Remove Service Discovery code from PAC (optional optimization)
3. Monitor production traffic and A2A latency
4. Set up CloudWatch alarms for Envoy health
5. Document Envoy configuration for future reference