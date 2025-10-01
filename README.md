# Pixell Agent Runtime (PAR)

Lightweight hosting layer for serving Agent Packages (APKGs) with support for A2A (Agent-to-Agent), REST, and UI surfaces.

## Architecture Overview

### Runtime Modes

#### 1. Three-Surface Mode (Single Agent)
Hosts a single agent package with up to three communication surfaces:
- **A2A** - gRPC server for Agent-to-Agent communication (default: port 50051)
- **REST** - HTTP API endpoints (default: port 8080)
- **UI** - Web interface (default: port 3000, or multiplexed with REST)

**Activation:** Set `AGENT_PACKAGE_PATH` environment variable

**Use Case:** Dedicated deployment for a specific agent requiring isolation

#### 2. Multi-Agent Mode (Agent Platform)
Hosts multiple agent packages dynamically within a single runtime:
- Runtime management API (ports 8080, 9090)
- Each deployed agent gets isolated A2A/REST/UI surfaces
- Dynamic port allocation (REST: 8080-8180, A2A: 50051-50151, UI: 3000-3100)
- Deploy/undeploy agents via API without container restarts
- Configurable capacity via `MAX_AGENTS` (default: 20)

**Activation:** Default mode (no `AGENT_PACKAGE_PATH` set)

**Use Case:** Platform for managing multiple agents efficiently

---

## Multi-Agent Mode: A2A Communication via Envoy Proxy

### Problem Statement

In multi-agent mode, each deployed agent requires unique ports:
```
Agent 1: REST=8080,  A2A=50051, UI=3000
Agent 2: REST=8081,  A2A=50052, UI=3001
Agent 3: REST=8082,  A2A=50053, UI=3002
...up to MAX_AGENTS
```

**Challenge:** Network Load Balancers (NLB) can only route to a single port, but agents use different A2A ports (50051, 50052, 50053...). Direct port mapping to each agent is impractical.

**Solution:** Use **Envoy Proxy sidecar with AWS App Mesh** to multiplex all A2A traffic through a single port (50051), with Envoy routing internally based on agent deployment ID.

### Architecture Design

```
┌──────────────────────────────────────────────────────────────────┐
│                         Internet                                  │
└────────────┬──────────────────────────────────┬──────────────────┘
             │                                   │
             │ HTTP/HTTPS (80/443)               │ gRPC (50051)
             │                                   │
    ┌────────▼────────┐                 ┌───────▼──────────┐
    │  Application    │                 │  Network Load    │
    │  Load Balancer  │                 │  Balancer (NLB)  │
    │     (ALB)       │                 │                  │
    └────────┬────────┘                 └───────┬──────────┘
             │                                   │
             │ Path: /agents/:id/*               │ Port: 50051
             │                                   │ (All A2A traffic)
             │                                   │
    ┌────────▼───────────────────────────────────▼──────────┐
    │           ECS Service (Fargate)                       │
    │    Service Registry: par.pixell-runtime.local         │
    └───────────────────────────────────────────────────────┘
             │
             │
    ┌────────▼─────────────────────────────────────────────┐
    │  ECS Task (PAR + Envoy Sidecar)                      │
    │  ┌────────────────────────────────────────────────┐  │
    │  │                                                 │  │
    │  │  ┌──────────────┐      ┌───────────────────┐  │  │
    │  │  │   Envoy      │      │  PAR Runtime      │  │  │
    │  │  │   Proxy      │      │  Port: 8000       │  │  │
    │  │  │ Port: 50051  │◄─────┤  Management API   │  │  │
    │  │  │ (External)   │      │                   │  │  │
    │  │  │              │      │  ┌─────────────┐  │  │  │
    │  │  │  Routes by   │      │  │ Agent A     │  │  │
    │  │  │  x-deploy-id │─────▶│  │ A2A: 50051  │  │  │
    │  │  │              │      │  │ REST: 8080  │  │  │
    │  │  │              │      │  └─────────────┘  │  │  │
    │  │  │              │      │                   │  │  │
    │  │  │              │      │  ┌─────────────┐  │  │  │
    │  │  │              │─────▶│  │ Agent B     │  │  │
    │  │  │              │      │  │ A2A: 50052  │  │  │
    │  │  │              │      │  │ REST: 8081  │  │  │
    │  │  └──────────────┘      │  └─────────────┘  │  │  │
    │  │         ▲               │                   │  │  │
    │  │         │               │  ... MAX_AGENTS   │  │  │
    │  │         │               └───────────────────┘  │  │
    │  │         │                                      │  │
    │  │    x-deployment-id                            │  │
    │  │    header routing                             │  │
    │  └────────────────────────────────────────────────┘  │
    └───────────────────────────────────────────────────────┘
```

### Traffic Flow

#### REST API Traffic (via ALB)
1. Client sends `GET https://pixell.example.com/agents/abc123/invoke`
2. ALB routes to ECS service's **management port 8000**
3. Runtime receives request, extracts agent ID `abc123`
4. Runtime **internally proxies** to agent's REST port (e.g., 8080)
5. Response flows back through ALB to client

**Key Point:** ALB only needs to route to the runtime's management API. The runtime handles internal routing to agent-specific ports.

#### A2A gRPC Traffic (via NLB + Envoy Proxy)
1. Client connects to `grpc://pixell-runtime-nlb:50051`
2. Client adds gRPC metadata: `x-deployment-id: abc123`
3. NLB routes to **Envoy proxy** (port 50051)
4. Envoy reads `x-deployment-id` metadata
5. Envoy looks up agent's internal port (e.g., 50051)
6. Envoy forwards to `127.0.0.1:50051` (agent's A2A server)
7. Response flows back: Agent → Envoy → NLB → Client

**Key Points:**
- All agents accessible via **single NLB endpoint** (pixell-runtime-nlb:50051)
- Envoy handles routing based on `x-deployment-id` gRPC metadata header
- No service discovery queries needed from clients
- PAC simply adds one header to all A2A calls

### Implementation Components

#### 1. Envoy Proxy Sidecar Container

Envoy runs as a sidecar container alongside PAR in the same ECS task:

```json
{
  "containerDefinitions": [
    {
      "name": "envoy",
      "image": "public.ecr.aws/appmesh/aws-appmesh-envoy:v1.27.0.0-prod",
      "essential": true,
      "environment": [
        {"name": "APPMESH_RESOURCE_ARN", "value": "arn:aws:appmesh:..."}
      ],
      "portMappings": [
        {"containerPort": 50051, "protocol": "tcp"},
        {"containerPort": 9901, "protocol": "tcp"}
      ],
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -f http://localhost:9901/ready || exit 1"]
      }
    },
    {
      "name": "pixell-runtime",
      "image": "636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-runtime:latest",
      "essential": true,
      "dependsOn": [
        {"containerName": "envoy", "condition": "HEALTHY"}
      ],
      "portMappings": [
        {"containerPort": 8000, "protocol": "tcp"}
      ]
    }
  ]
}
```

#### 2. AWS App Mesh Configuration

App Mesh provides managed Envoy configuration and service mesh capabilities:

```bash
# Create App Mesh
aws appmesh create-mesh --mesh-name pixell-runtime-mesh

# Create virtual node for PAR
aws appmesh create-virtual-node \
  --mesh-name pixell-runtime-mesh \
  --virtual-node-name par-node \
  --spec '{
    "listeners": [{
      "portMapping": {"port": 50051, "protocol": "grpc"}
    }],
    "serviceDiscovery": {
      "awsCloudMap": {
        "namespaceName": "pixell-runtime.local",
        "serviceName": "par"
      }
    }
  }'

# Create virtual service
aws appmesh create-virtual-service \
  --mesh-name pixell-runtime-mesh \
  --virtual-service-name par.pixell-runtime.local \
  --spec '{"provider": {"virtualNode": {"virtualNodeName": "par-node"}}}'
```

#### 3. ECS Service with Service Registry

Link ECS service to Cloud Map for PAR instance discovery:

```bash
aws ecs create-service \
  --cluster pixell-runtime-cluster \
  --service-name pixell-runtime \
  --task-definition pixell-runtime:latest \
  --desired-count 1 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[...],securityGroups=[...]}" \
  --service-registries "registryArn=<service-discovery-arn>,containerName=envoy,containerPort=50051"
```

#### 4. Target Groups Configuration

**ALB Target Group (REST API):**
- Protocol: HTTP
- Port: 8080 (management API)
- Health Check: `/health`
- Target Type: `ip`

**NLB Target Group (A2A gRPC):**
- Protocol: TCP
- Port: 50051 (first agent's default port)
- Target Type: `ip`
- Deregistration Delay: 30s (for faster failover)

#### 5. Runtime Internal Routing

The runtime must implement request routing logic:

```python
# REST API: Route by agent ID
@app.api_route("/agents/{agent_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_to_agent(agent_id: str, path: str, request: Request):
    agent_deployment = deployment_manager.get_deployment(agent_id)
    if not agent_deployment:
        raise HTTPException(404, "Agent not found")

    # Proxy to agent's REST port
    agent_url = f"http://localhost:{agent_deployment.rest_port}/{path}"
    # Forward request and return response
    ...

# A2A: Each agent registers DNS via service discovery
# Format: {agent_id}.agents.pixell-runtime.local -> task_ip:dynamic_port
```

### Deployment Workflow

1. **Client Deploys Agent Package**
   ```bash
   POST /deploy
   {
     "agentAppId": "abc123",
     "version": "1.0.0",
     "package_location": "s3://apkg-registry/abc123.apkg"
   }
   ```

2. **Runtime Allocates Resources**
   - Download APKG from S3
   - Find available ports (REST: 8081, A2A: 50052, UI: 3001)
   - Start agent subprocess with allocated ports
   - Register agent in deployment manager

3. **Service Discovery Registration**
   - Runtime registers agent's A2A endpoint in Cloud Map
   - DNS record: `abc123.agents.pixell-runtime.local` → `10.0.1.214:32769`

4. **Client Connects**
   - REST: `https://pixell.example.com/agents/abc123/invoke`
   - A2A: Resolve `abc123.agents.pixell-runtime.local`, connect via gRPC

### Scaling Strategy

#### Vertical Scaling (Single Task)
- Increase task CPU/Memory (up to 16 vCPU, 120 GB)
- Increase `MAX_AGENTS` to 50-100
- Monitor resource utilization per agent

#### Horizontal Scaling (Multiple Tasks)
- Run multiple ECS tasks (desired count: 3+)
- ALB distributes REST traffic across tasks
- Each task hosts up to `MAX_AGENTS` agents
- Total capacity: `tasks × MAX_AGENTS`

#### Agent Placement Strategy
Option A: **Hash-based Routing** (preferred)
- Hash agent ID to determine target task
- Ensures agent always runs on same task
- Simplifies A2A discovery (predictable DNS)

Option B: **First-Available**
- Deploy to first task with available capacity
- Requires dynamic DNS updates on migration
- Better resource utilization

### Current Infrastructure

**Existing Resources:**
- ALB: `pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com`
- NLB: `pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com`
- Target Group (REST): `par-multi-agent-tg` → port 8080
- Target Group (A2A): `pixell-runtime-a2a-tg` → port 50051
- Security Group: `sg-01fadbe4320c283f7` (allows 8080, 9090, 50051)

**Required Changes:**
1. Create AWS Cloud Map namespace and service
2. Update ECS task definition to use dynamic port mapping
3. Link ECS service to service registry
4. Implement runtime's internal routing logic
5. Update agent deployment flow to register in service discovery

---

## Implementation Plan

### Phase 1: Service Discovery Setup
```bash
# 1. Create Cloud Map namespace
aws servicediscovery create-private-dns-namespace \
  --name pixell-runtime.local \
  --vpc vpc-0039e5988107ae565 \
  --description "Service discovery for PAR agents"

# 2. Create service discovery service for agents
aws servicediscovery create-service \
  --name agents \
  --namespace-id <namespace-id-from-step-1> \
  --dns-config "NamespaceId=<namespace-id>,DnsRecords=[{Type=SRV,TTL=10}]" \
  --health-check-custom-config FailureThreshold=1
```

### Phase 2: Update ECS Configuration
```bash
# 1. Register new task definition with dynamic port mapping
aws ecs register-task-definition --cli-input-json file://task-def-dynamic-ports.json

# 2. Update service to use service registry
aws ecs update-service \
  --cluster pixell-runtime-cluster \
  --service pixell-runtime-multi-agent \
  --task-definition pixell-runtime-multi-agent:latest \
  --service-registries "registryArn=<service-discovery-arn>,containerName=par,containerPort=50051" \
  --force-new-deployment
```

### Phase 3: Runtime Code Updates
1. Implement REST proxy routing by agent ID (`/agents/{id}/*`)
2. Add service discovery registration on agent deployment
3. Update agent startup to use allocated ports from environment
4. Add DNS resolution helper for A2A client connections

### Phase 4: Testing
1. Deploy test agent via API
2. Verify DNS resolution: `dig @169.254.169.253 {agent-id}.agents.pixell-runtime.local`
3. Test REST API: `curl https://alb-url/agents/{agent-id}/health`
4. Test A2A: `grpcurl {agent-id}.agents.pixell-runtime.local:50051 list`

---

## Configuration

### Environment Variables

**Three-Surface Mode:**
- `AGENT_PACKAGE_PATH` - Path to APKG file (activates three-surface mode)
- `REST_PORT` - REST API port (default: 8080)
- `A2A_PORT` - gRPC A2A port (default: 50051)
- `UI_PORT` - UI server port (default: 3000)
- `MULTIPLEXED` - Serve UI from REST server (default: true)
- `BASE_PATH` - Base URL path for REST/UI mounting

**Multi-Agent Mode:**
- `PORT` - Management API port (default: 8080)
- `ADMIN_PORT` - Admin interface port (default: 9090)
- `MAX_AGENTS` - Maximum concurrent agents (default: 20)
- `RUNTIME_MODE` - Set to "multi-agent"
- `RUNTIME_INSTANCE_ID` - Unique runtime identifier

**Common:**
- `LOG_LEVEL` - Logging level (default: INFO)
- `LOG_FORMAT` - Log format: json or text (default: json)
- `METRICS_ENABLED` - Enable Prometheus metrics (default: true)

---

## API Endpoints

### Management API (Multi-Agent Mode)

**Runtime Health:**
```
GET /health
GET /runtime/health
```

**Agent Deployment:**
```
POST /deploy
POST /runtime/deploy
Body: {
  "agentAppId": "string",
  "version": "string",
  "package_location": "s3://bucket/package.apkg"
}
```

**Deployment Health:**
```
GET /deployments/{deployment_id}/health
GET /runtime/deployments/{deployment_id}/health
```

**Agent Invocation (Proxied):**
```
POST /agents/{agent_id}/invoke
Body: agent-specific parameters
```

**Admin Interface:**
```
GET /admin/deployments  (port 9090)
GET /admin/metrics      (port 9090)
```

---

## Development

### Local Development
```bash
# Install dependencies
pip install -e .

# Run in three-surface mode
export AGENT_PACKAGE_PATH=./examples/sample-agent.apkg
python -m pixell_runtime

# Run in multi-agent mode
export RUNTIME_MODE=multi-agent
export MAX_AGENTS=5
python -m pixell_runtime
```

### Testing
```bash
# Run all tests
pytest

# Run specific test
pytest tests/test_deploy_api.py::test_deploy_agent

# Test A2A connection
python test_a2a_client.py localhost:50051
```

### Deployment
```bash
# Build Docker image
docker build -t pixell-runtime:latest .

# Push to ECR
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin 636212886452.dkr.ecr.us-east-2.amazonaws.com
docker tag pixell-runtime:latest 636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-runtime-multi-agent:latest
docker push 636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-runtime-multi-agent:latest

# Deploy to ECS
./scripts/deploy_par.sh
```

---

## Troubleshooting

### A2A Connection Refused

**Problem:** `grpc.StatusCode.UNAVAILABLE: failed to connect`

**Common Causes:**
1. **Missing port mapping:** ECS task definition doesn't expose A2A port
   - Check: `aws ecs describe-task-definition --task-definition <name>`
   - Fix: Add `{"containerPort": 50051, "protocol": "tcp"}` to portMappings

2. **Missing A2A_PORT env var:** Runtime doesn't start gRPC server
   - Check: Task definition environment variables
   - Fix: Add `{"name": "A2A_PORT", "value": "50051"}`

3. **Wrong runtime mode:** Multi-agent mode without deployed agents
   - Check: `curl http://<ip>:8080/health` - look for `"agents": 0`
   - Fix: Deploy an agent via POST /deploy, or use three-surface mode

4. **Security group blocks port:** Port not allowed in AWS security group
   - Check: `aws ec2 describe-security-groups --group-ids <sg-id>`
   - Fix: Add inbound rule for TCP port 50051

5. **Agent not deployed:** In multi-agent mode, no agent running yet
   - Check: Health endpoint shows `"agents": 0`
   - Fix: Deploy agent package via API

### Multiple Agents Can't Connect

**Problem:** Only first agent accessible via A2A

**Cause:** ECS task only exposes port 50051, but agents 2-N use ports 50052+

**Solution:** Implement dynamic port mapping (see architecture above)

---

## Performance Targets

- **Cold Start:** < 5s for 30MB APKG
- **In-Process Latency:** < 150ms (p95)
- **Network Latency:** < 300ms (p95)
- **Concurrent Agents:** 20+ per task (2 vCPU, 4GB RAM)
- **Throughput:** 1000+ req/s per runtime instance

---

## Security

- **Authentication:** OIDC via PAF ID tokens (planned)
- **Package Verification:** SHA-256 + signature validation
- **Sandboxing:** Process isolation per agent
- **Network:** VPC private subnets with NAT gateway
- **Secrets:** AWS Secrets Manager integration
- **Observability:** CloudWatch Logs + Prometheus metrics

---

## License

MIT License - see LICENSE file for details