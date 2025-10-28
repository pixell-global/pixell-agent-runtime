"""Unit tests for SupervisorState."""

import pytest
import asyncio
import subprocess
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
    manager.clean_agent_files.return_value = None
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
    downloader.extract_package.return_value = Path("/var/lib/pixell/extracted/abc123def456")
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
    # Mock file system operations for deploy.json
    with patch("builtins.open", MagicMock()), \
         patch("json.load", return_value={}), \
         patch("json.dump"), \
         patch("pathlib.Path.exists", return_value=False), \
         patch.object(supervisor_state, "_extract_package_environment", return_value={}):

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
    assert mock_package_downloader.extract_package.called  # New assertion for extraction
    assert mock_process_manager.spawn_agent.called

    # Verify agent is in state
    assert "4906eeb7" in supervisor_state.agents


@pytest.mark.asyncio
async def test_deploy_already_exists_same_deployment_id(supervisor_state, deploy_request):
    """Test deploying agent that already exists with same deployment_id (idempotent)."""
    # Deploy first time
    agent1 = await supervisor_state.deploy(deploy_request)

    # Deploy again with same deployment_id (should be idempotent)
    agent2 = await supervisor_state.deploy(deploy_request)

    # Should return the same agent
    assert agent1.agent_app_id == agent2.agent_app_id
    assert agent1.deployment_id == agent2.deployment_id


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
async def test_delete_success(supervisor_state, deploy_request, mock_process_manager, mock_port_allocator, mock_user_manager):
    """Test successful agent deletion with user cleanup."""
    # Deploy first
    await supervisor_state.deploy(deploy_request)

    # Delete with user cleanup
    delete_request = DeleteRequest(
        agent_app_id="4906eeb7",
        force=False,
        cleanup_user=True,
    )

    result = await supervisor_state.delete(delete_request)

    assert result is True

    # Verify cleanup was performed
    assert mock_process_manager.stop_agent.called
    assert mock_user_manager.clean_agent_files.called  # Agent files cleaned
    # NOTE: Ports are NOT released by PAR - PAC manages port lifecycle
    assert not mock_port_allocator.release.called
    assert mock_user_manager.delete_user.called  # User deleted when cleanup_user=True

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
    """Test deletion without user cleanup (new default behavior)."""
    # Deploy first
    await supervisor_state.deploy(deploy_request)

    # Delete without user cleanup (default behavior)
    delete_request = DeleteRequest(
        agent_app_id="4906eeb7",
        force=False,
        cleanup_user=False,
    )

    result = await supervisor_state.delete(delete_request)

    assert result is True
    # Verify agent files were cleaned
    assert mock_user_manager.clean_agent_files.called
    # Verify user was NOT deleted (preserved for reuse)
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


# Tests for Issue #2 fix: package extraction permissions
@patch("subprocess.run")
@patch("pathlib.Path.mkdir")
def test_initialize_shared_directories_success(mock_mkdir, mock_subprocess_run, mock_user_manager, mock_port_allocator, mock_package_downloader, mock_process_manager):
    """Test successful initialization of shared package extraction directory."""
    mock_subprocess_run.return_value = MagicMock(returncode=0)

    # Create SupervisorState - this should call _initialize_shared_directories
    state = SupervisorState(
        user_manager=mock_user_manager,
        port_allocator=mock_port_allocator,
        package_downloader=mock_package_downloader,
        process_manager=mock_process_manager,
    )

    # Verify directory creation was attempted
    assert mock_mkdir.called

    # Verify chmod 1777 was called
    assert mock_subprocess_run.called
    call_args = mock_subprocess_run.call_args[0][0]
    assert call_args == ["chmod", "1777", "/tmp/pixell_packages"]


@patch("subprocess.run")
@patch("pathlib.Path.mkdir")
def test_initialize_shared_directories_chmod_failure(mock_mkdir, mock_subprocess_run, mock_user_manager, mock_port_allocator, mock_package_downloader, mock_process_manager):
    """Test that chmod failure is logged but doesn't prevent initialization."""
    mock_subprocess_run.side_effect = subprocess.CalledProcessError(1, "chmod", stderr="Permission denied")

    # This should not raise - errors are logged but don't prevent startup
    state = SupervisorState(
        user_manager=mock_user_manager,
        port_allocator=mock_port_allocator,
        package_downloader=mock_package_downloader,
        process_manager=mock_process_manager,
    )

    # State should still be created
    assert state is not None
    assert isinstance(state, SupervisorState)
