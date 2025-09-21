# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Pixell-Runtime (PAR) is a lightweight hosting layer for serving Agent Packages (APKGs). It's a FastAPI-based web service that discovers, mounts, and exposes HTTP endpoints for agent invocation.

## Development Commands

Since this is a new project without source code yet, here are the expected commands once implementation begins:

```bash
# Install dependencies (once pyproject.toml or requirements.txt exists)
pip install -e .  # or: pip install -r requirements.txt

# Run development server
uvicorn main:app --reload  # or: python -m pixell_runtime

# Run tests
pytest
pytest tests/test_specific.py::test_name  # Run single test

# Linting and formatting (once configured)
ruff check .
ruff format .

# Type checking (if using mypy)
mypy .
```

## Architecture

### Core Components

1. **Package Discovery**: Pulls APKGs from S3/HTTPS registry, validates SHA-256 and signatures
2. **Agent Mounting**: Dynamically loads and mounts agent exports as HTTP endpoints
3. **Routing Layer**: Maps each agent export to `/agents/{id}/invoke` endpoints
4. **Management API**: Provides runtime control (`/runtime/health`, `/runtime/packages`, etc.)
5. **Observability**: Prometheus metrics, structured JSON logging, usage metering

### Key Design Decisions

- **Single-node deployment** initially (v1), with horizontal scaling planned for v1.2
- **In-process calls** supported via `runtime.call(agent_id, **kwargs)` for PAF-Core
- **Hot-reload capability** for new package versions without restart
- **OIDC authentication** using PAF ID tokens
- **Extensibility hooks** for custom loaders, metrics adapters, auth providers

### Integration Points

- **PAF-Core**: Headless orchestrator that coordinates multi-agent workflows
- **PAF (UI)**: React dashboard for business users
- **APKG Registry**: S3-based storage for agent packages
- **Stripe API**: Usage metering integration (optional)
- **Prometheus**: Metrics endpoint for monitoring

## Important Implementation Notes

- Verify package compatibility via `agent.yaml` before mounting
- Implement graceful shutdown with SIGTERM handling
- Sandbox package imports to prevent runtime crashes
- Support both REST invocations and in-process calls
- Private sub-agents should not be externally routable
- Cold start target: <5s for 30MB APKG
- p95 latency targets: <150ms (in-proc), <300ms (network)