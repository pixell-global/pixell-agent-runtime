# PAR Deployment Guide

This guide explains how to deploy Pixell Agent Runtime (PAR) to ECS using the updated deployment script.

## Prerequisites

1. **Tools installed:**
   - Docker
   - AWS CLI (configured with credentials)
   - jq
   - git

2. **AWS Resources:**
   - ECR repository created
   - ECS cluster running
   - ECS service created (can be empty initially)
   - IAM roles configured (see `deploy/IAM_POLICY.md`)
   - S3 bucket for packages (`pixell-agent-packages`)

3. **Agent Package:**
   - Valid APKG file uploaded to S3 or accessible via HTTPS
   - SHA256 checksum calculated (optional but recommended)

## Quick Start

### 1. Configure Environment Variables

Copy the example environment file:

```bash
cp deploy/.env.example .env
```

Edit `.env` and set the required values:

```bash
# Required
AGENT_APP_ID=my-agent-id
PACKAGE_URL=s3://pixell-agent-packages/my-agent/package.apkg

# AWS (for push/update)
AWS_REGION=us-east-2
AWS_ACCOUNT_ID=636212886452
ECR_REPO=pixell-agent-runtime

# ECS (for service update)
ECS_CLUSTER=pixell-runtime-cluster
ECS_SERVICE=pixell-runtime
```

### 2. Build and Deploy

**Full deployment (build + push + update):**
```bash
./scripts/deploy_par.sh
```

**Build only (local testing):**
```bash
./scripts/deploy_par.sh --build-only
```

**Push only (build + push to ECR):**
```bash
./scripts/deploy_par.sh --push-only
```

**Update only (register task def + update service):**
```bash
IMAGE_TAG=existing-tag ./scripts/deploy_par.sh --update-only
```

## Deployment Process

### What the Script Does

1. **Build Phase:**
   - Builds Docker image with platform `linux/amd64`
   - Tags with ECR URI and version tag (git sha + timestamp)

2. **Push Phase:**
   - Logs into ECR
   - Pushes image to ECR repository

3. **Update Phase:**
   - Reads task definition template (`deploy/ecs-task-definition-template.json`)
   - Injects environment variables:
     - `AGENT_APP_ID` (required)
     - `PACKAGE_URL` (required)
     - `DEPLOYMENT_ID`, `PACKAGE_SHA256` (optional)
     - Runtime config (ports, timeouts, budgets)
   - Registers new task definition
   - Updates ECS service with new task definition

### Important Notes

- **Service Discovery:** The script does NOT configure Cloud Map/Service Discovery. That's managed by PAC (control plane).
- **Container Name:** Uses `agent-runtime` (defined in task definition template)
- **Environment Variables:** All PAR-required env vars are injected automatically

## Environment Variable Reference

### Required

| Variable | Description | Example |
|----------|-------------|---------|
| `AGENT_APP_ID` | Unique agent identifier | `python-executor` |
| `PACKAGE_URL` | S3 or HTTPS URL to APKG | `s3://pixell-agent-packages/agent.apkg` |
| `AWS_ACCOUNT_ID` | AWS account ID (for ECR) | `636212886452` |
| `AWS_REGION` | AWS region | `us-east-2` |
| `ECR_REPO` | ECR repository name | `pixell-agent-runtime` |

### Optional (with defaults)

| Variable | Default | Description |
|----------|---------|-------------|
| `DEPLOYMENT_ID` | - | Deployment identifier for tracking |
| `PACKAGE_SHA256` | - | SHA256 checksum for package validation |
| `S3_BUCKET` | `pixell-agent-packages` | S3 bucket for packages |
| `BASE_PATH` | `/agents/${AGENT_APP_ID}` | Base path for agent routes |
| `REST_PORT` | `8080` | REST API port |
| `A2A_PORT` | `50051` | gRPC A2A port |
| `UI_PORT` | `3000` | UI port |
| `MULTIPLEXED` | `true` | Serve UI on REST port |
| `MAX_PACKAGE_SIZE_MB` | `100` | Max package size |
| `BOOT_BUDGET_MS` | `5000` | Boot time soft budget |
| `BOOT_HARD_LIMIT_MULTIPLIER` | `2.0` | Hard limit = budget × multiplier |
| `GRACEFUL_SHUTDOWN_TIMEOUT_SEC` | `30` | Graceful shutdown timeout |

## Verification

### 1. Check ECS Task Status

```bash
aws ecs describe-services \
  --cluster pixell-runtime-cluster \
  --services pixell-runtime \
  --query 'services[0].{RunningCount:runningCount,DesiredCount:desiredCount,TaskDefinition:taskDefinition}' \
  --region us-east-2
```

### 2. Check Container Logs

```bash
aws logs tail /ecs/pixell-agent-runtime --follow
```

Look for:
- `"event":"load"` - Package loading
- `"event":"runtime_ready"` - Runtime ready
- `"boot_ms":XXX` - Boot time

### 3. Health Check

```bash
# Get task private IP
TASK_ARN=$(aws ecs list-tasks --cluster pixell-runtime-cluster --service-name pixell-runtime --region us-east-2 --query 'taskArns[0]' --output text)
TASK_IP=$(aws ecs describe-tasks --cluster pixell-runtime-cluster --tasks $TASK_ARN --region us-east-2 --query 'tasks[0].containers[0].networkInterfaces[0].privateIpv4Address' --output text)

# Test health endpoint (from within VPC)
curl http://$TASK_IP:8080/health
# Should return 200 OK when ready
```

### 4. Test Agent Endpoint

```bash
# Via ALB (if configured)
curl https://your-alb-url/agents/my-agent-id/api/...

# Direct to container (from within VPC)
curl http://$TASK_IP:8080/agents/my-agent-id/api/...
```

## Troubleshooting

### Container Exits Immediately

**Check logs for:**
```bash
aws logs tail /ecs/pixell-agent-runtime --follow
```

**Common causes:**
1. Missing `AGENT_APP_ID` - look for "AGENT_APP_ID missing or empty"
2. Invalid `PACKAGE_URL` - look for "file:// URLs are not allowed"
3. S3 access denied - look for "Failed to download package"
4. Package validation failed - look for "SHA256 mismatch"

### Health Check Returns 503

**Possible causes:**
1. Package still downloading - wait up to 60s
2. Package installation failed - check logs
3. Handler loading failed - check agent manifest

**Debug:**
```bash
# Check boot progress
aws logs tail /ecs/pixell-agent-runtime --follow | grep -E "load|ready|error"
```

### Boot Time Exceeded Budget

**Warning in logs:**
```json
{"level":"warning","boot_ms":7500,"budget_ms":5000,"event":"Boot time exceeded budget"}
```

**Solutions:**
1. Increase `BOOT_BUDGET_MS` if boot time is acceptable
2. Optimize package size
3. Use wheelhouse cache (EFS mount) to speed up pip installs

### Boot Time Exceeded Hard Limit

**Error in logs:**
```json
{"level":"error","boot_ms":15000,"hard_limit_ms":10000,"event":"Boot time exceeded hard limit"}
```

Container will exit with code 1.

**Solutions:**
1. Increase `BOOT_HARD_LIMIT_MULTIPLIER`
2. Set to 0 to disable hard limit
3. Investigate slow boot (package size, network, CPU)

## Best Practices

### 1. Use SHA256 Validation

Always set `PACKAGE_SHA256` in production:

```bash
# Generate checksum
aws s3 cp s3://pixell-agent-packages/agent.apkg - | sha256sum

# Set in .env
PACKAGE_SHA256=abc123...
```

### 2. Set Deployment ID

Use deployment ID for tracking:

```bash
DEPLOYMENT_ID="deploy-$(date +%Y%m%d-%H%M%S)"
```

### 3. Monitor Boot Time

Set realistic budgets:

```bash
# Soft budget (warning only)
BOOT_BUDGET_MS=5000

# Hard limit (exit if exceeded)
BOOT_HARD_LIMIT_MULTIPLIER=2.0  # Exit if > 10 seconds
```

### 4. Use Wheelhouse Cache

Mount EFS for faster installs (see task definition template):

```json
{
  "mountPoints": [{
    "sourceVolume": "wheelhouse-cache",
    "containerPath": "/opt/wheelhouse",
    "readOnly": true
  }]
}
```

Set in container:
```bash
WHEELHOUSE_DIR=/opt/wheelhouse
```

### 5. Graceful Shutdown

Set appropriate timeout for graceful shutdown:

```bash
# Allow 30s for in-flight requests to complete
GRACEFUL_SHUTDOWN_TIMEOUT_SEC=30
```

Ensure ECS task `stopTimeout` > graceful timeout (template has 35s).

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Deploy PAR

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v1
        with:
          aws-region: us-east-2

      - name: Deploy PAR
        env:
          AGENT_APP_ID: ${{ secrets.AGENT_APP_ID }}
          PACKAGE_URL: ${{ secrets.PACKAGE_URL }}
          PACKAGE_SHA256: ${{ secrets.PACKAGE_SHA256 }}
          AWS_ACCOUNT_ID: ${{ secrets.AWS_ACCOUNT_ID }}
          ECR_REPO: pixell-agent-runtime
          ECS_CLUSTER: pixell-runtime-cluster
          ECS_SERVICE: pixell-runtime
        run: ./scripts/deploy_par.sh
```

## Security Checklist

- [ ] IAM task role has only S3 GetObject permission
- [ ] IAM execution role has ECR pull and CloudWatch logs permissions
- [ ] `PACKAGE_URL` is validated (only s3:// or https://)
- [ ] `PACKAGE_SHA256` is set for production deployments
- [ ] Security groups allow traffic only from ALB/NLB
- [ ] VPC endpoints configured for S3 (keeps traffic within AWS)
- [ ] Container runs as non-root user (if applicable)
- [ ] Secrets not in environment variables (use AWS Secrets Manager)

## Next Steps

After successful deployment:

1. **Configure Load Balancer:**
   - See `deploy/ALB_HEALTH_CHECK.md` for ALB/NLB configuration

2. **Set Up Monitoring:**
   - CloudWatch alarms for health check failures
   - Dashboard for boot time metrics
   - Log analysis for error patterns

3. **Test Zero-Downtime Deployment:**
   - Deploy new version while serving traffic
   - Verify graceful shutdown and health check behavior

4. **Performance Testing:**
   - Measure P95 latency
   - Load test agent endpoints
   - Optimize boot time if needed
