"""Tests for PAR Supervisor components."""

import asyncio
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from supervisor.models import ProcessState, PARProcess, PortAllocation, ProcessConfig
from supervisor.process_manager import ProcessManager


class TestPortAllocation:
    """Test port allocation functionality."""
    
    def test_allocate_port(self):
        """Test basic port allocation."""
        pa = PortAllocation(start_port=8001, end_port=8003)
        
        # Allocate first port
        port1 = pa.allocate_port("proc1")
        assert port1 == 8001
        assert pa.allocated_ports[8001] == "proc1"
        
        # Allocate second port
        port2 = pa.allocate_port("proc2")
        assert port2 == 8002
        
        # Allocate third port
        port3 = pa.allocate_port("proc3")
        assert port3 == 8003
        
        # No more ports available
        port4 = pa.allocate_port("proc4")
        assert port4 is None
        
    def test_release_port(self):
        """Test port release."""
        pa = PortAllocation(start_port=8001, end_port=8002)
        
        # Allocate all ports
        port1 = pa.allocate_port("proc1")
        port2 = pa.allocate_port("proc2")
        
        # Release first port
        pa.release_port(port1)
        assert 8001 not in pa.allocated_ports
        
        # Can allocate again
        port3 = pa.allocate_port("proc3")
        assert port3 == 8001
        
    def test_get_process_port(self):
        """Test getting port by process ID."""
        pa = PortAllocation()
        
        port = pa.allocate_port("test-proc")
        assert pa.get_process_port("test-proc") == port
        assert pa.get_process_port("unknown") is None


class TestPARProcess:
    """Test PAR process model."""
    
    def test_process_state(self):
        """Test process state tracking."""
        proc = PARProcess(
            process_id="test",
            agent_id="agent1",
            package_id="pkg1",
            port=8001,
            state=ProcessState.STARTING
        )
        
        assert not proc.is_running
        assert proc.uptime is None
        
        proc.state = ProcessState.RUNNING
        assert proc.is_running


@pytest.mark.asyncio
class TestProcessManager:
    """Test process manager functionality."""
    
    async def test_spawn_process(self):
        """Test spawning a process."""
        # This is a basic structure test since we can't actually spawn processes in tests
        pm = ProcessManager()
        
        assert len(pm.processes) == 0
        assert len(pm.port_allocation.allocated_ports) == 0
        
    async def test_process_lifecycle(self):
        """Test process lifecycle management."""
        pm = ProcessManager()
        await pm.start()
        
        # Verify initial state
        assert pm._monitor_task is not None
        assert len(pm.processes) == 0
        
        await pm.stop()
        

if __name__ == "__main__":
    pytest.main([__file__, "-v"])