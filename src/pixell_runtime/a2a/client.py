"""A2A gRPC client with service discovery support."""

import os
import socket
from typing import Optional
import grpc
import structlog

from pixell_runtime.utils.service_discovery import get_service_discovery_client

logger = structlog.get_logger()


class A2AClient:
    """Client for A2A gRPC communication with service discovery."""

    def __init__(self, prefer_internal: bool = True):
        """Initialize A2A client.

        Args:
            prefer_internal: If True, prefer Service Discovery over external NLB
        """
        self.prefer_internal = prefer_internal
        self.sd_client = get_service_discovery_client()

    def get_agent_channel(
        self,
        deployment_id: Optional[str] = None,
        timeout: int = 30
    ) -> grpc.aio.Channel:
        """Get gRPC channel to an agent.

        Strategy:
        1. If deployment_id provided, check if it's a local deployment (subprocess)
        2. If deployment_id provided and Service Discovery available:
           - Try to find specific agent by ID
        3. If prefer_internal and Service Discovery available:
           - Return channel to any healthy agent
        4. Fall back to external NLB endpoint

        Args:
            deployment_id: Optional specific deployment to target
            timeout: Connection timeout in seconds

        Returns:
            Async gRPC channel

        Raises:
            RuntimeError: If no agents available
        """
        # Helper: quick TCP reachability check
        def _is_port_open(host: str, port: int, timeout_sec: float = 0.25) -> bool:
            try:
                with socket.create_connection((host, port), timeout=timeout_sec):
                    return True
            except Exception:
                return False

        # Prefer reachable local deployment if present on this instance
        if deployment_id:
            try:
                from pixell_runtime.api.deploy import get_deploy_manager
                manager = get_deploy_manager()
                record = manager.get(deployment_id)
                if record and record.a2a_port:
                    host = "127.0.0.1"
                    port = int(record.a2a_port)
                    if _is_port_open(host, port):
                        endpoint = f"{host}:{port}"
                        logger.info("Using local deployment (reachable)",
                                   deployment_id=deployment_id,
                                   endpoint=endpoint)
                        return grpc.aio.insecure_channel(endpoint)
                    else:
                        logger.info("Local deployment not reachable, falling back",
                                   deployment_id=deployment_id, port=port)
            except Exception as e:
                logger.debug("Local deployment check failed, falling back",
                             deployment_id=deployment_id, error=str(e))

        # Try Service Discovery (for agents on other PAR instances)
        if self.prefer_internal and self.sd_client:
            if deployment_id:
                agent = self.sd_client.discover_agent_by_id(deployment_id)
                if agent:
                    endpoint = f"{agent['ipv4']}:{agent['port']}"
                    logger.info("Using Service Discovery (specific agent)",
                               deployment_id=deployment_id, endpoint=endpoint)
                    return grpc.aio.insecure_channel(endpoint)

            # Get any healthy agent
            agents = self.sd_client.discover_agents(max_results=5)
            if agents:
                agent = agents[0]  # TODO: Add load balancing logic
                endpoint = f"{agent['ipv4']}:{agent['port']}"
                logger.info("Using Service Discovery (any agent)",
                           endpoint=endpoint, instance_id=agent['instance_id'])
                return grpc.aio.insecure_channel(endpoint)

        # Fall back to external endpoint (NLB)
        external_endpoint = os.getenv('A2A_EXTERNAL_ENDPOINT')
        if external_endpoint:
            logger.info("Using external A2A endpoint", endpoint=external_endpoint)
            return grpc.aio.insecure_channel(external_endpoint)

        # Last resort: try localhost (for local development)
        a2a_port = os.getenv('A2A_PORT', '50051')
        localhost_endpoint = f"localhost:{a2a_port}"
        logger.warning("No Service Discovery or external endpoint, using localhost",
                      endpoint=localhost_endpoint)
        return grpc.aio.insecure_channel(localhost_endpoint)

    async def health_check(self, deployment_id: Optional[str] = None) -> bool:
        """Check health of an agent.

        Args:
            deployment_id: Optional specific deployment to check

        Returns:
            True if healthy, False otherwise
        """
        try:
            from pixell_runtime.proto import agent_pb2, agent_pb2_grpc

            channel = self.get_agent_channel(deployment_id=deployment_id)
            stub = agent_pb2_grpc.AgentServiceStub(channel)

            response = await stub.Health(agent_pb2.Empty(), timeout=2.0)
            return response.ok

        except Exception as e:
            logger.warning("A2A health check failed",
                          deployment_id=deployment_id, error=str(e))
            return False

    async def invoke(
        self,
        action: str,
        context: str,
        deployment_id: Optional[str] = None,
        timeout: float = 30.0
    ) -> dict:
        """Invoke an agent action via A2A.

        Args:
            action: Action name to invoke
            context: JSON context for the action
            deployment_id: Optional specific deployment to target
            timeout: Invocation timeout in seconds

        Returns:
            dict with 'response' and optionally 'error'

        Raises:
            grpc.RpcError: If invocation fails
        """
        from pixell_runtime.proto import agent_pb2, agent_pb2_grpc

        channel = self.get_agent_channel(deployment_id=deployment_id)
        stub = agent_pb2_grpc.AgentServiceStub(channel)

        # Build parameters dict from context
        parameters = {"context": context}

        # Generate request ID for tracing
        import time
        request_id = f"req_{int(time.time() * 1000000)}"

        request = agent_pb2.ActionRequest(
            action=action,
            parameters=parameters,
            request_id=request_id
        )
        response = await stub.Invoke(request, timeout=timeout)

        return {
            "success": response.success,
            "response": response.result,
            "error": response.error if response.error else None
        }


# Global singleton
_a2a_client: Optional[A2AClient] = None


def get_a2a_client(prefer_internal: bool = True) -> A2AClient:
    """Get or create global A2A client instance."""
    global _a2a_client
    if _a2a_client is None:
        _a2a_client = A2AClient(prefer_internal=prefer_internal)
    return _a2a_client