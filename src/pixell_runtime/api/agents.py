"""Agent API endpoints."""

from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from pixell_runtime.agents.manager import AgentManager
from pixell_runtime.core.exceptions import AgentNotFoundError, PackageError
from pixell_runtime.core.models import Agent, InvocationRequest, InvocationResponse

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