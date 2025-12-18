"""UI serving implementation."""

import os
from pathlib import Path
from typing import Optional

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from pixell_runtime.core.models import AgentPackage
from pixell_runtime.utils.basepath import get_base_path

logger = structlog.get_logger()


def setup_ui_routes(app: FastAPI, package: AgentPackage, base_path_override: Optional[str] = None):
    """Setup UI routes for serving static assets.
    
    Args:
        app: FastAPI application
        package: Agent package with UI configuration
    """
    logger.info("Setting up UI routes", package_id=package.id, has_ui_config=bool(package.manifest.ui))
    
    if not package.manifest.ui or not package.manifest.ui.path:
        logger.warning("No UI configuration found in package", 
                     package_id=package.id,
                     has_manifest_ui=bool(package.manifest.ui),
                     ui_path=getattr(package.manifest.ui, 'path', None) if package.manifest.ui else None)
        return
    
    logger.info("UI configuration found", 
               package_id=package.id,
               ui_path=package.manifest.ui.path,
               ui_base_path=getattr(package.manifest.ui, 'basePath', None))
    
    ui_path = Path(package.path) / package.manifest.ui.path
    # Compose base path from environment BASE_PATH (or override) and manifest.ui.basePath
    env_base = base_path_override if base_path_override is not None else get_base_path()
    manifest_base = (package.manifest.ui.basePath or "/").strip()
    if manifest_base == "/":
        base_path = env_base
    else:
        if manifest_base.startswith("/"):
            manifest_base = manifest_base[1:]
        base_path = env_base + "/" + manifest_base if env_base != "/" else "/" + manifest_base
    
    if not ui_path.exists():
        logger.error("UI path does not exist", ui_path=str(ui_path))
        return
    
    # Ensure base_path starts and ends with /
    if not base_path.startswith("/"):
        base_path = "/" + base_path
    if not base_path.endswith("/"):
        base_path = base_path + "/"
    
    logger.info("Setting up UI routes", ui_path=str(ui_path), base_path=base_path)
    
    # Mount static files if directory exists
    static_dir = ui_path / "static"
    if static_dir.exists() and static_dir.is_dir():
        app.mount(
            f"{base_path}static",
            StaticFiles(directory=str(static_dir)),
            name="ui_static"
        )
    
    # Mount assets directory (for React/Vite builds)
    assets_dir = ui_path / "assets"
    if assets_dir.exists() and assets_dir.is_dir():
        app.mount(
            f"{base_path}assets",
            StaticFiles(directory=str(assets_dir)),
            name="ui_assets"
        )
    
    # Provide runtime configuration for the UI to discover API base
    @app.get(f"{base_path}ui-config.json")
    async def ui_config():
        api_base = base_path[:-1] + "/api" if base_path != "/" else "/api"
        return {"apiBase": api_base}

    # Lightweight UI health endpoint always available
    @app.get(f"{base_path}ui/health")
    async def ui_health():
        index_file = ui_path / "index.html"
        return {"ok": index_file.exists(), "service": "ui"}

    # Serve UI with SPA fallback while not shadowing API/health endpoints
    @app.get(f"{base_path}ui/{{path:path}}")
    async def serve_ui(path: str, request: Request):
        # Do not intercept API, A2A, health, metadata, or config endpoints
        reserved = {"health", "meta", "ui-config.json"}
        if path.startswith("api/") or path.startswith("a2a/") or path in reserved:
            raise HTTPException(status_code=404)

        # If it's a file request (has extension), serve the actual file
        if "." in path and not path.endswith("/"):
            file_path = ui_path / path
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))

        # For SPA routing, serve index.html for all other requests
        index_file = ui_path / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))

        raise HTTPException(status_code=404, detail="UI not found")

    # Serve UI at root path as well (for direct access)
    @app.get(f"{base_path}")
    async def serve_ui_root(request: Request):
        """Serve UI at root path, but only if no other routes match."""
        # Check if this is a request for reserved endpoints
        path = request.url.path
        if path in ["/health", "/meta", "/a2a/health", "/ui/health", "/ui-config.json"]:
            raise HTTPException(status_code=404)
        
        # Serve index.html for root path
        index_file = ui_path / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        
        raise HTTPException(status_code=404, detail="UI not found")


def create_ui_app(package: AgentPackage, port: int = 3000) -> FastAPI:
    """Create a standalone UI server application.
    
    Args:
        package: Agent package with UI configuration
        port: Port for the UI server
        
    Returns:
        FastAPI application configured for UI serving
    """
    app = FastAPI(
        title="Pixell Agent UI",
        description="UI server for agent package",
        version="0.1.0"
    )
    
    # Add CORS middleware
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Setup UI routes
    setup_ui_routes(app, package)
    
    # Add health endpoint
    @app.get("/health")
    async def health():
        """UI server health check."""
        return {"ok": True, "service": "ui", "port": port}
    
    return app


def validate_ui_assets(package: AgentPackage) -> bool:
    """Validate that UI assets exist and are properly configured.
    
    Args:
        package: Agent package to validate
        
    Returns:
        True if UI assets are valid, False otherwise
    """
    if not package.manifest.ui or not package.manifest.ui.path:
        return False
    
    ui_path = Path(package.path) / package.manifest.ui.path
    index_file = ui_path / "index.html"
    
    if not ui_path.exists():
        logger.error("UI path does not exist", ui_path=str(ui_path))
        return False
    
    if not index_file.exists():
        logger.error("UI index.html not found", index_file=str(index_file))
        return False
    
    logger.info("UI assets validated successfully", ui_path=str(ui_path))
    return True
