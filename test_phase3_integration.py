#!/usr/bin/env python3
"""Test Phase 3 - Integration with Python Agent APKG."""

import asyncio
import httpx
import json
import sys
import subprocess
import time
from pathlib import Path
import uvicorn

# Add to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from supervisor import Supervisor


async def test_with_real_apkg():
    """Test with real Python agent APKG."""
    print("=== Phase 3 Integration Test ===\n")
    
    # Check APKG exists
    apkg_path = Path("pixell-python-agent-0.1.0.apkg").absolute()
    if not apkg_path.exists():
        print(f"ERROR: APKG not found at {apkg_path}")
        return False
        
    print(f"✓ Found APKG: {apkg_path}")
    
    # Create supervisor
    config = {
        "base_port": 8001,
        "initial_agents": []
    }
    
    supervisor = Supervisor(config)
    
    # Run supervisor in background
    server = uvicorn.Server(uvicorn.Config(
        app=supervisor.app,
        host="0.0.0.0",
        port=8000,
        log_level="error"
    ))
    
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(2)
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Spawn Python agent
            print("\n1. Spawning Python agent...")
            spawn_data = {
                "agent_id": "python-agent-test",
                "package_id": "pixell-python-agent@0.1.0",
                "package_path": str(apkg_path),
                "env_vars": {}
            }
            
            resp = await client.post("http://localhost:8000/supervisor/spawn", json=spawn_data)
            if resp.status_code != 200:
                print(f"✗ Failed to spawn: {resp.text}")
                return False
                
            data = resp.json()
            port = data["port"]
            print(f"✓ Agent spawned on port {port}")
            
            # 2. Wait for agent to be ready
            print("\n2. Waiting for agent to be ready...")
            ready = False
            for i in range(15):
                await asyncio.sleep(2)
                try:
                    resp = await client.get(f"http://localhost:{port}/health")
                    if resp.status_code == 200:
                        health = resp.json()
                        print(f"  Health: {health.get('agent_status', 'unknown')}")
                        if health.get("status") == "healthy":
                            ready = True
                            break
                except:
                    pass
                    
            if not ready:
                print("✗ Agent failed to become ready")
                return False
                
            print("✓ Agent is ready!")
            
            # 3. Test invocation
            print("\n3. Testing agent invocation...")
            
            # Python code execution test
            test_code = {
                "code": """
# Simple test
result = 2 + 2
print(f"The answer is {result}")
""",
                "session_id": "test-session"
            }
            
            resp = await client.post(
                "http://localhost:8000/agents/python-agent-test/invoke",
                json=test_code
            )
            
            if resp.status_code == 200:
                result = resp.json()
                print("✓ Invocation successful!")
                print(f"  Result: {json.dumps(result, indent=2)}")
            else:
                print(f"✗ Invocation failed: {resp.status_code}")
                print(f"  Response: {resp.text}")
                
            # 4. Test A2A communication
            print("\n4. Testing A2A communication...")
            
            # Spawn second agent
            spawn_data2 = {
                "agent_id": "python-agent-2",
                "package_id": "pixell-python-agent@0.1.0", 
                "package_path": str(apkg_path),
                "env_vars": {"INSTANCE": "2"}
            }
            
            resp = await client.post("http://localhost:8000/supervisor/spawn", json=spawn_data2)
            if resp.status_code == 200:
                print("✓ Second agent spawned")
                await asyncio.sleep(3)
                
                # Test A2A through first agent
                a2a_test = {
                    "code": """
# Import A2A client
from pixell_agent_runtime.a2a_client import get_a2a_client

# Make A2A call
a2a = get_a2a_client()
result = await a2a.call('python-agent-2', 'invoke', {
    'code': 'result = "Hello from agent 2!"',
    'session_id': 'a2a-test'
})

print(f"A2A Response: {result}")
""",
                    "session_id": "a2a-test"
                }
                
                resp = await client.post(
                    "http://localhost:8000/agents/python-agent-test/invoke",
                    json=a2a_test
                )
                
                if resp.status_code == 200:
                    print("✓ A2A communication successful!")
                    print(f"  Response: {resp.json()}")
                else:
                    print("✗ A2A test failed")
                    
            # 5. Check logs
            print("\n5. Checking logs...")
            resp = await client.get("http://localhost:8000/supervisor/logs?limit=5")
            if resp.status_code == 200:
                logs = resp.json()
                print(f"✓ Retrieved {logs['count']} log entries")
                
            return True
            
    finally:
        # Shutdown
        server.should_exit = True
        await asyncio.sleep(1)
        

async def main():
    """Run the test."""
    success = await test_with_real_apkg()
    
    if success:
        print("\n✓ Phase 3 Integration Test PASSED!")
    else:
        print("\n✗ Phase 3 Integration Test FAILED!")
        

if __name__ == "__main__":
    asyncio.run(main())