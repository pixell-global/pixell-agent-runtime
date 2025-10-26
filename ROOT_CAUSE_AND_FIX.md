# Root Cause Analysis and Fix

## 🔴 The Problem

Client receives HTTP 464 error when calling gRPC endpoints through ALB:
```
gRPC Error: StatusCode.UNKNOWN
Details: "Received http2 header with status: 464"
```

## 🎯 Root Cause

**ALB Target Group Health Check Misconfiguration**

The gRPC target group `pac-agent-4906eeb7-grpc-v3` has:
```
Protocol: HTTP
ProtocolVersion: HTTP2  ✓ (correct)
Port: 60000            ✓ (correct - gRPC port)
HealthCheckProtocol: HTTP
HealthCheckPath: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/health  ✗ (WRONG!)
```

### Why It Fails

1. **Health check uses HTTP on gRPC port (60000)**
   - Port 60000 is a gRPC server (expects HTTP/2 with gRPC protocol)
   - Health check sends plain HTTP/1.1 GET request
   - gRPC server doesn't understand HTTP/1.1 → fails

2. **Even if HTTP worked, path is wrong**
   - Health check path: `/agents/{id}/health`
   - Actual endpoint: `/agents/{id}/a2a/health` (on REST port 63000)
   - gRPC port (60000) has NO HTTP endpoints at all

3. **Consequence**
   - Health checks fail continuously
   - ALB marks target as UNHEALTHY
   - ALB refuses to route traffic to unhealthy targets
   - ALB returns HTTP 464 to clients
   - **Requests never reach PAR!**
   - **Interceptor never executes!**

## ✅ Verification

### Target Health Status
```bash
$ aws elbv2 describe-target-health \
    --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pac-agent-4906eeb7-grpc-v3/5c7fba6a73475cca

{
  "Target": "i-09dcb7f387166efd0",
  "Port": 60000,
  "Health": "unhealthy",  ← UNHEALTHY!
  "Reason": "Target.FailedHealthChecks",
  "Description": "Health checks failed"
}
```

### What's Actually Running
```bash
# Port 60000 - gRPC server (NO HTTP endpoints)
$ curl http://18.119.137.118:60000/agents/{id}/health
curl: (1) Received HTTP/0.9 when not allowed  ← gRPC server, not HTTP!

# Port 63000 - REST server (HAS health endpoint)
$ curl http://18.119.137.118:63000/agents/{id}/a2a/health
HTTP/1.1 200 OK  ← This works! ✓
{"ok":true,"message":"Agent is healthy","timestamp":1729039572}
```

### The Interceptor IS Working
From PAR logs:
```json
{"event":"PAR Routing Interceptor initialized", "agent_id":"4906eeb7...", "prefix":"/agents/4906eeb7.../a2a"}
{"event":"Created A2A gRPC server", "port":60000, "servicer_type":"VividCommenterAgentService"}
{"event":"PAR interceptor: pass-through (no prefix)", "path":"/pixell.agent.AgentService/Health"}
```

The interceptor is initialized and working, but ALB never sends traffic because all targets are unhealthy!

## 🛠️ The Fix

### Option 1: Fix Health Check Path (RECOMMENDED)

Change the health check to use the REST port with correct path:

```bash
aws elbv2 modify-target-group \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pac-agent-4906eeb7-grpc-v3/5c7fba6a73475cca \
  --health-check-protocol HTTP \
  --health-check-port 63000 \
  --health-check-path '/agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/health'
```

**Why this works:**
- Health check hits REST port (63000) which understands HTTP
- Path `/agents/{id}/a2a/health` exists on REST server
- Health checks will pass → targets become HEALTHY
- ALB routes gRPC traffic to port 60000 (as configured)
- Interceptor strips prefix, everything works!

### Option 2: Use gRPC Health Check Protocol

Use AWS ALB's gRPC health check feature:

```bash
aws elbv2 modify-target-group \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pac-agent-4906eeb7-grpc-v3/5c7fba6a73475cca \
  --health-check-protocol HTTP \
  --health-check-port 60000 \
  --matcher 'GrpcCode=0' \
  --health-check-path '/grpc.health.v1.Health/Check'
```

**Requirements:**
- Agent must implement standard gRPC health check service
- Defined in: https://github.com/grpc/grpc/blob/master/doc/health-checking.md

**Why this might not work yet:**
- vivid-commenter agent may not have standard gRPC health service implemented
- Would require adding health service to agent code

### Option 3: Add HTTP Health Endpoint to gRPC Server

Modify PAR to add an HTTP health check endpoint on the gRPC port.

**Why NOT recommended:**
- Mixing HTTP and gRPC on same port is complex
- gRPC server doesn't support HTTP/1.1 natively
- Would require significant changes to PAR

## 📋 Recommended Action

**Use Option 1** - it's the simplest and requires no code changes:

```bash
# Fix the health check configuration
aws elbv2 modify-target-group \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pac-agent-4906eeb7-grpc-v3/5c7fba6a73475cca \
  --health-check-port 63000 \
  --health-check-path '/agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/health'

# Verify health status (wait 30 seconds for health checks to run)
sleep 30

# Check target health
aws elbv2 describe-target-health \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pac-agent-4906eeb7-grpc-v3/5c7fba6a73475cca

# Should show:
# {
#   "Health": "healthy",  ← NOW HEALTHY!
#   ...
# }
```

After this change:
1. ✅ Health checks will pass
2. ✅ ALB will route traffic to gRPC port (60000)
3. ✅ Interceptor will strip the prefix
4. ✅ Agent will receive clean gRPC paths
5. ✅ Everything works!

## 🎓 Key Learnings

1. **ALB Target Group health checks must match the actual service**
   - HTTP health checks need HTTP endpoints
   - gRPC ports don't speak HTTP/1.1
   - Use a separate port (REST) for HTTP health checks

2. **The interceptor code is CORRECT**
   - It's initialized properly
   - It will work once traffic reaches it
   - The problem is infrastructure, not code

3. **HTTP 464 from ALB**
   - Non-standard AWS error code
   - Means: "No healthy targets available"
   - Check target group health status first!

## 🔍 How to Debug Similar Issues

1. **Check target health first:**
   ```bash
   aws elbv2 describe-target-health --target-group-arn <arn>
   ```

2. **Verify health check configuration:**
   ```bash
   aws elbv2 describe-target-groups --target-group-arns <arn>
   ```

3. **Test health endpoint directly:**
   ```bash
   curl http://<instance-ip>:<health-check-port><health-check-path>
   ```

4. **Check PAR logs:**
   ```bash
   aws logs tail /pixell/agent-runtime --since 10m
   ```

5. **Verify ports are listening:**
   ```bash
   aws ssm send-command --instance-ids <id> \
     --document-name "AWS-RunShellScript" \
     --parameters 'commands=["netstat -tlnp"]'
   ```
