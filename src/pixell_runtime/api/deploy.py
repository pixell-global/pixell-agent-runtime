"""Deploy API implementing push-only PAR contract."""

from __future__ import annotations

from typing import Any, Dict

import hmac
import json
import os

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from pixell_runtime.a2a.client import get_a2a_client
from pixell_runtime.deploy.manager import DeploymentManager
from pixell_runtime.deploy.models import DeploymentRequest, DeploymentStatus


router = APIRouter()
logger = structlog.get_logger()


_deployment_manager: DeploymentManager | None = None


def init_deploy_manager(manager: DeploymentManager) -> DeploymentManager:
    global _deployment_manager
    _deployment_manager = manager
    return _deployment_manager


def get_deploy_manager() -> DeploymentManager:
    if _deployment_manager is None:
        raise RuntimeError("DeploymentManager not initialized")
    return _deployment_manager


def _require_bearer(auth_header: str | None, settings_secret: str | None):
    if not settings_secret:
        return  # if not configured, skip auth for local dev
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth_header.split(" ", 1)[1]
    if token != settings_secret:
        raise HTTPException(status_code=403, detail="Invalid runtime secret")


class DeployAccepted(BaseModel):
    status: str
    deploymentId: str


@router.post("/deploy", response_model=DeployAccepted, status_code=202)
async def deploy_endpoint(
    payload: DeploymentRequest,
    req: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    # Auth
    runtime_secret = os.getenv("PAR_RUNTIME_SECRET")
    _require_bearer(authorization, runtime_secret)

    # Idempotency
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header required")
    if idempotency_key != payload.deploymentId:
        raise HTTPException(status_code=400, detail="Idempotency-Key must equal deploymentId")

    # Get or initialize deployment manager
    try:
        manager = get_deploy_manager()
    except RuntimeError:
        # Lazily initialize from app state for TestClient and local cases
        from pathlib import Path
        from pixell_runtime.core.config import Settings
        from pixell_runtime.deploy.manager import DeploymentManager
        state_mgr = getattr(req.app.state, "deployment_manager", None)
        if state_mgr is None:
            settings = getattr(req.app.state, "settings", Settings())
            state_mgr = DeploymentManager(Path(settings.package_cache_dir))
            req.app.state.deployment_manager = state_mgr
        init_deploy_manager(state_mgr)
        manager = state_mgr
    record = await manager.deploy(payload)
    return DeployAccepted(status="accepted", deploymentId=record.deploymentId)


@router.get("/deployments/{deployment_id}/health")
async def deployment_health(deployment_id: str) -> Dict[str, Any]:
    manager = get_deploy_manager()
    record = manager.get(deployment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Deployment not found")

    # Map status to healthy boolean
    healthy = record.status == DeploymentStatus.HEALTHY

    # Build message based on status
    message = None
    if record.status == DeploymentStatus.DOWNLOADING:
        message = "Downloading package"
    elif record.status == DeploymentStatus.LOADING:
        message = "Loading package"
    elif record.status == DeploymentStatus.STARTING:
        message = "Starting runtime"
    elif record.status == DeploymentStatus.FAILED:
        message = record.details.get("error", "Deployment failed")

    # Add A2A health check for healthy deployments
    a2a_healthy = None
    if healthy and record.a2a_port:
        try:
            client = get_a2a_client(prefer_internal=True)
            a2a_healthy = await client.health_check(deployment_id=deployment_id)
            logger.info("A2A health check result",
                       deployment_id=deployment_id,
                       a2a_healthy=a2a_healthy)
        except Exception as e:
            logger.warning("A2A health check failed",
                          deployment_id=deployment_id, error=str(e))
            a2a_healthy = False

    # Overall health includes A2A if it's configured
    overall_healthy = healthy
    if a2a_healthy is not None:
        overall_healthy = healthy and a2a_healthy

    return {
        "status": record.status.value,
        "healthy": overall_healthy,  # ← Required by PAC contract
        "message": message,
        "details": record.details,
        "surfaces": {
            "rest": record.rest_port is not None,
            "a2a": a2a_healthy if a2a_healthy is not None else False,
            "ui": record.ui_port is not None
        },
        # Keep ports for backward compatibility
        "ports": {
            "rest": record.rest_port,
            "a2a": record.a2a_port,
            "ui": record.ui_port,
        } if record.rest_port else None,
    }


@router.post("/deployments/{deployment_id}/invoke")
async def deployment_invoke(deployment_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke an agent via A2A.

    Args:
        deployment_id: Deployment to invoke
        payload: JSON with 'action' and 'context' fields

    Returns:
        Invocation response
    """
    manager = get_deploy_manager()
    record = manager.get(deployment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Deployment not found")

    if record.status != DeploymentStatus.HEALTHY:
        raise HTTPException(status_code=503, detail=f"Deployment not healthy: {record.status.value}")

    if not record.a2a_port:
        raise HTTPException(status_code=400, detail="Deployment does not have A2A service")

    action = payload.get("action")
    context = payload.get("context", "{}")

    if not action:
        raise HTTPException(status_code=400, detail="Missing 'action' field")

    try:
        client = get_a2a_client(prefer_internal=True)
        result = await client.invoke(
            action=action,
            context=context if isinstance(context, str) else json.dumps(context),
            deployment_id=deployment_id,
            timeout=30.0
        )
        logger.info("A2A invocation completed",
                   deployment_id=deployment_id,
                   action=action,
                   success=result.get("error") is None)
        return result
    except Exception as e:
        logger.error("A2A invocation failed",
                    deployment_id=deployment_id,
                    action=action,
                    error=str(e))
        raise HTTPException(status_code=500, detail=f"Invocation failed: {str(e)}")


