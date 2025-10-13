"""Unit tests for PortAllocator."""

import pytest
from pixell_runtime.supervisor.port_allocator import PortAllocator
from pixell_runtime.supervisor.models import Ports


def test_port_allocator_init():
    """Test PortAllocator initialization."""
    allocator = PortAllocator()
    assert allocator.max_agents() == 20
    assert allocator.available_slots() == 20
    assert len(allocator.allocations) == 0


def test_allocate_first_agent():
    """Test allocating ports for first agent."""
    allocator = PortAllocator()
    ports = allocator.allocate("4906eeb7")

    assert ports.rest == 8081
    assert ports.a2a == 50052
    assert ports.ui == 3001
    assert allocator.available_slots() == 19


def test_allocate_multiple_agents():
    """Test allocating ports for multiple agents."""
    allocator = PortAllocator()

    # Allocate for 3 agents
    ports1 = allocator.allocate("agent1")
    ports2 = allocator.allocate("agent2")
    ports3 = allocator.allocate("agent3")

    # Each should get unique ports
    assert ports1.rest == 8081
    assert ports2.rest == 8082
    assert ports3.rest == 8083

    assert ports1.a2a == 50052
    assert ports2.a2a == 50053
    assert ports3.a2a == 50054

    assert ports1.ui == 3001
    assert ports2.ui == 3002
    assert ports3.ui == 3003

    assert allocator.available_slots() == 17


def test_allocate_reuse_existing():
    """Test reusing existing allocation."""
    allocator = PortAllocator()

    # Allocate for agent
    ports1 = allocator.allocate("4906eeb7")
    assert ports1.rest == 8081

    # Allocate again with reuse=True (default)
    ports2 = allocator.allocate("4906eeb7", reuse=True)
    assert ports2.rest == 8081  # Same ports
    assert allocator.available_slots() == 19  # Still only 1 agent


def test_allocate_replace_existing():
    """Test replacing existing allocation."""
    allocator = PortAllocator()

    # Allocate for agent
    ports1 = allocator.allocate("4906eeb7")
    assert ports1.rest == 8081

    # Allocate second agent to consume next port
    allocator.allocate("other_agent")

    # Replace allocation for first agent with reuse=False
    ports2 = allocator.allocate("4906eeb7", reuse=False)
    # Port 8081 was released, so it's available again and will be reused
    assert ports2.rest == 8081  # First available port after release


def test_release_ports():
    """Test releasing allocated ports."""
    allocator = PortAllocator()

    # Allocate and release
    allocator.allocate("4906eeb7")
    assert allocator.available_slots() == 19

    released = allocator.release("4906eeb7")
    assert released is True
    assert allocator.available_slots() == 20


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

        # Verify ports are in range
        assert 8081 <= ports.rest <= 8100
        assert 50052 <= ports.a2a <= 50071
        assert 3001 <= ports.ui <= 3020


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
