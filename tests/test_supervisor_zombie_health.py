"""Unit tests for zombie process health detection."""

import pytest
import subprocess
import platform
import os
from unittest.mock import patch, MagicMock
from datetime import datetime

from pixell_runtime.supervisor.process_manager import ProcessManager
from pixell_runtime.supervisor.models import AgentProcess, AgentStatus, Ports


@pytest.fixture
def process_manager():
    """Create ProcessManager instance."""
    return ProcessManager()


def test_is_process_zombie_with_none_pid(process_manager):
    """Test that is_process_zombie returns False for None PID."""
    assert process_manager.is_process_zombie(None) is False


def test_is_process_zombie_with_nonexistent_pid(process_manager):
    """Test that is_process_zombie returns False for non-existent PID."""
    # Use a very high PID that's unlikely to exist
    fake_pid = 99999999
    assert process_manager.is_process_zombie(fake_pid) is False


def test_is_process_zombie_with_normal_process(process_manager):
    """Test that is_process_zombie returns False for normal processes."""
    # Use current process (definitely not a zombie)
    current_pid = os.getpid()
    assert process_manager.is_process_zombie(current_pid) is False


@pytest.mark.skipif(
    platform.system() != "Linux",
    reason="Real zombie processes only testable on Linux"
)
def test_is_process_zombie_with_real_zombie(process_manager):
    """Integration test with a real zombie process (Linux only).

    This test spawns a child process that exits immediately, creating
    a real zombie until we reap it.
    """
    # Spawn a child that exits immediately
    proc = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    zombie_pid = proc.pid

    # Give it a moment to become a zombie
    import time
    time.sleep(0.1)

    try:
        # Check if it's detected as zombie
        is_zombie = process_manager.is_process_zombie(zombie_pid)

        # It should be a zombie
        assert is_zombie is True

    finally:
        # Clean up - reap the zombie
        try:
            proc.wait(timeout=1)
        except:
            pass


def test_is_process_zombie_with_mocked_psutil_zombie():
    """Test zombie detection with mocked psutil returning zombie status."""
    import psutil

    process_manager = ProcessManager()
    test_pid = 12345

    # Mock psutil.Process to return zombie status
    with patch("psutil.Process") as mock_process_class:
        mock_process = MagicMock()
        mock_process.status.return_value = psutil.STATUS_ZOMBIE
        mock_process_class.return_value = mock_process

        result = process_manager.is_process_zombie(test_pid)

        assert result is True
        mock_process_class.assert_called_once_with(test_pid)


def test_is_process_zombie_with_mocked_psutil_running():
    """Test that running processes are not detected as zombies."""
    import psutil

    process_manager = ProcessManager()
    test_pid = 12345

    # Mock psutil.Process to return running status
    with patch("psutil.Process") as mock_process_class:
        mock_process = MagicMock()
        mock_process.status.return_value = psutil.STATUS_RUNNING
        mock_process_class.return_value = mock_process

        result = process_manager.is_process_zombie(test_pid)

        assert result is False


@pytest.mark.skip(reason="psutil is a required dependency, ImportError not realistic")
def test_is_process_zombie_handles_psutil_import_error(process_manager):
    """Test graceful degradation when psutil is not available.

    Skipped because psutil is a required dependency in pyproject.toml.
    The ImportError handling exists for robustness but won't occur in practice.
    """
    pass


def test_is_process_zombie_handles_no_such_process():
    """Test handling of psutil.NoSuchProcess exception."""
    import psutil

    process_manager = ProcessManager()
    test_pid = 99999

    with patch("psutil.Process") as mock_process_class:
        mock_process_class.side_effect = psutil.NoSuchProcess(test_pid)

        result = process_manager.is_process_zombie(test_pid)

        assert result is False


def test_get_process_health_for_nonexistent_agent(process_manager):
    """Test get_process_health for agent not in processes dict."""
    health = process_manager.get_process_health("nonexistent-agent")

    assert health == {
        "is_alive": False,
        "is_zombie": False,
        "memory_mb": 0.0,
        "cpu_percent": 0.0,
        "pid": None,
    }


def test_get_process_health_for_terminated_process(process_manager):
    """Test get_process_health for terminated (reaped) process."""
    agent_id = "test-agent"

    # Create a mock process that has terminated (poll() returns exit code)
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = 0  # Process exited with code 0

    process_manager.processes[agent_id] = mock_process

    health = process_manager.get_process_health(agent_id)

    assert health["is_alive"] is False
    assert health["is_zombie"] is False
    assert health["memory_mb"] == 0.0
    assert health["cpu_percent"] == 0.0
    assert health["pid"] == 12345


def test_get_process_health_for_zombie_process(process_manager):
    """Test get_process_health for zombie process."""
    import psutil

    agent_id = "test-agent"
    test_pid = 12345

    # Create a mock process that hasn't terminated (poll() returns None)
    # but is a zombie (psutil detects it)
    mock_process = MagicMock()
    mock_process.pid = test_pid
    mock_process.poll.return_value = None  # Still in process table

    process_manager.processes[agent_id] = mock_process

    # Mock psutil to report zombie status
    with patch("psutil.Process") as mock_psutil_class:
        mock_psutil = MagicMock()
        mock_psutil.status.return_value = psutil.STATUS_ZOMBIE
        mock_psutil_class.return_value = mock_psutil

        health = process_manager.get_process_health(agent_id)

        assert health["is_alive"] is False
        assert health["is_zombie"] is True
        assert health["memory_mb"] == 0.0
        assert health["cpu_percent"] == 0.0
        assert health["pid"] == test_pid


def test_get_process_health_for_alive_process(process_manager):
    """Test get_process_health for healthy, alive process."""
    import psutil

    agent_id = "test-agent"
    test_pid = 12345

    # Create a mock process that is running
    mock_process = MagicMock()
    mock_process.pid = test_pid
    mock_process.poll.return_value = None  # Still running

    process_manager.processes[agent_id] = mock_process

    # Mock psutil to report running status with metrics
    with patch("psutil.Process") as mock_psutil_class:
        mock_psutil = MagicMock()
        mock_psutil.status.return_value = psutil.STATUS_RUNNING

        # Mock memory and CPU metrics
        mock_memory_info = MagicMock()
        mock_memory_info.rss = 100 * 1024 * 1024  # 100 MB in bytes
        mock_psutil.memory_info.return_value = mock_memory_info
        mock_psutil.cpu_percent.return_value = 25.5

        mock_psutil_class.return_value = mock_psutil

        health = process_manager.get_process_health(agent_id)

        assert health["is_alive"] is True
        assert health["is_zombie"] is False
        assert health["memory_mb"] == 100.0
        assert health["cpu_percent"] == 25.5
        assert health["pid"] == test_pid


@pytest.mark.skip(reason="psutil is a required dependency, ImportError not realistic")
def test_get_process_health_handles_psutil_unavailable(process_manager):
    """Test get_process_health when psutil is not available.

    Skipped because psutil is a required dependency in pyproject.toml.
    The ImportError handling exists for robustness but won't occur in practice.
    """
    pass


def test_is_running_returns_false_for_zombie(process_manager):
    """Test that is_running returns False for zombie processes."""
    import psutil

    agent_id = "test-agent"
    test_pid = 12345

    # Create a mock process
    mock_process = MagicMock()
    mock_process.pid = test_pid
    mock_process.poll.return_value = None  # In process table

    process_manager.processes[agent_id] = mock_process

    # Mock psutil to report zombie
    with patch("psutil.Process") as mock_psutil_class:
        mock_psutil = MagicMock()
        mock_psutil.status.return_value = psutil.STATUS_ZOMBIE
        mock_psutil_class.return_value = mock_psutil

        # is_running should return False for zombies
        assert process_manager.is_running(agent_id) is False


def test_is_running_returns_true_for_healthy_process(process_manager):
    """Test that is_running returns True for healthy processes."""
    import psutil

    agent_id = "test-agent"
    test_pid = 12345

    # Create a mock running process
    mock_process = MagicMock()
    mock_process.pid = test_pid
    mock_process.poll.return_value = None

    process_manager.processes[agent_id] = mock_process

    # Mock psutil to report running
    with patch("psutil.Process") as mock_psutil_class:
        mock_psutil = MagicMock()
        mock_psutil.status.return_value = psutil.STATUS_RUNNING
        mock_psutil_class.return_value = mock_psutil

        # is_running should return True
        assert process_manager.is_running(agent_id) is True


def test_is_running_returns_false_for_terminated(process_manager):
    """Test that is_running returns False for terminated processes."""
    agent_id = "test-agent"

    # Create a mock terminated process
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = 0  # Exited

    process_manager.processes[agent_id] = mock_process

    assert process_manager.is_running(agent_id) is False


def test_zombie_string_status_detection(process_manager):
    """Test that zombie detection works with string status 'zombie'."""
    import psutil

    test_pid = 12345

    with patch("psutil.Process") as mock_process_class:
        mock_process = MagicMock()
        # Some versions of psutil return string instead of constant
        mock_process.status.return_value = "zombie"
        mock_process_class.return_value = mock_process

        result = process_manager.is_process_zombie(test_pid)

        assert result is True


def test_zombie_Z_status_detection(process_manager):
    """Test that zombie detection works with status 'Z'."""
    import psutil

    test_pid = 12345

    with patch("psutil.Process") as mock_process_class:
        mock_process = MagicMock()
        # Single letter status like in /proc/*/stat
        mock_process.status.return_value = "Z"
        mock_process_class.return_value = mock_process

        result = process_manager.is_process_zombie(test_pid)

        assert result is True
