"""REST API server implementation."""

import time
from typing import Any, Dict, Optional

import structlog
from fastapi import FastAPI, HTTPException, Request, APIRouter
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from pixell_runtime.core.models import AgentPackage
from pixell_runtime.utils.basepath import get_base_path, get_ports
import grpc
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

    # Create routers with prefixes
    builtins_router = APIRouter(prefix=_base_path)
    api_router = APIRouter(prefix=f"{_base_path}/api")

    # Mount agent-specific routes if package provides them under {BASE_PATH}/api
    if package and package.manifest.rest and package.manifest.rest.entry:
        mount_agent_routes(api_router, package)
    
    # Built-in endpoints under {BASE_PATH}
    setup_builtin_endpoints(builtins_router, package)

    # Include routers in app
    app.include_router(builtins_router)
    app.include_router(api_router)
    
    return app


def mount_agent_routes(app: FastAPI | APIRouter, package: AgentPackage):
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


def setup_builtin_endpoints(app: FastAPI | APIRouter, package: Optional[AgentPackage] = None):
    """Setup built-in REST endpoints.
    
    Args:
        app: FastAPI application
        package: Optional agent package for metadata
    """
    
    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        # Determine gRPC health by calling AgentService.Health if configured
        a2a_ok = False
        if package and package.manifest.a2a:
            try:
                _, a2a_port, _ = get_ports()
                with grpc.insecure_channel(f"localhost:{a2a_port}") as channel:
                    stub = agent_pb2_grpc.AgentServiceStub(channel)
                    stub.Health(agent_pb2.Empty())
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
    
    @app.get("/meta")
    async def get_metadata():
        """Get bundle metadata."""
        if not package:
            raise HTTPException(status_code=404, detail="No package loaded")
        
        return {
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
    
    @app.get("/a2a/health")
    async def a2a_health_check():
        """A2A health check endpoint (HTTP shim for gRPC)."""
        if not package or not package.manifest.a2a:
            raise HTTPException(status_code=404, detail="A2A service not available")
        try:
            _, a2a_port, _ = get_ports()
            with grpc.insecure_channel(f"localhost:{a2a_port}") as channel:
                stub = agent_pb2_grpc.AgentServiceStub(channel)
                stub.Health(agent_pb2.Empty())
            return {"ok": True, "service": "a2a", "timestamp": int(time.time() * 1000)}
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"gRPC health failed: {e}")
    
    @app.get("/ui/health")
    async def ui_health_check():
        """UI health check endpoint."""
        if not package or not package.manifest.ui:
            raise HTTPException(status_code=404, detail="UI not available")
        
        # Check if UI assets exist
        from pathlib import Path
        ui_path = Path(package.path) / package.manifest.ui.path
        index_file = ui_path / "index.html"
        
        if not index_file.exists():
            raise HTTPException(status_code=404, detail="UI index.html not found")
        
        return {"ok": True, "service": "ui", "timestamp": int(time.time() * 1000)}
    
    @app.get("/")
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
