"""gRPC Gateway for multi-agent path-based routing.

This gateway solves the path mismatch problem where:
- ALB forwards requests with full paths: /agents/{agent_id}/a2a/pixell.agent.AgentService/Health
- Agent gRPC servers expect clean paths: /pixell.agent.AgentService/Health

The gateway:
1. Listens on port 50051 (external access via ALB)
2. Parses incoming paths to extract agent_id
3. Strips routing prefix to get clean gRPC method path
4. Looks up agent's A2A port from SupervisorState
5. Forwards request to agent's gRPC server (ports 60000-60199)
6. Returns response to client

Architecture:
- Gateway: port 50051 (external, via ALB)
- Agents: ports 60000-60199 (internal, 200-agent capacity)
- Routing: Local SupervisorState lookup (fast, no database calls)
"""

import asyncio
import re
from typing import Optional
import structlog

import grpc
from grpc import aio

from pixell_runtime.supervisor.state import SupervisorState

logger = structlog.get_logger()

# Path pattern: /agents/{agent_id}/a2a/{service}/{method}
# Example: /agents/4906eeb7/a2a/pixell.agent.AgentService/Health
PATH_PATTERN = re.compile(r"^/agents/(?P<agent_id>[^/]+)/a2a/(?P<service_method>.+)$")

# Gateway port (external access via ALB)
GATEWAY_PORT = 50051


class GrpcGateway:
    """gRPC gateway for path-based routing to agents.

    This gateway acts as a reverse proxy, parsing agent IDs from request paths
    and forwarding to the correct agent's gRPC server.
    """

    def __init__(self, supervisor_state: SupervisorState, port: int = GATEWAY_PORT):
        """Initialize gateway.

        Args:
            supervisor_state: SupervisorState instance for agent lookup
            port: Port to listen on (default: 50051)
        """
        self.supervisor_state = supervisor_state
        self.port = port
        self.server: Optional[aio.Server] = None

        logger.info(
            "GrpcGateway initialized",
            port=port,
            note="Gateway will route requests to agents based on path"
        )

    async def start(self):
        """Start the gRPC gateway server."""
        logger.info("Starting gRPC gateway", port=self.port)

        # Create gRPC server
        self.server = aio.server()

        # Register generic handler for all methods
        self.server.add_generic_rpc_handlers((self._create_generic_handler(),))

        # Bind to port
        listen_addr = f"0.0.0.0:{self.port}"
        self.server.add_insecure_port(listen_addr)

        # Start server
        await self.server.start()

        logger.info(
            "gRPC gateway started successfully",
            address=listen_addr,
            gateway_port=self.port,
            agent_port_range="60000-60199",
            note="Listening for ALB requests on port 50051"
        )

    async def stop(self, grace_period: float = 5.0):
        """Stop the gRPC gateway server.

        Args:
            grace_period: Grace period in seconds for shutdown
        """
        if not self.server:
            logger.warning("Gateway not running, nothing to stop")
            return

        logger.info("Stopping gRPC gateway", grace_period=grace_period)

        await self.server.stop(grace_period)
        self.server = None

        logger.info("gRPC gateway stopped")

    async def wait_for_termination(self):
        """Wait for server to terminate."""
        if self.server:
            await self.server.wait_for_termination()

    def _create_generic_handler(self):
        """Create generic RPC handler for all methods.

        Returns:
            GenericRpcHandler that routes all requests
        """
        class GatewayHandler(grpc.GenericRpcHandler):
            """Generic RPC handler that routes all requests."""

            def __init__(self, gateway):
                self.gateway = gateway

            def service(self, handler_call_details):
                """Handle all incoming RPC calls.

                Args:
                    handler_call_details: grpc.HandlerCallDetails

                Returns:
                    RpcMethodHandler for the request
                """
                # Create unary-unary handler
                return grpc.unary_unary_rpc_method_handler(
                    self.gateway._handle_request,
                    request_deserializer=lambda x: x,  # Pass through raw bytes
                    response_serializer=lambda x: x,  # Pass through raw bytes
                )

        return GatewayHandler(self)

    async def _handle_request(self, request, context):
        """Handle incoming gRPC call.

        Args:
            request: Request message (raw bytes)
            context: gRPC context

        Returns:
            Response message (raw bytes) or error
        """
        # Get invocation metadata
        invocation_metadata = context.invocation_metadata()
        method = context._invocation_metadata.method  # Full path from client

        logger.debug(
            "Gateway received request",
            method=method,
            metadata={m.key: m.value for m in invocation_metadata}
        )

        # Parse path to extract agent_id and clean method
        match = PATH_PATTERN.match(method)

        if not match:
            error_msg = (
                f"Invalid path format: {method}. "
                f"Expected: /agents/{{agent_id}}/a2a/{{service}}/{{method}}"
            )
            logger.error("Path parsing failed", method=method, error=error_msg)
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, error_msg)
            return b""

        agent_id = match.group("agent_id")
        service_method = match.group("service_method")
        clean_method = f"/{service_method}"  # Clean gRPC method path

        logger.info(
            "Parsed gateway request",
            agent_id=agent_id,
            original_path=method,
            clean_method=clean_method
        )

        # Look up agent in supervisor state
        agent = self.supervisor_state.get_agent(agent_id)

        if not agent:
            error_msg = f"Agent {agent_id} not found"
            logger.error("Agent lookup failed", agent_id=agent_id)
            await context.abort(grpc.StatusCode.NOT_FOUND, error_msg)
            return b""

        if not agent.ports or not agent.ports.a2a:
            error_msg = f"Agent {agent_id} has no A2A port allocated"
            logger.error("Agent port missing", agent_id=agent_id)
            await context.abort(grpc.StatusCode.INTERNAL, error_msg)
            return b""

        # Forward request to agent's A2A port
        target_port = agent.ports.a2a
        target_address = f"localhost:{target_port}"

        logger.info(
            "Forwarding request to agent",
            agent_id=agent_id,
            target_port=target_port,
            clean_method=clean_method
        )

        try:
            # Create channel to agent
            async with aio.insecure_channel(target_address) as channel:
                # Create generic stub
                stub = channel.unary_unary(
                    clean_method,
                    request_serializer=lambda x: x,  # Pass through raw bytes
                    response_deserializer=lambda x: x,  # Pass through raw bytes
                )

                # Forward metadata from original request
                metadata = tuple((m.key, m.value) for m in invocation_metadata)

                # Call agent with timeout
                response = await stub(
                    request,
                    metadata=metadata,
                    timeout=30.0  # 30 second timeout
                )

                logger.info(
                    "Request forwarded successfully",
                    agent_id=agent_id,
                    target_port=target_port,
                    clean_method=clean_method
                )

                return response

        except grpc.RpcError as e:
            error_msg = f"Agent gRPC error: {e.code()}: {e.details()}"
            logger.error(
                "Agent request failed",
                agent_id=agent_id,
                target_port=target_port,
                error=error_msg,
                grpc_code=e.code()
            )
            await context.abort(e.code(), e.details())
            return b""

        except Exception as e:
            error_msg = f"Gateway forwarding error: {str(e)}"
            logger.error(
                "Gateway forwarding failed",
                agent_id=agent_id,
                target_port=target_port,
                error=str(e),
                exc_info=True
            )
            await context.abort(grpc.StatusCode.INTERNAL, error_msg)
            return b""


async def main():
    """Test/development entry point for gateway."""
    from pixell_runtime.supervisor.state import SupervisorState

    logger.info("Starting gRPC gateway (standalone mode)")

    # Create supervisor state
    supervisor_state = SupervisorState()

    # Create and start gateway
    gateway = GrpcGateway(supervisor_state)
    await gateway.start()

    try:
        # Wait for termination
        await gateway.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down")
    finally:
        await gateway.stop()
        await supervisor_state.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
