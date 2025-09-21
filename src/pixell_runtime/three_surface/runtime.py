"""Three-surface runtime implementation."""

import asyncio
import os
import signal
import sys
from typing import Optional

import grpc.aio
import structlog
import uvicorn
from fastapi import FastAPI

from pixell_runtime.agents.loader import PackageLoader
from pixell_runtime.a2a.server import create_grpc_server, start_grpc_server
from pixell_runtime.rest.server import create_rest_app
from pixell_runtime.ui.server import setup_ui_routes, validate_ui_assets
from pixell_runtime.core.models import AgentPackage
from pixell_runtime.utils.logging import setup_logging

logger = structlog.get_logger()


class ThreeSurfaceRuntime:
    """Three-surface runtime that orchestrates A2A, REST, and UI services."""
    
    def __init__(self, package_path: str):
        """Initialize three-surface runtime.
        
        Args:
            package_path: Path to the agent package (APKG file)
        """
        self.package_path = package_path
        self.package: Optional[AgentPackage] = None
        
        # Server instances
        self.rest_app: Optional[FastAPI] = None
        self.grpc_server: Optional[grpc.aio.Server] = None
        self._rest_server: Optional[uvicorn.Server] = None
        self._ui_server: Optional[uvicorn.Server] = None
        
        # Configuration
        self.rest_port = int(os.getenv("REST_PORT", "8080"))
        self.a2a_port = int(os.getenv("A2A_PORT", "50051"))
        self.ui_port = int(os.getenv("UI_PORT", "3000"))
        self.multiplexed = os.getenv("MULTIPLEXED", "true").lower() == "true"
        
        # Setup logging
        setup_logging("INFO", "json")
        
        # Setup signal handlers
        self._setup_signal_handlers()
    
    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logger.info("Received shutdown signal", signal=signum)
            asyncio.create_task(self.shutdown())
        
        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)
    
    async def load_package(self) -> AgentPackage:
        """Load the agent package."""
        logger.info("Loading agent package", path=self.package_path)
        
        # Create package loader
        from pathlib import Path
        packages_dir = Path("/tmp/pixell_packages")
        loader = PackageLoader(packages_dir)
        
        # Load package
        self.package = loader.load_package(Path(self.package_path))
        
        logger.info("Package loaded successfully", 
                   package_id=self.package.id,
                   surfaces={
                       "a2a": self.package.manifest.a2a is not None,
                       "rest": self.package.manifest.rest is not None,
                       "ui": self.package.manifest.ui is not None
                   })
        
        return self.package
    
    async def start_rest_server(self):
        """Start the REST server."""
        if not self.package:
            raise RuntimeError("Package must be loaded before starting servers")
        
        logger.info("Starting REST server", port=self.rest_port)
        
        # Create REST app
        self.rest_app = create_rest_app(self.package)
        
        # Setup UI routes if multiplexed
        if self.multiplexed and self.package.manifest.ui:
            setup_ui_routes(self.rest_app, self.package)
        
        # Start server
        config = uvicorn.Config(
            self.rest_app,
            host="0.0.0.0",
            port=self.rest_port,
            log_config=None,  # We handle logging ourselves
            access_log=False,
        )
        
        server = uvicorn.Server(config)
        self._rest_server = server
        await server.serve()
    
    async def start_grpc_server(self):
        """Start the A2A gRPC server."""
        if not self.package:
            raise RuntimeError("Package must be loaded before starting servers")
        
        if not self.package.manifest.a2a:
            logger.info("No A2A configuration found, skipping gRPC server")
            return
        
        logger.info("Starting A2A gRPC server", port=self.a2a_port)
        
        # Create gRPC server
        self.grpc_server = create_grpc_server(self.package, self.a2a_port)
        
        # Start server
        await start_grpc_server(self.grpc_server)
    
    async def start_ui_server(self):
        """Start standalone UI server (if not multiplexed)."""
        if self.multiplexed:
            logger.info("UI is multiplexed with REST server, skipping standalone UI server")
            return
        
        if not self.package or not self.package.manifest.ui:
            logger.info("No UI configuration found, skipping UI server")
            return
        
        logger.info("Starting standalone UI server", port=self.ui_port)
        
        # Validate UI assets
        if not validate_ui_assets(self.package):
            logger.error("UI assets validation failed")
            return
        
        # Create UI app
        from pixell_runtime.ui.server import create_ui_app
        ui_app = create_ui_app(self.package, self.ui_port)
        
        # Start server
        config = uvicorn.Config(
            ui_app,
            host="0.0.0.0",
            port=self.ui_port,
            log_config=None,
            access_log=False,
        )
        
        server = uvicorn.Server(config)
        self._ui_server = server
        await server.serve()
    
    async def start(self):
        """Start all configured services."""
        logger.info("Starting three-surface runtime", 
                   multiplexed=self.multiplexed,
                   ports={
                       "rest": self.rest_port,
                       "a2a": self.a2a_port,
                       "ui": self.ui_port
                   })
        
        # Load package
        await self.load_package()
        
        # Start services concurrently
        tasks = []
        
        # Always start REST server
        tasks.append(asyncio.create_task(self.start_rest_server()))
        
        # Start A2A server if configured
        if self.package.manifest.a2a:
            tasks.append(asyncio.create_task(self.start_grpc_server()))
        
        # Start UI server if not multiplexed and configured
        if not self.multiplexed and self.package.manifest.ui:
            tasks.append(asyncio.create_task(self.start_ui_server()))
        
        # Wait for all tasks
        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error("Error in runtime", error=str(e))
            await self.shutdown()
    
    async def shutdown(self):
        """Gracefully shutdown all services."""
        logger.info("Shutting down three-surface runtime")
        
        # Shutdown gRPC server
        if self.grpc_server:
            logger.info("Shutting down gRPC server")
            await self.grpc_server.stop(grace=5.0)
            self.grpc_server = None

        # Signal REST server to exit
        if self._rest_server is not None:
            try:
                logger.info("Signaling REST server to exit")
                self._rest_server.should_exit = True
            except Exception:
                pass

        # Signal UI server to exit
        if self._ui_server is not None:
            try:
                logger.info("Signaling UI server to exit")
                self._ui_server.should_exit = True
            except Exception:
                pass
        
        logger.info("Runtime shutdown complete")


def create_runtime(package_path: str) -> ThreeSurfaceRuntime:
    """Create a three-surface runtime instance.
    
    Args:
        package_path: Path to the agent package (APKG file)
        
    Returns:
        Configured runtime instance
    """
    return ThreeSurfaceRuntime(package_path)


async def main():
    """Main entry point for three-surface runtime."""
    if len(sys.argv) != 2:
        print("Usage: python -m pixell_runtime.three_surface.runtime <package_path>")
        sys.exit(1)
    
    package_path = sys.argv[1]
    runtime = create_runtime(package_path)
    
    try:
        await runtime.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        await runtime.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
