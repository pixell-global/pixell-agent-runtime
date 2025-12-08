# SOCKET ARCHITECTURE: DETAILED REQUEST FLOW ANALYSIS

**Purpose:** Understand if Nginx reverse proxy is necessary for socket-based deployment

---

## CURRENT PORT-BASED ARCHITECTURE (A2A Request Flow)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 1: CLIENT INITIATES REQUEST                                       │
└─────────────────────────────────────────────────────────────────────────┘

Client Application (e.g., talk_to_agent.py)
  Location: Developer's machine / Another agent
  Request: gRPC Invoke(ActionRequest)
  Target: par.pixell.global:443
  Path: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/pixell.agent.AgentService/Invoke
  Protocol: gRPC (HTTP/2)
  Payload: Protobuf-encoded ActionRequest with A2A message

                              │
                              │ DNS Lookup: par.pixell.global
                              ↓

┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 2: DNS RESOLUTION                                                  │
└─────────────────────────────────────────────────────────────────────────┘

Route 53 / DNS
  par.pixell.global → 18.216.3.57, 18.219.207.35 (ALB IP addresses)

                              │
                              │ TCP SYN to 18.216.3.57:443
                              ↓

┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 3: AWS APPLICATION LOAD BALANCER (ALB)                            │
│ Name: pixell-runtime-alb                                                │
│ DNS: pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com          │
│ Public IPs: 18.216.3.57, 18.219.207.35                                 │
│ VPC: vpc-0039e5988107ae565 (Runtime VPC - 10.0.0.0/16)                │
│ Security Group: sg-0f5b28ee64419e95d                                   │
└─────────────────────────────────────────────────────────────────────────┘

  HTTPS Listener (Port 443)
    │
    ├─ TLS Termination (*.pixell.global certificate)
    │  └─ Decrypt HTTPS → HTTP/2
    │
    ├─ Path-Based Routing Rules:
    │
    │  Rule Priority 100: /agents/4906eeb7-*/a2a/*
    │  ├─ Condition: path-pattern = "/agents/4906eeb7-*/a2a/*"
    │  ├─ Action: forward to target-group
    │  └─ Target Group: pac-agent-4906eeb7-grpc
    │       ├─ Name: pac-agent-4906eeb7-grpc
    │       ├─ Protocol: HTTP
    │       ├─ ProtocolVersion: HTTP2  ⚠️ CRITICAL for gRPC!
    │       ├─ Port: 60000
    │       ├─ VPC: vpc-0039e5988107ae565
    │       ├─ Health Check:
    │       │   └─ Path: /agents/4906eeb7-*/health
    │       │   └─ Protocol: HTTP (not gRPC - ALB limitation)
    │       │   └─ Port: 60000
    │       │   └─ Interval: 30s
    │       └─ Targets:
    │           └─ 10.0.1.37:60000 (EC2 instance, Status: healthy)
    │
    │  [... 199 more similar rules for other agents ...]
    │
    └─ Default Rule: Return 404 Not Found

                              │
                              │ Forward HTTP/2 request to 10.0.1.37:60000
                              ↓

┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 4: EC2 INSTANCE (PAR Host)                                        │
│ Instance ID: i-09dcb7f387166efd0                                       │
│ Type: m7g.medium (1 vCPU, 4GB RAM, ARM64)                              │
│ Private IP: 10.0.1.37                                                  │
│ Public IP: 18.119.137.118                                              │
│ VPC: vpc-0039e5988107ae565 (same as ALB)                              │
│ Subnet: subnet-0a79126c8f2c8f05c (us-east-2a)                         │
│ Security Group: sg-0c13cfb5da4e67ea7                                   │
│   Inbound Rules:                                                        │
│   - Port 60000-60199 from ALB (A2A gRPC)                               │
│   - Port 63000-63199 from ALB (REST API)                               │
│   - Port 65000-65199 from ALB (UI)                                     │
│   - Port 9000 from VPC (Supervisor API)                                │
│   - Port 50051 from VPC (gRPC Gateway) ⚠️ NOT USED in current setup   │
└─────────────────────────────────────────────────────────────────────────┘

  Network Interface (eth0: 10.0.1.37)
    │
    │ TCP connection on port 60000
    ↓

  ┌─────────────────────────────────────────────────────────────────────┐
  │ Agent Process (agent_4906eeb7)                                      │
  │ User: agent_4906eeb7                                                │
  │ PID: 2145                                                           │
  │ Working Dir: /home/agent_4906eeb7/                                  │
  │ Process: /usr/bin/python3.11 -m pixell_runtime                     │
  │                                                                      │
  │ ┌─────────────────────────────────────────────────────────────────┐ │
  │ │ gRPC Server (grpc.aio.server)                                   │ │
  │ │ Listening: 0.0.0.0:60000                                        │ │
  │ │ Protocol: HTTP/2 (gRPC)                                         │ │
  │ │                                                                  │ │
  │ │ Service: pixell.agent.AgentService                              │ │
  │ │   └─ Method: Invoke(ActionRequest) -> ActionResult             │ │
  │ │                                                                  │ │
  │ │ Handler receives request:                                       │ │
  │ │   1. Parse ActionRequest protobuf                               │ │
  │ │   2. Extract A2A message from request.message                   │ │
  │ │   3. Parse JSON-RPC 2.0 params from message.params_json         │ │
  │ │   4. Route to skill handler based on metadata.skill             │ │
  │ │   5. Execute agent logic                                        │ │
  │ │   6. Build ActionResult response                                │ │
  │ │   7. Serialize to protobuf                                      │ │
  │ │   8. Return via gRPC                                            │ │
  │ └─────────────────────────────────────────────────────────────────┘ │
  └─────────────────────────────────────────────────────────────────────┘

                              │
                              │ gRPC response (ActionResult)
                              ↓

Response path: Agent:60000 → EC2:60000 → ALB → Client
Total latency: ~50-200ms (depends on agent logic)
```

---

## PROPOSED SOCKET-BASED ARCHITECTURE - OPTION 1 (WITH NGINX)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 1: CLIENT INITIATES REQUEST (Same as before)                      │
└─────────────────────────────────────────────────────────────────────────┘

Client Application (e.g., talk_to_agent.py)
  Request: gRPC Invoke(ActionRequest)
  Target: par.pixell.global:443
  Path: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/pixell.agent.AgentService/Invoke

                              │
                              │ DNS Lookup: par.pixell.global
                              ↓

┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 2: DNS RESOLUTION (Same as before)                                │
└─────────────────────────────────────────────────────────────────────────┘

Route 53: par.pixell.global → 18.216.3.57 (ALB)

                              │
                              ↓

┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 3: AWS APPLICATION LOAD BALANCER (SIMPLIFIED!)                    │
│ Name: pixell-runtime-alb                                                │
└─────────────────────────────────────────────────────────────────────────┘

  HTTPS Listener (Port 443)
    │
    ├─ TLS Termination
    │
    ├─ Path-Based Routing Rules (ONLY 3 RULES instead of 600!):
    │
    │  Rule 1: /agents/*/a2a/*  ⚠️ Wildcard routing!
    │  ├─ Condition: path-pattern = "/agents/*/a2a/*"
    │  ├─ Action: forward to target-group
    │  └─ Target Group: pixell-grpc-proxy
    │       ├─ Name: pixell-grpc-proxy (SHARED by all agents)
    │       ├─ Protocol: HTTP
    │       ├─ ProtocolVersion: HTTP2  ⚠️ CRITICAL for gRPC!
    │       ├─ Port: 50051
    │       ├─ VPC: vpc-0039e5988107ae565
    │       ├─ Health Check:
    │       │   └─ Path: /health
    │       │   └─ Port: 50051
    │       └─ Targets:
    │           └─ 10.0.1.37:50051 (Nginx gRPC proxy)
    │
    │  Rule 2: /agents/*/rest/*
    │  └─ Target Group: pixell-rest-proxy (Port 8080)
    │
    │  Rule 3: /agents/*/ui/*
    │  └─ Target Group: pixell-ui-proxy (Port 3000)
    │
    └─ Default Rule: Return 404

                              │
                              │ Forward to 10.0.1.37:50051
                              ↓

┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 4: EC2 INSTANCE                                                    │
│ Instance ID: i-09dcb7f387166efd0                                       │
│ Security Group: sg-0c13cfb5da4e67ea7 (UPDATED)                         │
│   Inbound Rules:                                                        │
│   - Port 8080 from ALB (REST proxy) ← NEW                              │
│   - Port 50051 from ALB (gRPC proxy) ← NEW                             │
│   - Port 3000 from ALB (UI proxy) ← NEW                                │
│   - Port 9000 from VPC (Supervisor API)                                │
│   - Ports 60000-60199, 63000-63199, 65000-65199 ← REMOVE after migration│
└─────────────────────────────────────────────────────────────────────────┘

  Network Interface (eth0: 10.0.1.37)
    │
    │ TCP connection on port 50051
    ↓

  ┌─────────────────────────────────────────────────────────────────────┐
  │ NGINX REVERSE PROXY (NEW COMPONENT)                                │
  │ Process: nginx (running as root)                                   │
  │ Config: /etc/nginx/conf.d/pixell-agents.conf                       │
  │ PID: ~1000                                                          │
  │                                                                      │
  │ ┌─────────────────────────────────────────────────────────────────┐ │
  │ │ gRPC Proxy Server                                               │ │
  │ │ Listening: 0.0.0.0:50051 (HTTP/2)                               │ │
  │ │                                                                  │ │
  │ │ Incoming request:                                                │ │
  │ │   Path: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/...   │ │
  │ │                                                                  │ │
  │ │ Routing Logic:                                                   │ │
  │ │   1. Parse path with regex:                                     │ │
  │ │      /agents/(?<agent_id>[a-f0-9-]+)/a2a/(?<grpc_path>.*)      │ │
  │ │                                                                  │ │
  │ │   2. Extract agent_id: "4906eeb7-9959-414e-84c6-f2445822ebe4"  │ │
  │ │                                                                  │ │
  │ │   3. Extract short_id (first 8 chars): "4906eeb7"               │ │
  │ │                                                                  │ │
  │ │   4. Build socket path:                                         │ │
  │ │      /var/run/pixell-agents/agent_4906eeb7/a2a.sock            │ │
  │ │                                                                  │ │
  │ │   5. Check if socket exists:                                    │ │
  │ │      if (!-S $socket_path) { return 404; }                     │ │
  │ │                                                                  │ │
  │ │   6. Forward gRPC request to Unix domain socket:                │ │
  │ │      grpc_pass grpc://unix:/var/run/.../agent_4906eeb7/a2a.sock│ │
  │ └─────────────────────────────────────────────────────────────────┘ │
  └─────────────────────────────────────────────────────────────────────┘

                              │
                              │ Unix Domain Socket (IPC)
                              ↓

  ┌─────────────────────────────────────────────────────────────────────┐
  │ SOCKET DIRECTORY                                                    │
  │ Path: /var/run/pixell-agents/                                      │
  │ Permissions: 755 root:root                                          │
  │                                                                      │
  │ ┌─────────────────────────────────────────────────────────────────┐ │
  │ │ agent_4906eeb7/                                                 │ │
  │ │ Permissions: 750 agent_4906eeb7:nginx                           │ │
  │ │                                                                  │ │
  │ │ ├─ rest.sock                                                    │ │
  │ │ │  Permissions: 660 agent_4906eeb7:nginx                        │ │
  │ │ │  Type: Unix domain socket (SOCK_STREAM)                       │ │
  │ │ │                                                                │ │
  │ │ ├─ a2a.sock  ← REQUEST GOES HERE                                │ │
  │ │ │  Permissions: 660 agent_4906eeb7:nginx                        │ │
  │ │ │  Type: Unix domain socket (SOCK_STREAM)                       │ │
  │ │ │  Protocol: gRPC (HTTP/2)                                      │ │
  │ │ │                                                                │ │
  │ │ └─ ui.sock                                                      │ │
  │ │    Permissions: 660 agent_4906eeb7:nginx                        │ │
  │ └─────────────────────────────────────────────────────────────────┘ │
  └─────────────────────────────────────────────────────────────────────┘

                              │
                              │ gRPC over Unix socket
                              ↓

  ┌─────────────────────────────────────────────────────────────────────┐
  │ Agent Process (agent_4906eeb7)                                      │
  │ User: agent_4906eeb7                                                │
  │ PID: 2145                                                           │
  │ Environment:                                                         │
  │   SOCKET_MODE=true                                                  │
  │   A2A_SOCKET=/var/run/pixell-agents/agent_4906eeb7/a2a.sock       │
  │                                                                      │
  │ ┌─────────────────────────────────────────────────────────────────┐ │
  │ │ gRPC Server (grpc.aio.server)                                   │ │
  │ │ Listening: unix:/var/run/.../agent_4906eeb7/a2a.sock           │ │
  │ │ Protocol: HTTP/2 (gRPC over Unix socket)                        │ │
  │ │                                                                  │ │
  │ │ Server initialization:                                          │ │
  │ │   server = grpc.aio.server()                                    │ │
  │ │   server.add_AgentServiceServicer(...)                          │ │
  │ │   server.add_insecure_port("unix:/var/run/.../a2a.sock")       │ │
  │ │   await server.start()                                          │ │
  │ │                                                                  │ │
  │ │ Handler receives request (same as port mode):                   │ │
  │ │   1. Parse ActionRequest protobuf                               │ │
  │ │   2. Extract A2A message                                        │ │
  │ │   3. Parse JSON-RPC 2.0 params                                  │ │
  │ │   4. Route to skill handler                                     │ │
  │ │   5. Execute agent logic                                        │ │
  │ │   6. Build ActionResult response                                │ │
  │ │   7. Return via gRPC                                            │ │
  │ └─────────────────────────────────────────────────────────────────┘ │
  └─────────────────────────────────────────────────────────────────────┘

                              │
                              │ gRPC response (ActionResult)
                              ↓

Response path: Agent socket → Nginx:50051 → ALB → Client
Total latency: ~50-200ms + 0.5ms (Nginx overhead)
```

---

## PROPOSED SOCKET-BASED ARCHITECTURE - OPTION 2 (EXTEND PAR gRPC GATEWAY)

**Question: Can we use the existing PAR gRPC Gateway instead of Nginx?**

```
┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 1-3: Same as Option 1 (Client → DNS → ALB)                        │
└─────────────────────────────────────────────────────────────────────────┘

ALB routes /agents/*/a2a/* → Target: 10.0.1.37:50051

                              │
                              ↓

┌─────────────────────────────────────────────────────────────────────────┐
│ STEP 4: EC2 INSTANCE                                                    │
└─────────────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────────┐
  │ PAR gRPC GATEWAY (EXISTING COMPONENT - ENHANCED)                   │
  │ File: src/pixell_runtime/supervisor/grpc_gateway.py                │
  │ Process: Part of PAR supervisor                                    │
  │ Listening: 0.0.0.0:50051 (HTTP/2)                                  │
  │ Status: Currently exists but NOT used (ALB routes directly to      │
  │         agent ports, bypassing gateway)                             │
  │                                                                      │
  │ PROPOSED ENHANCEMENT:                                               │
  │ ┌─────────────────────────────────────────────────────────────────┐ │
  │ │ Incoming request:                                               │ │
  │ │   Path: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/...   │ │
  │ │                                                                  │ │
  │ │ Routing Logic (MODIFY _handle_request):                         │ │
  │ │   1. Extract agent_id from path                                 │ │
  │ │   2. Lookup agent in SupervisorState.agents                     │ │
  │ │   3. Get socket_paths from agent info                           │ │
  │ │   4. IF socket_mode:                                            │ │
  │ │        target = f"unix:{agent.socket_paths.a2a}"                │ │
  │ │      ELSE:                                                       │ │
  │ │        target = f"localhost:{agent.ports.a2a}"  # Legacy        │ │
  │ │   5. Create gRPC channel to target                              │ │
  │ │   6. Forward request                                            │ │
  │ │   7. Return response                                            │ │
  │ └─────────────────────────────────────────────────────────────────┘ │
  └─────────────────────────────────────────────────────────────────────┘

                              │
                              │ gRPC over Unix socket
                              ↓

  [Same socket directory and agent process as Option 1]

⚠️ LIMITATION OF THIS APPROACH:
  - PAR gRPC Gateway only handles gRPC (A2A) traffic
  - What about REST API traffic (/agents/*/rest/*)?
  - What about UI traffic (/agents/*/ui/*)?

  OPTIONS:
    A. Add REST/UI handlers to PAR supervisor (FastAPI + static files)
       → Makes supervisor more complex
       → Supervisor becomes a multi-purpose proxy

    B. Run 3 separate proxies (gRPC gateway + REST proxy + UI proxy)
       → More processes to manage
       → Still need something to handle REST/UI (could be Python FastAPI)

    C. Use Nginx for ALL traffic (Option 1)
       → Single, well-tested reverse proxy
       → Handles gRPC, REST, and UI
       → Standard solution for this pattern
```

---

## COMPARISON: WITH NGINX vs WITHOUT NGINX

### Option 1: Nginx Reverse Proxy (Recommended)

**Pros:**
- ✅ Single component handles REST, gRPC, and UI routing
- ✅ Nginx is battle-tested for high-throughput proxy scenarios
- ✅ Built-in support for Unix domain sockets
- ✅ Excellent gRPC support (with http2 module)
- ✅ Low latency overhead (~0.5ms)
- ✅ Easy to configure path-based routing with regex
- ✅ Can add auth, rate limiting, caching at proxy layer
- ✅ Separates routing concerns from supervisor logic

**Cons:**
- ❌ New dependency (must install/configure Nginx)
- ❌ Another process to monitor
- ❌ Additional 100MB RAM overhead
- ❌ Becomes single point of failure (but so does EC2 instance)

### Option 2: Extend PAR gRPC Gateway

**Pros:**
- ✅ Uses existing component (grpc_gateway.py)
- ✅ No new dependencies
- ✅ Python-based (same language as PAR)

**Cons:**
- ❌ Only handles gRPC (A2A) traffic
- ❌ Need separate solution for REST and UI
- ❌ Increases supervisor complexity
- ❌ Python async performance < Nginx for high concurrency
- ❌ Would need to add FastAPI endpoints for REST routing
- ❌ Would need static file serving for UI routing
- ❌ Ends up reinventing Nginx functionality

### Option 3: No Proxy - ALB Direct to Sockets?

**Is this possible?**
- ❌ NO! ALB cannot route to Unix domain sockets
- ❌ ALB only supports TCP/IP targets (IP:port)
- ❌ Unix sockets are local IPC mechanism
- ❌ Requires a proxy on EC2 to bridge ALB (TCP) → Sockets (IPC)

---

## DETAILED COMPONENT INTERACTION DIAGRAM

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         COMPONENT INTERACTION                           │
│                      (Socket-Based with Nginx)                          │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│ AWS REGION: us-east-2                                                   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │ Application Load Balancer (pixell-runtime-alb)                 │   │
│  │ Listener: HTTPS:443                                            │   │
│  │                                                                  │   │
│  │ Target Group 1: pixell-grpc-proxy                              │   │
│  │   ├─ Protocol: HTTP, ProtocolVersion: HTTP2                    │   │
│  │   ├─ Port: 50051                                                │   │
│  │   └─ Target: i-09dcb7f387166efd0:50051                         │   │
│  │                                                                  │   │
│  │ Target Group 2: pixell-rest-proxy                              │   │
│  │   ├─ Protocol: HTTP, ProtocolVersion: HTTP1                    │   │
│  │   ├─ Port: 8080                                                 │   │
│  │   └─ Target: i-09dcb7f387166efd0:8080                          │   │
│  │                                                                  │   │
│  │ Target Group 3: pixell-ui-proxy                                │   │
│  │   ├─ Protocol: HTTP, ProtocolVersion: HTTP1                    │   │
│  │   ├─ Port: 3000                                                 │   │
│  │   └─ Target: i-09dcb7f387166efd0:3000                          │   │
│  └────────────────────────────────────────────────────────────────┘   │
│                            │                                            │
│                            │ TCP Connections                            │
│                            ↓                                            │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │ EC2 Instance: i-09dcb7f387166efd0                              │   │
│  │ Private IP: 10.0.1.37                                           │   │
│  ├────────────────────────────────────────────────────────────────┤   │
│  │                                                                  │   │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐│   │
│  │  │ Nginx :50051     │  │ Nginx :8080      │  │ Nginx :3000   ││   │
│  │  │ (gRPC Proxy)     │  │ (REST Proxy)     │  │ (UI Proxy)    ││   │
│  │  └────────┬─────────┘  └────────┬─────────┘  └───────┬───────┘│   │
│  │           │                     │                    │         │   │
│  │           │ Unix Socket IPC     │                    │         │   │
│  │           ↓                     ↓                    ↓         │   │
│  │  ┌──────────────────────────────────────────────────────────┐ │   │
│  │  │ /var/run/pixell-agents/                                  │ │   │
│  │  │                                                            │ │   │
│  │  │ agent_4906eeb7/                                           │ │   │
│  │  │ ├─ rest.sock  ←────────────────────┘                    │ │   │
│  │  │ ├─ a2a.sock   ←────────┘                                 │ │   │
│  │  │ └─ ui.sock    ←──────────────────────────────────┘       │ │   │
│  │  │                                                            │ │   │
│  │  │ agent_ed8784f3/                                           │ │   │
│  │  │ ├─ rest.sock                                              │ │   │
│  │  │ ├─ a2a.sock                                               │ │   │
│  │  │ └─ ui.sock                                                │ │   │
│  │  └──────────────────────────────────────────────────────────┘ │   │
│  │           │                     │                    │         │   │
│  │           ↓                     ↓                    ↓         │   │
│  │  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐│   │
│  │  │ Agent 1 Process  │  │ Agent 1 Process  │  │ Agent 1 Proc  ││   │
│  │  │ gRPC server      │  │ FastAPI server   │  │ Static server ││   │
│  │  │ on a2a.sock      │  │ on rest.sock     │  │ on ui.sock    ││   │
│  │  └──────────────────┘  └──────────────────┘  └───────────────┘│   │
│  │                                                                  │   │
│  │  ┌──────────────────────────────────────────────────────────┐ │   │
│  │  │ PAR Supervisor                                            │ │   │
│  │  │ Port: 9000 (HTTP API for deployment management)          │ │   │
│  │  │ ├─ SupervisorState (agent registry)                      │ │   │
│  │  │ ├─ SocketAllocator (allocate socket paths)               │ │   │
│  │  │ ├─ ProcessManager (spawn agents with socket config)      │ │   │
│  │  │ └─ UserManager (create Linux users)                      │ │   │
│  │  └──────────────────────────────────────────────────────────┘ │   │
│  └────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## LATENCY BREAKDOWN COMPARISON

### Port-Based (Current):

```
Client Request
  ↓ [1ms] DNS resolution (cached)
ALB
  ↓ [5-10ms] TLS termination + routing
EC2 Network Interface
  ↓ [0.1ms] Local TCP connection
Agent Process Port 60000
  ↓ [50-200ms] Agent processing
Response
  ↓ [5-10ms] ALB + TLS
Client

Total: ~61-221ms (baseline)
```

### Socket-Based with Nginx:

```
Client Request
  ↓ [1ms] DNS resolution (cached)
ALB
  ↓ [5-10ms] TLS termination + routing
EC2 Network Interface
  ↓ [0.1ms] Local TCP connection
Nginx Proxy Port 50051
  ↓ [0.2ms] Nginx routing logic (regex match)
Unix Domain Socket
  ↓ [0.1ms] IPC overhead (faster than TCP!)
Agent Process Socket
  ↓ [50-200ms] Agent processing
Response (reverse path)
  ↓ [0.1ms] Socket → Nginx
  ↓ [0.2ms] Nginx → EC2
  ↓ [5-10ms] ALB + TLS
Client

Total: ~61.7-221.7ms (+0.7ms overhead)
```

**Overhead: +0.7ms (0.3% increase)**

---

## PERFORMANCE CHARACTERISTICS

### Unix Domain Sockets vs TCP Localhost

**Benchmark (Linux, same host):**
- TCP localhost (127.0.0.1): ~0.2-0.3ms latency, ~10 GB/s throughput
- Unix domain socket: ~0.1ms latency, ~15 GB/s throughput
- **Socket is 2-3× faster than TCP for local IPC!**

**Why sockets are faster:**
- No TCP/IP stack processing
- No port allocation/deallocation
- No SYN/ACK handshake
- Direct memory-to-memory copy (via kernel)
- Fewer context switches

### Nginx Performance

**Nginx benchmarks (gRPC proxy):**
- Requests/sec: 100,000+ (single core)
- Latency overhead: 0.2-0.5ms (p50), 0.5-1ms (p99)
- Memory per connection: ~10KB
- CPU per request: <0.01ms

**Nginx is NOT a bottleneck for this use case.**

---

## CONCLUSION: IS NGINX NECESSARY?

### YES, Nginx is necessary because:

1. **ALB cannot route directly to Unix sockets** - Need TCP-to-socket bridge
2. **Handles all 3 traffic types** (REST, gRPC, UI) in one component
3. **Proven performance** at scale (used by 30%+ of websites)
4. **Minimal overhead** (+0.5ms vs 50-200ms agent processing)
5. **Simpler than extending PAR** - Don't reinvent reverse proxy
6. **Better separation of concerns** - Routing logic separate from supervisor

### Alternative (without Nginx):

You would need to:
- Extend PAR gRPC gateway for A2A routing → Unix sockets ✅
- Add REST proxy to PAR supervisor (new FastAPI endpoints) ❌
- Add UI static file server to PAR supervisor ❌
- Manage 3 different routing mechanisms ❌
- Mix routing logic with deployment logic ❌

**This is essentially rebuilding Nginx in Python - not recommended!**

---

## RECOMMENDATION

**Use Nginx as the reverse proxy for socket-based deployment.**

**Deployment:**
```bash
# On EC2 instance i-09dcb7f387166efd0

# 1. Install Nginx
sudo yum install -y nginx

# 2. Deploy config
sudo cp pixell-agents.conf /etc/nginx/conf.d/

# 3. Start Nginx
sudo systemctl enable nginx
sudo systemctl start nginx

# 4. Update security group sg-0c13cfb5da4e67ea7
# Add inbound rules: 8080, 50051, 3000 from ALB security group

# 5. Update ALB target groups (via PAC deployment)
# Create: pixell-rest-proxy, pixell-grpc-proxy, pixell-ui-proxy
# Register target: i-09dcb7f387166efd0

# 6. Deploy first agent in socket mode
# PAR supervisor creates sockets, agent binds to them
```

**Total infrastructure changes:**
- ALB: 600 → 3 target groups (99% reduction!)
- EC2: +1 process (Nginx), +100MB RAM
- Security Group: 600 → 6 port rules (3 proxy + 3 management)
- Complexity: Simplified (single routing layer)

---

**Document Complete**
**Next Step:** Update socket-refactor-impact-analysis.md with Nginx as confirmed requirement
