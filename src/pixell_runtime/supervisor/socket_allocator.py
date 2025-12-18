"""Socket path allocation for agent services.

This module provides Unix domain socket path allocation for agents,
replacing TCP ports with socket files for unlimited agent capacity.

Socket Architecture:
- Base directory: /var/run/pixell-agents/
- Per-agent directory: /var/run/pixell-agents/agent_{short_id}/
- Socket files: rest.sock, a2a.sock, ui.sock

Benefits over ports:
- No 200-agent limit (port exhaustion)
- Better security (file permissions vs network)
- Lower latency (no TCP overhead)
- Simpler ALB configuration (single target group)
"""

import os
import shutil
import subprocess
import structlog
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = structlog.get_logger()

# Base directory for all agent sockets
SOCKET_BASE_DIR = Path(os.getenv("SOCKET_BASE_DIR", "/var/run/pixell-agents"))

# Unix socket path length limit (including null terminator)
# Linux: 108 bytes, macOS: 104 bytes
# We use 100 as safe limit
MAX_SOCKET_PATH_LENGTH = 100


@dataclass
class SocketPaths:
    """Socket paths for an agent.

    Contains paths to Unix domain sockets for each service:
    - rest: REST API server socket
    - a2a: A2A gRPC server socket
    - ui: UI server socket (for future use, currently multiplexed on REST)
    """
    base_dir: Path
    rest: Path
    a2a: Path
    ui: Path

    def __post_init__(self):
        """Validate socket paths don't exceed Unix limit."""
        for name, path in [("rest", self.rest), ("a2a", self.a2a), ("ui", self.ui)]:
            path_str = str(path)
            if len(path_str) >= MAX_SOCKET_PATH_LENGTH:
                raise ValueError(
                    f"Socket path too long for {name}: {len(path_str)} chars "
                    f"(max {MAX_SOCKET_PATH_LENGTH}). Path: {path_str}"
                )

    def all_exist(self) -> bool:
        """Check if all socket files exist."""
        return self.rest.exists() and self.a2a.exists() and self.ui.exists()

    def any_exist(self) -> bool:
        """Check if any socket files exist."""
        return self.rest.exists() or self.a2a.exists() or self.ui.exists()

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "base_dir": str(self.base_dir),
            "rest": str(self.rest),
            "a2a": str(self.a2a),
            "ui": str(self.ui),
        }


class SocketAllocator:
    """Allocates Unix domain socket paths for agent services.

    Unlike PortAllocator which has a 200-agent limit, SocketAllocator
    can support unlimited agents (limited only by disk space and inodes).

    Socket paths are deterministic based on agent_app_id, using the first
    8 characters as a short ID to keep paths under Unix socket length limits.

    Directory structure:
        /var/run/pixell-agents/
        └── agent_{short_id}/
            ├── rest.sock
            ├── a2a.sock
            └── ui.sock
    """

    def __init__(self, base_dir: Optional[Path] = None):
        """Initialize socket allocator.

        Args:
            base_dir: Base directory for socket files (default: /var/run/pixell-agents)
        """
        self.base_dir = base_dir or SOCKET_BASE_DIR

        logger.info(
            "SocketAllocator initialized",
            base_dir=str(self.base_dir),
            note="Unlimited agent capacity via Unix domain sockets"
        )

    @staticmethod
    def extract_short_id(agent_app_id: str) -> str:
        """Extract short ID from agent_app_id.

        Takes the first segment (before first hyphen) and returns up to 8 chars.
        This matches Nginx's map extraction for socket routing.

        Examples:
            "4906eeb7-9959-4a2b-..." -> "4906eeb7" (UUID format)
            "myagent-test-001" -> "myagent" (custom format)
            "abcd1234" -> "abcd1234" (no hyphens)

        Args:
            agent_app_id: Full agent identifier (typically UUID)

        Returns:
            First 8 characters of the first segment (before hyphen)
        """
        # Take first segment before hyphen, then first 8 chars
        first_segment = agent_app_id.split('-')[0]
        return first_segment[:8].lower()

    def get_agent_dir(self, agent_app_id: str, short_id: Optional[str] = None) -> Path:
        """Get the socket directory path for an agent.

        Args:
            agent_app_id: Agent identifier (UUID)
            short_id: Explicit short ID from PAC (preferred if provided)

        Returns:
            Path to agent's socket directory
        """
        # Use explicit short_id from PAC if provided, otherwise extract from UUID
        effective_short_id = short_id if short_id else self.extract_short_id(agent_app_id)
        return self.base_dir / f"agent_{effective_short_id}"

    def allocate(self, agent_app_id: str, short_id: Optional[str] = None) -> SocketPaths:
        """Allocate socket paths for an agent.

        This method is deterministic - the same agent_app_id/short_id always
        returns the same socket paths. Does NOT create the directory.

        Args:
            agent_app_id: Agent identifier (UUID)
            short_id: Explicit short ID from PAC (preferred if provided)

        Returns:
            SocketPaths with paths for rest, a2a, and ui sockets

        Raises:
            ValueError: If resulting socket paths exceed Unix limit
        """
        agent_dir = self.get_agent_dir(agent_app_id, short_id)
        effective_short_id = short_id if short_id else self.extract_short_id(agent_app_id)

        paths = SocketPaths(
            base_dir=agent_dir,
            rest=agent_dir / "rest.sock",
            a2a=agent_dir / "a2a.sock",
            ui=agent_dir / "ui.sock",
        )

        logger.info(
            "Allocated socket paths",
            agent_app_id=agent_app_id,
            short_id=effective_short_id,
            base_dir=str(paths.base_dir),
            rest=str(paths.rest),
            a2a=str(paths.a2a),
            ui=str(paths.ui),
        )

        return paths

    def create_agent_directory(
        self,
        agent_app_id: str,
        owner: Optional[str] = None,
        group: Optional[str] = None,
        short_id: Optional[str] = None,
    ) -> SocketPaths:
        """Create socket directory for an agent with proper permissions.

        Creates the directory structure:
            /var/run/pixell-agents/agent_{short_id}/

        Directory is created with mode 0o750 (rwxr-x---) to allow:
        - Owner (agent user): full access
        - Group (nginx): read/execute for socket access
        - Others: no access

        Args:
            agent_app_id: Agent identifier (UUID)
            owner: Directory owner (default: current user)
            group: Directory group (default: "nginx" for socket access)
            short_id: Explicit short ID from PAC (preferred if provided)

        Returns:
            SocketPaths with allocated paths

        Raises:
            RuntimeError: If directory creation fails
        """
        paths = self.allocate(agent_app_id, short_id)
        agent_dir = paths.base_dir

        try:
            # Ensure base directory exists
            self.base_dir.mkdir(parents=True, exist_ok=True)

            # Create agent directory
            agent_dir.mkdir(parents=True, exist_ok=True)

            # Set permissions: rwxr-x--- (750)
            agent_dir.chmod(0o750)

            # Set ownership if specified
            if owner or group:
                self._set_ownership(agent_dir, owner, group)

            logger.info(
                "Created agent socket directory",
                agent_app_id=agent_app_id,
                path=str(agent_dir),
                owner=owner,
                group=group,
                mode="0750",
            )

            return paths

        except Exception as e:
            logger.error(
                "Failed to create agent socket directory",
                agent_app_id=agent_app_id,
                path=str(agent_dir),
                error=str(e),
            )
            raise RuntimeError(
                f"Failed to create socket directory for {agent_app_id}: {e}"
            ) from e

    def _set_ownership(
        self,
        path: Path,
        owner: Optional[str] = None,
        group: Optional[str] = None,
    ) -> None:
        """Set ownership of a path using chown.

        Args:
            path: Path to set ownership on
            owner: Owner username (optional)
            group: Group name (optional)
        """
        if not owner and not group:
            return

        ownership = f"{owner or ''}:{group or ''}"

        try:
            result = subprocess.run(
                ["chown", ownership, str(path)],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            logger.debug(
                "Set ownership",
                path=str(path),
                ownership=ownership,
            )
        except subprocess.CalledProcessError as e:
            logger.warning(
                "Failed to set ownership (may need root)",
                path=str(path),
                ownership=ownership,
                error=e.stderr,
            )
        except FileNotFoundError:
            logger.warning(
                "chown command not found",
                path=str(path),
                note="Skipping ownership change"
            )

    def cleanup(self, agent_app_id: str) -> bool:
        """Clean up socket directory for an agent.

        Removes the entire agent directory including all socket files.
        Safe to call even if directory doesn't exist.

        Args:
            agent_app_id: Agent identifier

        Returns:
            True if directory was removed, False if it didn't exist
        """
        agent_dir = self.get_agent_dir(agent_app_id)

        if not agent_dir.exists():
            logger.debug(
                "Socket directory does not exist, nothing to clean",
                agent_app_id=agent_app_id,
                path=str(agent_dir),
            )
            return False

        try:
            # Remove stale socket files first (they might be in use)
            for sock_file in agent_dir.glob("*.sock"):
                try:
                    sock_file.unlink()
                except Exception as e:
                    logger.warning(
                        "Failed to remove socket file",
                        file=str(sock_file),
                        error=str(e),
                    )

            # Remove the directory
            shutil.rmtree(agent_dir)

            logger.info(
                "Cleaned up agent socket directory",
                agent_app_id=agent_app_id,
                path=str(agent_dir),
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to cleanup socket directory",
                agent_app_id=agent_app_id,
                path=str(agent_dir),
                error=str(e),
            )
            # Don't raise - cleanup failures shouldn't block operations
            return False

    def remove_stale_socket(self, socket_path: Path) -> bool:
        """Remove a stale socket file if it exists.

        Should be called before binding to a socket to ensure
        no stale socket file blocks the bind operation.

        Args:
            socket_path: Path to socket file

        Returns:
            True if socket was removed, False if it didn't exist
        """
        if not socket_path.exists():
            return False

        try:
            socket_path.unlink()
            logger.info(
                "Removed stale socket file",
                path=str(socket_path),
            )
            return True
        except Exception as e:
            logger.warning(
                "Failed to remove stale socket",
                path=str(socket_path),
                error=str(e),
            )
            return False

    def validate_socket_paths(self, paths: SocketPaths) -> bool:
        """Validate that socket paths are usable.

        Checks:
        - Base directory exists
        - Socket files exist and are actual sockets

        Args:
            paths: SocketPaths to validate

        Returns:
            True if all sockets exist and are valid
        """
        if not paths.base_dir.exists():
            return False

        for name, sock_path in [
            ("rest", paths.rest),
            ("a2a", paths.a2a),
            ("ui", paths.ui),
        ]:
            if not sock_path.exists():
                logger.debug(f"Socket {name} does not exist: {sock_path}")
                return False

            # Check if it's actually a socket (not a regular file)
            import stat
            try:
                mode = sock_path.stat().st_mode
                if not stat.S_ISSOCK(mode):
                    logger.warning(
                        f"Path exists but is not a socket: {sock_path}",
                        mode=oct(mode),
                    )
                    return False
            except Exception as e:
                logger.warning(f"Failed to stat socket {sock_path}: {e}")
                return False

        return True
