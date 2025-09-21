#!/usr/bin/env python3
"""Test script to load and invoke the Python agent."""

import asyncio
import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from pixell_runtime.agents.manager import AgentManager
from pixell_runtime.core.models import InvocationRequest


async def test_python_agent():
    """Test the Python agent."""
    print("=== Testing Python Agent ===")
    
    # Create agent manager
    packages_dir = Path("/tmp/pixell-runtime-test/packages")
    manager = AgentManager(packages_dir)
    
    # Load the APKG
    apkg_path = Path("pixell-python-agent-0.1.0.apkg")
    print(f"\n1. Loading package: {apkg_path}")
    
    try:
        package = await manager.load_package(apkg_path)
        print(f"   ✓ Package loaded: {package.id}")
        print(f"   - Status: {package.status}")
        print(f"   - Path: {package.path}")
    except Exception as e:
        print(f"   ✗ Failed to load package: {e}")
        return
    
    # List agents
    print("\n2. Available agents:")
    agents = manager.list_agents()
    for agent in agents:
        print(f"   - {agent.id}")
        print(f"     Name: {agent.export.name}")
        print(f"     Status: {agent.status}")
        print(f"     Private: {agent.export.private}")
    
    # Test invocation
    print("\n3. Testing agent invocation:")
    
    # Find the code-executor agent
    executor_agent = None
    for agent in agents:
        if "code-executor" in agent.id:
            executor_agent = agent
            break
    
    if not executor_agent:
        print("   ✗ Could not find code-executor agent")
        # Try with the first available agent
        if agents:
            executor_agent = agents[0]
            print(f"   Using fallback agent: {executor_agent.id}")
    
    if executor_agent:
        # Create a simple test request
        request = InvocationRequest(
            agent_id=executor_agent.id,
            input={
                "code": "print('Hello from Python agent!')\nresult = 2 + 2",
                "session_id": "test-session"
            }
        )
        
        print(f"   Invoking agent: {executor_agent.id}")
        print(f"   Input: {json.dumps(request.input, indent=2)}")
        
        try:
            response = await manager.invoke_agent(request)
            print(f"   ✓ Invocation successful!")
            print(f"   - Duration: {response.duration_ms:.2f}ms")
            print(f"   - Output: {json.dumps(response.output, indent=2)}")
        except Exception as e:
            print(f"   ✗ Invocation failed: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_python_agent())