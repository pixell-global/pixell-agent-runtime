"""Unit tests for PortAllocator."""

import pytest
from pixell_runtime.supervisor.port_allocator import PortAllocator
from pixell_runtime.supervisor.models import Ports


def test_port_allocator_init():
    """Test PortAllocator initialization (NEW: 200-agent capacity)."""
    allocator = PortAllocator()
    assert allocator.max_agents() == 200  # Updated from 20 to 200
    assert allocator.available_slots() == 200
    assert len(allocator.allocations) == 0


def test_allocate_first_agent():
    """Test allocating ports for first agent (NEW: PAC port ranges)."""
    allocator = PortAllocator()
    ports = allocator.allocate("4906eeb7")

    # New PAC port ranges
    assert ports.rest == 63000  # Updated from 8081
    assert ports.a2a == 60000   # Updated from 50052
    assert ports.ui == 65000    # Updated from 3001
    assert allocator.available_slots() == 199  # Updated from 19


def test_allocate_multiple_agents():
    """Test allocating ports for multiple agents (NEW: PAC port ranges)."""
    allocator = PortAllocator()

    # Allocate for 3 agents
    ports1 = allocator.allocate("agent1")
    ports2 = allocator.allocate("agent2")
    ports3 = allocator.allocate("agent3")

    # Each should get unique ports in new ranges
    assert ports1.rest == 63000  # Updated from 8081
    assert ports2.rest == 63001  # Updated from 8082
    assert ports3.rest == 63002  # Updated from 8083

    assert ports1.a2a == 60000  # Updated from 50052
    assert ports2.a2a == 60001  # Updated from 50053
    assert ports3.a2a == 60002  # Updated from 50054

    assert ports1.ui == 65000  # Updated from 3001
    assert ports2.ui == 65001  # Updated from 3002
    assert ports3.ui == 65002  # Updated from 3003

    assert allocator.available_slots() == 197  # Updated from 17


def test_allocate_reuse_existing():
    """Test reusing existing allocation."""
    allocator = PortAllocator()

    # Allocate for agent
    ports1 = allocator.allocate("4906eeb7")
    assert ports1.rest == 63000

    # Allocate again with reuse=True (default)
    ports2 = allocator.allocate("4906eeb7", reuse=True)
    assert ports2.rest == 63000  # Same ports
    assert allocator.available_slots() == 199  # Still only 1 agent


def test_allocate_replace_existing():
    """Test replacing existing allocation."""
    allocator = PortAllocator()

    # Allocate for agent
    ports1 = allocator.allocate("4906eeb7")
    assert ports1.rest == 63000

    # Allocate second agent to consume next port
    allocator.allocate("other_agent")

    # Replace allocation for first agent with reuse=False
    ports2 = allocator.allocate("4906eeb7", reuse=False)
    # Port 63000 was released, so it's available again and will be reused
    assert ports2.rest == 63000  # First available port after release


def test_release_ports():
    """Test releasing allocated ports."""
    allocator = PortAllocator()

    # Allocate and release
    allocator.allocate("4906eeb7")
    assert allocator.available_slots() == 199

    released = allocator.release("4906eeb7")
    assert released is True
    assert allocator.available_slots() == 200


def test_release_nonexistent():
    """Test releasing ports for nonexistent agent."""
    allocator = PortAllocator()

    released = allocator.release("nonexistent")
    assert released is False


def test_get_allocation():
    """Test getting allocation for agent."""
    allocator = PortAllocator()

    # Before allocation
    assert allocator.get_allocation("4906eeb7") is None

    # After allocation
    ports = allocator.allocate("4906eeb7")
    retrieved = allocator.get_allocation("4906eeb7")
    assert retrieved is not None
    assert retrieved.rest == ports.rest
    assert retrieved.a2a == ports.a2a
    assert retrieved.ui == ports.ui


def test_is_allocated():
    """Test checking if agent has allocation."""
    allocator = PortAllocator()

    assert allocator.is_allocated("4906eeb7") is False

    allocator.allocate("4906eeb7")
    assert allocator.is_allocated("4906eeb7") is True

    allocator.release("4906eeb7")
    assert allocator.is_allocated("4906eeb7") is False


def test_allocate_max_agents():
    """Test allocating maximum number of agents."""
    allocator = PortAllocator()
    max_agents = allocator.max_agents()

    # Allocate max agents
    for i in range(max_agents):
        agent_id = f"agent_{i}"
        ports = allocator.allocate(agent_id)
        assert ports is not None

    assert allocator.available_slots() == 0

    # Try to allocate one more - should fail
    with pytest.raises(RuntimeError, match="No ports available"):
        allocator.allocate("agent_overflow")


def test_get_all_allocations():
    """Test getting all allocations."""
    allocator = PortAllocator()

    # Allocate for 3 agents
    allocator.allocate("agent1")
    allocator.allocate("agent2")
    allocator.allocate("agent3")

    all_allocs = allocator.get_all_allocations()
    assert len(all_allocs) == 3
    assert "agent1" in all_allocs
    assert "agent2" in all_allocs
    assert "agent3" in all_allocs


def test_port_ranges():
    """Test that allocated ports stay within defined ranges."""
    allocator = PortAllocator()

    # Allocate 10 agents
    for i in range(10):
        ports = allocator.allocate(f"agent_{i}")

        # Verify ports are in range (NEW: PAC port ranges)
        assert 63000 <= ports.rest <= 63199
        assert 60000 <= ports.a2a <= 60199
        assert 65000 <= ports.ui <= 65199


def test_allocation_uniqueness():
    """Test that each agent gets unique ports."""
    allocator = PortAllocator()

    # Allocate 5 agents
    allocations = []
    for i in range(5):
        ports = allocator.allocate(f"agent_{i}")
        allocations.append(ports)

    # Check REST ports are unique
    rest_ports = [p.rest for p in allocations]
    assert len(rest_ports) == len(set(rest_ports))

    # Check A2A ports are unique
    a2a_ports = [p.a2a for p in allocations]
    assert len(a2a_ports) == len(set(a2a_ports))

    # Check UI ports are unique
    ui_ports = [p.ui for p in allocations]
    assert len(ui_ports) == len(set(ui_ports))
