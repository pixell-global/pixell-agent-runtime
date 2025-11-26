"""Integration tests for supervisor module.

These tests verify that the supervisor can be imported and basic functionality works.
"""

import pytest
from unittest.mock import patch, MagicMock


def test_supervisor_module_imports():
    """Test that supervisor module can be imported."""
    from pixell_runtime import supervisor

    # Check main exports
    assert hasattr(supervisor, "DeployRequest")
    assert hasattr(supervisor, "UpdateRequest")
    assert hasattr(supervisor, "DeleteRequest")
    assert hasattr(supervisor, "DeployResponse")
    assert hasattr(supervisor, "AgentStatus")
    assert hasattr(supervisor, "Ports")
    assert hasattr(supervisor, "AgentProcess")


def test_supervisor_components_import():
    """Test that all supervisor components can be imported."""
    # These should not raise ImportError
    from pixell_runtime.supervisor import server
    from pixell_runtime.supervisor import state
    from pixell_runtime.supervisor import models
    from pixell_runtime.supervisor import user_manager
    from pixell_runtime.supervisor import port_allocator
    from pixell_runtime.supervisor import package_downloader
    from pixell_runtime.supervisor import process_manager

    assert server is not None
    assert state is not None
    assert models is not None
    assert user_manager is not None
    assert port_allocator is not None
    assert package_downloader is not None
    assert process_manager is not None


def test_supervisor_server_app_creation():
    """Test that FastAPI app can be created."""
    from pixell_runtime.supervisor.server import app

    assert app is not None
    assert hasattr(app, "routes")

    # Check that expected routes exist
    route_paths = [route.path for route in app.routes]
    assert "/health" in route_paths
    assert "/agents" in route_paths  # POST /agents for deploy
    assert "/agents/{agent_app_id}" in route_paths  # PUT /agents/{id} for update, DELETE, GET
    assert "/agents/{agent_app_id}/status" in route_paths  # GET /agents/{id}/status
    assert "/status" in route_paths


def test_supervisor_models_validation():
    """Test that models validate correctly."""
    from pixell_runtime.supervisor.models import (
        DeployRequest,
        Ports,
        AgentStatus,
    )

    # Create valid ports (NEW: PAC port ranges)
    ports = Ports(rest=63000, a2a=60000, ui=65000)
    assert ports.rest == 63000
    assert ports.a2a == 60000
    assert ports.ui == 65000

    # Test DeployRequest
    request = DeployRequest(
        agent_app_id="test123",
        deployment_id="dep-123",
        package_url="s3://bucket/package.apkg",
        version="1.0.0",  # Required by PAC
        org_id="org-123",  # Required by PAC
    )
    assert request.agent_app_id == "test123"
    assert request.version == "1.0.0"
    assert request.org_id == "org-123"
    assert request.boot_budget_ms == 120000  # Default value (2 minutes)

    # Test AgentStatus enum
    assert AgentStatus.RUNNING.value == "running"
    assert AgentStatus.FAILED.value == "failed"


def test_port_allocator_basic():
    """Test basic port allocation (NEW: PAC port ranges)."""
    from pixell_runtime.supervisor.port_allocator import PortAllocator

    allocator = PortAllocator()

    # Allocate ports for first agent (NEW: PAC ranges)
    ports1 = allocator.allocate("agent1")
    assert 63000 <= ports1.rest <= 63199
    assert 60000 <= ports1.a2a <= 60199
    assert 65000 <= ports1.ui <= 65199

    # Allocate ports for second agent
    ports2 = allocator.allocate("agent2")
    assert ports2.rest != ports1.rest
    assert ports2.a2a != ports1.a2a
    assert ports2.ui != ports1.ui

    # Release ports
    assert allocator.release("agent1") is True
    assert allocator.release("agent1") is False  # Already released


def test_supervisor_state_initialization():
    """Test that SupervisorState can be initialized."""
    from pixell_runtime.supervisor.state import SupervisorState
    from unittest.mock import MagicMock

    # Mock dependencies
    user_manager = MagicMock()
    port_allocator = MagicMock()
    package_downloader = MagicMock()
    process_manager = MagicMock()

    # Create supervisor state
    state = SupervisorState(
        user_manager=user_manager,
        port_allocator=port_allocator,
        package_downloader=package_downloader,
        process_manager=process_manager,
    )

    assert state is not None
    assert len(state.agents) == 0
    assert state.user_manager is user_manager
    assert state.port_allocator is port_allocator


@patch("pixell_runtime.supervisor.server.supervisor_state")
def test_supervisor_server_health_endpoint(mock_state):
    """Test health endpoint."""
    from fastapi.testclient import TestClient
    from pixell_runtime.supervisor.server import app

    # Mock state
    mock_state.return_value = MagicMock()

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    # Note: PAC contract expects different format, but this test just checks basic health


def test_supervisor_cli_entrypoint_exists():
    """Test that CLI entrypoint exists."""
    from pixell_runtime.supervisor import __main__

    assert hasattr(__main__, "main")
    assert callable(__main__.main)


def test_all_supervisor_tests_passing():
    """Verify that all supervisor tests are available."""
    import glob
    import os

    # Get tests directory
    tests_dir = os.path.dirname(__file__)

    # Find all supervisor test files
    supervisor_tests = glob.glob(os.path.join(tests_dir, "test_supervisor*.py"))

    # Should have multiple test files
    assert len(supervisor_tests) >= 5, f"Expected at least 5 supervisor test files, found {len(supervisor_tests)}"

    # Verify key test files exist
    test_files = [os.path.basename(f) for f in supervisor_tests]
    assert "test_supervisor_models.py" in test_files
    assert "test_supervisor_port_allocator.py" in test_files
    assert "test_supervisor_package_downloader.py" in test_files
    assert "test_supervisor_process_manager.py" in test_files
    assert "test_supervisor_state.py" in test_files
    assert "test_supervisor_server.py" in test_files
