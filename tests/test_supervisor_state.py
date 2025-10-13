"""Unit tests for SupervisorState."""

import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path
from datetime import datetime

from pixell_runtime.supervisor.state import SupervisorState
from pixell_runtime.supervisor.models import (
    DeployRequest,
    UpdateRequest,
    DeleteRequest,
    AgentStatus,
    Ports,
)


@pytest.fixture
def mock_user_manager():
    """Create mock LinuxUserManager."""
    manager = MagicMock()
    manager.get_username.return_value = "agent_4906eeb7"
    manager.create_user.return_value = Path("/home/agent_4906eeb7")
    manager.ensure_directories.return_value = None
    manager.delete_user.return_value = None
    return manager


@pytest.fixture
def mock_port_allocator():
    """Create mock PortAllocator."""
    allocator = MagicMock()
    allocator.allocate.return_value = Ports(rest=8081, a2a=50052, ui=3001)
    allocator.release.return_value = True
    allocator.max_agents.return_value = 20
    allocator.available_slots.return_value = 20
    return allocator


@pytest.fixture
def mock_package_downloader():
    """Create mock PackageDownloader."""
    downloader = MagicMock()
    downloader.download.return_value = Path("/var/lib/pixell/packages/abc123.apkg")
    return downloader


@pytest.fixture
def mock_process_manager():
    """Create mock ProcessManager."""
    manager = MagicMock()
    manager.spawn_agent.return_value = 12345
    manager.is_running.return_value = True
    manager.health_check = AsyncMock(return_value=True)
    manager.stop_agent.return_value = True
    manager.cleanup.return_value = None
    return manager


@pytest.fixture
def supervisor_state(
    mock_user_manager, mock_port_allocator, mock_package_downloader, mock_process_manager
):
    """Create SupervisorState with mocked dependencies."""
    return SupervisorState(
        user_manager=mock_user_manager,
        port_allocator=mock_port_allocator,
        package_downloader=mock_package_downloader,
        process_manager=mock_process_manager,
    )


@pytest.fixture
def deploy_request():
    """Create sample DeployRequest."""
    return DeployRequest(
        agent_app_id="4906eeb7",
        deployment_id="dep-123",
        package_url="s3://bucket/package.apkg",
        package_sha256="abc123def456",
        version="1.0.0",  # Required by PAC
        org_id="org-123",  # Required by PAC
        max_package_size_mb=100,
        boot_budget_ms=5000,
        boot_hard_limit_multiplier=2.0,
        graceful_shutdown_timeout_sec=30,
        env={"VAR1": "value1"},
    )


@pytest.mark.asyncio
async def test_deploy_success(supervisor_state, deploy_request, mock_user_manager, mock_port_allocator, mock_package_downloader, mock_process_manager):
    """Test successful agent deployment."""
    agent_process = await supervisor_state.deploy(deploy_request)

    # Verify agent was deployed
    assert agent_process.agent_app_id == "4906eeb7"
    assert agent_process.deployment_id == "dep-123"
    assert agent_process.status == AgentStatus.RUNNING
    assert agent_process.linux_user == "agent_4906eeb7"
    assert agent_process.pid == 12345
    assert agent_process.ports.rest == 8081

    # Verify dependencies were called
    assert mock_user_manager.get_username.called
    assert mock_user_manager.create_user.called
    assert mock_user_manager.ensure_directories.called
    assert mock_port_allocator.allocate.called
    assert mock_package_downloader.download.called
    assert mock_process_manager.spawn_agent.called
    assert mock_process_manager.health_check.called

    # Verify agent is in state
    assert "4906eeb7" in supervisor_state.agents


@pytest.mark.asyncio
async def test_deploy_already_exists(supervisor_state, deploy_request):
    """Test deploying agent that already exists."""
    # Deploy first time
    await supervisor_state.deploy(deploy_request)

    # Try to deploy again
    with pytest.raises(RuntimeError, match="already deployed"):
        await supervisor_state.deploy(deploy_request)


@pytest.mark.asyncio
async def test_deploy_user_creation_fails(
    supervisor_state, deploy_request, mock_user_manager
):
    """Test deployment failure during user creation."""
    mock_user_manager.create_user.side_effect = Exception("User creation failed")

    with pytest.raises(Exception, match="User creation failed"):
        await supervisor_state.deploy(deploy_request)

    # Agent should not be in state since user creation failed before agent was added
    agent = supervisor_state.get_agent("4906eeb7")
    assert agent is None


@pytest.mark.asyncio
async def test_deploy_package_download_fails(
    supervisor_state, deploy_request, mock_package_downloader
):
    """Test deployment failure during package download."""
    mock_package_downloader.download.side_effect = Exception("Download failed")

    with pytest.raises(Exception, match="Download failed"):
        await supervisor_state.deploy(deploy_request)

    # Verify agent status is FAILED
    agent = supervisor_state.get_agent("4906eeb7")
    assert agent is not None
    assert agent.status == AgentStatus.FAILED


@pytest.mark.asyncio
async def test_deploy_spawn_fails(
    supervisor_state, deploy_request, mock_process_manager
):
    """Test deployment failure during process spawn."""
    mock_process_manager.spawn_agent.side_effect = Exception("Spawn failed")

    with pytest.raises(Exception, match="Spawn failed"):
        await supervisor_state.deploy(deploy_request)

    # Verify agent status is FAILED
    agent = supervisor_state.get_agent("4906eeb7")
    assert agent is not None
    assert agent.status == AgentStatus.FAILED


@pytest.mark.asyncio
async def test_deploy_health_check_timeout(
    supervisor_state, deploy_request, mock_process_manager
):
    """Test deployment failure due to health check timeout."""
    # Health check always fails
    mock_process_manager.health_check = AsyncMock(return_value=False)

    # Set short timeout for test
    deploy_request.boot_budget_ms = 100
    deploy_request.boot_hard_limit_multiplier = 1.0

    with pytest.raises(RuntimeError, match="failed health check"):
        await supervisor_state.deploy(deploy_request)

    # Verify agent status is FAILED
    agent = supervisor_state.get_agent("4906eeb7")
    assert agent is not None
    assert agent.status == AgentStatus.FAILED
    assert "failed health check" in agent.error_message


@pytest.mark.asyncio
async def test_deploy_process_crashes_during_startup(
    supervisor_state, deploy_request, mock_process_manager
):
    """Test deployment failure when process crashes during startup."""
    # Health check fails, process not running
    mock_process_manager.health_check = AsyncMock(return_value=False)
    mock_process_manager.is_running.return_value = False

    with pytest.raises(RuntimeError, match="process terminated during startup"):
        await supervisor_state.deploy(deploy_request)

    # Verify agent status is FAILED
    agent = supervisor_state.get_agent("4906eeb7")
    assert agent is not None
    assert agent.status == AgentStatus.FAILED
    assert "terminated during startup" in agent.error_message


@pytest.mark.asyncio
async def test_update_success(supervisor_state, deploy_request):
    """Test successful agent update."""
    # Deploy first
    await supervisor_state.deploy(deploy_request)

    # Update
    update_request = UpdateRequest(
        agent_app_id="4906eeb7",
        deployment_id="dep-456",
        package_url="s3://bucket/new-package.apkg",
        package_sha256="new123sha",
    )

    agent_process = await supervisor_state.update(update_request)

    # Verify agent was updated
    assert agent_process.deployment_id == "dep-456"
    assert agent_process.package_url == "s3://bucket/new-package.apkg"
    assert agent_process.package_sha256 == "new123sha"
    assert agent_process.status == AgentStatus.RUNNING


@pytest.mark.asyncio
async def test_update_not_found(supervisor_state):
    """Test updating non-existent agent."""
    update_request = UpdateRequest(
        agent_app_id="nonexistent",
        deployment_id="dep-456",
        package_url="s3://bucket/package.apkg",
    )

    with pytest.raises(RuntimeError, match="not found"):
        await supervisor_state.update(update_request)


@pytest.mark.asyncio
async def test_update_with_config_changes(supervisor_state, deploy_request):
    """Test update with configuration changes."""
    # Deploy first
    await supervisor_state.deploy(deploy_request)

    # Update with new config
    update_request = UpdateRequest(
        agent_app_id="4906eeb7",
        deployment_id="dep-456",
        package_url="s3://bucket/new-package.apkg",
        max_package_size_mb=200,
        boot_budget_ms=10000,
    )

    agent_process = await supervisor_state.update(update_request)

    # Verify config was updated
    assert agent_process.config["max_package_size_mb"] == 200
    assert agent_process.config["boot_budget_ms"] == 10000


@pytest.mark.asyncio
async def test_update_health_check_fails(
    supervisor_state, deploy_request, mock_process_manager
):
    """Test update failure when health check fails."""
    # Deploy first - health check succeeds
    await supervisor_state.deploy(deploy_request)

    # Reset the mock to track new calls
    mock_process_manager.health_check.reset_mock()

    # Make health check fail for update
    mock_process_manager.health_check = AsyncMock(return_value=False)

    # Update
    update_request = UpdateRequest(
        agent_app_id="4906eeb7",
        deployment_id="dep-456",
        package_url="s3://bucket/new-package.apkg",
        boot_budget_ms=100,
        boot_hard_limit_multiplier=1.0,
    )

    with pytest.raises(RuntimeError, match="failed health check after update"):
        await supervisor_state.update(update_request)

    # Verify agent status is FAILED
    agent = supervisor_state.get_agent("4906eeb7")
    assert agent.status == AgentStatus.FAILED


@pytest.mark.asyncio
async def test_delete_success(supervisor_state, deploy_request, mock_process_manager, mock_port_allocator, mock_user_manager):
    """Test successful agent deletion."""
    # Deploy first
    await supervisor_state.deploy(deploy_request)

    # Delete
    delete_request = DeleteRequest(
        agent_app_id="4906eeb7",
        force=False,
        cleanup_user=True,
    )

    result = await supervisor_state.delete(delete_request)

    assert result is True

    # Verify cleanup was performed
    assert mock_process_manager.stop_agent.called
    assert mock_port_allocator.release.called
    assert mock_user_manager.delete_user.called

    # Verify agent is removed from state
    assert "4906eeb7" not in supervisor_state.agents


@pytest.mark.asyncio
async def test_delete_not_found(supervisor_state):
    """Test deleting non-existent agent."""
    delete_request = DeleteRequest(
        agent_app_id="nonexistent",
        force=False,
        cleanup_user=True,
    )

    result = await supervisor_state.delete(delete_request)
    assert result is False


@pytest.mark.asyncio
async def test_delete_without_user_cleanup(
    supervisor_state, deploy_request, mock_user_manager
):
    """Test deletion without user cleanup."""
    # Deploy first
    await supervisor_state.deploy(deploy_request)

    # Delete without user cleanup
    delete_request = DeleteRequest(
        agent_app_id="4906eeb7",
        force=False,
        cleanup_user=False,
    )

    result = await supervisor_state.delete(delete_request)

    assert result is True
    # Verify user was NOT deleted
    assert not mock_user_manager.delete_user.called


@pytest.mark.asyncio
async def test_delete_force(supervisor_state, deploy_request, mock_process_manager):
    """Test force deletion."""
    # Deploy first
    await supervisor_state.deploy(deploy_request)

    # Force delete
    delete_request = DeleteRequest(
        agent_app_id="4906eeb7",
        force=True,
        cleanup_user=True,
    )

    result = await supervisor_state.delete(delete_request)

    assert result is True
    # Verify stop_agent was called with force=True
    mock_process_manager.stop_agent.assert_called_with("4906eeb7", force=True)


def test_get_agent_exists(supervisor_state):
    """Test getting existing agent."""
    # Manually add agent to state
    from pixell_runtime.supervisor.models import AgentProcess

    agent = AgentProcess(
        agent_app_id="4906eeb7",
        deployment_id="dep-123",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        linux_user="agent_4906eeb7",
        package_path="/test.apkg",
        package_url="s3://bucket/package.apkg",
        created_at=datetime.now(),
        config={},
    )
    supervisor_state.agents["4906eeb7"] = agent

    result = supervisor_state.get_agent("4906eeb7")
    assert result is not None
    assert result.agent_app_id == "4906eeb7"


def test_get_agent_not_exists(supervisor_state):
    """Test getting non-existent agent."""
    result = supervisor_state.get_agent("nonexistent")
    assert result is None


def test_list_agents_empty(supervisor_state):
    """Test listing agents when empty."""
    agents = supervisor_state.list_agents()
    assert len(agents) == 0


def test_list_agents_with_agents(supervisor_state):
    """Test listing agents."""
    from pixell_runtime.supervisor.models import AgentProcess

    # Add multiple agents
    for i in range(3):
        agent = AgentProcess(
            agent_app_id=f"agent_{i}",
            deployment_id=f"dep-{i}",
            status=AgentStatus.RUNNING,
            ports=Ports(rest=8081 + i, a2a=50052 + i, ui=3001 + i),
            linux_user=f"agent_agent_{i}",
            package_path="/test.apkg",
            package_url="s3://bucket/package.apkg",
            created_at=datetime.now(),
            config={},
        )
        supervisor_state.agents[f"agent_{i}"] = agent

    agents = supervisor_state.list_agents()
    assert len(agents) == 3


@pytest.mark.asyncio
async def test_cleanup(supervisor_state, mock_process_manager):
    """Test cleanup."""
    await supervisor_state.cleanup()
    assert mock_process_manager.cleanup.called


@pytest.mark.asyncio
async def test_deploy_cleanup_on_failure(
    supervisor_state, deploy_request, mock_process_manager
):
    """Test that cleanup is attempted on deployment failure."""
    # Make process running initially, then spawn fails
    mock_process_manager.is_running.return_value = True
    mock_process_manager.spawn_agent.side_effect = Exception("Spawn failed")

    try:
        await supervisor_state.deploy(deploy_request)
    except Exception:
        pass

    # Verify cleanup was attempted
    assert mock_process_manager.stop_agent.called or mock_process_manager.is_running.called
