# Node.js/npm Environment Architecture for PAR

## Executive Summary

This document proposes an architecture to add Node.js/npm support to Pixell Agent Runtime (PAR), enabling agent apps with React frontends (like vivid-commenter) to build and serve their `client/` directories with proper per-user isolation.

**Goal**: Set up npm/Node environment for EACH agent app under each Linux user without interference, compile React components from `client/`, and serve them via the UI port.

---

## Current Python Architecture (For Reference)

| Component | Location | Isolation Method |
|-----------|----------|------------------|
| Virtual Environment | `~/venv/` | Per-user home directory |
| Package Extraction | `/tmp/pixell_packages/{agent_app_id}/` | Sticky bit (1777) shared dir |
| Package Cache | `/var/lib/pixell/packages/` | Supervisor-owned |
| Logs | `/var/lib/pixell/logs/` | Supervisor-owned |
| Process | Spawned via `subprocess.Popen(user=linux_user)` | Linux user isolation |

**Key Pattern**: Per-user home directories provide isolation, shared temp directories enable caching with sticky bit permissions.

---

## Proposed Architecture: Option A (Recommended)

### **Per-User npm Environment with Local node_modules**

This mirrors the Python venv approach most closely.

### 1. Directory Structure

```
/home/agent_{org_short_id}_{agent_short_id}/
├── venv/                         # Python virtual environment (existing)
├── packages/                     # Extracted .apkg (existing)
│   └── client/                   # React frontend source
│       ├── package.json
│       ├── vite.config.ts
│       └── src/
├── node_modules/                 # npm packages (NEW)
├── .npm/                         # npm cache (NEW)
├── .npmrc                        # npm config (NEW)
├── dist/                         # Compiled frontend assets (NEW)
│   ├── index.html
│   ├── assets/
│   └── ...
├── logs/                         # Agent logs (existing)
└── .cache/                       # General cache (existing)
```

### 2. Implementation Changes

#### A. Extend `user_manager.py` (Lines 250-292)

```python
directories = {
    "venv": home_dir / "venv",
    "packages": home_dir / "packages",
    "logs": home_dir / "logs",
    "cache": home_dir / ".cache",
    # NEW: Node.js directories
    "node_modules": home_dir / "node_modules",
    "npm_cache": home_dir / ".npm",
    "dist": home_dir / "dist",
}
```

#### B. Add to `process_manager.py`

```python
def setup_node_environment(
    self,
    agent_app_id: str,
    linux_user: str,
    home_dir: Path,
    package_path: Path,
) -> Optional[Path]:
    """
    Setup Node.js environment and build React frontend.
    Returns path to dist/ if successful, None if no client/ directory.
    """
    client_path = package_path / "client"

    # Check if agent has a frontend
    if not (client_path / "package.json").exists():
        logger.info(f"No client/ directory found for {agent_app_id}, skipping Node.js setup")
        return None

    node_modules_dir = home_dir / "node_modules"
    npm_cache_dir = home_dir / ".npm"
    dist_dir = home_dir / "dist"

    # Create .npmrc for per-user config
    npmrc_path = home_dir / ".npmrc"
    npmrc_content = f"""
cache={npm_cache_dir}
prefix={home_dir}
"""
    npmrc_path.write_text(npmrc_content)

    # Set ownership
    for path in [node_modules_dir, npm_cache_dir, dist_dir, npmrc_path]:
        subprocess.run(
            ["chown", "-R", f"{linux_user}:{linux_user}", str(path)],
            check=True
        )

    # Install dependencies
    logger.info(f"Installing npm dependencies for {agent_app_id}")
    subprocess.run(
        ["npm", "ci", "--prefer-offline"],
        cwd=client_path,
        user=linux_user,  # Run as agent user
        env={
            "HOME": str(home_dir),
            "NPM_CONFIG_USERCONFIG": str(npmrc_path),
            "PATH": os.environ["PATH"],
        },
        check=True,
        timeout=300,  # 5 minute timeout
    )

    # Build frontend
    logger.info(f"Building React frontend for {agent_app_id}")
    subprocess.run(
        ["npm", "run", "build"],
        cwd=client_path,
        user=linux_user,
        env={
            "HOME": str(home_dir),
            "NPM_CONFIG_USERCONFIG": str(npmrc_path),
            "NODE_ENV": "production",
            "PATH": os.environ["PATH"],
        },
        check=True,
        timeout=600,  # 10 minute timeout for build
    )

    # Copy dist/ to home directory
    client_dist = client_path / "dist"
    if client_dist.exists():
        subprocess.run(
            ["cp", "-r", str(client_dist), str(dist_dir)],
            check=True
        )
        subprocess.run(
            ["chown", "-R", f"{linux_user}:{linux_user}", str(dist_dir)],
            check=True
        )
        return dist_dir
    else:
        raise RuntimeError(f"Build failed: dist/ not found after npm run build")
```

#### C. Update `state.py` Deployment Flow (Lines 322-524)

```python
async def deploy(self, request: DeployRequest) -> AgentProcess:
    # ... existing code ...

    # Step 5 (NEW): Setup Node.js and build frontend if client/ exists
    dist_path = self.process_manager.setup_node_environment(
        agent_app_id=agent_app_id,
        linux_user=username,
        home_dir=home_dir,
        package_path=package_path,
    )

    # Step 6: Spawn agent process with UI path
    env = request.env.copy()
    if dist_path:
        env["UI_DIST_PATH"] = str(dist_path)

    pid = self.process_manager.spawn_agent(
        agent_app_id=agent_app_id,
        linux_user=username,
        package_path=package_path,
        ports=ports,
        env=env,
    )
```

### 3. Serving the Frontend

#### Option A1: PAR Serves Static Files (Recommended)

Add a static file server to the supervisor that proxies to agent's dist/ directory.

**File**: `supervisor/server.py`

```python
from fastapi.staticfiles import StaticFiles

@app.get("/agents/{agent_app_id}/ui/{path:path}")
async def serve_ui(agent_app_id: str, path: str):
    """Serve static frontend files for an agent."""
    agent = state.get_agent(agent_app_id)
    if not agent:
        raise HTTPException(404, "Agent not found")

    dist_path = agent.env.get("UI_DIST_PATH")
    if not dist_path:
        raise HTTPException(404, "Agent has no UI")

    file_path = Path(dist_path) / path
    if not file_path.exists():
        # Fallback to index.html for SPA routing
        file_path = Path(dist_path) / "index.html"

    return FileResponse(file_path)
```

**Agent A2A Response**:
```python
a2a_message.metadata = {
    "url": f"http://supervisor:9000/agents/{agent_app_id}/ui/index.html?msg={message_id}",
    "title": "Reddit Posts",
}
```

#### Option A2: Agent Serves Own Static Files

The Python agent process serves its own dist/ directory via FastAPI.

**File**: Agent's `http_main.py`

```python
from fastapi.staticfiles import StaticFiles
import os

dist_path = os.getenv("UI_DIST_PATH")
if dist_path and Path(dist_path).exists():
    app.mount("/ui", StaticFiles(directory=dist_path, html=True), name="ui")
```

**Agent A2A Response**:
```python
a2a_message.metadata = {
    "url": f"http://localhost:{ports.ui}/ui/index.html?msg={message_id}",
    "title": "Reddit Posts",
}
```

### 4. Pros & Cons

**Pros**:
- ✅ Complete isolation between agents (each has own node_modules)
- ✅ Mirrors existing Python venv pattern
- ✅ No shared state risk
- ✅ Easy to debug (all files in agent's home directory)
- ✅ No need for global npm installation management

**Cons**:
- ❌ Disk space: Each agent has duplicate node_modules (~200-500MB for React apps)
- ❌ Slower deployments: npm install runs per agent
- ❌ No caching between agents

---

## Proposed Architecture: Option B (Alternative)

### **Shared npm Cache with Per-User node_modules**

This adds a global npm cache to improve deployment speed while maintaining isolation.

### 1. Directory Structure

```
# Global npm cache (like /var/lib/pixell/packages/)
/var/lib/pixell/npm_cache/
├── _cacache/
├── registry.npmjs.org/
└── ...

# Per-agent
/home/agent_{org_short_id}_{agent_short_id}/
├── node_modules/         # Per-user, installed from cache
├── .npmrc                # Points to shared cache
├── dist/
└── ...
```

### 2. Implementation Changes

#### Initialize Shared npm Cache (in `state.py` like lines 78-127)

```python
def _initialize_shared_directories(self):
    # ... existing code ...

    # NEW: Global npm cache
    npm_cache_dir = Path("/var/lib/pixell/npm_cache")
    npm_cache_dir.mkdir(parents=True, exist_ok=True)

    # Set permissions: 1777 (world-writable with sticky bit)
    subprocess.run(
        ["chmod", "1777", str(npm_cache_dir)],
        check=True
    )
```

#### Update .npmrc Config

```python
npmrc_content = f"""
cache=/var/lib/pixell/npm_cache
prefix={home_dir}
"""
```

### 3. Pros & Cons

**Pros**:
- ✅ Faster npm installs (shared cache)
- ✅ Less network bandwidth usage
- ✅ Still maintains per-user node_modules isolation

**Cons**:
- ❌ Slightly more complex
- ❌ Potential race conditions if multiple agents install same package simultaneously (npm handles this)
- ❌ Cache invalidation complexity

---

## Proposed Architecture: Option C (Not Recommended)

### **Pre-built Frontend in .apkg**

Build the React app during .apkg creation, ship compiled dist/ in the package.

### 1. Flow

```
Developer Machine:
    cd client/
    npm run build
    # Produces dist/

    cd ..
    # Package includes client/dist/ in .apkg

PAR:
    # Extract .apkg
    # Copy dist/ to agent home directory
    # No npm install, no build step
```

### 2. Pros & Cons

**Pros**:
- ✅ No Node.js required on PAR instances
- ✅ Fastest deployment (no build step)
- ✅ Simplest architecture

**Cons**:
- ❌ Cannot customize build per environment (API URLs, etc.)
- ❌ Larger .apkg files (includes node_modules in dist/)
- ❌ Less flexible (requires rebuild for any frontend change)
- ❌ Breaks agent.yaml manifest transparency (dist/ is opaque)

---

## Comparison Table

| Feature | Option A (Per-User) | Option B (Shared Cache) | Option C (Pre-built) |
|---------|---------------------|-------------------------|----------------------|
| **Isolation** | ✅ Complete | ✅ Complete | ✅ Complete |
| **Disk Usage** | ⚠️ High (~500MB/agent) | ⚠️ High (~500MB/agent) | ✅ Low (~10MB/agent) |
| **Deploy Speed** | ⚠️ Slow (5-10 min) | ✅ Fast (2-3 min) | ✅ Instant |
| **Flexibility** | ✅ Full build control | ✅ Full build control | ❌ No runtime config |
| **Complexity** | ✅ Simple | ⚠️ Moderate | ✅ Simplest |
| **Network Usage** | ⚠️ High | ✅ Low (cached) | ✅ None (pre-built) |
| **Node.js Required** | ✅ Yes | ✅ Yes | ❌ No |
| **Environment Variables** | ✅ Can inject at build time | ✅ Can inject at build time | ❌ Baked in |

---

## Recommended Approach

**Primary Recommendation**: **Option B (Shared npm Cache)**

**Rationale**:
1. Balances isolation (per-user node_modules) with efficiency (shared cache)
2. Allows environment-specific builds (API URLs, feature flags)
3. Follows PAR's existing pattern (shared package cache at `/var/lib/pixell/packages/`)
4. Reasonable disk usage with faster deployments than Option A
5. Maintains transparency in agent.yaml (source code in .apkg, not pre-compiled blobs)

**Fallback**: Option C for agents with infrequent frontend changes and strict deploy time SLAs.

---

## Implementation Phases

### Phase 1: Basic Node.js Setup (MVP)
- [ ] Add node_modules/, .npm/, dist/ to user directories
- [ ] Implement `setup_node_environment()` in process_manager.py
- [ ] Test with vivid-commenter

### Phase 2: Build Integration
- [ ] Integrate npm build into deployment flow (state.py)
- [ ] Add build timeout and error handling
- [ ] Add build logs to agent log file

### Phase 3: Static File Serving
- [ ] Choose serving strategy (A1 vs A2)
- [ ] Implement static file endpoint
- [ ] Update A2A message URL generation

### Phase 4: Optimization
- [ ] Add shared npm cache (Option B)
- [ ] Implement cache warming (pre-populate common packages)
- [ ] Add build artifact caching (skip rebuild if package.json unchanged)

### Phase 5: Production Hardening
- [ ] Add resource limits (build timeout, disk quota)
- [ ] Implement build failure recovery
- [ ] Add metrics (build time, disk usage)

---

## Open Questions & Clarifications Needed

### 1. **Node.js Version Management**
**Question**: Should we support multiple Node.js versions per agent, or standardize on one version?

**Options**:
- A) System-wide Node.js (e.g., `/usr/bin/node` v20 LTS)
- B) Per-agent Node.js via nvm/n (parallel to venv)
- C) Specify Node version in agent.yaml manifest

**Recommendation**: Start with A (system-wide), add B if needed.

---

### 2. **Build Environment Variables**
**Question**: How should agents inject environment-specific config (API URLs, feature flags) into the React build?

**Options**:
- A) Read from .env file in client/ (must be in .apkg)
- B) Inject via PAR environment variables during build (`VITE_API_URL=$REST_PORT`)
- C) Pass via agent.yaml config section

**Example**:
```yaml
# agent.yaml
frontend:
  enabled: true
  framework: vite
  env:
    VITE_API_URL: "http://localhost:${REST_PORT}"
    VITE_A2A_URL: "http://localhost:${A2A_PORT}"
```

**Recommendation**: Option B + C (agent.yaml declares env vars, PAR injects ports at build time).

---

### 3. **Build Caching**
**Question**: Should we cache build artifacts if package.json/package-lock.json haven't changed?

**Scenario**:
- Agent v1.0.0 deployed (Python code changed)
- Client code unchanged (same package.json hash)
- Should we skip `npm run build` and reuse previous dist/?

**Options**:
- A) Always rebuild (safest, slower)
- B) Cache dist/ by package.json + package-lock.json hash (faster, complex)
- C) Let developer decide via agent.yaml: `frontend.cache_builds: true`

**Recommendation**: Start with A, add B as optimization.

---

### 4. **Frontend Framework Flexibility**
**Question**: Should we support non-Vite builds (Next.js, Create React App, custom Webpack)?

**Current**: vivid-commenter uses Vite (`npm run build`)

**Options**:
- A) Hard-code `npm run build` (assumes it's defined in package.json)
- B) Read from agent.yaml: `frontend.build_command: "npm run build"`
- C) Auto-detect (check for next.config.js, vite.config.ts, etc.)

**Recommendation**: Option B (explicit in agent.yaml).

---

### 5. **Static File Serving Strategy**
**Question**: Should PAR supervisor serve UI files, or should each agent serve its own?

**Options**:
- **A1: Supervisor serves** (URL: `http://supervisor:9000/agents/{id}/ui/`)
  - Pros: Centralized, easier ALB routing
  - Cons: Supervisor becomes single point of failure

- **A2: Agent serves** (URL: `http://agent-host:{UI_PORT}/ui/`)
  - Pros: Decentralized, agent owns its full lifecycle
  - Cons: Requires ALB routing to UI_PORT range (65000-65199)

**Current PAR Design**: Supervisor proxies gRPC requests to agents, but REST/UI is agent-owned.

**Recommendation**: A2 (agent serves own UI) to match existing PAR philosophy.

---

### 6. **Build Failure Handling**
**Question**: What should happen if `npm run build` fails during deployment?

**Options**:
- A) Fail deployment (agent not started)
- B) Deploy anyway but mark UI as unavailable
- C) Use previous successful build (if exists)

**Recommendation**: A (fail deployment) for consistency, with optional B for gradual rollout.

---

### 7. **Development Mode**
**Question**: Should agents support running `npm run dev` (Vite dev server) instead of serving static dist/?

**Use Case**: Developer iterates on frontend without redeploying agent.

**Options**:
- A) No, always build static dist/ (production-only)
- B) Yes, if `NODE_ENV=development`, run `npm run dev` as background process
- C) Separate PAR mode: `--dev-mode` enables live reload

**Recommendation**: A (production-only) initially, add B for local development.

---

### 8. **npm Registry & Private Packages**
**Question**: How should agents authenticate to private npm registries (e.g., for `@pixell/agent-ui`)?

**Options**:
- A) Global .npmrc with registry token (security risk)
- B) Per-agent .npmrc with token from environment variable
- C) Use npm_config_* environment variables during npm install

**Example**:
```python
env = {
    "NPM_CONFIG_REGISTRY": "https://registry.npmjs.org/",
    "NPM_CONFIG_//registry.npmjs.org/:_authToken": os.getenv("NPM_TOKEN"),
}
```

**Recommendation**: B + C (per-agent .npmrc + environment variables).

---

### 9. **Package.json Validation**
**Question**: Should PAR validate package.json structure before attempting build?

**Checks**:
- Ensure `scripts.build` exists
- Ensure `dependencies` are declared
- Ensure no malicious scripts (postinstall hooks)

**Recommendation**: Yes, add basic validation (check for `scripts.build`).

---

### 10. **Disk Space Management**
**Question**: Should PAR enforce disk quotas for node_modules/ and dist/?

**Context**:
- Large React apps can have 500MB node_modules
- 200 agents = 100GB disk usage
- EC2 instance may have limited disk

**Options**:
- A) No limits (trust agent developers)
- B) Soft limit (warn if > 1GB per agent)
- C) Hard limit (fail deployment if > 1GB)

**Recommendation**: B initially (monitoring + alerts), C if abuse detected.

---

### 11. **UI Port Allocation**
**Question**: Current UI_PORT range (65000-65199) assumes HTTP server. Does frontend dev server need different ports?

**Current**:
- REST: 63000-63199
- A2A: 60000-60199
- UI: 65000-65199

**With npm run dev**: Vite dev server runs on port 5174 by default (from vite.config.ts).

**Options**:
- A) Override Vite port to use UI_PORT (65000+)
- B) Allocate new port range for dev servers (66000-66199)
- C) Always build static (no dev server)

**Recommendation**: A (override Vite port to match UI_PORT).

---

### 12. **TypeScript Compilation**
**Question**: vivid-commenter runs `tsc -b && vite build`. Should PAR handle TypeScript separately?

**Options**:
- A) No, `npm run build` handles it (abstracts build details)
- B) Yes, run `tsc --noEmit` separately for type checking
- C) Add to agent.yaml: `frontend.check_types: true`

**Recommendation**: A (trust npm run build), add B as optional quality check.

---

### 13. **ALB Routing for UI**
**Question**: How should PAC configure ALB to route to agent UIs?

**Current ALB Routing**:
```
/agents/{agent_app_id}/a2a/* → gRPC Gateway (50051) → Agent A2A Port (60000+)
```

**New UI Routing Options**:
- A) `/agents/{agent_app_id}/ui/*` → Supervisor (9000) → Agent UI Port (65000+)
- B) `/agents/{agent_app_id}/ui/*` → Agent UI Port (65000+) directly
- C) Separate domain: `{agent_app_id}.ui.pixell.com` → Agent UI Port

**Recommendation**: A (via supervisor) for consistency with A2A pattern.

---

## Security Considerations

### 1. **npm Script Execution**
- ⚠️ npm allows arbitrary scripts (postinstall, preinstall)
- 🛡️ Mitigation: Run `npm ci --ignore-scripts` to disable lifecycle scripts
- 🛡️ Mitigation: Validate package.json for malicious scripts before install

### 2. **Build-Time Code Execution**
- ⚠️ Vite plugins can execute arbitrary code during build
- 🛡️ Mitigation: Run build as agent user (not root)
- 🛡️ Mitigation: Enforce build timeout (10 minutes)

### 3. **Shared npm Cache Poisoning**
- ⚠️ Agent A could modify shared cache, affect Agent B
- 🛡️ Mitigation: Use sticky bit (1777) on cache directory (only owner can delete)
- 🛡️ Mitigation: Validate package integrity via package-lock.json

### 4. **Private npm Token Leakage**
- ⚠️ .npmrc contains registry tokens for private packages
- 🛡️ Mitigation: Per-agent .npmrc in home directory (mode 0600)
- 🛡️ Mitigation: Pass tokens via environment variables, not baked into .npmrc

---

## Example: vivid-commenter Deployment Flow

### 1. Package Structure
```
vivid-commenter.apkg (ZIP)
├── agent.yaml
├── pyproject.toml
├── app/
│   └── ... (Python code)
├── client/
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.ts
│   └── src/
│       └── ... (React code)
└── main.py
```

### 2. agent.yaml
```yaml
name: "Vivid Commenter"
version: "1.0.0"
runtime: "python"

frontend:
  enabled: true
  framework: "vite"
  build_command: "npm run build"
  env:
    VITE_API_URL: "http://localhost:${REST_PORT}"
    VITE_API_BASE_PATH: "/api/v1"

skills:
  - id: reddit_crawl_posts_with_keyword
    ui: true  # Indicates this skill returns UI
```

### 3. Deployment Steps (PAR)

```python
# 1. Create user
home_dir = user_manager.create_user("vivid-commenter", "x8f2k9m4n7p1", "a7b2c9d4")
# → /home/agent_x8f2k9m4n7p1_a7b2c9d4/

# 2. Extract package
extract_path = "/tmp/pixell_packages/vivid-commenter/"

# 3. Setup Python venv
subprocess.run(["python3.11", "-m", "venv", f"{home_dir}/venv"])

# 4. Setup Node.js environment (NEW)
client_path = extract_path / "client"
npm_cache = home_dir / ".npm"
node_modules = home_dir / "node_modules"

# Install
subprocess.run(
    ["npm", "ci"],
    cwd=client_path,
    user="agent_x8f2k9m4n7p1_a7b2c9d4",
    env={
        "HOME": str(home_dir),
        "VITE_API_URL": f"http://localhost:63000",  # Inject REST_PORT
    }
)

# Build
subprocess.run(
    ["npm", "run", "build"],
    cwd=client_path,
    user="agent_x8f2k9m4n7p1_a7b2c9d4",
    env={
        "HOME": str(home_dir),
        "NODE_ENV": "production",
    }
)

# Copy dist/
shutil.copytree(client_path / "dist", home_dir / "dist")

# 5. Spawn agent
subprocess.Popen(
    ["/usr/bin/python3.11", "-m", "pixell_runtime"],
    user="agent_x8f2k9m4n7p1_a7b2c9d4",
    env={
        "AGENT_APP_ID": "vivid-commenter",
        "REST_PORT": "63000",
        "A2A_PORT": "60000",
        "UI_PORT": "65000",
        "UI_DIST_PATH": f"{home_dir}/dist",  # (NEW)
    }
)
```

### 4. Agent Serves UI

```python
# vivid-commenter/http_main.py
from fastapi.staticfiles import StaticFiles
import os

app = FastAPI()

# Serve compiled React app
dist_path = os.getenv("UI_DIST_PATH")
if dist_path:
    app.mount("/ui", StaticFiles(directory=dist_path, html=True), name="ui")

# API routes
app.include_router(reddit_commenter_router, prefix="/api/v1")
```

### 5. A2A Response with UI URL

```python
# app/v1/reddit_commenter/executor/reddit_commenter_executor.py
async def crawl_posts_with_keyword(..., message_id: str):
    # ... crawl posts, save to DB with message_id ...

    a2a_message = new_agent_text_message("Successfully crawled 10 posts")
    a2a_message.metadata = {
        # UI accessible at http://agent-host:65000/ui/?msg=msg_123456
        "url": f"http://localhost:{os.getenv('UI_PORT')}/ui/?msg={message_id}",
        "title": "Reddit Posts",
    }
    return a2a_message
```

### 6. User Accesses UI

```
User → ALB (HTTPS:443) → Supervisor (9000) → Agent UI (65000) → /ui/index.html
                                                                    ↓
                                                            React app loads
                                                                    ↓
                                                    Calls /api/v1/reddit/{msg_id}
                                                                    ↓
                                                            Renders table
```

---

## Next Steps

1. **Review this draft** with team
2. **Answer open questions** (especially #1-5)
3. **Prototype Option B** with vivid-commenter
4. **Measure**:
   - Build time (npm ci + npm run build)
   - Disk usage (node_modules + dist)
   - Deploy time impact
5. **Iterate** based on results

---

## References

- PAR Process Manager: `/Users/syum/dev/pixell-agent-runtime/src/pixell_runtime/supervisor/process_manager.py`
- PAR User Manager: `/Users/syum/dev/pixell-agent-runtime/src/pixell_runtime/supervisor/user_manager.py`
- PAR State Manager: `/Users/syum/dev/pixell-agent-runtime/src/pixell_runtime/supervisor/state.py`
- vivid-commenter client: `/Users/syum/dev/vivid-commenter/client/`
- Vite Build Docs: https://vite.dev/guide/build.html
- npm ci Docs: https://docs.npmjs.com/cli/v8/commands/npm-ci
