# PAC Deployment Flow - Agent Deployment Architecture

**Document Version:** 1.0
**Date:** December 2, 2025
**Repository:** pixell-agent-cloud
**Purpose:** Document how PAC deploys agents to EC2 instances

---

## DEPLOYMENT OVERVIEW

PAC (Pixell Agent Cloud) orchestrates agent deployment through a 6-step pipeline that provisions agents on EC2 instances running PAR (Pixell Agent Runtime) supervisor.

### High-Level Flow
```
PAC Database                    PAC Worker                      PAR Supervisor
     │                              │                                │
     │ 1. Create deployment         │                                │
     │    (status: pending)         │                                │
     │◄─────────────────────────────│                                │
     │                              │                                │
     │ 2. Claim deployment          │                                │
     │    (status: processing)      │                                │
     │◄─────────────────────────────│                                │
     │                              │                                │
     │ 3. Allocate ports           │                                │
     │◄─────────────────────────────│                                │
     │                              │                                │
     │                              │ 4. POST /agents                │
     │                              │──────────────────────────────►│
     │                              │                                │
     │                              │    Response: ports, linux_user │
     │                              │◄──────────────────────────────│
     │                              │                                │
     │ 5. Create ALB resources      │                                │
     │◄─────────────────────────────│                                │
     │                              │                                │
     │ 6. Complete deployment       │                                │
     │    (status: deployed)        │                                │
     │◄─────────────────────────────│                                │
```

---

## KEY FILES IN PAC

| File | Lines | Purpose |
|------|-------|---------|
| `src/lib/deployment/ec2-multi-agent.ts` | 646 | Main provisioning orchestrator |
| `src/lib/aws/alb.ts` | 1400+ | ALB target groups and listener rules |
| `src/lib/supervisor/client.ts` | 434 | HTTP client to PAR supervisor |
| `src/lib/ports/allocator.ts` | 512 | Port allocation from database |
| `src/lib/deployment/worker.ts` | 631 | 6-step deployment pipeline |
| `src/workers/deployment-worker-eventbridge.ts` | 171 | EventBridge task executor |

---

## DEPLOYMENT STEPS IN DETAIL

### Step 1: Validate Package (10%)
- Validates package exists and is valid
- Checks package metadata

### Step 2: Upload to S3 (30%)
- Uploads `.apkg` file to S3 bucket `pixell-agent-packages`
- Computes SHA256 hash for cache invalidation
- S3 URL format: `s3://pixell-agent-packages/{org_id}/{agent_app_id}/{version}/{filename}.apkg`

### Step 3: Generate Metadata (50%)
- Extracts agent metadata from package
- Resolves environment variable templates with secrets

### Step 4: Provision Runtime (70%)
**File:** `src/lib/deployment/ec2-multi-agent.ts`

**Sub-steps:**
1. **Select EC2 Instance**
   - Query `ec2_instances` table for healthy instance with capacity
   - Returns instance IP for supervisor API calls

2. **Allocate Ports**
   - Uses database transaction with `FOR UPDATE` lock
   - Finds first available slot (0-199)
   - Assigns 3 ports per agent:
     - A2A: `60000 + slot_number`
     - REST: `63000 + slot_number`
     - UI: `65000 + slot_number`

3. **Create ALB Target Groups**
   - Creates 2 target groups per agent:
     - `pac-agent-{shortId}-rest` (HTTP1, port 63000+)
     - `pac-agent-{shortId}-grpc` (HTTP2, port 60000+)
   - **CRITICAL:** gRPC target group MUST use HTTP2 protocol version

4. **Deploy via Supervisor API**
   - POST to `http://{instanceIp}:9000/agents`
   - Body: `DeployAgentParams` with ports, package URL, environment

5. **Register Targets**
   - Register EC2 instance:port with target groups
   - Instance ID + port combination

6. **Create Listener Rules**
   - Creates 3 rules per agent on ALB HTTPS:443 listener:
     - `/agents/{id}/api*` → REST target group
     - `/agents/{id}/a2a*` → gRPC target group
     - `/agents/{id}/*` → REST target group (fallback)

### Step 5: Health Check (90%)
- Waits for target groups to become healthy
- Polls ALB target health API

### Step 6: Finalize (100%)
- Marks deployment as 'deployed'
- Records public URL: `https://par.pixell.global/agents/{agent_app_id}`

---

## SUPERVISOR CLIENT API

**File:** `src/lib/supervisor/client.ts`

### Deploy Agent Request
```typescript
interface DeployAgentParams {
  agent_app_id: string;       // Full agent UUID
  deployment_id: string;      // PAC deployment ID
  package_url: string;        // S3 URL to .apkg file
  version: string;            // Package version
  org_id: string;             // Organization ID
  org_short_id: string;       // 16-char org short ID
  agent_short_id: string;     // 8-char agent short ID
  ports?: {                   // Pre-allocated ports from PAC
    rest: number;             // 63000-63199
    a2a: number;              // 60000-60199
    ui: number;               // 65000-65199
  };
  package_sha256?: string;    // SHA256 for cache invalidation
  env?: Record<string, string>; // Resolved environment variables
}
```

### Deploy Agent Response
```typescript
interface DeployAgentResponse {
  status: string;             // "running"
  linux_user: string;         // "agent_org_short_agent_short"
  ports: {
    rest: number;
    a2a: number;
    ui: number;
  };
}
```

### API Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/agents` | Deploy new agent |
| PUT | `/agents/{id}` | Update agent |
| DELETE | `/agents/{id}` | Delete agent |
| GET | `/agents/{id}/status` | Get agent status |
| GET | `/health` | Instance health and capacity |

---

## PORT ALLOCATION

**File:** `src/lib/ports/allocator.ts`

### Port Ranges (Per Instance)
| Service | Range | Capacity |
|---------|-------|----------|
| A2A (gRPC) | 60000-60199 | 200 agents |
| REST API | 63000-63199 | 200 agents |
| UI Server | 65000-65199 | 200 agents |

### Allocation Flow
```
1. BEGIN TRANSACTION
2. SELECT slot FROM ec2_port_allocations
   WHERE instance_id = ? AND status = 'available'
   ORDER BY slot_number ASC
   LIMIT 1 FOR UPDATE
3. UPDATE ec2_port_allocations
   SET agent_app_id = ?, status = 'allocated', allocated_at = NOW()
   WHERE slot_number = ?
4. COMMIT
5. Return {a2a: 60000+slot, rest: 63000+slot, ui: 65000+slot}
```

### Port Lifecycle States
| State | Description |
|-------|-------------|
| `available` | Slot can be allocated |
| `allocated` | Slot reserved, deployment in progress |
| `in_use` | Agent running on these ports |
| `released` | Agent deleted, slot freed |

---

## ALB CONFIGURATION

**File:** `src/lib/aws/alb.ts`

### Current ALB Resources
| Resource | Value |
|----------|-------|
| **ALB Name** | `px-pixell-runtime-alb` |
| **ALB ARN** | `arn:aws:elasticloadbalancing:us-east-2:636212886452:loadbalancer/app/px-pixell-runtime-alb/bc04340265e7343e` |
| **HTTPS Listener** | Port 443 |
| **VPC** | `vpc-0dc5816f0b041abad` (px-vpc) |

### Target Group Naming Convention
```
pac-agent-{shortId}-rest   # HTTP1, port 63000+slot
pac-agent-{shortId}-grpc   # HTTP2, port 60000+slot
```

### Listener Rule Priority Calculation
```typescript
// From agent app ID, compute base priority
const hash = hashString(agentId) % 10000;
const base = hash + 100;

// Rule priorities:
// API rule:  base
// A2A rule:  base + 1
// UI rule:   base + 2
```

### Path Patterns
| Rule | Pattern | Target |
|------|---------|--------|
| API | `/agents/{id}/api*` | REST target group |
| A2A | `/agents/{id}/a2a*` | gRPC target group |
| UI | `/agents/{id}/*` | REST target group (fallback) |

### CRITICAL: HTTP2 for gRPC
**Problem:** If gRPC target group uses HTTP1, clients receive "464 Incompatible Protocol" error.

**Root Cause:** gRPC servers ONLY accept HTTP/2. HTTP1 requests are rejected.

**Solution:** Target group must be created with `ProtocolVersion: HTTP2`.

**AWS Limitation:** `ProtocolVersion` is IMMUTABLE after creation - only solution is delete and recreate.

---

## DATABASE TABLES

### ec2_agent_deployments
| Column | Type | Description |
|--------|------|-------------|
| deployment_id | string | PAC deployment ID |
| agent_app_id | string | Agent UUID |
| instance_id | string | EC2 instance ID |
| instance_ip | string | Private IP |
| rest_port | int | REST port (63000+) |
| a2a_port | int | A2A port (60000+) |
| ui_port | int | UI port (65000+) |
| linux_user | string | Agent user name |
| status | string | Deployment status |

### ec2_port_allocations
| Column | Type | Description |
|--------|------|-------------|
| instance_id | string | EC2 instance ID |
| slot_number | int | Slot 0-199 |
| agent_app_id | string | Assigned agent (or NULL) |
| a2a_port | int | 60000 + slot |
| rest_port | int | 63000 + slot |
| ui_port | int | 65000 + slot |
| status | string | available/allocated/in_use/released |

### alb_target_groups
| Column | Type | Description |
|--------|------|-------------|
| agent_app_id | string | Agent UUID |
| tg_arn | string | AWS target group ARN |
| tg_name | string | Target group name |
| surface | string | rest or grpc |
| protocol_version | string | HTTP1 or HTTP2 |
| port | int | Target port |
| vpc_id | string | VPC ID |

---

## CHANGES NEEDED FOR SOCKET MODE

### PAC Code Changes

**File:** `src/lib/deployment/ec2-multi-agent.ts`
- Add `socket_mode: true` to deploy request
- Remove port allocation (not needed for sockets)
- Skip per-agent target group creation
- Skip per-agent listener rule creation

**File:** `src/lib/aws/alb.ts`
- Remove `ensureAgentTargetGroups()` calls for socket mode
- Remove `ensureAgentListenerRules()` calls for socket mode
- Use shared proxy target groups instead

**File:** `src/lib/supervisor/client.ts`
- Update `DeployAgentParams` to include `socket_mode: boolean`
- Remove `ports` field when socket_mode is true

### Database Changes
- Add `socket_mode` column to `ec2_agent_deployments`
- Port allocation tables become optional (only for port mode)

---

## REQUEST FLOW COMPARISON

### Current (Port Mode)
```
Client → ALB:443 → Listener Rule → Per-Agent TG → EC2:{63000+} → Agent
```

### Future (Socket Mode)
```
Client → ALB:443 → Listener Rule → Shared TG → EC2:8080 (Nginx) → Unix Socket → Agent
```

---

## HARDCODED VALUES

### Infrastructure IDs (Already in px-vpc)
| Resource | ID |
|----------|-----|
| VPC | `vpc-0dc5816f0b041abad` |
| EC2 Instance | `i-0df57d61c09d02b00` |
| ALB | `px-pixell-runtime-alb` |
| HTTPS Listener | `arn:aws:elasticloadbalancing:...:listener/app/px-pixell-runtime-alb/bc04340265e7343e/dd8644d338a2a781` |

### Environment Variables
| Variable | Default |
|----------|---------|
| `EC2_SUPERVISOR_PORT` | 9000 |
| `AGENTS_PUBLIC_HOST` | par.pixell.global |
| `AGENT_PACKAGES_BUCKET` | pixell-agent-packages |
| `DEPLOYMENT_MODE` | ec2-multi-agent |
