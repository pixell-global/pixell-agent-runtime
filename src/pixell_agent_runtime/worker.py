"""PAR Worker process - runs a single APKG."""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional, Dict, Any
import click
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from pixell_runtime.agents.loader import PackageLoader
    from pixell_runtime.agents.manager import AgentManager
    from pixell_runtime.agents.adapter_factory import create_adapter
    from pixell_runtime.core.models import AgentStatus
except ImportError as e:
    print(f"Import error: {e}")
    print(f"Python path: {sys.path}")
    raise

logger = logging.getLogger(__name__)


class WorkerApp:
    """Worker application that runs a single agent package."""
    
    def __init__(self, agent_id: str, package_path: str, port: int):
        self.agent_id = agent_id
        self.package_path = Path(package_path)
        self.port = port
        self.grpc_port = port + 10000  # gRPC port = HTTP port + 10000
        self.package = None
        self.agent_manager = None
        self.adapter = None
        self.grpc_server = None
        self.app = self._create_app()
        
    def _create_app(self) -> FastAPI:
        """Create FastAPI app for the worker."""
        app = FastAPI(
            title=f"PAR Worker - {self.agent_id}",
            description=f"Worker process serving agent {self.agent_id}",
            version="1.0.0"
        )
        
        @app.on_event("startup")
        async def startup():
            """Load the agent package on startup."""
            logger.info(f"Worker starting for agent {self.agent_id} on port {self.port}")
            
            try:
                # Create package loader
                packages_dir = Path("/tmp/pixell-runtime/packages") / str(self.port)
                loader = PackageLoader(packages_dir)
                
                # Load the package
                self.package = loader.load_package(self.package_path)
                logger.info(f"Loaded package: {self.package.id}")
                
                # Create agent manager
                self.agent_manager = AgentManager(packages_dir)
                
                # Create adapter for the package
                self.adapter = await create_adapter(self.package)
                logger.info(f"Created adapter: {type(self.adapter).__name__}")
                
                # Set up A2A context
                from pixell_agent_runtime.a2a_client import get_a2a_client
                
                # Get supervisor URL from parent
                supervisor_url = os.environ.get("PAR_SUPERVISOR_URL", "http://localhost:8000")
                os.environ["PAR_SUPERVISOR_URL"] = supervisor_url
                
                # Set gRPC port in environment
                os.environ["A2A_PORT"] = str(self.grpc_port)
                
                # Initialize the adapter
                await self.adapter.initialize()
                
                # Start gRPC server if the agent supports it
                await self._start_grpc_server()
                
                # Update package status
                self.package.status = AgentStatus.READY
                
                logger.info(f"Agent {self.agent_id} ready on HTTP port {self.port}, gRPC port {self.grpc_port}")
                
            except Exception as e:
                logger.error(f"Failed to load package: {e}")
                if self.package:
                    self.package.status = AgentStatus.ERROR
                raise
                
        @app.on_event("shutdown")
        async def shutdown():
            """Clean up on shutdown."""
            logger.info(f"Worker shutting down for agent {self.agent_id}")
            
            # Stop gRPC server
            if self.grpc_server:
                try:
                    self.grpc_server.stop()
                except Exception as e:
                    logger.error(f"Error stopping gRPC server: {e}")
            
            if self.adapter:
                try:
                    await self.adapter.cleanup()
                except Exception as e:
                    logger.error(f"Error during adapter cleanup: {e}")
            
        @app.get("/health")
        async def health():
            """Health check endpoint."""
            return {
                "status": "healthy" if self.package and self.package.status == AgentStatus.READY else "starting",
                "agent_id": self.agent_id,
                "port": self.port,
                "grpc_port": self.grpc_port,
                "grpc_enabled": self.grpc_server is not None,
                "package": str(self.package_path),
                "package_id": self.package.id if self.package else None,
                "agent_status": self.package.status.value if self.package else "loading"
            }
            
        @app.post("/invoke")
        async def invoke(request: Request):
            """Invoke the agent."""
            if not self.adapter:
                raise HTTPException(status_code=503, detail="Agent not ready")
                
            try:
                # Get request body
                body = await request.json()
                
                # Call the adapter
                result = await self.adapter.invoke("invoke", body)
                
                return result
                
            except Exception as e:
                logger.error(f"Error invoking agent: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))
                
        # Add dynamic routes for all exports
        @app.post("/exports/{export_name}")
        async def invoke_export(export_name: str, request: Request):
            """Invoke a specific export."""
            if not self.adapter:
                raise HTTPException(status_code=503, detail="Agent not ready")
                
            # For Python agent with entrypoint, we handle specific actions
            if hasattr(self.package.manifest, 'entrypoint') and self.package.manifest.entrypoint:
                # Python agent supports these actions
                if export_name not in ["get_info", "list_capabilities", "execute"]:
                    raise HTTPException(status_code=404, detail=f"Export '{export_name}' not found")
            else:
                # Check if export exists in manifest
                if not self.package or not hasattr(self.package.manifest, 'exports') or export_name not in [e.name for e in self.package.manifest.exports]:
                    raise HTTPException(status_code=404, detail=f"Export '{export_name}' not found")
                
            try:
                body = await request.json()
                
                # Log available methods for debugging
                if hasattr(self.adapter, 'adapter') and hasattr(self.adapter.adapter, 'process_request'):
                    logger.info(f"Using PythonAgentAdapter for export {export_name}")
                    # For Python agent, map export names to actions
                    if export_name in ["get_info", "list_capabilities", "execute"]:
                        body["action"] = export_name
                        result = self.adapter.adapter.process_request(body)
                        return result
                
                result = await self.adapter.invoke(export_name, body)
                return result
            except Exception as e:
                logger.error(f"Error invoking export {export_name}: {e}", exc_info=True)
                raise HTTPException(status_code=500, detail=str(e))
                
        # TODO: Mount actual runtime routes when integrated
        
        return app
        
    async def _start_grpc_server(self):
        """Start gRPC server if the agent supports it."""
        try:
            # Check if this is a Python agent with gRPC support
            if self.package and "grpc" in str(self.package.manifest.entrypoint):
                # Try to start gRPC server
                from pixell_agent_runtime.a2a_grpc_client import A2AGrpcServer
                
                self.grpc_server = A2AGrpcServer(port=self.grpc_port)
                
                # For Python agent, we need to get the service implementation
                if hasattr(self.adapter.module, 'create_grpc_server'):
                    # Agent provides a function to create the server
                    service_impl = self.adapter.module.create_grpc_server()
                    
                    # Get the add_servicer function
                    from src.a2a import python_agent_pb2_grpc
                    self.grpc_server.start(
                        service_impl, 
                        python_agent_pb2_grpc.add_PythonAgentServicer_to_server
                    )
                else:
                    logger.info("Agent does not provide gRPC server implementation")
        except Exception as e:
            logger.warning(f"Could not start gRPC server: {e}")
            # Not critical - agent can still work via HTTP
        
    def run(self):
        """Run the worker process."""
        uvicorn.run(
            self.app,
            host="0.0.0.0",
            port=self.port,
            log_level="info",
            access_log=True
        )


@click.command()
@click.option("--port", type=int, required=True, help="Port to listen on")
@click.option("--agent-id", required=True, help="Agent ID")
@click.option("--package-path", required=True, help="Path to agent package")
def main(port: int, agent_id: str, package_path: str):
    """Run PAR worker process."""
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format=f"[%(asctime)s] [%(levelname)s] [Worker-{agent_id}] %(message)s"
    )
    
    # Create and run worker
    worker = WorkerApp(agent_id, package_path, port)
    worker.run()


if __name__ == "__main__":
    main()