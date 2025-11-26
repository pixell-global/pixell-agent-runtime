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
                a2a_port = int(os.getenv("A2A_PORT", "50052"))
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

    @app.get("/a2a/health")
    async def _top_a2a_health_alias():
        """Top-level A2A health check alias for ECS health checks."""
        if not package or not package.manifest.a2a:
            return {"ok": False, "service": "a2a", "timestamp": int(time.time() * 1000)}
        try:
            import os
            a2a_port = int(os.getenv("A2A_PORT", "50052"))
            async with grpc_aio.insecure_channel(f"localhost:{a2a_port}") as channel:
                stub = agent_pb2_grpc.AgentServiceStub(channel)
                await stub.Health(agent_pb2.Empty(), timeout=0.5)
            return {"ok": True, "service": "a2a", "timestamp": int(time.time() * 1000)}
        except Exception:
            return {"ok": False, "service": "a2a", "timestamp": int(time.time() * 1000)}

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
        
        # IMPORTANT: Add venv site-packages FIRST, before package path
        # This ensures that dependencies like a2a-sdk are found before
        # the agent's own modules are imported
        import sys
        from pathlib import Path
        package_path = Path(package.path)
        
        if hasattr(package, 'venv_path') and package.venv_path:
            venv_path = Path(package.venv_path)
            # Windows uses "Lib", Unix uses "lib"
            lib_dir = "Lib" if sys.platform == "win32" else "lib"
            
            # Try two possible paths for site-packages:
            # 1. Lib/site-packages (Windows venv standard)
            # 2. Lib/pythonX.Y/site-packages (some venv configurations)
            venv_site_packages = None
            possible_paths = [
                venv_path / lib_dir / "site-packages",
                venv_path / lib_dir / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
            ]
            
            for possible_path in possible_paths:
                if possible_path.exists():
                    venv_site_packages = possible_path
                    break
            
            if venv_site_packages:
                if str(venv_site_packages) not in sys.path:
                    sys.path.insert(0, str(venv_site_packages))
                    logger.info(
                        "Added venv site-packages to sys.path",
                        path=str(venv_site_packages)
                    )
        
        # Add package path to sys.path (after venv, so dependencies take precedence)
        if str(package_path) not in sys.path:
            sys.path.insert(0, str(package_path))

        # Sanitize environment variables before importing agent module
        # Some agent modules (like src/config.py) may try to convert env vars to int
        # at module level, and "None" string will cause errors
        import os
        env_vars_to_sanitize = ["DB_PORT", "DB_HOST", "DB_NAME"]
        
        # Check for .env file in package directory and sanitize it
        env_file = package_path / ".env"
        if env_file.exists():
            logger.debug("Found .env file in package, checking for invalid values", path=str(env_file))
            try:
                with open(env_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                
                # Check if .env file contains invalid values
                modified = False
                new_lines = []
                for line in lines:
                    original_line = line
                    line = line.strip()
                    # Skip comments and empty lines
                    if not line or line.startswith('#'):
                        new_lines.append(original_line)
                        continue
                    
                    # Parse KEY=VALUE format
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        # Remove quotes if present
                        if value.startswith('"') and value.endswith('"'):
                            value = value[1:-1]
                        elif value.startswith("'") and value.endswith("'"):
                            value = value[1:-1]
                        
                        # Check if this is a variable we need to sanitize
                        if key in env_vars_to_sanitize:
                            value_lower = value.lower()
                            if value_lower in ("none", "null") or value == "":
                                # Comment out the line instead of removing it
                                new_lines.append(f"# {original_line.strip()}  # Commented out by runtime: invalid value\n")
                                modified = True
                                logger.info(
                                    "Commented out invalid value in .env file",
                                    var_name=key,
                                    original_value=value,
                                    file=str(env_file)
                                )
                                continue
                    
                    new_lines.append(original_line)
                
                # Write back if modified
                if modified:
                    with open(env_file, 'w', encoding='utf-8') as f:
                        f.writelines(new_lines)
                    logger.info("Updated .env file to remove invalid values", file=str(env_file))
            except Exception as e:
                logger.warning("Failed to sanitize .env file", file=str(env_file), error=str(e))
        
        # Log current values before sanitization (check os.environ directly)
        logger.debug(
            "Checking environment variables before sanitization",
            db_port=os.environ.get("DB_PORT"),
            db_host=os.environ.get("DB_HOST"),
            db_name=os.environ.get("DB_NAME")
        )
        
        for var_name in env_vars_to_sanitize:
            # Check os.environ directly to catch "None" strings
            if var_name in os.environ:
                value = os.environ[var_name]
                if value is not None:
                    value_stripped = str(value).strip()
                    value_lower = value_stripped.lower()
                    if value_lower in ("none", "null") or value_stripped == "":
                        # Remove invalid values so default is used
                        del os.environ[var_name]
                        logger.info(
                            "Removed invalid environment variable",
                            var_name=var_name,
                            original_value=value
                        )
        
        # Log values after sanitization
        logger.debug(
            "Environment variables after sanitization",
            db_port=os.environ.get("DB_PORT"),
            db_host=os.environ.get("DB_HOST"),
            db_name=os.environ.get("DB_NAME")
        )

        # Import and mount routes or app
        # Note: load_dotenv() in config.py may set env vars during import,
        # so we need to catch and handle that
        # The problem is that Config class fields are evaluated at module level,
        # so we can't clean env vars after import - we need to prevent the error
        # by ensuring env vars are clean BEFORE Config class fields are evaluated
        
        # Try importing with multiple retries, cleaning env vars each time
        max_retries = 3
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # Clean env vars before each import attempt
                for var_name in env_vars_to_sanitize:
                    if var_name in os.environ:
                        value = os.environ[var_name]
                        value_stripped = str(value).strip()
                        value_lower = value_stripped.lower()
                        if value_lower in ("none", "null") or value_stripped == "":
                            del os.environ[var_name]
                            if attempt > 0:
                                logger.debug(
                                    "Removed invalid environment variable (before import attempt)",
                                    var_name=var_name,
                                    original_value=value,
                                    attempt=attempt + 1
                                )
                
                # Try importing
                module = __import__(module_path, fromlist=[function_name])
                
                # If we get here, import succeeded
                # Clean env vars one more time in case load_dotenv() set them
                for var_name in env_vars_to_sanitize:
                    if var_name in os.environ:
                        value = os.environ[var_name]
                        value_stripped = str(value).strip()
                        value_lower = value_stripped.lower()
                        if value_lower in ("none", "null") or value_stripped == "":
                            del os.environ[var_name]
                            logger.info(
                                "Removed invalid environment variable (post-import)",
                                var_name=var_name,
                                original_value=value
                            )
                
                # Success - break out of retry loop
                break
                
            except Exception as e:
                last_error = e
                error_str = str(e)
                
                # Check if this is the error we're trying to fix
                if "invalid literal for int()" in error_str and "None" in error_str:
                    if attempt < max_retries - 1:
                        logger.warning(
                            "Import failed due to invalid env var, will retry",
                            error=error_str,
                            attempt=attempt + 1,
                            max_retries=max_retries,
                            exc_info=True
                        )
                        
                        # Remove module from cache
                        import sys
                        modules_to_remove = [module_path]
                        for key in list(sys.modules.keys()):
                            if key == module_path or key.startswith(module_path + "."):
                                modules_to_remove.append(key)
                        
                        for mod_name in modules_to_remove:
                            if mod_name in sys.modules:
                                del sys.modules[mod_name]
                                logger.debug("Removed module from cache", module=mod_name)
                        
                        # Clean env vars before retry
                        for var_name in env_vars_to_sanitize:
                            if var_name in os.environ:
                                value = os.environ[var_name]
                                value_stripped = str(value).strip()
                                value_lower = value_stripped.lower()
                                if value_lower in ("none", "null") or value_stripped == "":
                                    del os.environ[var_name]
                                    logger.info(
                                        "Removed invalid environment variable (before retry)",
                                        var_name=var_name,
                                        original_value=value,
                                        attempt=attempt + 2
                                    )
                    else:
                        # Last attempt failed
                        logger.error(
                            "Import failed after all retries",
                            error=error_str,
                            max_retries=max_retries,
                            exc_info=True
                        )
                        raise
                else:
                    # Different error - don't retry
                    raise
        
        # If we get here, import succeeded (module is defined)
        if hasattr(module, function_name):
            mount_function = getattr(module, function_name)
            mounted = None

            # Allow three patterns for backwards/forwards compatibility:
            # 1) mount(app) style (legacy)
            # 2) mount(app=app) style
            # 3) main() style returning FastAPI / APIRouter (http_main:main)
            try:
                mounted = mount_function(app)
            except TypeError:
                try:
                    mounted = mount_function(app=app)
                except TypeError:
                    # Zero-arg factory: treat as app factory and call with no args
                    mounted = mount_function()

            # If the function returned a FastAPI app or APIRouter, include its routes
            from fastapi import FastAPI as _FastAPI, APIRouter as _APIRouter
            target = app  # FastAPI 또는 APIRouter 모두 include_router 사용 가능
            if isinstance(mounted, _FastAPI):
                target.include_router(mounted.router)
                logger.info(
                    "Mounted agent REST FastAPI app from entry",
                    entry=rest_path,
                    routes=len(getattr(mounted.router, "routes", [])),
                )
            elif isinstance(mounted, _APIRouter):
                target.include_router(mounted)
                logger.info(
                    "Mounted agent REST router from entry",
                    entry=rest_path,
                    routes=len(getattr(mounted, "routes", [])),
                )
            else:
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
                a2a_port = int(os.getenv("A2A_PORT", "50052"))
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
            a2a_port = int(os.getenv("A2A_PORT", "50052"))
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
