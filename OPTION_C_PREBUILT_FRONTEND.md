# Option C: Pre-built Frontend Architecture

## Overview

Build the React app during `.apkg` creation (on developer's machine), ship the compiled `dist/` directory inside the `.apkg` file. PAR just serves the static files - **no Node.js/npm required on PAR instances**.

---

## How It Works

### 1. Developer Workflow

```bash
# On developer machine
cd vivid-commenter/client/

# Install dependencies
npm install

# Build for production
npm run build
# → Generates client/dist/

# Return to root
cd ..

# Create .apkg with dist/ included
zip -r vivid-commenter.apkg \
    agent.yaml \
    pyproject.toml \
    app/ \
    client/dist/ \        # ← Include compiled frontend
    main.py \
    http_main.py
```

### 2. .apkg Structure

```
vivid-commenter.apkg
├── agent.yaml
├── pyproject.toml
├── app/
│   └── ... (Python backend code)
├── client/
│   └── dist/                    # ← Pre-compiled frontend
│       ├── index.html
│       ├── assets/
│       │   ├── index-a1b2c3d4.js
│       │   └── index-e5f6g7h8.css
│       └── vite.svg
├── main.py
└── http_main.py
```

**Note**: `client/src/`, `package.json`, `node_modules/` are **NOT** included - only the built `dist/` folder.

---

## PAR Structural Changes

### Change 1: No Build Step Needed

**Current Flow** (Option A/B):
```
Extract .apkg → Install npm deps → Build frontend → Serve dist/
```

**Option C Flow**:
```
Extract .apkg → Serve dist/  ✅ (Skip build entirely)
```

**Implementation**: NO changes to `process_manager.py` or `user_manager.py` needed!

---

### Change 2: Agent Serves Static Files

The Python agent's FastAPI app serves the pre-built `dist/` directory.

#### Method 1: Agent Serves Own UI (Recommended)

**File**: `vivid-commenter/http_main.py`

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
import os

app = FastAPI()

# Get dist path from environment
agent_package_path = Path(os.getenv("AGENT_PACKAGE_PATH"))  # /tmp/pixell_packages/{agent_id}/
dist_path = agent_package_path / "client" / "dist"

if dist_path.exists():
    # Mount static files
    app.mount("/ui", StaticFiles(directory=str(dist_path), html=True), name="ui")

    # SPA fallback: all /ui/* routes serve index.html for client-side routing
    @app.get("/ui/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = dist_path / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        # Fallback to index.html for React Router
        return FileResponse(dist_path / "index.html")
else:
    # No UI available
    @app.get("/ui")
    def no_ui():
        return {"error": "This agent has no UI"}

# API routes
app.include_router(reddit_commenter_router, prefix="/api/v1")
```

**URL**: `http://localhost:{UI_PORT}/ui/`

**Pros**:
- ✅ Agent owns full lifecycle (REST + A2A + UI)
- ✅ No supervisor changes needed
- ✅ Scales with agent instances
- ✅ Simple ALB routing

**Cons**:
- ⚠️ FastAPI/Uvicorn not optimized for static files (but fine for low traffic)

---

#### Method 2: Supervisor Proxies to Agent's dist/

**File**: `src/pixell_runtime/supervisor/server.py`

```python
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path

@app.get("/agents/{agent_app_id}/ui/{file_path:path}")
async def serve_ui(agent_app_id: str, file_path: str):
    """Serve static files from agent's dist/ directory."""

    # Get agent info
    agent = state.get_agent(agent_app_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    # Get package path
    package_path = agent.package_path  # /tmp/pixell_packages/{agent_id}/
    dist_path = package_path / "client" / "dist"

    if not dist_path.exists():
        raise HTTPException(404, "Agent has no UI")

    # Serve file
    if file_path == "" or file_path == "/":
        file_path = "index.html"

    target_file = dist_path / file_path

    if target_file.is_file():
        return FileResponse(target_file)
    else:
        # SPA fallback for client-side routing
        return FileResponse(dist_path / "index.html")
```

**URL**: `http://supervisor:9000/agents/{agent_app_id}/ui/`

**Pros**:
- ✅ Centralized UI serving
- ✅ Easier ALB routing (single endpoint)
- ✅ Could add caching layer here

**Cons**:
- ⚠️ Supervisor becomes single point of failure for UI
- ⚠️ More supervisor load (all UI traffic goes through it)

---

### Change 3: Update A2A Message URLs

**File**: `vivid-commenter/app/v1/reddit_commenter/executor/reddit_commenter_executor.py`

```python
import os

async def crawl_posts_with_keyword(..., message_id: str):
    # ... crawl posts logic ...

    # Method 1 URL (Agent serves own UI)
    ui_port = os.getenv("UI_PORT")  # 65000-65199
    ui_url = f"http://localhost:{ui_port}/ui?msg={message_id}"

    # Method 2 URL (Supervisor serves UI)
    # agent_app_id = os.getenv("AGENT_APP_ID")
    # ui_url = f"http://supervisor:9000/agents/{agent_app_id}/ui?msg={message_id}"

    a2a_message = new_agent_text_message("Successfully crawled 10 posts")
    a2a_message.metadata = {
        "url": ui_url,
        "title": "Reddit Posts",
    }
    return a2a_message
```

---

### Change 4: PAR Deployment Flow (Minimal Changes)

**File**: `src/pixell_runtime/supervisor/state.py`

```python
async def deploy(self, request: DeployRequest) -> AgentProcess:
    """Deploy an agent."""

    # Step 1: Create Linux user (unchanged)
    home_dir = self.user_manager.create_user(...)

    # Step 2: Allocate ports (unchanged)
    ports = request.ports or self.port_allocator.allocate(agent_app_id)

    # Step 3: Download & extract package (unchanged)
    package_path = await self.package_downloader.download(
        request.package_url,
        request.package_sha256,
    )

    # Step 4: Check if UI exists (NEW - optional validation)
    dist_path = package_path / "client" / "dist"
    has_ui = dist_path.exists()
    logger.info(f"Agent {agent_app_id} has UI: {has_ui}")

    # Step 5: Spawn agent process (unchanged)
    env = request.env.copy()
    env["HAS_UI"] = str(has_ui)  # Optional: inform agent

    pid = self.process_manager.spawn_agent(
        agent_app_id=agent_app_id,
        linux_user=username,
        package_path=package_path,
        ports=ports,
        env=env,
    )

    return AgentProcess(...)
```

**That's it!** No build step, no npm installation.

---

## ALB Routing Configuration

### Method 1: Route to Agent UI Port

```yaml
# ALB Target Group
TargetGroup:
  Port: 65000-65199  # UI_PORT range
  Protocol: HTTP
  HealthCheck:
    Path: /ui/
    Interval: 30

# Routing Rule
Rule:
  Condition:
    PathPattern: /agents/*/ui/*
  Action:
    Forward: UITargetGroup
```

**URL Flow**:
```
User Request:
  https://pixell.com/agents/vivid-commenter/ui?msg=123

ALB:
  → http://ec2-instance:65000/ui?msg=123

Agent FastAPI:
  → Serves /tmp/pixell_packages/vivid-commenter/client/dist/index.html
```

---

### Method 2: Route to Supervisor

```yaml
# Routing Rule
Rule:
  Condition:
    PathPattern: /agents/*/ui/*
  Action:
    Forward: SupervisorTargetGroup  # Port 9000

# No new target groups needed
```

**URL Flow**:
```
User Request:
  https://pixell.com/agents/vivid-commenter/ui?msg=123

ALB:
  → http://supervisor:9000/agents/vivid-commenter/ui?msg=123

Supervisor:
  → Serves /tmp/pixell_packages/vivid-commenter/client/dist/index.html
```

---

## Environment Variable Injection Problem

### The Challenge

With pre-built `dist/`, how do you handle environment-specific config?

**Example**: API URL changes per environment
- Local dev: `http://localhost:18000`
- Staging: `https://staging-api.pixell.com`
- Production: `https://api.pixell.com`

### Solution 1: Runtime Config Injection (Recommended)

Generate a `config.js` file at deployment time and serve it alongside `dist/`.

#### PAR generates config at deploy time

**File**: `src/pixell_runtime/supervisor/state.py`

```python
async def deploy(self, request: DeployRequest) -> AgentProcess:
    # ... existing code ...

    # Generate runtime config (NEW)
    dist_path = package_path / "client" / "dist"
    if dist_path.exists():
        config_js = f"""
window.APP_CONFIG = {{
    API_URL: "http://localhost:{ports.rest}",
    A2A_URL: "http://localhost:{ports.a2a}",
    AGENT_ID: "{agent_app_id}",
    BASE_PATH: "/agents/{agent_app_id}",
}};
"""
        (dist_path / "config.js").write_text(config_js)

    # ... spawn agent ...
```

#### Frontend loads config

**File**: `vivid-commenter/client/dist/index.html`

```html
<!DOCTYPE html>
<html>
<head>
    <script src="/ui/config.js"></script>  <!-- Load runtime config -->
    <script type="module" src="/ui/assets/index-a1b2c3d4.js"></script>
</head>
<body>
    <div id="root"></div>
</body>
</html>
```

**File**: `vivid-commenter/client/src/services/api.ts`

```typescript
// Read from runtime config injected by PAR
const API_URL = (window as any).APP_CONFIG?.API_URL || 'http://localhost:18000';

export async function fetchPostById(id: string) {
    const res = await fetch(`${API_URL}/api/v1/reddit/${id}`);
    return res.json();
}
```

**Pros**:
- ✅ Single build artifact works across all environments
- ✅ Config injected at deployment time
- ✅ No rebuild needed for config changes

**Cons**:
- ⚠️ Requires modifying index.html to load config.js
- ⚠️ Runtime config exposed in browser (not suitable for secrets)

---

### Solution 2: Build Multiple Versions

Build separate `.apkg` files for each environment.

```bash
# On developer machine

# Build for staging
VITE_API_URL=https://staging-api.pixell.com npm run build
zip -r vivid-commenter-staging.apkg ... client/dist/

# Build for production
VITE_API_URL=https://api.pixell.com npm run build
zip -r vivid-commenter-prod.apkg ... client/dist/
```

**Pros**:
- ✅ True compile-time optimization
- ✅ No runtime config injection needed

**Cons**:
- ❌ Must rebuild .apkg for every environment
- ❌ Cannot deploy same .apkg to multiple environments
- ❌ Slower iteration

---

### Solution 3: Relative URLs (Simplest)

Use relative URLs that work regardless of environment.

**File**: `vivid-commenter/client/src/services/api.ts`

```typescript
// Always use same-origin API
const API_URL = window.location.origin;  // http://localhost:65000

export async function fetchPostById(id: string) {
    // http://localhost:65000/api/v1/reddit/{id}
    const res = await fetch(`${API_URL}/api/v1/reddit/${id}`);
    return res.json();
}
```

**Requirements**:
- UI and API must be served from same origin
- PAR must route both `/ui/*` and `/api/*` to agent's FastAPI server

**Pros**:
- ✅ Works in any environment without config
- ✅ No runtime injection needed
- ✅ Single build artifact

**Cons**:
- ⚠️ Requires UI and API on same server (not suitable for CDN)

---

## Comparison: Option C vs Option B

| Feature | Option C (Pre-built) | Option B (Build on PAR) |
|---------|---------------------|------------------------|
| **Node.js Required** | ❌ No | ✅ Yes |
| **npm Cache Setup** | ❌ No | ✅ Yes |
| **Deploy Time** | ✅ Instant (~5s) | ⚠️ Slow (~2-5 min) |
| **Disk Usage** | ✅ ~10MB (dist/ only) | ⚠️ ~500MB (node_modules + dist) |
| **PAR Complexity** | ✅ Minimal (just serve files) | ⚠️ High (build pipeline) |
| **Runtime Config** | ⚠️ Needs injection (Solution 1/3) | ✅ Build-time injection |
| **Env-Specific Builds** | ❌ Must rebuild .apkg | ✅ Build per environment |
| **Build Failure Risk** | ✅ Fail before .apkg creation | ⚠️ Fail during deployment |
| **Developer Workflow** | ⚠️ Must build before packaging | ✅ Ship source, PAR builds |
| **CDN Support** | ✅ Easy (S3 upload dist/) | ⚠️ Harder (build first) |
| **.apkg Size** | ⚠️ Larger (~5MB dist) | ✅ Smaller (no dist/) |
| **Debugging** | ⚠️ Production build only | ✅ Can enable source maps |

---

## Recommended Approach for Option C

If you choose Option C, I recommend:

### 1. **Agent Serves Own UI** (Method 1)
- Each agent's FastAPI mounts its `dist/` directory
- URL: `http://localhost:{UI_PORT}/ui/`
- ALB routes `/agents/{id}/ui/*` → Agent's UI_PORT

### 2. **Runtime Config Injection** (Solution 1)
- PAR generates `config.js` at deploy time
- Injects ports, agent_id, environment
- Frontend loads via `<script src="/ui/config.js"></script>`

### 3. **Build Script in .apkg Repo**

```bash
# vivid-commenter/scripts/build_apkg.sh
#!/bin/bash

set -e

echo "Building React frontend..."
cd client/
npm ci
npm run build
cd ..

echo "Creating .apkg..."
zip -r vivid-commenter.apkg \
    agent.yaml \
    pyproject.toml \
    requirements.txt \
    app/ \
    client/dist/ \
    main.py \
    http_main.py \
    -x "*.pyc" "__pycache__/*" ".git/*"

echo "✅ vivid-commenter.apkg created"
ls -lh vivid-commenter.apkg
```

---

## PAR Code Changes Summary

### Files to Modify

| File | Change | Complexity |
|------|--------|-----------|
| `supervisor/state.py` | Add `config.js` generation (optional) | 🟢 Low (10 lines) |
| Agent's `http_main.py` | Mount `dist/` as static files | 🟢 Low (15 lines) |
| Agent's executor | Update UI URL in A2A messages | 🟢 Low (2 lines) |

### Files NOT Modified

- ❌ `process_manager.py` (no build step needed)
- ❌ `user_manager.py` (no node_modules directory)
- ❌ `package_downloader.py` (no changes)
- ❌ `port_allocator.py` (no changes)

---

## Example: Complete Flow for vivid-commenter

### 1. Developer Builds .apkg

```bash
cd vivid-commenter/

# Build frontend
cd client/
npm ci
VITE_API_URL=/api npm run build  # Use relative URL
cd ..

# Create .apkg
zip -r vivid-commenter.apkg \
    agent.yaml \
    pyproject.toml \
    app/ \
    client/dist/ \
    main.py \
    http_main.py

# Upload to S3
aws s3 cp vivid-commenter.apkg s3://pixell-agent-packages/vivid-commenter-v1.0.0.apkg
```

### 2. PAR Deploys Agent

```python
# PAC sends deploy request to PAR
DeployRequest(
    agent_app_id="vivid-commenter",
    package_url="s3://pixell-agent-packages/vivid-commenter-v1.0.0.apkg",
    ports=Ports(rest=63000, a2a=60000, ui=65000),
)

# PAR extracts to /tmp/pixell_packages/vivid-commenter/
# → client/dist/index.html exists ✅

# PAR generates config.js (optional)
# /tmp/pixell_packages/vivid-commenter/client/dist/config.js:
#   window.APP_CONFIG = {API_URL: "http://localhost:63000", ...}

# PAR spawns agent with AGENT_PACKAGE_PATH env var
```

### 3. Agent Starts Serving

```python
# http_main.py loads
from pathlib import Path
import os

agent_package_path = Path(os.getenv("AGENT_PACKAGE_PATH"))
dist_path = agent_package_path / "client" / "dist"

app.mount("/ui", StaticFiles(directory=str(dist_path), html=True))
```

### 4. User Accesses UI

```
https://pixell.com/agents/vivid-commenter/ui?msg=123
    ↓ ALB
http://ec2-instance:65000/ui?msg=123
    ↓ FastAPI
/tmp/pixell_packages/vivid-commenter/client/dist/index.html
    ↓ Browser loads
<script src="/ui/config.js"></script>  (runtime config)
<script src="/ui/assets/index-abc123.js"></script>  (React app)
    ↓ React app calls
fetch("http://localhost:63000/api/v1/reddit/123")  (from config.js)
```

---

## When to Choose Option C

**Choose Option C if**:
- ✅ Deploy speed is critical (< 10 seconds)
- ✅ You want to minimize PAR complexity
- ✅ You don't need per-environment builds
- ✅ Your frontend has no build-time secrets
- ✅ You're okay with runtime config injection
- ✅ You want to avoid Node.js dependencies on PAR

**Avoid Option C if**:
- ❌ You need true environment-specific builds (API keys, feature flags)
- ❌ You want to build once, deploy anywhere (contradictory - you want same build for all envs)
- ❌ You need build-time environment variable substitution
- ❌ Your frontend config changes frequently

---

## Next Steps if Choosing Option C

1. **Decide on serving method** (Agent vs Supervisor)
2. **Decide on config strategy** (Runtime injection vs Relative URLs)
3. **Update agent template** (http_main.py with StaticFiles mount)
4. **Create .apkg build script**
5. **Test with vivid-commenter**
6. **Document in agent developer guide**

---

## My Recommendation

I still recommend **Option B** (build on PAR) for long-term flexibility, but **Option C is perfectly viable** if:
- You're okay with a build step in your CI/CD before creating .apkg
- You use relative URLs or runtime config injection
- You prioritize simple PAR architecture over build flexibility

**Hybrid Approach**: Start with Option C for MVP, migrate to Option B later if you need environment-specific builds.
