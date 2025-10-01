"""Main entry point for Pixell Runtime."""

import asyncio
import os
import signal
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict

import structlog
import uvicorn
from fastapi import FastAPI
from prometheus_client import make_asgi_app

from pixell_runtime import __version__
from pixell_runtime.api.health import router as health_router
from pixell_runtime.api.health import health_check as runtime_health_check
from pixell_runtime.api.agents import router as agents_router, init_agent_manager
from pixell_runtime.api.deploy import router as deploy_router, init_deploy_manager
from pixell_runtime.deploy.manager import DeploymentManager
from pixell_runtime.api.middleware import (
    setup_error_handling,
    setup_logging_middleware,
    setup_metrics_middleware,
)
from pixell_runtime.core.config import Settings
from pixell_runtime.utils.logging import setup_logging
from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("Starting Pixell Runtime", version=__version__)

    # Initialize components
    settings = app.state.settings

    # Initialize agent manager and deployment manager
    from pathlib import Path
    packages_dir = Path(settings.package_cache_dir)
    app.state.agent_manager = init_agent_manager(packages_dir)
    logger.info("Agent manager initialized", packages_dir=str(packages_dir))

    # Deployment manager
    app.state.deployment_manager = init_deploy_manager(DeploymentManager(packages_dir))
    logger.info("Deployment manager initialized")

    # Start A2A router in multi-agent mode
    app.state.a2a_router_server = None
    runtime_mode = os.getenv("RUNTIME_MODE", "single")
    if runtime_mode == "multi-agent":
        try:
            from pixell_runtime.a2a.router import create_router_server, start_router_server
            a2a_port = int(os.getenv("A2A_PORT", "50052"))

            # Check if Envoy is present (Envoy has ENVOY_ADMIN_URL set)
            # If Envoy is present, bind to localhost only (internal routing)
            # Otherwise, bind to 0.0.0.0 for external access
            bind_address = "127.0.0.1" if os.getenv("ENVOY_ADMIN_URL") else "0.0.0.0"

            logger.info("Starting A2A router for multi-agent mode", port=a2a_port, bind_address=bind_address)
            app.state.a2a_router_server = create_router_server(
                app.state.deployment_manager,
                port=a2a_port,
                bind_address=bind_address
            )
            await start_router_server(app.state.a2a_router_server)
            logger.info("A2A router started successfully", port=a2a_port, bind_address=bind_address)
        except Exception as e:
            logger.exception("Failed to start A2A router", error=str(e))
            # Don't fail startup if router fails
    else:
        logger.info("Skipping A2A router (not in multi-agent mode)")

    yield

    # Cleanup
    logger.info("Shutting down Pixell Runtime")
    try:
        # Stop A2A router if running
        if hasattr(app.state, "a2a_router_server") and app.state.a2a_router_server:
            from pixell_runtime.a2a.router import stop_router_server
            await stop_router_server(app.state.a2a_router_server)
            logger.info("A2A router stopped")
    except Exception:
        logger.exception("Error stopping A2A router")

    try:
        if hasattr(app.state, "deployment_manager") and app.state.deployment_manager:
            await app.state.deployment_manager.shutdown_all()
    except Exception:
        pass


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create FastAPI application."""
    if settings is None:
        settings = Settings()
    
    # Setup logging
    setup_logging(settings.log_level, settings.log_format)
    
    app = FastAPI(
        title="Pixell Runtime",
        version=__version__,
        description="Lightweight hosting layer for Agent Packages",
        lifespan=lifespan,
    )
    
    # Store settings in app state
    app.state.settings = settings
    
    # Setup middleware
    setup_error_handling(app)
    setup_logging_middleware(app)
    setup_metrics_middleware(app)
    
    # Mount routers
    app.include_router(health_router, prefix="/runtime", tags=["runtime"])
    # Expose agent endpoints at root per external contract
    app.include_router(agents_router, tags=["agents"])  # /agents/*
    # Also mount under /runtime for backward compatibility
    app.include_router(agents_router, prefix="/runtime", tags=["agents"])  # legacy paths
    # Mount deploy endpoints both under / and /runtime for compatibility
    app.include_router(deploy_router, tags=["deploy"])  # provides /deploy and /deployments/{id}/health
    app.include_router(deploy_router, prefix="/runtime", tags=["deploy"])  # legacy paths

    # Provide top-level runtime health alias per contract
    @app.get("/health")
    async def top_level_health():
        return await runtime_health_check()
    
    
    # Mount Prometheus metrics endpoint
    if settings.metrics_enabled:
        metrics_app = make_asgi_app()
        app.mount("/metrics", metrics_app)
    
    return app


def run():
    """Run the application."""
    # Check if we should run in three-surface mode
    package_path = os.getenv("AGENT_PACKAGE_PATH")
    if package_path:
        logger.info("Running in three-surface mode", package_path=package_path)
        runtime = ThreeSurfaceRuntime(package_path)
        asyncio.run(runtime.start())
        return
    
    # Default multi-agent runtime mode
    settings = Settings()
    
    # Handle graceful shutdown
    def handle_sigterm(signum, frame):
        logger.info("Received SIGTERM, initiating graceful shutdown")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, handle_sigterm)
    
    # Configure uvicorn
    config = uvicorn.Config(
        "pixell_runtime.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        workers=settings.workers,
        reload=settings.reload,
        log_config=None,  # We handle logging ourselves
        access_log=False,  # Handled by middleware
    )
    
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    run()