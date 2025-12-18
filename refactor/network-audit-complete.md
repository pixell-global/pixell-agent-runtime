# Complete Network Audit - A2A Request Path Analysis

**Date:** December 2, 2025
**Purpose:** Trace every network layer from client to agent for A2A protocol
**Status:** MULTIPLE CRITICAL ISSUES FOUND

---

## EXECUTIVE SUMMARY - WHY A2A IS BROKEN

```
CLIENT → DNS → ALB → Target Group → EC2 → Agent
         ❌      ❌        ❌          ⚠️      ❌
```

| Layer | Status | Issue |
|-------|--------|-------|
| **DNS** | ❌ BROKEN | Points to OLD ALB in OLD VPC |
| **OLD ALB** | ❌ BROKEN | Target groups in wrong VPC, no targets registered |
| **NEW ALB** | ⚠️ PARTIAL | Has SSL cert, but NO `/agents/*` listener rules |
| **Security Group** | ⚠️ MISSING | Port 8080 not open (needed for Nginx) |
| **EC2** | ⚠️ PARTIAL | No Nginx, no socket directory, gRPC Gateway on 50051 works |
| **Agents** | ❌ BROKEN | Not listening on expected ports (60000+, 63000+) |

---

## LAYER 1: DNS (Route53)

### Current State
```
par.pixell.global      → pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com (OLD)
agents.pixell.global   → pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com (OLD)
```

### Required State
```
par.pixell.global      → px-pixell-runtime-alb-133550711.us-east-2.elb.amazonaws.com (NEW)
agents.pixell.global   → px-pixell-runtime-alb-133550711.us-east-2.elb.amazonaws.com (NEW)
```

### Details
| Record | Type | Current Target | Required Target |
|--------|------|----------------|-----------------|
| `par.pixell.global` | A (Alias) | OLD ALB (`pixell-runtime-alb-420577088`) | NEW ALB (`px-pixell-runtime-alb-133550711`) |
| `agents.pixell.global` | A (Alias) | OLD ALB | NEW ALB |

**Hosted Zone ID:** `Z0366260153B1X4I8MP66`

---

## LAYER 2: APPLICATION LOAD BALANCERS

### OLD ALB (pixell-runtime-alb) - BROKEN
| Property | Value |
|----------|-------|
| **Name** | `pixell-runtime-alb` |
| **DNS** | `pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com` |
| **VPC** | `vpc-0039e5988107ae565` (pixell-runtime-vpc - OLD) |
| **Status** | ❌ BROKEN - EC2 is in different VPC |

**Listeners:**
| Port | Protocol | Default Target Group |
|------|----------|---------------------|
| 80 | HTTP | Redirect to HTTPS |
| 443 | HTTPS | `par-multi-agent-tg` (EMPTY - no targets!) |

**Listener Rules (HTTPS:443):**
| Priority | Condition | Target Group | Status |
|----------|-----------|--------------|--------|
| 10 | Host: `app.pixell.global` | `pixell-web-tg` | ⚠️ |
| 10066 | Path: `/agents/4906eeb7-.../api/*` | `pac-agent-4906eeb7-rest` | ❌ No targets |
| 10067 | Path: `/agents/4906eeb7-.../a2a/*` | `pac-agent-4906eeb7-grpc` | ❌ No targets |
| 10068 | Path: `/agents/4906eeb7-.../*` | `pac-agent-4906eeb7-rest` | ❌ No targets |
| 26020-26022 | `/agents/c489095f-.../*` | `pac-agent-c489095f-*` | ❌ No targets |
| 39556-39558 | `/agents/ed8784f3-.../*` | `pac-agent-ed8784f3-*` | ❌ No targets |
| default | - | `par-multi-agent-tg` | ❌ No targets |

---

### NEW ALB (px-pixell-runtime-alb) - NEEDS CONFIGURATION
| Property | Value |
|----------|-------|
| **Name** | `px-pixell-runtime-alb` |
| **DNS** | `px-pixell-runtime-alb-133550711.us-east-2.elb.amazonaws.com` |
| **VPC** | `vpc-0dc5816f0b041abad` (px-vpc - NEW) ✓ |
| **Security Groups** | `sg-0869c371d1826a660`, `sg-0c0f983d66125ec24` |
| **Subnets** | `subnet-035d6ed0a581e57df`, `subnet-059d23db977f85843`, `subnet-0fd1d99dab3fdf17b` |

**SSL Certificates:**
| Certificate ARN | Domain | Status |
|-----------------|--------|--------|
| `arn:aws:acm:...certificate/edbdabdd-03b5-4e0f-9cb5-f7f9975442fd` | `app.pixell.global` | ✓ Attached |
| `arn:aws:acm:...certificate/27009de7-9e7f-40af-b0f9-2222638f78a5` | `par.pixell.global` | ✓ Attached |

**Listeners:**
| Port | Protocol | Default Target Group |
|------|----------|---------------------|
| 80 | HTTP | Redirect to HTTPS |
| 443 | HTTPS | `px-par-multi-agent-tg` |

**Listener Rules (HTTPS:443):**
| Priority | Condition | Target Group | Status |
|----------|-----------|--------------|--------|
| 10 | Host: `app.pixell.global` | `px-pixell-web-tg` | ✓ Works |
| default | - | `px-par-multi-agent-tg` | ⚠️ Only health works |

**❌ MISSING RULES:**
- `/agents/*/api/*` → REST target group
- `/agents/*/a2a/*` → gRPC target group
- `/agents/*` → catch-all

---

## LAYER 3: TARGET GROUPS

### Per-Agent Target Groups (ALL IN WRONG VPC!)

| Target Group | Port | Protocol | VPC | Targets | Status |
|--------------|------|----------|-----|---------|--------|
| `pac-agent-4906eeb7-grpc` | 60000 | HTTP2 | OLD ❌ | 0 | EMPTY |
| `pac-agent-4906eeb7-rest` | 63000 | HTTP1 | OLD ❌ | 0 | EMPTY |
| `pac-agent-c489095f-grpc` | 60002 | HTTP2 | OLD ❌ | 0 | EMPTY |
| `pac-agent-c489095f-rest` | 63002 | HTTP1 | OLD ❌ | 0 | EMPTY |
| `pac-agent-ed8784f3-grpc` | 60001 | HTTP2 | OLD ❌ | 0 | EMPTY |
| `pac-agent-ed8784f3-rest` | 63001 | HTTP1 | OLD ❌ | 0 | EMPTY |

**Root Cause:** Target groups are in OLD VPC (`vpc-0039e5988107ae565`), but EC2 instance is in NEW VPC (`vpc-0dc5816f0b041abad`). Cannot register cross-VPC targets.

### New VPC Target Groups

| Target Group | Port | Protocol | VPC | Health | Status |
|--------------|------|----------|-----|--------|--------|
| `px-par-multi-agent-tg` | 8080 | HTTP1 | NEW ✓ | ✓ Healthy (8081) | Works for health |
| `px-pixell-runtime-a2a-tg` | 50051 | TCP | NEW ✓ | ✓ Healthy | NLB only |

**Note:** `px-par-multi-agent-tg` has instance registered on port 8081, not 8080!

---

## LAYER 4: NETWORK LOAD BALANCER (A2A gRPC)

### px-pixell-runtime-nlb
| Property | Value |
|----------|-------|
| **Name** | `px-pixell-runtime-nlb` |
| **DNS** | `px-pixell-runtime-nlb-be04d3987c2dbaa1.elb.us-east-2.amazonaws.com` |
| **VPC** | `vpc-0dc5816f0b041abad` (NEW) ✓ |
| **Type** | Network |

**Listeners:**
| Port | Protocol | Target Group |
|------|----------|--------------|
| 50051 | TCP | `px-pixell-runtime-a2a-tg` |

**Target Health:**
| Target | Port | Status |
|--------|------|--------|
| `172.31.13.141` | 50051 | ✓ Healthy |

**❌ MISSING:** No DNS record points to this NLB!

---

## LAYER 5: SECURITY GROUPS

### ALB Security Groups

**sg-0c0f983d66125ec24 (px-pixell-runtime-alb)**
| Port | Protocol | Source | Status |
|------|----------|--------|--------|
| 80 | TCP | 0.0.0.0/0 | ✓ |
| 443 | TCP | 0.0.0.0/0 | ✓ |

### EC2 Security Group

**sg-02a98c7cec76b53fa (pixell-agent-runtime-sg)**
| Port Range | Protocol | Source | Purpose | Status |
|------------|----------|--------|---------|--------|
| 22 | TCP | 0.0.0.0/0 | SSH | ✓ |
| 3001-3020 | TCP | 0.0.0.0/0 | UI ports | ✓ |
| 6379 | TCP | 0.0.0.0/0 | Redis | ✓ |
| 8081-8100 | TCP | 0.0.0.0/0 | Health/API | ✓ |
| 9000 | TCP | 0.0.0.0/0 | PAR Supervisor | ✓ |
| 50051 | TCP | 0.0.0.0/0 | gRPC Gateway | ✓ |
| 50052-50071 | TCP | 0.0.0.0/0 | Agent gRPC | ✓ |
| 60000-60199 | TCP | 0.0.0.0/0 | A2A ports | ✓ |
| 63000-63199 | TCP | 0.0.0.0/0 | REST ports | ✓ |
| 65000-65199 | TCP | 0.0.0.0/0 | UI ports | ✓ |
| **8080** | TCP | - | **Nginx proxy** | ❌ **MISSING** |

---

## LAYER 6: VPC & SUBNETS

### px-vpc (vpc-0dc5816f0b041abad) - NEW VPC
| Property | Value |
|----------|-------|
| **CIDR** | 172.31.0.0/16 |
| **Internet Gateway** | `igw-011acbeead44b6a2e` |
| **NAT Gateway** | None (public subnets) |

**Subnets:**
| Subnet ID | CIDR | AZ | Public IP |
|-----------|------|----|-----------|
| `subnet-035d6ed0a581e57df` | 172.31.0.0/20 | us-east-2a | ✓ |
| `subnet-059d23db977f85843` | 172.31.16.0/20 | us-east-2b | ✓ |
| `subnet-0fd1d99dab3fdf17b` | 172.31.32.0/20 | us-east-2c | ✓ |

**Route Table:**
| Destination | Target |
|-------------|--------|
| 172.31.0.0/16 | local |
| 0.0.0.0/0 | `igw-011acbeead44b6a2e` |

---

## LAYER 7: EC2 INSTANCE

### i-0df57d61c09d02b00 (pixell-agent-runtime)
| Property | Value |
|----------|-------|
| **VPC** | `vpc-0dc5816f0b041abad` (NEW) ✓ |
| **Subnet** | `subnet-035d6ed0a581e57df` (us-east-2a) |
| **Private IP** | 172.31.13.141 |
| **Public IP** | 18.116.13.50 |
| **Security Group** | `sg-02a98c7cec76b53fa` |

### Currently Listening Ports
| Port | Process | Purpose | Status |
|------|---------|---------|--------|
| 22 | sshd | SSH | ✓ Works |
| 6379 | redis-server | Redis | ✓ Works |
| 9000 | python (supervisor) | PAR Supervisor API | ✓ Works |
| 50051 | python (supervisor) | gRPC Gateway | ✓ Works |
| 8081 | python (agent) | Agent health/REST | ✓ Works |

### NOT Listening (Expected for A2A)
| Port Range | Purpose | Status |
|------------|---------|--------|
| 60000-60199 | Agent A2A (gRPC) | ❌ No agent listening |
| 63000-63199 | Agent REST | ❌ No agent listening |
| 65000-65199 | Agent UI | ❌ No agent listening |
| 8080 | Nginx proxy | ❌ Nginx not installed |

---

## LAYER 8: NGINX PROXY

### Current State
| Item | Status |
|------|--------|
| Nginx installed | ❌ NO |
| Nginx service | ❌ NOT RUNNING |
| `/var/run/pixell-agents/` | ❌ NOT EXISTS |
| Socket files | ❌ NOT EXISTS |

---

## LAYER 9: AGENT PROCESS

### Running Agents
| Agent ID | User | PID | Ports | Status |
|----------|------|-----|-------|--------|
| `ed8784f3-b602-481c-8701-3b6406c8fd98` | `agent_ed8784f3_b602` | 63011 | REST:8081, A2A:50052, UI:3001 | ⚠️ Non-standard ports |

**Note:** Agent is using ports 8081/50052/3001, NOT the expected 63000+/60000+/65000+ ports!

---

## A2A REQUEST PATH - CURRENT BROKEN FLOW

```
1. Client: curl https://par.pixell.global/agents/{id}/a2a/health
                    │
                    ▼
2. DNS:    par.pixell.global → pixell-runtime-alb-420577088 (OLD ALB)
                    │                    ❌ WRONG ALB
                    ▼
3. OLD ALB: Listener rule for /agents/{id}/a2a/*
                    │        → pac-agent-{id}-grpc target group
                    ▼                    ❌ TARGET GROUP IN WRONG VPC
4. Target Group: pac-agent-{id}-grpc (VPC: vpc-0039e5988107ae565)
                    │                    ❌ NO TARGETS REGISTERED
                    ▼
5. Result: 502 Bad Gateway or Connection Refused
```

---

## A2A REQUEST PATH - REQUIRED FLOW (Socket Mode)

```
1. Client: curl https://par.pixell.global/agents/{id}/a2a/health
                    │
                    ▼
2. DNS:    par.pixell.global → px-pixell-runtime-alb (NEW ALB)
                    │                    ✓ Correct ALB in NEW VPC
                    ▼
3. NEW ALB: HTTPS:443 → Listener rule for /agents/*
                    │        → px-agents-proxy-tg (port 8080)
                    ▼                    ✓ Single shared target group
4. Target Group: px-agents-proxy-tg → 172.31.13.141:8080
                    │                    ✓ Healthy target
                    ▼
5. Nginx:  Port 8080 → Parse /agents/{id}/a2a/*
                    │        → Unix socket /var/run/pixell-agents/agent_{short_id}/a2a.sock
                    ▼                    ✓ Route to correct socket
6. Agent:  a2a.sock → gRPC server → Response
                    │                    ✓ Agent handles request
                    ▼
7. Response: 200 OK with gRPC response
```

---

## FIXES REQUIRED

### Priority 1: Immediate (Fix Routing)
1. ⬜ Update DNS `par.pixell.global` → NEW ALB
2. ⬜ Add `/agents/*` listener rule to NEW ALB
3. ⬜ Add port 8080 to EC2 security group

### Priority 2: Infrastructure (Socket Mode)
4. ⬜ Install Nginx on EC2
5. ⬜ Create `/var/run/pixell-agents/` directory
6. ⬜ Configure Nginx for socket proxy

### Priority 3: Code Changes
7. ⬜ PAR: Add socket_allocator.py
8. ⬜ PAR: Update process_manager.py for socket env vars
9. ⬜ PAC: Update deployment to use socket_mode=true

### Priority 4: Cleanup
10. ⬜ Delete old per-agent target groups in OLD VPC
11. ⬜ Delete OLD ALB (after verification)

---

## QUICK REFERENCE - RESOURCE IDS

### NEW VPC Resources (USE THESE)
| Resource | ID |
|----------|-----|
| VPC | `vpc-0dc5816f0b041abad` |
| EC2 Instance | `i-0df57d61c09d02b00` |
| EC2 Private IP | `172.31.13.141` |
| EC2 Security Group | `sg-02a98c7cec76b53fa` |
| ALB | `px-pixell-runtime-alb` |
| ALB Listener (HTTPS) | `arn:aws:elasticloadbalancing:us-east-2:636212886452:listener/app/px-pixell-runtime-alb/bc04340265e7343e/dd8644d338a2a781` |
| Target Group | `px-par-multi-agent-tg` |
| NLB | `px-pixell-runtime-nlb` |
| NLB A2A TG | `px-pixell-runtime-a2a-tg` |

### OLD VPC Resources (DO NOT USE)
| Resource | ID | Status |
|----------|-----|--------|
| VPC | `vpc-0039e5988107ae565` | Deprecated |
| ALB | `pixell-runtime-alb` | Deprecated |
| Target Groups | `pac-agent-*-grpc`, `pac-agent-*-rest` | Deprecated |

### SSL Certificates
| Domain | ARN |
|--------|-----|
| `par.pixell.global` | `arn:aws:acm:us-east-2:636212886452:certificate/27009de7-9e7f-40af-b0f9-2222638f78a5` |
| `app.pixell.global` | `arn:aws:acm:us-east-2:636212886452:certificate/edbdabdd-03b5-4e0f-9cb5-f7f9975442fd` |
