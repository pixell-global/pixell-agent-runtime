"""Health check endpoints."""

from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Response

from pixell_runtime import __version__
from pixell_runtime.core.models import RuntimeInfo
from pixell_runtime.deploy.manager import DeploymentManager
from pixell_runtime.api.deploy import get_deploy_manager

router = APIRouter()

# Track start time
START_TIME = datetime.now(timezone.utc)


@router.get("/health", response_model=Dict[str, str])
async def health_check() -> Dict[str, str]:
    """Simple health check endpoint."""
    return {
        "status": "healthy",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/info", response_model=RuntimeInfo)
async def runtime_info() -> RuntimeInfo:
    """Get detailed runtime information."""
    # TODO: Get actual stats from package manager
    # Optionally incorporate deployment stats
    try:
        manager = get_deploy_manager()
        packages_loaded = len(manager.deployments)
        agents_mounted = 0
    except Exception:
        packages_loaded = 0
        agents_mounted = 0

    return RuntimeInfo(
        version=__version__,
        start_time=START_TIME,
        packages_loaded=packages_loaded,
        agents_mounted=agents_mounted,
        total_invocations=0,
        status="healthy",
    )