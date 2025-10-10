# Deployment Script Fixes

## Summary

The `scripts/deploy_par.sh` script has been updated to work with the new PAR architecture after the migration to per-agent ECS tasks and data-plane-only execution model.

## Issues Fixed

### 1. ❌ Wrong Task Definition File → ✅ Fixed

**Before:**
```bash
TD_JSON="${PROJECT_ROOT}/deploy/ecs-task-definition-envoy.json"
```

**Problem:**
- Used old envoy-based task definition with outdated env vars
- Had `RUNTIME_MODE`, `MAX_AGENTS`, `PORT`, `ADMIN_PORT` (multi-agent subprocess pattern)
- Missing all new required vars (`AGENT_APP_ID`, `PACKAGE_URL`, etc.)

**After:**
```bash
TD_JSON="${PROJECT_ROOT}/deploy/ecs-task-definition-template.json"
```

**Fixed:**
- Uses correct template with proper health checks
- Has all new environment variables
- Configured for single-agent per container

---

### 2. ❌ Missing Critical Environment Variables → ✅ Fixed

**Before:** Only 6 env vars set
- BASE_PATH, REST_PORT, A2A_PORT, UI_PORT
- SERVICE_DISCOVERY_NAMESPACE, SERVICE_DISCOVERY_SERVICE

**After:** All 15 required/optional env vars set

**Required (with validation):**
```bash
AGENT_APP_ID_ENV="${AGENT_APP_ID:?Error: AGENT_APP_ID must be set}"
PACKAGE_URL_ENV="${PACKAGE_URL:?Error: PACKAGE_URL must be set}"
```

**Optional (with defaults):**
```bash
DEPLOYMENT_ID_ENV="${DEPLOYMENT_ID:-}"
PACKAGE_SHA256_ENV="${PACKAGE_SHA256:-}"
AWS_REGION_ENV="${AWS_REGION:-us-east-2}"
S3_BUCKET_ENV="${S3_BUCKET:-pixell-agent-packages}"
REST_PORT_ENV="${REST_PORT:-8080}"
A2A_PORT_ENV="${A2A_PORT:-50051}"
UI_PORT_ENV="${UI_PORT:-3000}"
BASE_PATH_ENV="${BASE_PATH:-/agents/${AGENT_APP_ID}}"
MULTIPLEXED_ENV="${MULTIPLEXED:-true}"
MAX_PACKAGE_SIZE_MB_ENV="${MAX_PACKAGE_SIZE_MB:-100}"
BOOT_BUDGET_MS_ENV="${BOOT_BUDGET_MS:-5000}"
BOOT_HARD_LIMIT_MULTIPLIER_ENV="${BOOT_HARD_LIMIT_MULTIPLIER:-2.0}"
GRACEFUL_SHUTDOWN_TIMEOUT_SEC_ENV="${GRACEFUL_SHUTDOWN_TIMEOUT_SEC:-30}"
```

**Benefits:**
- Script will fail fast if required vars missing
- All PAR features now configurable via env vars
- Proper defaults match production requirements

---

### 3. ❌ Container Name Mismatch → ✅ Fixed

**Before:**
```bash
if .name == "par" then
```

**Problem:**
- Task definition template uses container name `"agent-runtime"`
- Script would fail to patch the correct container

**After:**
```bash
if .name == "agent-runtime" then
```

**Fixed:**
- Matches container name in task definition template
- Environment variables correctly injected

---

### 4. ❌ Service Discovery Logic in PAR → ✅ Fixed

**Before:** 30+ lines of Cloud Map/service discovery logic
```bash
# Check if service already has service registries configured
EXISTING_REGISTRIES=$(aws ecs describe-services ...)
# Check if service registry is configured in Cloud Map
SD_SERVICE_ARN=$(aws servicediscovery list-services ...)
# Configuring service with service discovery...
```

**Problem:**
- Service discovery is **control-plane** responsibility (owned by PAC)
- PAR is **data-plane only** - should NOT manage Cloud Map
- Violates architecture separation
- Creates security risk (requires SD permissions)

**After:** Simplified to single update command
```bash
# NOTE: Service discovery (Cloud Map) is managed by PAC (control plane), not PAR.
# PAR is data-plane only. Simply update the task definition.
aws ecs update-service --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --service "$ECS_SERVICE" \
  --task-definition "$TD_ARN" \
  >/dev/null
```

**Fixed:**
- Respects data-plane/control-plane separation
- Follows network isolation requirements
- Simpler, more maintainable code

---

### 5. ❌ Incomplete Documentation → ✅ Fixed

**Before:** Minimal header comments listing a few env vars

**After:** Comprehensive documentation
- All required and optional env vars documented
- Defaults clearly stated
- Usage examples provided
- Links to related docs

**Also Created:**
- `deploy/.env.example` - Template with all env vars and descriptions
- `deploy/DEPLOYMENT_GUIDE.md` - Complete deployment guide
- `deploy/DEPLOYMENT_SCRIPT_FIXES.md` - This document

---

## Verification

### Test the Fixed Script

1. **Validate required vars:**
```bash
# Should fail with clear error
./scripts/deploy_par.sh --build-only
# Error: AGENT_APP_ID must be set
```

2. **Build with valid config:**
```bash
export AGENT_APP_ID=test-agent
export PACKAGE_URL=s3://pixell-agent-packages/test.apkg
./scripts/deploy_par.sh --build-only
# Should build successfully
```

3. **Verify task definition patching:**
```bash
# Set all env vars in .env
source .env

# Run update to see generated task definition
./scripts/deploy_par.sh --update-only 2>&1 | grep -A 20 "Registering"
```

### What Gets Deployed

With the fixed script, the ECS task will have:

```json
{
  "environment": [
    {"name": "AGENT_APP_ID", "value": "my-agent"},
    {"name": "DEPLOYMENT_ID", "value": "deploy-20240106"},
    {"name": "PACKAGE_URL", "value": "s3://pixell-agent-packages/agent.apkg"},
    {"name": "PACKAGE_SHA256", "value": "abc123..."},
    {"name": "AWS_REGION", "value": "us-east-2"},
    {"name": "S3_BUCKET", "value": "pixell-agent-packages"},
    {"name": "BASE_PATH", "value": "/agents/my-agent"},
    {"name": "REST_PORT", "value": "8080"},
    {"name": "A2A_PORT", "value": "50051"},
    {"name": "UI_PORT", "value": "3000"},
    {"name": "MULTIPLEXED", "value": "true"},
    {"name": "MAX_PACKAGE_SIZE_MB", "value": "100"},
    {"name": "BOOT_BUDGET_MS", "value": "5000"},
    {"name": "BOOT_HARD_LIMIT_MULTIPLIER", "value": "2.0"},
    {"name": "GRACEFUL_SHUTDOWN_TIMEOUT_SEC", "value": "30"}
  ]
}
```

All values match PAR's runtime requirements from the migration design.

---

## Architecture Compliance

The fixed script now properly implements the data-plane-only model:

| Responsibility | Owner | Script Behavior |
|---------------|-------|-----------------|
| Build Docker image | PAR deployment | ✅ Implemented |
| Push to ECR | PAR deployment | ✅ Implemented |
| Register task definition | PAR deployment | ✅ Implemented |
| Update ECS service | PAR deployment | ✅ Implemented (task def only) |
| Configure Cloud Map | **PAC (control plane)** | ✅ Removed from script |
| Manage ALB/NLB rules | **PAC (control plane)** | ✅ Not in script |
| Database operations | **PAC (control plane)** | ✅ Not in script |

---

## Breaking Changes

If you were using the old script, you'll need to:

1. **Set new required env vars:**
   ```bash
   export AGENT_APP_ID=your-agent-id
   export PACKAGE_URL=s3://bucket/agent.apkg
   ```

2. **Remove service discovery env vars:**
   - `SERVICE_DISCOVERY_NAMESPACE` (no longer used)
   - `SERVICE_DISCOVERY_SERVICE` (no longer used)

3. **Update to new task definition:**
   - Script now uses `ecs-task-definition-template.json`
   - Old `ecs-task-definition-envoy.json` is deprecated

4. **IAM Permissions:**
   - Script no longer needs `servicediscovery:*` permissions
   - Still needs: `ecr:*`, `ecs:RegisterTaskDefinition`, `ecs:UpdateService`

---

## Migration Path

### For Existing Deployments

1. **Update .env file:**
   ```bash
   cp deploy/.env.example .env
   # Fill in values from your current deployment
   ```

2. **Test build locally:**
   ```bash
   ./scripts/deploy_par.sh --build-only
   ```

3. **Deploy to staging:**
   ```bash
   ECS_CLUSTER=staging ./scripts/deploy_par.sh
   ```

4. **Verify health:**
   ```bash
   aws logs tail /ecs/pixell-agent-runtime --follow
   # Look for: "event":"runtime_ready"
   ```

5. **Deploy to production:**
   ```bash
   ./scripts/deploy_par.sh
   ```

### For New Deployments

1. **Copy example config:**
   ```bash
   cp deploy/.env.example .env
   ```

2. **Set required values:**
   ```bash
   # Edit .env
   AGENT_APP_ID=my-agent
   PACKAGE_URL=s3://pixell-agent-packages/my-agent.apkg
   ```

3. **Deploy:**
   ```bash
   ./scripts/deploy_par.sh
   ```

---

## Related Documentation

- `deploy/DEPLOYMENT_GUIDE.md` - Complete deployment guide
- `deploy/.env.example` - Environment variable template
- `deploy/IAM_POLICY.md` - IAM role requirements
- `deploy/ALB_HEALTH_CHECK.md` - Health check configuration
- `deploy/ecs-task-definition-template.json` - ECS task definition

---

## Summary

The deployment script is now:

✅ **Architecture compliant** - Data-plane only, no control-plane operations
✅ **Feature complete** - Supports all new PAR capabilities
✅ **Validated** - Fails fast on missing required config
✅ **Documented** - Complete guides and examples
✅ **Secure** - No unnecessary AWS permissions required
✅ **Production ready** - All env vars, health checks, graceful shutdown configured
