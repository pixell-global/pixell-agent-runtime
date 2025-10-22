"""Unit tests for zombie process reaping in SupervisorState."""

import pytest
import asyncio
import os
import signal
import subprocess
import time
from unittest.mock import patch, MagicMock, call
from pathlib import Path
from datetime import datetime

from pixell_runtime.supervisor.state import SupervisorState
from pixell_runtime.supervisor.models import (
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
    allocator.max_agents.return_value = 20
    allocator.available_slots.return_value = 20
    return allocator


@pytest.fixture
def mock_package_downloader():
    """Create mock PackageDownloader."""
    downloader = MagicMock()
    downloader.download.return_value = Path("/var/lib/pixell/packages/test.apkg")
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
    return manager


@pytest.fixture
async def supervisor_state(
    mock_user_manager, mock_port_allocator, mock_package_downloader, mock_process_manager
):
    """Create SupervisorState with mocked dependencies and cleanup after test."""
    state = SupervisorState(
        user_manager=mock_user_manager,
        port_allocator=mock_port_allocator,
        package_downloader=mock_package_downloader,
        process_manager=mock_process_manager,
    )

    # Give the zombie reaper task time to start
    await asyncio.sleep(0.1)

    yield state

    # Cleanup
    await state.cleanup()


@pytest.mark.asyncio
async def test_zombie_reaper_task_starts(supervisor_state):
    """Test that zombie reaper task starts automatically."""
    assert supervisor_state._zombie_reaper_task is not None
    assert not supervisor_state._zombie_reaper_task.done()


@pytest.mark.asyncio
async def test_zombie_reaper_task_cancelled_on_cleanup(supervisor_state):
    """Test that zombie reaper task is cancelled during cleanup."""
    task = supervisor_state._zombie_reaper_task
    assert not task.done()

    await supervisor_state.cleanup()

    assert task.done()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_zombie_process_reaped_and_state_cleaned(
    supervisor_state, mock_process_manager
):
    """Test that zombie processes are reaped and agent state is cleaned up."""

    # Create a fake agent entry
    agent_id = "test-agent-123"
    test_pid = 99999

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
        started_at=datetime.now(),
    )

    supervisor_state.agents[agent_id] = agent
    mock_process_manager.processes[agent_id] = MagicMock(pid=test_pid)

    # Mock log file
    mock_log_file = MagicMock()
    mock_process_manager.log_files[agent_id] = mock_log_file

    # Mock os.waitpid to simulate finding a zombie
    with patch("os.waitpid") as mock_waitpid:
        # First call returns the zombie, second call returns (0, 0) - no more zombies
        mock_waitpid.side_effect = [
            (test_pid, 0),  # Found zombie with PID test_pid
            (0, 0),         # No more zombies
        ]

        # Wait for one iteration of the zombie reaper (5 seconds + processing time)
        await asyncio.sleep(5.5)

    # Verify agent status was updated
    assert supervisor_state.agents[agent_id].status == AgentStatus.FAILED
    assert supervisor_state.agents[agent_id].stopped_at is not None
    assert "Process died unexpectedly" in supervisor_state.agents[agent_id].error_message

    # Verify process manager cleanup
    assert agent_id not in mock_process_manager.processes
    assert agent_id not in mock_process_manager.log_files
    mock_log_file.close.assert_called_once()


@pytest.mark.asyncio
async def test_zombie_reaper_handles_no_children(supervisor_state):
    """Test that zombie reaper handles ChildProcessError gracefully."""

    # Mock os.waitpid to raise ChildProcessError (no children)
    with patch("os.waitpid") as mock_waitpid:
        mock_waitpid.side_effect = ChildProcessError("No child processes")

        # Wait for one iteration of the zombie reaper
        await asyncio.sleep(5.5)

    # Task should still be running despite the error
    assert not supervisor_state._zombie_reaper_task.done()


@pytest.mark.asyncio
async def test_zombie_reaper_handles_unknown_pid(supervisor_state):
    """Test that zombie reaper handles PIDs not belonging to tracked agents."""

    # Mock os.waitpid to return an unknown PID
    with patch("os.waitpid") as mock_waitpid:
        mock_waitpid.side_effect = [
            (88888, 0),  # Unknown PID
            (0, 0),      # No more zombies
        ]

        # Wait for one iteration
        await asyncio.sleep(5.5)

    # Task should still be running
    assert not supervisor_state._zombie_reaper_task.done()

    # No agents should be affected
    assert len(supervisor_state.agents) == 0


@pytest.mark.asyncio
async def test_zombie_reaper_handles_multiple_zombies(
    supervisor_state, mock_process_manager
):
    """Test that zombie reaper handles multiple zombies in one iteration."""

    # Create multiple fake agents
    agent1_id = "agent-1"
    agent2_id = "agent-2"
    agent3_id = "agent-3"
    pid1 = 11111
    pid2 = 22222
    pid3 = 33333

    for agent_id, pid in [(agent1_id, pid1), (agent2_id, pid2), (agent3_id, pid3)]:
        agent = AgentProcess(
            agent_app_id=agent_id,
            deployment_id=f"dep-{agent_id}",
            status=AgentStatus.RUNNING,
            ports=Ports(rest=8081, a2a=50052, ui=3001),
            pid=pid,
            linux_user="agent_test",
            package_path="/tmp/test.apkg",
            package_url="s3://test/test.apkg",
            created_at=datetime.now(),
            started_at=datetime.now(),
        )
        supervisor_state.agents[agent_id] = agent
        mock_process_manager.processes[agent_id] = MagicMock(pid=pid)
        mock_process_manager.log_files[agent_id] = MagicMock()

    # Mock os.waitpid to simulate finding multiple zombies
    # Note: cleanup helper also calls waitpid, which will raise ChildProcessError
    # since the zombie was already reaped by main loop
    with patch("os.waitpid") as mock_waitpid:
        def waitpid_side_effect(pid, options):
            """Mock waitpid behavior.

            Main loop calls waitpid(-1) to reap any zombie.
            Cleanup helper calls waitpid(specific_pid) which raises ChildProcessError
            since the zombie was already reaped.
            """
            if pid == -1:
                # Main loop reaping any zombie
                try:
                    return waitpid_side_effect.zombies.pop(0)
                except IndexError:
                    return (0, 0)  # No more zombies
            else:
                # Cleanup helper trying to reap specific PID - already reaped
                raise ChildProcessError("No child processes")

        # List of zombies to be reaped by main loop
        waitpid_side_effect.zombies = [
            (pid1, 0),  # First zombie
            (pid2, 0),  # Second zombie
            (pid3, 0),  # Third zombie
        ]

        mock_waitpid.side_effect = waitpid_side_effect

        # Wait for one iteration
        await asyncio.sleep(5.5)

    # Verify all agents were updated
    for agent_id in [agent1_id, agent2_id, agent3_id]:
        assert supervisor_state.agents[agent_id].status == AgentStatus.FAILED
        assert agent_id not in mock_process_manager.processes
        assert agent_id not in mock_process_manager.log_files


@pytest.mark.asyncio
async def test_zombie_reaper_logs_exit_code_and_signal(supervisor_state):
    """Test that zombie reaper logs exit codes and signals correctly."""

    # Create a fake agent
    agent_id = "test-agent"
    test_pid = 77777

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
        started_at=datetime.now(),
    )

    supervisor_state.agents[agent_id] = agent

    # Mock os.waitpid and status check functions
    with patch("os.waitpid") as mock_waitpid, \
         patch("os.WIFEXITED", return_value=True), \
         patch("os.WEXITSTATUS", return_value=137), \
         patch("os.WIFSIGNALED", return_value=False):

        # Simulate finding a zombie with exit code
        mock_waitpid.side_effect = [
            (test_pid, 256),  # Status with exit code
            (0, 0),           # No more zombies
        ]

        # Wait for one iteration
        await asyncio.sleep(5.5)

    # Verify error message includes exit code
    assert "137" in supervisor_state.agents[agent_id].error_message or \
           "exit_code" in supervisor_state.agents[agent_id].error_message


@pytest.mark.asyncio
async def test_zombie_reaper_continues_after_error(supervisor_state):
    """Test that zombie reaper continues running after encountering errors."""

    iteration_count = 0

    def mock_waitpid_with_error(*args, **kwargs):
        nonlocal iteration_count
        iteration_count += 1

        if iteration_count == 1:
            # First iteration: raise an unexpected error
            raise RuntimeError("Simulated error")
        else:
            # Subsequent iterations: normal behavior
            raise ChildProcessError("No child processes")

    with patch("os.waitpid", side_effect=mock_waitpid_with_error):
        # Wait for multiple iterations (2 * 5 seconds)
        await asyncio.sleep(11)

    # Task should still be running despite the error
    assert not supervisor_state._zombie_reaper_task.done()
    assert iteration_count >= 2


@pytest.mark.asyncio
async def test_real_zombie_process_integration():
    """Integration test with a real zombie process.

    This test spawns an actual child process that immediately exits,
    creating a real zombie that the supervisor should reap.
    """
    # Create supervisor with mocked dependencies
    mock_process_manager = MagicMock()
    mock_process_manager.processes = {}
    mock_process_manager.log_files = {}
    mock_process_manager.cleanup.return_value = None

    mock_user_manager = MagicMock()
    mock_port_allocator = MagicMock()
    mock_package_downloader = MagicMock()

    supervisor = SupervisorState(
        user_manager=mock_user_manager,
        port_allocator=mock_port_allocator,
        package_downloader=mock_package_downloader,
        process_manager=mock_process_manager,
    )

    try:
        # Create an agent entry first
        agent_id = "zombie-test-agent"

        # Spawn a real child process that exits immediately
        # This will become a zombie until reaped
        proc = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
        child_pid = proc.pid

        agent = AgentProcess(
            agent_app_id=agent_id,
            deployment_id="dep-zombie",
            status=AgentStatus.RUNNING,
            ports=Ports(rest=8081, a2a=50052, ui=3001),
            pid=child_pid,
            linux_user="agent_test",
            package_path="/tmp/test.apkg",
            package_url="s3://test/test.apkg",
            created_at=datetime.now(),
            started_at=datetime.now(),
        )
        supervisor.agents[agent_id] = agent

        # Give process time to exit and become a zombie
        await asyncio.sleep(0.1)

        # Wait for zombie reaper to run (up to 6 seconds)
        # The reaper checks every 5 seconds, so we need to wait at least that long
        await asyncio.sleep(6)

        # Verify the agent was marked as failed
        assert supervisor.agents[agent_id].status == AgentStatus.FAILED
        assert "Process died unexpectedly" in supervisor.agents[agent_id].error_message

    finally:
        await supervisor.cleanup()


@pytest.mark.asyncio
async def test_zombie_reaper_cleans_log_file_gracefully(
    supervisor_state, mock_process_manager
):
    """Test that zombie reaper handles log file cleanup errors gracefully."""

    # Create a fake agent with a log file that raises an error on close
    agent_id = "test-agent"
    test_pid = 55555

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
        started_at=datetime.now(),
    )

    supervisor_state.agents[agent_id] = agent
    mock_process_manager.processes[agent_id] = MagicMock(pid=test_pid)

    # Mock log file that raises error on close
    mock_log_file = MagicMock()
    mock_log_file.close.side_effect = IOError("Cannot close file")
    mock_process_manager.log_files[agent_id] = mock_log_file

    with patch("os.waitpid") as mock_waitpid:
        mock_waitpid.side_effect = [
            (test_pid, 0),  # Found zombie
            (0, 0),         # No more zombies
        ]

        # Wait for one iteration
        await asyncio.sleep(5.5)

    # Despite the error, cleanup should have been attempted
    mock_log_file.close.assert_called_once()

    # Agent should still be marked as failed
    assert supervisor_state.agents[agent_id].status == AgentStatus.FAILED
