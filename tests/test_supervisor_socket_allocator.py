"""Unit tests for SocketAllocator."""

import os
import stat
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from pixell_runtime.supervisor.socket_allocator import (
    SocketAllocator,
    SocketPaths,
    SOCKET_BASE_DIR,
    MAX_SOCKET_PATH_LENGTH,
)


@pytest.fixture
def temp_socket_dir():
    """Create a temporary directory for socket tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def socket_allocator(temp_socket_dir):
    """Create a SocketAllocator with a temporary base directory."""
    return SocketAllocator(base_dir=temp_socket_dir)


class TestSocketPaths:
    """Tests for SocketPaths dataclass."""

    def test_socket_paths_creation(self, temp_socket_dir):
        """Test creating SocketPaths with valid paths."""
        paths = SocketPaths(
            base_dir=temp_socket_dir / "agent_test1234",
            rest=temp_socket_dir / "agent_test1234" / "rest.sock",
            a2a=temp_socket_dir / "agent_test1234" / "a2a.sock",
            ui=temp_socket_dir / "agent_test1234" / "ui.sock",
        )
        assert paths.base_dir == temp_socket_dir / "agent_test1234"
        assert paths.rest == temp_socket_dir / "agent_test1234" / "rest.sock"
        assert paths.a2a == temp_socket_dir / "agent_test1234" / "a2a.sock"
        assert paths.ui == temp_socket_dir / "agent_test1234" / "ui.sock"

    def test_socket_paths_to_dict(self, temp_socket_dir):
        """Test SocketPaths.to_dict() method."""
        paths = SocketPaths(
            base_dir=temp_socket_dir / "agent_test1234",
            rest=temp_socket_dir / "agent_test1234" / "rest.sock",
            a2a=temp_socket_dir / "agent_test1234" / "a2a.sock",
            ui=temp_socket_dir / "agent_test1234" / "ui.sock",
        )
        d = paths.to_dict()
        assert "base_dir" in d
        assert "rest" in d
        assert "a2a" in d
        assert "ui" in d

    def test_socket_paths_all_exist_false_when_missing(self, temp_socket_dir):
        """Test all_exist returns False when sockets don't exist."""
        paths = SocketPaths(
            base_dir=temp_socket_dir / "agent_test1234",
            rest=temp_socket_dir / "agent_test1234" / "rest.sock",
            a2a=temp_socket_dir / "agent_test1234" / "a2a.sock",
            ui=temp_socket_dir / "agent_test1234" / "ui.sock",
        )
        assert paths.all_exist() is False
        assert paths.any_exist() is False


class TestSocketAllocator:
    """Tests for SocketAllocator class."""

    def test_socket_allocator_init(self, temp_socket_dir):
        """Test SocketAllocator initialization."""
        allocator = SocketAllocator(base_dir=temp_socket_dir)
        assert allocator.base_dir == temp_socket_dir

    def test_socket_allocator_default_base_dir(self):
        """Test SocketAllocator uses default base dir when not specified."""
        allocator = SocketAllocator()
        assert allocator.base_dir == SOCKET_BASE_DIR

    def test_extract_short_id_uuid(self):
        """Test extracting short ID from UUID-format agent_app_id."""
        short_id = SocketAllocator.extract_short_id("4906eeb7-9959-4a2b-8c1d-123456789012")
        assert short_id == "4906eeb7"

    def test_extract_short_id_no_hyphens(self):
        """Test extracting short ID when there are no hyphens."""
        short_id = SocketAllocator.extract_short_id("4906eeb79959")
        assert short_id == "4906eeb7"

    def test_extract_short_id_short_input(self):
        """Test extracting short ID from short input."""
        short_id = SocketAllocator.extract_short_id("abc")
        assert short_id == "abc"

    def test_extract_short_id_lowercase(self):
        """Test that short ID is lowercased."""
        short_id = SocketAllocator.extract_short_id("4906EEB7-XXXX")
        assert short_id == "4906eeb7"

    def test_get_agent_dir(self, socket_allocator, temp_socket_dir):
        """Test getting agent directory path."""
        agent_dir = socket_allocator.get_agent_dir("4906eeb7-9959-4a2b-8c1d-123456789012")
        assert agent_dir == temp_socket_dir / "agent_4906eeb7"

    def test_allocate_returns_correct_paths(self, socket_allocator, temp_socket_dir):
        """Test allocate returns correct socket paths."""
        paths = socket_allocator.allocate("4906eeb7-9959-4a2b-8c1d-123456789012")

        assert paths.base_dir == temp_socket_dir / "agent_4906eeb7"
        assert paths.rest == temp_socket_dir / "agent_4906eeb7" / "rest.sock"
        assert paths.a2a == temp_socket_dir / "agent_4906eeb7" / "a2a.sock"
        assert paths.ui == temp_socket_dir / "agent_4906eeb7" / "ui.sock"

    def test_allocate_deterministic(self, socket_allocator):
        """Test that allocate is deterministic - same ID always returns same paths."""
        agent_id = "4906eeb7-9959-4a2b-8c1d-123456789012"
        paths1 = socket_allocator.allocate(agent_id)
        paths2 = socket_allocator.allocate(agent_id)

        assert paths1.base_dir == paths2.base_dir
        assert paths1.rest == paths2.rest
        assert paths1.a2a == paths2.a2a
        assert paths1.ui == paths2.ui

    def test_allocate_different_agents_get_different_paths(self, socket_allocator):
        """Test that different agents get different socket paths."""
        paths1 = socket_allocator.allocate("4906eeb7-0000-0000-0000-000000000001")
        paths2 = socket_allocator.allocate("ed8784f3-0000-0000-0000-000000000002")

        assert paths1.base_dir != paths2.base_dir
        assert paths1.rest != paths2.rest

    def test_create_agent_directory(self, socket_allocator, temp_socket_dir):
        """Test creating agent socket directory."""
        paths = socket_allocator.create_agent_directory("4906eeb7-9959-4a2b-8c1d-123456789012")

        # Directory should exist
        assert paths.base_dir.exists()
        assert paths.base_dir.is_dir()

        # Check permissions (750 = rwxr-x---)
        mode = paths.base_dir.stat().st_mode
        assert stat.S_IMODE(mode) == 0o750

    def test_create_agent_directory_idempotent(self, socket_allocator):
        """Test that calling create_agent_directory twice doesn't fail."""
        agent_id = "4906eeb7-9959-4a2b-8c1d-123456789012"

        # Create twice
        paths1 = socket_allocator.create_agent_directory(agent_id)
        paths2 = socket_allocator.create_agent_directory(agent_id)

        assert paths1.base_dir == paths2.base_dir
        assert paths1.base_dir.exists()

    def test_cleanup_removes_directory(self, socket_allocator):
        """Test cleanup removes the agent socket directory."""
        agent_id = "4906eeb7-9959-4a2b-8c1d-123456789012"

        # Create directory
        paths = socket_allocator.create_agent_directory(agent_id)
        assert paths.base_dir.exists()

        # Create a dummy socket file
        paths.rest.touch()
        assert paths.rest.exists()

        # Cleanup
        result = socket_allocator.cleanup(agent_id)
        assert result is True
        assert not paths.base_dir.exists()

    def test_cleanup_nonexistent_no_error(self, socket_allocator):
        """Test cleanup on nonexistent directory doesn't raise error."""
        result = socket_allocator.cleanup("nonexistent-agent-id")
        assert result is False

    def test_remove_stale_socket(self, socket_allocator):
        """Test removing a stale socket file."""
        # Create a dummy file
        agent_id = "4906eeb7-9959-4a2b-8c1d-123456789012"
        paths = socket_allocator.create_agent_directory(agent_id)
        paths.rest.touch()
        assert paths.rest.exists()

        # Remove stale socket
        result = socket_allocator.remove_stale_socket(paths.rest)
        assert result is True
        assert not paths.rest.exists()

    def test_remove_stale_socket_nonexistent(self, socket_allocator):
        """Test removing nonexistent socket returns False."""
        nonexistent = Path("/tmp/nonexistent_socket.sock")
        result = socket_allocator.remove_stale_socket(nonexistent)
        assert result is False

    def test_validate_socket_paths_all_exist(self, socket_allocator):
        """Test validate_socket_paths when all sockets exist as actual sockets."""
        import socket

        agent_id = "4906eeb7-9959-4a2b-8c1d-123456789012"
        paths = socket_allocator.create_agent_directory(agent_id)

        # Create actual Unix sockets
        for sock_path in [paths.rest, paths.a2a, paths.ui]:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(str(sock_path))
            sock.close()

        result = socket_allocator.validate_socket_paths(paths)
        assert result is True

    def test_validate_socket_paths_missing_sockets(self, socket_allocator):
        """Test validate_socket_paths returns False when sockets are missing."""
        agent_id = "4906eeb7-9959-4a2b-8c1d-123456789012"
        paths = socket_allocator.create_agent_directory(agent_id)

        # Don't create any socket files
        result = socket_allocator.validate_socket_paths(paths)
        assert result is False

    def test_validate_socket_paths_regular_file_not_socket(self, socket_allocator):
        """Test validate_socket_paths returns False for regular files (not sockets)."""
        agent_id = "4906eeb7-9959-4a2b-8c1d-123456789012"
        paths = socket_allocator.create_agent_directory(agent_id)

        # Create regular files instead of sockets
        paths.rest.touch()
        paths.a2a.touch()
        paths.ui.touch()

        result = socket_allocator.validate_socket_paths(paths)
        assert result is False


class TestSocketPathLengthLimit:
    """Tests for Unix socket path length limits."""

    def test_socket_path_length_under_limit(self, temp_socket_dir):
        """Test that socket paths are under the Unix limit."""
        allocator = SocketAllocator(base_dir=temp_socket_dir)
        paths = allocator.allocate("4906eeb7-9959-4a2b-8c1d-123456789012")

        # All paths should be under the limit
        assert len(str(paths.rest)) < MAX_SOCKET_PATH_LENGTH
        assert len(str(paths.a2a)) < MAX_SOCKET_PATH_LENGTH
        assert len(str(paths.ui)) < MAX_SOCKET_PATH_LENGTH

    def test_socket_path_validation_fails_for_long_paths(self):
        """Test that SocketPaths validation fails for paths over 100 chars."""
        # Create a very long path
        long_dir = Path("/tmp/" + "a" * 100)

        with pytest.raises(ValueError, match="Socket path too long"):
            SocketPaths(
                base_dir=long_dir,
                rest=long_dir / "rest.sock",
                a2a=long_dir / "a2a.sock",
                ui=long_dir / "ui.sock",
            )


class TestOwnershipSetting:
    """Tests for ownership setting (requires mocking on non-root systems)."""

    def test_set_ownership_calls_chown(self, socket_allocator):
        """Test that _set_ownership calls chown with correct args."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            socket_allocator._set_ownership(
                Path("/tmp/test"),
                owner="agent",
                group="nginx"
            )

            mock_run.assert_called_once()
            call_args = mock_run.call_args[0][0]
            assert call_args[0] == "chown"
            assert "agent:nginx" in call_args[1]

    def test_set_ownership_handles_missing_chown(self, socket_allocator):
        """Test that _set_ownership handles missing chown gracefully."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("chown not found")

            # Should not raise
            socket_allocator._set_ownership(
                Path("/tmp/test"),
                owner="agent",
                group="nginx"
            )
