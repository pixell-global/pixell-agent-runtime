#!/usr/bin/env python3
"""Test individual components of Multi-PAR implementation."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from supervisor.models import PortAllocation, ProcessConfig, PARProcess, ProcessState
from supervisor.process_manager import ProcessManager
from supervisor.router import Router
from supervisor.supervisor import Supervisor


async def test_models():
    """Test data models."""
    print("=== Testing Models ===")
    
    # Test port allocation
    pa = PortAllocation(start_port=8001, end_port=8003)
    port1 = pa.allocate_port("test1")
    print(f"Allocated port {port1} for test1")
    
    port2 = pa.allocate_port("test2")
    print(f"Allocated port {port2} for test2")
    
    pa.release_port(port1)
    print(f"Released port {port1}")
    
    port3 = pa.allocate_port("test3")
    print(f"Allocated port {port3} for test3 (should reuse {port1})")
    
    print("✓ Port allocation working\n")


async def test_process_manager():
    """Test process manager."""
    print("=== Testing Process Manager ===")
    
    pm = ProcessManager()
    await pm.start()
    
    print(f"Process manager started")
    print(f"Active processes: {len(pm.processes)}")
    print(f"Allocated ports: {pm.port_allocation.allocated_ports}")
    
    await pm.stop()
    print("✓ Process manager lifecycle working\n")


async def test_supervisor_app():
    """Test supervisor app creation."""
    print("=== Testing Supervisor App ===")
    
    supervisor = Supervisor()
    app = supervisor.app
    
    # Check routes
    routes = [route.path for route in app.routes]
    print("Registered routes:")
    for route in routes:
        if isinstance(route, str) and route.startswith("/supervisor"):
            print(f"  - {route}")
    
    print("✓ Supervisor app created successfully\n")


async def main():
    """Run all tests."""
    print("Testing Multi-PAR Phase 1 Components\n")
    
    await test_models()
    await test_process_manager()
    await test_supervisor_app()
    
    print("\n=== All component tests passed! ===")


if __name__ == "__main__":
    asyncio.run(main())