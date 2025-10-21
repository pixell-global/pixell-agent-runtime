"""FastAPI HTTP server for supervisor API.

This server provides HTTP endpoints for deploying, updating, deleting, and monitoring agents.
It runs on port 9000 and orchestrates all supervisor components.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import List, Optional

import structlog
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from pixell_runtime.supervisor.models import (
    DeployRequest,
    UpdateRequest,
    DeleteRequest,
    DeployResponse,
    AgentStatusResponse,
    AgentProcess,
    AgentStatus,
)
from pixell_runtime.supervisor.state import SupervisorState
from pixell_runtime.supervisor.grpc_gateway import GrpcGateway

logger = structlog.get_logger()


# Global supervisor state and gateway
supervisor_state: Optional[SupervisorState] = None
grpc_gateway: Optional[GrpcGateway] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for FastAPI app.

    Initializes supervisor state and gRPC gateway on startup,
    cleans up on shutdown.
    """
    global supervisor_state, grpc_gateway

    # Startup
    logger.info("Starting supervisor server")
    supervisor_state = SupervisorState()

    # Start gRPC gateway for path-based routing
    logger.info("Starting gRPC gateway for agent routing")
    grpc_gateway = GrpcGateway(supervisor_state)
    await grpc_gateway.start()
    logger.info(
        "gRPC gateway started",
        gateway_port=grpc_gateway.port,
        note="Gateway routes /agents/{id}/a2a/* to agent ports 60000-60199"
    )

    yield

    # Shutdown
    logger.info("Shutting down supervisor server")

    # Stop gateway first
    if grpc_gateway:
        logger.info("Stopping gRPC gateway")
        await grpc_gateway.stop()

    # Then cleanup supervisor state
    if supervisor_state:
        await supervisor_state.cleanup()


# Create FastAPI app
app = FastAPI(
    title="Pixell Agent Supervisor",
    description="Multi-agent supervisor for EC2 deployment",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Health check endpoint for instance.

    Returns health status and capacity information for PAC integration.

    Returns:
        Health status with capacity metrics
    """
    if not supervisor_state:
        return {
            "status": "unhealthy",
            "agents_running": 0,
            "capacity": {
                "current": 0,
                "max": 20,
                "available": 20,
            },
            "disk_free_gb": 0.0,
            "memory_free_mb": 0.0,
            "cpu_load": [0.0, 0.0, 0.0],
        }

    agents = supervisor_state.list_agents()
    running_agents = sum(1 for a in agents if a.status == AgentStatus.RUNNING)
    max_agents = supervisor_state.port_allocator.max_agents()
    available = supervisor_state.port_allocator.available_slots()

    # Get system metrics (placeholder - could be enhanced with psutil)
    disk_free_gb = 0.0
    memory_free_mb = 0.0
    cpu_load = [0.0, 0.0, 0.0]
    try:
        import psutil
        import shutil

        # Disk space
        disk_usage = shutil.disk_usage("/")
        disk_free_gb = disk_usage.free / (1024 ** 3)  # bytes to GB

        # Memory
        mem = psutil.virtual_memory()
        memory_free_mb = mem.available / (1024 ** 2)  # bytes to MB

        # CPU load average (1, 5, 15 min)
        cpu_load = list(psutil.getloadavg()) if hasattr(psutil, 'getloadavg') else [0.0, 0.0, 0.0]
    except (ImportError, Exception):
        pass  # If psutil not available, use defaults

    return {
        "status": "healthy",
        "agents_running": running_agents,
        "capacity": {
            "current": len(agents),
            "max": max_agents,
            "available": available,
        },
        "disk_free_gb": disk_free_gb,
        "memory_free_mb": memory_free_mb,
        "cpu_load": cpu_load,
    }


@app.post("/agents", response_model=DeployResponse, status_code=status.HTTP_201_CREATED)
async def deploy_agent(request: DeployRequest):
    """Deploy a new agent.

    Args:
        request: Deployment request

    Returns:
        DeployResponse with agent info

    Raises:
        HTTPException: If deployment fails
    """
    if not supervisor_state:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supervisor not initialized",
        )

    logger.info(
        "Received deploy request",
        agent_app_id=request.agent_app_id,
        deployment_id=request.deployment_id,
    )

    try:
        agent_process = await supervisor_state.deploy(request)

        return DeployResponse(
            agent_app_id=agent_process.agent_app_id,
            deployment_id=agent_process.deployment_id,
            status=agent_process.status.value,
            message=f"Agent {agent_process.agent_app_id} deployed successfully",
            ports=agent_process.ports,
            linux_user=agent_process.linux_user,
            pid=agent_process.pid,
            created_at=agent_process.created_at,
        )

    except Exception as e:
        logger.error(
            "Deploy request failed",
            agent_app_id=request.agent_app_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Deployment failed: {str(e)}",
        )


@app.put("/agents/{agent_app_id}", response_model=DeployResponse)
async def update_agent(agent_app_id: str, request: UpdateRequest):
    """Update an existing agent (zero-downtime).

    Args:
        agent_app_id: Agent identifier (from URL path)
        request: Update request

    Returns:
        DeployResponse with updated agent info

    Raises:
        HTTPException: If update fails
    """
    if not supervisor_state:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supervisor not initialized",
        )

    # Ensure agent_app_id from path matches request (if provided in body)
    if hasattr(request, 'agent_app_id') and request.agent_app_id and request.agent_app_id != agent_app_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent ID mismatch: path has {agent_app_id}, body has {request.agent_app_id}",
        )

    # Set agent_app_id from path if not in request
    request.agent_app_id = agent_app_id

    logger.info(
        "Received update request",
        agent_app_id=agent_app_id,
        deployment_id=request.deployment_id,
    )

    try:
        agent_process = await supervisor_state.update(request)

        return DeployResponse(
            agent_app_id=agent_process.agent_app_id,
            deployment_id=agent_process.deployment_id,
            status=agent_process.status.value,
            message=f"Agent {agent_process.agent_app_id} updated successfully",
            ports=agent_process.ports,
            linux_user=agent_process.linux_user,
            pid=agent_process.pid,
            created_at=agent_process.created_at,
        )

    except Exception as e:
        logger.error(
            "Update request failed",
            agent_app_id=request.agent_app_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Update failed: {str(e)}",
        )


@app.delete("/agents/{agent_app_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(agent_app_id: str, force: bool = False, cleanup_user: bool = False):
    """Delete an agent deployment.

    Args:
        agent_app_id: Agent identifier
        force: Force kill process immediately
        cleanup_user: Delete Linux user and home directory (default: False, user is preserved for fast redeployment)

    Raises:
        HTTPException: If deletion fails
    """
    if not supervisor_state:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supervisor not initialized",
        )

    logger.info("Received delete request", agent_app_id=agent_app_id, force=force)

    try:
        request = DeleteRequest(
            agent_app_id=agent_app_id,
            force=force,
            cleanup_user=cleanup_user,
        )

        deleted = await supervisor_state.delete(request)

        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent {agent_app_id} not found",
            )

        return None  # 204 No Content

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Delete request failed",
            agent_app_id=agent_app_id,
            error=str(e),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Deletion failed: {str(e)}",
        )


@app.get("/agents/{agent_app_id}", response_model=AgentProcess)
async def get_agent(agent_app_id: str):
    """Get agent information.

    Args:
        agent_app_id: Agent identifier

    Returns:
        AgentProcess with agent info

    Raises:
        HTTPException: If agent not found
    """
    if not supervisor_state:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supervisor not initialized",
        )

    agent = supervisor_state.get_agent(agent_app_id)

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_app_id} not found",
        )

    return agent


@app.get("/agents/{agent_app_id}/status", response_model=AgentStatusResponse)
async def get_agent_status(agent_app_id: str):
    """Get detailed agent status with metrics.

    This endpoint provides process metrics and health status for PAC integration.

    Args:
        agent_app_id: Agent identifier

    Returns:
        AgentStatusResponse with detailed status

    Raises:
        HTTPException: If agent not found
    """
    if not supervisor_state:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supervisor not initialized",
        )

    agent = supervisor_state.get_agent(agent_app_id)

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_app_id} not found",
        )

    # Get real-time process health (detects zombies immediately)
    process_health = supervisor_state.process_manager.get_process_health(agent_app_id)

    # Determine actual status - override agent.status if zombie detected
    # This prevents false positives during the window before zombie reaper runs
    if process_health["is_zombie"]:
        # Zombie detected - report as failed
        actual_status = "failed"
        health = {"rest": False, "a2a": False, "ui": False}
        uptime_seconds = 0
        logger.warning(
            "Agent process is zombie - reporting as failed",
            agent_app_id=agent_app_id,
            pid=agent.pid,
        )
    elif not process_health["is_alive"]:
        # Process stopped/terminated
        actual_status = "stopped"
        health = {"rest": False, "a2a": False, "ui": False}
        uptime_seconds = 0
    else:
        # Process is alive - use supervisor state
        actual_status = agent.status.value
        health = {
            "rest": agent.status == AgentStatus.RUNNING,
            "a2a": agent.status == AgentStatus.RUNNING,
            "ui": agent.status == AgentStatus.RUNNING,
        }

        # Calculate uptime only for alive processes
        uptime_seconds = 0
        if agent.started_at:
            from datetime import datetime
            uptime_seconds = int((datetime.utcnow() - agent.started_at).total_seconds())

    return AgentStatusResponse(
        agent_app_id=agent.agent_app_id,
        status=actual_status,  # Use real-time status instead of cached agent.status
        process_id=agent.pid,  # PAC expects 'process_id', not 'pid'
        uptime_seconds=uptime_seconds,
        memory_mb=process_health["memory_mb"],
        cpu_percent=process_health["cpu_percent"],
        ports=agent.ports,
        health=health,
    )


@app.get("/agents", response_model=List[AgentProcess])
async def list_agents():
    """List all agents.

    Returns:
        List of AgentProcess objects
    """
    if not supervisor_state:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supervisor not initialized",
        )

    return supervisor_state.list_agents()


@app.get("/status")
async def get_status():
    """Get supervisor status.

    Returns:
        Status information including agent count, port availability, etc.
    """
    if not supervisor_state:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supervisor not initialized",
        )

    agents = supervisor_state.list_agents()

    # Count agents by status
    status_counts = {}
    for agent in agents:
        status_val = agent.status.value
        status_counts[status_val] = status_counts.get(status_val, 0) + 1

    return {
        "service": "supervisor",
        "healthy": True,
        "total_agents": len(agents),
        "status_counts": status_counts,
        "max_agents": supervisor_state.port_allocator.max_agents(),
        "available_slots": supervisor_state.port_allocator.available_slots(),
    }


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler for uncaught exceptions."""
    logger.error(
        "Unhandled exception",
        path=request.url.path,
        method=request.method,
        error=str(exc),
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": f"Internal server error: {str(exc)}"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "pixell_runtime.supervisor.server:app",
        host="0.0.0.0",
        port=9000,
        log_level="info",
        reload=False,
    )
