# Overall Architecture: PAC • PAR • PAK • Agent Apps

**PAC (Pixell Agent Cloud)** provisions and orchestrates runtime containers on **AWS ECS Fargate** for each agent app. PAC is responsible for task definitions, port mappings, ALB/Target Group routing, health checks, scaling, and recording runtime health/metadata.

**PAR (Pixell Agent Runtime)** is the container image that boots inside each Fargate task for an agent app. It loads the agent package, starts the serving surfaces, and wires the app’s logic to **three interfaces**:
1) **A2A (gRPC)** – machine-to-agent protocol for orchestration & automation.
2) **REST API** – HTTP/JSON endpoints for programmatic use and dashboards.
3) **UI** – static or server-rendered assets for human interaction.

**PAK (Pixell Agent Kit)** is the developer-facing CLI & templates for building agent app packages. It scaffolds projects, validates `agent.yaml`, builds the bundle (code + UI assets), and runs local dev with ports that match PAR and deploys through PAC.

**Agent Apps** are packaged units (e.g., `.apkg` layout) that expose optional A2A service, REST endpoints, and a UI. The runtime (PAR) auto-wires these based on `agent.yaml` conventions.


---

# PAR Changes — `par_agent_porotocol_integration.md`

> **Prompt to coding AI:** Implement a **three-surface runtime** so any agent package can expose **A2A (gRPC)**, **REST**, and **UI** with minimal code. The runtime should auto-wire surfaces based on `agent.yaml`, honor `REST_PORT`/`A2A_PORT`/`UI_PORT`, and provide consistent health/metrics.

## Why These Changes Are Needed
- Today, PAR boots an agent but does **not** provide a first-class gRPC server, REST router, or UI serving contract. PAC exposes only 8080 by default; agents lack a standard way to accept A2A calls or serve a UI.
- We need a **uniform, opinionated runtime** so developers only implement handlers and ship assets; PAR handles serving, wiring, and health.

## Required Changes

### 1) Configuration Contract — `agent.yaml`
Add/validate the following optional fields:
```yaml
name: my-agent
version: 0.1.0
entrypoint: dist/entry.js        # general bootstrap if needed

a2a:
  service: dist/a2a/server.js    # gRPC server entry (exports createGrpcServer())

rest:
  entry: dist/rest/index.js      # exports mount(app) to attach routes

ui:
  path: dist/ui                  # folder with built static assets (index.html at least)
  basePath: /                    # optional mount path
```

### 2) Ports & Env
- Read env with defaults:
  - `REST_PORT` default `8080`
  - `A2A_PORT` default `50051`
  - `UI_PORT`  default `3000` (only if separate UI server needed)
- **Mode selection:**
  - **Multiplexed (default):** Serve UI from REST server; add `/a2a/*` HTTP/2 shim for gRPC if needed.
  - **Multi-Port:** Run REST on `REST_PORT`, gRPC on `A2A_PORT`, and serve UI on `UI_PORT` (optional).

### 3) A2A (gRPC) Server
- Include `proto/agent.proto` (ship with PAR or as a dependency) defining minimum RPCs:
  - `Health(Empty) returns (HealthStatus)`
  - `DescribeCapabilities(Empty) returns (Capabilities)`
  - `Invoke(ActionRequest) returns (ActionResult)`
  - `Ping(Empty) returns (Pong)`
- Implement `createGrpcServer()` that:
  - Binds to `A2A_PORT`
  - Loads agent implementation if the package overrides/extends handlers
  - Emits structured logs per call (request id, method, latency)

### 4) REST Server
- Use **Express/Fastify** (Node) or **FastAPI** (Python) and bind to `REST_PORT`.
- Mount agent REST routes via `rest.entry` → `mount(app)` function.
- Built-in endpoints:
  - `GET /health` → `{"ok": true, "surfaces": {"rest": true, "a2a": <bool>, "ui": <bool>}}`
  - `GET /meta`   → bundle metadata (name, version, build time)
- Add rate limits and request logging.

### 5) UI Serving
- **Multiplexed:** Serve `ui.path` as static from REST server at `/` (or `basePath`).
- **Separate UI server (optional):** Start a lightweight static server on `UI_PORT` (e.g., `serve-static`).

### 6) Health Probes
- `GET /health` (REST): returns OK if REST is alive.
- `GET /a2a/health` (HTTP shim): pings gRPC service internally and returns HTTP 200 if gRPC is accepting calls.
- Optional: `GET /ui/health` returns 200 if index.html is present and readable.

### 7) Observability & Resilience
- Structured logs (JSON) for each surface.
- Graceful shutdown hooks.
- Prometheus-style `/metrics` (optional), or basic counters.

## Acceptance Criteria
- With **only** `rest.entry`, the agent serves `/api/*` and `/health` on `REST_PORT`.
- With **a2a.service**, gRPC calls succeed on `A2A_PORT` (or `/a2a/*` if multiplexed) and `/a2a/health` returns 200.
- With **ui.path**, static UI is served at `/` (or `basePath`) and is reachable behind ALB.
- Health reflects partial failures (e.g., gRPC down while REST up).

## Testing Plan
- **Unit:** load `agent.yaml` and verify wiring; simulate missing files for good errors.
- **Integration:** run fixture agent containing all three surfaces; curl endpoints + run a gRPC client.
- **E2E:** deploy via PAC; verify ALB endpoints and health recording.

## Rollout
- Ship multiplexed-by-default runtime (single port). Enable multi-port via env flags. Document both.
