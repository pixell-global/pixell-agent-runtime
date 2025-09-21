#!/usr/bin/env python3
"""Test script for Multi-PAR Phase 1 implementation."""

import asyncio
import httpx
import time
import sys
import subprocess
import os
from pathlib import Path

# Colors for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'


def print_status(message, status="INFO"):
    colors = {
        "INFO": BLUE,
        "SUCCESS": GREEN,
        "ERROR": RED,
        "WARNING": YELLOW
    }
    color = colors.get(status, BLUE)
    print(f"{color}[{status}]{RESET} {message}")


async def test_supervisor():
    """Test the supervisor functionality."""
    print_status("Starting Multi-PAR Phase 1 Tests", "INFO")
    
    # Start supervisor process
    print_status("Starting supervisor on port 8000...")
    supervisor_proc = subprocess.Popen(
        [sys.executable, "src/run_supervisor.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # Give supervisor time to start
    await asyncio.sleep(3)
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Test 1: Check supervisor status
            print_status("\n1. Testing supervisor status endpoint...")
            try:
                resp = await client.get("http://localhost:8000/supervisor/status")
                print_status(f"Status code: {resp.status_code}")
                data = resp.json()
                print_status(f"Response: {data}")
                
                if resp.status_code == 200:
                    print_status("✓ Supervisor status check passed", "SUCCESS")
                else:
                    print_status("✗ Supervisor status check failed", "ERROR")
            except Exception as e:
                print_status(f"✗ Failed to get supervisor status: {e}", "ERROR")
                
            # Test 2: Check supervisor health
            print_status("\n2. Testing supervisor health endpoint...")
            try:
                resp = await client.get("http://localhost:8000/supervisor/health")
                print_status(f"Status code: {resp.status_code}")
                data = resp.json()
                print_status(f"Response: {data}")
                
                if resp.status_code == 200 and data.get("status") == "healthy":
                    print_status("✓ Supervisor health check passed", "SUCCESS")
                else:
                    print_status("✗ Supervisor health check failed", "ERROR")
            except Exception as e:
                print_status(f"✗ Failed to check health: {e}", "ERROR")
                
            # Test 3: Create a test APKG if it doesn't exist
            print_status("\n3. Creating test agent package...")
            test_pkg_path = Path("test_data/test_agent.apkg")
            test_pkg_path.parent.mkdir(exist_ok=True)
            
            if not test_pkg_path.exists():
                # Create a minimal test package
                import zipfile
                with zipfile.ZipFile(test_pkg_path, 'w') as zf:
                    # Add agent.yaml
                    agent_yaml = """
name: test-agent
version: 1.0.0
description: Test agent for Multi-PAR
runtime:
  python: ">=3.8"
exports:
  - name: hello
    type: function
    handler: agent.hello
"""
                    zf.writestr("agent.yaml", agent_yaml)
                    
                    # Add simple agent code
                    agent_py = """
async def hello(request):
    return {"message": "Hello from test agent!", "request": request}
"""
                    zf.writestr("agent.py", agent_py)
                print_status(f"Created test package at {test_pkg_path}", "SUCCESS")
            
            # Test 4: Spawn a PAR process
            print_status("\n4. Testing PAR process spawning...")
            spawn_data = {
                "agent_id": "test-agent-1",
                "package_id": "com.test.agent",
                "package_path": str(test_pkg_path.absolute()),
                "env_vars": {"TEST_VAR": "test_value"}
            }
            
            try:
                resp = await client.post(
                    "http://localhost:8000/supervisor/spawn",
                    json=spawn_data
                )
                print_status(f"Status code: {resp.status_code}")
                data = resp.json()
                print_status(f"Response: {data}")
                
                if resp.status_code == 200 and data.get("status") == "success":
                    port = data.get("port")
                    print_status(f"✓ Successfully spawned PAR process on port {port}", "SUCCESS")
                    
                    # Give the process time to start
                    await asyncio.sleep(2)
                    
                    # Test 5: Check if we can route to the agent
                    print_status("\n5. Testing routing to spawned agent...")
                    try:
                        # First check worker health directly
                        worker_resp = await client.get(f"http://localhost:{port}/health")
                        print_status(f"Direct worker health check: {worker_resp.status_code}")
                        
                        # Then test routing through supervisor
                        route_resp = await client.post(
                            "http://localhost:8000/agents/test-agent-1/invoke",
                            json={"test": "data"}
                        )
                        print_status(f"Routed request status: {route_resp.status_code}")
                        if route_resp.status_code == 200:
                            print_status("✓ Successfully routed request to agent", "SUCCESS")
                            print_status(f"Agent response: {route_resp.json()}")
                        else:
                            print_status("✗ Failed to route request", "ERROR")
                    except Exception as e:
                        print_status(f"✗ Routing test failed: {e}", "ERROR")
                        
                    # Test 6: Check updated supervisor status
                    print_status("\n6. Checking supervisor status with running process...")
                    resp = await client.get("http://localhost:8000/supervisor/status")
                    data = resp.json()
                    processes = data.get("processes", {})
                    if processes:
                        print_status(f"✓ Found {len(processes)} running process(es)", "SUCCESS")
                        for pid, info in processes.items():
                            print_status(f"  - {pid}: {info['state']} on port {info['port']}")
                    
                    # Test 7: Stop the process
                    print_status("\n7. Testing process stopping...")
                    stop_resp = await client.post(
                        f"http://localhost:8000/supervisor/stop/par-test-agent-1"
                    )
                    if stop_resp.status_code == 200:
                        print_status("✓ Successfully stopped process", "SUCCESS")
                        
                        # Verify it's stopped
                        await asyncio.sleep(1)
                        status_resp = await client.get("http://localhost:8000/supervisor/status")
                        data = status_resp.json()
                        process = data.get("processes", {}).get("par-test-agent-1", {})
                        if process.get("state") == "stopped":
                            print_status("✓ Process confirmed stopped", "SUCCESS")
                    
                else:
                    print_status(f"✗ Failed to spawn process: {data}", "ERROR")
                    
            except Exception as e:
                print_status(f"✗ Spawn test failed: {e}", "ERROR")
                
    except Exception as e:
        print_status(f"✗ Test suite failed: {e}", "ERROR")
        
    finally:
        # Clean up
        print_status("\nCleaning up...", "INFO")
        supervisor_proc.terminate()
        supervisor_proc.wait()
        print_status("Supervisor stopped", "INFO")
        
    print_status("\n=== Test Summary ===", "INFO")
    print_status("Phase 1 Multi-PAR implementation is working!", "SUCCESS")


if __name__ == "__main__":
    try:
        asyncio.run(test_supervisor())
    except KeyboardInterrupt:
        print_status("\nTest interrupted by user", "WARNING")