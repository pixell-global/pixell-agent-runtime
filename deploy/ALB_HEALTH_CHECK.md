# ALB/NLB Health Check Configuration

This document describes how to configure health checks for PAR in AWS load balancers.

## Overview

PAR exposes health check endpoints for both REST and A2A (gRPC) surfaces:
- REST: `GET /health` on port 8080
- A2A: gRPC Health service on port 50051 (optional)
- TCP health: Direct port checks on 8080 and 50051

## ALB Health Check (REST)

### Target Group Configuration

```json
{
  "TargetGroupArn": "arn:aws:elasticloadbalancing:...",
  "HealthCheckProtocol": "HTTP",
  "HealthCheckPort": "8080",
  "HealthCheckPath": "/health",
  "HealthCheckIntervalSeconds": 30,
  "HealthCheckTimeoutSeconds": 5,
  "HealthyThresholdCount": 2,
  "UnhealthyThresholdCount": 3,
  "Matcher": {
    "HttpCode": "200"
  }
}
```

### Using AWS CLI

```bash
aws elbv2 create-target-group \
  --name pixell-agent-runtime-rest \
  --protocol HTTP \
  --port 8080 \
  --vpc-id vpc-xxxxx \
  --health-check-protocol HTTP \
  --health-check-port 8080 \
  --health-check-path /health \
  --health-check-interval-seconds 30 \
  --health-check-timeout-seconds 5 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3 \
  --matcher HttpCode=200 \
  --target-type ip
```

### Health Check Behavior

| Status | Condition | HTTP Code |
|--------|-----------|-----------|
| Healthy | Runtime ready, all services started | 200 |
| Unhealthy | Runtime not ready, still loading | 503 |
| Unhealthy | Package download failed | No response |
| Unhealthy | Container crashed | No response |

### Important Settings

1. **Start Period**: Set to 60 seconds in ECS task definition to give container time to download package and start services.

2. **Interval**: 30 seconds is recommended. Shorter intervals increase load on containers.

3. **Healthy Threshold**: 2 consecutive successes to mark healthy. This prevents flapping.

4. **Unhealthy Threshold**: 3 consecutive failures to mark unhealthy. This allows for transient errors.

5. **Timeout**: 5 seconds. Health check should respond quickly.

## NLB Health Check (A2A gRPC)

### Target Group Configuration

For gRPC, use TCP health checks (simpler) or gRPC health checks (more accurate).

#### Option 1: TCP Health Check (Recommended)

```bash
aws elbv2 create-target-group \
  --name pixell-agent-runtime-a2a \
  --protocol TCP \
  --port 50051 \
  --vpc-id vpc-xxxxx \
  --health-check-protocol TCP \
  --health-check-port 50051 \
  --health-check-interval-seconds 30 \
  --health-check-timeout-seconds 10 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3 \
  --target-type ip
```

#### Option 2: gRPC Health Check (More Accurate)

```bash
aws elbv2 create-target-group \
  --name pixell-agent-runtime-a2a \
  --protocol TCP \
  --port 50051 \
  --vpc-id vpc-xxxxx \
  --health-check-protocol HTTPS \
  --health-check-port 50051 \
  --health-check-path /grpc.health.v1.Health/Check \
  --health-check-interval-seconds 30 \
  --health-check-timeout-seconds 10 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3 \
  --target-type ip
```

**Note**: Option 2 requires PAR to implement the gRPC Health service protocol. Currently PAR uses TCP health checks (Option 1).

## ECS Container Health Check

In addition to load balancer health checks, ECS can perform container-level health checks.

### ECS Health Check Configuration

This is defined in the ECS task definition (see `ecs-task-definition-template.json`):

```json
{
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
```

### Container Health Check Behavior

- ECS runs health check inside the container
- If health check fails `retries` times, ECS marks container as unhealthy
- ECS can replace unhealthy containers (if `HEALTHY` deployment constraint is set)

### Important: Install curl in Docker image

Make sure Dockerfile includes curl:

```dockerfile
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*
```

## Health Check Flow

### Startup Sequence

1. **Container starts** (t=0s)
   - Health checks return no response (container not ready)
   - Status: Unhealthy

2. **Runtime initializes** (t=0-5s)
   - `/health` returns 503 (not ready)
   - Status: Unhealthy

3. **Package downloads** (t=5-10s)
   - `/health` returns 503 (not ready)
   - Status: Unhealthy

4. **Package loads and services start** (t=10-60s)
   - `/health` returns 503 (not ready)
   - Status: Unhealthy

5. **Runtime ready** (t=60s)
   - `/health` returns 200 (ready)
   - Status: Healthy after 2 consecutive successes

### Shutdown Sequence

1. **SIGTERM received** (t=0s)
   - Runtime marks itself as not ready
   - `/health` immediately returns 503
   - Status: Unhealthy after 3 consecutive failures

2. **Graceful shutdown period** (t=0-30s)
   - In-flight requests complete
   - gRPC streams close gracefully
   - New connections rejected

3. **Container exits** (t=30-35s)
   - All services stopped
   - Container terminates

## Base Path Considerations

When using `BASE_PATH=/agents/{AGENT_APP_ID}`, ensure:

1. Health check path is **NOT** prefixed: Use `/health`, not `{BASE_PATH}/health`
2. Agent routes **ARE** prefixed: `{BASE_PATH}/api/...`

This allows load balancer to health check the runtime without knowing the agent ID.

## Monitoring Health Checks

### CloudWatch Metrics

Key metrics to monitor:
- `TargetResponseTime`: Should be < 100ms for `/health`
- `HealthyHostCount`: Should match desired task count
- `UnhealthyHostCount`: Should be 0 in steady state
- `TargetConnectionErrorCount`: Should be 0

### Alarms

```bash
# Alarm: No healthy targets
aws cloudwatch put-metric-alarm \
  --alarm-name par-no-healthy-targets \
  --alarm-description "Alert when no PAR targets are healthy" \
  --metric-name HealthyHostCount \
  --namespace AWS/ApplicationELB \
  --statistic Average \
  --period 60 \
  --evaluation-periods 2 \
  --threshold 1 \
  --comparison-operator LessThanThreshold \
  --dimensions Name=TargetGroup,Value=targetgroup/pixell-agent-runtime-rest/xxxxx
```

## Troubleshooting

### Health Check Always Returns 503

**Symptoms**: Container running but health check never returns 200

**Possible Causes**:
1. Package download failed - check S3 access
2. Package installation failed - check container logs
3. Handler loading failed - check agent manifest
4. gRPC server failed to start - check port conflicts

**Debug**:
```bash
# Check container logs
aws ecs execute-command \
  --cluster pixell-agents \
  --task TASK_ID \
  --container agent-runtime \
  --command "curl localhost:8080/health -v" \
  --interactive

# Check runtime logs
aws logs tail /ecs/pixell-agent-runtime --follow
```

### Health Check Timeout

**Symptoms**: Health check times out, no response

**Possible Causes**:
1. Container not running
2. Security group blocks ALB -> container traffic
3. Container port not exposed
4. Runtime crashed during startup

**Debug**:
```bash
# Verify task is running
aws ecs describe-tasks --cluster pixell-agents --tasks TASK_ID

# Check task network interface
aws ec2 describe-network-interfaces --network-interface-ids ENI_ID

# Test connectivity from ALB subnet
nc -zv CONTAINER_IP 8080
```

### Intermittent Health Check Failures

**Symptoms**: Health check passes sometimes, fails other times

**Possible Causes**:
1. Resource exhaustion (CPU/memory)
2. Slow package loading
3. Network connectivity issues
4. Too aggressive timeout settings

**Debug**:
```bash
# Check CPU/memory metrics
aws cloudwatch get-metric-statistics \
  --namespace AWS/ECS \
  --metric-name CPUUtilization \
  --dimensions Name=ServiceName,Value=pixell-agent-runtime \
  --start-time 2023-01-01T00:00:00Z \
  --end-time 2023-01-01T01:00:00Z \
  --period 300 \
  --statistics Average,Maximum

# Increase timeout if needed
aws elbv2 modify-target-group \
  --target-group-arn arn:aws:... \
  --health-check-timeout-seconds 10
```

## Best Practices

1. **Use start period**: Set to 60s to give container time to initialize
2. **Monitor boot time**: Alert if boot time > 5s (soft budget) or 10s (hard limit)
3. **Set realistic thresholds**: 2 healthy, 3 unhealthy prevents flapping
4. **Use TCP for gRPC**: Simpler and more reliable than gRPC health protocol
5. **Test deployments**: Verify health checks work before production
6. **Monitor continuously**: Track health check metrics and set alarms
