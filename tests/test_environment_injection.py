"""Unit tests for environment variable injection (Issue #15)."""

import json
import pytest
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from pixell_runtime.agents.loader import PackageLoader
from pixell_runtime.core.models import AgentManifest
from pixell_runtime.supervisor.process_manager import ProcessManager
from pixell_runtime.supervisor.models import Ports


class TestEnvironmentExtraction:
    """Test environment variable extraction from agent.yaml and deploy.json."""

    @pytest.fixture
    def temp_package_dir(self):
        """Create a temporary package directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def loader(self, temp_package_dir):
        """Create a PackageLoader instance."""
        packages_dir = temp_package_dir / "packages"
        packages_dir.mkdir()
        return PackageLoader(packages_dir=packages_dir)

    def test_parse_manifest_extracts_environment(self, loader):
        """Test that _parse_manifest extracts environment from agent.yaml."""
        manifest_data = {
            "name": "test-agent",
            "version": "1.0.0",
            "entrypoint": "main:handler",
            "environment": {
                "DB_HOST": "localhost",
                "DB_PORT": "5432",
                "API_KEY": "${API_KEY}",
                "DEBUG": "true"
            }
        }

        manifest = loader._parse_manifest(manifest_data)

        assert isinstance(manifest, AgentManifest)
        assert manifest.environment == {
            "DB_HOST": "localhost",
            "DB_PORT": "5432",
            "API_KEY": "${API_KEY}",
            "DEBUG": "true"
        }

    def test_parse_manifest_without_environment(self, loader):
        """Test that _parse_manifest handles missing environment section."""
        manifest_data = {
            "name": "test-agent",
            "version": "1.0.0",
            "entrypoint": "main:handler",
        }

        manifest = loader._parse_manifest(manifest_data)

        assert manifest.environment == {}

    def test_read_deploy_json_success(self, loader, temp_package_dir):
        """Test successful deploy.json reading."""
        package_dir = temp_package_dir / "test-package"
        package_dir.mkdir()

        deploy_data = {
            "expose": ["rest", "a2a"],
            "ports": {"rest": 8080, "a2a": 50051},
            "environment": {
                "RUNTIME_ENV": "production",
                "API_URL": "https://api.example.com"
            }
        }

        deploy_json = package_dir / "deploy.json"
        deploy_json.write_text(json.dumps(deploy_data))

        result = loader._read_deploy_json(package_dir)

        assert result == deploy_data
        assert result["environment"] == {
            "RUNTIME_ENV": "production",
            "API_URL": "https://api.example.com"
        }

    def test_read_deploy_json_missing_file(self, loader, temp_package_dir):
        """Test that missing deploy.json returns empty dict."""
        package_dir = temp_package_dir / "test-package"
        package_dir.mkdir()

        result = loader._read_deploy_json(package_dir)

        assert result == {}

    def test_read_deploy_json_invalid_json(self, loader, temp_package_dir):
        """Test that invalid deploy.json is handled gracefully."""
        package_dir = temp_package_dir / "test-package"
        package_dir.mkdir()

        deploy_json = package_dir / "deploy.json"
        deploy_json.write_text("{ invalid json }")

        result = loader._read_deploy_json(package_dir)

        assert result == {}

    def test_environment_merging_precedence(self, loader, temp_package_dir):
        """Test that deploy.json environment takes precedence over agent.yaml."""
        package_dir = temp_package_dir / "test-package"
        package_dir.mkdir()

        # Create agent.yaml
        agent_yaml = package_dir / "agent.yaml"
        agent_yaml.write_text("""
name: test-agent
version: 1.0.0
entrypoint: main:handler
environment:
  DB_HOST: localhost
  DB_PORT: "5432"
  APP_MODE: development
""")

        # Create deploy.json (overrides DB_HOST and adds new var)
        deploy_json = package_dir / "deploy.json"
        deploy_data = {
            "environment": {
                "DB_HOST": "prod-db.example.com",  # Override
                "DEPLOYMENT_ID": "abc123"  # New
            }
        }
        deploy_json.write_text(json.dumps(deploy_data))

        # Mock venv creation to avoid actual venv setup
        with patch.object(loader, '_ensure_venv') as mock_venv:
            mock_venv.return_value = temp_package_dir / "venv"

            package = loader.load_from_directory(package_dir, agent_app_id="test-123")

            # Verify merged environment
            expected_env = {
                "DB_HOST": "prod-db.example.com",  # From deploy.json (overrides agent.yaml)
                "DB_PORT": "5432",  # From agent.yaml
                "APP_MODE": "development",  # From agent.yaml
                "DEPLOYMENT_ID": "abc123"  # From deploy.json (new)
            }
            assert package.environment == expected_env


class TestProcessManagerEnvironment:
    """Test environment variable passing in ProcessManager."""

    @pytest.fixture
    def process_manager(self):
        """Create ProcessManager instance."""
        return ProcessManager(graceful_shutdown_timeout_sec=5)

    @pytest.fixture
    def mock_ports(self):
        """Create mock ports."""
        return Ports(rest=63000, a2a=60000, ui=65000)

    @pytest.fixture
    def mock_filesystem(self):
        """Mock filesystem operations."""
        with patch("pathlib.Path.mkdir"), patch("builtins.open", new_callable=MagicMock) as mock_open:
            mock_log_file = MagicMock()
            mock_open.return_value = mock_log_file
            yield mock_open

    @patch("subprocess.Popen")
    def test_spawn_agent_with_package_env(
        self,
        mock_popen,
        process_manager,
        mock_ports,
        mock_filesystem
    ):
        """Test that package_env is added to process environment."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        package_env = {
            "DB_HOST": "localhost",
            "API_KEY": "secret",
            "DEBUG": "true"
        }

        pid = process_manager.spawn_agent(
            agent_app_id="4906eeb7",
            linux_user="agent_4906eeb7",
            package_path=Path("/var/lib/pixell/packages/test.apkg"),
            ports=mock_ports,
            package_env=package_env,
        )

        assert pid == 12345

        # Verify subprocess.Popen was called with merged environment
        call_args = mock_popen.call_args
        kwargs = call_args[1]

        assert "env" in kwargs
        env = kwargs["env"]

        # Check that package_env variables are present
        assert env["DB_HOST"] == "localhost"
        assert env["API_KEY"] == "secret"
        assert env["DEBUG"] == "true"

        # Check that PAR runtime variables are also present
        assert env["AGENT_APP_ID"] == "4906eeb7"
        assert env["REST_PORT"] == "63000"

    @patch("subprocess.Popen")
    def test_spawn_agent_env_precedence(
        self,
        mock_popen,
        process_manager,
        mock_ports,
        mock_filesystem
    ):
        """Test that DeployRequest env overrides package_env."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        package_env = {
            "DB_HOST": "localhost",
            "API_KEY": "package_key"
        }

        deploy_env = {
            "API_KEY": "deploy_key",  # Override package_env
            "DEPLOYMENT_ID": "xyz789"  # New variable
        }

        pid = process_manager.spawn_agent(
            agent_app_id="4906eeb7",
            linux_user="agent_4906eeb7",
            package_path=Path("/var/lib/pixell/packages/test.apkg"),
            ports=mock_ports,
            env=deploy_env,
            package_env=package_env,
        )

        assert pid == 12345

        # Verify environment precedence
        call_args = mock_popen.call_args
        kwargs = call_args[1]
        env = kwargs["env"]

        # deploy_env should override package_env
        assert env["API_KEY"] == "deploy_key"

        # package_env values should be present if not overridden
        assert env["DB_HOST"] == "localhost"

        # deploy_env new values should be present
        assert env["DEPLOYMENT_ID"] == "xyz789"

    @patch("subprocess.Popen")
    def test_spawn_agent_without_package_env(
        self,
        mock_popen,
        process_manager,
        mock_ports,
        mock_filesystem
    ):
        """Test that spawn_agent works without package_env (backward compat)."""
        mock_process = MagicMock()
        mock_process.pid = 12345
        mock_popen.return_value = mock_process

        pid = process_manager.spawn_agent(
            agent_app_id="4906eeb7",
            linux_user="agent_4906eeb7",
            package_path=Path("/var/lib/pixell/packages/test.apkg"),
            ports=mock_ports,
        )

        assert pid == 12345

        # Verify subprocess.Popen was called with basic environment
        call_args = mock_popen.call_args
        kwargs = call_args[1]

        assert "env" in kwargs
        env = kwargs["env"]

        # Check that PAR runtime variables are present
        assert env["AGENT_APP_ID"] == "4906eeb7"
        assert env["REST_PORT"] == "63000"
