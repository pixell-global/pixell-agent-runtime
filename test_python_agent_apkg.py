#!/usr/bin/env python3
"""Test loading and invoking the Python agent APKG."""

import asyncio
import httpx
import sys
import json
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


async def test_python_agent():
    """Test the Python agent APKG."""
    print_status("=== Testing Python Agent APKG ===", "INFO")
    
    # Start supervisor
    print_status("Starting supervisor...")
    import subprocess
    supervisor_proc = subprocess.Popen(
        [sys.executable, "src/run_supervisor.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # Wait for startup
    await asyncio.sleep(3)
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Check supervisor health
            print_status("\n1. Checking supervisor health...")
            resp = await client.get("http://localhost:8000/supervisor/health")
            if resp.status_code != 200:
                print_status("Supervisor not healthy", "ERROR")
                return
            print_status("Supervisor is healthy", "SUCCESS")
            
            # 2. Spawn Python agent
            print_status("\n2. Spawning Python agent...")
            apkg_path = Path("pixell-python-agent-0.1.0.apkg").absolute()
            
            if not apkg_path.exists():
                print_status(f"APKG file not found: {apkg_path}", "ERROR")
                return
                
            spawn_data = {
                "agent_id": "python-agent",
                "package_id": "python-agent@0.1.0",
                "package_path": str(apkg_path),
                "env_vars": {},
                "restart_policy": "on-failure",
                "max_restarts": 3
            }
            
            resp = await client.post(
                "http://localhost:8000/supervisor/spawn",
                json=spawn_data
            )
            
            if resp.status_code != 200:
                print_status(f"Failed to spawn agent: {resp.text}", "ERROR")
                return
                
            data = resp.json()
            port = data["port"]
            print_status(f"Python agent spawned on port {port}", "SUCCESS")
            
            # 3. Wait for agent to be ready
            print_status("\n3. Waiting for agent to be ready...")
            for i in range(10):
                await asyncio.sleep(2)
                
                # Check health directly
                try:
                    health_resp = await client.get(f"http://localhost:{port}/health")
                    if health_resp.status_code == 200:
                        health_data = health_resp.json()
                        if health_data.get("status") == "healthy":
                            print_status("Agent is ready!", "SUCCESS")
                            print_status(f"Package ID: {health_data.get('package_id')}")
                            break
                except:
                    pass
                    
                if i == 9:
                    print_status("Agent failed to become ready", "ERROR")
                    return
                    
            # 4. Test basic invocation through supervisor
            print_status("\n4. Testing basic invocation through supervisor...")
            test_request = {
                "action": "greet",
                "name": "Multi-PAR"
            }
            
            resp = await client.post(
                "http://localhost:8000/agents/python-agent/invoke",
                json=test_request
            )
            
            if resp.status_code == 200:
                result = resp.json()
                print_status("Invocation successful!", "SUCCESS")
                print_status(f"Response: {json.dumps(result, indent=2)}")
            else:
                print_status(f"Invocation failed: {resp.status_code} - {resp.text}", "ERROR")
                
            # 5. Test A2A protocol
            print_status("\n5. Testing A2A protocol...")
            
            # First, spawn a second agent to test A2A
            print_status("Spawning second agent instance...")
            spawn_data2 = {
                "agent_id": "python-agent-2",
                "package_id": "python-agent@0.1.0",
                "package_path": str(apkg_path),
                "env_vars": {"INSTANCE": "2"}
            }
            
            resp = await client.post(
                "http://localhost:8000/supervisor/spawn",
                json=spawn_data2
            )
            
            if resp.status_code == 200:
                print_status("Second agent spawned", "SUCCESS")
                await asyncio.sleep(3)
                
                # Test A2A call from first to second
                a2a_request = {
                    "action": "call_other_agent",
                    "target_agent": "python-agent-2",
                    "method": "invoke",
                    "params": {"action": "greet", "name": "Agent 1"}
                }
                
                resp = await client.post(
                    "http://localhost:8000/agents/python-agent/invoke",
                    json=a2a_request
                )
                
                if resp.status_code == 200:
                    print_status("A2A call successful!", "SUCCESS")
                    print_status(f"A2A Response: {json.dumps(resp.json(), indent=2)}")
                else:
                    print_status("A2A call failed", "WARNING")
                    
            # 6. Check process status
            print_status("\n6. Checking process status...")
            resp = await client.get("http://localhost:8000/supervisor/status")
            if resp.status_code == 200:
                status = resp.json()
                processes = status.get("processes", {})
                print_status(f"Active processes: {len(processes)}")
                
                for proc_id, info in processes.items():
                    print_status(f"  - {proc_id}: {info['state']} on port {info['port']}")
                    if "resources" in info:
                        res = info["resources"]
                        print_status(f"    Memory: {res['memory']['rss_bytes'] / 1024 / 1024:.1f} MB")
                        print_status(f"    CPU: {res['cpu']['percent']:.1f}%")
                        
            # 7. Test specific export
            print_status("\n7. Testing specific export...")
            if apkg_path.exists():
                # Check what exports are available
                resp = await client.post(
                    "http://localhost:8000/agents/python-agent/exports/process_data",
                    json={"data": [1, 2, 3, 4, 5], "operation": "sum"}
                )
                
                if resp.status_code == 200:
                    print_status("Export invocation successful!", "SUCCESS")
                    print_status(f"Result: {resp.json()}")
                elif resp.status_code == 404:
                    print_status("Export not found (this is OK if the agent doesn't have this export)", "WARNING")
                    
            # 8. Check logs
            print_status("\n8. Checking agent logs...")
            resp = await client.get("http://localhost:8000/supervisor/logs?limit=10")
            if resp.status_code == 200:
                logs_data = resp.json()
                print_status(f"Found {logs_data['count']} log entries")
                for log in logs_data["logs"][:5]:
                    print(f"  [{log['timestamp']}] [{log['level']}] {log['message'][:80]}...")
                    
    finally:
        # Clean up
        print_status("\nStopping supervisor...")
        supervisor_proc.terminate()
        supervisor_proc.wait()
        

async def main():
    """Run the test."""
    try:
        await test_python_agent()
        print_status("\n=== Test completed ===", "SUCCESS")
    except Exception as e:
        print_status(f"\nTest failed with error: {e}", "ERROR")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())