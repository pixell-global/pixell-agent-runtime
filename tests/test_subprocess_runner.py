"""Tests for subprocess agent runner."""

import asyncio
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from pixell_runtime.agents.loader import PackageLoader
from pixell_runtime.core.models import AgentPackage
from pixell_runtime.three_surface.subprocess_runner import SubprocessAgentRunner


@pytest.fixture
def temp_dirs():
    """Create temporary directories for testing."""
    temp_root = Path(tempfile.mkdtemp())
    packages_dir = temp_root / "packages"
    venvs_dir = temp_root / "venvs"
    packages_dir.mkdir()
    venvs_dir.mkdir()

    yield packages_dir, venvs_dir

    # Cleanup
    shutil.rmtree(temp_root)


@pytest.fixture
def sample_package_with_venv(temp_dirs):
    """Create a sample package with venv."""
    packages_dir, venvs_dir = temp_dirs

    # Create minimal package
    pkg_dir = Path(tempfile.mkdtemp())

    manifest = {
        "name": "test-agent",
        "version": "1.0.0",
        "description": "Test",
        "author": "test",
        "entrypoint": "main:app",
    }
    with open(pkg_dir / "agent.yaml", "w") as f:
        yaml.dump(manifest, f)

    # Create minimal requirements
    with open(pkg_dir / "requirements.txt", "w") as f:
        f.write("# No deps for testing\n")

    (pkg_dir / "src").mkdir()
    (pkg_dir / "src" / "__init__.py").touch()

    # Create APKG
    apkg_path = packages_dir / "test.apkg"
    with zipfile.ZipFile(apkg_path, "w") as zf:
        for file_path in pkg_dir.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(pkg_dir))

    shutil.rmtree(pkg_dir)

    # Load package (creates venv)
    loader = PackageLoader(packages_dir, venvs_dir)
    package = loader.load_package(apkg_path, agent_app_id="test-123")

    return package


class TestSubprocessRunner:
    """Test SubprocessAgentRunner."""

    def test_runner_initialization(self, sample_package_with_venv):
        """Test that runner initializes correctly."""
        runner = SubprocessAgentRunner(
            sample_package_with_venv,
            rest_port=8001,
            a2a_port=50052,
            ui_port=3000
        )

        assert runner.package == sample_package_with_venv
        assert runner.rest_port == 8001
        assert runner.a2a_port == 50052
        assert runner.ui_port == 3000
        assert runner.process is None

    def test_runner_requires_venv(self):
        """Test that runner requires venv_path."""
        # Create package without venv
        package = MagicMock(spec=AgentPackage)
        package.venv_path = None

        runner = SubprocessAgentRunner(package, 8001, 50052, 3000)

        with pytest.raises(ValueError, match="venv_path"):
            asyncio.run(runner.start())

    @patch('subprocess.Popen')
    async def test_runner_starts_subprocess(self, mock_popen, sample_package_with_venv):
        """Test that runner starts subprocess with correct command."""
        # Mock subprocess
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.stdout = []
        mock_process.stderr = []
        mock_popen.return_value = mock_process

        runner = SubprocessAgentRunner(
            sample_package_with_venv,
            rest_port=8001,
            a2a_port=50052,
            ui_port=3000,
            multiplexed=True
        )

        await runner.start()

        # Check subprocess was started
        assert mock_popen.called
        call_args = mock_popen.call_args

        # Check command includes venv python
        cmd = call_args[0][0]
        assert "bin/python" in cmd[0]
        assert "-m" in cmd
        assert "pixell_runtime" in cmd

        # Check ports in command
        assert "--rest-port" in cmd
        assert "8001" in cmd
        assert "--a2a-port" in cmd
        assert "50052" in cmd

    @patch('subprocess.Popen')
    async def test_runner_stop_graceful(self, mock_popen, sample_package_with_venv):
        """Test that runner stops gracefully."""
        # Mock subprocess
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.stdout = []
        mock_process.stderr = []
        mock_process.poll.return_value = None  # Running
        mock_process.wait.return_value = None
        mock_popen.return_value = mock_process

        runner = SubprocessAgentRunner(sample_package_with_venv, 8001, 50052, 3000)
        await runner.start()

        # Stop runner
        await runner.stop(timeout=1)

        # Check terminate was called
        assert mock_process.terminate.called
        assert mock_process.wait.called

    @patch('subprocess.Popen')
    async def test_runner_stop_force_kill(self, mock_popen, sample_package_with_venv):
        """Test that runner force kills if timeout."""
        import subprocess

        # Mock subprocess that doesn't terminate
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.stdout = []
        mock_process.stderr = []
        mock_process.poll.return_value = None
        mock_process.wait.side_effect = [subprocess.TimeoutExpired(cmd=[], timeout=1), None]
        mock_popen.return_value = mock_process

        runner = SubprocessAgentRunner(sample_package_with_venv, 8001, 50052, 3000)
        await runner.start()

        # Stop runner with short timeout
        await runner.stop(timeout=1)

        # Check kill was called after timeout
        assert mock_process.terminate.called
        assert mock_process.kill.called

    @patch('subprocess.Popen')
    async def test_runner_is_running(self, mock_popen, sample_package_with_venv):
        """Test is_running property."""
        # Mock subprocess
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_process.stdout = []
        mock_process.stderr = []
        mock_process.poll.return_value = None  # Running
        mock_popen.return_value = mock_process

        runner = SubprocessAgentRunner(sample_package_with_venv, 8001, 50052, 3000)

        # Before start
        assert runner.is_running is False

        # After start
        await runner.start()
        assert runner.is_running is True

        # After exit
        mock_process.poll.return_value = 0  # Exited
        assert runner.is_running is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
