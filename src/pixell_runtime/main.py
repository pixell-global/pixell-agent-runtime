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
from pixell_runtime.api.agents import router as agents_router, init_agent_manager
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
    
    # Initialize agent manager
    from pathlib import Path
    packages_dir = Path(settings.package_cache_dir)
    app.state.agent_manager = init_agent_manager(packages_dir)
    logger.info("Agent manager initialized", packages_dir=str(packages_dir))
    
    yield
    
    # Cleanup
    logger.info("Shutting down Pixell Runtime")
    # TODO: Graceful shutdown of components


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
    app.include_router(agents_router, prefix="/runtime", tags=["agents"])
    
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