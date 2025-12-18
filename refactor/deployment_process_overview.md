# PIXELL AGENT CLOUD (PAC) - DEPLOYMENT ARCHITECTURE ANALYSIS

**Document Version:** 1.0
**Date:** November 1, 2025
**Author:** Claude Code (Architecture Analysis)
**Scope:** Complete deployment flow from upload to runtime

---

## EXECUTIVE SUMMARY

Pixell Agent Cloud is a **multi-tenant agent hosting platform** that deploys and manages custom agent applications on AWS infrastructure. The system currently uses an **EC2-based multi-agent deployment model** where multiple agent apps run on a single EC2 instance managed by a Python supervisor (PAR - Pixell Agent Runtime).

**Current Deployment Model:** EC2 Multi-Agent with Port-Based Routing
**Region:** us-east-2 (Ohio)
**Primary VPCs:** 2 (Web VPC + Runtime VPC)
**Current Scale:** 1 EC2 instance (m7g.medium) running ~3 agent apps

---

## SYSTEM ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PIXELL AGENT CLOUD (PAC)                        │
│                   Deployment & Runtime Management System                │
└─────────────────────────────────────────────────────────────────────────┘

┌──────────────────────┐         ┌─────────────────────────────────────┐
│   Developer/User     │         │       External Traffic              │
│   (CLI/Web UI)       │         │   (A2A Clients, API Consumers)      │
└──────────┬───────────┘         └───────────┬─────────────────────────┘
           │                                  │
           │ 1. Upload .apkg                  │ 4. Invoke Agent
           │    (POST /api/agent-apps/        │    (POST /api/agents/
           │     {id}/packages/deploy)        │     {id}/a2a/...)
           ↓                                  ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                         PAC WEB SERVICE (Fargate)                       │
│                    vpc-0dc5816f0b041abad (px-vpc)                       │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  ALB: pac-alb.us-east-2.elb.amazonaws.com                      │   │
│  │  DNS: cloud.pixell.global → ALB                                │   │
│  │  ├─ Listener HTTPS:443                                         │   │
│  │  │  ├─ /api/* → pac-web-tg-port-3000 (Fargate tasks)          │   │
│  │  │  └─ /* → pac-web-tg-port-3000 (Next.js web UI)             │   │
│  └────────────────────────────────────────────────────────────────┘   │
│           │                                                             │
│           ↓                                                             │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  ECS Service: pixell-agent-cloud-web-service                   │   │
│  │  Cluster: pixell-runtime-cluster                               │   │
│  │  Task Definition: pixell-agent-cloud-web (Fargate)             │   │
│  │  ├─ Container: web                                             │   │
│  │  │  ├─ Image: 636212886452.dkr.ecr../pixell-agent-cloud       │   │
│  │  │  ├─ Port: 3000 (Next.js server)                             │   │
│  │  │  ├─ ENV: DB_HOST, NEXTAUTH_SECRET, ECS_*, ALB_ARN           │   │
│  │  │  └─ Secrets: AWS Secrets Manager (pac/mysql)                │   │
│  │  └─ Resources: 512 CPU, 1024MB RAM                             │   │
│  └────────────────────────────────────────────────────────────────┘   │
│           │                                                             │
│           │ 2. Create Deployment Record                                │
│           ↓                                                             │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  RDS MySQL: database-3.cmkyt9c4u4iq.us-east-2.rds.amazonaws.com│  │
│  │  Database: pac                                                  │   │
│  │  Tables:                                                        │   │
│  │  ├─ eventbridge_deployments (deployment queue)                 │   │
│  │  ├─ ec2_agent_deployments (active deployments)                 │   │
│  │  ├─ packages (uploaded .apkg metadata)                         │   │
│  │  ├─ agent_apps (agent app registry)                            │   │
│  │  ├─ organizations (tenants)                                    │   │
│  │  └─ api_keys (authentication)                                  │   │
│  └────────────────────────────────────────────────────────────────┘   │
│           │                                                             │
│           │ 3. Trigger EventBridge Event                               │
│           ↓                                                             │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  EventBridge Rule: pac-deployment-processor                     │   │
│  │  Event Pattern: {                                               │   │
│  │    source: "pixell.agent.cloud",                                │   │
│  │    detail-type: "DeploymentQueued"                              │   │
│  │  }                                                               │   │
│  │  Target: ECS Task (pac-deployment-worker)                       │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
           │
           │ 4. Launch Worker Task
           ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                    DEPLOYMENT WORKER (Fargate Task)                     │
│                    vpc-0039e5988107ae565 (runtime VPC)                  │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  ECS Task: pac-deployment-worker (one-time task)                │   │
│  │  Triggered by: EventBridge event                                │   │
│  │  Image: 636212886452.dkr.ecr../pixell-agent-cloud:worker       │   │
│  │  Entry: npx tsx src/workers/deployment-worker-eventbridge.ts   │   │
│  │  Resources: 256 CPU, 512MB RAM                                  │   │
│  │                                                                  │   │
│  │  Processing Steps:                                              │   │
│  │  1. Fetch deployment from eventbridge_deployments table         │   │
│  │  2. Download .apkg from temp storage                            │   │
│  │  3. Upload to S3: s3://pixell-agent-packages/{id}/{ver}.apkg   │   │
│  │  4. Extract environment variables from .apkg                    │   │
│  │  5. Call ec2-multi-agent.ts:provisionAgent()                   │   │
│  │     ├─ Select EC2 instance (instance-selector.ts)              │   │
│  │     ├─ Allocate ports (port allocator)                         │   │
│  │     ├─ Create/update ALB target groups                         │   │
│  │     ├─ Deploy via PAR supervisor gRPC                          │   │
│  │     └─ Wait for health checks                                  │   │
│  │  6. Update deployment status to 'completed'                     │   │
│  │  7. Task exits                                                  │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
           │
           │ 5. Deploy to PAR Supervisor (gRPC)
           ↓
┌─────────────────────────────────────────────────────────────────────────┐
│             PIXELL AGENT RUNTIME (PAR) - EC2 Multi-Agent                │
│                    vpc-0039e5988107ae565 (runtime VPC)                  │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  EC2 Instance: i-09dcb7f387166efd0 (pixell-agent-runtime)      │   │
│  │  Type: m7g.medium (1 vCPU, 4GB RAM, ARM64)                      │   │
│  │  Private IP: 10.0.1.37                                          │   │
│  │  Public IP: 18.119.137.118                                      │   │
│  │  OS: Amazon Linux 2023                                          │   │
│  │                                                                  │   │
│  │  ┌──────────────────────────────────────────────────────────┐  │   │
│  │  │  PAR Supervisor (Python gRPC Server)                     │  │   │
│  │  │  Port: 9000 (gRPC management API)                        │  │   │
│  │  │  Source: pixell-agent-runtime repo                       │  │   │
│  │  │                                                           │  │   │
│  │  │  Responsibilities:                                        │  │   │
│  │  │  ├─ Receive deploy/update/delete requests via gRPC       │  │   │
│  │  │  ├─ Create Linux user per agent (agent_{short_id})      │  │   │
│  │  │  ├─ Download .apkg from S3                              │  │   │
│  │  │  ├─ Extract to /home/agent_{short_id}/                  │  │   │
│  │  │  ├─ Start agent process (uvicorn/gunicorn)              │  │   │
│  │  │  ├─ Monitor health via HTTP polling                     │  │   │
│  │  │  └─ Report status back to worker                        │  │   │
│  │  └──────────────────────────────────────────────────────────┘  │   │
│  │                                                                  │   │
│  │  ┌──────────────────────────────────────────────────────────┐  │   │
│  │  │  Agent Processes (Isolated Linux Users)                  │  │   │
│  │  │                                                           │  │   │
│  │  │  ┌─────────────────────────────────────────────────┐    │  │   │
│  │  │  │ Agent 1: agent_4906eeb7                         │    │  │   │
│  │  │  │ REST Port: 63000                                 │    │  │   │
│  │  │  │ gRPC Port: 60000                                 │    │  │   │
│  │  │  │ UI Port: 66000                                   │    │  │   │
│  │  │  │ Process: Python FastAPI (uvicorn)                │    │  │   │
│  │  │  │ Working Dir: /home/agent_4906eeb7/               │    │  │   │
│  │  │  └─────────────────────────────────────────────────┘    │  │   │
│  │  │                                                           │  │   │
│  │  │  ┌─────────────────────────────────────────────────┐    │  │   │
│  │  │  │ Agent 2: agent_ed8784f3                         │    │  │   │
│  │  │  │ REST Port: 63001                                 │    │  │   │
│  │  │  │ gRPC Port: 60001                                 │    │  │   │
│  │  │  │ UI Port: 66001                                   │    │  │   │
│  │  │  │ Process: Python FastAPI (uvicorn)                │    │  │   │
│  │  │  │ Working Dir: /home/agent_ed8784f3/               │    │  │   │
│  │  │  └─────────────────────────────────────────────────┘    │  │   │
│  │  │                                                           │  │   │
│  │  │  ┌─────────────────────────────────────────────────┐    │  │   │
│  │  │  │ Agent 3: agent_c489095f                         │    │  │   │
│  │  │  │ REST Port: 63002                                 │    │  │   │
│  │  │  │ gRPC Port: 60002                                 │    │  │   │
│  │  │  │ UI Port: 66002                                   │    │  │   │
│  │  │  │ Process: Python FastAPI (uvicorn)                │    │  │   │
│  │  │  │ Working Dir: /home/agent_c489095f/               │    │  │   │
│  │  │  └─────────────────────────────────────────────────┘    │  │   │
│  │  └──────────────────────────────────────────────────────────┘  │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
           │
           │ 6. Register with ALB
           ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                       APPLICATION LOAD BALANCER (ALB)                   │
│                    vpc-0039e5988107ae565 (runtime VPC)                  │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │  ALB: pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com │   │
│  │  DNS: cloud.pixell.global → ALB (via CloudFront/Route53)       │   │
│  │  Security Group: sg-0f5b28ee64419e95d                           │   │
│  │                                                                  │   │
│  │  ┌──────────────────────────────────────────────────────────┐  │   │
│  │  │  HTTPS Listener :443                                      │  │   │
│  │  │                                                           │  │   │
│  │  │  ┌─────────────────────────────────────────────────┐    │  │   │
│  │  │  │ Rule 1: Path = /agents/4906eeb7-*/a2a/*         │    │  │   │
│  │  │  │ Target Group: pac-agent-4906eeb7-grpc           │    │  │   │
│  │  │  │ Port: 60000 (gRPC/HTTP2)                        │    │  │   │
│  │  │  │ Protocol: HTTP2                                 │    │  │   │
│  │  │  │ Health: GET /agents/4906eeb7-*/health           │    │  │   │
│  │  │  │ Targets: 10.0.1.37:60000 (EC2 instance)         │    │  │   │
│  │  │  └─────────────────────────────────────────────────┘    │  │   │
│  │  │                                                           │  │   │
│  │  │  ┌─────────────────────────────────────────────────┐    │  │   │
│  │  │  │ Rule 2: Path = /agents/4906eeb7-*/rest/*        │    │  │   │
│  │  │  │ Target Group: pac-agent-4906eeb7-rest           │    │  │   │
│  │  │  │ Port: 63000 (REST/HTTP)                         │    │  │   │
│  │  │  │ Protocol: HTTP                                  │    │  │   │
│  │  │  │ Health: GET /agents/4906eeb7-*/health           │    │  │   │
│  │  │  │ Targets: 10.0.1.37:63000 (EC2 instance)         │    │  │   │
│  │  │  └─────────────────────────────────────────────────┘    │  │   │
│  │  │                                                           │  │   │
│  │  │  [... Similar rules for ed8784f3, c489095f ...]          │  │   │
│  │  └──────────────────────────────────────────────────────────┘  │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## DETAILED DEPLOYMENT FLOW

### Step-by-Step: Deploying an Agent Package

```
1. UPLOAD PHASE
===============
Developer → PAC Web API
  POST /api/agent-apps/{agent_app_id}/packages/deploy
  Authorization: Bearer sk_live_XXXXXXXX
  Content-Type: multipart/form-data
  Body:
    - file: agent.apkg (ZIP archive)
    - version: "1.0.0"
    - force_overwrite: true (optional)
    - expose_mode: "multiplex" (optional)
    - expose: "rest,a2a,ui" (optional)

PAC Web Service (route.ts:10-446)
  ├─ Validate API key (validateDeploymentAuth)
  ├─ Calculate SHA256 hash of .apkg file
  ├─ Validate package structure (validatePackage)
  ├─ Check credit balance
  ├─ Check PAR capacity (checkPARCapacity)
  ├─ Create/update package record in MySQL
  │  └─ INSERT INTO packages (id, agent_app_id, version, sha256, ...)
  ├─ Upload .apkg to temp storage (/tmp/deployments/)
  │  └─ File path: /tmp/deployments/{deployment_id}/{version}.apkg
  ├─ Create deployment job in MySQL
  │  └─ INSERT INTO eventbridge_deployments
  │     (id, package_id, agent_app_id, status='pending', ...)
  └─ Send EventBridge event
     └─ Event: DeploymentQueued { deploymentId, agentAppId, version }

Response: HTTP 202 Accepted
{
  "deployment": {
    "id": "uuid-xxx",
    "status": "queued",
    "queued_at": "2025-11-01T12:00:00Z"
  },
  "tracking": {
    "status_url": "/api/deployments/{id}"
  }
}

2. PROCESSING PHASE
===================
EventBridge → ECS Task (deployment-worker)
  Rule: pac-deployment-processor
  Target: ECS Fargate Task
  Cluster: pixell-runtime-cluster
  Task Definition: pac-deployment-worker
  Network: vpc-0039e5988107ae565 (runtime VPC)
  Subnets: subnet-0b0e8734fc88867f7, subnet-0a79126c8f2c8f05c
  Security Group: sg-01fadbe4320c283f7

Worker Task Starts (deployment-worker-eventbridge.ts)
  ├─ Fetch deployment record from MySQL
  │  └─ SELECT * FROM eventbridge_deployments WHERE id = ?
  ├─ Claim job (prevent duplicate processing)
  │  └─ UPDATE eventbridge_deployments
  │     SET status='processing', worker_task_arn='ecs-task-xxx'
  │     WHERE id=? AND status='pending'
  ├─ Download .apkg from temp storage
  │  └─ Read file: /tmp/deployments/{deployment_id}/{version}.apkg
  ├─ Upload to S3
  │  └─ PUT s3://pixell-agent-packages/{package_id}/{version}.apkg
  ├─ Extract package metadata
  │  └─ Read agent.yaml from .apkg (ZIP)
  │  └─ Extract environment variables
  ├─ Call processDeployment() (worker.ts:128)
  └─ Call provisionAgent() (ec2-multi-agent.ts)

3. EC2 PROVISIONING PHASE
==========================
provisionAgent() (ec2-multi-agent.ts:173-500)
  ├─ Clean up existing deployment (if any)
  │  ├─ SELECT * FROM ec2_agent_deployments
  │  │  WHERE agent_app_id=? AND status NOT IN ('replaced','deleted')
  │  ├─ Delete agent from PAR supervisor (supervisorDeleteAgent)
  │  │  └─ gRPC call: 10.0.1.37:9000/supervisor.SupervisorService/DeleteAgent
  │  ├─ Deregister from ALB target groups
  │  │  └─ aws elbv2 deregister-targets --target-group-arn ... --targets Id=i-xxx,Port=63000
  │  └─ UPDATE ec2_agent_deployments SET status='replaced'
  │
  ├─ Select EC2 instance (instance-selector.ts:selectInstance)
  │  ├─ Query active instances from database
  │  │  └─ SELECT * FROM ec2_instances WHERE status='active'
  │  ├─ Calculate available capacity per instance
  │  │  └─ Check: (totalCPU - usedCPU) >= requiredCPU
  │  │          (totalMemory - usedMemory) >= requiredMemory
  │  └─ Return instance with most available capacity
  │     └─ Selected: i-09dcb7f387166efd0 (10.0.1.37)
  │
  ├─ Allocate ports (ports/allocator.ts:allocatePort)
  │  ├─ Query available ports from port_allocations table
  │  │  └─ SELECT * FROM port_allocations
  │  │     WHERE instance_id=? AND is_allocated=false
  │  ├─ Allocate REST port: 63000-63999 range
  │  ├─ Allocate gRPC (A2A) port: 60000-60999 range
  │  ├─ Allocate UI port: 66000-66999 range
  │  └─ Mark ports as allocated in database
  │     └─ UPDATE port_allocations SET is_allocated=true, agent_app_id=?
  │
  ├─ Fetch agent secrets (aws/secrets.ts)
  │  └─ aws secretsmanager get-secret-value --secret-id agent-{id}
  │
  ├─ Resolve environment variables (template-resolver.ts)
  │  ├─ Template: "API_KEY={{secrets.api_key}}"
  │  ├─ Resolve: "API_KEY=sk_live_12345"
  │  └─ Validate: All placeholders resolved
  │
  ├─ Create/update ALB target groups (aws/alb.ts)
  │  ├─ Ensure REST target group
  │  │  ├─ Name: pac-agent-{short_id}-rest
  │  │  ├─ Port: 63000
  │  │  ├─ Protocol: HTTP
  │  │  ├─ Health check: GET /agents/{id}/health on port 63000
  │  │  └─ Create if not exists, update if exists
  │  │
  │  ├─ Ensure gRPC target group
  │  │  ├─ Name: pac-agent-{short_id}-grpc
  │  │  ├─ Port: 60000
  │  │  ├─ Protocol: HTTP2 (CRITICAL for gRPC!)
  │  │  ├─ Health check: GET /agents/{id}/health on port 60000
  │  │  └─ Create if not exists
  │  │     ⚠️ CANNOT update ProtocolVersion if already exists!
  │  │        Must delete and recreate if wrong protocol.
  │  │
  │  ├─ Register EC2 instance as target
  │  │  └─ aws elbv2 register-targets
  │  │     --target-group-arn arn:aws:...:pac-agent-xxx-rest
  │  │     --targets Id=i-09dcb7f387166efd0,Port=63000
  │  │
  │  └─ Create ALB listener rules
  │     ├─ Rule for REST: /agents/{id}/rest/* → rest target group
  │     └─ Rule for gRPC: /agents/{id}/a2a/* → grpc target group
  │
  └─ Deploy to PAR supervisor (supervisor/client.ts)
     └─ gRPC call: deployAgent(supervisorIp, params)
        Host: 10.0.1.37:9000
        Service: supervisor.SupervisorService/DeployAgent
        Request: {
          agentId: "4906eeb7-...",
          packageUrl: "s3://pixell-agent-packages/{id}/{ver}.apkg",
          restPort: 63000,
          grpcPort: 60000,
          uiPort: 66000,
          environment: { API_KEY: "...", ... },
          cpuLimit: 256,
          memoryLimit: 512
        }

4. PAR SUPERVISOR PHASE
========================
PAR Supervisor Receives gRPC Request (10.0.1.37:9000)
  ├─ Validate request
  ├─ Create Linux user: agent_4906eeb7
  │  └─ useradd -m -s /bin/bash agent_4906eeb7
  ├─ Download package from S3
  │  └─ aws s3 cp s3://pixell-agent-packages/{id}/{ver}.apkg /tmp/
  ├─ Extract to agent home directory
  │  └─ unzip /tmp/{ver}.apkg -d /home/agent_4906eeb7/
  ├─ Set ownership
  │  └─ chown -R agent_4906eeb7:agent_4906eeb7 /home/agent_4906eeb7/
  ├─ Create Python virtual environment
  │  └─ python3 -m venv /home/agent_4906eeb7/venv
  ├─ Install dependencies
  │  └─ /home/agent_4906eeb7/venv/bin/pip install -r requirements.txt
  ├─ Start agent process as Linux user
  │  └─ sudo -u agent_4906eeb7 bash -c "
  │     cd /home/agent_4906eeb7 &&
  │     source venv/bin/activate &&
  │     uvicorn main:app --host 0.0.0.0 --port 63000 &
  │     uvicorn main:grpc_app --host 0.0.0.0 --port 60000 &
  │     "
  ├─ Monitor process (poll health endpoint)
  │  └─ GET http://localhost:63000/agents/{id}/health
  │  └─ Retry every 3s for 60s max
  └─ Return success response to worker
     └─ { status: "running", pid: 12345 }

5. FINALIZATION PHASE
=====================
Worker Task Continues (deployment-worker-eventbridge.ts:132-140)
  ├─ Record deployment in ec2_agent_deployments table
  │  └─ INSERT INTO ec2_agent_deployments
  │     (deployment_id, agent_app_id, instance_id, instance_ip,
  │      rest_port, a2a_port, ui_port, status='active', ...)
  │
  ├─ Update deployment status
  │  └─ UPDATE eventbridge_deployments
  │     SET status='completed', progress=100, completed_at=NOW()
  │     WHERE id=?
  │
  ├─ Clean up temp files
  │  └─ rm -rf /tmp/deployments/{deployment_id}/
  │
  └─ Exit task (worker terminates)

6. RUNTIME PHASE
================
Agent is now LIVE and accessible via:
  - REST API: https://cloud.pixell.global/agents/{id}/rest/...
  - gRPC (A2A): https://cloud.pixell.global/agents/{id}/a2a/...
  - UI: https://cloud.pixell.global/agents/{id}/ui/...

All traffic flows through:
  Client → ALB (pixell-runtime-alb) → EC2:port → Agent process
```

---

## AWS RESOURCE INVENTORY

### VPCs and Networking

```
VPC 1: px-vpc (vpc-0dc5816f0b041abad)
  ├─ CIDR: 172.31.0.0/16
  ├─ Region: us-east-2
  ├─ Purpose: PAC Web Service (Fargate)
  ├─ Subnets:
  │  ├─ subnet-035d6ed0a581e57df (us-east-2a)
  │  ├─ subnet-059d23db977f85843 (us-east-2b)
  │  └─ subnet-0fd1d99dab3fdf17b (us-east-2c)
  ├─ Security Groups:
  │  └─ sg-017f2185976c6fb0d (PAC web service SG)
  └─ Load Balancers:
     └─ pac-alb (pac-alb-2089685514.us-east-2.elb.amazonaws.com)
        └─ DNS: cloud.pixell.global

VPC 2: pixell-runtime-vpc (vpc-0039e5988107ae565)
  ├─ CIDR: 10.0.0.0/16
  ├─ Region: us-east-2
  ├─ Purpose: Runtime agents, workers, EC2 instances
  ├─ Subnets:
  │  ├─ subnet-0c17f7bdd92e9e20a (agents, ALB)
  │  ├─ subnet-01afa3db23e73c46d (agents, ALB)
  │  ├─ subnet-0b0e8734fc88867f7 (workers)
  │  └─ subnet-0a79126c8f2c8f05c (workers)
  ├─ Security Groups:
  │  ├─ sg-063217792cd7a39d9 (pixell-runtime-ecs)
  │  ├─ sg-01fadbe4320c283f7 (pixell-runtime-sg, workers)
  │  ├─ sg-0c13cfb5da4e67ea7 (pixell-agent-runtime-sg, EC2)
  │  └─ sg-0f5b28ee64419e95d (pixell-runtime-alb SG)
  └─ Load Balancers:
     └─ pixell-runtime-alb (pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com)
        ├─ Target Groups: pac-agent-*-rest, pac-agent-*-grpc
        └─ DNS: Routed via cloud.pixell.global (CloudFront?)
```

### EC2 Instances

```
Instance: i-09dcb7f387166efd0
  ├─ Name: pixell-agent-runtime
  ├─ Type: m7g.medium (1 vCPU, 4GB RAM, ARM64)
  ├─ State: running
  ├─ VPC: vpc-0039e5988107ae565 (runtime VPC)
  ├─ Private IP: 10.0.1.37
  ├─ Public IP: 18.119.137.118
  ├─ Security Group: sg-0c13cfb5da4e67ea7
  ├─ IAM Role: (likely pixell-runtime-instance-role)
  ├─ Running:
  │  ├─ PAR Supervisor (Python gRPC server on port 9000)
  │  ├─ Agent processes (3 deployed):
  │  │  ├─ agent_4906eeb7 (ports 60000, 63000, 66000)
  │  │  ├─ agent_ed8784f3 (ports 60001, 63001, 66001)
  │  │  └─ agent_c489095f (ports 60002, 63002, 66002)
  │  └─ Monitoring/logging daemons
  └─ Registered in ALB target groups:
     ├─ pac-agent-4906eeb7-rest:63000
     ├─ pac-agent-4906eeb7-grpc:60000
     ├─ pac-agent-ed8784f3-rest:63001
     ├─ pac-agent-ed8784f3-grpc:60001
     ├─ pac-agent-c489095f-rest:63002
     └─ pac-agent-c489095f-grpc:60002
```

### ECS Resources

```
Cluster: pixell-runtime-cluster
  ├─ ARN: arn:aws:ecs:us-east-2:636212886452:cluster/pixell-runtime-cluster
  ├─ Services:
  │  ├─ pixell-agent-cloud-web-service (Fargate)
  │  │  ├─ Task Definition: pixell-agent-cloud-web:XX
  │  │  ├─ Desired Count: 1
  │  │  ├─ Launch Type: FARGATE
  │  │  ├─ VPC: vpc-0dc5816f0b041abad (px-vpc)
  │  │  ├─ Subnets: subnet-035d6ed0a581e57df, ...
  │  │  └─ Target Group: pac-web-tg-port-3000
  │  │
  │  └─ pixell-runtime (PAR service - NOT USED in current setup)
  │     └─ Replaced by EC2-based PAR
  │
  └─ Task Definitions:
     ├─ pixell-agent-cloud-web (web service)
     │  ├─ Image: 636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-agent-cloud:latest
     │  ├─ CPU: 512, Memory: 1024
     │  ├─ Port: 3000
     │  └─ Secrets: pac/mysql (AWS Secrets Manager)
     │
     └─ pac-deployment-worker (deployment processor)
        ├─ Image: 636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-agent-cloud:worker
        ├─ CPU: 256, Memory: 512
        ├─ Entry: npx tsx src/workers/deployment-worker-eventbridge.ts
        └─ Network: runtime VPC (must reach PAR supervisor)
```

### S3 Buckets

```
Bucket: pixell-agent-packages (us-east-2)
  ├─ Purpose: Agent package storage
  ├─ Structure:
  │  ├─ {package_id}/{version}.apkg (final packages)
  │  ├─ deployments/ (temp uploads, deleted after processing)
  │  └─ packages/ (organized by package ID)
  └─ Access:
     ├─ PAC web service (upload)
     ├─ Deployment worker (upload to final location)
     └─ PAR supervisor (download for deployment)
```

### RDS Database

```
Instance: database-3
  ├─ Endpoint: database-3.cmkyt9c4u4iq.us-east-2.rds.amazonaws.com
  ├─ Engine: MySQL 8.0
  ├─ Status: available
  ├─ VPC: (likely px-vpc or peered)
  ├─ Database: pac
  ├─ Credentials: Stored in AWS Secrets Manager (pac/mysql)
  └─ Tables:
     ├─ eventbridge_deployments (deployment queue/history)
     ├─ ec2_agent_deployments (active agent deployments)
     ├─ packages (uploaded .apkg metadata)
     ├─ agent_apps (agent app registry)
     ├─ organizations (multi-tenancy)
     ├─ api_keys (authentication)
     ├─ port_allocations (port management)
     └─ ec2_instances (instance registry)
```

### EventBridge

```
Rule: pac-deployment-processor
  ├─ Event Pattern:
  │  {
  │    "source": ["pixell.agent.cloud"],
  │    "detail-type": ["DeploymentQueued"]
  │  }
  ├─ Target: ECS Task
  │  ├─ Cluster: pixell-runtime-cluster
  │  ├─ Task Definition: pac-deployment-worker:latest
  │  ├─ Launch Type: FARGATE
  │  ├─ Network:
  │  │  ├─ VPC: vpc-0039e5988107ae565 (runtime VPC)
  │  │  ├─ Subnets: subnet-0b0e8734fc88867f7, subnet-0a79126c8f2c8f05c
  │  │  └─ Security Group: sg-01fadbe4320c283f7
  │  └─ IAM Role: arn:aws:iam::636212886452:role/pac-eventbridge-ecs-role
  └─ State: ENABLED
```

### Secrets Manager

```
Secret: pac/mysql
  ├─ ARN: arn:aws:secretsmanager:us-east-2:636212886452:secret:pac/mysql-XXXXXX
  ├─ Description: MySQL and application credentials for Pixell Agent Cloud
  ├─ Keys:
  │  ├─ DB_HOST: database-3.cmkyt9c4u4iq.us-east-2.rds.amazonaws.com
  │  ├─ DB_NAME: pac
  │  ├─ DB_USER: pac
  │  ├─ DB_PASSWORD: PACPixell2025!
  │  ├─ DB_PORT: 3306
  │  ├─ NEXTAUTH_SECRET: pixell-nextauth-secret-2025-mysql
  │  ├─ NEXTAUTH_URL: https://cloud.pixell.global
  │  ├─ SESSION_SECRET: ...
  │  ├─ STRIPE_SECRET_KEY: sk_live_...
  │  ├─ S3_ACCESS_KEY_ID: AKIAZIIJ6HO2EJWO2ZH4
  │  ├─ S3_SECRET_ACCESS_KEY: ...
  │  └─ AWS_ACCOUNT_ID: 636212886452
  └─ Used by:
     ├─ PAC web service (ECS task)
     └─ Deployment worker (ECS task)
```

### ECR Repositories

```
Registry: 636212886452.dkr.ecr.us-east-2.amazonaws.com

Repositories:
  ├─ pixell-agent-cloud
  │  ├─ latest (web service image)
  │  ├─ worker (deployment worker image)
  │  └─ deploy-YYYYMMDDHHMMSS (versioned deployments)
  │
  └─ pixell-runtime
     └─ latest (PAR supervisor image - not used in EC2 setup)
```

---

## KEY CONFIGURATION FILES

### Deployment Scripts

1. **scripts/deploy_cloud.sh** (Main deployment script)
   - Builds Docker images for web + worker
   - Pushes to ECR
   - Updates ECS task definitions
   - Updates EventBridge targets
   - Manages secrets in Secrets Manager
   - Handles rollback on failure

2. **scripts/create-ec2-runtime-instance.sh**
   - Creates EC2 instance for PAR
   - Installs dependencies
   - Starts PAR supervisor

### API Routes (Next.js)

1. **src/app/api/agent-apps/[id]/packages/deploy/route.ts**
   - Handles .apkg uploads
   - Validates packages and authentication
   - Creates deployment records
   - Triggers EventBridge events

2. **src/workers/deployment-worker-eventbridge.ts**
   - Processes deployment queue
   - Downloads .apkg from temp storage
   - Uploads to S3
   - Calls ec2-multi-agent provisioning

### Library Code

1. **src/lib/deployment/ec2-multi-agent.ts**
   - Main orchestrator for EC2 deployments
   - Manages instance selection
   - Allocates ports
   - Creates ALB target groups
   - Deploys via PAR supervisor

2. **src/lib/supervisor/client.ts**
   - gRPC client for PAR supervisor
   - Calls deployAgent, updateAgent, deleteAgent

3. **src/lib/ports/allocator.ts**
   - Port allocation logic
   - Manages port_allocations table

4. **src/lib/aws/alb.ts**
   - ALB target group management
   - Creates listener rules
   - Registers/deregisters targets

---

## CURRENT LIMITATIONS & ISSUES

### 1. **Port Exhaustion Risk**
- Each agent consumes 3 ports (REST, gRPC, UI)
- Single EC2 instance has ~64K ports total
- Practical limit: ~1,000-2,000 agents per instance
- Current allocation ranges:
  - REST: 63000-63999 (1,000 ports)
  - gRPC: 60000-60999 (1,000 ports)
  - UI: 66000-66999 (1,000 ports)

### 2. **No Horizontal Scaling for EC2**
- Only 1 EC2 instance currently deployed
- No auto-scaling configured
- Manual intervention required to add capacity

### 3. **ALB Target Group Protocol Issue**
- ProtocolVersion is IMMUTABLE after creation
- Broken HTTP1 target groups for gRPC cannot be fixed
- Must manually delete and recreate
- Documented in ec2-multi-agent.ts:1-115

### 4. **Resource Isolation**
- Agents share same EC2 instance
- One heavy agent can starve others
- Linux user isolation only (no CPU/memory limits enforced)

### 5. **Single Point of Failure**
- 1 EC2 instance handles all agent traffic
- If instance fails, all agents go down
- No automatic failover

---

## COST ANALYSIS (Current Setup)

```
Monthly AWS Costs (Estimated):

EC2:
  └─ m7g.medium (1 instance)
     ├─ On-Demand: ~$35/month
     └─ Spot: ~$10-15/month (70% savings)

RDS MySQL:
  └─ db.t3.micro
     └─ ~$15/month

ECS Fargate:
  ├─ PAC Web Service (running 24/7)
  │  └─ 512 CPU, 1024MB: ~$15/month
  └─ Deployment Workers (on-demand, ~5 min/deployment)
     └─ 256 CPU, 512MB: ~$0.01 per deployment

S3:
  └─ pixell-agent-packages
     └─ Storage + requests: ~$5/month (for 100s of packages)

ALB:
  ├─ pixell-runtime-alb: ~$20/month
  └─ pac-alb: ~$20/month

Secrets Manager:
  └─ pac/mysql: ~$0.40/month

EventBridge:
  └─ Free tier (1M events/month)

Data Transfer:
  └─ Varies ($0.09/GB out)

TOTAL: ~$120-$130/month (current usage)
```

**Scaling to 10,000 agents:**
- With current model: Would need 10-20 EC2 instances
- Estimated cost: ~$1,700/month (EC2 Spot) + overhead
- Alternative: Multi-tenant process model ~$3,000/month

---

## RECOMMENDATIONS

Based on the analysis, here are recommendations for improving the architecture:

### Short Term (1-2 weeks)
1. **Add EC2 auto-scaling** - Configure ASG to add instances when CPU >80%
2. **Implement health monitoring** - Add CloudWatch alarms for instance health
3. **Fix target group creation** - Ensure HTTP2 protocol for all gRPC target groups

### Medium Term (1-2 months)
4. **Multi-instance support** - Update code to handle >1 EC2 instance
5. **Resource limits** - Enforce CPU/memory limits per agent (cgroups)
6. **Port management improvements** - Better port allocation algorithm

### Long Term (3-6 months)
7. **Consider Unix domain sockets** - Replace port-based routing (eliminates port exhaustion)
8. **Implement Redis registry** - For multi-machine agent routing
9. **ECS Spot instances** - Migrate to ECS on EC2 Spot for cost optimization

---

## POTENTIAL REFACTORING: SOCKET-BASED ROUTING

### Current: Port-Based Model
```
Problem: Each agent needs 3 unique TCP ports
- Limits scalability to ~1,000 agents per instance
- Port management complexity
- Network overhead for localhost connections

Example:
  Agent A: ports 60000, 63000, 66000
  Agent B: ports 60001, 63001, 66001
  ...
  Agent N: ports 60999, 63999, 66999 (limit reached!)
```

### Proposed: Socket-Based Model
```
Solution: Use Unix domain sockets instead of TCP ports
- No port exhaustion (sockets are files)
- Faster communication (no TCP overhead)
- Simpler routing (filesystem-based)

Example:
  /var/run/agents/agent-{id}/rest.sock
  /var/run/agents/agent-{id}/grpc.sock
  /var/run/agents/agent-{id}/ui.sock
```

### Implementation Changes Required
1. **PAR Supervisor**: Start agents with socket bindings instead of ports
2. **Gateway/Proxy**: Route based on agent_id → socket path lookup
3. **ALB**: Single target group per surface type (REST, gRPC, UI)
4. **Health Checks**: Connect via sockets instead of HTTP ports

### Benefits
- **Eliminate port exhaustion** - Scale to 10,000+ agents on one instance
- **Reduce latency** - Unix sockets are 2-3× faster than localhost TCP
- **Simplify networking** - No port allocation/deallocation logic
- **Lower memory** - No TCP connection tracking overhead

### Trade-offs
- **Single machine only** - Sockets don't work across network
- **Requires proxy** - Need HTTP→socket gateway (nginx, envoy, custom)
- **ALB integration** - Need reverse proxy on EC2 to expose via ALB

---

**Document Status:** Complete
**Last Updated:** November 1, 2025
**Next Review:** When architectural changes are proposed
