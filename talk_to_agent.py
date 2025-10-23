#!/usr/bin/env python3
"""Interactive client to talk to deployed agents via A2A (gRPC)."""

import argparse
import asyncio
import json
import os
import socket
import sys
import time
from typing import Optional, List, Tuple

# CRITICAL: Set DNS resolver BEFORE importing grpc
# This must be done before grpc module is loaded
if 'GRPC_DNS_RESOLVER' not in os.environ:
    os.environ['GRPC_DNS_RESOLVER'] = 'native'

import grpc
import grpc.aio
import structlog

from pixell_runtime.proto import agent_pb2, agent_pb2_grpc

logger = structlog.get_logger()


def resolve_host_with_system_dns(host: str, port: int) -> List[Tuple[str, int]]:
    """Resolve hostname using Python's system DNS resolver.

    This uses socket.getaddrinfo which respects system DNS settings,
    unlike gRPC's C-ares resolver which may timeout.

    Args:
        host: Hostname to resolve
        port: Port number

    Returns:
        List of (ip_address, port) tuples

    Raises:
        socket.gaierror: If DNS resolution fails
    """
    try:
        logger.info("Resolving host with system DNS", host=host, port=port)
        results = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
        addresses = [(addr[4][0], port) for addr in results]
        logger.info("DNS resolution successful", host=host, addresses=[a[0] for a in addresses])
        return addresses
    except socket.gaierror as e:
        logger.error("DNS resolution failed", host=host, error=str(e))
        raise


def print_dns_troubleshooting(host: str):
    """Print helpful DNS troubleshooting information."""
    print(f"\n{'='*60}")
    print("🔧 DNS RESOLUTION TROUBLESHOOTING")
    print(f"{'='*60}")
    print(f"\nThe gRPC client failed to resolve: {host}")
    print("\nThis is a client-side DNS issue with gRPC's C-ares resolver.")
    print("Your AWS infrastructure is likely fine - this is a local DNS problem.")
    print("\n📋 Quick Fixes:")
    print("\n1. Use native DNS resolver (recommended):")
    print("   export GRPC_DNS_RESOLVER=native")
    print(f"   python talk_to_agent.py")
    print("\n2. Connect directly to IP:")
    print(f"   python talk_to_agent.py --direct-ip 18.219.207.35")
    print("\n3. Add to /etc/hosts (development only):")
    print(f"   sudo sh -c 'echo \"18.219.207.35 {host}\" >> /etc/hosts'")
    print("\n4. Check your VPN/firewall:")
    print("   - VPN may be blocking UDP port 53 (DNS)")
    print("   - Firewall may be interfering with DNS queries")
    print(f"\n{'='*60}\n")


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
        # Handle both bytes and string (gRPC can send either)
        if isinstance(client_call_details.method, bytes):
            original_method = client_call_details.method.decode('utf-8')
        else:
            original_method = client_call_details.method

        # Must encode to bytes (gRPC requires bytes for method field)
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

    def __init__(
        self,
        host: str = "par.pixell.global",
        port: int = 443,
        agent_app_id: str = None,
        dns_strategy: str = "auto",
        direct_ip: Optional[str] = None,
        timeout: float = 10.0,
        retries: int = 3,
        verbose: bool = False
    ):
        """Initialize the agent client with DNS fallback strategies.

        Args:
            host: Hostname of the PAR instance
            port: Port for gRPC (443 for HTTPS/TLS)
            agent_app_id: The agent app ID for path-based routing
            dns_strategy: DNS resolution strategy ('auto', 'native', 'c-ares', 'system')
            direct_ip: Direct IP address to connect to (bypasses DNS)
            timeout: Connection timeout in seconds
            retries: Number of connection retry attempts
            verbose: Enable verbose logging
        """
        self.host = host
        self.port = port
        self.agent_app_id = agent_app_id
        self.dns_strategy = dns_strategy
        self.timeout = timeout
        self.retries = retries
        self.verbose = verbose

        # Create interceptor for ALB path-based routing if agent_app_id provided
        interceptors = []
        if agent_app_id:
            path_prefix = f"/agents/{agent_app_id}/a2a"
            interceptor = PathPrefixInterceptor(path_prefix)
            interceptors.append(interceptor)
            if verbose:
                logger.info("Using path prefix for ALB routing",
                           host=host,
                           port=port,
                           path_prefix=path_prefix)

        # Determine target address and channel options
        target_address, channel_options = self._prepare_connection(host, port, direct_ip)

        # Create channel with appropriate strategy
        if port == 443:
            # Create SSL credentials for TLS
            self.credentials = grpc.ssl_channel_credentials()
            self.channel = grpc.aio.secure_channel(
                target_address,
                self.credentials,
                options=channel_options,
                interceptors=interceptors
            )
        else:
            # Insecure for local testing (direct to container)
            self.channel = grpc.aio.insecure_channel(
                target_address,
                options=channel_options,
                interceptors=interceptors
            )

        self.stub = agent_pb2_grpc.AgentServiceStub(self.channel)

    def _prepare_connection(
        self,
        host: str,
        port: int,
        direct_ip: Optional[str]
    ) -> Tuple[str, List[Tuple[str, any]]]:
        """Prepare connection target and options based on DNS strategy.

        Args:
            host: Hostname to connect to
            port: Port number
            direct_ip: Direct IP to use (if provided)

        Returns:
            Tuple of (target_address, channel_options)
        """
        channel_options = [
            ('grpc.ssl_target_name_override', host),
            ('grpc.default_authority', host),
        ]

        # Strategy 1: Direct IP (highest priority)
        if direct_ip:
            print(f"🔗 Using direct IP: {direct_ip}")
            if self.verbose:
                logger.info("Using direct IP connection", ip=direct_ip, host=host)
            return f"{direct_ip}:{port}", channel_options

        # Strategy 2: Check environment variable GRPC_DNS_RESOLVER
        env_resolver = os.environ.get('GRPC_DNS_RESOLVER')
        if env_resolver:
            print(f"🔗 Using DNS resolver from environment: {env_resolver}")
            if env_resolver == 'native':
                channel_options.append(('grpc.dns_resolver', 'native'))
            if self.verbose:
                logger.info("Using environment DNS resolver", resolver=env_resolver)
            return f"{host}:{port}", channel_options

        # Strategy 3: User-specified DNS strategy
        if self.dns_strategy == 'native':
            print("🔗 Using native DNS resolver (system DNS)")
            channel_options.append(('grpc.dns_resolver', 'native'))
            if self.verbose:
                logger.info("Using native DNS resolver")
            return f"{host}:{port}", channel_options

        elif self.dns_strategy == 'system':
            # Pre-resolve with system DNS, connect to IP
            print("🔗 Pre-resolving with system DNS...")
            try:
                addresses = resolve_host_with_system_dns(host, port)
                resolved_ip = addresses[0][0]
                print(f"✅ Resolved {host} → {resolved_ip}")
                if self.verbose:
                    logger.info("Pre-resolved with system DNS",
                               host=host,
                               ip=resolved_ip)
                return f"{resolved_ip}:{port}", channel_options
            except socket.gaierror as e:
                print(f"❌ System DNS resolution failed: {e}")
                print_dns_troubleshooting(host)
                raise

        elif self.dns_strategy == 'c-ares':
            # Use default C-ares resolver (may timeout)
            print("🔗 Using C-ares DNS resolver (default, may timeout)")
            if self.verbose:
                logger.info("Using C-ares DNS resolver")
            return f"{host}:{port}", channel_options

        # Strategy 4: Auto mode - try native first, then c-ares
        else:  # 'auto'
            print("🔗 Auto mode: trying native DNS resolver first")
            channel_options.append(('grpc.dns_resolver', 'native'))
            if self.verbose:
                logger.info("Using auto DNS strategy (native)")
            return f"{host}:{port}", channel_options

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
        """Get agent capabilities (optional - not all agents implement this).

        Note: Many agents only implement Health() and Invoke() methods.
        This method may raise UNIMPLEMENTED error for basic agents.

        Returns:
            Capabilities dictionary with methods and metadata

        Raises:
            grpc.RpcError: If method not implemented or request fails
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

            # Extract metadata from gRPC response (dict/map)
            metadata = dict(response.metadata) if response.metadata else {}

            result = {
                "success": response.success,
                "result": response.result,
                "error": response.error if response.error else None,
                "request_id": response.request_id,
                "duration_ms": response.duration_ms,
                "metadata": metadata
            }

            logger.info("Agent responded",
                       success=result["success"],
                       duration_ms=result.get("duration_ms"),
                       has_metadata=bool(metadata))

            return result
        except grpc.RpcError as e:
            logger.error("Invocation failed", error=str(e), code=e.code())
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
    except grpc.RpcError as e:
        print(f"⚠️  Health check failed: {e.details()}")
        # Check if it's a DNS error
        if "DNS resolution failed" in str(e.details()) or "DNS" in str(e.details()):
            print("\n⚠️  This looks like a DNS resolution issue!")
            print("Tip: Try running with: python talk_to_agent.py --dns-resolver native")
        print("Continuing anyway...")
    except Exception as e:
        print(f"⚠️  Health check failed: {e}")
        print("Continuing anyway...")

    # Get capabilities (optional - not all agents implement this)
    try:
        print("\n📋 Getting agent capabilities...")
        capabilities = await client.describe_capabilities()
        print(f"Available methods: {', '.join(capabilities.get('methods', []))}")
        if capabilities.get('metadata'):
            print(f"Metadata: {capabilities['metadata']}")
    except grpc.RpcError as e:
        # Silently skip if method not implemented (many agents only implement Health + Invoke)
        if e.code() == grpc.StatusCode.UNIMPLEMENTED:
            print("ℹ️  Agent doesn't support capability introspection (using basic Health + Invoke)")
        else:
            print(f"⚠️  Failed to get capabilities: {e.details()}")
            # Check if it's a DNS error
            if "DNS resolution failed" in str(e.details()) or "DNS" in str(e.details()):
                print("\n⚠️  This looks like a DNS resolution issue!")
                print("Tip: Try running with: python talk_to_agent.py --dns-resolver native")
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

                # Display metadata (UI data) if present
                metadata = result.get('metadata', {})
                if metadata:
                    print("\n" + "-" * 60)
                    print("📋 Activity/UI Metadata:")
                    for key, value in metadata.items():
                        if key == 'html':
                            # Truncate HTML for display
                            preview = value[:200] + "..." if len(value) > 200 else value
                            print(f"  {key}: {preview}")
                        elif key == 'url':
                            print(f"  {key}: {value}")
                        else:
                            print(f"  {key}: {value}")
                    print("-" * 60)

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
            # Check if it's a DNS error
            if "DNS resolution failed" in str(e.details()) or "DNS" in str(e.details()):
                print("\n⚠️  This looks like a DNS resolution issue!")
                print("Tip: Try running with: python talk_to_agent.py --dns-resolver native")
                print("Or use --help to see all DNS troubleshooting options")
        except Exception as e:
            print(f"\n❌ Error: {e}")
            if client.verbose:
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
    """Main entry point with command-line argument support."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Interactive client to talk to deployed agents via A2A (gRPC)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
DNS Troubleshooting:
  If you experience DNS timeouts, try these strategies:

  1. Use native DNS resolver (recommended):
     python talk_to_agent.py --dns-resolver native

  2. Connect directly to IP:
     python talk_to_agent.py --direct-ip 18.219.207.35

  3. Pre-resolve with system DNS:
     python talk_to_agent.py --dns-resolver system

  4. Set environment variable:
     export GRPC_DNS_RESOLVER=native
     python talk_to_agent.py

Examples:
  # Auto mode (tries native DNS first)
  python talk_to_agent.py

  # Force native DNS resolver
  python talk_to_agent.py --dns-resolver native

  # Connect to specific agent with direct IP
  python talk_to_agent.py --agent-id abc123 --direct-ip 18.219.207.35

  # Verbose mode for debugging
  python talk_to_agent.py --verbose
        """
    )

    parser.add_argument(
        '--agent-id',
        type=str,
        help='Agent app ID to connect to (will prompt if not provided)'
    )
    parser.add_argument(
        '--host',
        type=str,
        default='par.pixell.global',
        help='Hostname to connect to (default: par.pixell.global)'
    )
    parser.add_argument(
        '--port',
        type=int,
        default=443,
        help='Port to connect to (default: 443 for TLS)'
    )
    parser.add_argument(
        '--dns-resolver',
        type=str,
        choices=['auto', 'native', 'c-ares', 'system'],
        default='auto',
        help='DNS resolution strategy (default: auto - tries native first)'
    )
    parser.add_argument(
        '--direct-ip',
        type=str,
        help='Connect directly to this IP address, bypassing DNS'
    )
    parser.add_argument(
        '--timeout',
        type=float,
        default=10.0,
        help='Connection timeout in seconds (default: 10.0)'
    )
    parser.add_argument(
        '--retries',
        type=int,
        default=3,
        help='Number of connection retry attempts (default: 3)'
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    print("🤖 Agent Client (A2A/gRPC)")
    print("=" * 60)

    # Get agent app ID
    if args.agent_id:
        agent_app_id = args.agent_id
        print(f"Agent ID: {agent_app_id}")
    else:
        # Prompt for agent app ID with default
        default_agent_app_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
        agent_app_id = input(f"Enter agent app ID [{default_agent_app_id}]: ").strip()

        if not agent_app_id:
            agent_app_id = default_agent_app_id
            print(f"Using default: {agent_app_id}")

    print(f"\n🔗 Connecting to {args.host}:{args.port}")
    print(f"📍 Agent path: /agents/{agent_app_id}/a2a")

    if args.verbose:
        print(f"🔧 DNS strategy: {args.dns_resolver}")
        if args.direct_ip:
            print(f"🔧 Direct IP: {args.direct_ip}")
        print(f"🔧 Timeout: {args.timeout}s")
        print(f"🔧 Retries: {args.retries}")

    try:
        # Create client with configured options
        client = AgentClient(
            host=args.host,
            port=args.port,
            agent_app_id=agent_app_id,
            dns_strategy=args.dns_resolver,
            direct_ip=args.direct_ip,
            timeout=args.timeout,
            retries=args.retries,
            verbose=args.verbose
        )

        try:
            # Always run interactive mode
            await interactive_mode(client, agent_app_id)
        finally:
            await client.close()

    except socket.gaierror as e:
        print(f"\n❌ DNS Resolution Failed: {e}")
        print_dns_troubleshooting(args.host)
        sys.exit(1)
    except grpc.RpcError as e:
        print(f"\n❌ gRPC Connection Failed: {e.details()}")
        print(f"Code: {e.code()}")
        # Check if it's a DNS-related error
        if "DNS resolution failed" in str(e.details()) or "DNS" in str(e.details()):
            print_dns_troubleshooting(args.host)
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected Error: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
