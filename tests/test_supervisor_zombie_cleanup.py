"""Tests for zombie process cleanup in DELETE and DEPLOY operations."""

import pytest
import os
from unittest.mock import patch, MagicMock, AsyncMock, call
from datetime import datetime
from pathlib import Path

from pixell_runtime.supervisor.state import SupervisorState
from pixell_runtime.supervisor.models import (
    DeployRequest,
    DeleteRequest,
    AgentProcess,
    AgentStatus,
    Ports,
)


@pytest.fixture
def mock_user_manager():
    """Create mock LinuxUserManager."""
    manager = MagicMock()
    manager.get_username.return_value = "agent_test"
    manager.create_user.return_value = Path("/home/agent_test")
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
    return allocator


@pytest.fixture
def mock_package_downloader():
    """Create mock PackageDownloader."""
    downloader = MagicMock()
    downloader.download.return_value = Path("/tmp/test.apkg")
    return downloader


@pytest.fixture
def mock_process_manager():
    """Create mock ProcessManager."""
    manager = MagicMock()
    manager.processes = {}
    manager.log_files = {}
    manager.spawn_agent.return_value = 12345
    manager.is_running.return_value = True
    manager.stop_agent.return_value = True
    manager.cleanup.return_value = None

    # Mock get_process_health to return healthy by default
    manager.get_process_health.return_value = {
        "is_alive": True,
        "is_zombie": False,
        "memory_mb": 100.0,
        "cpu_percent": 10.0,
        "pid": 12345,
    }

    return manager


@pytest.fixture
def supervisor_state(mock_user_manager, mock_port_allocator, mock_package_downloader, mock_process_manager):
    """Create SupervisorState with mocked dependencies."""
    return SupervisorState(
        user_manager=mock_user_manager,
        port_allocator=mock_port_allocator,
        package_downloader=mock_package_downloader,
        process_manager=mock_process_manager,
    )


# Test 1: DELETE zombie removes from process_manager
@pytest.mark.asyncio
async def test_delete_zombie_cleans_process_manager_state(supervisor_state, mock_process_manager):
    """Test that DELETE removes zombie from process_manager.processes."""
    agent_id = "zombie-agent-123"
    test_pid = 99999

    # Setup: Create zombie agent
    agent = AgentProcess(
        agent_app_id=agent_id,
        deployment_id="dep-123",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        pid=test_pid,
        linux_user="agent_test",
        package_path="/tmp/test.apkg",
        package_url="s3://test/test.apkg",
        created_at=datetime.now(),
    )
    supervisor_state.agents[agent_id] = agent

    # Add to process_manager
    mock_process = MagicMock()
    mock_process.pid = test_pid
    mock_process_manager.processes[agent_id] = mock_process
    mock_process_manager.log_files[agent_id] = MagicMock()

    # Mock is_running to return False (zombie/dead)
    mock_process_manager.is_running.return_value = False

    # Call delete
    request = DeleteRequest(agent_app_id=agent_id, force=False, cleanup_user=False)
    await supervisor_state.delete(request)

    # Assert: agent removed from state
    assert agent_id not in supervisor_state.agents

    # Assert: process_manager cleaned
    assert agent_id not in mock_process_manager.processes
    assert agent_id not in mock_process_manager.log_files


# Test 2: DELETE zombie reaps via waitpid
@pytest.mark.asyncio
async def test_delete_zombie_reaps_process(supervisor_state, mock_process_manager):
    """Test that DELETE attempts to reap zombie via waitpid."""
    agent_id = "zombie-agent-456"
    test_pid = 88888

    # Setup zombie agent
    agent = AgentProcess(
        agent_app_id=agent_id,
        deployment_id="dep-456",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        pid=test_pid,
        linux_user="agent_test",
        package_path="/tmp/test.apkg",
        package_url="s3://test/test.apkg",
        created_at=datetime.now(),
    )
    supervisor_state.agents[agent_id] = agent

    # Mock is_running to return False
    mock_process_manager.is_running.return_value = False

    # Mock waitpid to track if called
    with patch("os.waitpid") as mock_waitpid:
        mock_waitpid.return_value = (test_pid, 0)  # Successfully reaped

        # Call delete
        request = DeleteRequest(agent_app_id=agent_id, force=False, cleanup_user=False)
        await supervisor_state.delete(request)

        # Assert waitpid called with correct PID
        mock_waitpid.assert_called_once_with(test_pid, os.WNOHANG)


# Test 3: DELETE handles already-reaped zombies gracefully
@pytest.mark.asyncio
async def test_delete_handles_already_reaped_zombie(supervisor_state, mock_process_manager):
    """Test DELETE handles ChildProcessError gracefully."""
    agent_id = "reaped-agent"
    test_pid = 77777

    # Setup agent
    agent = AgentProcess(
        agent_app_id=agent_id,
        deployment_id="dep-777",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        pid=test_pid,
        linux_user="agent_test",
        package_path="/tmp/test.apkg",
        package_url="s3://test/test.apkg",
        created_at=datetime.now(),
    )
    supervisor_state.agents[agent_id] = agent

    # Mock is_running to return False
    mock_process_manager.is_running.return_value = False

    # Mock waitpid to raise ChildProcessError (already reaped)
    with patch("os.waitpid", side_effect=ChildProcessError("No child processes")):
        # Call delete - should succeed despite error
        request = DeleteRequest(agent_app_id=agent_id, force=False, cleanup_user=False)
        result = await supervisor_state.delete(request)

        # Assert delete succeeded
        assert result is True
        assert agent_id not in supervisor_state.agents


# Test 4: DEPLOY detects zombie and auto-cleans
@pytest.mark.asyncio
async def test_deploy_existing_zombie_auto_cleanup(supervisor_state, mock_process_manager, mock_package_downloader, mock_user_manager):
    """Test DEPLOY auto-cleans zombie before deploying."""
    agent_id = "auto-clean-agent"

    # Setup: Create zombie agent with deployment_id=A
    existing_agent = AgentProcess(
        agent_app_id=agent_id,
        deployment_id="deployment-A",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        pid=55555,
        linux_user="agent_test",
        package_path="/tmp/old.apkg",
        package_url="s3://test/old.apkg",
        created_at=datetime.now(),
    )
    supervisor_state.agents[agent_id] = existing_agent

    # Mock get_process_health to return zombie
    mock_process_manager.get_process_health.return_value = {
        "is_alive": False,
        "is_zombie": True,
        "memory_mb": 0.0,
        "cpu_percent": 0.0,
        "pid": 55555,
    }

    # Mock is_running for delete path
    mock_process_manager.is_running.return_value = False

    # Mock spawn_agent for new deployment
    mock_process_manager.spawn_agent.return_value = 66666

    # Call deploy with deployment_id=B
    request = DeployRequest(
        agent_app_id=agent_id,
        deployment_id="deployment-B",
        package_url="s3://test/new.apkg",
        version="2.0.0",
        org_id="org-123",
        ports=Ports(rest=8081, a2a=50052, ui=3001),
    )

    new_agent = await supervisor_state.deploy(request)

    # Assert: new agent deployed with deployment_id=B
    assert new_agent.deployment_id == "deployment-B"
    assert new_agent.pid == 66666

    # Assert: spawn_agent was called (new process created)
    mock_process_manager.spawn_agent.assert_called_once()


# Test 5: DEPLOY detects dead process (not zombie) and auto-cleans
@pytest.mark.asyncio
async def test_deploy_existing_dead_auto_cleanup(supervisor_state, mock_process_manager, mock_package_downloader):
    """Test DEPLOY auto-cleans dead (not zombie) process."""
    agent_id = "dead-agent"

    # Setup: Create dead agent (terminated, not zombie)
    existing_agent = AgentProcess(
        agent_app_id=agent_id,
        deployment_id="deployment-old",
        status=AgentStatus.FAILED,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        pid=44444,
        linux_user="agent_test",
        package_path="/tmp/old.apkg",
        package_url="s3://test/old.apkg",
        created_at=datetime.now(),
    )
    supervisor_state.agents[agent_id] = existing_agent

    # Mock get_process_health to return dead (not zombie)
    mock_process_manager.get_process_health.return_value = {
        "is_alive": False,
        "is_zombie": False,
        "memory_mb": 0.0,
        "cpu_percent": 0.0,
        "pid": 44444,
    }

    # Mock is_running
    mock_process_manager.is_running.return_value = False

    # Mock spawn_agent
    mock_process_manager.spawn_agent.return_value = 77777

    # Call deploy
    request = DeployRequest(
        agent_app_id=agent_id,
        deployment_id="deployment-new",
        package_url="s3://test/new.apkg",
        version="3.0.0",
        org_id="org-123",
        ports=Ports(rest=8081, a2a=50052, ui=3001),
    )

    new_agent = await supervisor_state.deploy(request)

    # Assert: new agent deployed
    assert new_agent.deployment_id == "deployment-new"
    assert new_agent.pid == 77777


# Test 6: DEPLOY returns existing healthy agent (idempotent)
@pytest.mark.asyncio
async def test_deploy_existing_healthy_idempotent(supervisor_state, mock_process_manager):
    """Test DEPLOY returns existing agent if alive and same deployment_id."""
    agent_id = "healthy-agent"

    # Setup: Create healthy agent
    existing_agent = AgentProcess(
        agent_app_id=agent_id,
        deployment_id="deployment-123",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        pid=33333,
        linux_user="agent_test",
        package_path="/tmp/test.apkg",
        package_url="s3://test/test.apkg",
        created_at=datetime.now(),
    )
    supervisor_state.agents[agent_id] = existing_agent

    # Mock get_process_health to return alive
    mock_process_manager.get_process_health.return_value = {
        "is_alive": True,
        "is_zombie": False,
        "memory_mb": 150.0,
        "cpu_percent": 25.0,
        "pid": 33333,
    }

    # Call deploy with same deployment_id
    request = DeployRequest(
        agent_app_id=agent_id,
        deployment_id="deployment-123",  # Same deployment_id
        package_url="s3://test/test.apkg",
        version="1.0.0",
        org_id="org-123",
    )

    returned_agent = await supervisor_state.deploy(request)

    # Assert: returns existing agent (idempotent)
    assert returned_agent is existing_agent
    assert returned_agent.pid == 33333

    # Assert: spawn_agent NOT called
    mock_process_manager.spawn_agent.assert_not_called()


# Test 7: Helper method is idempotent
def test_cleanup_process_manager_state_idempotent(supervisor_state, mock_process_manager):
    """Test _cleanup_process_manager_state can be called multiple times."""
    agent_id = "test-agent"
    test_pid = 12345

    # Add to process_manager
    mock_process_manager.processes[agent_id] = MagicMock()
    mock_process_manager.log_files[agent_id] = MagicMock()

    # Mock waitpid
    with patch("os.waitpid", return_value=(test_pid, 0)):
        # Call helper twice
        supervisor_state._cleanup_process_manager_state(agent_id, test_pid)
        supervisor_state._cleanup_process_manager_state(agent_id, test_pid)

        # Assert: no errors, state cleaned
        assert agent_id not in mock_process_manager.processes
        assert agent_id not in mock_process_manager.log_files


# Test 8: Helper method handles missing agent gracefully
def test_cleanup_process_manager_state_missing_agent(supervisor_state, mock_process_manager):
    """Test helper handles agent not in processes dict."""
    agent_id = "missing-agent"
    test_pid = 99999

    # Don't add to process_manager

    # Call helper - should not raise
    supervisor_state._cleanup_process_manager_state(agent_id, test_pid)

    # Assert: no errors
    assert agent_id not in mock_process_manager.processes


# Test 9: Integration test - DELETE then DEPLOY same agent
@pytest.mark.asyncio
async def test_delete_zombie_then_deploy_succeeds(supervisor_state, mock_process_manager, mock_package_downloader):
    """Integration: DELETE zombie, then DEPLOY same agent_app_id."""
    agent_id = "redeploy-agent"

    # Step 1: Create zombie
    zombie_agent = AgentProcess(
        agent_app_id=agent_id,
        deployment_id="old-deployment",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        pid=11111,
        linux_user="agent_test",
        package_path="/tmp/old.apkg",
        package_url="s3://test/old.apkg",
        created_at=datetime.now(),
    )
    supervisor_state.agents[agent_id] = zombie_agent

    # Mock is_running to return False (zombie)
    mock_process_manager.is_running.return_value = False

    # Step 2: DELETE
    delete_req = DeleteRequest(agent_app_id=agent_id, force=True, cleanup_user=False)
    await supervisor_state.delete(delete_req)

    assert agent_id not in supervisor_state.agents

    # Step 3: DEPLOY same agent_app_id
    mock_process_manager.spawn_agent.return_value = 22222

    deploy_req = DeployRequest(
        agent_app_id=agent_id,
        deployment_id="new-deployment",
        package_url="s3://test/new.apkg",
        version="2.0.0",
        org_id="org-123",
        ports=Ports(rest=8081, a2a=50052, ui=3001),
    )

    new_agent = await supervisor_state.deploy(deploy_req)

    # Assert: new agent deployed
    assert new_agent.agent_app_id == agent_id
    assert new_agent.deployment_id == "new-deployment"
    assert new_agent.pid == 22222


# Test 10: Integration test - DEPLOY with zombie auto-recovery
@pytest.mark.asyncio
async def test_deploy_with_zombie_auto_recovery(supervisor_state, mock_process_manager, mock_package_downloader):
    """Integration: DEPLOY auto-recovers from zombie state."""
    agent_id = "recovery-agent"

    # Setup: Create zombie
    zombie_agent = AgentProcess(
        agent_app_id=agent_id,
        deployment_id="crashed-deployment",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=8081, a2a=50052, ui=3001),
        pid=88888,
        linux_user="agent_test",
        package_path="/tmp/crashed.apkg",
        package_url="s3://test/crashed.apkg",
        created_at=datetime.now(),
    )
    supervisor_state.agents[agent_id] = zombie_agent

    # Mock get_process_health to return zombie
    mock_process_manager.get_process_health.return_value = {
        "is_alive": False,
        "is_zombie": True,
        "memory_mb": 0.0,
        "cpu_percent": 0.0,
        "pid": 88888,
    }

    # Mock is_running for delete path
    mock_process_manager.is_running.return_value = False

    # Mock spawn_agent
    mock_process_manager.spawn_agent.return_value = 99999

    # Call DEPLOY with different deployment_id
    deploy_req = DeployRequest(
        agent_app_id=agent_id,
        deployment_id="recovery-deployment",
        package_url="s3://test/recovery.apkg",
        version="3.0.0",
        org_id="org-123",
        ports=Ports(rest=8081, a2a=50052, ui=3001),
    )

    new_agent = await supervisor_state.deploy(deploy_req)

    # Assert: Zombie cleaned, new agent deployed
    assert new_agent.agent_app_id == agent_id
    assert new_agent.deployment_id == "recovery-deployment"
    assert new_agent.pid == 99999
    assert new_agent.status == AgentStatus.RUNNING
