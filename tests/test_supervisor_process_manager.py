"""Unit tests for ProcessManager."""

import pytest
import asyncio
import subprocess
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path

from pixell_runtime.supervisor.process_manager import ProcessManager
from pixell_runtime.supervisor.models import AgentStatus, Ports


@pytest.fixture
def mock_ports():
    """Create mock ports."""
    return Ports(rest=63000, a2a=60000, ui=65000)


@pytest.fixture
def process_manager():
    """Create ProcessManager instance."""
    return ProcessManager(graceful_shutdown_timeout_sec=5)


@pytest.fixture
def mock_filesystem():
    """Mock filesystem operations for process manager tests."""
    with patch("pathlib.Path.mkdir"), patch("builtins.open", new_callable=MagicMock) as mock_open:
        mock_log_file = MagicMock()
        mock_open.return_value = mock_log_file
        yield mock_open


def test_process_manager_init():
    """Test ProcessManager initialization."""
    pm = ProcessManager(graceful_shutdown_timeout_sec=10)
    assert pm.graceful_shutdown_timeout_sec == 10
    assert len(pm.processes) == 0


@patch("subprocess.Popen")
def test_spawn_agent_success(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test successful agent spawn."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_popen.return_value = mock_process

    pid = process_manager.spawn_agent(
        agent_app_id="4906eeb7",
        linux_user="agent_4906eeb7",
        package_path=Path("/var/lib/pixell/packages/test.apkg"),
        ports=mock_ports,
        env={"CUSTOM_VAR": "value"},
    )

    assert pid == 12345
    assert "4906eeb7" in process_manager.processes
    assert process_manager.processes["4906eeb7"] == mock_process

    # Verify subprocess.Popen was called correctly
    assert mock_popen.called
    call_args = mock_popen.call_args
    cmd = call_args[0][0]
    kwargs = call_args[1]

    # Check command structure: ["/usr/bin/python3.11", "-m", "pixell_runtime"]
    assert cmd == ["/usr/bin/python3.11", "-m", "pixell_runtime"]

    # Check that user parameter was passed (Python 3.9+ native user switching)
    assert "user" in kwargs
    assert kwargs["user"] == "agent_4906eeb7"

    # Check that env dict was passed to Popen
    assert "env" in kwargs
    assert "AGENT_APP_ID" in kwargs["env"]
    assert "AGENT_PACKAGE_PATH" in kwargs["env"]
    assert "CUSTOM_VAR" in kwargs["env"]
    assert kwargs["env"]["CUSTOM_VAR"] == "value"


@patch("subprocess.Popen")
def test_spawn_agent_failure(mock_popen, process_manager, mock_ports):
    """Test agent spawn failure."""
    mock_popen.side_effect = Exception("Spawn failed")

    with pytest.raises(RuntimeError, match="Failed to spawn agent"):
        process_manager.spawn_agent(
            agent_app_id="4906eeb7",
            linux_user="agent_4906eeb7",
            package_path=Path("/var/lib/pixell/packages/test.apkg"),
            ports=mock_ports,
        )


def test_is_running_not_exists(process_manager):
    """Test is_running for non-existent agent."""
    assert process_manager.is_running("nonexistent") is False


@patch("subprocess.Popen")
def test_is_running_exists_running(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test is_running for running agent."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = None  # Still running
    mock_popen.return_value = mock_process

    process_manager.spawn_agent(
        agent_app_id="4906eeb7",
        linux_user="agent_4906eeb7",
        package_path=Path("/test.apkg"),
        ports=mock_ports,
    )

    assert process_manager.is_running("4906eeb7") is True


@patch("subprocess.Popen")
def test_is_running_exists_stopped(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test is_running for stopped agent."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = 0  # Exited
    mock_popen.return_value = mock_process

    process_manager.spawn_agent(
        agent_app_id="4906eeb7",
        linux_user="agent_4906eeb7",
        package_path=Path("/test.apkg"),
        ports=mock_ports,
    )

    assert process_manager.is_running("4906eeb7") is False


def test_get_pid_not_exists(process_manager):
    """Test get_pid for non-existent agent."""
    assert process_manager.get_pid("nonexistent") is None


@patch("subprocess.Popen")
def test_get_pid_exists(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test get_pid for existing agent."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_popen.return_value = mock_process

    process_manager.spawn_agent(
        agent_app_id="4906eeb7",
        linux_user="agent_4906eeb7",
        package_path=Path("/test.apkg"),
        ports=mock_ports,
    )

    assert process_manager.get_pid("4906eeb7") == 12345


def test_stop_agent_not_exists(process_manager):
    """Test stopping non-existent agent."""
    result = process_manager.stop_agent("nonexistent")
    assert result is False


@patch("subprocess.Popen")
def test_stop_agent_already_stopped(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test stopping already stopped agent."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = 0  # Already exited
    mock_popen.return_value = mock_process

    process_manager.spawn_agent(
        agent_app_id="4906eeb7",
        linux_user="agent_4906eeb7",
        package_path=Path("/test.apkg"),
        ports=mock_ports,
    )

    result = process_manager.stop_agent("4906eeb7")
    assert result is True
    assert "4906eeb7" not in process_manager.processes


@patch("subprocess.Popen")
def test_stop_agent_graceful(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test graceful agent stop."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = None  # Running
    mock_process.wait.return_value = None  # Exits gracefully
    mock_popen.return_value = mock_process

    process_manager.spawn_agent(
        agent_app_id="4906eeb7",
        linux_user="agent_4906eeb7",
        package_path=Path("/test.apkg"),
        ports=mock_ports,
    )

    result = process_manager.stop_agent("4906eeb7", force=False)
    assert result is True
    assert "4906eeb7" not in process_manager.processes

    # Verify terminate was called
    assert mock_process.terminate.called
    assert not mock_process.kill.called


@patch("subprocess.Popen")
def test_stop_agent_force(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test force kill agent."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = None  # Running
    mock_process.wait.return_value = None
    mock_popen.return_value = mock_process

    process_manager.spawn_agent(
        agent_app_id="4906eeb7",
        linux_user="agent_4906eeb7",
        package_path=Path("/test.apkg"),
        ports=mock_ports,
    )

    result = process_manager.stop_agent("4906eeb7", force=True)
    assert result is True
    assert "4906eeb7" not in process_manager.processes

    # Verify kill was called
    assert mock_process.kill.called
    assert not mock_process.terminate.called


@patch("subprocess.Popen")
def test_stop_agent_timeout_then_kill(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test graceful stop that times out and requires force kill."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = None  # Running

    # First wait (graceful) times out, second wait (after kill) succeeds
    mock_process.wait.side_effect = [subprocess.TimeoutExpired("cmd", 5), None]
    mock_popen.return_value = mock_process

    process_manager.spawn_agent(
        agent_app_id="4906eeb7",
        linux_user="agent_4906eeb7",
        package_path=Path("/test.apkg"),
        ports=mock_ports,
    )

    result = process_manager.stop_agent("4906eeb7", force=False, timeout=1)
    assert result is True
    assert "4906eeb7" not in process_manager.processes

    # Verify both terminate and kill were called
    assert mock_process.terminate.called
    assert mock_process.kill.called


@pytest.mark.asyncio
async def test_health_check_not_running(process_manager, mock_ports):
    """Test health check for non-running agent."""
    result = await process_manager.health_check("4906eeb7", mock_ports)
    assert result is False


@pytest.mark.asyncio
@patch("subprocess.Popen")
async def test_health_check_http_success(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test successful health check."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = None  # Running
    mock_popen.return_value = mock_process

    process_manager.spawn_agent(
        agent_app_id="4906eeb7",
        linux_user="agent_4906eeb7",
        package_path=Path("/test.apkg"),
        ports=mock_ports,
    )

    # Mock httpx client
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "healthy"}

        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_response
        )

        result = await process_manager.health_check("4906eeb7", mock_ports)
        assert result is True


@pytest.mark.asyncio
@patch("subprocess.Popen")
async def test_health_check_http_unhealthy(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test health check with unhealthy response."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = None  # Running
    mock_popen.return_value = mock_process

    process_manager.spawn_agent(
        agent_app_id="4906eeb7",
        linux_user="agent_4906eeb7",
        package_path=Path("/test.apkg"),
        ports=mock_ports,
    )

    # Mock httpx client with unhealthy status
    with patch("httpx.AsyncClient") as mock_client:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "unhealthy"}

        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_response
        )

        result = await process_manager.health_check("4906eeb7", mock_ports)
        assert result is False


@pytest.mark.asyncio
@patch("subprocess.Popen")
async def test_health_check_http_error(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test health check with HTTP error."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = None  # Running
    mock_popen.return_value = mock_process

    process_manager.spawn_agent(
        agent_app_id="4906eeb7",
        linux_user="agent_4906eeb7",
        package_path=Path("/test.apkg"),
        ports=mock_ports,
    )

    # Mock httpx client that raises exception
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        result = await process_manager.health_check("4906eeb7", mock_ports)
        assert result is False


def test_get_process_status_not_exists(process_manager):
    """Test get_process_status for non-existent agent."""
    status = process_manager.get_process_status("nonexistent")
    assert status == AgentStatus.STOPPED


@patch("subprocess.Popen")
def test_get_process_status_running(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test get_process_status for running agent."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = None  # Running
    mock_popen.return_value = mock_process

    process_manager.spawn_agent(
        agent_app_id="4906eeb7",
        linux_user="agent_4906eeb7",
        package_path=Path("/test.apkg"),
        ports=mock_ports,
    )

    status = process_manager.get_process_status("4906eeb7")
    assert status == AgentStatus.RUNNING


@patch("subprocess.Popen")
def test_get_process_status_failed(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test get_process_status for failed agent."""
    mock_process = MagicMock()
    mock_process.pid = 12345
    mock_process.poll.return_value = 1  # Exited with error
    mock_popen.return_value = mock_process

    process_manager.spawn_agent(
        agent_app_id="4906eeb7",
        linux_user="agent_4906eeb7",
        package_path=Path("/test.apkg"),
        ports=mock_ports,
    )

    status = process_manager.get_process_status("4906eeb7")
    assert status == AgentStatus.FAILED


@patch("subprocess.Popen")
def test_stop_all(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test stopping all agents."""
    # Spawn 3 agents
    for i in range(3):
        mock_process = MagicMock()
        mock_process.pid = 12345 + i
        mock_process.poll.return_value = None  # Running
        mock_process.wait.return_value = None
        mock_popen.return_value = mock_process

        ports = Ports(rest=63000 + i, a2a=60000 + i, ui=65000 + i)
        process_manager.spawn_agent(
            agent_app_id=f"agent_{i}",
            linux_user=f"agent_agent_{i}",
            package_path=Path("/test.apkg"),
            ports=ports,
        )

    assert len(process_manager.processes) == 3

    # Stop all
    count = process_manager.stop_all(force=False)
    assert count == 3
    assert len(process_manager.processes) == 0


@patch("subprocess.Popen")
def test_stop_all_with_error(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test stop_all continues on error."""
    # Spawn 2 agents
    for i in range(2):
        mock_process = MagicMock()
        mock_process.pid = 12345 + i
        mock_process.poll.return_value = None  # Running

        # First agent fails to stop, second succeeds
        if i == 0:
            mock_process.terminate.side_effect = Exception("Stop failed")
        else:
            mock_process.wait.return_value = None

        mock_popen.return_value = mock_process

        ports = Ports(rest=63000 + i, a2a=60000 + i, ui=65000 + i)
        process_manager.spawn_agent(
            agent_app_id=f"agent_{i}",
            linux_user=f"agent_agent_{i}",
            package_path=Path("/test.apkg"),
            ports=ports,
        )

    # Stop all - should continue despite error
    count = process_manager.stop_all(force=False)
    # One should succeed despite the other failing
    assert count == 1


@patch("subprocess.Popen")
def test_cleanup(mock_popen, process_manager, mock_ports, mock_filesystem):
    """Test cleanup stops all processes."""
    # Spawn agents
    for i in range(2):
        mock_process = MagicMock()
        mock_process.pid = 12345 + i
        mock_process.poll.return_value = None  # Running
        mock_process.wait.return_value = None
        mock_popen.return_value = mock_process

        ports = Ports(rest=63000 + i, a2a=60000 + i, ui=65000 + i)
        process_manager.spawn_agent(
            agent_app_id=f"agent_{i}",
            linux_user=f"agent_agent_{i}",
            package_path=Path("/test.apkg"),
            ports=ports,
        )

    process_manager.cleanup()
    assert len(process_manager.processes) == 0


# Tests for Issue #2 fix: package ownership
@patch("subprocess.run")
@patch("zipfile.ZipFile")
@patch("yaml.safe_load")
def test_ensure_package_ownership_success(mock_yaml_load, mock_zipfile, mock_subprocess_run, process_manager):
    """Test successful package ownership fix."""
    # Mock the zipfile to contain agent.yaml
    mock_zf = MagicMock()
    mock_zipfile.return_value.__enter__.return_value = mock_zf
    mock_zf.namelist.return_value = ["agent.yaml", "other_file.py"]

    # Mock the manifest yaml content
    mock_yaml_load.return_value = {"name": "test-agent", "version": "1.0.0"}

    # Mock subprocess to succeed
    mock_subprocess_run.return_value = MagicMock(returncode=0, stderr="")

    # Mock directory exists
    with patch("pathlib.Path.exists", return_value=True):
        process_manager._ensure_package_ownership(
            agent_app_id="test-agent-id",
            linux_user="agent_test",
            package_path=Path("/var/lib/pixell/packages/test.apkg")
        )

    # Verify chown was called
    assert mock_subprocess_run.called
    call_args = mock_subprocess_run.call_args[0][0]
    assert call_args[0] == "chown"
    assert call_args[1] == "-R"
    assert "agent_test:agent_test" in call_args[2]


@patch("zipfile.ZipFile")
def test_ensure_package_ownership_no_manifest(mock_zipfile, process_manager):
    """Test when agent.yaml is missing from package."""
    # Mock the zipfile without agent.yaml
    mock_zf = MagicMock()
    mock_zipfile.return_value.__enter__.return_value = mock_zf
    mock_zf.namelist.return_value = ["other_file.py"]

    # Should not raise - just log and return
    process_manager._ensure_package_ownership(
        agent_app_id="test-agent-id",
        linux_user="agent_test",
        package_path=Path("/var/lib/pixell/packages/test.apkg")
    )

    # Should not have tried to open agent.yaml
    assert not mock_zf.open.called


@patch("zipfile.ZipFile")
def test_ensure_package_ownership_bad_zipfile(mock_zipfile, process_manager):
    """Test handling of bad/corrupt zip file."""
    # Mock bad zipfile
    import zipfile
    mock_zipfile.side_effect = zipfile.BadZipFile("Not a zip file")

    # Should not raise - just log and return
    process_manager._ensure_package_ownership(
        agent_app_id="test-agent-id",
        linux_user="agent_test",
        package_path=Path("/var/lib/pixell/packages/test.apkg")
    )


@patch("subprocess.run")
@patch("zipfile.ZipFile")
@patch("yaml.safe_load")
def test_ensure_package_ownership_directory_not_exists(mock_yaml_load, mock_zipfile, mock_subprocess_run, process_manager):
    """Test when extracted directory doesn't exist yet."""
    # Mock the zipfile
    mock_zf = MagicMock()
    mock_zipfile.return_value.__enter__.return_value = mock_zf
    mock_zf.namelist.return_value = ["agent.yaml"]
    mock_yaml_load.return_value = {"name": "test-agent", "version": "1.0.0"}

    # Mock directory doesn't exist
    with patch("pathlib.Path.exists", return_value=False):
        process_manager._ensure_package_ownership(
            agent_app_id="test-agent-id",
            linux_user="agent_test",
            package_path=Path("/var/lib/pixell/packages/test.apkg")
        )

    # Chown should not be called if directory doesn't exist
    assert not mock_subprocess_run.called


@patch("subprocess.run")
@patch("zipfile.ZipFile")
@patch("yaml.safe_load")
def test_ensure_package_ownership_chown_failure(mock_yaml_load, mock_zipfile, mock_subprocess_run, process_manager):
    """Test when chown fails (non-fatal)."""
    # Mock the zipfile
    mock_zf = MagicMock()
    mock_zipfile.return_value.__enter__.return_value = mock_zf
    mock_zf.namelist.return_value = ["agent.yaml"]
    mock_yaml_load.return_value = {"name": "test-agent", "version": "1.0.0"}

    # Mock subprocess to fail
    mock_subprocess_run.return_value = MagicMock(returncode=1, stderr="Permission denied")

    # Mock directory exists
    with patch("pathlib.Path.exists", return_value=True):
        # Should not raise - just log warning
        process_manager._ensure_package_ownership(
            agent_app_id="test-agent-id",
            linux_user="agent_test",
            package_path=Path("/var/lib/pixell/packages/test.apkg")
        )
