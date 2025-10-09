"""REST API server implementation."""

import time
from typing import Any, Dict, Optional, Union

import structlog
from fastapi import FastAPI, HTTPException, Request, APIRouter
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from pixell_runtime.core.models import AgentPackage
from pixell_runtime.utils.basepath import get_base_path, get_ports
import grpc
import grpc.aio as grpc_aio
from pixell_runtime.proto import agent_pb2, agent_pb2_grpc

logger = structlog.get_logger()


def create_rest_app(package: Optional[AgentPackage] = None, base_path: Optional[str] = None) -> FastAPI:
    """Create FastAPI application with agent-specific routes.
    
    Args:
        package: Optional agent package with custom REST routes
        
    Returns:
        Configured FastAPI application
    """
    app = FastAPI(
        title="Pixell Agent Runtime",
        description="Three-surface runtime for agent packages",
        version="0.1.0"
    )
    # Runtime readiness flag (gated in /health). Default: not ready until runtime flips it.
    app.state.runtime_ready = False
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Add request logging middleware
    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        
        logger.info(
            "Request processed",
            method=request.method,
            url=str(request.url),
            status_code=response.status_code,
            process_time=process_time
        )
        
        return response
    
    # Determine base path
    _base_path = base_path or get_base_path()
    # Ensure base path formatting (no trailing slash except for root)
    if not _base_path.startswith("/"):
        _base_path = "/" + _base_path
    if len(_base_path) > 1 and _base_path.endswith("/"):
        _base_path = _base_path[:-1]

    # Create routers (apply prefix at include time for correctness)
    _prefix = "" if _base_path == "/" else _base_path
    builtins_router = APIRouter()
    # Mount agent routes at {BASE_PATH}; agent packages should prefix their routes with /api
    agent_router = APIRouter()

    # Mount agent-specific routes if package provides them under {BASE_PATH}/api
    if package and package.manifest.rest and package.manifest.rest.entry:
        mount_agent_routes(agent_router, package)
    
    # Built-in endpoints under {BASE_PATH}
    setup_builtin_endpoints(builtins_router, package, main_app=app)

    # Include routers in app with base prefix
    app.include_router(builtins_router, prefix=_prefix)
    app.include_router(agent_router, prefix=_prefix)
    # Also include agent routes at root to ensure availability regardless of base path
    if _prefix:
        app.include_router(agent_router, prefix="")

    # Also expose agent health check at top level regardless of base path
    @app.get("/agents/{agent_id}/health")
    async def _top_agent_health_alias(agent_id: str):
        """Top-level agent health check alias."""
        import os

        current_agent_id = os.getenv("AGENT_APP_ID", "")

        if not current_agent_id or current_agent_id != agent_id:
            return JSONResponse(
                {
                    "error": "Agent not found",
                    "agentId": agent_id,
                    "loaded": False
                },
                status_code=404
            )

        if not package:
            return JSONResponse(
                {
                    "error": "No package loaded",
                    "agentId": agent_id,
                    "loaded": False
                },
                status_code=503
            )

        if not getattr(app.state, "runtime_ready", False):
            return JSONResponse(
                {
                    "agentId": agent_id,
                    "status": "starting",
                    "loaded": True
                },
                status_code=503
            )

        return {
            "agentId": agent_id,
            "status": "healthy",
            "loaded": True
        }

    # Also expose a top-level health alias for runtime checks regardless of base path
    @app.get("/health")
    async def _top_health_alias():
        # Delegate to built-in health handler by calling function directly
        # Since the route function is nested, re-run the logic inline
        # Gate readiness: return 503 until startup completed
        try:
            if not getattr(app.state, "runtime_ready", False):
                return JSONResponse({
                    "ok": False,
                    "surfaces": {"rest": False, "a2a": False, "ui": False},
                    "timestamp": int(time.time() * 1000)
                }, status_code=503)
        except Exception:
            pass

        a2a_ok = False
        if package and package.manifest.a2a:
            try:
                # Use actual A2A port from environment (set by runtime/deployer)
                import os
                a2a_port = int(os.getenv("A2A_PORT", "50051"))
                async with grpc_aio.insecure_channel(f"localhost:{a2a_port}") as channel:
                    stub = agent_pb2_grpc.AgentServiceStub(channel)
                    await stub.Health(agent_pb2.Empty(), timeout=0.5)
                a2a_ok = True
            except Exception:
                a2a_ok = False
        ui_ok = False
        if package and package.manifest.ui and package.manifest.ui.path:
            try:
                from pathlib import Path
                ui_path = Path(package.path) / package.manifest.ui.path
                ui_ok = (ui_path / "index.html").exists()
            except Exception:
                ui_ok = False
        return {"ok": True, "surfaces": {"rest": True, "a2a": a2a_ok, "ui": ui_ok}}

    @app.get("/ui/health")
    async def _top_ui_health_alias():
        # Mirror built-in UI health behavior at root level
        try:
            if not package or not package.manifest.ui:
                return {"ok": False, "service": "ui", "timestamp": int(time.time() * 1000)}
            from pathlib import Path
            ui_path = Path(package.path) / (package.manifest.ui.path or "")
            index_file = ui_path / "index.html"
            return {"ok": index_file.exists(), "service": "ui", "timestamp": int(time.time() * 1000)}
        except Exception:
            return {"ok": False, "service": "ui", "timestamp": int(time.time() * 1000)}
    # Debug: log mounted routes
    try:
        for route in getattr(app, "routes", []):
            try:
                logger.info("Mounted route", path=getattr(route, "path", None))
            except Exception:
                pass
    except Exception:
        pass
    
    return app


def mount_agent_routes(app: Union[FastAPI, APIRouter], package: AgentPackage):
    """Mount agent-specific REST routes.
    
    Args:
        app: FastAPI application
        package: Agent package with REST configuration
    """
    try:
        # Import the custom REST module
        rest_path = package.manifest.rest.entry
        if ":" in rest_path:
            module_path, function_name = rest_path.split(":", 1)
        else:
            module_path = rest_path
            function_name = "mount"
        
        # Add package path to sys.path for imports
        import sys
        from pathlib import Path
        package_path = Path(package.path)
        if str(package_path) not in sys.path:
            sys.path.insert(0, str(package_path))
        
        # Import and mount routes
        module = __import__(module_path, fromlist=[function_name])
        if hasattr(module, function_name):
            mount_function = getattr(module, function_name)
            # Prefer mount(app, base_path=...) if available
            try:
                mount_function(app)
            except TypeError:
                # Attempt to call with keyword if function signature supports it
                try:
                    mount_function(app=app)
                except Exception:
                    raise
            logger.info("Mounted agent REST routes", entry=rest_path)
        else:
            logger.warning("Mount function not found", function=function_name)
            
    except Exception as e:
        logger.error("Failed to mount agent REST routes", error=str(e))


def setup_builtin_endpoints(router: APIRouter, package: Optional[AgentPackage] = None, main_app: Optional[FastAPI] = None):
    """Setup built-in REST endpoints.

    Args:
        router: APIRouter to add endpoints to
        package: Optional agent package for metadata
        main_app: Main FastAPI application for accessing app.state
    """

    @router.get("/agents/{agent_id}/health")
    async def agent_health_check(agent_id: str):
        """Health check endpoint for a specific agent.

        This endpoint is called by PAC to verify that a specific agent is loaded and healthy.
        """
        import os

        # Get the agent_app_id for the currently loaded package
        current_agent_id = os.getenv("AGENT_APP_ID", "")

        # Check if the requested agent ID matches the loaded agent
        if not current_agent_id or current_agent_id != agent_id:
            return JSONResponse(
                {
                    "error": "Agent not found",
                    "agentId": agent_id,
                    "loaded": False
                },
                status_code=404
            )

        # Check if package is loaded
        if not package:
            return JSONResponse(
                {
                    "error": "No package loaded",
                    "agentId": agent_id,
                    "loaded": False
                },
                status_code=503
            )

        # Check if runtime is ready
        if main_app and not getattr(main_app.state, "runtime_ready", False):
            return JSONResponse(
                {
                    "agentId": agent_id,
                    "status": "starting",
                    "loaded": True
                },
                status_code=503
            )

        # Agent is loaded and healthy
        return {
            "agentId": agent_id,
            "status": "healthy",
            "loaded": True
        }

    @router.get("/health")
    async def health_check():
        """Health check endpoint."""
        # Gate readiness: return 503 until startup completed
        try:
            if main_app and not getattr(main_app.state, "runtime_ready", False):
                return JSONResponse({
                    "ok": False,
                    "surfaces": {"rest": False, "a2a": False, "ui": False},
                    "timestamp": int(time.time() * 1000)
                }, status_code=503)
        except Exception:
            pass
        # Determine gRPC health by calling AgentService.Health if configured
        a2a_ok = False
        if package and package.manifest.a2a:
            try:
                import os
                a2a_port = int(os.getenv("A2A_PORT", "50051"))
                async with grpc_aio.insecure_channel(f"localhost:{a2a_port}") as channel:
                    stub = agent_pb2_grpc.AgentServiceStub(channel)
                    # Use a short timeout so health doesn't hang if gRPC isn't ready
                    await stub.Health(agent_pb2.Empty(), timeout=0.5)
                a2a_ok = True
            except Exception:
                a2a_ok = False
        
        # Determine UI presence by checking assets
        ui_ok = False
        if package and package.manifest.ui and package.manifest.ui.path:
            try:
                from pathlib import Path
                ui_path = Path(package.path) / package.manifest.ui.path
                ui_ok = (ui_path / "index.html").exists()
            except Exception:
                ui_ok = False

        surfaces = {
            "rest": True,
            "a2a": a2a_ok,
            "ui": ui_ok
        }
        
        return {
            "ok": True,
            "surfaces": surfaces,
            "timestamp": int(time.time() * 1000)
        }
    
    @router.get("/meta")
    async def get_metadata():
        """Get bundle metadata."""
        if not package:
            raise HTTPException(status_code=404, detail="No package loaded")
        
        meta = {
            "name": package.manifest.name,
            "version": package.manifest.version,
            "description": package.manifest.description,
            "author": package.manifest.author,
            "build_time": package.loaded_at.isoformat(),
            "surfaces": {
                "a2a": package.manifest.a2a is not None,
                "rest": package.manifest.rest is not None,
                "ui": package.manifest.ui is not None
            }
        }
        # Include boot metrics if available
        try:
            if main_app:
                stats = getattr(main_app.state, "boot_stats", None)
                if stats:
                    meta["boot_stats"] = stats
        except Exception:
            pass
        return meta
    
    @router.get("/a2a/health")
    async def a2a_health_check():
        """A2A health check endpoint (HTTP shim for gRPC)."""
        if not package or not package.manifest.a2a:
            raise HTTPException(status_code=404, detail="A2A service not available")
        try:
            import os
            a2a_port = int(os.getenv("A2A_PORT", "50051"))
            async with grpc_aio.insecure_channel(f"localhost:{a2a_port}") as channel:
                stub = agent_pb2_grpc.AgentServiceStub(channel)
                await stub.Health(agent_pb2.Empty(), timeout=0.5)
            return {"ok": True, "service": "a2a", "timestamp": int(time.time() * 1000)}
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"gRPC health failed: {e}")
    
    @router.get("/ui/health")
    async def ui_health_check():
        """UI health check endpoint."""
        try:
            if not package or not package.manifest.ui:
                return {"ok": False, "service": "ui", "timestamp": int(time.time() * 1000)}
            # Check if UI assets exist
            from pathlib import Path
            ui_path = Path(package.path) / package.manifest.ui.path
            index_file = ui_path / "index.html"
            if not index_file.exists():
                return {"ok": False, "service": "ui", "timestamp": int(time.time() * 1000)}
            return {"ok": True, "service": "ui", "timestamp": int(time.time() * 1000)}
        except Exception:
            return {"ok": False, "service": "ui", "timestamp": int(time.time() * 1000)}
    
    @router.get("/")
    async def root():
        """Root endpoint."""
        return {
            "service": "Pixell Agent Runtime",
            "version": "0.1.0",
            "endpoints": {
                "health": "/health",
                "metadata": "/meta",
                "a2a_health": "/a2a/health",
                "ui_health": "/ui/health"
            }
        }
