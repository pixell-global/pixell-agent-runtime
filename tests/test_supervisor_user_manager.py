"""Unit tests for LinuxUserManager with home directory ownership verification."""

import pytest
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, call

from pixell_runtime.supervisor.user_manager import LinuxUserManager


@pytest.fixture
def user_manager():
    """Create LinuxUserManager instance."""
    return LinuxUserManager(home_base=Path("/home"))


@pytest.fixture
def mock_user_exists():
    """Mock user_exists to return True by default."""
    with patch.object(LinuxUserManager, "user_exists", return_value=True) as mock:
        yield mock


@pytest.fixture
def mock_subprocess_run():
    """Mock subprocess.run for command execution."""
    with patch("subprocess.run") as mock:
        mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        yield mock


@pytest.fixture
def mock_home_exists():
    """Mock Path.exists to return True for home directory."""
    with patch.object(Path, "exists", return_value=True) as mock:
        yield mock


class TestCreateUserOwnershipVerification:
    """Tests for ownership verification in create_user method."""

    def test_create_user_new_user_creates_normally(self, user_manager, mock_subprocess_run):
        """Test creating a new user (no ownership check needed)."""
        with patch.object(LinuxUserManager, "user_exists", return_value=False):
            home_dir = user_manager.create_user("4906eeb7")

            # Should call useradd
            assert mock_subprocess_run.called
            useradd_call = mock_subprocess_run.call_args_list[0]
            assert "useradd" in useradd_call[0][0]
            assert home_dir == Path("/home/agent_4906eeb7")

    def test_create_user_exists_home_not_exists(self, user_manager, mock_user_exists):
        """Test existing user with home directory that doesn't exist."""
        with patch.object(Path, "exists", return_value=False):
            home_dir = user_manager.create_user("4906eeb7")

            # Should return home_dir without attempting ownership check
            assert home_dir == Path("/home/agent_4906eeb7")

    def test_create_user_exists_ownership_correct(self, user_manager, mock_user_exists, mock_home_exists):
        """Test existing user with correct ownership (not root)."""
        # Mock stat to return non-root UID
        mock_stat = MagicMock()
        mock_stat.st_uid = 1001  # Not root

        with patch.object(Path, "stat", return_value=mock_stat) as mock_stat_call:
            home_dir = user_manager.create_user("4906eeb7")

            # Should check stat
            mock_stat_call.assert_called_once()

            # Should return without calling chown/chmod
            assert home_dir == Path("/home/agent_4906eeb7")

    def test_create_user_exists_ownership_root_repaired(self, user_manager, mock_user_exists, mock_home_exists, mock_subprocess_run):
        """Test existing user with root ownership - should repair."""
        # Mock stat to return root UID (0)
        mock_stat = MagicMock()
        mock_stat.st_uid = 0  # Root!

        with patch.object(Path, "stat", return_value=mock_stat):
            home_dir = user_manager.create_user("4906eeb7")

            # Should call chown and chmod
            assert mock_subprocess_run.call_count == 2

            # Verify chown call
            chown_call = mock_subprocess_run.call_args_list[0]
            assert chown_call[0][0] == ["chown", "-R", "agent_4906eeb7:agent_4906eeb7", "/home/agent_4906eeb7"]
            assert chown_call[1]["timeout"] == 30

            # Verify chmod call
            chmod_call = mock_subprocess_run.call_args_list[1]
            assert chmod_call[0][0] == ["chmod", "0700", "/home/agent_4906eeb7"]
            assert chmod_call[1]["timeout"] == 5

            assert home_dir == Path("/home/agent_4906eeb7")

    def test_create_user_exists_ownership_repair_chown_fails(self, user_manager, mock_user_exists, mock_home_exists):
        """Test ownership repair continues when chown fails."""
        # Mock stat to return root UID
        mock_stat = MagicMock()
        mock_stat.st_uid = 0

        with patch.object(Path, "stat", return_value=mock_stat), \
             patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "chown", stderr="Permission denied")):

            # Should not raise exception (non-blocking error)
            home_dir = user_manager.create_user("4906eeb7")

            # Should still return home_dir
            assert home_dir == Path("/home/agent_4906eeb7")

    def test_create_user_exists_ownership_repair_chmod_fails(self, user_manager, mock_user_exists, mock_home_exists):
        """Test ownership repair continues when chmod fails."""
        # Mock stat to return root UID
        mock_stat = MagicMock()
        mock_stat.st_uid = 0

        def mock_run(cmd, **kwargs):
            if "chown" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            elif "chmod" in cmd:
                raise subprocess.CalledProcessError(1, "chmod", stderr="Permission denied")
            return MagicMock(returncode=0)

        with patch.object(Path, "stat", return_value=mock_stat), \
             patch("subprocess.run", side_effect=mock_run):

            # Should not raise exception
            home_dir = user_manager.create_user("4906eeb7")
            assert home_dir == Path("/home/agent_4906eeb7")

    def test_create_user_exists_stat_fails(self, user_manager, mock_user_exists, mock_home_exists):
        """Test handles stat() failures gracefully."""
        with patch.object(Path, "stat", side_effect=OSError("Permission denied")):
            # Should not raise exception
            home_dir = user_manager.create_user("4906eeb7")
            assert home_dir == Path("/home/agent_4906eeb7")

    def test_create_user_with_short_ids_ownership_repair(self, user_manager, mock_user_exists, mock_home_exists, mock_subprocess_run):
        """Test ownership repair works with short IDs."""
        # Mock stat to return root UID
        mock_stat = MagicMock()
        mock_stat.st_uid = 0

        with patch.object(Path, "stat", return_value=mock_stat):
            home_dir = user_manager.create_user(
                "4906eeb7-9959-414e-84c6-f2445822ebe4",
                org_short_id="8c82966883524dad",
                agent_short_id="4906eeb7"
            )

            # Should repair with correct username
            chown_call = mock_subprocess_run.call_args_list[0]
            assert "agent_8c82966883524dad_4906eeb7:agent_8c82966883524dad_4906eeb7" in chown_call[0][0]
            assert home_dir == Path("/home/agent_8c82966883524dad_4906eeb7")

    def test_create_user_ownership_check_logs_correctly(self, user_manager, mock_user_exists, mock_home_exists):
        """Test that ownership check logs appropriate messages."""
        # Mock stat to return non-root UID
        mock_stat = MagicMock()
        mock_stat.st_uid = 1001

        with patch.object(Path, "stat", return_value=mock_stat), \
             patch("pixell_runtime.supervisor.user_manager.logger") as mock_logger:

            user_manager.create_user("4906eeb7")

            # Should log that user exists
            mock_logger.info.assert_called()

    def test_create_user_ownership_repair_logs_warning(self, user_manager, mock_user_exists, mock_home_exists, mock_subprocess_run):
        """Test that ownership repair logs warning."""
        # Mock stat to return root UID
        mock_stat = MagicMock()
        mock_stat.st_uid = 0

        with patch.object(Path, "stat", return_value=mock_stat), \
             patch("pixell_runtime.supervisor.user_manager.logger") as mock_logger:

            user_manager.create_user("4906eeb7")

            # Should log warning about root ownership
            mock_logger.warning.assert_called()
            warning_call = mock_logger.warning.call_args
            assert "owned by root" in warning_call[0][0].lower()

    def test_create_user_ownership_repair_logs_success(self, user_manager, mock_user_exists, mock_home_exists, mock_subprocess_run):
        """Test that successful repair logs info."""
        # Mock stat to return root UID
        mock_stat = MagicMock()
        mock_stat.st_uid = 0

        with patch.object(Path, "stat", return_value=mock_stat), \
             patch("pixell_runtime.supervisor.user_manager.logger") as mock_logger:

            user_manager.create_user("4906eeb7")

            # Should log success
            assert any("repaired" in str(call).lower() for call in mock_logger.info.call_args_list)

    def test_create_user_ownership_repair_logs_error_on_failure(self, user_manager, mock_user_exists, mock_home_exists):
        """Test that failed repair logs error."""
        # Mock stat to return root UID
        mock_stat = MagicMock()
        mock_stat.st_uid = 0

        with patch.object(Path, "stat", return_value=mock_stat), \
             patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "chown", stderr="Failed")), \
             patch("pixell_runtime.supervisor.user_manager.logger") as mock_logger:

            user_manager.create_user("4906eeb7")

            # Should log error
            mock_logger.error.assert_called()
            error_call = mock_logger.error.call_args
            assert "failed" in error_call[0][0].lower()

    def test_create_user_multiple_calls_idempotent(self, user_manager, mock_user_exists, mock_home_exists):
        """Test multiple calls are idempotent."""
        # Mock stat to return non-root UID (correct ownership)
        mock_stat = MagicMock()
        mock_stat.st_uid = 1001

        with patch.object(Path, "stat", return_value=mock_stat) as mock_stat_call, \
             patch("subprocess.run") as mock_run:

            # Call twice
            home_dir1 = user_manager.create_user("4906eeb7")
            home_dir2 = user_manager.create_user("4906eeb7")

            # Should return same path
            assert home_dir1 == home_dir2

            # Should not call chown/chmod (ownership already correct)
            mock_run.assert_not_called()

    def test_create_user_ownership_timeout_parameters(self, user_manager, mock_user_exists, mock_home_exists, mock_subprocess_run):
        """Test that chown/chmod have appropriate timeouts."""
        # Mock stat to return root UID
        mock_stat = MagicMock()
        mock_stat.st_uid = 0

        with patch.object(Path, "stat", return_value=mock_stat):
            user_manager.create_user("4906eeb7")

            # Verify timeouts
            chown_call = mock_subprocess_run.call_args_list[0]
            assert chown_call[1]["timeout"] == 30  # chown should have 30s timeout

            chmod_call = mock_subprocess_run.call_args_list[1]
            assert chmod_call[1]["timeout"] == 5   # chmod should have 5s timeout


class TestUsernameGeneration:
    """Tests for username generation logic."""

    def test_get_username_with_short_ids(self, user_manager):
        """Test username generation with short IDs."""
        username = user_manager.get_username(
            "4906eeb7-9959-414e-84c6-f2445822ebe4",
            org_short_id="8c82966883524dad",
            agent_short_id="4906eeb7"
        )
        assert username == "agent_8c82966883524dad_4906eeb7"

    def test_get_username_without_short_ids(self, user_manager):
        """Test username generation without short IDs (legacy)."""
        username = user_manager.get_username("4906eeb7")
        assert username == "agent_4906eeb7"

    def test_get_username_with_uuid(self, user_manager):
        """Test username generation with full UUID."""
        username = user_manager.get_username("4906eeb7-9959-414e-84c6-f2445822ebe4")
        assert username == "agent_4906eeb7_9959_414e_84c6_f2445822ebe4"
