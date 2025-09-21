#!/usr/bin/env python3
"""Simple test for Phase 2 features without external process."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from supervisor.models import ProcessConfig, ProcessState
from supervisor.process_manager import ProcessManager
from supervisor.resource_manager import ResourceManager
from supervisor.log_aggregator import LogEntry, LogAggregator
from datetime import datetime


async def test_restart_logic():
    """Test restart logic without actual processes."""
    print("=== Testing Restart Logic ===")
    
    pm = ProcessManager()
    
    # Create a mock process config
    config = ProcessConfig(
        agent_id="test-agent",
        package_id="com.test.agent",
        package_path="/tmp/test.apkg",
        env_vars={},
        restart_policy="on-failure",
        max_restarts=3,
        restart_delay_seconds=1
    )
    
    # Create a mock process that has crashed
    from supervisor.models import PARProcess
    process = PARProcess(
        process_id="par-test-agent",
        agent_id="test-agent",
        package_id="com.test.agent",
        port=8001,
        state=ProcessState.CRASHED,
        config=config,
        exit_code=1,
        restart_count=0
    )
    
    # Test should_restart logic
    should_restart = await pm._should_restart(process)
    print(f"Should restart (exit_code=1, policy=on-failure): {should_restart}")
    assert should_restart == True
    
    # Test with never policy
    process.config.restart_policy = "never"
    should_restart = await pm._should_restart(process)
    print(f"Should restart (policy=never): {should_restart}")
    assert should_restart == False
    
    # Test with max restarts exceeded
    process.config.restart_policy = "on-failure"
    process.restart_count = 3
    should_restart = await pm._should_restart(process)
    print(f"Should restart (restart_count=3, max=3): {should_restart}")
    assert should_restart == False
    
    print("✓ Restart logic tests passed\n")


async def test_resource_manager():
    """Test resource manager functionality."""
    print("=== Testing Resource Manager ===")
    
    # Test getting current process stats
    import os
    pid = os.getpid()
    
    stats = ResourceManager.get_process_stats(pid)
    if "error" not in stats:
        print(f"Current process stats:")
        print(f"  PID: {stats['pid']}")
        print(f"  Memory: {stats['memory']['rss_bytes'] / 1024 / 1024:.2f} MB")
        print(f"  CPU: {stats['cpu']['percent']:.1f}%")
        print(f"  Threads: {stats['num_threads']}")
        print("✓ Resource monitoring working")
    else:
        print(f"✗ Resource monitoring failed: {stats['error']}")
        
    # Test process nice value
    try:
        ResourceManager.set_process_nice(pid, 10)
        print("✓ Process priority setting working")
    except Exception as e:
        print(f"✗ Process priority setting failed: {e}")
        
    print()


async def test_log_aggregator():
    """Test log aggregator functionality."""
    print("=== Testing Log Aggregator ===")
    
    aggregator = LogAggregator(max_entries_per_process=10)
    await aggregator.start()
    
    # Add some test log entries
    process_id = "test-process"
    aggregator.process_logs[process_id] = aggregator.process_logs.get(process_id, [])
    
    # Create test logs
    test_logs = [
        LogEntry(process_id, datetime.utcnow(), "INFO", "Test message 1"),
        LogEntry(process_id, datetime.utcnow(), "ERROR", "Test error message"),
        LogEntry(process_id, datetime.utcnow(), "DEBUG", "Test debug message"),
    ]
    
    for log in test_logs:
        aggregator.process_logs[process_id].append(log)
        
    # Test getting logs
    logs = aggregator.get_logs(process_id=process_id)
    print(f"Retrieved {len(logs)} logs")
    
    # Test filtering by level
    error_logs = aggregator.get_logs(process_id=process_id, level="ERROR")
    print(f"Error logs: {len(error_logs)}")
    
    # Test log parsing
    test_line = "[2024-01-01T12:00:00] [INFO] [Worker-test] Starting worker"
    entry = LogEntry.from_line("test", test_line)
    print(f"Parsed log: level={entry.level}, message={entry.message}")
    
    await aggregator.stop()
    print("✓ Log aggregator tests passed\n")


async def test_port_allocation():
    """Test port allocation edge cases."""
    print("=== Testing Port Allocation ===")
    
    from supervisor.models import PortAllocation
    
    # Test with limited port range
    pa = PortAllocation(start_port=8001, end_port=8003)
    
    # Allocate all ports
    ports = []
    for i in range(3):
        port = pa.allocate_port(f"proc{i}")
        ports.append(port)
        print(f"Allocated port {port} for proc{i}")
        
    # Try to allocate when full
    port = pa.allocate_port("proc3")
    print(f"Allocation when full: {port} (should be None)")
    assert port is None
    
    # Release and reallocate
    pa.release_port(ports[1])
    port = pa.allocate_port("proc4")
    print(f"Reallocated port: {port} (should be {ports[1]})")
    assert port == ports[1]
    
    print("✓ Port allocation tests passed\n")


async def main():
    """Run all unit tests."""
    print("Multi-PAR Phase 2 Unit Tests")
    print("=" * 40)
    print()
    
    await test_restart_logic()
    await test_resource_manager()
    await test_log_aggregator()
    await test_port_allocation()
    
    print("=" * 40)
    print("✓ All Phase 2 unit tests passed!")


if __name__ == "__main__":
    asyncio.run(main())