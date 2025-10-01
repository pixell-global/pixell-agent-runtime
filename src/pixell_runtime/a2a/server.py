"""A2A gRPC server implementation."""

import asyncio
import time
from typing import Any, Dict, Optional

import grpc
import structlog
from concurrent import futures

from pixell_runtime.core.models import AgentPackage

logger = structlog.get_logger()


class AgentServiceImpl:
    """Default A2A service implementation."""
    
    def __init__(self, package: Optional[AgentPackage] = None):
        """Initialize service with optional agent package."""
        self.package = package
        self.custom_handlers = {}
        
        # Load custom handlers if package provides them
        if package and package.manifest.a2a and package.manifest.a2a.service:
            self._load_custom_handlers()
    
    def _load_custom_handlers(self):
        """Load custom handlers from agent package."""
        try:
            # Import the custom service module
            service_path = self.package.manifest.a2a.service
            if ":" in service_path:
                module_path, function_name = service_path.split(":", 1)
            else:
                module_path = service_path
                function_name = "create_grpc_server"
            
            # Add package path to sys.path for imports
            import sys
            from pathlib import Path
            package_path = Path(self.package.path)
            if str(package_path) not in sys.path:
                sys.path.insert(0, str(package_path))
            
            # Import and get custom handlers
            module = __import__(module_path, fromlist=[function_name])
            if hasattr(module, function_name):
                custom_service = getattr(module, function_name)()
                if hasattr(custom_service, 'custom_handlers'):
                    self.custom_handlers = custom_service.custom_handlers
                logger.info("Loaded custom A2A handlers", service=service_path)
        except Exception as e:
            logger.warning("Failed to load custom A2A handlers", error=str(e))
    
    async def Health(self, request, context):
        """Health check endpoint."""
        from pixell_runtime.proto import agent_pb2
        
        return agent_pb2.HealthStatus(
            ok=True,
            message="Agent is healthy",
            timestamp=int(time.time() * 1000)
        )
    
    async def DescribeCapabilities(self, request, context):
        """Describe agent capabilities."""
        from pixell_runtime.proto import agent_pb2
        
        capabilities = agent_pb2.Capabilities()
        capabilities.methods.extend(["Health", "DescribeCapabilities", "Invoke", "Ping"])
        
        if self.package:
            capabilities.metadata["name"] = self.package.manifest.name
            capabilities.metadata["version"] = self.package.manifest.version
            capabilities.metadata["description"] = self.package.manifest.description or ""
        
        return capabilities
    
    async def Invoke(self, request, context):
        """Invoke an action."""
        from pixell_runtime.proto import agent_pb2
        
        start_time = time.time()
        request_id = request.request_id or f"req_{int(time.time() * 1000)}"
        
        try:
            # Check for custom handler first
            if request.action in self.custom_handlers:
                result = await self.custom_handlers[request.action](request.parameters)
                success = True
                error = ""
            else:
                # Default implementation - delegate to agent exports
                if self.package and self.package.manifest.exports:
                    # For now, use the first export as the main handler
                    export = self.package.manifest.exports[0]
                    # This would need to be implemented based on the agent's handler
                    result = f"Action '{request.action}' executed with parameters: {dict(request.parameters)}"
                    success = True
                    error = ""
                else:
                    result = ""
                    success = False
                    error = f"No handler found for action: {request.action}"
            
            duration_ms = int((time.time() - start_time) * 1000)
            
            return agent_pb2.ActionResult(
                success=success,
                result=str(result),
                error=error,
                request_id=request_id,
                duration_ms=duration_ms
            )
            
        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error("Action invocation failed", action=request.action, error=str(e))
            
            return agent_pb2.ActionResult(
                success=False,
                result="",
                error=str(e),
                request_id=request_id,
                duration_ms=duration_ms
            )
    
    async def Ping(self, request, context):
        """Simple ping endpoint."""
        from pixell_runtime.proto import agent_pb2
        
        return agent_pb2.Pong(
            message="pong",
            timestamp=int(time.time() * 1000)
        )


def create_grpc_server(package: Optional[AgentPackage] = None, port: int = 50051) -> grpc.aio.Server:
    """Create and configure gRPC server.
    
    Args:
        package: Optional agent package with custom handlers
        port: Port to bind the server to
        
    Returns:
        Configured gRPC server
    """
    # Import here to avoid circular imports; ensure package is importable
    import sys
    from pathlib import Path
    pkg_dir = Path(__file__).resolve().parents[1]
    if str(pkg_dir) not in sys.path:
        sys.path.insert(0, str(pkg_dir))
    from pixell_runtime.proto import agent_pb2_grpc
    
    # Create server
    server = grpc.aio.server(futures.ThreadPoolExecutor(max_workers=10))
    
    # Create service implementation
    service_impl = AgentServiceImpl(package)
    
    # Add service to server
    agent_pb2_grpc.add_AgentServiceServicer_to_server(service_impl, server)
    
    # Configure server
    listen_addr = f'[::]:{port}'
    server.add_insecure_port(listen_addr)
    
    logger.info("Created A2A gRPC server", port=port, listen_addr=listen_addr)
    
    return server


async def start_grpc_server(server: grpc.aio.Server):
    """Start the gRPC server."""
    logger.info("Starting A2A gRPC server")
    await server.start()
    # Do not block the event loop here; let runtime shutdown handle server.stop()
