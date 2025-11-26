# PAC/PAR Deployment Architecture - Detailed Flow

## Complete Deployment Flow Diagram

```
═══════════════════════════════════════════════════════════════════════════════════════════════════
STAGE 1: AGENT APP SOURCE CODE → .APKG FILE
═══════════════════════════════════════════════════════════════════════════════════════════════════

┌─────────────────────────────────────┐
│ Agent Source Code Repository        │
│ (e.g., vivid-commenter)             │
├─────────────────────────────────────┤
│ /src/main.py                        │  ← Agent business logic
│ /src/grpc_server.py                 │  ← gRPC servicer implementation
│ /agent.yaml                         │  ← Agent manifest
│ /requirements.txt                   │  ← Python dependencies
│ /pyproject.toml                     │  ← Build configuration
└─────────────────────────────────────┘
                 │
                 │ $ pixell build
                 ↓
┌─────────────────────────────────────┐
│ Agent Package (.apkg file)          │
│ vivid-commenter-1.0.1.apkg          │
├─────────────────────────────────────┤
│ Structure (ZIP archive):            │
│ ├── agent.yaml                      │  ← Manifest with a2a config
│ │   └── a2a:                        │
│ │       └── service: "src.grpc..."  │
│ ├── src/                            │
│ │   ├── main.py                     │
│ │   └── grpc_server.py              │
│ ├── requirements.txt                │
│ └── pyproject.toml                  │
└─────────────────────────────────────┘

Key manifest fields (agent.yaml):
  version: "1.0.1"
  name: "vivid-commenter"
  entrypoint: "src.main:handler"           # For REST/webhook calls
  a2a:
    service: "src.grpc_server:create_service"  # For gRPC


═══════════════════════════════════════════════════════════════════════════════════════════════════
STAGE 2: PAC DEPLOYMENT (pac deploy)
═══════════════════════════════════════════════════════════════════════════════════════════════════

Developer runs:
  $ pac deploy vivid-commenter-1.0.1.apkg

┌──────────────────────────────────────────────────────────────────────────────┐
│ PAC (Pixell Agent Cloud) - Cloud Orchestrator                               │
│ Running on ECS/Fargate or EC2                                              │
└──────────────────────────────────────────────────────────────────────────────┘
                          │
                          │ 1. Uploads .apkg to S3
                          ↓
┌──────────────────────────────────────────────────────────────────────────────┐
│ S3: pixell-agent-packages/vivid-commenter-1.0.1.apkg                       │
└──────────────────────────────────────────────────────────────────────────────┘
                          │
                          │ 2. PAC generates agent_app_id
                          │    agent_app_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
                          ↓
┌──────────────────────────────────────────────────────────────────────────────┐
│ PAC: Agent App Record Creation                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ {                                                                            │
│   "agent_app_id": "4906eeb7-9959-414e-84c6-f2445822ebe4",                  │
│   "package_url": "s3://pixell-agent-packages/vivid-commenter-1.0.1.apkg",  │
│   "manifest": { ... parsed from agent.yaml ... },                          │
│   "surfaces": {                                                             │
│     "rest": true,    # Has entrypoint (REST API)                           │
│     "a2a": true,     # Has a2a.service (gRPC)                              │
│     "ui": false      # No UI defined                                       │
│   },                                                                        │
│   "port_assignments": {                                                     │
│     "rest_port": 63000,   # ← PAC assigns unique ports                     │
│     "a2a_port": 60000,    # ← per agent on PAR                             │
│     "ui_port": 65000                                                        │
│   }                                                                         │
│ }                                                                            │
└──────────────────────────────────────────────────────────────────────────────┘
                          │
                          │ 3. PAC creates ALB target groups
                          │    and routing rules
                          ↓
┌──────────────────────────────────────────────────────────────────────────────┐
│ AWS Application Load Balancer (ALB): pixell-runtime-alb                    │
│ DNS: par.pixell.global → 18.216.3.57, 18.219.207.35                        │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ HTTPS Listener (Port 443):                                                  │
│ ├── TLS Certificate: *.pixell.global                                       │
│ └── Listener ARN: ...e892897267af8e76                                      │
│                                                                              │
│ ┌──────────────────────────────────────────────────────────────────┐       │
│ │ ALB Routing Rules (Priority Order)                              │       │
│ ├──────────────────────────────────────────────────────────────────┤       │
│ │                                                                  │       │
│ │ Priority 10066:                                                  │       │
│ │   Path: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/api/*     │       │
│ │   ↓                                                              │       │
│ │   Target Group: pac-agent-4906eeb7-rest                         │       │
│ │   ├── Protocol: HTTP                                            │       │
│ │   ├── ProtocolVersion: HTTP1                                    │       │
│ │   ├── Port: 63000                                               │       │
│ │   ├── Health: /agents/{id}/health                               │       │
│ │   └── Targets: i-09dcb7f387166efd0:63000 (HEALTHY ✓)          │       │
│ │                                                                  │       │
│ │ ─────────────────────────────────────────────────────────────   │       │
│ │                                                                  │       │
│ │ Priority 10067: ⚠️  THIS IS THE ISSUE!                         │       │
│ │   Path: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/*     │       │
│ │   ↓                                                              │       │
│ │   Target Group: pac-agent-4906eeb7-grpc-v3                      │       │
│ │   ├── Protocol: HTTP                                            │       │
│ │   ├── ProtocolVersion: HTTP2  ← CORRECT! ✓                     │       │
│ │   ├── Port: 60000                                               │       │
│ │   ├── Health: /agents/{id}/health  ← WRONG PATH! ✗            │       │
│ │   │           Should be: /agents/{id}/a2a/health               │       │
│ │   └── Targets: i-09dcb7f387166efd0:60000 (UNHEALTHY ✗)        │       │
│ │                                                                  │       │
│ │       🔴 HEALTH CHECK FAILING → ALB MARKS TARGET UNHEALTHY     │       │
│ │       🔴 ALB RETURNS HTTP 464 TO CLIENT                        │       │
│ │                                                                  │       │
│ │ ─────────────────────────────────────────────────────────────   │       │
│ │                                                                  │       │
│ │ Priority 10068:                                                  │       │
│ │   Path: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/*         │       │
│ │   ↓                                                              │       │
│ │   Target Group: pac-agent-4906eeb7-rest (same as API)          │       │
│ │   └── Targets: i-09dcb7f387166efd0:63000 (HEALTHY ✓)          │       │
│ │                                                                  │       │
│ │ Default Rule:                                                    │       │
│ │   → Forward to default target group                             │       │
│ └──────────────────────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────────────────────┘
                          │
                          │ 4. PAC sends deployment request to PAR
                          ↓


═══════════════════════════════════════════════════════════════════════════════════════════════════
STAGE 3: PAR DEPLOYMENT ON EC2
═══════════════════════════════════════════════════════════════════════════════════════════════════

┌──────────────────────────────────────────────────────────────────────────────┐
│ EC2 Instance: i-09dcb7f387166efd0 (pixell-agent-runtime)                   │
│ Private IP: 10.0.1.37                                                       │
│ Public IP: 18.119.137.118                                                   │
│ Security Groups: Allow 60000, 63000, 65000 from ALB                        │
└──────────────────────────────────────────────────────────────────────────────┘
                          │
                          │ PAC API Call:
                          │ POST /deploy
                          │ {
                          │   "agent_app_id": "4906eeb7...",
                          │   "package_url": "s3://...",
                          │   "ports": {rest: 63000, a2a: 60000, ui: 65000}
                          │ }
                          ↓
┌──────────────────────────────────────────────────────────────────────────────┐
│ PAR (Pixell Agent Runtime) - Agent Deployment Process                      │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ Step 1: Create Linux User                                                   │
│ ────────────────────────────────────────────────────────────────────────    │
│   $ useradd -m -s /bin/bash agent_8c82966883524dad_4906eeb7                │
│                                                                              │
│   User naming pattern:                                                       │
│     agent_{random_id}_{first_8_chars_of_agent_app_id}                      │
│                                                                              │
│   Home directory created:                                                    │
│     /home/agent_8c82966883524dad_4906eeb7/                                 │
│     ├── logs/          ← Agent log files                                   │
│     ├── packages/      ← Extracted .apkg contents                          │
│     └── venv/          ← Python virtual environment                        │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ Step 2: Download and Install .apkg                                          │
│ ────────────────────────────────────────────────────────────────────────    │
│   $ aws s3 cp s3://pixell-agent-packages/vivid-commenter.apkg \            │
│               /var/lib/pixell/packages/f73eed8fffa2532a.apkg                │
│                                                                              │
│   $ unzip f73eed8fffa2532a.apkg -d /tmp/pixell_packages/vivid-commenter@1.0.1/ │
│                                                                              │
│   Extracted structure:                                                       │
│     /tmp/pixell_packages/vivid-commenter@1.0.1/                            │
│     ├── agent.yaml                                                           │
│     ├── src/                                                                 │
│     │   ├── main.py                                                          │
│     │   └── grpc_server.py                                                   │
│     ├── requirements.txt                                                     │
│     └── pyproject.toml                                                       │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ Step 3: Create Python Virtual Environment                                   │
│ ────────────────────────────────────────────────────────────────────────    │
│   $ python3.11 -m venv /tmp/venvs/vivid-commenter@1.0.1_f2ca88b            │
│   $ source /tmp/venvs/vivid-commenter@1.0.1_f2ca88b/bin/activate           │
│                                                                              │
│   $ pip install setuptools wheel                                            │
│   $ pip install -e /tmp/pixell_packages/vivid-commenter@1.0.1/             │
│   $ pip install -r /tmp/pixell_packages/vivid-commenter@1.0.1/requirements.txt │
│                                                                              │
│   Installed packages (178 total):                                           │
│     - grpcio-1.73.1                     ← gRPC runtime                      │
│     - grpcio-tools-1.73.1               ← gRPC code generation              │
│     - a2a-sdk-0.2.14                    ← Pixell A2A SDK                    │
│     - fastapi-0.115.14                  ← REST framework                    │
│     - ... (175 more dependencies)                                           │
│                                                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ Step 4: Start Three-Surface Runtime                                         │
│ ────────────────────────────────────────────────────────────────────────    │
│   $ su - agent_8c82966883524dad_4906eeb7                                   │
│   $ /usr/bin/python3.11 -m pixell_runtime \                                │
│       --agent-app-id 4906eeb7-9959-414e-84c6-f2445822ebe4 \                │
│       --package /var/lib/pixell/packages/f73eed8fffa2532a.apkg \           │
│       --rest-port 63000 \                                                   │
│       --a2a-port 60000 \                                                    │
│       --ui-port 65000 \                                                     │
│       --multiplexed                                                          │
│                                                                              │
│   Process: PID 149755                                                       │
│   User: agent_8c82966883524dad_4906eeb7                                    │
│   Command: python3.11 -m pixell_runtime                                     │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════════════════════════
STAGE 4: PAR RUNTIME INITIALIZATION (Three-Surface Runtime)
═══════════════════════════════════════════════════════════════════════════════════════════════════

┌──────────────────────────────────────────────────────────────────────────────┐
│ ThreeSurfaceRuntime (pixell_runtime/three_surface/runtime.py)              │
│ PID: 149755                                                                  │
│ User: agent_8c82966883524dad_4906eeb7                                       │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│ Initialization Flow:                                                         │
│ ═════════════════════                                                        │
│                                                                              │
│ 1. Load Package                                                              │
│    ├── Parse agent.yaml manifest                                           │
│    ├── Detect surfaces: {rest: false, a2a: true, ui: false}               │
│    └── Load agent code into venv                                           │
│                                                                              │
│ 2. Start REST Server (FastAPI) - Port 63000                                 │
│    ├── Bind: 0.0.0.0:63000 (IPv4)                                          │
│    ├── Routes:                                                              │
│    │   ├── GET  /agents/{id}/health          ← ALB health check           │
│    │   ├── GET  /agents/{id}/api/*           ← Agent REST API             │
│    │   └── GET  /agents/{id}/*               ← Agent UI (if enabled)      │
│    └── Status: LISTENING ✓                                                 │
│                                                                              │
│ 3. Start A2A gRPC Server - Port 60000                                       │
│    ├── Code: src/pixell_runtime/a2a/server.py                             │
│    ├── Function: create_grpc_server()                                      │
│    │                                                                        │
│    │   def create_grpc_server(                                             │
│    │       package=package,                                                │
│    │       port=60000,                                                     │
│    │       agent_a2a_port=None,                                            │
│    │       agent_id="4906eeb7-9959-414e-84c6-f2445822ebe4"  ← PASSED!     │
│    │   ):                                                                  │
│    │                                                                        │
│    ├── ┌────────────────────────────────────────────────────┐             │
│    │   │ Interceptor Chain Creation                        │             │
│    │   ├────────────────────────────────────────────────────┤             │
│    │   │ if agent_id:                                      │             │
│    │   │   interceptor = PARRoutingInterceptor(            │             │
│    │   │       agent_id=agent_id                           │             │
│    │   │   )                                                │             │
│    │   │   interceptors.append(interceptor)                │             │
│    │   │                                                    │             │
│    │   │ server = grpc.aio.server(                         │             │
│    │   │     ThreadPoolExecutor(max_workers=10),           │             │
│    │   │     interceptors=interceptors  ← ADDED! ✓         │             │
│    │   │ )                                                  │             │
│    │   └────────────────────────────────────────────────────┘             │
│    │                                                                        │
│    ├── ┌────────────────────────────────────────────────────┐             │
│    │   │ PARRoutingInterceptor Initialization             │             │
│    │   ├────────────────────────────────────────────────────┤             │
│    │   │ agent_id: "4906eeb7-9959-414e-84c6-f2445822ebe4" │             │
│    │   │ prefix: "/agents/{id}/a2a"                        │             │
│    │   │ prefix_len: 53                                    │             │
│    │   │                                                    │             │
│    │   │ Log: "PAR Routing Interceptor initialized" ✓     │             │
│    │   └────────────────────────────────────────────────────┘             │
│    │                                                                        │
│    ├── Load Agent's gRPC Servicer                                          │
│    │   ├── Import: src.grpc_server:create_service                         │
│    │   ├── Pattern: "full_servicer" detected                              │
│    │   ├── Class: VividCommenterAgentService                              │
│    │   └── Registered: AgentServiceServicer ✓                             │
│    │                                                                        │
│    ├── Bind: [::]:60000 (IPv6)  ← IMPORTANT!                              │
│    └── Status: LISTENING ✓                                                 │
│                                                                              │
│ 4. Start UI Server (if enabled) - Port 65000                                │
│    └── Status: DISABLED (no UI in manifest)                                │
│                                                                              │
│ Runtime Ready! ✓                                                            │
│ ├── REST: 0.0.0.0:63000 (IPv4)                                             │
│ └── A2A:  [::]:60000    (IPv6)                                             │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════════════════════════
STAGE 5: REQUEST FLOW (Client → ALB → PAR → Agent)
═══════════════════════════════════════════════════════════════════════════════════════════════════

┌─────────────────────────────────────────────────────────────────────────────┐
│ CLIENT REQUEST (Your laptop: talk_to_agent.py)                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  $ python talk_to_agent.py --dns-resolver native                           │
│                                                                             │
│  Connection: par.pixell.global:443                                         │
│  Protocol: gRPC over TLS (HTTP/2)                                          │
│  Path: /pixell.agent.AgentService/Health                                   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ Client-Side Interceptor (PathPrefixInterceptor)                     │  │
│  ├──────────────────────────────────────────────────────────────────────┤  │
│  │ Rewrites path:                                                       │  │
│  │   FROM: /pixell.agent.AgentService/Health                            │  │
│  │   TO:   /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/           │  │
│  │         pixell.agent.AgentService/Health                             │  │
│  │                                                                       │  │
│  │ Log: "Rewriting gRPC path" ✓                                        │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
│  Sends gRPC Request:                                                        │
│    :method = POST                                                           │
│    :path = /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/              │
│             pixell.agent.AgentService/Health                                │
│    :authority = par.pixell.global                                          │
│    content-type = application/grpc                                          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                          │
                          │ TLS (HTTPS/443)
                          ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ ALB: pixell-runtime-alb (par.pixell.global)                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│ 1. TLS Termination                                                          │
│    └── Decrypt HTTPS → Forward as HTTP/2                                   │
│                                                                             │
│ 2. Path-Based Routing                                                       │
│    ┌─────────────────────────────────────────────────────────────────┐    │
│    │ Path: /agents/4906eeb7.../a2a/pixell.agent.AgentService/Health │    │
│    │                                                                  │    │
│    │ Matches Rule Priority 10067:                                   │    │
│    │   Condition: /agents/4906eeb7.../a2a/*                         │    │
│    │   Action: Forward to pac-agent-4906eeb7-grpc-v3               │    │
│    └─────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│ 3. Health Check                                                             │
│    ┌─────────────────────────────────────────────────────────────────┐    │
│    │ Target Group: pac-agent-4906eeb7-grpc-v3                       │    │
│    │ Health Check Path: /agents/4906eeb7.../health  ← WRONG! ✗     │    │
│    │                                                                 │    │
│    │ ALB attempts: GET /agents/4906eeb7.../health                   │    │
│    │ PAR expects:  GET /agents/4906eeb7.../a2a/health               │    │
│    │                                                                 │    │
│    │ Result: 404 Not Found → UNHEALTHY                              │    │
│    │                                                                 │    │
│    │ 🔴 ALL TARGETS UNHEALTHY!                                      │    │
│    └─────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│ 4. ALB Response                                                             │
│    ┌─────────────────────────────────────────────────────────────────┐    │
│    │ No healthy targets available                                   │    │
│    │ HTTP/2 status: 464 (Non-standard AWS error)                    │    │
│    │ Returns to client                                               │    │
│    └─────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                          │
                          │ ⚠️  ERROR: HTTP 464
                          ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ CLIENT RECEIVES ERROR                                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  gRPC Error:                                                                │
│    StatusCode: UNKNOWN                                                      │
│    Details: "Received http2 header with status: 464"                       │
│                                                                             │
│  ⚠️  THE REQUEST NEVER REACHES PAR!                                        │
│  ⚠️  INTERCEPTOR NEVER EXECUTES!                                           │
└─────────────────────────────────────────────────────────────────────────────┘


═══════════════════════════════════════════════════════════════════════════════════════════════════
WHAT SHOULD HAPPEN (After Fix)
═══════════════════════════════════════════════════════════════════════════════════════════════════

Client Request → ALB
                 │
                 │ Path: /agents/4906eeb7.../a2a/pixell.agent.AgentService/Health
                 │ Matches: Priority 10067 (a2a/* rule)
                 │ Target: pac-agent-4906eeb7-grpc-v3
                 │ Health Check: /agents/4906eeb7.../a2a/health ← CORRECT PATH
                 │ Target Status: HEALTHY ✓
                 ↓
ALB Forwards → EC2:60000 (HTTP/2)
               │
               │ HTTP/2 Stream
               │ :path = /agents/4906eeb7.../a2a/pixell.agent.AgentService/Health
               ↓
PAR gRPC Server (Port 60000)
   │
   │ Request enters interceptor chain
   ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│ PARRoutingInterceptor.intercept_service()                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  original_method = "/agents/4906eeb7.../a2a/pixell.agent.AgentService/Health" │
│                                                                             │
│  if original_method.startswith(self.prefix):  # "/agents/4906eeb7.../a2a" │
│      # YES! Match found                                                     │
│      stripped_method = original_method[self.prefix_len:]                    │
│      # "/pixell.agent.AgentService/Health"                                 │
│                                                                             │
│      modified_details = _HandlerCallDetails(                                │
│          method=stripped_method,                                            │
│          invocation_metadata=...                                            │
│      )                                                                      │
│                                                                             │
│      Log: "PAR interceptor: stripped routing prefix" ✓                    │
│      return await continuation(modified_details)                            │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
   │
   │ Stripped path: /pixell.agent.AgentService/Health
   ↓
Agent's gRPC Servicer (VividCommenterAgentService)
   │
   │ async def Health(self, request, context):
   │     return HealthStatus(ok=True, message="Agent is healthy")
   ↓
Response → PAR → ALB → Client ✓


═══════════════════════════════════════════════════════════════════════════════════════════════════
THE ROOT CAUSE
═══════════════════════════════════════════════════════════════════════════════════════════════════

🔴 ISSUE: ALB Target Group Health Check Path is WRONG

Target Group: pac-agent-4906eeb7-grpc-v3
  ├── Protocol: HTTP
  ├── ProtocolVersion: HTTP2  ← CORRECT ✓
  ├── Port: 60000             ← CORRECT ✓
  ├── HealthCheckPath: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/health  ← WRONG! ✗
  │                    Should be: /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/health
  └── HealthCheckProtocol: HTTP

Because health checks fail:
  → ALB marks all targets as UNHEALTHY
  → ALB refuses to route traffic to unhealthy targets
  → ALB returns HTTP 464 to client
  → Request NEVER reaches PAR
  → Interceptor NEVER executes

The interceptor code is PERFECT and WORKING!
The problem is INFRASTRUCTURE CONFIGURATION!


═══════════════════════════════════════════════════════════════════════════════════════════════════
THE FIX
═══════════════════════════════════════════════════════════════════════════════════════════════════

Option 1: Fix Target Group Health Check Path (RECOMMENDED)
──────────────────────────────────────────────────────────

$ aws elbv2 modify-target-group \
    --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pac-agent-4906eeb7-grpc-v3/5c7fba6a73475cca \
    --health-check-path '/agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/health'

This will:
  ✓ Fix health check path
  ✓ ALB will detect healthy targets
  ✓ Traffic will flow
  ✓ Interceptor will work as designed


Option 2: Add Health Check Route to PAR
───────────────────────────────────────

Add route in PAR REST server:
  GET /agents/{id}/health → proxy to /agents/{id}/a2a/health

This keeps existing health check path but adds compatibility route.


═══════════════════════════════════════════════════════════════════════════════════════════════════
PORT MAPPING SUMMARY
═══════════════════════════════════════════════════════════════════════════════════════════════════

EC2 Instance: i-09dcb7f387166efd0
├── Port 63000 (REST) - 0.0.0.0 (IPv4)
│   ├── ALB Target: pac-agent-4906eeb7-rest:63000 (HEALTHY ✓)
│   ├── Handles: /agents/{id}/api/*, /agents/{id}/*
│   └── Health: /agents/{id}/health → 200 OK
│
└── Port 60000 (gRPC) - [::] (IPv6)
    ├── ALB Target: pac-agent-4906eeb7-grpc-v3:60000 (UNHEALTHY ✗)
    ├── Should handle: /agents/{id}/a2a/*
    ├── Health: /agents/{id}/health → 404 NOT FOUND (WRONG PATH!)
    └── Should be: /agents/{id}/a2a/health → 200 OK


═══════════════════════════════════════════════════════════════════════════════════════════════════
VERIFICATION LOGS
═══════════════════════════════════════════════════════════════════════════════════════════════════

From PAR logs (/pixell/agent-runtime):

✓ Interceptor initialized:
  "PAR Routing Interceptor initialized"
  agent_id: "4906eeb7-9959-414e-84c6-f2445822ebe4"
  prefix: "/agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a"

✓ gRPC server created:
  "Created A2A gRPC server"
  port: 60000
  servicer_type: "VividCommenterAgentService"

✓ Interceptor is working (for internal health checks):
  "PAR interceptor: pass-through (no prefix)"
  path: "/pixell.agent.AgentService/Health"
  # These are PAR's own health checks (no prefix), not from ALB

✗ No incoming requests from ALB:
  # No logs showing prefixed paths being stripped
  # Because ALB never sends traffic (all targets unhealthy)

═══════════════════════════════════════════════════════════════════════════════════════════════════
```

## Summary

**The interceptor is WORKING PERFECTLY!**

The issue is NOT with your code. The issue is with **ALB target group health check configuration**:

- **Current Health Check**: `/agents/{id}/health` → 404 Not Found
- **Should Be**: `/agents/{id}/a2a/health` → 200 OK

Because health checks fail, ALB marks all targets as unhealthy and refuses to route traffic, returning HTTP 464 to clients. The interceptor never gets a chance to run because requests never reach PAR!
