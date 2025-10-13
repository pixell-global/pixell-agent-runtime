#!/usr/bin/env python3
"""
Debug A2A connection issues with multiple approaches.
"""

import asyncio
import socket
import sys
import time
from pathlib import Path

# Add the runtime src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import grpc
from pixell_runtime.proto import agent_pb2, agent_pb2_grpc


async def test_tcp_connection(host: str, port: int):
    """Test basic TCP connectivity."""
    print(f"🔌 Testing TCP connection to {host}:{port}")

    try:
        # Test with socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((host, port))
        sock.close()

        if result == 0:
            print(f"✅ TCP connection successful")
            return True
        else:
            print(f"❌ TCP connection failed with error code: {result}")
            return False
    except Exception as e:
        print(f"❌ TCP connection exception: {e}")
        return False


async def test_grpc_insecure(host: str, port: int):
    """Test gRPC connection with insecure channel."""
    print(f"🔐 Testing gRPC insecure connection to {host}:{port}")

    try:
        channel = grpc.aio.insecure_channel(f"{host}:{port}")
        stub = agent_pb2_grpc.AgentServiceStub(channel)

        # Try with longer timeout
        response = await stub.Health(agent_pb2.Empty(), timeout=10.0)
        await channel.close()

        print(f"✅ gRPC Health successful: {response}")
        return True

    except Exception as e:
        print(f"❌ gRPC insecure failed: {e}")
        try:
            await channel.close()
        except:
            pass
        return False


async def test_grpc_with_options(host: str, port: int):
    """Test gRPC with various options."""
    print(f"⚙️ Testing gRPC with options to {host}:{port}")

    # Try different gRPC options
    options = [
        ('grpc.keepalive_time_ms', 30000),
        ('grpc.keepalive_timeout_ms', 5000),
        ('grpc.keepalive_permit_without_calls', True),
        ('grpc.http2.max_pings_without_data', 0),
        ('grpc.http2.min_time_between_pings_ms', 10000),
        ('grpc.http2.min_ping_interval_without_data_ms', 300000)
    ]

    try:
        channel = grpc.aio.insecure_channel(f"{host}:{port}", options=options)
        stub = agent_pb2_grpc.AgentServiceStub(channel)

        response = await stub.Health(agent_pb2.Empty(), timeout=15.0)
        await channel.close()

        print(f"✅ gRPC with options successful: {response}")
        return True

    except Exception as e:
        print(f"❌ gRPC with options failed: {e}")
        try:
            await channel.close()
        except:
            pass
        return False


async def test_multiple_attempts(host: str, port: int):
    """Test with multiple retry attempts."""
    print(f"🔄 Testing multiple attempts to {host}:{port}")

    for attempt in range(3):
        print(f"  Attempt {attempt + 1}/3...")

        try:
            channel = grpc.aio.insecure_channel(f"{host}:{port}")
            stub = agent_pb2_grpc.AgentServiceStub(channel)

            response = await stub.Health(agent_pb2.Empty(), timeout=5.0)
            await channel.close()

            print(f"✅ Attempt {attempt + 1} successful: {response}")
            return True

        except Exception as e:
            print(f"❌ Attempt {attempt + 1} failed: {e}")
            try:
                await channel.close()
            except:
                pass

            if attempt < 2:  # Don't sleep on last attempt
                await asyncio.sleep(2)

    return False


async def main():
    """Main debugging function."""
    if len(sys.argv) > 1:
        host_port = sys.argv[1]
        if ":" in host_port:
            host, port = host_port.split(":", 1)
            port = int(port)
        else:
            host = host_port
            port = 50051
    else:
        host = "pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com"
        port = 50051

    print(f"🐛 A2A Connection Debug Tool")
    print(f"📍 Target: {host}:{port}")
    print("=" * 60)

    # Test 1: Basic TCP connectivity
    tcp_ok = await test_tcp_connection(host, port)
    print()

    # Test 2: Standard gRPC insecure
    grpc_ok = await test_grpc_insecure(host, port)
    print()

    # Test 3: gRPC with options
    grpc_options_ok = await test_grpc_with_options(host, port)
    print()

    # Test 4: Multiple attempts
    retry_ok = await test_multiple_attempts(host, port)
    print()

    print("📊 Summary:")
    print(f"  TCP Connection: {'✅' if tcp_ok else '❌'}")
    print(f"  gRPC Standard: {'✅' if grpc_ok else '❌'}")
    print(f"  gRPC Options: {'✅' if grpc_options_ok else '❌'}")
    print(f"  Multiple Retries: {'✅' if retry_ok else '❌'}")

    if any([tcp_ok, grpc_ok, grpc_options_ok, retry_ok]):
        print("\n🎉 At least one connection method worked!")
    else:
        print("\n😞 All connection methods failed.")
        print("Possible issues:")
        print("  - NLB target is unhealthy")
        print("  - gRPC service not responding to health checks")
        print("  - Network routing issue")
        print("  - Service might be down")


if __name__ == "__main__":
    asyncio.run(main())