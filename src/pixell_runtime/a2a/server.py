"""A2A gRPC server implementation."""

import asyncio
import time
from typing import Any, Dict, Optional, List

import grpc
import structlog
from concurrent import futures

from pixell_runtime.core.models import AgentPackage
from pixell_runtime.a2a.interceptor import PARRoutingInterceptor

logger = structlog.get_logger()


class AgentServiceImpl:
    """Default A2A service implementation."""

    def __init__(self, package: Optional[AgentPackage] = None, agent_a2a_port: Optional[int] = None):
        """Initialize service with optional agent package.

        Args:
            package: Agent package metadata
            agent_a2a_port: Port where the agent's gRPC server is running (for subprocess mode)
        """
        self.package = package
        self.custom_handlers = {}  # Will be injected by create_grpc_server() if needed
        self.agent_a2a_port = agent_a2a_port  # Port for forwarding to agent's gRPC server
    
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
        """Invoke an action by forwarding to agent's gRPC server."""
        from pixell_runtime.proto import agent_pb2, agent_pb2_grpc

        start_time = time.time()
        request_id = request.request_id or f"req_{int(time.time() * 1000)}"

        try:
            # Check for custom handler first
            if request.action in self.custom_handlers:
                result = await self.custom_handlers[request.action](request.parameters)
                success = True
                error = ""
            # If agent has its own gRPC server (subprocess mode), forward to it
            elif self.agent_a2a_port:
                try:
                    # Connect to agent's gRPC server
                    agent_address = f"localhost:{self.agent_a2a_port}"
                    async with grpc.aio.insecure_channel(agent_address) as channel:
                        stub = agent_pb2_grpc.AgentServiceStub(channel)

                        # Forward the request to the agent
                        agent_response = await stub.Invoke(request, timeout=30)

                        # Return the agent's response
                        return agent_response

                except Exception as forward_error:
                    logger.error(
                        "Failed to forward to agent gRPC server",
                        action=request.action,
                        port=self.agent_a2a_port,
                        error=str(forward_error)
                    )
                    result = ""
                    success = False
                    error = f"Failed to forward to agent: {str(forward_error)}"
            else:
                # No custom handler and no agent gRPC server
                result = ""
                success = False
                error = f"No handler found for action: {request.action}"

            duration_ms = int((time.time() - start_time) * 1000)

            return agent_pb2.ActionResult(
                success=success,
                result=str(result),
                error=error,
                request_id=request_id,
                duration_ms=duration_ms,
                metadata={}  # Empty metadata for custom handlers (they can add their own)
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.error("Action invocation failed", action=request.action, error=str(e))

            return agent_pb2.ActionResult(
                success=False,
                result="",
                error=str(e),
                request_id=request_id,
                duration_ms=duration_ms,
                metadata={}  # Empty metadata on error
            )
    
    async def Ping(self, request, context):
        """Simple ping endpoint."""
        from pixell_runtime.proto import agent_pb2

        return agent_pb2.Pong(
            message="pong",
            timestamp=int(time.time() * 1000)
        )


def _load_agent_service(package: AgentPackage) -> tuple:
    """Load agent's gRPC service or handlers.

    Detects which pattern the agent is using:
    - Full Servicer: Agent provides complete gRPC servicer implementation
    - Handlers Dict: Agent provides action handlers dict
    - None: Agent doesn't provide a service

    Args:
        package: Agent package with a2a service configuration

    Returns:
        Tuple of (service, pattern, handlers) where:
        - service: The loaded servicer instance or None
        - pattern: "full_servicer" | "handlers_dict" | "none"
        - handlers: Dict of custom handlers (only for handlers_dict pattern)
    """
    if not package or not package.manifest.a2a or not package.manifest.a2a.service:
        return None, "none", None

    try:
        # Parse service path (format: "module.path:function_name")
        service_path = package.manifest.a2a.service
        if ":" in service_path:
            module_path, function_name = service_path.split(":", 1)
        else:
            module_path = service_path
            function_name = "create_service"  # Default function name

        # Add package path to sys.path for imports
        import sys
        from pathlib import Path
        package_path = Path(package.path)
        if str(package_path) not in sys.path:
            sys.path.insert(0, str(package_path))

        # Add venv site-packages to sys.path BEFORE importing agent modules
        # This fixes ModuleNotFoundError for agent dependencies like sqlalchemy
        if hasattr(package, 'venv_path') and package.venv_path:
            venv_site_packages = (
                Path(package.venv_path) / "lib" /
                f"python{sys.version_info.major}.{sys.version_info.minor}" /
                "site-packages"
            )
            if venv_site_packages.exists():
                if str(venv_site_packages) not in sys.path:
                    sys.path.insert(0, str(venv_site_packages))
                    logger.info(
                        "Added venv site-packages to sys.path",
                        path=str(venv_site_packages)
                    )

        # Import and call the service creation function
        module = __import__(module_path, fromlist=[function_name])
        if not hasattr(module, function_name):
            logger.warning(
                "Service function not found in module",
                module=module_path,
                function=function_name,
                service_path=service_path
            )
            return None, "none", None

        service_fn = getattr(module, function_name)
        result = service_fn()

        # Detect pattern based on what was returned
        if result is None:
            logger.info("Agent service function returned None")
            return None, "none", None

        # Pattern 1: Check if it's a full gRPC servicer
        # Try isinstance check first (most reliable)
        try:
            from pixell_runtime.proto import agent_pb2_grpc
            if isinstance(result, agent_pb2_grpc.AgentServiceServicer):
                logger.info(
                    "Detected full gRPC servicer pattern",
                    service_class=type(result).__name__,
                    module=module_path
                )
                return result, "full_servicer", None
        except (ImportError, TypeError) as e:
            logger.debug("isinstance check failed, using duck typing", error=str(e))

        # Fall back to duck typing (check for required methods)
        if hasattr(result, 'Health') and hasattr(result, 'Invoke'):
            logger.info(
                "Detected full gRPC servicer pattern (duck typing)",
                service_class=type(result).__name__,
                module=module_path
            )
            return result, "full_servicer", None

        # Pattern 2: Check if it's a handlers dict
        if isinstance(result, dict) and 'custom_handlers' in result:
            handlers = result['custom_handlers']
            logger.info(
                "Detected handlers dict pattern",
                handler_count=len(handlers) if isinstance(handlers, dict) else 0,
                module=module_path
            )
            return None, "handlers_dict", handlers

        # Pattern 2 (legacy): Check if result has .custom_handlers attribute
        if hasattr(result, 'custom_handlers'):
            handlers = result.custom_handlers
            logger.info(
                "Detected handlers via attribute",
                handler_count=len(handlers) if isinstance(handlers, dict) else 0,
                module=module_path
            )
            return None, "handlers_dict", handlers

        # Unknown pattern
        logger.warning(
            "Agent service returned unsupported type",
            result_type=type(result).__name__,
            has_health=hasattr(result, 'Health'),
            has_invoke=hasattr(result, 'Invoke'),
            has_custom_handlers=hasattr(result, 'custom_handlers'),
            module=module_path
        )
        return None, "none", None

    except Exception as e:
        logger.error(
            "Failed to load agent service",
            error=str(e),
            service_path=package.manifest.a2a.service if package and package.manifest.a2a else "unknown",
            exc_info=True
        )
        return None, "none", None


def create_grpc_server(
    package: Optional[AgentPackage] = None,
    port: int = 50052,
    agent_a2a_port: Optional[int] = None,
    agent_id: Optional[str] = None
) -> grpc.aio.Server:
    """Create and configure gRPC server.

    Supports three agent patterns:
    1. Full Servicer: Agent provides complete gRPC servicer implementation
    2. Handlers Dict: Agent provides action handlers, PAR dispatches via AgentServiceImpl
    3. Subprocess Forwarding: Agent runs own gRPC server, PAR forwards via AgentServiceImpl

    Args:
        package: Optional agent package with a2a service
        port: Port to bind the server to (default: 50052)
        agent_a2a_port: Port for forwarding to agent's subprocess gRPC server (Pattern 3)
        agent_id: Agent app ID for path-based routing interceptor (NEW)

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

    # Build interceptor chain
    interceptors: List[grpc.aio.ServerInterceptor] = []

    if agent_id:
        # Add PAR routing interceptor (MUST be first in chain!)
        routing_interceptor = PARRoutingInterceptor(agent_id=agent_id)
        interceptors.append(routing_interceptor)
        logger.info(
            "Added PAR routing interceptor",
            agent_id=agent_id,
            prefix=routing_interceptor.prefix
        )
    else:
        logger.warning(
            "No agent_id provided - routing interceptor not added. "
            "ALB path-based routing will not work!"
        )

    # Create server with interceptors
    server = grpc.aio.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=interceptors if interceptors else None
    )

    # Load agent's service (if package provides one)
    agent_service, pattern, handlers = _load_agent_service(package)

    # Decide which servicer to register based on detected pattern
    if pattern == "full_servicer" and agent_service is not None:
        # Pattern 1: Use agent's servicer directly
        service_impl = agent_service
        logger.info(
            "Registered agent-provided gRPC servicer",
            service_class=type(agent_service).__name__,
            port=port
        )
    else:
        # Pattern 2 or 3: Use PAR's AgentServiceImpl
        service_impl = AgentServiceImpl(package, agent_a2a_port=agent_a2a_port)

        if pattern == "handlers_dict" and handlers:
            # Pattern 2: Inject custom handlers into AgentServiceImpl
            service_impl.custom_handlers = handlers
            logger.info(
                "Registered AgentServiceImpl with custom handlers",
                handler_count=len(handlers),
                port=port
            )
        elif agent_a2a_port:
            # Pattern 3: AgentServiceImpl will forward to subprocess
            logger.info(
                "Registered AgentServiceImpl with subprocess forwarding",
                port=port,
                agent_a2a_port=agent_a2a_port
            )
        else:
            # Default: AgentServiceImpl with no customization
            logger.info(
                "Registered default AgentServiceImpl",
                port=port
            )

    # Add service to server
    agent_pb2_grpc.add_AgentServiceServicer_to_server(service_impl, server)

    # Configure server
    # Bind on IPv4 to avoid environments without IPv6
    listen_addr = f'0.0.0.0:{port}'
    server.add_insecure_port(listen_addr)

    logger.info(
        "Created A2A gRPC server",
        port=port,
        listen_addr=listen_addr,
        servicer_type=type(service_impl).__name__
    )

    return server


async def start_grpc_server(server: grpc.aio.Server):
    """Start the gRPC server."""
    logger.info("Starting A2A gRPC server")
    await server.start()
    # Do not block the event loop here; let runtime shutdown handle server.stop()
