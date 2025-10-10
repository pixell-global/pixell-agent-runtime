#!/usr/bin/env python3
"""
A2A gRPC client for testing deployed agent communication.
This script demonstrates how to communicate with a deployed agent via A2A.
"""

import asyncio
import sys
import time
from pathlib import Path

# Add the runtime src to path so we can import the protobuf definitions
sys.path.insert(0, str(Path(__file__).parent / "src"))

import grpc
from pixell_runtime.proto import agent_pb2, agent_pb2_grpc


async def test_a2a_communication(host: str = "127.0.0.1", port: int = 50051):
    """Test A2A communication with a deployed agent."""

    # Create gRPC channel
    channel = grpc.aio.insecure_channel(f"{host}:{port}")
    stub = agent_pb2_grpc.AgentServiceStub(channel)

    try:
        print(f"🔗 Connecting to agent at {host}:{port}")

        # 1. Health check
        print("\n1️⃣ Testing Health Check...")
        try:
            health_response = await stub.Health(agent_pb2.Empty())
            print(f"✅ Health: ok={health_response.ok}, message='{health_response.message}'")
        except Exception as e:
            print(f"❌ Health check failed: {e}")
            return

        # 2. Ping test
        print("\n2️⃣ Testing Ping...")
        try:
            ping_response = await stub.Ping(agent_pb2.Empty())
            print(f"✅ Ping: message='{ping_response.message}', timestamp={ping_response.timestamp}")
        except Exception as e:
            print(f"❌ Ping failed: {e}")

        # 3. Describe capabilities
        print("\n3️⃣ Testing Capabilities...")
        try:
            capabilities = await stub.DescribeCapabilities(agent_pb2.Empty())
            print(f"✅ Capabilities:")
            print(f"   Methods: {list(capabilities.methods)}")
            print(f"   Metadata: {dict(capabilities.metadata)}")
        except Exception as e:
            print(f"❌ Capabilities check failed: {e}")

        # 4. Test action invocation
        print("\n4️⃣ Testing Action Invocation...")
        try:
            request = agent_pb2.ActionRequest(
                action="comment",
                parameters={
                    "text": "Hello from A2A gRPC client! Can you help me understand this code?"
                },
                request_id=f"test-{int(time.time())}"
            )

            result = await stub.Invoke(request)
            print(f"✅ Action Result:")
            print(f"   Success: {result.success}")
            print(f"   Result: {result.result}")
            print(f"   Duration: {result.duration_ms}ms")
            if result.error:
                print(f"   Error: {result.error}")
        except Exception as e:
            print(f"❌ Action invocation failed: {e}")

    finally:
        await channel.close()


def main():
    """Main entry point."""
    if len(sys.argv) > 1:
        host_port = sys.argv[1]
        if ":" in host_port:
            host, port = host_port.split(":", 1)
            port = int(port)
        else:
            host = host_port
            port = 50051
    else:
        host = "127.0.0.1"
        port = 50051

    print(f"🤖 Testing A2A communication with deployed agent")
    print(f"📍 Target: {host}:{port}")
    print("=" * 60)

    asyncio.run(test_a2a_communication(host, port))


if __name__ == "__main__":
    main()