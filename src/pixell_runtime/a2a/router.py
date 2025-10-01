"""PAR-level A2A gRPC router for multi-agent deployments.

This router listens on port 50052 (internal) and forwards requests to individual
agent A2A servers based on the x-deployment-id header. It acts as a central routing
point for external requests coming through Envoy.

Architecture:
    External Client → NLB:50051 → Envoy:50051 → PAR Router:50052 → Agent:5005X
"""

import asyncio
from typing import Optional

import grpc
import structlog
from concurrent import futures

from pixell_runtime.proto import agent_pb2, agent_pb2_grpc

logger = structlog.get_logger()


class A2ARouterServicer(agent_pb2_grpc.AgentServiceServicer):
    """Router that forwards A2A requests to the correct agent based on deployment_id."""

    def __init__(self, deployment_manager):
        """Initialize router with deployment manager for lookups.

        Args:
            deployment_manager: DeploymentManager instance for looking up agent ports
        """
        self.deployment_manager = deployment_manager
        logger.info("A2A router servicer initialized")

    def _get_deployment_id_from_context(self, context: grpc.ServicerContext) -> Optional[str]:
        """Extract x-deployment-id from gRPC metadata.

        Args:
            context: gRPC servicer context containing metadata

        Returns:
            deployment_id if found, None otherwise
        """
        metadata = dict(context.invocation_metadata())

        # Check common header formats
        deployment_id = (
            metadata.get('x-deployment-id') or
            metadata.get('deployment-id') or
            metadata.get('deployment_id')
        )

        if deployment_id:
            logger.debug("Found deployment_id in metadata", deployment_id=deployment_id)
        else:
            logger.warning("No deployment_id found in metadata", metadata_keys=list(metadata.keys()))

        return deployment_id

    def _get_agent_channel(self, deployment_id: str) -> Optional[grpc.aio.Channel]:
        """Get gRPC channel to the agent's A2A server.

        Args:
            deployment_id: The deployment ID to route to

        Returns:
            gRPC channel to agent or None if agent not found
        """
        # Look up deployment record
        record = self.deployment_manager.get(deployment_id)

        if not record:
            logger.error("Deployment not found", deployment_id=deployment_id)
            return None

        if not record.a2a_port:
            logger.error("Deployment has no A2A port", deployment_id=deployment_id)
            return None

        # Create async channel to agent's A2A server on localhost
        # Use 127.0.0.1 explicitly to avoid IPv6/IPv4 resolution issues
        endpoint = f"127.0.0.1:{record.a2a_port}"
        logger.debug("Routing to agent", deployment_id=deployment_id, endpoint=endpoint)

        return grpc.aio.insecure_channel(endpoint)

    async def Health(self, request, context):
        """Forward health check to the target agent."""
        deployment_id = self._get_deployment_id_from_context(context)

        if not deployment_id:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("Missing x-deployment-id header")
            return agent_pb2.HealthStatus(ok=False, message="Missing x-deployment-id header")

        channel = self._get_agent_channel(deployment_id)
        if not channel:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Deployment {deployment_id} not found or not ready")
            return agent_pb2.HealthStatus(ok=False, message=f"Deployment {deployment_id} not found")

        try:
            # Forward request to agent using async stub
            stub = agent_pb2_grpc.AgentServiceStub(channel)
            response = await stub.Health(request, timeout=5.0)
            logger.debug("Health check forwarded successfully", deployment_id=deployment_id)
            return response
        except grpc.RpcError as e:
            logger.error("Failed to forward health check", deployment_id=deployment_id, error=str(e))
            context.set_code(e.code())
            context.set_details(f"Failed to reach agent: {e.details()}")
            return agent_pb2.HealthStatus(ok=False, message=f"Agent unreachable: {e.details()}")

    async def DescribeCapabilities(self, request, context):
        """Forward capabilities request to the target agent."""
        deployment_id = self._get_deployment_id_from_context(context)

        if not deployment_id:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("Missing x-deployment-id header")
            return agent_pb2.Capabilities()

        channel = self._get_agent_channel(deployment_id)
        if not channel:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Deployment {deployment_id} not found or not ready")
            return agent_pb2.Capabilities()

        try:
            stub = agent_pb2_grpc.AgentServiceStub(channel)
            response = await stub.DescribeCapabilities(request, timeout=5.0)
            logger.debug("DescribeCapabilities forwarded successfully", deployment_id=deployment_id)
            return response
        except grpc.RpcError as e:
            logger.error("Failed to forward DescribeCapabilities", deployment_id=deployment_id, error=str(e))
            context.set_code(e.code())
            context.set_details(f"Failed to reach agent: {e.details()}")
            return agent_pb2.Capabilities()

    async def Invoke(self, request, context):
        """Forward invoke request to the target agent."""
        deployment_id = self._get_deployment_id_from_context(context)

        if not deployment_id:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("Missing x-deployment-id header")
            return agent_pb2.ActionResult(
                success=False,
                error="Missing x-deployment-id header",
                request_id=request.request_id
            )

        channel = self._get_agent_channel(deployment_id)
        if not channel:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Deployment {deployment_id} not found or not ready")
            return agent_pb2.ActionResult(
                success=False,
                error=f"Deployment {deployment_id} not found",
                request_id=request.request_id
            )

        try:
            stub = agent_pb2_grpc.AgentServiceStub(channel)
            response = await stub.Invoke(request, timeout=300.0)  # 5 min timeout for long actions
            logger.info("Invoke forwarded successfully",
                       deployment_id=deployment_id,
                       action=request.action,
                       success=response.success)
            return response
        except grpc.RpcError as e:
            logger.error("Failed to forward Invoke",
                        deployment_id=deployment_id,
                        action=request.action,
                        error=str(e))
            context.set_code(e.code())
            context.set_details(f"Failed to reach agent: {e.details()}")
            return agent_pb2.ActionResult(
                success=False,
                error=f"Agent unreachable: {e.details()}",
                request_id=request.request_id
            )

    async def Ping(self, request, context):
        """Forward ping request to the target agent."""
        deployment_id = self._get_deployment_id_from_context(context)

        if not deployment_id:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details("Missing x-deployment-id header")
            return agent_pb2.Pong(message="error: missing x-deployment-id header")

        channel = self._get_agent_channel(deployment_id)
        if not channel:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(f"Deployment {deployment_id} not found or not ready")
            return agent_pb2.Pong(message=f"error: deployment {deployment_id} not found")

        try:
            stub = agent_pb2_grpc.AgentServiceStub(channel)
            response = await stub.Ping(request, timeout=5.0)
            logger.debug("Ping forwarded successfully", deployment_id=deployment_id)
            return response
        except grpc.RpcError as e:
            logger.error("Failed to forward Ping", deployment_id=deployment_id, error=str(e))
            context.set_code(e.code())
            context.set_details(f"Failed to reach agent: {e.details()}")
            return agent_pb2.Pong(message=f"error: {e.details()}")


def create_router_server(deployment_manager, port: int = 50052, bind_address: str = "127.0.0.1") -> grpc.aio.Server:
    """Create and configure the A2A router gRPC server.

    Args:
        deployment_manager: DeploymentManager instance for agent lookups
        port: Port to bind the router to (default: 50052)
        bind_address: Address to bind to (default: 127.0.0.1 for Envoy setups, use 0.0.0.0 for external access)

    Returns:
        Configured gRPC server
    """
    server = grpc.aio.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=[
            ('grpc.max_send_message_length', 100 * 1024 * 1024),  # 100MB
            ('grpc.max_receive_message_length', 100 * 1024 * 1024),  # 100MB
        ]
    )

    # Create and add router servicer
    router_servicer = A2ARouterServicer(deployment_manager)
    agent_pb2_grpc.add_AgentServiceServicer_to_server(router_servicer, server)

    # Bind to specified address
    listen_addr = f'{bind_address}:{port}'
    server.add_insecure_port(listen_addr)

    logger.info("Created A2A router gRPC server", port=port, listen_addr=listen_addr)

    return server


async def start_router_server(server: grpc.aio.Server):
    """Start the A2A router gRPC server.

    Args:
        server: The gRPC server to start
    """
    logger.info("Starting A2A router gRPC server")
    await server.start()
    logger.info("A2A router gRPC server started successfully")


async def stop_router_server(server: grpc.aio.Server):
    """Gracefully stop the A2A router gRPC server.

    Args:
        server: The gRPC server to stop
    """
    logger.info("Stopping A2A router gRPC server")
    await server.stop(grace=5.0)
    logger.info("A2A router gRPC server stopped")