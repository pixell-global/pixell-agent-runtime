# PIXELL AGENT RUNTIME - A2A REQUEST FLOW ANALYSIS

**Document Version:** 1.0
**Date:** November 1, 2025
**Author:** Claude Code (Runtime Analysis)
**Scope:** Complete A2A (Agent-to-Agent) request flow from client to agent response

---

## EXECUTIVE SUMMARY

This document analyzes how agent applications respond to A2A (Agent-to-Agent) requests in the Pixell Agent Runtime (PAR) system deployed on EC2. The system uses a **multi-agent supervisor architecture** where:

- **Supervisor Process:** Runs on EC2 (port 9000) managing multiple agent processes
- **Agent Processes:** Each runs as isolated Linux user with dedicated ports (60000-60199 for gRPC)
- **gRPC Gateway:** Routes requests from ALB to correct agent based on path-based routing
- **Protocol:** gRPC with JSON-RPC 2.0 message format for agent communication

**Key Components:**
- **EC2 Instance:** i-09dcb7f387166efd0 (m7g.medium, 10.0.1.37)
- **Supervisor:** Python FastAPI server on port 9000 (HTTP) + gRPC Gateway on port 50051
- **Agents:** Python processes on ports 60000-60199 (gRPC), 63000-63199 (REST)
- **ALB:** pixell-runtime-alb routing traffic via HTTPS on port 443

---

## SYSTEM ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL CLIENT                                     │
│                  (talk_to_agent.py, other agents, APIs)                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     │ A2A gRPC Request
                                     │ Target: par.pixell.global:443
                                     │ Path: /agents/{agent_id}/a2a/pixell.agent.AgentService/Invoke
                                     │ Format: JSON-RPC 2.0 wrapped in gRPC
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                    AWS APPLICATION LOAD BALANCER (ALB)                      │
│                    pixell-runtime-alb.us-east-2.elb.amazonaws.com           │
│                    DNS: par.pixell.global                                   │
│                    Port: 443 (HTTPS/TLS)                                    │
│                    VPC: vpc-0039e5988107ae565 (10.0.0.0/16)                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  HTTPS Listener :443                                                        │
│  ├─ TLS Certificate: *.pixell.global                                       │
│  │                                                                          │
│  ├─ Path-Based Routing Rules (by agent_id):                                │
│  │                                                                          │
│  │  ┌──────────────────────────────────────────────────────────┐          │
│  │  │ Rule: /agents/4906eeb7-*/a2a/*                           │          │
│  │  │ Target Group: pac-agent-4906eeb7-grpc                    │          │
│  │  │ Port: 60000                                               │          │
│  │  │ Protocol: HTTP2 (CRITICAL for gRPC!)                     │          │
│  │  │ Health Check: GET /agents/4906eeb7-*/health              │          │
│  │  │ Targets: 10.0.1.37:60000                                 │          │
│  │  └──────────────────────────────────────────────────────────┘          │
│  │                                                                          │
│  │  ┌──────────────────────────────────────────────────────────┐          │
│  │  │ Rule: /agents/ed8784f3-*/a2a/*                           │          │
│  │  │ Target Group: pac-agent-ed8784f3-grpc                    │          │
│  │  │ Port: 60001                                               │          │
│  │  │ Protocol: HTTP2                                           │          │
│  │  │ Targets: 10.0.1.37:60001                                 │          │
│  │  └──────────────────────────────────────────────────────────┘          │
│  │                                                                          │
│  │  [Additional rules for other agents...]                                 │
│  │                                                                          │
│  └─ Default Action: Return 404 Not Found                                   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     │ Forwards HTTP/2 gRPC request
                                     │ Target: 10.0.1.37:60000 (agent's gRPC port)
                                     │ Path: /agents/{agent_id}/a2a/pixell.agent.AgentService/Invoke
                                     ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                         EC2 INSTANCE (PAR Host)                             │
│                    i-09dcb7f387166efd0 (pixell-agent-runtime)               │
│                    Type: m7g.medium (1 vCPU, 4GB RAM, ARM64)                │
│                    Private IP: 10.0.1.37                                    │
│                    Public IP: 18.119.137.118                                │
│                    OS: Amazon Linux 2023                                    │
│                    Security Group: sg-0c13cfb5da4e67ea7                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────┐    │
│  │  SUPERVISOR PROCESS (runs as root)                                │    │
│  │  Binary: /usr/bin/python3.11 -m pixell_runtime.supervisor        │    │
│  │  PID: ~1500-2000 (systemd managed)                                │    │
│  │                                                                    │    │
│  │  Components:                                                       │    │
│  │  ├─ HTTP Server (Port 9000)                                       │    │
│  │  │  └─ FastAPI app for deployment management                     │    │
│  │  │     Endpoints:                                                 │    │
│  │  │     - POST /agents (deploy)                                    │    │
│  │  │     - PUT /agents/{id} (update)                                │    │
│  │  │     - DELETE /agents/{id} (delete)                             │    │
│  │  │     - GET /health (supervisor health)                          │    │
│  │  │                                                                 │    │
│  │  ├─ gRPC Gateway (Port 50051) ⚠️ NOT USED IN CURRENT SETUP       │    │
│  │  │  └─ Path-based routing to agent gRPC servers                   │    │
│  │  │     (This was planned but ALB direct routing is used instead)  │    │
│  │  │                                                                 │    │
│  │  ├─ SupervisorState                                               │    │
│  │  │  └─ In-memory registry of deployed agents                      │    │
│  │  │                                                                 │    │
│  │  ├─ ProcessManager                                                │    │
│  │  │  └─ Spawns/stops agent processes via subprocess.Popen          │    │
│  │  │                                                                 │    │
│  │  ├─ PortAllocator                                                 │    │
│  │  │  └─ Manages port assignments (60000-60199 for A2A)             │    │
│  │  │                                                                 │    │
│  │  ├─ UserManager                                                   │    │
│  │  │  └─ Creates Linux users (agent_xxx)                            │    │
│  │  │                                                                 │    │
│  │  └─ PackageDownloader                                             │    │
│  │     └─ Downloads .apkg from S3                                    │    │
│  │                                                                    │    │
│  └───────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────┐    │
│  │  AGENT PROCESSES (isolated Linux users)                           │    │
│  │                                                                    │    │
│  │  ┌─────────────────────────────────────────────────────────┐     │    │
│  │  │ Agent: Vivid Commenter                                  │     │    │
│  │  │ ID: 4906eeb7-9959-414e-84c6-f2445822ebe4               │     │    │
│  │  │ Linux User: agent_4906eeb7                              │     │    │
│  │  │ Home: /home/agent_4906eeb7/                             │     │    │
│  │  │                                                          │     │    │
│  │  │ Ports:                                                   │     │    │
│  │  │ ├─ REST: 63000 (HTTP FastAPI)                           │     │    │
│  │  │ ├─ A2A:  60000 (gRPC) ← RECEIVES REQUEST HERE           │     │    │
│  │  │ └─ UI:   66000 (HTTP static files)                      │     │    │
│  │  │                                                          │     │    │
│  │  │ Process:                                                 │     │    │
│  │  │ ├─ Command: /usr/bin/python3.11 -m pixell_runtime      │     │    │
│  │  │ ├─ PID: 2145 (example)                                  │     │    │
│  │  │ ├─ Working Dir: /home/agent_4906eeb7/                   │     │    │
│  │  │ ├─ Log: /var/lib/pixell/logs/agent_4906eeb7.log         │     │    │
│  │  │ └─ Environment:                                          │     │    │
│  │  │    - AGENT_APP_ID=4906eeb7-9959-414e-84c6-f2445822ebe4  │     │    │
│  │  │    - REST_PORT=63000                                     │     │    │
│  │  │    - A2A_PORT=60000                                      │     │    │
│  │  │    - UI_PORT=66000                                       │     │    │
│  │  │    - BASE_PATH=/agents/4906eeb7-9959-414e-84c6-f248...  │     │    │
│  │  │    - MULTIPLEXED=true                                    │     │    │
│  │  │    - AGENT_PACKAGE_PATH=/tmp/...vivid-commenter.apkg    │     │    │
│  │  │                                                          │     │    │
│  │  │ Runtime Structure:                                       │     │    │
│  │  │ ┌──────────────────────────────────────────────────┐   │     │    │
│  │  │ │ pixell_runtime module (Python)                   │   │     │    │
│  │  │ │                                                   │   │     │    │
│  │  │ │ 1. Package Extraction:                           │   │     │    │
│  │  │ │    ├─ Extract .apkg to /tmp/pixell_packages/     │   │     │    │
│  │  │ │    ├─ Read agent.yaml manifest                   │   │     │    │
│  │  │ │    └─ Install dependencies (pip install -r ...)   │   │     │    │
│  │  │ │                                                   │   │     │    │
│  │  │ │ 2. Surface Initialization:                       │   │     │    │
│  │  │ │    ├─ REST Surface (if entrypoint defined)       │   │     │    │
│  │  │ │    ├─ A2A Surface (if a2a.service defined)       │   │     │    │
│  │  │ │    └─ UI Surface (if ui.static_dir defined)      │   │     │    │
│  │  │ │                                                   │   │     │    │
│  │  │ │ 3. gRPC Server Setup (A2A):                      │   │     │    │
│  │  │ │    ├─ Import agent's gRPC service                │   │     │    │
│  │  │ │    │  (from a2a.service in manifest)             │   │     │    │
│  │  │ │    ├─ Create PARRoutingInterceptor               │   │     │    │
│  │  │ │    │  (strips /agents/{id}/a2a prefix)           │   │     │    │
│  │  │ │    ├─ Create grpc.aio.server()                   │   │     │    │
│  │  │ │    ├─ Add agent service to server                │   │     │    │
│  │  │ │    └─ Listen on port 60000 (A2A_PORT)            │   │     │    │
│  │  │ │                                                   │   │     │    │
│  │  │ │ 4. Start Event Loop:                             │   │     │    │
│  │  │ │    └─ asyncio.run() with all servers             │   │     │    │
│  │  │ │                                                   │   │     │    │
│  │  │ └──────────────────────────────────────────────────┘   │     │    │
│  │  │                                                          │     │    │
│  │  └─────────────────────────────────────────────────────────┘     │    │
│  │                                                                   │    │
│  │  ┌─────────────────────────────────────────────────────────┐     │    │
│  │  │ Agent: PAF Core Agent                                   │     │    │
│  │  │ ID: ed8784f3-b602-481c-8701-3b6406c8fd98              │     │    │
│  │  │ Ports: REST=63001, A2A=60001, UI=66001                 │     │    │
│  │  │ Linux User: agent_ed8784f3                             │     │    │
│  │  │ [Similar structure to above]                            │     │    │
│  │  └─────────────────────────────────────────────────────────┘     │    │
│  │                                                                   │    │
│  │  ┌─────────────────────────────────────────────────────────┐     │    │
│  │  │ Agent: Another Agent                                     │     │    │
│  │  │ ID: c489095f-xxxx-xxxx-xxxx-xxxxxxxxxxxx                │     │    │
│  │  │ Ports: REST=63002, A2A=60002, UI=66002                  │     │    │
│  │  │ [Similar structure]                                      │     │    │
│  │  └─────────────────────────────────────────────────────────┘     │    │
│  │                                                                   │    │
│  └───────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## DETAILED A2A REQUEST FLOW

### Request Journey: Client → Agent → Response

```
STEP 1: CLIENT PREPARES REQUEST
════════════════════════════════════════════════════════════════════════════

Client Code (talk_to_agent.py):
┌────────────────────────────────────────────────────────────────────────┐
│ import grpc                                                            │
│ from pixell_runtime.proto import agent_pb2, agent_pb2_grpc            │
│                                                                        │
│ # 1.1 Create A2A message (JSON-RPC 2.0 format)                        │
│ a2a_params = {                                                         │
│     "message": {                                                       │
│         "kind": "message",                                             │
│         "role": "user",                                                │
│         "messageId": "msg-uuid-1234",                                 │
│         "metadata": {                                                  │
│             "skill": "chat",           # Action to perform            │
│             "params": {                # Parameters                   │
│                 "message": "Hello!"                                    │
│             }                                                          │
│         },                                                             │
│         "parts": [                                                     │
│             {"kind": "text", "text": "Hello!"}                        │
│         ]                                                              │
│     }                                                                  │
│ }                                                                      │
│                                                                        │
│ # 1.2 Wrap in gRPC ActionRequest                                      │
│ request = agent_pb2.ActionRequest(                                     │
│     message=agent_pb2.A2AMessage(                                      │
│         jsonrpc="2.0",                                                 │
│         id="req-uuid-5678",                                            │
│         method="message/send",                                         │
│         params_json=json.dumps(a2a_params)                             │
│     )                                                                  │
│ )                                                                      │
│                                                                        │
│ # 1.3 Create interceptor to add path prefix                           │
│ # This transforms gRPC method paths for ALB routing                   │
│ interceptor = PathPrefixInterceptor("/agents/4906eeb7-xxx/a2a")       │
│                                                                        │
│ # 1.4 Create gRPC channel with TLS                                    │
│ credentials = grpc.ssl_channel_credentials()                          │
│ channel = grpc.aio.secure_channel(                                    │
│     "par.pixell.global:443",                                          │
│     credentials,                                                       │
│     interceptors=[interceptor]                                         │
│ )                                                                      │
│                                                                        │
│ # 1.5 Create stub and invoke                                          │
│ stub = agent_pb2_grpc.AgentServiceStub(channel)                       │
│ response = await stub.Invoke(request)                                 │
│                                                                        │
│ # Client sends gRPC call with method path:                            │
│ # /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/                  │
│ #     pixell.agent.AgentService/Invoke                                │
└────────────────────────────────────────────────────────────────────────┘


STEP 2: DNS RESOLUTION
════════════════════════════════════════════════════════════════════════════

Client DNS Lookup:
  Domain: par.pixell.global
  Result: → 18.216.3.57, 18.219.207.35 (ALB IP addresses)

TCP Connection:
  Client → 18.216.3.57:443 (HTTPS/TLS)

TLS Handshake:
  Client ← Server Certificate (*.pixell.global)
  Client → ClientHello
  ← Established TLS 1.3 connection


STEP 3: ALB ROUTING (AWS Application Load Balancer)
════════════════════════════════════════════════════════════════════════════

ALB receives HTTPS request on port 443:

Request Details:
┌────────────────────────────────────────────────────────────────────────┐
│ Protocol: HTTP/2 (gRPC requirement)                                   │
│ Path: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/               │
│       pixell.agent.AgentService/Invoke                                │
│ Method: POST                                                           │
│ Headers:                                                               │
│   - Host: par.pixell.global                                           │
│   - Content-Type: application/grpc                                    │
│   - grpc-encoding: identity                                           │
│   - user-agent: grpc-python/1.x.x                                     │
│ Body: [Protobuf-encoded ActionRequest]                                │
└────────────────────────────────────────────────────────────────────────┘

ALB Processing:
1. TLS termination (decrypt HTTPS → HTTP/2)
2. Path matching against listener rules
3. Extract agent_id from path: 4906eeb7-9959-414e-84c6-f2445822ebe4
4. Match rule: /agents/4906eeb7-*/a2a/*
5. Select target group: pac-agent-4906eeb7-grpc
6. Load balance to target: 10.0.1.37:60000
7. Health check: Verify target is healthy (GET /agents/4906eeb7-*/health)

Target Group Configuration:
┌────────────────────────────────────────────────────────────────────────┐
│ Name: pac-agent-4906eeb7-grpc                                         │
│ Protocol: HTTP                                                         │
│ ProtocolVersion: HTTP2 ⚠️ CRITICAL FOR gRPC                          │
│ Port: 60000                                                            │
│ VPC: vpc-0039e5988107ae565                                            │
│ Health Check:                                                          │
│   - Protocol: HTTP                                                     │
│   - Path: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/health         │
│   - Port: traffic-port (60000)                                        │
│   - Interval: 30s                                                      │
│   - Timeout: 5s                                                        │
│   - Healthy Threshold: 2                                               │
│   - Unhealthy Threshold: 2                                             │
│ Targets:                                                               │
│   - 10.0.1.37:60000 (Status: healthy)                                 │
└────────────────────────────────────────────────────────────────────────┘

ALB forwards HTTP/2 request to 10.0.1.37:60000


STEP 4: AGENT gRPC SERVER RECEIVES REQUEST
════════════════════════════════════════════════════════════════════════════

Request arrives at agent's gRPC server on port 60000:

Agent Process (agent_4906eeb7):
┌────────────────────────────────────────────────────────────────────────┐
│ gRPC Server Configuration:                                            │
│   - Port: 60000                                                        │
│   - Protocol: gRPC (HTTP/2)                                           │
│   - Interceptors:                                                      │
│     └─ PARRoutingInterceptor (strips path prefix)                    │
│                                                                        │
│ Incoming Request:                                                      │
│   Path: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/             │
│         pixell.agent.AgentService/Invoke                              │
│                                                                        │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ STEP 4.1: PARRoutingInterceptor                                  │ │
│ │ (src/pixell_runtime/a2a/interceptor.py)                          │ │
│ │                                                                   │ │
│ │ Interceptor receives request:                                     │ │
│ │   - Original path: /agents/4906eeb7.../a2a/pixell.agent...       │ │
│ │   - Prefix to strip: /agents/4906eeb7.../a2a                     │ │
│ │                                                                   │ │
│ │ Processing:                                                       │ │
│ │   1. Extract path from handler_call_details.method               │ │
│ │   2. Check if starts with prefix                                 │ │
│ │   3. Strip prefix from path                                      │ │
│ │   4. Create new handler details with clean path                  │ │
│ │                                                                   │ │
│ │ Result:                                                           │ │
│ │   - Clean path: /pixell.agent.AgentService/Invoke                │ │
│ │   - Forward to agent's gRPC handler                              │ │
│ │                                                                   │ │
│ │ Code:                                                             │ │
│ │   if original_method.startswith(self.prefix):                    │ │
│ │       stripped_method = original_method[self.prefix_len:]        │ │
│ │       modified_details = _HandlerCallDetails(                    │ │
│ │           method=stripped_method,                                │ │
│ │           invocation_metadata=...                                │ │
│ │       )                                                           │ │
│ │       return await continuation(modified_details)                │ │
│ └──────────────────────────────────────────────────────────────────┘ │
│                                                                        │
│ ┌──────────────────────────────────────────────────────────────────┐ │
│ │ STEP 4.2: Agent's gRPC Service Handler                           │ │
│ │ (Agent's code from .apkg, e.g., vivid-commenter)                 │ │
│ │                                                                   │ │
│ │ Service Definition (from agent.proto):                            │ │
│ │   service AgentService {                                          │ │
│ │       rpc Invoke(ActionRequest) returns (ActionResult);          │ │
│ │   }                                                               │ │
│ │                                                                   │ │
│ │ Handler receives:                                                 │ │
│ │   - Clean method: /pixell.agent.AgentService/Invoke              │ │
│ │   - Request: ActionRequest protobuf message                      │ │
│ │                                                                   │ │
│ │ Agent Code (example from vivid-commenter):                        │ │
│ │   async def Invoke(self, request, context):                      │ │
│ │       # Extract A2A message                                      │ │
│ │       a2a_msg = request.message                                  │ │
│ │                                                                   │ │
│ │       # Parse JSON-RPC 2.0 params                                │ │
│ │       params = json.loads(a2a_msg.params_json)                   │ │
│ │       message = params["message"]                                │ │
│ │       skill = message["metadata"]["skill"]                       │ │
│ │       user_params = message["metadata"]["params"]                │ │
│ │                                                                   │ │
│ │       # Route to skill handler                                   │ │
│ │       if skill == "chat":                                        │ │
│ │           result = await self.handle_chat(user_params)           │ │
│ │       elif skill == "comment":                                   │ │
│ │           result = await self.handle_comment(user_params)        │ │
│ │       else:                                                       │ │
│ │           result = {"error": "Unknown skill"}                    │ │
│ │                                                                   │ │
│ │       # Build response                                           │ │
│ │       return agent_pb2.ActionResult(                             │ │
│ │           success=True,                                          │ │
│ │           result=json.dumps(result),                             │ │
│ │           request_id=a2a_msg.id,                                 │ │
│ │           duration_ms=100                                        │ │
│ │       )                                                           │ │
│ └──────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘


STEP 5: RESPONSE FLOW (Agent → Client)
════════════════════════════════════════════════════════════════════════════

Agent returns gRPC response:

Response Message (ActionResult):
┌────────────────────────────────────────────────────────────────────────┐
│ success: true                                                          │
│ result: '{"response": "Hello! I can help with code comments."}'       │
│ error: ""                                                              │
│ request_id: "req-uuid-5678"                                           │
│ duration_ms: 95                                                        │
│ metadata: {}                                                           │
└────────────────────────────────────────────────────────────────────────┘

Response Path:
  Agent gRPC Server (port 60000)
    → Serialized as Protobuf
    → Sent over HTTP/2 connection
  ↓
  ALB (10.0.1.37:60000 → par.pixell.global:443)
    → Forwards response to client
    → TLS encryption
  ↓
  Client (talk_to_agent.py)
    → Receives gRPC response
    → Deserializes Protobuf → ActionResult object
    → Parses result JSON
    → Displays to user
```

---

## PROTOCOL DEEP DIVE

### A2A Message Format (JSON-RPC 2.0)

The A2A protocol wraps JSON-RPC 2.0 messages in gRPC for transport efficiency and type safety.

#### Complete Message Structure

```python
# Wire Format (gRPC Protobuf)
ActionRequest {
    message: A2AMessage {
        jsonrpc: "2.0"                    # JSON-RPC version
        id: "req-abc-123"                 # Request UUID
        method: "message/send"            # Always "message/send" for A2A
        params_json: "{...}"              # JSON-encoded message (see below)
    }
}

# Decoded params_json (A2A Message)
{
    "message": {
        "kind": "message",                # Message type
        "role": "user",                   # Sender role
        "messageId": "msg-xyz-789",       # Message UUID
        "metadata": {
            "skill": "chat",              # Agent action/skill to invoke
            "params": {                   # Skill-specific parameters
                "message": "Hello!",
                "context": {...}
            }
        },
        "parts": [                        # Message content parts
            {
                "kind": "text",
                "text": "Hello!"
            }
        ]
    }
}
```

#### Protocol Evolution

**Legacy Format (Deprecated):**
```python
ActionRequest {
    action: "chat"                        # Direct action field
    parameters: {"message": "Hello!"}     # Direct params map
    request_id: "req-123"
}
```

**Current A2A Format:**
```python
ActionRequest {
    message: A2AMessage {
        jsonrpc: "2.0"
        method: "message/send"
        params_json: "{\"message\": {...}}"  # Nested message structure
    }
}
```

**Why the Change?**
- Standard JSON-RPC 2.0 compliance (interoperable with other systems)
- Support for multi-part messages (text, images, files)
- Richer metadata for agent routing and coordination
- Better support for conversational context

---

## PORT ALLOCATION STRATEGY

### Current Port Ranges (Per EC2 Instance)

```
Port Range Allocation:
┌─────────────────────────────────────────────────────────────────────┐
│ Surface Type  │ Port Range      │ Capacity  │ Current Usage       │
├───────────────┼─────────────────┼───────────┼─────────────────────┤
│ A2A (gRPC)    │ 60000 - 60199   │ 200 slots │ 3 agents (1.5%)     │
│ REST (HTTP)   │ 63000 - 63199   │ 200 slots │ 3 agents (1.5%)     │
│ UI (HTTP)     │ 66000 - 66199   │ 200 slots │ 0 agents (0%)       │
├───────────────┼─────────────────┼───────────┼─────────────────────┤
│ Supervisor    │ 9000            │ 1         │ 1 (supervisor API)  │
│ Gateway       │ 50051           │ 1         │ 1 (gRPC gateway)    │
└─────────────────────────────────────────────────────────────────────┘

Current Deployment:
  agent_4906eeb7 (Vivid Commenter):
    - A2A: 60000
    - REST: 63000
    - UI: 66000

  agent_ed8784f3 (PAF Core Agent):
    - A2A: 60001
    - REST: 63001
    - UI: 66001

  agent_c489095f (Another Agent):
    - A2A: 60002
    - REST: 63002
    - UI: 66002

Scaling Capacity:
  - Max agents per instance: 200 (limited by port range)
  - Current usage: 3 agents (1.5%)
  - Available slots: 197 agents
```

### Port Allocation Algorithm

```python
# Supervisor Port Allocator (port_allocator.py)

class PortAllocator:
    def __init__(self):
        self.base_ports = {
            "a2a": 60000,     # gRPC (Agent-to-Agent)
            "rest": 63000,    # REST API
            "ui": 66000,      # UI static files
        }
        self.max_agents = 200
        self.allocated = set()  # Set of allocated indices (0-199)

    def allocate(self) -> Ports:
        """Allocate next available port set."""
        # Find first available index
        for idx in range(self.max_agents):
            if idx not in self.allocated:
                self.allocated.add(idx)
                return Ports(
                    rest=self.base_ports["rest"] + idx,   # e.g., 63003
                    a2a=self.base_ports["a2a"] + idx,     # e.g., 60003
                    ui=self.base_ports["ui"] + idx,       # e.g., 66003
                )
        raise RuntimeError("No available ports (max 200 agents)")

    def release(self, ports: Ports):
        """Release ports back to pool."""
        idx = ports.a2a - self.base_ports["a2a"]
        self.allocated.discard(idx)
```

---

## SECURITY & ISOLATION

### Linux User Isolation

Each agent runs as a separate Linux user for security isolation:

```bash
# User Creation (UserManager.create_user)
sudo useradd -m -s /bin/bash agent_4906eeb7

# Home Directory Structure:
/home/agent_4906eeb7/
├── .cache/               # UV/pip cache
├── .local/               # User-local packages
└── venv/                 # Python virtual environment (if used)

# Shared Package Extraction:
/tmp/pixell_packages/
├── vivid-commenter@1.0.1/
│   ├── src/
│   ├── agent.yaml
│   └── requirements.txt
└── paf-core-agent@2.0.0/
    └── ...

# Permissions:
- Agent user owns: /home/agent_4906eeb7/
- Agent user can read: /tmp/pixell_packages/vivid-commenter@1.0.1/
- Agent user CANNOT: access other agents' home directories
```

### Process Spawning

```python
# ProcessManager.spawn_agent()

# Build environment for agent
process_env = {
    "AGENT_APP_ID": "4906eeb7-9959-414e-84c6-f2445822ebe4",
    "REST_PORT": "63000",
    "A2A_PORT": "60000",
    "UI_PORT": "66000",
    "BASE_PATH": "/agents/4906eeb7-9959-414e-84c6-f2445822ebe4",
    "MULTIPLEXED": "true",
    "PYTHONUNBUFFERED": "1",
    "HOME": "/home/agent_4906eeb7",
}

# Spawn process as target user (Python 3.9+ feature)
process = subprocess.Popen(
    ["/usr/bin/python3.11", "-m", "pixell_runtime"],
    user="agent_4906eeb7",        # ← setuid to agent user
    env=process_env,
    stdout=log_handle,
    stderr=subprocess.STDOUT,
)
```

**Security Benefits:**
- Filesystem isolation (agent can only write to its home directory)
- Process isolation (agent cannot signal other agents)
- Resource limits (can apply cgroups per user)
- Audit trail (all actions logged under specific user)

**Limitations:**
- All agents share same network namespace (can bind to any IP)
- All agents can read shared package cache (/tmp/pixell_packages/)
- No CPU/memory limits enforced (requires cgroups integration)

---

## HEALTH MONITORING

### ALB Health Checks

Each agent has dedicated health check configuration:

```yaml
Target Group: pac-agent-4906eeb7-grpc
Health Check:
  Protocol: HTTP                    # Not gRPC (ALB limitation)
  Port: traffic-port                # Same as gRPC port (60000)
  Path: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/health
  Interval: 30 seconds
  Timeout: 5 seconds
  Healthy Threshold: 2 consecutive successes
  Unhealthy Threshold: 2 consecutive failures

Expected Response:
  Status: 200 OK
  Body: {"ok": true, "message": "Agent healthy", "timestamp": 1730476800}
```

**Health Check Implementation (Agent):**

```python
# pixell_runtime provides health endpoint for each agent

class AgentService(agent_pb2_grpc.AgentServiceServicer):
    async def Health(self, request, context):
        """Health check endpoint."""
        return agent_pb2.HealthStatus(
            ok=True,
            message="Agent healthy",
            timestamp=int(time.time())
        )
```

### Supervisor Health Monitoring

```bash
# Supervisor HTTP Health Check
GET http://10.0.1.37:9000/health

Response:
{
  "status": "healthy",
  "agents_running": 3,
  "capacity": {
    "current": 3,
    "max": 200,
    "available": 197
  },
  "disk_free_gb": 45.2,
  "memory_free_mb": 2048,
  "cpu_load": [0.15, 0.20, 0.18]
}
```

---

## CURRENT ARCHITECTURE LIMITATIONS

### 1. Port-Based Routing Scalability

**Issue:** Each agent requires 3 unique TCP ports (REST, A2A, UI)

**Impact:**
- Max 200 agents per EC2 instance (limited by port range 60000-60199)
- Port management complexity
- Cannot exceed port range without code changes

**Alternative (Discussed):**
- Unix domain sockets for local routing
- Would eliminate port limits
- Requires ALB integration changes

### 2. Single EC2 Instance (No Horizontal Scaling)

**Issue:** Only 1 EC2 instance handles all agent traffic

**Impact:**
- Single point of failure
- No auto-scaling
- Limited to one instance's resources (1 vCPU, 4GB RAM)

**Future Architecture:**
- Multi-instance deployment
- Redis-based agent registry for cross-instance routing
- ALB distributes traffic across multiple EC2 instances

### 3. No Resource Limits Per Agent

**Issue:** Agents share EC2 resources with no enforced limits

**Impact:**
- One heavy agent can starve others (CPU/memory)
- No guaranteed QoS per agent
- Hard to predict capacity

**Solution:**
- Implement cgroups for CPU/memory limits
- Monitor per-agent resource usage
- Classify agents by resource tier

### 4. gRPC Gateway Not Used

**Issue:** Built gRPC gateway on port 50051 but ALB routes directly to agents

**Current Flow:**
```
ALB :443 → Agent :60000 (direct)
```

**Original Design:**
```
ALB :443 → Gateway :50051 → Agent :60000 (via gateway)
```

**Why Changed:**
- ALB HTTP2 target groups work well for direct routing
- Gateway adds latency
- Simpler architecture

---

## PERFORMANCE CHARACTERISTICS

### Request Latency Breakdown

**Typical A2A Request (measured from talk_to_agent.py):**

```
Total E2E Latency: ~150-300ms

Breakdown:
├─ DNS Resolution: 5-10ms
│  (cached after first request)
├─ TLS Handshake: 20-40ms
│  (reused for multiple requests)
├─ ALB Processing: 5-15ms
│  (path matching, target selection)
├─ Network (ALB → EC2): 1-2ms
│  (same VPC, low latency)
├─ gRPC Deserialization: 1-2ms
│  (Protobuf parsing)
├─ Interceptor Processing: <1ms
│  (string manipulation)
├─ Agent Handler: 50-200ms
│  (varies by agent logic, ML inference, etc.)
├─ Response Serialization: 1-2ms
└─ Return Path: 10-20ms
   (EC2 → ALB → Client)

Optimizations Applied:
- Connection pooling (gRPC reuses HTTP/2 connections)
- DNS caching (GRPC_DNS_RESOLVER=native)
- TLS session resumption
- Protobuf binary encoding (smaller than JSON)
```

### Throughput Capacity

**Single Agent (on m7g.medium):**
- Concurrent requests: ~100-500 (depends on agent logic)
- Requests per second: ~50-200 (I/O-bound agents)
- Requests per second: ~10-50 (CPU-bound agents, e.g., ML inference)

**EC2 Instance (3 agents currently):**
- Total capacity: ~150-600 req/sec (aggregate)
- Current load: <5 req/sec (development usage)
- Headroom: 95%+ available

---

## TROUBLESHOOTING GUIDE

### Common Issues

#### 1. 464 "Incompatible Protocol" Error

**Symptom:** gRPC requests fail with "StatusCode.UNKNOWN: Received http2 header with status: 464"

**Root Cause:** ALB target group has `ProtocolVersion: HTTP1` instead of `HTTP2`

**Fix:**
```bash
# 1. Identify target group
aws elbv2 describe-target-groups --region us-east-2 \
  --names pac-agent-4906eeb7-grpc \
  --query 'TargetGroups[0].ProtocolVersion'

# Output: "HTTP1" ← WRONG!

# 2. Delete and recreate target group (ProtocolVersion is IMMUTABLE)
TG_ARN=$(aws elbv2 describe-target-groups --region us-east-2 \
  --names pac-agent-4906eeb7-grpc \
  --query 'TargetGroups[0].TargetGroupArn' --output text)

aws elbv2 delete-target-group --region us-east-2 --target-group-arn $TG_ARN

# 3. Redeploy agent (PAC will create new target group with HTTP2)
```

**Prevention:** Ensure `ec2-multi-agent.ts:ensureHttpTargetGroup()` uses `protocolVersion: 'HTTP2'`

#### 2. Agent Not Receiving Requests (404 from ALB)

**Symptom:** Client gets 404 Not Found

**Diagnosis:**
```bash
# Check ALB listener rules
aws elbv2 describe-rules --region us-east-2 \
  --listener-arn <LISTENER_ARN> \
  --query 'Rules[?contains(Conditions[0].Values[0], `4906eeb7`)]'

# Output: [] ← No rule found!

# Check target group registration
aws elbv2 describe-target-health --region us-east-2 \
  --target-group-arn <TG_ARN>

# Expected: TargetHealth.State = "healthy"
```

**Fix:** Redeploy agent to create ALB rules

#### 3. Agent Process Crashed (Zombie)

**Symptom:** ALB health check fails, supervisor reports agent as "running" but process is dead

**Diagnosis:**
```bash
# SSH to EC2
ssh ec2-user@18.119.137.118

# Check agent process
ps aux | grep agent_4906eeb7

# If zombie:
#   agent_4906eeb7  2145  0.0  0.0      0     0 ?        Z    10:00   0:00 [python3.11] <defunct>

# Check supervisor status
curl http://localhost:9000/agents/4906eeb7-9959-414e-84c6-f2445822ebe4/status
```

**Fix:**
```bash
# Stop and restart agent via supervisor
curl -X DELETE http://localhost:9000/agents/4906eeb7-9959-414e-84c6-f2445822ebe4

# Redeploy
curl -X POST http://localhost:9000/agents \
  -H "Content-Type: application/json" \
  -d '{...}'
```

---

## FUTURE ENHANCEMENTS

### 1. Socket-Based Routing (Eliminates Port Limits)

**Proposal:**
```
Current: Each agent binds to 3 TCP ports (60000, 63000, 66000)
Future:  Each agent binds to Unix sockets

/var/run/pixell/agents/
├── 4906eeb7-9959-414e-84c6-f2445822ebe4/
│   ├── a2a.sock    ← gRPC server
│   ├── rest.sock   ← REST API
│   └── ui.sock     ← UI server
└── ed8784f3-b602-481c-8701-3b6406c8fd98/
    └── ...

Benefits:
- No port exhaustion (10,000+ agents per instance)
- Faster IPC (no TCP overhead)
- Simpler port management

Challenges:
- ALB cannot route to Unix sockets
- Need reverse proxy on EC2 (nginx, envoy)
- More complex health checks
```

### 2. Multi-Instance Deployment with Redis

**Current:**
```
ALB → EC2 (single instance) → Agents
```

**Future:**
```
                    ┌─→ EC2-1 (agents 1-200)
ALB → Redis Lookup ─┼─→ EC2-2 (agents 201-400)
                    └─→ EC2-3 (agents 401-600)

Redis stores: agent_id → instance_ip mapping
```

### 3. Resource Limits via cgroups

```bash
# Create cgroup for agent
cgcreate -g cpu,memory:/agents/agent_4906eeb7

# Set limits
echo "256000" > /sys/fs/cgroup/cpu/agents/agent_4906eeb7/cpu.cfs_quota_us
echo "512M" > /sys/fs/cgroup/memory/agents/agent_4906eeb7/memory.limit_in_bytes

# Spawn agent in cgroup
cgexec -g cpu,memory:/agents/agent_4906eeb7 \
  python3.11 -m pixell_runtime
```

---

## APPENDIX: KEY FILES REFERENCE

### Supervisor (EC2)

**Main Entry Point:**
- `src/pixell_runtime/supervisor/__main__.py` - Supervisor startup
- `src/pixell_runtime/supervisor/server.py` - HTTP API (port 9000)
- `src/pixell_runtime/supervisor/grpc_gateway.py` - gRPC gateway (port 50051)

**Core Components:**
- `src/pixell_runtime/supervisor/state.py` - SupervisorState (agent registry)
- `src/pixell_runtime/supervisor/process_manager.py` - Process lifecycle
- `src/pixell_runtime/supervisor/port_allocator.py` - Port allocation
- `src/pixell_runtime/supervisor/user_manager.py` - Linux user management
- `src/pixell_runtime/supervisor/package_downloader.py` - S3 package download

### Agent Runtime

**Protocol:**
- `src/pixell_runtime/proto/agent.proto` - gRPC service definition
- `src/pixell_runtime/proto/agent_pb2.py` - Generated Protobuf code
- `src/pixell_runtime/proto/agent_pb2_grpc.py` - Generated gRPC stubs

**Interceptors:**
- `src/pixell_runtime/a2a/interceptor.py` - PARRoutingInterceptor (path stripping)

**Client:**
- `talk_to_agent.py` - Interactive A2A client
- `src/pixell_runtime/agent_registry.py` - Agent configuration registry

### Deployment (PAC)

**Orchestration:**
- `pixell-agent-cloud/src/lib/deployment/ec2-multi-agent.ts` - Deployment orchestrator
- `pixell-agent-cloud/src/lib/supervisor/client.ts` - gRPC client to supervisor
- `pixell-agent-cloud/src/lib/aws/alb.ts` - ALB target group management

---

**Document Status:** Complete
**Last Updated:** November 1, 2025
**Next Review:** When architectural changes are proposed or implemented
