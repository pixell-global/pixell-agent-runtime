"""Agent API endpoints."""

from pathlib import Path
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, HTTPException, Response, UploadFile, Request
from pydantic import BaseModel

from pixell_runtime.agents.manager import AgentManager
from pixell_runtime.api.deploy import get_deploy_manager
from pixell_runtime.core.exceptions import AgentNotFoundError
from pixell_runtime.core.models import InvocationRequest, InvocationResponse
from pixell_runtime.proto import agent_pb2, agent_pb2_grpc

router = APIRouter()

# Global agent manager instance (will be properly initialized in app startup)
_agent_manager: AgentManager = None


def get_agent_manager() -> AgentManager:
    """Get agent manager instance."""
    if _agent_manager is None:
        raise RuntimeError("Agent manager not initialized")
    return _agent_manager


class LoadPackageRequest(BaseModel):
    """Request to load a package."""
    path: str


class AgentListResponse(BaseModel):
    """Response for agent list."""
    agents: List[Dict[str, Any]]


@router.post("/packages/load")
async def load_package(request: LoadPackageRequest):
    """Load an APKG package."""
    manager = get_agent_manager()

    try:
        package = await manager.load_package(Path(request.path))
        return {
            "status": "success",
            "package_id": package.id,
            "agents": [agent.id for agent in manager.list_agents() if agent.package_id == package.id]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/packages/upload")
async def upload_package(file: UploadFile):
    """Upload and load an APKG package."""
    manager = get_agent_manager()

    # Save uploaded file
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".apkg") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        package = await manager.load_package(Path(tmp_path))
        return {
            "status": "success",
            "package_id": package.id,
            "agents": [agent.id for agent in manager.list_agents() if agent.package_id == package.id]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        # Clean up temp file
        Path(tmp_path).unlink(missing_ok=True)


@router.get("/agents", response_model=AgentListResponse)
async def list_agents():
    """List all loaded agents."""
    manager = get_agent_manager()
    agents = manager.list_agents()

    return AgentListResponse(
        agents=[
            {
                "id": agent.id,
                "package_id": agent.package_id,
                "name": agent.export.name,
                "description": agent.export.description,
                "status": agent.status.value,
                "private": agent.export.private
            }
            for agent in agents
        ]
    )


@router.post("/agents/{agent_id:path}/invoke", response_model=InvocationResponse)
async def invoke_agent(agent_id: str, request: Dict[str, Any]):
    """Invoke an agent."""
    manager = get_agent_manager()

    # Create invocation request
    invocation = InvocationRequest(
        agent_id=agent_id,
        input=request.get("input", {}),
        context=request.get("context"),
        trace_id=request.get("trace_id")
    )

    try:
        response = await manager.invoke_agent(invocation)
        return response
    except AgentNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def init_agent_manager(packages_dir: Path) -> AgentManager:
    """Initialize the global agent manager."""
    global _agent_manager
    _agent_manager = AgentManager(packages_dir)
    return _agent_manager


# --- Deployment-backed Agent Endpoints ---

@router.get("/agents/{deployment_id}/health")
async def agent_deployment_health(deployment_id: str):
    manager = get_deploy_manager()
    record = manager.get(deployment_id)
    if not record or not record.rest_port:
        raise HTTPException(status_code=404, detail="Deployment not found")

    base = f"http://127.0.0.1:{record.rest_port}"
    # Prefer top-level health alias to avoid double-prefix issues
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=2.0)) as client:
            resp = await client.get(f"{base}/health")
            if resp.status_code == 404:
                # Fallback to base-path health if alias not present
                resp = await client.get(f"{base}/agents/{deployment_id}/health")
            return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout):
        raise HTTPException(status_code=503, detail="Agent REST service unreachable")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent REST health check failed: {e}")


@router.get("/agents/{deployment_id}/a2a/health")
async def agent_deployment_a2a_health(deployment_id: str):
    manager = get_deploy_manager()
    record = manager.get(deployment_id)
    if not record or not record.a2a_port:
        raise HTTPException(status_code=404, detail="Deployment not found")

    try:
        # Simple connectivity check using socket
        import socket
        import asyncio

        async def check_port():
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection('127.0.0.1', record.a2a_port),
                    timeout=2.0
                )
                writer.close()
                await writer.wait_closed()
                return True
            except Exception:
                return False

        if await check_port():
            return {"ok": True, "service": "a2a", "port": record.a2a_port}
        else:
            raise HTTPException(status_code=503, detail="A2A gRPC port not accessible")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"gRPC health check failed: {e}")


@router.api_route("/agents/{deployment_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_to_agent(deployment_id: str, path: str, request: Request):
    """Proxy all requests to deployed agent's REST API."""
    manager = get_deploy_manager()
    record = manager.get(deployment_id)

    if not record or not record.rest_port:
        raise HTTPException(status_code=404, detail=f"Agent deployment not found: {deployment_id}")

    # Build target URL - forward to agent's REST port
    target_url = f"http://127.0.0.1:{record.rest_port}/{path}"

    # Preserve query parameters
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            # Forward request body
            body = await request.body()

            # Forward headers (exclude host and other proxy headers)
            headers = dict(request.headers)
            headers.pop("host", None)
            headers.pop("content-length", None)

            # Make proxied request
            resp = await client.request(
                method=request.method,
                url=target_url,
                content=body,
                headers=headers
            )

            # Return response with original status code and headers
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=dict(resp.headers)
            )

    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail=f"Agent REST service unreachable at port {record.rest_port}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Agent request timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Proxy error: {str(e)}")
