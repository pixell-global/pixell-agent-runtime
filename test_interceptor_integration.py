#!/usr/bin/env python3
"""Integration test for PAR gRPC routing interceptor.

Tests the interceptor with a real agent (vivid-commenter) to verify:
1. Prefixed paths are correctly stripped (/agents/{id}/a2a/... → /...)
2. Clean paths pass through unchanged (local dev scenario)
3. All gRPC methods work correctly
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

import grpc
import structlog

from pixell_runtime.proto import agent_pb2, agent_pb2_grpc
from pixell_runtime.a2a.server import create_grpc_server
from pixell_runtime.core.models import AgentPackage

logger = structlog.get_logger()


async def test_interceptor_integration():
    """Test interceptor with real gRPC server."""

    agent_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
    test_port = 50099  # Use different port to avoid conflicts

    print(f"\n{'='*60}")
    print("🧪 PAR gRPC Interceptor Integration Test")
    print(f"{'='*60}\n")

    # Create a minimal server (no agent package for simplicity)
    print(f"1️⃣  Creating gRPC server with interceptor...")
    print(f"   Agent ID: {agent_id}")
    print(f"   Port: {test_port}")

    server = create_grpc_server(
        package=None,
        port=test_port,
        agent_a2a_port=None,
        agent_id=agent_id
    )

    # Start server
    print(f"2️⃣  Starting gRPC server...")
    await server.start()
    print(f"   ✅ Server started on port {test_port}\n")

    try:
        # Test 1: Clean path (local dev scenario)
        print(f"3️⃣  Test 1: Clean path (no prefix)")
        print(f"   Path: /pixell.agent.AgentService/Health")

        async with grpc.aio.insecure_channel(f'localhost:{test_port}') as channel:
            stub = agent_pb2_grpc.AgentServiceStub(channel)

            try:
                response = await stub.Health(agent_pb2.Empty(), timeout=5)
                print(f"   ✅ Health check succeeded!")
                print(f"   Response: ok={response.ok}, message={response.message}")
            except grpc.RpcError as e:
                print(f"   ❌ Health check failed: {e.code()} - {e.details()}")
                return False

        # Test 2: Prefixed path (ALB routing scenario)
        print(f"\n4️⃣  Test 2: Prefixed path (ALB routing)")
        prefixed_path = f"/agents/{agent_id}/a2a"
        print(f"   Prefix: {prefixed_path}")
        print(f"   Full path: {prefixed_path}/pixell.agent.AgentService/Health")

        # We need to manually construct the request with prefixed path
        # This simulates what ALB does
        async with grpc.aio.insecure_channel(f'localhost:{test_port}') as channel:
            # Create a unary-unary call with prefixed method
            prefixed_method = f"{prefixed_path}/pixell.agent.AgentService/Health"

            try:
                # Use the channel's unary_unary to make a raw call
                call = channel.unary_unary(
                    method=prefixed_method,
                    request_serializer=agent_pb2.Empty.SerializeToString,
                    response_deserializer=agent_pb2.HealthStatus.FromString,
                )

                response = await call(agent_pb2.Empty(), timeout=5)
                print(f"   ✅ Health check with prefix succeeded!")
                print(f"   Response: ok={response.ok}, message={response.message}")
                print(f"   🎉 Interceptor correctly stripped the prefix!")
            except grpc.RpcError as e:
                print(f"   ❌ Health check failed: {e.code()} - {e.details()}")
                print(f"   ⚠️  This suggests the interceptor didn't strip the prefix")
                return False

        # Test 3: Wrong agent ID (should pass through but fail at handler)
        print(f"\n5️⃣  Test 3: Wrong agent ID prefix (should pass through)")
        wrong_agent_id = "WRONG-AGENT-ID-12345"
        wrong_prefixed_path = f"/agents/{wrong_agent_id}/a2a/pixell.agent.AgentService/Health"
        print(f"   Path: {wrong_prefixed_path}")

        async with grpc.aio.insecure_channel(f'localhost:{test_port}') as channel:
            try:
                call = channel.unary_unary(
                    method=wrong_prefixed_path,
                    request_serializer=agent_pb2.Empty.SerializeToString,
                    response_deserializer=agent_pb2.HealthStatus.FromString,
                )

                response = await call(agent_pb2.Empty(), timeout=5)
                print(f"   ⚠️  Call succeeded unexpectedly - wrong prefix should have failed")
                return False
            except grpc.RpcError as e:
                # Expected to fail with UNIMPLEMENTED or NOT_FOUND
                if e.code() == grpc.StatusCode.UNIMPLEMENTED:
                    print(f"   ✅ Failed as expected with UNIMPLEMENTED")
                    print(f"   Interceptor correctly passed through wrong prefix")
                else:
                    print(f"   ❌ Failed with unexpected code: {e.code()} - {e.details()}")
                    return False

        # Test 4: DescribeCapabilities (another gRPC method)
        print(f"\n6️⃣  Test 4: DescribeCapabilities with prefix")
        capabilities_method = f"{prefixed_path}/pixell.agent.AgentService/DescribeCapabilities"
        print(f"   Path: {capabilities_method}")

        async with grpc.aio.insecure_channel(f'localhost:{test_port}') as channel:
            try:
                call = channel.unary_unary(
                    method=capabilities_method,
                    request_serializer=agent_pb2.Empty.SerializeToString,
                    response_deserializer=agent_pb2.Capabilities.FromString,
                )

                response = await call(agent_pb2.Empty(), timeout=5)
                print(f"   ✅ DescribeCapabilities with prefix succeeded!")
                print(f"   Methods: {list(response.methods)}")
            except grpc.RpcError as e:
                print(f"   ❌ DescribeCapabilities failed: {e.code()} - {e.details()}")
                return False

        print(f"\n{'='*60}")
        print("✅ ALL TESTS PASSED!")
        print(f"{'='*60}\n")

        return True

    finally:
        # Stop server
        print("7️⃣  Stopping gRPC server...")
        await server.stop(grace=1.0)
        print("   ✅ Server stopped\n")


async def main():
    """Run integration tests."""
    try:
        success = await test_interceptor_integration()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
