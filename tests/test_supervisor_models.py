"""Unit tests for supervisor models."""

import pytest
from datetime import datetime
from pixell_runtime.supervisor.models import (
    AgentStatus,
    Ports,
    DeployRequest,
    UpdateRequest,
    DeleteRequest,
    AgentProcess,
    DeployResponse,
    HealthResponse,
    ListAgentsResponse,
)


def test_agent_status_enum():
    """Test AgentStatus enum values."""
    assert AgentStatus.PENDING == "pending"
    assert AgentStatus.STARTING == "starting"
    assert AgentStatus.RUNNING == "running"
    assert AgentStatus.STOPPING == "stopping"
    assert AgentStatus.STOPPED == "stopped"
    assert AgentStatus.FAILED == "failed"
    assert AgentStatus.UPDATING == "updating"


def test_ports_valid():
    """Test valid Ports creation."""
    ports = Ports(rest=8081, a2a=50052, ui=3001)
    assert ports.rest == 8081
    assert ports.a2a == 50052
    assert ports.ui == 3001


def test_ports_invalid_range():
    """Test Ports validation rejects out-of-range values."""
    # REST port out of range
    with pytest.raises(ValueError):
        Ports(rest=8000, a2a=50052, ui=3001)

    with pytest.raises(ValueError):
        Ports(rest=8101, a2a=50052, ui=3001)

    # A2A port out of range
    with pytest.raises(ValueError):
        Ports(rest=8081, a2a=50000, ui=3001)

    with pytest.raises(ValueError):
        Ports(rest=8081, a2a=50100, ui=3001)

    # UI port out of range
    with pytest.raises(ValueError):
        Ports(rest=8081, a2a=50052, ui=3000)

    with pytest.raises(ValueError):
        Ports(rest=8081, a2a=50052, ui=3021)


def test_deploy_request_minimal():
    """Test DeployRequest with minimal required fields."""
    req = DeployRequest(
        agent_app_id="4906eeb7",
        deployment_id="dep_123",
        package_url="s3://pixell-packages/agent.apkg",
        version="1.0.0",  # Required by PAC
        org_id="org-123"  # Required by PAC
    )
    assert req.agent_app_id == "4906eeb7"
    assert req.deployment_id == "dep_123"
    assert req.package_url == "s3://pixell-packages/agent.apkg"
    assert req.version == "1.0.0"
    assert req.org_id == "org-123"
    assert req.package_sha256 is None
    assert req.max_package_size_mb == 100
    assert req.boot_budget_ms == 5000
    assert req.boot_hard_limit_multiplier == 2.0
    assert req.graceful_shutdown_timeout_sec == 30
    assert req.env == {}


def test_deploy_request_full():
    """Test DeployRequest with all fields."""
    req = DeployRequest(
        agent_app_id="4906eeb7",
        deployment_id="dep_123",
        package_url="s3://pixell-packages/agent.apkg",
        package_sha256="abc123",
        version="1.0.0",  # Required by PAC
        org_id="org-123",  # Required by PAC
        max_package_size_mb=200,
        boot_budget_ms=10000,
        boot_hard_limit_multiplier=3.0,
        graceful_shutdown_timeout_sec=60,
        env={"FOO": "bar"}
    )
    assert req.package_sha256 == "abc123"
    assert req.version == "1.0.0"
    assert req.org_id == "org-123"
    assert req.max_package_size_mb == 200
    assert req.boot_budget_ms == 10000
    assert req.boot_hard_limit_multiplier == 3.0
    assert req.graceful_shutdown_timeout_sec == 60
    assert req.env == {"FOO": "bar"}


def test_update_request():
    """Test UpdateRequest."""
    req = UpdateRequest(
        agent_app_id="4906eeb7",
        deployment_id="dep_456",
        package_url="s3://pixell-packages/agent_v2.apkg"
    )
    assert req.agent_app_id == "4906eeb7"
    assert req.deployment_id == "dep_456"
    assert req.package_url == "s3://pixell-packages/agent_v2.apkg"


def test_delete_request():
    """Test DeleteRequest."""
    req = DeleteRequest(agent_app_id="4906eeb7")
    assert req.agent_app_id == "4906eeb7"
    assert req.force is False
    assert req.cleanup_user is True

    req2 = DeleteRequest(agent_app_id="4906eeb7", force=True, cleanup_user=False)
    assert req2.force is True
    assert req2.cleanup_user is False


def test_agent_process():
    """Test AgentProcess model."""
    now = datetime.now()
    ports = Ports(rest=8081, a2a=50052, ui=3001)

    process = AgentProcess(
        agent_app_id="4906eeb7",
        deployment_id="dep_123",
        status=AgentStatus.RUNNING,
        ports=ports,
        pid=12345,
        linux_user="agent_4906eeb7",
        package_path="/home/agent_4906eeb7/packages/agent.apkg",
        package_url="s3://pixell-packages/agent.apkg",
        created_at=now,
        started_at=now,
    )

    assert process.agent_app_id == "4906eeb7"
    assert process.status == AgentStatus.RUNNING
    assert process.ports.rest == 8081
    assert process.pid == 12345
    assert process.linux_user == "agent_4906eeb7"


def test_deploy_response():
    """Test DeployResponse model."""
    now = datetime.now()
    ports = Ports(rest=8081, a2a=50052, ui=3001)

    response = DeployResponse(
        agent_app_id="4906eeb7",
        deployment_id="dep_123",
        status=AgentStatus.STARTING,
        ports=ports,
        linux_user="agent_4906eeb7",
        message="Agent deployment initiated",
        created_at=now,
    )

    assert response.agent_app_id == "4906eeb7"
    assert response.status == AgentStatus.STARTING
    assert response.ports.rest == 8081


def test_health_response():
    """Test HealthResponse model."""
    now = datetime.now()
    response = HealthResponse(
        ok=True,
        agents_running=3,
        agents_total=5,
        available_ports=15,
        timestamp=now,
    )

    assert response.ok is True
    assert response.agents_running == 3
    assert response.agents_total == 5
    assert response.available_ports == 15


def test_list_agents_response():
    """Test ListAgentsResponse model."""
    now = datetime.now()
    ports = Ports(rest=8081, a2a=50052, ui=3001)

    process = AgentProcess(
        agent_app_id="4906eeb7",
        deployment_id="dep_123",
        status=AgentStatus.RUNNING,
        ports=ports,
        linux_user="agent_4906eeb7",
        package_path="/home/agent_4906eeb7/packages/agent.apkg",
        package_url="s3://pixell-packages/agent.apkg",
        created_at=now,
    )

    response = ListAgentsResponse(
        agents=[process],
        total=1,
        timestamp=now,
    )

    assert response.total == 1
    assert len(response.agents) == 1
    assert response.agents[0].agent_app_id == "4906eeb7"
