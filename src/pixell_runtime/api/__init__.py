"""API module for Pixell Agent Runtime."""

from .health import router as health_router
from .agents import router as agents_router

try:
    from .deploy import router as deploy_router
except Exception:
    deploy_router = None

__all__ = [
    "health_router",
    "agents_router",
    "deploy_router",
]