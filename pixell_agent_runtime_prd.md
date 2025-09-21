# Pixell‑Agent‑Runtime – Product Requirements Document

**Version 0.1 – July 2025**\
Authors: Pixell Core Team

---

## 1 Purpose & Scope

The **Pixell‑Agent‑Runtime** ("PAR") is a lightweight **hosting layer** that discovers, mounts, and serves multiple *Agent Packages (APKGs)* produced by **Pixell Agent Kit (PAK)**. It exposes each exported sub‑agent as a stable HTTP endpoint (`/agents/{id}/invoke`) for consumption by:

- **PAF‑Core** – Headless orchestrator that coordinates multi‑agent workflows.
- **PAF (UI)** – React dashboard that lets business users run agents and view results.

> **Goal:** Provide a zero‑ops runtime that makes agents hot‑pluggable, version‑safe, and observable, so a 2‑person team can look like a platform company.

### In Scope (v1)

- Package discovery & pull (S3/HTTPS registry).
- APKG validation & signature check.
- Dynamic mount/unmount of exported sub‑agents.
- Internal vs. external call routing (in‑proc vs. REST).
- Basic usage metering & Prometheus metrics.
- Webhooks to notify PAF‑Core of package changes.
- Single‑node deployment (FastAPI + Uvicorn + Gunicorn).

### Out of Scope (v1)

- Horizontal autoscaling & multi‑tenancy (deferred to v1.2).
- UI components (provided by PAF).
- Long‑running orchestration (handled by PAF‑Core or the agent engines themselves).

---

## 2 Glossary

| Acronym      | Meaning                                                                 |
| ------------ | ----------------------------------------------------------------------- |
| **PAF**      | *Pixell Agent Framework* – React front‑end (dashboard, auth, settings). |
| **PAF‑Core** | Headless orchestrator invoked via REST/WebSocket by PAF UI.             |
| **PAK**      | *Pixell Agent Kit* – CLI / tooling that builds APKG files.              |
| **APKG**     | *Agent Package* – zipped artifact with `agent.yaml`, code, deps.        |
| **PAR**      | *Pixell‑Agent‑Runtime* – this runtime/host.                             |

---

## 3 Architecture Relationship

```mermaid
flowchart TD
  subgraph DevLaptop
    A[Agent source code] -->|"pak build"| B(APKG)
  end
  B -->|upload via CI| S3Registry[(APKG Registry)]
  S3Registry -->|lazy pull / webhook| PAR
  PAR -->|REST /agents/{id}/invoke| PAF-Core
  PAF-Core -->|Graph orchestration| PAR
  PAF-Core -->|WS/REST| PAF-UI
  PAF-UI -->|UX & auth| EndUser
```

**Narrative** 1. Developer packages agent → APKG in registry. 2. PAR pulls & mounts exports; exposes each as HTTP & local callable. 3. PAF‑Core composes workflows by hitting `/agents/*` endpoints. 4. PAF UI shows logs, metrics, and lets users trigger runs.

---

## 4 Detailed Functional Requirements

### 4.1 Package Discovery & Mounting

| #     | Requirement                                                       | Priority |
| ----- | ----------------------------------------------------------------- | -------- |
|  D‑01 | Support *pull list* via env `PACKAGES_URLS` (comma‑sep).          | Must     |
|  D‑02 | Support *registry index* (JSON in S3) with polling interval.      | Must     |
|  D‑03 | Verify SHA‑256 & optional GPG signature on download.              | Must     |
|  D‑04 | Reject package if `agent.yaml` incompatible with runtime version. | Must     |
|  D‑05 | Hot‑reload new versions without container restart (graceful).     | Should   |

### 4.2 Routing & Invocation

| #     | Requirement                                                            | Priority |
| ----- | ---------------------------------------------------------------------- | -------- |
|  R‑01 | Map each `exports.id` to `/agents/{id}/invoke` (POST JSON).            | Must     |
|  R‑02 | Provide in‑proc call: `runtime.call(agent_id, **kwargs)` for PAF‑Core. | Must     |
|  R‑03 | Distinguish *private* sub‑agents (not routable).                       | Must     |
|  R‑04 | Support file uploads via `multipart/form‑data`.                        | Should   |
|  R‑05 | Return JSON Schema validation errors with 4xx.                         | Should   |

### 4.3 Security & Auth

|  #    | Requirement                                           | Priority |
| ----- | ----------------------------------------------------- | -------- |
|  S‑01 | OIDC bearer token auth (reuse PAF ID tokens).         | Must     |
|  S‑02 | Per‑agent RBAC (`agent.yaml.role_required`).          | Should   |
|  S‑03 | TLS termination via ALB or self‑signed for local dev. | Must     |

### 4.4 Usage Metering & Observability

|  #    | Requirement                                                     | Priority |
| ----- | --------------------------------------------------------------- | -------- |
|  M‑01 | Middleware counts requests & token usage (if LLM call wrapped). | Must     |
|  M‑02 | Expose Prometheus `/metrics` for ops dashboard.                 | Must     |
|  M‑03 | Structured JSON logs (agent\_id, version, latency, status).     | Must     |
|  M‑04 | Push daily usage to Stripe metering API (if `STRIPE_KEY`).      | Should   |

### 4.5 Management API

|  #                                  | Endpoint                         | Purpose |
| ----------------------------------- | -------------------------------- | ------- |
|  `GET /runtime/health`              | Liveness probe.                  |         |
|  `GET /runtime/packages`            | List mounted APKGs (+ versions). |         |
|  `POST /runtime/reload`             | Force reload from registry.      |         |
|  `POST /runtime/agents/{id}/enable` | Toggle agent availability.       |         |
|  `POST /runtime/shutdown`           | Graceful stop (ops only).        |         |

### 4.6 Extensibility Hooks

- **Loader plugins** – future language runtimes (Node, Rust).
- **Metrics adapters** – StatsD, Datadog.
- **Auth adapters** – custom JWT provider.

---

## 5 Non‑Functional Requirements

| Area             | Target                                                                                  |
| ---------------- | --------------------------------------------------------------------------------------- |
| **Performance**  | p95 latency < 150 ms for intra‑proc call; < 300 ms for network call under 100 req/s.    |
| **Scalability**  | Single instance handles 500 concurrent invocations; v1.2 introduces horizontal scaling. |
| **Availability** | 99.5 % uptime SLA (single‑region).                                                      |
| **Security**     | All dependencies `pip‑audit` clean; SOC‑2 roadmap.                                      |
| **DX**           | Cold start (package pull + mount) < 5 s for 30 MB APKG.                                 |

---

## 6 Deployment & Ops

- **Container image** (<150 MB) published to GHCR.
- **Helm chart** (v1.1) for optional Kubernetes deploy.
- Default store: EBS + S3; logs to CloudWatch.
- Lifecycle hooks: SIGTERM → stop accepting, finish inflight, shutdown.

---

## 7 Milestones & Timeline

| Date            | Milestone                                                     |
| --------------- | ------------------------------------------------------------- |
|  **Aug 23 ’25** | PAR v0.1 – package discovery, mount, `/agents/*`, Prometheus. |
|  Sep 13 ’25     | v0.2 – Webhooks, management API, hot‑reload.                  |
|  Oct 04 ’25     | v0.3 – Stripe usage, Helm chart, OIDC RBAC.                   |
|  Nov 01 ’25     | v1.0 GA – Multi‑tenant scaling, SLA 99.5 %, SOC‑2 kickoff.    |

---

## 8 Success Metrics

- **<10 min** from pushing APKG to seeing agent live in PAF UI.
- **>20 distinct agents** mounted in staging within first month.
- **<0.2 \$ per 1 K invocations** hosting cost at 100 RPM.

---

## 9 Risks & Mitigations

| Risk                              | Likelihood | Impact | Mitigation                                       |
| --------------------------------- | ---------- | ------ | ------------------------------------------------ |
| Registry outage blocks startup    | Medium     | High   | Local cache & exponential back‑off.              |
| Bad APKG crashes runtime          | Medium     | High   | Sandbox mount; isolate import errors.            |
| Token leakage in logs             | Low        | High   | Scrub middleware, e2e tests.                     |
| Version conflicts between exports | Low        | Medium | Namespacing (`agent_id@version`) & warning logs. |

---

## 10 Open Questions

1. Do we allow **inline APKG upload** via API to speed up dev loops?
2. Should we bake **OCI image support** to run heavier agents (GPU) inside PAR?
3. How soon do we need **multi‑region fail‑over** for enterprise SLA ≥ 99.9 %?

*© 2025 Pixell Global Inc.*

