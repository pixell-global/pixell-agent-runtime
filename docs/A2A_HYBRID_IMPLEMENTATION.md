# A2A Hybrid Communication Implementation Plan

## Overview

Implement hybrid A2A communication strategy for PAR:
- **Internal**: Use AWS Cloud Map Service Discovery for agent-to-agent calls
- **External**: Use NLB for external clients outside VPC

## Problem Statement

Current NLB target group has stale IP addresses because it's not integrated with ECS service auto-registration. This causes A2A health checks to fail and prevents external A2A communication.

## Solution Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     External Clients                         │
│                    (Outside AWS VPC)                         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
                ┌──────────────────────┐
                │   NLB (Port 50051)   │
                │  Auto-registered via │
                │    ECS Service       │
                └──────────┬───────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
    ┌─────────────────┐       ┌─────────────────┐
    │   PAR Instance  │       │   PAR Instance  │
    │  10.0.1.133     │       │  10.0.11.226    │
    └────────┬────────┘       └────────┬────────┘
             │                         │
             └────────────┬────────────┘
                          │
                          ▼
              ┌───────────────────────┐
              │  Service Discovery    │
              │ agents.pixell-runtime │
              │      .local           │
              └───────────────────────┘
                          ▲
                          │
              Internal A2A Calls
              (Use DNS lookup)
```

## Implementation Steps

### Phase 1: Add Service Discovery Query Support (PAR)

**File: `src/pixell_runtime/utils/service_discovery.py`**

Add method to discover healthy agent instances:

```python
def discover_agents(self, max_results: int = 10) -> list[dict]:
    """Discover healthy agent instances via Cloud Map.

    Args:
        max_results: Maximum number of instances to return

    Returns:
        List of dicts with keys: ipv4, port, instance_id, attributes
    """
    try:
        response = self.client.discover_instances(
            NamespaceName=self.namespace_name,
            ServiceName=self.service_name,
            MaxResults=max_results,
            HealthStatus='HEALTHY'
        )

        instances = []
        for inst in response.get('Instances', []):
            attrs = inst.get('Attributes', {})
            instances.append({
                'ipv4': attrs.get('AWS_INSTANCE_IPV4'),
                'port': int(attrs.get('AWS_INSTANCE_PORT', 50051)),
                'instance_id': inst.get('InstanceId'),
                'attributes': attrs
            })

        logger.info(
            "Discovered agent instances",
            count=len(instances),
            namespace=self.namespace_name,
            service=self.service_name
        )
        return instances

    except Exception as e:
        logger.error("Failed to discover instances", error=str(e))
        return []

def discover_agent_by_id(self, deployment_id: str) -> Optional[dict]:
    """Discover specific agent instance by deployment ID.

    Args:
        deployment_id: Deployment/instance ID to find

    Returns:
        Dict with ipv4, port if found, None otherwise
    """
    agents = self.discover_agents(max_results=100)
    for agent in agents:
        if agent['instance_id'] == deployment_id:
            return agent
    return None
```

**Estimated time:** 10 minutes

---

### Phase 2: Create A2A Client Module (PAR)

**New file: `src/pixell_runtime/a2a/client.py`**

Create reusable A2A client that intelligently chooses endpoint:

```python
"""A2A gRPC client with service discovery support."""

import os
from typing import Optional
import grpc
import structlog

from pixell_runtime.utils.service_discovery import get_service_discovery_client

logger = structlog.get_logger()


class A2AClient:
    """Client for A2A gRPC communication with service discovery."""

    def __init__(self, prefer_internal: bool = True):
        """Initialize A2A client.

        Args:
            prefer_internal: If True, prefer Service Discovery over external NLB
        """
        self.prefer_internal = prefer_internal
        self.sd_client = get_service_discovery_client()

    def get_agent_channel(
        self,
        deployment_id: Optional[str] = None,
        timeout: int = 30
    ) -> grpc.Channel:
        """Get gRPC channel to an agent.

        Strategy:
        1. If deployment_id provided and Service Discovery available:
           - Try to find specific agent by ID
        2. If prefer_internal and Service Discovery available:
           - Return channel to any healthy agent
        3. Fall back to external NLB endpoint

        Args:
            deployment_id: Optional specific deployment to target
            timeout: Connection timeout in seconds

        Returns:
            gRPC channel

        Raises:
            RuntimeError: If no agents available
        """
        # Try Service Discovery first (internal)
        if self.prefer_internal and self.sd_client:
            if deployment_id:
                agent = self.sd_client.discover_agent_by_id(deployment_id)
                if agent:
                    endpoint = f"{agent['ipv4']}:{agent['port']}"
                    logger.info("Using Service Discovery (specific agent)",
                               deployment_id=deployment_id, endpoint=endpoint)
                    return grpc.insecure_channel(endpoint)

            # Get any healthy agent
            agents = self.sd_client.discover_agents(max_results=5)
            if agents:
                agent = agents[0]  # TODO: Add load balancing logic
                endpoint = f"{agent['ipv4']}:{agent['port']}"
                logger.info("Using Service Discovery (any agent)",
                           endpoint=endpoint, instance_id=agent['instance_id'])
                return grpc.insecure_channel(endpoint)

        # Fall back to external endpoint (NLB)
        external_endpoint = os.getenv('A2A_EXTERNAL_ENDPOINT')
        if external_endpoint:
            logger.info("Using external A2A endpoint", endpoint=external_endpoint)
            return grpc.insecure_channel(external_endpoint)

        # Last resort: try localhost (for local development)
        a2a_port = os.getenv('A2A_PORT', '50051')
        localhost_endpoint = f"localhost:{a2a_port}"
        logger.warning("No Service Discovery or external endpoint, using localhost",
                      endpoint=localhost_endpoint)
        return grpc.insecure_channel(localhost_endpoint)

    async def health_check(self, deployment_id: Optional[str] = None) -> bool:
        """Check health of an agent.

        Args:
            deployment_id: Optional specific deployment to check

        Returns:
            True if healthy, False otherwise
        """
        try:
            from pixell_runtime.proto import agent_pb2, agent_pb2_grpc

            channel = self.get_agent_channel(deployment_id=deployment_id)
            stub = agent_pb2_grpc.AgentServiceStub(channel)

            response = await stub.Health(agent_pb2.Empty(), timeout=2.0)
            return response.ok

        except Exception as e:
            logger.warning("A2A health check failed",
                          deployment_id=deployment_id, error=str(e))
            return False


# Global singleton
_a2a_client: Optional[A2AClient] = None


def get_a2a_client(prefer_internal: bool = True) -> A2AClient:
    """Get or create global A2A client instance."""
    global _a2a_client
    if _a2a_client is None:
        _a2a_client = A2AClient(prefer_internal=prefer_internal)
    return _a2a_client
```

**Estimated time:** 15 minutes

---

### Phase 3: Update REST Health Endpoint to Use New Client (PAR) [OPTIONAL]

**File: `src/pixell_runtime/rest/server.py`**

**Note:** This phase is optional for single-agent deployments. The current implementation checks localhost which is fine for single-agent mode. Only update this if you want to use Service Discovery for single-agent deployments too.

Update `/a2a/health` endpoint to use new A2A client:

```python
# Around line 249-261
@app.get("/a2a/health")
async def a2a_health_check():
    """A2A health check endpoint (HTTP shim for gRPC)."""
    if not package or not package.manifest.a2a:
        raise HTTPException(status_code=404, detail="A2A service not available")

    try:
        # Use new A2A client with Service Discovery
        from pixell_runtime.a2a.client import get_a2a_client

        client = get_a2a_client(prefer_internal=True)
        is_healthy = await client.health_check()

        if is_healthy:
            return {"ok": True, "service": "a2a", "timestamp": int(time.time() * 1000)}
        else:
            raise HTTPException(status_code=503, detail="A2A health check failed")

    except Exception as e:
        raise HTTPException(status_code=503, detail=f"A2A health failed: {e}")
```

**Estimated time:** 5 minutes

---

### Phase 4: Update Deployment Health Endpoint (PAR)

**File: `src/pixell_runtime/api/deploy.py`**

Add A2A health check to the existing `/deployments/{deployment_id}/health` endpoint:

```python
# Add import at top (around line 16)
from pixell_runtime.a2a.client import get_a2a_client

# Update the existing endpoint (line 89-121)
@router.get("/deployments/{deployment_id}/health")
async def deployment_health(deployment_id: str) -> Dict[str, Any]:
    manager = get_deploy_manager()
    record = manager.get(deployment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Deployment not found")

    # Map status to healthy boolean
    healthy = record.status == DeploymentStatus.HEALTHY

    # Build message based on status
    message = None
    if record.status == DeploymentStatus.DOWNLOADING:
        message = "Downloading package"
    elif record.status == DeploymentStatus.LOADING:
        message = "Loading package"
    elif record.status == DeploymentStatus.STARTING:
        message = "Starting runtime"
    elif record.status == DeploymentStatus.FAILED:
        message = record.details.get("error", "Deployment failed")

    # Add A2A health check for healthy deployments
    a2a_healthy = None
    if healthy and record.a2a_port:
        try:
            client = get_a2a_client(prefer_internal=True)
            a2a_healthy = await client.health_check(deployment_id=deployment_id)
            logger.info("A2A health check result",
                       deployment_id=deployment_id,
                       a2a_healthy=a2a_healthy)
        except Exception as e:
            logger.warning("A2A health check failed",
                          deployment_id=deployment_id, error=str(e))
            a2a_healthy = False

    # Overall health includes A2A if it's configured
    overall_healthy = healthy
    if a2a_healthy is not None:
        overall_healthy = healthy and a2a_healthy

    return {
        "status": record.status.value,
        "healthy": overall_healthy,  # ← Required by PAC contract
        "message": message,
        "details": record.details,
        "surfaces": {
            "rest": record.rest_port is not None,
            "a2a": a2a_healthy if a2a_healthy is not None else False,
            "ui": record.ui_port is not None
        },
        # Keep ports for backward compatibility
        "ports": {
            "rest": record.rest_port,
            "a2a": record.a2a_port,
            "ui": record.ui_port,
        } if record.rest_port else None,
    }
```

**Why this file?**
- The `/deployments/{id}/health` endpoint is the contract between PAC and PAR
- PAC polls this endpoint to check deployment health
- Adding A2A health here ensures PAC knows when A2A communication is working

**Estimated time:** 10 minutes

---

### Phase 5: Update ECS Service Configuration (AWS)

Update ECS service to register with both target groups:

**Command:**
```bash
# Get current service configuration
aws ecs describe-services \
  --cluster pixell-runtime-cluster \
  --services pixell-runtime-multi-agent \
  --query 'services[0].loadBalancers' > current-lb-config.json

# Update service to add A2A target group
aws ecs update-service \
  --cluster pixell-runtime-cluster \
  --service pixell-runtime-multi-agent \
  --load-balancers \
    targetGroupArn=arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/par-multi-agent-tg/c28c15d19accbca4,containerName=par,containerPort=8080 \
    targetGroupArn=arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39,containerName=par,containerPort=50051 \
  --force-new-deployment
```

**Or update via task definition:**

Edit `deploy/ecs-task-definition.json` to ensure it's used by both services.

**Estimated time:** 5 minutes

---

### Phase 6: Add Environment Variable for External Endpoint (AWS)

Add `A2A_EXTERNAL_ENDPOINT` to ECS task definition:

```bash
aws ecs describe-task-definition \
  --task-definition pixell-runtime-multi-agent:12 \
  --query 'taskDefinition.containerDefinitions[0].environment' > env.json

# Add to env.json:
# {
#   "name": "A2A_EXTERNAL_ENDPOINT",
#   "value": "pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com:50051"
# }

# Register new task definition revision with updated environment
```

**Estimated time:** 5 minutes

---

### Phase 7: Testing

**Test Service Discovery:**
```bash
# From within VPC (or via Session Manager)
python3 -c "
from pixell_runtime.utils.service_discovery import get_service_discovery_client
client = get_service_discovery_client()
agents = client.discover_agents()
print(f'Found {len(agents)} agents:')
for agent in agents:
    print(f'  {agent}')
"
```

**Test A2A Client:**
```bash
# Test internal communication
python3 test_a2a_client.py  # Should use Service Discovery

# Test external communication (from laptop)
A2A_EXTERNAL_ENDPOINT=pixell-runtime-nlb-xxx.elb.us-east-2.amazonaws.com:50051 \
  python3 test_a2a_client.py
```

**Test Health Endpoints:**
```bash
# Test deployment health with A2A
curl http://pixell-runtime-alb-xxx.us-east-2.elb.amazonaws.com/deployments/{deployment_id}/health

# Test A2A health via REST
curl http://pixell-runtime-alb-xxx.us-east-2.elb.amazonaws.com/a2a/health
```

**Estimated time:** 15 minutes

---

## Summary

### Code Changes Required

| File | Changes | Lines | Time |
|------|---------|-------|------|
| `utils/service_discovery.py` | Add `discover_agents()` and `discover_agent_by_id()` | ~50 | 10 min |
| `a2a/client.py` (new) | Create A2A client with SD support | ~100 | 15 min |
| `rest/server.py` | Update `/a2a/health` endpoint | ~10 | 5 min |
| `api/deploy.py` | Update `/deployments/{id}/health` endpoint | ~30 | 10 min |

**Total PAR code changes:** ~190 lines, 40 minutes

### Infrastructure Changes

| Task | Time |
|------|------|
| Update ECS service load balancers | 5 min |
| Add A2A_EXTERNAL_ENDPOINT env var | 5 min |
| Testing and validation | 15 min |

**Total infrastructure changes:** 25 minutes

### Total Implementation Time: ~65 minutes

---

## Benefits

1. **Automatic failover**: Agents auto-discover healthy peers
2. **No stale IPs**: Service Discovery always returns current IPs
3. **Lower latency**: Direct container-to-container for internal calls
4. **External access**: NLB provides stable endpoint for outside clients
5. **Scalable**: Works seamlessly with ECS auto-scaling

---

## Rollback Plan

If issues arise:

1. Revert ECS service to single target group:
   ```bash
   aws ecs update-service --cluster pixell-runtime-cluster \
     --service pixell-runtime-multi-agent \
     --load-balancers targetGroupArn=arn:aws:...par-multi-agent-tg/...,containerName=par,containerPort=8080
   ```

2. Set `prefer_internal=False` in A2A client calls to use external endpoint only

3. Previous code still works - new code is additive, not replacing core functionality