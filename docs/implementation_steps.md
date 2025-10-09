## PAR Implementation Steps (Runtime Only)

Goal: Implement only what PAR (the Agent Runtime) is responsible for to realize the runtime side of the migration design. PAR is a pure executable environment inside each ECS task/container. It does not create/update ECS, Cloud Map, ALB/NLB, or any DB rows.

Authoritative references
- Migration intent: `docs/migration_system_design.md` (runtime focus)
- End-to-end expectations: `../pixell-agent-cloud/docs/request_response_trace.md` (PAR responsibilities section and sequence notes)

Runtime contract (must-haves)
- Env inputs: `AGENT_APP_ID` (primary identity), optional `DEPLOYMENT_ID` (trace/correlation), `REST_PORT=8080`, `A2A_PORT=50051`, `UI_PORT=3000`, `AWS_REGION=us-east-2`, `S3_BUCKET=pixell-agent-packages`, optional `BASE_PATH=/agents/{AGENT_APP_ID}`, `PACKAGE_URL` (s3:// or https://)
- Ports bound on 0.0.0.0: REST 8080, A2A 50051, UI 3000
- Health endpoints: REST `/health` returns 200 when ready; optional gRPC health; otherwise rely on TCP health
- Boot sequence: create isolated Python venv → fetch APKG → extract/install → load handlers → start surfaces → mark ready
- No DB, no SD/LB work, no ECS API calls

---

### Step 0: Config parsing and base path normalization
Implement a small config module that:
- Reads env variables; validates required ones; applies defaults for ports; normalizes `BASE_PATH`.
- Emits clear startup errors when required env is missing.

Guardrails (prevent common mistakes)
- Hard-fail if `AGENT_APP_ID` missing or empty; never synthesize it from other values
- Disallow dynamic/random ports; only accept numeric ports and default to 8080/50051/3000
- Normalize `BASE_PATH` to `/agents/{AGENT_APP_ID}` or `/` with no trailing slash (except root)

Test
- Unit: parse env with/without `BASE_PATH`; verify normalization to `/agents/{AGENT_APP_ID}` or `/` with no duplicate/trailing slashes.
- Unit: missing `AGENT_APP_ID` or ports invalid → process exits non-zero; error message logged.
- Negative: attempt to set `REST_PORT=0` or non-numeric → exit with clear error

---

### Step 1: REST skeleton with health gate
Create a FastAPI (or equivalent) server that:
- Binds to `REST_PORT` on 0.0.0.0.
- Exposes `/health` which returns 503 until `runtime_ready=True`, then 200.
- Supports mounting all agent routes under `BASE_PATH` (prefix) and also `/`-prefixed if needed by infra tests.

Guardrails
- Always bind on `0.0.0.0`; fail if binding fails (do not silently choose a random port)
- Health 200 only after readiness; never return 200 before handlers are mounted
- Route prefixing applies exactly once; forbid double prefixing

Test
- Integration: start container with `RUNTIME_READY=false` (default). `/health` returns 503, then flipping the flag returns 200.
- Unit: confirm prefixing correctness: requests to `{BASE_PATH}/api/...` are routed; root remains clean.
- Negative: assert `/health` not 200 before readiness; `{BASE_PATH}{BASE_PATH}/api/...` 404 (no double prefix)

---

### Step 2: APKG downloader (s3:// and https://)
Implement a downloader that:
- Supports `s3://bucket/key` via boto3 and `https://...` via httpx.
- Retries with exponential backoff; size/time limits; optional sha256 validation when provided.

Guardrails
- Only allow S3 GetObject and HTTPS GET; do not perform any other cloud operations
- Enforce a max package size and a total download timeout; abort with clear logs

Test
- Unit: mock S3 to return a small zip; verify retries on transient errors.
- Integration: serve a local HTTPS file; download, verify size and optional sha256.
- Negative: simulate S3 403/404 and timeout; verify bounded retries and fail-fast with clear error

---

### Step 3: APKG extractor and manifest detection
Implement:
- Zip extraction to a temp directory.
- Manifest detection (e.g., `agent.yaml` or equivalent) and validation (required fields for handlers/UI config).
- Determine install path: `setup.py`/`pyproject` preferred; fallback to `requirements.txt`.

Guardrails
- Refuse to proceed if required manifest fields (handlers, version) are missing
- Sanitize extraction path to prevent zip-slip

Test
- Unit: zips containing only `setup.py` install metadata → recognized.
- Unit: zips containing only `requirements.txt` → recognized.
- Unit: missing manifest → boot fails with a clear error; process exits non-zero.
- Negative: crafted zip with path traversal → detection and safe abort

---

### Step 4: Venv creation and dependency installation
Implement:
- Create a Python virtual environment per container instance (e.g., `/opt/agent-venv`).
- Install via `pip` from `setup.py`/`pyproject` (editable or wheel) or from `requirements.txt`.
- Optional: support a wheelhouse cache directory to reduce cold start errors/time.

Guardrails
- Install strictly into the venv; never into system Python
- Cap install time; on timeout, log and exit non-zero

Test
- Integration: run install for a tiny sample agent; verify packages end up in the venv; `python -c 'import agent'` succeeds inside venv.
- Failure path: simulate bad requirements; ensure the runtime logs errors and exits (health remains unhealthy).
- Negative: verify system site-packages remain unchanged; timeout path triggers controlled exit

---

### Step 5: Handler loading and route mounting
Implement:
- Discover REST, A2A, and UI handlers from the installed agent package per manifest.
- Mount REST routes under `{BASE_PATH}`.
- Serve static UI (if present) on `UI_PORT` or via REST server under `{BASE_PATH}/ui` when multiplexing.
- Start a gRPC server on `A2A_PORT` with the agent’s service implementation.

Guardrails
- Validate handlers exist before starting servers; if missing, keep health=503 and exit after grace period
- Bind gRPC on `0.0.0.0:A2A_PORT`; never attempt ALB/NLB/SD updates here

Test
- Integration: sample agent with REST `GET /api/ping` → returns JSON; verify path works under `{BASE_PATH}/api/ping`.
- Integration: sample gRPC service with unary method → client receives expected response on `localhost:50051`.
- Integration: UI folder with `index.html` accessible at `{BASE_PATH}/ui/` when present.
- Negative: missing handler → health stays 503, logs clear error, process exits non-zero

---

### Step 6: Health readiness and lifecycle
Implement:
- Flip `runtime_ready=True` only when REST/gRPC/UI are fully initialized.
- REST `/health` returns 200 only when `runtime_ready=True`; otherwise 503.
- Optional: implement gRPC health service; otherwise rely on TCP health.

Guardrails
- Do not mark ready until both REST and (if enabled) A2A are listening
- Ensure readiness can flip back to 503 if a critical background task fails early

Test
- Integration: verify `/health` transitions to 200 after handlers load.
- Integration: NLB/TCP check (simulated) sees port 50051 open only after service starts.
- Negative: simulate A2A init failure; `/health` remains 503 and process exits non-zero

---

### Step 7: Logging and observability inside the runtime
Implement:
- Structured logs (JSON) for: config read, download, extract, install, load, serve, health state changes.
- Include `AGENT_APP_ID`, `DEPLOYMENT_ID` in each log line for correlation.

Guardrails
- Redact secrets; never log tokens or credentials
- Enforce a consistent schema (level, ts, agentAppId, deploymentId, event, details)

Test
- Unit: verify log formatting and presence of correlation fields.
- Integration: capture container logs; confirm expected sequence markers (download → extract → install → load → ready).
- Negative: run with verbose mode; ensure no sensitive envs are printed

---

### Step 8: Failure handling and exit policy
Implement:
- Retries for download; bounded backoff.
- Fail-fast on unrecoverable: invalid APKG/manifest/install; exit non-zero so ECS restarts.
- Never attempt to update SD/LB/DB from the container.

Guardrails
- Static code checks to ensure no imports of ECS/ELB/ServiceDiscovery/DB client SDKs in runtime package
- Global circuit for repeated boot failures: sleep/backoff before exit to avoid hot-restart loops

Test
- Integration: corrupt zip triggers exit with clear error; `/health` remains 503 until exit.
- Integration: S3 403 triggers retry then exit; logs include error chain.
- Negative: scan runtime code for forbidden imports; CI fails if detected

---

### Step 9: Performance baseline
Implement:
- Simple startup timer; log total boot time.
- Optional: soft budget (e.g., <= N seconds for small APKGs) to alert on regressions.

Guardrails
- Emit a warning if boot exceeds budget; include phases (download, extract, install, load)

Test
- Integration: record boot time for sample agents; compare to threshold; log warning if exceeded.

---

### Step 10: Conformance checklist vs request_response_trace.md
Confirm that PAR behavior matches the doc:
- Env contract respected: `AGENT_APP_ID`, `DEPLOYMENT_ID`, ports, `BASE_PATH`, `PACKAGE_URL`, `AWS_REGION`, `S3_BUCKET`.
- Ports bound on 0.0.0.0: 8080 (REST), 50051 (A2A), 3000 (UI).
- REST `/health` and readiness gating implemented; optional gRPC health or TCP health assumed.
- APKG loader: fetch → extract → install → load handlers → serve.
- No DB/SD/LB/ECS interactions from the container.

Test
- End-to-end (local): run runtime container with a sample APKG and env; verify REST/UI/A2A surfaces respond as expected; ensure no cloud/AWS calls beyond S3 GetObject (if used).
- Negative: run with instrumentation capturing outbound connections; assert no calls to ECS/ELB/ServiceDiscovery endpoints

---

Notes and exclusions
- Service discovery, ALB/NLB rules, ECS services, and all database writes are explicitly PAC/infrastructure-owned and out of scope here.
- The runtime should not assume or attempt any Cloud Map registration or target group attachment.


