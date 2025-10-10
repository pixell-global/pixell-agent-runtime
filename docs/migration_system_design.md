## PAR Migration System Design: Per-Agent ECS + Cloud Map with MySQL State

### 1) Background and Current Roles

- **PAC (Pixell Agent Cloud)**: Hosts agent apps (source) and exposes APIs used by PAK and operators. Receives APKG uploads and triggers deployments. Publicly reachable as control-plane.
- **PAK (Pixell Agent Kit)**: Builds `.apkg` from agent apps and uploads to S3 via PAC’s API. No runtime responsibilities.
- **PAR (Pixell Agent Runtime)**: Runs APKG bundles. Today it launches agents as subprocesses inside the PAR task (in-memory state). Exposes REST for control and can multiplex agent REST/UI, and launches gRPC A2A ports.
- **Agent apps**: Each APKG contains three surfaces to expose publicly:
  - UI (static assets)
  - REST API (HTTP)
  - A2A (gRPC)

#### Current problems
- Agent processes are subprocesses of PAR; on PAR roll/update, subprocesses die and state is lost.
- Desired/actual state is in-memory only; there is no persistence or reconcile on boot.
- A2A streams are coupled to PAR lifecycle if proxied through it.

#### Migration goal (big picture)
- Decouple execution from orchestration. This document now focuses on the Agent Runtime (the executable environment inside each ECS task/container that serves UI/REST/A2A). The orchestration/controller responsibilities (desired state, MySQL, reconcile to ECS/SD/LB) are moved to PAC and are out of scope here. PAK/PAC interfaces remain stable.

---

### 2) Scope, Objectives, and Non‑Goals (Runtime Only)

#### Objectives (Runtime)
- Provide a stable executable environment for each agent:
  - Consistent ports: REST 8080, A2A 50051, UI 3000
  - Predictable base path behavior for REST/UI when `BASE_PATH` is provided
  - Fast, reliable startup (optionally fetch APKG from S3 at boot)
  - Health endpoints for REST and A2A
  - Clear logs/metrics surfacing agent readiness and errors
- Keep environment variable names stable across components

#### Non‑Goals
- Persistence, desired state, deployment records, and reconcile loops (these live in PAC)
- Changing PAK packaging format or PAC upload API
- Implementing service discovery or ALB/NLB rules (set by PAC/infrastructure)

---

### 2.1) Ownership and Boundaries

- Out of scope (owned by PAC): MySQL schema, deployment tables, reconcile controller, Cloud Map registration, ALB/NLB rule management, ECS Service lifecycle
- In scope (owned by this repo/Runtime): container image(s), process model, ports, env var contract, health checks, base path normalization, APKG load/validation inside the container, logs/metrics

---

### 3) AWS Environment Inventory (runtime-relevant)

All identifiers below are discovered in us-east-2 and MUST be used verbatim in configuration where applicable.

- ECS Cluster: `arn:aws:ecs:us-east-2:636212886452:cluster/pixell-runtime-cluster`
- ECS Services (current):
  - `arn:aws:ecs:us-east-2:636212886452:service/pixell-runtime-cluster/pixell-runtime-multi-agent`
  - `arn:aws:ecs:us-east-2:636212886452:service/pixell-runtime-cluster/pixell-runtime`
- Task Definitions:
  - `pixell-runtime-multi-agent:70`
  - `pixell-runtime:41`
- Cloud Map Namespace: `pixell-runtime.local` (Id: `ns-ipmcpi2q5twajhzm`)
- Cloud Map Services:
  - `agents` (Id: `srv-7dawft5lzgs5ndpg`)
- ALB Target Group (PAR control / REST): `arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/par-multi-agent-tg/c28c15d19accbca4`
- NLB/ALB Target Group (A2A): `arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39`
- S3 Bucket for packages: `pixell-agent-packages`
  (Database details are intentionally excluded; PAC owns persistence.)

Load Balancer Listeners (HTTPS redirect and default forward):

```text
ALB DNS: pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com

Listener :443 HTTPS
- Certificate: arn:aws:acm:us-east-2:636212886452:certificate/27009de7-9e7f-40af-b0f9-2222638f78a5
- Default action: forward → par-multi-agent-tg (HTTP/8080)

Listener :80 HTTP
- Default action: redirect → HTTPS :443
```

Subnets and SGs attached to current services must be reused for new agent services unless explicitly changed and verified:

```text
Subnets (example from ecs describe-services):
- subnet-0b0e8734fc88867f7  (10.0.11.0/24, private, us-east-2b)
- subnet-0a79126c8f2c8f05c  (10.0.1.0/24, public,  us-east-2a)
- subnet-0ba0bc56ff418036e  (10.0.2.0/24, public,  us-east-2b)

Security Groups:
- sg-01fadbe4320c283f7  (pixell-runtime-sg)
  - Ingress: TCP/8080 0.0.0.0/0; TCP/50051 0.0.0.0/0; Egress: all
- sg-063217792cd7a39d9  (pixell-runtime-ecs-sg)
  - Ingress: TCP/8000 from sg-0f5b28ee64419e95d; TCP/9090 from sg-0f5b28ee64419e95d; TCP/50051 from 10.0.0.0/16; Egress: all
```

---

### 4) Environment Variables (Runtime)

The following variables are already in use; values must match real AWS resources above. Do not rename keys.

Runtime container inputs (provided by PAC/infrastructure):
  - `AWS_REGION=us-east-2`
  - `S3_BUCKET=pixell-agent-packages` (if runtime fetches APKG)

Database variables are out of scope for runtime; no DB access from agent containers.

Service discovery is configured by PAC/infrastructure; runtime does not perform registrations.

Agent Service containers will additionally define fixed listen ports:

- `REST_PORT=8080` (container listen)
- `A2A_PORT=50051` (container listen)
- `UI_PORT=3000` (container listen)

Note: Listener/target groups are attached by PAC/infrastructure and must map to these container ports exactly.

---

### 5) Data Model (out of scope for Runtime)

The runtime is an executable environment only. It does not read or write any database.
All persistence, desired state, and reconciliation are owned by PAC. See
`pixell-agent-cloud/docs/pac_deployment_controller.md` for the authoritative
data model and controller responsibilities.

---

### 6) Runtime Changes, Risks, and Implementation

#### A. Execution Model: Subprocess → ECS Task per agent (Runtime focus)
- What changes:
  - PAR stops spawning agents as subprocesses.
  - PAC owns desired state and reconciliation to ECS/Service Discovery/ALB-NLB; the runtime only executes the agent inside the ECS task (no controller logic here).
  - Each agent service uses fixed container ports: REST 8080, A2A 50051, UI 3000.
- Risks/side effects:
  - Misconfigured ports/target groups lead to 5xx or connect timeouts.
  - Incorrect subnets/SGs block public exposure.
  - Task resource sizing (CPU/memory) may throttle agents.
- Implementation notes (runtime responsibilities only):
  - Provide container(s) that start the agent process reliably with ports 8080/50051/3000
  - Accept `BASE_PATH` to mount REST/UI under `/agents/{id}` consistently
  - Load APKG from an injected path or fetch from S3 (if `PACKAGE_URL`/`S3_BUCKET` provided)
  - Expose health endpoints; log startup milestones; fail fast on invalid packages

#### B. State Management
- What changes:
  - Runtime no longer keeps authoritative state; it is stateless beyond local filesystem/cache
- Risks/side effects:
  - Data drift if writes are not transactional.
  - (Out of scope) ECS/SD orchestration mistakes handled by PAC
- Implementation notes:
  - None (handled in PAC)

#### C. Service Discovery & Networking
- What changes:
  - Runtime only listens on container interfaces/ports; external exposure is handled by PAC/infrastructure
  - Container must listen on 0.0.0.0 at:
    - REST: 8080/TCP (`REST_PORT`)
    - A2A:  50051/TCP (`A2A_PORT`)
    - UI:   3000/TCP (`UI_PORT`) if applicable
  - PAC configures ECS Service/Task, ALB/NLB target groups, and Cloud Map; runtime does not manage service discovery or load balancers
- Risks/side effects:
  - None inside runtime if ports are consistent and health endpoints are correct
- Implementation notes:
  - Bind to 0.0.0.0 for each exposed surface; do not attempt any LB/SD registration from the container
  - Ensure REST base path handling matches expectations; serve UI if provided under the base path

#### D. Artifact Management: APKG in S3 (Runtime consumption)
- What changes:
  - Runtime may fetch APKG on startup if `PACKAGE_URL` is provided; otherwise expects baked-in artifacts
- Risks/side effects:
  - Incorrect bucket/key or IAM issues break cold starts.
- Implementation notes:
  - Validate `PACKAGE_URL`/`S3_BUCKET`; fail fast with clear logs

#### E. API Compatibility
- What changes:
  - Runtime exposes the same REST/UI/A2A surfaces; no API contract changes at the agent level
- Risks/side effects:
  - None if ports and base path behavior remain consistent
- Implementation notes:
  - Keep request/response models stable

---

### 7) Implementation Plan (Phased, Runtime Only)

Phase 0 – Preparation
- Finalize runtime container image(s): ensure ports, health, APKG fetch, base path handling
- Verify env var contract: `REST_PORT`, `A2A_PORT`, `UI_PORT`, optional `BASE_PATH`, `PACKAGE_URL`, `S3_BUCKET`, `AWS_REGION`

Phase 1 – Dual-run (no traffic cut)
- Keep legacy subprocess path OFF by default; retain as emergency fallback only
- Create one pilot runtime task via PAC (out of scope here); verify container behavior

Phase 2 – Gradual migration
- Migrate agents in batches; validate runtime health endpoints and logs

Phase 3 – Cutover
- Disable subprocess execution entirely in this repo
- Confirm all agents run as ECS tasks and runtime health is green

Phase 4 – Cleanup
- Remove residual temp extraction paths not needed in container
- Tighten IAM for runtime containers to minimum S3 read (if fetching APKG)

Rollback Plan (Runtime)
- If a new runtime image regresses, switch task definition back to prior image (handled in PAC)

---

### 8) Testing & Verification

Functional
- PAC ➔ write package+deployment rows, PAR reconciles to ECS.
- Agent REST: 200 on `/health` and custom routes via ALB.
- Agent UI: `index.html` reachable under configured base path.
- A2A gRPC: connect, unary + streaming calls succeed via NLB/Cloud Map.

Resilience
- PAR rolling update does not interrupt active A2A to agents.
- ECS service rolling update drains and preserves REST/gRPC via target group deregistration delay.

Security/IAM
- ECS task has S3 read on `pixell-agent-packages/*`; Cloud Map register/deregister; Describe/List ECS.

Data Integrity
- DB rows reflect actual ECS/Cloud Map state; reconcile fixes drift.

Performance
- Baseline P99 latency for A2A through NLB; REST through ALB; compare against pre-migration.

Verification Checklists (must pass)
- Env values match resources:
  - `AWS_REGION=us-east-2`
  - `S3_BUCKET=pixell-agent-packages` exists and contains uploaded APKGs.
  - `SERVICE_DISCOVERY_NAMESPACE=pixell-runtime.local` exists (Id `ns-ipmcpi2q5twajhzm`).
  - `SERVICE_DISCOVERY_SERVICE=agents` exists (Id `srv-7dawft5lzgs5ndpg`).
  - ECS cluster `pixell-runtime-cluster` exists; subnets/SGs match service wiring.
  - Target groups exist and point to correct container ports (8080, 50051, 3000 as applicable).

Smoke Commands (read-only examples)

```bash
# Services
aws ecs describe-services \
  --cluster pixell-runtime-cluster \
  --services pixell-runtime-multi-agent pixell-runtime \
  --region us-east-2

# Cloud Map
aws servicediscovery list-services --region us-east-2
aws servicediscovery list-instances --service-id srv-7dawft5lzgs5ndpg --region us-east-2

# Target groups
aws elbv2 describe-target-groups --names par-multi-agent-tg --region us-east-2
aws elbv2 describe-target-groups --names pixell-runtime-a2a-tg --region us-east-2
```

---

### 9) Component Responsibilities After Migration

- PAK: unchanged. Builds APKG and uploads via PAC. PAC records `packages` row.
- PAC: writes `deployments` rows (desired state), exposes operator APIs. No direct ECS calls.
- PAR (controller): watches DB, reconciles ECS/Cloud Map/LB; updates `agent_services`, `service_endpoints`, and emits `deploy_events`, `health_checks`.
- Agent Services: run isolated; expose REST/UI via ALB and A2A via NLB/Cloud Map.

---

### 10) Open Items and Decisions

- Agent container image strategy:
  - Option A: Generic agent runner image pulls APKG from S3 on start.
  - Option B: CI builds per-agent image embedding APKG. (Option A recommended to keep PAK flow unchanged.)

- Gateway for unified hostnames:
  - If a single hostname is desired, place Envoy in front of ALB/NLB with Cloud Map EDS. Otherwise use separate DNS for REST/UI and A2A.

All of the above avoids introducing any store beyond MySQL and S3.

---

### 11) PAR Reconcile Controller Specification

Goal: Idempotently converge DB desired state to AWS (ECS/Cloud Map/LB) without race conditions. Controller must be stateless; progress persists in MySQL.

Loop cadence: every 10s (configurable). Work units keyed by `deployment_id`.

State machine (per deployment_id):

```text
pending → creating-service → registering-discovery → attaching-target-groups → health-checking → active
pending → creating-service → failed (on unrecoverable error)
active → stopped (if desired_state='stopped') → deleting-service → terminal
```

Idempotency keys
- ECS Service Name: `agent-${deployment_id}` (DNS-safe)
- Cloud Map InstanceId: ECS TaskId (allows multiple healthy tasks during rolling update)
- MySQL rows: one `agent_services` row per deployment_id; re-used on retries

Concurrency
- Lock per deployment_id using `SELECT ... FOR UPDATE` inside a short transaction; release quickly.
- Controller processes up to N deployments in parallel (configurable), backpressure if queue grows.

Operations
1) Read `deployments` where desired_state in ('pending','active','stopped').
2) Ensure `packages` exists; validate S3 path; compute immutable run args (ports, env).
3) Ensure ECS TaskDefinition (shared runner) exists or create on first use:
   - Image: `pixell-agent-runner:stable` (runner pulls APKG from S3 at start), or per-agent image if adopted later
   - Env: `S3_BUCKET`, `AWS_REGION`, `AGENT_APP_ID`, `DEPLOYMENT_ID`, `REST_PORT=8080`, `A2A_PORT=50051`, `UI_PORT=3000`
4) Ensure ECS Service `agent-${deployment_id}` exists (REPLICA desiredCount=1):
   - Cluster: `pixell-runtime-cluster`
   - Subnets: as inventoried; SecurityGroups: allow 8080/50051 as required
   - Service registry: `SERVICE_DISCOVERY_SERVICE=agents` on port 50051
   - Attach target groups: REST → `par-multi-agent-tg` (port 8080), A2A → `pixell-runtime-a2a-tg` (port 50051)
5) Update `agent_services` with ARNs, set status accordingly; write `deploy_events` on transitions.
6) Health validation: record to `health_checks` (REST `/health`, A2A gRPC health) until both ok → set deployment active.
7) For `desired_state='stopped'`: set service desiredCount=0; wait for drain; detach; set status stopped.

Error handling
- Exponential backoff per deployment_id with cap; store last_error in `deploy_events`.
- Hard failure only if resource names conflict with wrong ARNs (env mismatch) → emit clear remediation.

---

### 12) ECS Templates and Naming

Service name: `agent-${deployment_id}`
Task definition family: `pixell-agent-runner`

Container ports
- 8080/TCP (REST)
- 50051/TCP (gRPC A2A)
- 3000/TCP (UI static)

Environment (unchanged names only)
- `AWS_REGION=us-east-2`
- `S3_BUCKET=pixell-agent-packages`
- `AGENT_APP_ID`, `DEPLOYMENT_ID`
- `REST_PORT=8080`, `A2A_PORT=50051`, `UI_PORT=3000`

Networking
- Cluster: `pixell-runtime-cluster`
- Subnets: `subnet-0b0e8734fc88867f7`, `subnet-0a79126c8f2c8f05c`, `subnet-0ba0bc56ff418036e`
- SecurityGroups: `sg-01fadbe4320c283f7` or `sg-063217792cd7a39d9` per exposure pattern

Discovery and LB attachment
- Cloud Map: namespace `pixell-runtime.local`, service `agents (srv-7dawft5lzgs5ndpg)` on 50051
- Target groups:
  - REST: `par-multi-agent-tg` → containerPort 8080
  - A2A:  `pixell-runtime-a2a-tg` → containerPort 50051

---

### 13) Routing and DNS Plan

REST/UI via ALB (HTTPS 443)
- Default forward to `par-multi-agent-tg` (already configured). Add rules to route:
  - `/agents/{deployment_id}/api/*` → agent REST (8080)
  - `/agents/{deployment_id}/ui/*`  → agent UI (3000) via same service if multiplexed, or separate target group if split

gRPC A2A via NLB or Cloud Map
- Prefer direct resolution via Cloud Map `agents` with client using instance filtering (deployment_id) or header-based selection at a gateway (Envoy).
- If using NLB, target group must include agent tasks (50051) and client uses NLB DNS directly.

Consistency checks (must pass)
- Listener 443 forwards to `par-multi-agent-tg`, which expects containerPort 8080.
- Any added ALB rules reference the same TG and preserve health check path.
- No env names are changed; values match inventoried ARNs/IDs.

---

### 14) Execution Steps with Checklists

1) Database
- [ ] Confirm connectivity to RDS with `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`
- [ ] Apply schema (done); verify tables exist

2) Controller deployment
- [ ] Build and deploy PAR controller image (stateless)
- [ ] Grant IAM for ECS, Cloud Map, ELB, S3 read to `S3_BUCKET`

3) Pilot agent
- [ ] Insert `packages` and `deployments` rows (pilot ID)
- [ ] Controller creates ECS service `agent-${deployment_id}`
- [ ] Validate Cloud Map registration and LB health

4) Traffic validation
- [ ] REST `/health` 200 via ALB path
- [ ] UI served under `/agents/{id}/ui/`
- [ ] A2A gRPC unary + stream succeed to 50051

5) Batch migration
- [ ] Repeat for N agents in batches with monitoring

6) Cutover
- [ ] Disable subprocess path
- [ ] Confirm all deployments show `active`

Rollback
- [ ] For a failing `deployment_id`, set `desired_state='stopped'`
- [ ] Re-enable legacy subprocess for that ID only (feature flag) if needed

---

### 15) Detailed Test Cases

Functional
- Create deployment → ECS service exists, TGs attached, Cloud Map instance present
- Delete deployment → service scales to 0 and deregisters

Health/Resilience
- Rolling update of PAR does not affect existing A2A streams
- Rolling update of an agent: ALB/NLB drain works; no 5xx spikes beyond threshold

Security
- SGs allow only required ports from expected CIDRs / SG sources
- IAM: least-privilege for ECS/SD/ELB/S3

Data
- DB reflects state transitions; `deploy_events` recorded; `health_checks` populated periodically

Performance
- P99 latency within agreed SLOs; compare pre/post migration

---

### 16) Operations Runbook

Create deployment (operator via PAC)
1) Upload APKG via PAC → writes `packages`
2) Create `deployments` row with `desired_state='active'`
3) Wait for controller to reconcile to `active`

Investigate unhealthy agent
1) Check `health_checks` and recent `deploy_events`
2) Verify ECS service and task logs; ensure TG health is green
3) If broken, set `desired_state='stopped'`, remediate APKG, redeploy

Scale out a deployment
1) For now keep desiredCount=1 per service (isolation). If future scale, add `replicas` column and adjust controller.

---

### 17) Environment Variables by Component (no renames)

PAC (control-plane API)
- DB: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- AWS: `AWS_REGION=us-east-2`, `S3_BUCKET=pixell-agent-packages`
- Contract to PAK: unchanged (upload APKG to S3 via PAC API)

PAR Controller (stateless reconciler)
- DB: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`
- AWS: `AWS_REGION=us-east-2`, `S3_BUCKET=pixell-agent-packages`
- Discovery: `SERVICE_DISCOVERY_NAMESPACE=pixell-runtime.local`, `SERVICE_DISCOVERY_SERVICE=agents`
- Cluster/Network: cluster name and ARNs are fixed in code/config; subnets/SGs as inventoried

Agent Service (task)
- Ports: `REST_PORT=8080`, `A2A_PORT=50051`, `UI_PORT=3000`
- Identity: `AGENT_APP_ID`, `DEPLOYMENT_ID`
- AWS: `AWS_REGION=us-east-2`, `S3_BUCKET=pixell-agent-packages`

PAK (builder)
- No runtime env changes; continues to build `.apkg` and call PAC API for upload

Validation rule: controller must assert these values match the inventoried AWS resources before taking any action; otherwise emit a `deploy_events` error and skip.

---

