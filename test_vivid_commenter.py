#!/usr/bin/env python3
"""Test script to invoke vivid-commenter agent via A2A/gRPC."""

import asyncio
import grpc
import json
from pixell_runtime.proto import a2a_pb2, a2a_pb2_grpc


async def test_vivid_commenter():
    """Invoke vivid-commenter agent via gRPC."""

    # Connect to agent A2A server (internal - no external NLB)
    # Agent is running on 10.0.1.201:50053
    agent_address = "10.0.1.201:50053"

    print(f"Connecting to vivid-commenter agent at {agent_address}...")

    async with grpc.aio.insecure_channel(agent_address) as channel:
        stub = a2a_pb2_grpc.AgentToAgentStub(channel)

        # Test 1: Health check via gRPC
        print("\n1. Testing gRPC Health check...")
        try:
            health_req = a2a_pb2.HealthRequest()
            health_resp = await stub.Health(health_req)
            print(f"   ✅ Health: {health_resp}")
        except grpc.RpcError as e:
            print(f"   ❌ Health failed: {e.code()} - {e.details()}")

        # Test 2: Invoke agent with "comment" action
        print("\n2. Invoking agent with 'comment' action...")
        try:
            invoke_req = a2a_pb2.InvokeRequest(
                action="comment",
                context=json.dumps({
                    "code": "def hello():\n    print('world')",
                    "language": "python"
                })
            )
            invoke_resp = await stub.Invoke(invoke_req)
            print(f"   ✅ Response: {invoke_resp.response}")
            if invoke_resp.error:
                print(f"   ⚠️  Error: {invoke_resp.error}")
        except grpc.RpcError as e:
            print(f"   ❌ Invoke failed: {e.code()} - {e.details()}")

        # Test 3: Get agent metadata
        print("\n3. Getting agent metadata...")
        try:
            meta_req = a2a_pb2.MetadataRequest()
            meta_resp = await stub.GetMetadata(meta_req)
            print(f"   ✅ Metadata:")
            print(f"      Agent ID: {meta_resp.agentId}")
            print(f"      Version: {meta_resp.version}")
            print(f"      Actions: {list(meta_resp.actions)}")
        except grpc.RpcError as e:
            print(f"   ❌ Metadata failed: {e.code()} - {e.details()}")


if __name__ == "__main__":
    asyncio.run(test_vivid_commenter())
