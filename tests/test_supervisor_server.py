"""Unit tests for supervisor FastAPI server."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
from fastapi.testclient import TestClient

from pixell_runtime.supervisor.models import (
    DeployRequest,
    UpdateRequest,
    DeleteRequest,
    AgentProcess,
    AgentStatus,
    Ports,
)


@pytest.fixture
def mock_supervisor_state():
    """Create mock SupervisorState."""
    state = MagicMock()
    state.deploy = AsyncMock()
    state.update = AsyncMock()
    state.delete = AsyncMock()
    state.get_agent = MagicMock()
    state.list_agents = MagicMock(return_value=[])
    state.cleanup = AsyncMock()

    # Mock port allocator
    state.port_allocator = MagicMock()
    state.port_allocator.max_agents.return_value = 20
    state.port_allocator.available_slots.return_value = 15

    return state


@pytest.fixture
def client(mock_supervisor_state):
    """Create test client with mocked state."""
    with patch("pixell_runtime.supervisor.server.SupervisorState") as mock_class:
        mock_class.return_value = mock_supervisor_state

        from pixell_runtime.supervisor.server import app

        with TestClient(app) as test_client:
            # Override the global state
            import pixell_runtime.supervisor.server as server_module
            server_module.supervisor_state = mock_supervisor_state

            yield test_client


def test_health_endpoint(client, mock_supervisor_state):
    """Test health check endpoint."""
    # Mock list_agents to return empty list
    mock_supervisor_state.list_agents.return_value = []

    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    # PAC contract expects different fields, but test basic structure
    assert "agents_running" in data
    assert "capacity" in data


def test_deploy_agent_success(client, mock_supervisor_state):
    """Test successful agent deployment."""
    # Mock successful deployment
    agent_process = AgentProcess(
        agent_app_id="4906eeb7",
        deployment_id="dep-123",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        linux_user="agent_4906eeb7",
        package_path="/test.apkg",
        package_url="s3://bucket/package.apkg",
        created_at=datetime.now(),
        pid=12345,
        config={},
    )
    mock_supervisor_state.deploy.return_value = agent_process

    # Send deploy request
    request_data = {
        "agent_app_id": "4906eeb7",
        "deployment_id": "dep-123",
        "package_url": "s3://bucket/package.apkg",
        "package_sha256": "abc123",
        "version": "1.0.0",  # Required by PAC
        "org_id": "org-123",  # Required by PAC
        "max_package_size_mb": 100,
        "boot_budget_ms": 5000,
        "boot_hard_limit_multiplier": 2.0,
        "graceful_shutdown_timeout_sec": 30,
    }

    response = client.post("/agents", json=request_data)  # Changed from /agents/deploy

    assert response.status_code == 201
    data = response.json()
    assert data["agent_app_id"] == "4906eeb7"
    assert data["deployment_id"] == "dep-123"
    assert data["status"] == "running"
    assert data["ports"]["rest"] == 8081
    assert data["pid"] == 12345


def test_deploy_agent_failure(client, mock_supervisor_state):
    """Test deployment failure."""
    mock_supervisor_state.deploy.side_effect = Exception("Deployment failed")

    request_data = {
        "agent_app_id": "4906eeb7",
        "deployment_id": "dep-123",
        "package_url": "s3://bucket/package.apkg",
        "version": "1.0.0",  # Required by PAC
        "org_id": "org-123",  # Required by PAC
    }

    response = client.post("/agents", json=request_data)  # Changed from /agents/deploy

    assert response.status_code == 500
    assert "Deployment failed" in response.json()["detail"]


def test_deploy_agent_invalid_request(client):
    """Test deployment with invalid request data."""
    request_data = {
        "agent_app_id": "4906eeb7",
        # Missing required fields: deployment_id, package_url, version, org_id
    }

    response = client.post("/agents", json=request_data)  # Changed from /agents/deploy

    assert response.status_code == 422  # Validation error


def test_update_agent_success(client, mock_supervisor_state):
    """Test successful agent update."""
    agent_process = AgentProcess(
        agent_app_id="4906eeb7",
        deployment_id="dep-456",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        linux_user="agent_4906eeb7",
        package_path="/test.apkg",
        package_url="s3://bucket/new-package.apkg",
        created_at=datetime.now(),
        pid=12346,
        config={},
    )
    mock_supervisor_state.update.return_value = agent_process

    request_data = {
        "deployment_id": "dep-456",
        "package_url": "s3://bucket/new-package.apkg",
    }

    response = client.put("/agents/4906eeb7", json=request_data)  # Changed to PUT /agents/{id}

    assert response.status_code == 200
    data = response.json()
    assert data["agent_app_id"] == "4906eeb7"
    assert data["deployment_id"] == "dep-456"
    assert data["status"] == "running"


def test_update_agent_not_found(client, mock_supervisor_state):
    """Test updating non-existent agent."""
    mock_supervisor_state.update.side_effect = RuntimeError("Agent not found")

    request_data = {
        "deployment_id": "dep-456",
        "package_url": "s3://bucket/package.apkg",
    }

    response = client.put("/agents/nonexistent", json=request_data)  # Changed to PUT /agents/{id}

    assert response.status_code == 500
    assert "not found" in response.json()["detail"].lower()


def test_delete_agent_success(client, mock_supervisor_state):
    """Test successful agent deletion."""
    mock_supervisor_state.delete.return_value = True

    response = client.delete("/agents/4906eeb7")

    assert response.status_code == 204
    assert mock_supervisor_state.delete.called


def test_delete_agent_with_params(client, mock_supervisor_state):
    """Test deletion with query parameters."""
    mock_supervisor_state.delete.return_value = True

    response = client.delete("/agents/4906eeb7?force=true&cleanup_user=false")

    assert response.status_code == 204

    # Verify parameters were passed
    call_args = mock_supervisor_state.delete.call_args
    request = call_args[0][0]
    assert request.agent_app_id == "4906eeb7"
    assert request.force is True
    assert request.cleanup_user is False


def test_delete_agent_not_found(client, mock_supervisor_state):
    """Test deleting non-existent agent."""
    mock_supervisor_state.delete.return_value = False

    response = client.delete("/agents/nonexistent")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_delete_agent_failure(client, mock_supervisor_state):
    """Test deletion failure."""
    mock_supervisor_state.delete.side_effect = Exception("Deletion failed")

    response = client.delete("/agents/4906eeb7")

    assert response.status_code == 500
    assert "Deletion failed" in response.json()["detail"]


def test_get_agent_success(client, mock_supervisor_state):
    """Test getting agent info."""
    agent_process = AgentProcess(
        agent_app_id="4906eeb7",
        deployment_id="dep-123",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        linux_user="agent_4906eeb7",
        package_path="/test.apkg",
        package_url="s3://bucket/package.apkg",
        created_at=datetime.now(),
        pid=12345,
        config={},
    )
    mock_supervisor_state.get_agent.return_value = agent_process

    response = client.get("/agents/4906eeb7")

    assert response.status_code == 200
    data = response.json()
    assert data["agent_app_id"] == "4906eeb7"
    assert data["status"] == "running"


def test_get_agent_not_found(client, mock_supervisor_state):
    """Test getting non-existent agent."""
    mock_supervisor_state.get_agent.return_value = None

    response = client.get("/agents/nonexistent")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_list_agents_empty(client, mock_supervisor_state):
    """Test listing agents when empty."""
    mock_supervisor_state.list_agents.return_value = []

    response = client.get("/agents")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 0


def test_list_agents_with_agents(client, mock_supervisor_state):
    """Test listing agents."""
    agents = [
        AgentProcess(
            agent_app_id=f"agent_{i}",
            deployment_id=f"dep-{i}",
            status=AgentStatus.RUNNING,
            ports=Ports(rest=8081 + i, a2a=50052 + i, ui=3001 + i),
            linux_user=f"agent_agent_{i}",
            package_path="/test.apkg",
            package_url="s3://bucket/package.apkg",
            created_at=datetime.now(),
            pid=12345 + i,
            config={},
        )
        for i in range(3)
    ]
    mock_supervisor_state.list_agents.return_value = agents

    response = client.get("/agents")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    assert data[0]["agent_app_id"] == "agent_0"
    assert data[1]["agent_app_id"] == "agent_1"
    assert data[2]["agent_app_id"] == "agent_2"


def test_get_status(client, mock_supervisor_state):
    """Test getting supervisor status."""
    agents = [
        AgentProcess(
            agent_app_id="agent_1",
            deployment_id="dep-1",
            status=AgentStatus.RUNNING,
            ports=Ports(rest=8081, a2a=50052, ui=3001),
            linux_user="agent_agent_1",
            package_path="/test.apkg",
            package_url="s3://bucket/package.apkg",
            created_at=datetime.now(),
            pid=12345,
            config={},
        ),
        AgentProcess(
            agent_app_id="agent_2",
            deployment_id="dep-2",
            status=AgentStatus.FAILED,
            ports=Ports(rest=8082, a2a=50053, ui=3002),
            linux_user="agent_agent_2",
            package_path="/test.apkg",
            package_url="s3://bucket/package.apkg",
            created_at=datetime.now(),
            pid=12346,
            config={},
        ),
    ]
    mock_supervisor_state.list_agents.return_value = agents

    response = client.get("/status")

    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "supervisor"
    assert data["healthy"] is True
    assert data["total_agents"] == 2
    assert data["status_counts"]["running"] == 1
    assert data["status_counts"]["failed"] == 1
    assert data["max_agents"] == 20
    assert data["available_slots"] == 15


def test_endpoints_without_initialized_state(client):
    """Test endpoints fail gracefully when state not initialized."""
    import pixell_runtime.supervisor.server as server_module

    # Temporarily set state to None
    original_state = server_module.supervisor_state
    server_module.supervisor_state = None

    try:
        # Test various endpoints
        response = client.post("/agents", json={  # Changed from /agents/deploy
            "agent_app_id": "test",
            "deployment_id": "dep-1",
            "package_url": "s3://bucket/package.apkg",
            "version": "1.0.0",
            "org_id": "org-123",
        })
        assert response.status_code == 503

        response = client.get("/agents/test")
        assert response.status_code == 503

        response = client.get("/agents")
        assert response.status_code == 503

        response = client.delete("/agents/test")
        assert response.status_code == 503

    finally:
        # Restore state
        server_module.supervisor_state = original_state


def test_cors_and_error_handling(client):
    """Test that server handles errors properly."""
    # This tests the global exception handler
    # We can't easily trigger it without mocking, but we can verify the handler exists
    from pixell_runtime.supervisor.server import app

    # Check that exception handler is registered
    assert len(app.exception_handlers) > 0


def test_get_agent_status_for_zombie_process(client, mock_supervisor_state):
    """Test that /agents/{id}/status reports zombies as failed."""
    # Create mock agent
    agent = AgentProcess(
        agent_app_id="zombie-agent",
        deployment_id="dep-123",
        status=AgentStatus.RUNNING,  # Supervisor thinks it's running
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        linux_user="agent_zombie",
        package_path="/test.apkg",
        package_url="s3://bucket/package.apkg",
        created_at=datetime.now(),
        started_at=datetime.now(),
        pid=12345,
        config={},
    )
    mock_supervisor_state.get_agent.return_value = agent

    # Mock process_manager.get_process_health() to report zombie
    mock_process_manager = MagicMock()
    mock_process_manager.get_process_health.return_value = {
        "is_alive": False,
        "is_zombie": True,
        "memory_mb": 0.0,
        "cpu_percent": 0.0,
        "pid": 12345,
    }
    mock_supervisor_state.process_manager = mock_process_manager

    # Get agent status
    response = client.get("/agents/zombie-agent/status")

    assert response.status_code == 200
    data = response.json()

    # Zombie should be reported as failed, not running
    assert data["status"] == "failed"
    assert data["process_id"] == 12345

    # Health should be all False
    assert data["health"]["rest"] is False
    assert data["health"]["a2a"] is False
    assert data["health"]["ui"] is False

    # Metrics should be zero
    assert data["memory_mb"] == 0.0
    assert data["cpu_percent"] == 0.0

    # Uptime should be zero
    assert data["uptime_seconds"] == 0


def test_get_agent_status_for_healthy_process(client, mock_supervisor_state):
    """Test that /agents/{id}/status reports healthy processes correctly."""
    from datetime import datetime

    # Use utcnow consistently
    now = datetime.utcnow()

    # Create mock healthy agent
    agent = AgentProcess(
        agent_app_id="healthy-agent",
        deployment_id="dep-123",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        linux_user="agent_healthy",
        package_path="/test.apkg",
        package_url="s3://bucket/package.apkg",
        created_at=now,
        started_at=now,
        pid=12345,
        config={},
    )
    mock_supervisor_state.get_agent.return_value = agent

    # Mock process_manager.get_process_health() to report healthy
    mock_process_manager = MagicMock()
    mock_process_manager.get_process_health.return_value = {
        "is_alive": True,
        "is_zombie": False,
        "memory_mb": 150.5,
        "cpu_percent": 25.3,
        "pid": 12345,
    }
    mock_supervisor_state.process_manager = mock_process_manager

    # Get agent status
    response = client.get("/agents/healthy-agent/status")

    assert response.status_code == 200
    data = response.json()

    # Should be reported as running
    assert data["status"] == "running"
    assert data["process_id"] == 12345

    # Health should be all True
    assert data["health"]["rest"] is True
    assert data["health"]["a2a"] is True
    assert data["health"]["ui"] is True

    # Metrics should be non-zero
    assert data["memory_mb"] == 150.5
    assert data["cpu_percent"] == 25.3

    # Uptime should be calculated (allow small tolerance for test execution time)
    assert data["uptime_seconds"] >= 0
    assert data["uptime_seconds"] < 10  # Should be very small in test


def test_get_agent_status_for_stopped_process(client, mock_supervisor_state):
    """Test that /agents/{id}/status reports stopped processes correctly."""
    # Create mock stopped agent
    agent = AgentProcess(
        agent_app_id="stopped-agent",
        deployment_id="dep-123",
        status=AgentStatus.STOPPED,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        linux_user="agent_stopped",
        package_path="/test.apkg",
        package_url="s3://bucket/package.apkg",
        created_at=datetime.now(),
        stopped_at=datetime.now(),
        pid=12345,
        config={},
    )
    mock_supervisor_state.get_agent.return_value = agent

    # Mock process_manager.get_process_health() to report not alive
    mock_process_manager = MagicMock()
    mock_process_manager.get_process_health.return_value = {
        "is_alive": False,
        "is_zombie": False,
        "memory_mb": 0.0,
        "cpu_percent": 0.0,
        "pid": 12345,
    }
    mock_supervisor_state.process_manager = mock_process_manager

    # Get agent status
    response = client.get("/agents/stopped-agent/status")

    assert response.status_code == 200
    data = response.json()

    # Should be reported as stopped
    assert data["status"] == "stopped"

    # Health should be all False
    assert data["health"]["rest"] is False
    assert data["health"]["a2a"] is False
    assert data["health"]["ui"] is False

    # Metrics should be zero
    assert data["memory_mb"] == 0.0
    assert data["cpu_percent"] == 0.0

    # Uptime should be zero
    assert data["uptime_seconds"] == 0
