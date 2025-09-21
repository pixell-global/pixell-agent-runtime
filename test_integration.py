#!/usr/bin/env python3
"""Integration test for Multi-PAR Phase 1."""

import asyncio
import httpx
import uvicorn
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent / "src"))

from supervisor import Supervisor


async def run_tests():
    """Run integration tests against the supervisor."""
    print("Running integration tests...")
    
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        # Test 1: Check supervisor status
        print("\n1. Testing supervisor status...")
        resp = await client.get("/supervisor/status")
        assert resp.status_code == 200
        data = resp.json()
        print(f"✓ Status: {data}")
        
        # Test 2: Check health
        print("\n2. Testing supervisor health...")
        resp = await client.get("/supervisor/health")
        assert resp.status_code == 200
        data = resp.json()
        print(f"✓ Health: {data}")
        
        # Test 3: Spawn a test process
        print("\n3. Testing process spawn...")
        spawn_data = {
            "agent_id": "test-agent",
            "package_id": "com.test.agent",
            "package_path": str(Path("test_data/test_agent.apkg").absolute()),
            "env_vars": {"TEST": "true"}
        }
        
        resp = await client.post("/supervisor/spawn", json=spawn_data)
        if resp.status_code == 200:
            data = resp.json()
            print(f"✓ Spawned process on port {data['port']}")
            
            # Wait a bit for process to start
            await asyncio.sleep(2)
            
            # Test 4: Check process status
            print("\n4. Checking process status...")
            resp = await client.get("/supervisor/status")
            data = resp.json()
            processes = data.get("processes", {})
            print(f"✓ Active processes: {len(processes)}")
            for pid, info in processes.items():
                print(f"  - {pid}: {info['state']} on port {info['port']}")
                
            # Test 5: Try routing to agent
            print("\n5. Testing agent routing...")
            try:
                resp = await client.post("/agents/test-agent/invoke", json={"test": "data"})
                if resp.status_code == 200:
                    print(f"✓ Successfully routed to agent: {resp.json()}")
                else:
                    print(f"✗ Routing failed: {resp.status_code}")
            except Exception as e:
                print(f"✗ Routing error: {e}")
                
            # Test 6: Stop process
            print("\n6. Testing process stop...")
            resp = await client.post("/supervisor/stop/par-test-agent")
            if resp.status_code == 200:
                print("✓ Process stopped successfully")
        else:
            print(f"✗ Failed to spawn process: {resp.status_code} - {resp.text}")


async def main():
    """Main entry point."""
    print("=== Multi-PAR Phase 1 Integration Test ===\n")
    
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
        log_level="error"  # Reduce noise
    ))
    
    # Start server in background task
    server_task = asyncio.create_task(server.serve())
    
    # Wait for server to start
    await asyncio.sleep(2)
    
    try:
        # Run tests
        await run_tests()
        print("\n=== All tests completed! ===")
    finally:
        # Shutdown server
        server.should_exit = True
        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())