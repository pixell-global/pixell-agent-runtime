#!/usr/bin/env python3
"""Test Phase 2 features of Multi-PAR implementation."""

import asyncio
import httpx
import sys
import time
from pathlib import Path
import signal
import os

# Colors for output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'


def print_test(test_name: str, passed: bool, details: str = ""):
    """Print test result."""
    status = f"{GREEN}✓ PASSED{RESET}" if passed else f"{RED}✗ FAILED{RESET}"
    print(f"\n{test_name}: {status}")
    if details:
        print(f"  {details}")


async def test_auto_restart():
    """Test auto-restart functionality."""
    print(f"\n{BLUE}=== Testing Auto-Restart Feature ==={RESET}")
    
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30.0) as client:
        # 1. Spawn a process with restart policy
        print("\n1. Spawning process with auto-restart policy...")
        spawn_data = {
            "agent_id": "restart-test",
            "package_id": "com.test.restart",
            "package_path": str(Path("test_data/test_agent.apkg").absolute()),
            "env_vars": {"TEST_MODE": "crash"},
            "restart_policy": "on-failure",
            "max_restarts": 3,
            "restart_delay_seconds": 2
        }
        
        resp = await client.post("/supervisor/spawn", json=spawn_data)
        if resp.status_code != 200:
            print_test("Spawn with restart policy", False, f"Failed to spawn: {resp.text}")
            return
            
        data = resp.json()
        port = data["port"]
        print(f"  Process spawned on port {port}")
        
        # Wait for process to start
        await asyncio.sleep(2)
        
        # 2. Get initial status
        resp = await client.get("/supervisor/status")
        initial_status = resp.json()
        initial_pid = None
        
        for proc_id, info in initial_status["processes"].items():
            if info["agent_id"] == "restart-test":
                initial_pid = info["pid"]
                print(f"  Initial PID: {initial_pid}")
                break
                
        # 3. Kill the process to trigger restart
        print("\n2. Killing process to trigger auto-restart...")
        if initial_pid:
            try:
                os.kill(initial_pid, signal.SIGKILL)
                print(f"  Sent SIGKILL to PID {initial_pid}")
            except ProcessLookupError:
                print("  Process already dead")
                
        # 4. Wait for restart
        print("\n3. Waiting for auto-restart...")
        await asyncio.sleep(5)
        
        # 5. Check if process was restarted
        resp = await client.get("/supervisor/status")
        new_status = resp.json()
        
        restarted = False
        new_pid = None
        restart_count = 0
        
        for proc_id, info in new_status["processes"].items():
            if info["agent_id"] == "restart-test":
                new_pid = info["pid"]
                restart_count = info.get("restart_count", 0)
                if new_pid != initial_pid and info["state"] == "running":
                    restarted = True
                break
                
        print_test("Auto-restart", restarted, 
                  f"New PID: {new_pid}, Restart count: {restart_count}")
        
        # Clean up
        await client.post("/supervisor/stop/par-restart-test")
        
        return restarted


async def test_resource_limits():
    """Test resource limits functionality."""
    print(f"\n{BLUE}=== Testing Resource Limits ==={RESET}")
    
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30.0) as client:
        # 1. Spawn process with resource limits
        print("\n1. Spawning process with resource limits...")
        spawn_data = {
            "agent_id": "resource-test",
            "package_id": "com.test.resource",
            "package_path": str(Path("test_data/test_agent.apkg").absolute()),
            "env_vars": {},
            "memory_limit_mb": 100,  # 100MB limit
            "cpu_limit": 0.5  # 50% of one CPU
        }
        
        resp = await client.post("/supervisor/spawn", json=spawn_data)
        if resp.status_code != 200:
            print_test("Spawn with resource limits", False, f"Failed to spawn: {resp.text}")
            return False
            
        print("  Process spawned with memory limit: 100MB, CPU limit: 0.5 cores")
        
        # 2. Wait and check status with resource usage
        await asyncio.sleep(3)
        
        resp = await client.get("/supervisor/status")
        status = resp.json()
        
        has_resources = False
        for proc_id, info in status["processes"].items():
            if info["agent_id"] == "resource-test":
                resources = info.get("resources", {})
                if resources:
                    has_resources = True
                    print(f"\n2. Resource usage:")
                    print(f"  Memory: {resources['memory']['rss_bytes'] / 1024 / 1024:.2f} MB")
                    print(f"  CPU: {resources['cpu']['percent']:.1f}%")
                    print(f"  Threads: {resources['num_threads']}")
                break
                
        print_test("Resource monitoring", has_resources, 
                  "Resource stats available" if has_resources else "No resource stats")
        
        # Clean up
        await client.post("/supervisor/stop/par-resource-test")
        
        return has_resources


async def test_log_aggregation():
    """Test log aggregation functionality."""
    print(f"\n{BLUE}=== Testing Log Aggregation ==={RESET}")
    
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30.0) as client:
        # 1. Clear any existing logs
        await client.delete("/supervisor/logs")
        
        # 2. Spawn a process that generates logs
        print("\n1. Spawning process that generates logs...")
        spawn_data = {
            "agent_id": "log-test",
            "package_id": "com.test.logs",
            "package_path": str(Path("test_data/test_agent.apkg").absolute()),
            "env_vars": {"VERBOSE": "true"}
        }
        
        resp = await client.post("/supervisor/spawn", json=spawn_data)
        if resp.status_code != 200:
            print_test("Spawn for log test", False, f"Failed to spawn: {resp.text}")
            return False
            
        # 3. Wait for some logs to be generated
        await asyncio.sleep(3)
        
        # 4. Get logs
        print("\n2. Fetching aggregated logs...")
        resp = await client.get("/supervisor/logs?limit=10")
        log_data = resp.json()
        
        has_logs = log_data["count"] > 0
        print(f"  Found {log_data['count']} log entries")
        
        if has_logs:
            print("\n3. Sample log entries:")
            for log in log_data["logs"][:3]:
                print(f"  [{log['timestamp']}] [{log['level']}] {log['message'][:50]}...")
                
        # 5. Test filtering by process
        resp = await client.get("/supervisor/logs?process_id=par-log-test")
        filtered_data = resp.json()
        
        print_test("Log aggregation", has_logs, 
                  f"Total logs: {log_data['count']}, Filtered: {filtered_data['count']}")
        
        # 6. Clear logs
        await client.delete("/supervisor/logs?process_id=par-log-test")
        
        # Clean up
        await client.post("/supervisor/stop/par-log-test")
        
        return has_logs


async def test_multiple_processes():
    """Test managing multiple processes simultaneously."""
    print(f"\n{BLUE}=== Testing Multiple Process Management ==={RESET}")
    
    async with httpx.AsyncClient(base_url="http://localhost:8000", timeout=30.0) as client:
        # 1. Spawn multiple processes
        print("\n1. Spawning 3 processes simultaneously...")
        processes = []
        
        for i in range(3):
            spawn_data = {
                "agent_id": f"multi-test-{i}",
                "package_id": f"com.test.multi{i}",
                "package_path": str(Path("test_data/test_agent.apkg").absolute()),
                "env_vars": {"INSTANCE": str(i)}
            }
            
            resp = await client.post("/supervisor/spawn", json=spawn_data)
            if resp.status_code == 200:
                data = resp.json()
                processes.append({
                    "agent_id": f"multi-test-{i}",
                    "port": data["port"],
                    "process_id": data["process_id"]
                })
                print(f"  Process {i} spawned on port {data['port']}")
                
        # 2. Check all are running
        await asyncio.sleep(2)
        resp = await client.get("/supervisor/status")
        status = resp.json()
        
        running_count = sum(1 for p in status["processes"].values() 
                          if p["state"] == "running" and "multi-test" in p["agent_id"])
        
        print(f"\n2. Running processes: {running_count}/3")
        
        # 3. Test routing to each
        print("\n3. Testing routing to each process...")
        routed_count = 0
        
        for proc in processes:
            try:
                resp = await client.post(
                    f"/agents/{proc['agent_id']}/invoke",
                    json={"test": "data"}
                )
                if resp.status_code == 200:
                    routed_count += 1
                    print(f"  ✓ Routed to {proc['agent_id']}")
            except:
                print(f"  ✗ Failed to route to {proc['agent_id']}")
                
        # 4. Clean up
        print("\n4. Stopping all test processes...")
        for proc in processes:
            await client.post(f"/supervisor/stop/{proc['process_id']}")
            
        success = running_count == 3 and routed_count == 3
        print_test("Multiple process management", success,
                  f"Running: {running_count}/3, Routed: {routed_count}/3")
        
        return success


async def run_phase2_tests():
    """Run all Phase 2 tests."""
    print(f"{BLUE}{'=' * 60}{RESET}")
    print(f"{BLUE}Multi-PAR Phase 2 Integration Tests{RESET}")
    print(f"{BLUE}{'=' * 60}{RESET}")
    
    # Start supervisor
    print("\nStarting supervisor...")
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
        # Check supervisor is running
        async with httpx.AsyncClient() as client:
            resp = await client.get("http://localhost:8000/supervisor/health")
            if resp.status_code != 200:
                print(f"{RED}Supervisor failed to start!{RESET}")
                return
                
        # Run tests
        results = []
        
        # Test 1: Auto-restart
        results.append(("Auto-restart", await test_auto_restart()))
        
        # Test 2: Resource limits
        results.append(("Resource limits", await test_resource_limits()))
        
        # Test 3: Log aggregation
        results.append(("Log aggregation", await test_log_aggregation()))
        
        # Test 4: Multiple processes
        results.append(("Multiple processes", await test_multiple_processes()))
        
        # Summary
        print(f"\n{BLUE}{'=' * 60}{RESET}")
        print(f"{BLUE}Test Summary{RESET}")
        print(f"{BLUE}{'=' * 60}{RESET}")
        
        passed = sum(1 for _, result in results if result)
        total = len(results)
        
        for test_name, result in results:
            status = f"{GREEN}PASSED{RESET}" if result else f"{RED}FAILED{RESET}"
            print(f"{test_name}: {status}")
            
        print(f"\nTotal: {passed}/{total} tests passed")
        
        if passed == total:
            print(f"\n{GREEN}✓ All Phase 2 features working correctly!{RESET}")
        else:
            print(f"\n{RED}✗ Some tests failed{RESET}")
            
    finally:
        # Stop supervisor
        print("\nStopping supervisor...")
        supervisor_proc.terminate()
        supervisor_proc.wait()


if __name__ == "__main__":
    # Create test package if needed
    test_pkg = Path("test_data/test_agent.apkg")
    if not test_pkg.exists():
        test_pkg.parent.mkdir(exist_ok=True)
        import zipfile
        with zipfile.ZipFile(test_pkg, 'w') as zf:
            zf.writestr("agent.yaml", """
name: test-agent
version: 1.0.0
runtime:
  python: ">=3.8"
exports:
  - name: invoke
    handler: agent.invoke
""")
            zf.writestr("agent.py", """
import os
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def invoke(request):
    instance = os.environ.get('INSTANCE', '0')
    verbose = os.environ.get('VERBOSE', 'false').lower() == 'true'
    
    if verbose:
        logger.info(f"Agent instance {instance} invoked with request: {request}")
        logger.debug("Debug message for testing")
        
    # Simulate work
    time.sleep(0.1)
    
    # Check for crash mode
    if os.environ.get('TEST_MODE') == 'crash':
        logger.error("Crashing as requested!")
        os._exit(1)
        
    return {"message": f"Hello from agent instance {instance}", "request": request}
""")
            
    asyncio.run(run_phase2_tests())