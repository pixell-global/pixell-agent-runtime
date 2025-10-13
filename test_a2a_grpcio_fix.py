#!/usr/bin/env python3
"""Test A2A connectivity to verify grpcio 1.66.1 fix."""

import asyncio
import json
import sys

# Add src to path
sys.path.insert(0, '/Users/syum/dev/pixell-agent-runtime/src')

from pixell_runtime.a2a.client import get_a2a_client


async def test_a2a_invocation():
    """Test A2A invocation with deployed agent."""

    deployment_id = "7312923e-9a47-43c1-a020-c628287d4c1f"

    print(f"Testing A2A invocation for deployment: {deployment_id}")
    print(f"This test verifies the grpcio 1.66.1 compatibility fix")
    print()

    # Get A2A client (will use local routing)
    client = get_a2a_client(prefer_internal=True)

    # Test 1: Health check
    print("Test 1: A2A Health Check")
    try:
        healthy = await client.health_check(deployment_id=deployment_id)
        print(f"✅ Health check result: {healthy}")
    except Exception as e:
        print(f"❌ Health check failed: {e}")
        import traceback
        traceback.print_exc()
        return

    # Test 2: Invoke agent with comment action
    print("\nTest 2: A2A Invocation (comment action)")
    try:
        result = await client.invoke(
            action="comment",
            context=json.dumps({
                "code": "def hello():\n    print('world')",
                "language": "python"
            }),
            deployment_id=deployment_id,
            timeout=30.0
        )
        print(f"✅ Invocation successful!")
        print(f"Response: {result.get('response', 'No response')[:200]}")
        if result.get('error'):
            print(f"Error: {result['error']}")
    except Exception as e:
        print(f"❌ Invocation failed: {e}")
        import traceback
        traceback.print_exc()

        # Check if it's the grpcio version error
        error_str = str(e)
        if "_registered_method" in error_str:
            print("\n🔴 GRPCIO VERSION MISMATCH DETECTED!")
            print("The '_registered_method' error indicates proto files were generated")
            print("with a newer grpcio-tools than the runtime grpcio version.")
        return

    print("\n✅ All A2A tests passed! grpcio 1.66.1 fix is working correctly.")


if __name__ == "__main__":
    asyncio.run(test_a2a_invocation())
