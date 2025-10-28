"""
PAR gRPC Routing Interceptor

This interceptor is PAR infrastructure - agent developers never interact with it.
It transparently strips ALB routing prefixes before forwarding to agent handlers.

Design principles:
1. Zero performance overhead (no serialization, just string manipulation)
2. Fail-safe (passes through non-prefixed paths for local dev)
3. Logged for debugging
4. Single responsibility (only path manipulation)

Architecture:
    Client Request → ALB → PAR gRPC Server → [Interceptor] → Agent Handler

    Example:
        Input:  /agents/4906eeb7-9959-414e-84c6-f2445822ebe4/a2a/pixell.agent.AgentService/Health
        Output: /pixell.agent.AgentService/Health
"""

import logging
import grpc
from typing import Callable, Any
from collections import namedtuple

import structlog

logger = structlog.get_logger()


# Create a simple HandlerCallDetails-like class for modifying request details
_HandlerCallDetails = namedtuple('_HandlerCallDetails', ['method', 'invocation_metadata'])


class PARRoutingInterceptor(grpc.aio.ServerInterceptor):
    """
    PAR infrastructure interceptor for stripping ALB routing prefixes.

    Transforms:
        /agents/{agent_id}/a2a/pixell.agent.AgentService/Health
        → /pixell.agent.AgentService/Health

    This allows:
    - PAC to route via path-based ALB rules
    - Agent apps to use standard gRPC paths
    - Local development without prefixes

    Usage:
        interceptor = PARRoutingInterceptor(agent_id="4906eeb7-...")
        server = grpc.aio.server(
            futures.ThreadPoolExecutor(),
            interceptors=[interceptor]
        )

    Note: This interceptor is async-compatible (grpc.aio.ServerInterceptor)
    """

    def __init__(self, agent_id: str):
        """
        Initialize interceptor for specific agent.

        Args:
            agent_id: UUID of the agent (used to construct prefix)

        Raises:
            ValueError: If agent_id is None or empty
        """
        if not agent_id:
            raise ValueError("agent_id cannot be None or empty")

        self.agent_id = agent_id
        self.prefix = f"/agents/{agent_id}/a2a"
        self.prefix_len = len(self.prefix)

        logger.info(
            "PAR Routing Interceptor initialized",
            agent_id=agent_id,
            prefix=self.prefix,
        )

    async def intercept_service(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        """
        Intercept incoming gRPC call and strip routing prefix if present.

        This method is called for every gRPC request. It checks if the request
        path has the ALB routing prefix and strips it before forwarding to the
        agent handler.

        Args:
            continuation: Function to invoke the next interceptor or handler
            handler_call_details: Details of the incoming RPC call

        Returns:
            RPC method handler (potentially with modified path)
        """
        try:
            original_method = handler_call_details.method

            # Normalize to string (gRPC can pass method as bytes or string)
            # This handles intermittent type variations from gRPC's internal processing
            if isinstance(original_method, bytes):
                original_method = original_method.decode('utf-8')

            # Check if path has ALB routing prefix
            if original_method.startswith(self.prefix):
                # Strip prefix: /agents/{id}/a2a/pixell... → /pixell...
                stripped_method = original_method[self.prefix_len:]

                logger.debug(
                    "PAR interceptor: stripped routing prefix",
                    original_path=original_method,
                    stripped_path=stripped_method,
                    agent_id=self.agent_id,
                )

                # Create new handler details with clean path
                # Use our namedtuple wrapper that implements the HandlerCallDetails interface
                modified_details = _HandlerCallDetails(
                    method=stripped_method,
                    invocation_metadata=handler_call_details.invocation_metadata,
                )

                # Forward to agent handler with clean path
                return await continuation(modified_details)

            # No prefix (local development, direct call)
            # Pass through unchanged
            logger.debug(
                "PAR interceptor: pass-through (no prefix)",
                path=original_method,
                agent_id=self.agent_id,
            )

            return await continuation(handler_call_details)

        except Exception as e:
            # NEVER crash - log error and pass through unchanged
            # This ensures interceptor failures don't break the agent
            logger.error(
                "PAR interceptor error: passing through unchanged",
                error=str(e),
                agent_id=self.agent_id,
                exc_info=True
            )
            return await continuation(handler_call_details)


class PARLoggingInterceptor(grpc.aio.ServerInterceptor):
    """
    Optional: Log all gRPC calls for debugging.

    This interceptor logs every gRPC request with method name and metadata.
    Useful for debugging routing issues or understanding traffic patterns.

    Usage:
        interceptors = [
            PARRoutingInterceptor(agent_id),  # Routing first!
            PARLoggingInterceptor(),          # Then logging
        ]

    Note: Should be added AFTER routing interceptor to log clean paths
    """

    async def intercept_service(
        self,
        continuation: Callable,
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        """Log incoming gRPC call and forward to handler."""
        try:
            logger.info(
                "gRPC call",
                method=handler_call_details.method,
                metadata=dict(handler_call_details.invocation_metadata or []),
            )
        except Exception as e:
            # Don't fail on logging errors
            logger.warning("Failed to log gRPC call", error=str(e))

        return await continuation(handler_call_details)
