# PAC Integration Guide - Envoy A2A Architecture

This document explains the changes required in PAC (Pixell Agent Cloud) to work with the new Envoy-based A2A architecture in PAR.

## Summary of Changes

**Before (Broken):** PAC tried to track individual agent ports and use NLB/Service Discovery to route to each agent.

**After (Working):** PAC uses a single NLB endpoint for all agents and adds `x-deployment-id` header for routing.

---

## Required PAC Code Changes

### 1. Update A2A Endpoint Configuration

**File:** Environment variables or configuration file

**Before:**
```bash
# Complex - needed to track each agent
PAR_API_URL=http://internal-alb.aws.com
```

**After:**
```bash
# Simple - single endpoint for all A2A traffic
PAR_API_URL=http://internal-alb.aws.com
PAR_A2A_ENDPOINT=pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com:50051
```

---

### 2. Simplify A2A Endpoint Lookup

**File:** `src/services/agent-communication.service.ts` (or similar)

**Before (Complex):**
```typescript
async getAgentA2AEndpoint(deploymentId: string): Promise<string> {
  // Query Service Discovery
  const sd = new ServiceDiscovery({ region: 'us-east-2' });
  const instances = await sd.discoverInstances({
    NamespaceName: 'pixell-runtime.local',
    ServiceName: 'agents',
    QueryParameters: { 'deployment_id': deploymentId }
  }).promise();

  if (!instances.Instances?.[0]) {
    throw new Error('Agent not found');
  }

  const ip = instances.Instances[0].Attributes.AWS_INSTANCE_IPV4;
  const port = instances.Instances[0].Attributes.AWS_INSTANCE_PORT;

  return `${ip}:${port}`;
}
```

**After (Simple):**
```typescript
getAgentA2AEndpoint(deploymentId: string): string {
  // All agents use the same endpoint!
  return process.env.PAR_A2A_ENDPOINT!;
  // Example: "pixell-runtime-nlb-xxx.elb.us-east-2.amazonaws.com:50051"
}
```

---

### 3. Add Routing Metadata to gRPC Calls

**File:** `src/grpc/client.ts` or wherever gRPC calls are made

**Before:**
```typescript
async callAgent(targetDeploymentId: string, request: ActionRequest) {
  const endpoint = await this.getA2AEndpoint(targetDeploymentId);
  const channel = grpc.createChannel(
    endpoint,
    grpc.credentials.createInsecure()
  );

  const client = new AgentServiceClient(channel);

  // Direct call
  return client.invoke(request);
}
```

**After (Add metadata):**
```typescript
async callAgent(targetDeploymentId: string, request: ActionRequest) {
  const endpoint = this.getA2AEndpoint(targetDeploymentId); // Same for all!
  const channel = grpc.createChannel(
    endpoint,
    grpc.credentials.createInsecure()
  );

  const client = new AgentServiceClient(channel);

  // Add x-deployment-id header for Envoy routing
  const metadata = new grpc.Metadata();
  metadata.set('x-deployment-id', targetDeploymentId);

  // Call with metadata
  return client.invoke(request, metadata);
}
```

**Key Change:** One line added: `metadata.set('x-deployment-id', targetDeploymentId)`

---

### 4. Remove Service Discovery Client

**Files to modify:**
- Remove AWS SDK Service Discovery imports
- Remove Service Discovery client initialization
- Remove IAM permissions for Service Discovery

**Before:**
```typescript
import { ServiceDiscovery } from 'aws-sdk';

class AgentService {
  private sd: ServiceDiscovery;

  constructor() {
    this.sd = new ServiceDiscovery({ region: 'us-east-2' });
  }

  // ... complex discovery logic
}
```

**After:**
```typescript
// No Service Discovery needed!
class AgentService {
  private readonly a2aEndpoint: string;

  constructor() {
    this.a2aEndpoint = process.env.PAR_A2A_ENDPOINT!;
  }

  // Simple!
}
```

---

### 5. Update Deployment Flow

**File:** `src/services/deployment.service.ts`

**Before (Track ports):**
```typescript
async deployAgent(request: DeploymentRequest) {
  // Send deploy request to PAR
  await this.parClient.deploy(request);

  // Poll health and store port info
  const health = await this.waitForHealthy(request.deploymentId);

  // Store port information in database
  await this.db.query(`
    UPDATE eventbridge_deployments
    SET
      rest_port = $1,
      a2a_port = $2,
      ui_port = $3,
      a2a_dns = $4
    WHERE id = $5
  `, [
    health.ports.rest,
    health.ports.a2a,
    health.ports.ui,
    `${deploymentId}.agents.pixell-runtime.local`,
    deploymentId
  ]);
}
```

**After (No port tracking):**
```typescript
async deployAgent(request: DeploymentRequest) {
  // Send deploy request to PAR
  await this.parClient.deploy(request);

  // Just wait for healthy status
  await this.waitForHealthy(request.deploymentId);

  // Update status only - no port tracking needed
  await this.db.query(`
    UPDATE eventbridge_deployments
    SET status = 'deployed', completed_at = NOW()
    WHERE id = $1
  `, [deploymentId]);
}
```

---

### 6. Update Health Check Handling

**File:** `src/services/par-client.service.ts` or similar

**Update to handle new health response format:**

```typescript
interface DeploymentHealth {
  status: string;
  healthy: boolean;  // ← New field (required by contract)
  message?: string;
  details?: Record<string, any>;
  ports?: {
    rest?: number;
    a2a?: number;
    ui?: number;
  };
}

async waitForHealthy(deploymentId: string): Promise<void> {
  const maxAttempts = 60;
  const delay = 2000; // 2 seconds

  for (let i = 0; i < maxAttempts; i++) {
    const health = await this.getDeploymentHealth(deploymentId);

    // Check new 'healthy' field
    if (health.healthy === true && health.status === 'healthy') {
      return;
    }

    if (health.status === 'failed') {
      throw new Error(`Deployment failed: ${health.message || 'Unknown error'}`);
    }

    await sleep(delay);
  }

  throw new Error('Deployment health check timeout');
}
```

---

## Database Changes

**No changes needed!**

You do **NOT** need to add these columns:
```sql
-- NOT NEEDED:
ALTER TABLE eventbridge_deployments ADD COLUMN rest_port INTEGER;
ALTER TABLE eventbridge_deployments ADD COLUMN a2a_port INTEGER;
ALTER TABLE eventbridge_deployments ADD COLUMN ui_port INTEGER;
ALTER TABLE eventbridge_deployments ADD COLUMN a2a_dns VARCHAR(255);
```

The `eventbridge_deployments` table stays as-is.

---

## IAM Permission Changes

**Remove Service Discovery permissions:**

```json
{
  "Effect": "Allow",
  "Action": [
    "servicediscovery:DiscoverInstances",
    "servicediscovery:GetInstance",
    "servicediscovery:ListInstances"
  ],
  "Resource": "*"
}
```

These are no longer needed!

---

## Testing Changes

### Test A2A Connectivity

```typescript
// Test file: tests/a2a-connectivity.test.ts
import { grpc } from '@grpc/grpc-js';
import { AgentServiceClient } from './proto/agent_grpc_pb';

describe('A2A Connectivity', () => {
  it('should route to agent via Envoy', async () => {
    const endpoint = process.env.PAR_A2A_ENDPOINT!;
    const channel = grpc.createChannel(
      endpoint,
      grpc.credentials.createInsecure()
    );

    const client = new AgentServiceClient(channel);

    const metadata = new grpc.Metadata();
    metadata.set('x-deployment-id', 'test-agent-deployment-id');

    const response = await client.invoke(
      {
        action: 'comment',
        parameters: { text: 'Test from PAC' }
      },
      metadata
    );

    expect(response.success).toBe(true);
  });
});
```

### Verify Metadata is Sent

```typescript
// Check that gRPC metadata is being sent correctly
const metadata = new grpc.Metadata();
metadata.set('x-deployment-id', 'abc123');

console.log('Metadata:', metadata.toJSON());
// Should output: { 'x-deployment-id': 'abc123' }
```

---

## Migration Checklist

### Phase 1: Code Changes (PAC Team)
- [ ] Add `PAR_A2A_ENDPOINT` environment variable
- [ ] Update `getAgentA2AEndpoint()` to return single endpoint
- [ ] Add `x-deployment-id` metadata to all gRPC calls
- [ ] Remove Service Discovery client code
- [ ] Update health check to use `healthy` field
- [ ] Remove port tracking in deployment flow

### Phase 2: Testing
- [ ] Test A2A calls in staging with metadata header
- [ ] Verify single endpoint works for all agents
- [ ] Test agent deployment flow
- [ ] Test agent-to-agent communication

### Phase 3: Cleanup
- [ ] Remove unused Service Discovery imports
- [ ] Remove Service Discovery IAM permissions
- [ ] Update documentation

---

## Benefits Summary

✅ **Simpler code** - No complex Service Discovery queries
✅ **Fewer dependencies** - No AWS SDK Service Discovery client
✅ **Faster A2A calls** - No endpoint resolution overhead
✅ **Easier testing** - Single endpoint to mock
✅ **Better security** - Fewer IAM permissions
✅ **Horizontal scaling** - NLB load balances across PAR instances automatically

---

## Troubleshooting

### A2A calls failing with "UNAVAILABLE"

**Check:** Is `x-deployment-id` header being sent?

```typescript
// Log metadata before call
console.log('Sending metadata:', metadata.toJSON());
```

### Agent not found error

**Check:** Is the deployment ID correct?

```bash
# Query PAR directly
curl http://par-alb/deployments/{deployment-id}/health
```

### Envoy not routing

**Check Envoy logs:**
```bash
aws logs tail /ecs/pixell-runtime --region us-east-2 --filter-pattern "envoy" --since 5m
```

---

## Questions?

- **Q: What if we scale to multiple PAR instances?**
  - A: NLB automatically load balances. Each instance has its own Envoy that routes to its local agents.

- **Q: Do agents need code changes?**
  - A: No! Agents are unaware of Envoy. They continue running on their assigned ports.

- **Q: What about REST API calls?**
  - A: No changes! REST still goes through ALB to PAR's management API (port 8000).

---

## Example: Complete Integration

```typescript
// src/services/agent-communication.service.ts
export class AgentCommunicationService {
  private readonly a2aEndpoint: string;
  private clients: Map<string, AgentServiceClient> = new Map();

  constructor() {
    this.a2aEndpoint = process.env.PAR_A2A_ENDPOINT!;
    if (!this.a2aEndpoint) {
      throw new Error('PAR_A2A_ENDPOINT environment variable required');
    }
  }

  private getClient(): AgentServiceClient {
    // Reuse channel for all agents (same endpoint!)
    if (!this.clients.has('shared')) {
      const channel = grpc.createChannel(
        this.a2aEndpoint,
        grpc.credentials.createInsecure()
      );
      this.clients.set('shared', new AgentServiceClient(channel));
    }
    return this.clients.get('shared')!;
  }

  async invokeAgent(
    targetDeploymentId: string,
    request: ActionRequest
  ): Promise<ActionResult> {
    const client = this.getClient();

    // Add routing metadata
    const metadata = new grpc.Metadata();
    metadata.set('x-deployment-id', targetDeploymentId);

    try {
      return await client.invoke(request, metadata);
    } catch (error) {
      if (error.code === grpc.status.NOT_FOUND) {
        throw new Error(`Agent ${targetDeploymentId} not found`);
      }
      throw error;
    }
  }

  async healthCheck(targetDeploymentId: string): Promise<boolean> {
    const client = this.getClient();
    const metadata = new grpc.Metadata();
    metadata.set('x-deployment-id', targetDeploymentId);

    try {
      const response = await client.health({}, metadata);
      return response.ok;
    } catch {
      return false;
    }
  }
}
```

---

**Ready to implement?** Start with Phase 1 code changes, test in staging, then deploy to production.