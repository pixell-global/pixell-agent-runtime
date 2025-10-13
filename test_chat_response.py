#!/usr/bin/env python3
"""
Test if agent returns real AI responses (not mock).
"""
import asyncio
import grpc
import sys
import json

# Add proto path
sys.path.insert(0, '/Users/syum/dev/pixell-agent-runtime/src')

from pixell_runtime.proto import agent_pb2, agent_pb2_grpc


async def test_chat():
    """Test chat endpoint to verify it returns real AI response."""

    # Connect to deployed agent via PAR's A2A proxy
    # The agent is at port 8081 based on logs
    target = "localhost:8081"

    print(f"🔗 Connecting to agent at {target}")

    async with grpc.aio.insecure_channel(target) as channel:
        stub = agent_pb2_grpc.AgentServiceStub(channel)

        # Test Health first
        try:
            print("\n1️⃣ Testing Health...")
            health_response = await stub.Health(agent_pb2.HealthRequest())
            print(f"✅ Health: ok={health_response.ok}, message={health_response.message}")
        except Exception as e:
            print(f"❌ Health failed: {e}")
            return

        # Test chat invocation
        try:
            print("\n2️⃣ Testing Chat...")
            request = agent_pb2.ActionRequest(
                action="chat",
                parameters={
                    "context": json.dumps({"message": "Say hello in one word"})
                },
                request_id="test-123"
            )

            response = await stub.Invoke(request)

            print(f"\n📥 Response:")
            print(f"  Success: {response.success}")
            print(f"  Duration: {response.duration_ms}ms")

            if response.success:
                result = json.loads(response.result) if response.result else {}
                print(f"  Result: {json.dumps(result, indent=2)}")

                # Check if it's a mock response
                if result.get("mock"):
                    print("\n❌ STILL RETURNING MOCK RESPONSES")
                    print("   Agent doesn't have access to OPENAI_API_KEY")
                else:
                    print("\n✅ REAL AI RESPONSE!")
                    print("   Agent successfully using OPENAI_API_KEY from .env")
            else:
                print(f"  Error: {response.error}")

        except Exception as e:
            print(f"❌ Chat invocation failed: {e}")


if __name__ == "__main__":
    asyncio.run(test_chat())
