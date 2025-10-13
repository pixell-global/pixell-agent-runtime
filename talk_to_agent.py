#!/usr/bin/env python3
"""Interactive client to talk to deployed agents via A2A (gRPC)."""

import asyncio
import json
import sys
from typing import Optional

import grpc
import grpc.aio
import structlog

from pixell_runtime.proto import agent_pb2, agent_pb2_grpc

logger = structlog.get_logger()


class PathPrefixInterceptor(grpc.aio.UnaryUnaryClientInterceptor):
    """Interceptor to prepend path prefix to all gRPC calls for ALB routing."""

    def __init__(self, path_prefix: str):
        """Initialize with path prefix.

        Args:
            path_prefix: Path prefix to prepend (e.g., "/agents/{agent_id}/a2a")
        """
        self.path_prefix = path_prefix.rstrip('/')

    async def intercept_unary_unary(self, continuation, client_call_details, request):
        """Intercept and modify the gRPC call path.

        Transforms paths like:
          /pixell.agent.AgentService/Health
        Into:
          /agents/{agent_id}/a2a/pixell.agent.AgentService/Health
        """
        # Modify the method (path) in the call details
        # gRPC method is bytes, so decode -> concatenate -> encode
        original_method = client_call_details.method.decode('utf-8')
        new_method = f"{self.path_prefix}{original_method}".encode('utf-8')

        # Create new call details with modified method
        new_details = grpc.aio.ClientCallDetails(
            method=new_method,
            timeout=client_call_details.timeout,
            metadata=client_call_details.metadata,
            credentials=client_call_details.credentials,
            wait_for_ready=client_call_details.wait_for_ready,
        )

        logger.debug("Rewriting gRPC path",
                    original=original_method,
                    rewritten=new_method.decode('utf-8'))

        return await continuation(new_details, request)


class AgentClient:
    """Client for communicating with agents via A2A (gRPC)."""

    def __init__(self, host: str = "par.pixell.global", port: int = 443, agent_app_id: str = None):
        """Initialize the agent client.

        Args:
            host: Hostname of the PAR instance
            port: Port for gRPC (443 for HTTPS/TLS)
            agent_app_id: The agent app ID for path-based routing
        """
        self.host = host
        self.port = port
        self.agent_app_id = agent_app_id

        # Create interceptor for ALB path-based routing if agent_app_id provided
        interceptors = []
        if agent_app_id:
            path_prefix = f"/agents/{agent_app_id}/a2a"
            interceptor = PathPrefixInterceptor(path_prefix)
            interceptors.append(interceptor)
            logger.info("Using path prefix for ALB routing",
                       host=host,
                       port=port,
                       path_prefix=path_prefix)

        # Create channel with interceptor
        if port == 443:
            # Create SSL credentials for TLS
            self.credentials = grpc.ssl_channel_credentials()
            self.channel = grpc.aio.secure_channel(
                f"{host}:{port}",
                self.credentials,
                options=[
                    ('grpc.ssl_target_name_override', host),
                    ('grpc.default_authority', host),
                ],
                interceptors=interceptors
            )
        else:
            # Insecure for local testing (direct to container)
            self.channel = grpc.aio.insecure_channel(
                f"{host}:{port}",
                interceptors=interceptors
            )

        self.stub = agent_pb2_grpc.AgentServiceStub(self.channel)

    async def check_health(self) -> dict:
        """Check if the agent is healthy.

        Returns:
            Health status dictionary
        """
        try:
            response = await self.stub.Health(agent_pb2.Empty())
            return {
                "ok": response.ok,
                "message": response.message,
                "timestamp": response.timestamp
            }
        except grpc.RpcError as e:
            logger.error("Health check failed", error=str(e), code=e.code())
            raise

    async def describe_capabilities(self) -> dict:
        """Get agent capabilities.

        Returns:
            Capabilities dictionary with methods and metadata
        """
        try:
            response = await self.stub.DescribeCapabilities(agent_pb2.Empty())
            return {
                "methods": list(response.methods),
                "metadata": dict(response.metadata)
            }
        except grpc.RpcError as e:
            logger.error("Failed to describe capabilities", error=str(e), code=e.code())
            raise

    async def invoke(
        self,
        action: str,
        parameters: dict
    ) -> dict:
        """Invoke an action on the agent.

        Args:
            action: The action name (e.g., "chat", "comment")
            parameters: Parameters for the action

        Returns:
            Response from the agent
        """
        # Convert parameters to string dict (protobuf limitation)
        str_params = {k: json.dumps(v) if not isinstance(v, str) else v
                      for k, v in parameters.items()}

        request = agent_pb2.ActionRequest(
            action=action,
            parameters=str_params,
            request_id=""  # Will be generated by agent if empty
        )

        logger.info("Invoking agent via A2A",
                   action=action,
                   parameters=list(parameters.keys()))

        try:
            response = await self.stub.Invoke(request)

            result = {
                "success": response.success,
                "result": response.result,
                "error": response.error if response.error else None,
                "request_id": response.request_id,
                "duration_ms": response.duration_ms
            }

            logger.info("Agent responded",
                       success=result["success"],
                       duration_ms=result.get("duration_ms"))

            return result
        except grpc.RpcError as e:
            logger.error("Invocation failed", error=str(e), code=e.code())
            raise

    async def ping(self) -> dict:
        """Ping the agent to check connectivity.

        Returns:
            Pong response with message and timestamp
        """
        try:
            response = await self.stub.Ping(agent_pb2.Empty())
            return {
                "message": response.message,
                "timestamp": response.timestamp
            }
        except grpc.RpcError as e:
            logger.error("Ping failed", error=str(e), code=e.code())
            raise

    async def close(self):
        """Close the gRPC channel."""
        await self.channel.close()


async def interactive_mode(client: AgentClient, agent_app_id: str):
    """Run interactive mode to chat with the agent.

    Args:
        client: The agent client
        agent_app_id: The agent app ID to talk to
    """
    print(f"\n🤖 Connected to agent: {agent_app_id}")
    print("=" * 60)

    # Check health
    try:
        health = await client.check_health()
        print(f"Health: {'✅ OK' if health.get('ok') else '❌ Not OK'}")
        print(f"Message: {health.get('message', 'N/A')}")
        print(f"Timestamp: {health.get('timestamp')}")
    except Exception as e:
        print(f"⚠️  Health check failed: {e}")
        print("Continuing anyway...")

    # Get capabilities
    try:
        print("\n📋 Getting agent capabilities...")
        capabilities = await client.describe_capabilities()
        print(f"Available methods: {', '.join(capabilities.get('methods', []))}")
        if capabilities.get('metadata'):
            print(f"Metadata: {capabilities['metadata']}")
    except Exception as e:
        print(f"⚠️  Failed to get capabilities: {e}")

    print("\n" + "=" * 60)
    print("Chat with the AI agent (or 'quit' to exit)")
    print("")
    print("You can:")
    print("  - Ask questions: What is Python?")
    print("  - Request code comments: comment:python:def hello(): pass")
    print("  - Just chat naturally!")
    print("=" * 60 + "\n")

    while True:
        try:
            # Get input
            user_input = input("\n💬 You: ").strip()

            if not user_input:
                continue

            if user_input.lower() in ['quit', 'exit', 'q']:
                print("\n👋 Goodbye!")
                break

            # Determine action and parameters based on input format
            # Format: comment:language:code OR just plain text
            if user_input.startswith("comment:") and user_input.count(':') >= 2:
                # Code comment mode: comment:language:code
                parts = user_input.split(':', 2)
                action = "comment"
                language = parts[1].strip()
                code = parts[2].strip()

                if not code:
                    print("⚠️  Code cannot be empty")
                    continue

                parameters = {
                    "code": code,
                    "language": language
                }
            else:
                # Plain conversation mode
                action = "chat"
                parameters = {
                    "message": user_input
                }

            # Invoke agent
            print(f"\n🔄 Sending to agent via A2A...")
            result = await client.invoke(
                action=action,
                parameters=parameters
            )

            # Display result
            print("\n" + "=" * 60)
            if result.get('success'):
                print("✅ Success!")
                print(f"\n{result.get('result', 'No response')}")
                if result.get('duration_ms'):
                    print(f"\n⏱️  Duration: {result['duration_ms']}ms")
            else:
                print("❌ Failed!")
                if result.get('error'):
                    print(f"Error: {result['error']}")
            print("=" * 60)

        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except grpc.RpcError as e:
            print(f"\n❌ gRPC Error: {e.details()}")
            print(f"Code: {e.code()}")
        except Exception as e:
            print(f"\n❌ Error: {e}")
            logger.exception("Invocation failed")


async def single_invocation(
    client: AgentClient,
    language: str,
    code: str
):
    """Make a single invocation to the agent.

    Args:
        client: The agent client
        language: Programming language
        code: Code to comment
    """
    print(f"\n🤖 Invoking agent via A2A")
    print(f"Language: {language}")
    print(f"Code: {code}\n")

    result = await client.invoke(
        action="comment",
        parameters={
            "code": code,
            "language": language
        }
    )

    print("=" * 60)
    if result.get('success'):
        print("✅ Success!")
        print(f"\n{result.get('result', 'No response')}")
        if result.get('duration_ms'):
            print(f"\n⏱️  Duration: {result['duration_ms']}ms")
    else:
        print("❌ Failed!")
        if result.get('error'):
            print(f"Error: {result['error']}")
    print("=" * 60)


async def main():
    """Main entry point."""
    print("🤖 Agent Client (A2A/gRPC)")
    print("=" * 60)

    # Prompt for agent app ID with default
    default_agent_app_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
    agent_app_id = input(f"Enter agent app ID [{default_agent_app_id}]: ").strip()

    if not agent_app_id:
        agent_app_id = default_agent_app_id
        print(f"Using default: {agent_app_id}")

    # Use par.pixell.global with TLS on port 443
    # The ALB will route to the agent based on the :path header in HTTP/2
    host = "par.pixell.global"
    port = 443

    print(f"\n🔗 Connecting to {host}:{port}")
    print(f"📍 Agent path: /agents/{agent_app_id}/a2a")

    # Note: gRPC over HTTP/2 through ALB requires the :path header
    # We'll use metadata to set the path for ALB routing

    client = AgentClient(host=host, port=port, agent_app_id=agent_app_id)

    try:
        # Always run interactive mode
        await interactive_mode(client, agent_app_id)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
