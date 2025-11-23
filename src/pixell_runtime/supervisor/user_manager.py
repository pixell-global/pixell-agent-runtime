"""Linux user management for agent isolation."""

import subprocess
import structlog
from pathlib import Path
from typing import Optional

logger = structlog.get_logger()


class LinuxUserManager:
    """Manages Linux users for agent isolation.

    Each agent runs as a dedicated Linux user (e.g., agent_4906eeb7) with:
    - Home directory at /home/agent_xxx/
    - No login shell (security)
    - Restricted permissions
    """

    def __init__(self, home_base: Path = Path("/home")):
        """Initialize user manager.

        Args:
            home_base: Base directory for user home directories (default: /home)
        """
        self.home_base = home_base
        logger.info("LinuxUserManager initialized", home_base=str(home_base))

    def get_username(self, agent_app_id: str) -> str:
        """Get username for an agent.

        Args:
            agent_app_id: Agent identifier (e.g., '4906eeb7' or '4906eeb7-9959-414e-84c6-f2445822ebe4')

        Returns:
            Username (e.g., 'agent_4906eeb7' or 'agent_4906eeb7_9959_414e_84c6_f2445822ebe4')

        Note:
            Hyphens in agent_app_id are replaced with underscores to comply with
            POSIX username requirements (useradd rejects hyphens).
        """
        # Sanitize agent_app_id: replace hyphens with underscores for valid Linux usernames
        sanitized_id = "_".join(agent_app_id.split("-")[0:2]) # TODO: 임시 버그 패치, 원본 코드 의도 파악 필요
        return f"agent_{sanitized_id}"

    def get_home_dir(self, agent_app_id: str) -> Path:
        """Get home directory path for an agent.

        Args:
            agent_app_id: Agent identifier

        Returns:
            Home directory path (e.g., /home/agent_4906eeb7)
        """
        return self.home_base / self.get_username(agent_app_id)

    def user_exists(self, agent_app_id: str) -> bool:
        """Check if user already exists.

        Args:
            agent_app_id: Agent identifier

        Returns:
            True if user exists, False otherwise
        """
        username = self.get_username(agent_app_id)
        try:
            # Use 'id' command to check if user exists
            result = subprocess.run(
                ["id", username],
                capture_output=True,
                text=True,
                check=False
            )
            exists = result.returncode == 0
            logger.debug("Checked user existence", username=username, exists=exists)
            return exists
        except Exception as e:
            logger.error("Failed to check user existence", username=username, error=str(e))
            return False

    def create_user(self, agent_app_id: str) -> Path:
        """Create a new Linux user for an agent.

        Args:
            agent_app_id: Agent identifier

        Returns:
            Home directory path

        Raises:
            RuntimeError: If user creation fails
        """
        username = self.get_username(agent_app_id)
        home_dir = self.get_home_dir(agent_app_id)

        # Check if user already exists
        if self.user_exists(agent_app_id):
            logger.info("User already exists", username=username)
            return home_dir

        try:
            # Create user with:
            # - Home directory
            # - No login shell (security)
            # - System user (no aging)
            logger.info("Creating Linux user", username=username, home_dir=str(home_dir))

            subprocess.run(
                [
                    "useradd",
                    "--system",              # System user (no aging)
                    "--shell", "/bin/false", # No login shell
                    "--home-dir", str(home_dir),
                    "--create-home",         # Create home directory
                    username
                ],
                capture_output=True,
                text=True,
                check=True
            )

            logger.info("Created Linux user", username=username, home_dir=str(home_dir))
            return home_dir

        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to create user {username}: {e.stderr}"
            logger.error("User creation failed", username=username, error=e.stderr)
            raise RuntimeError(error_msg) from e

    def delete_user(self, agent_app_id: str, remove_home: bool = True) -> None:
        """Delete a Linux user.

        Args:
            agent_app_id: Agent identifier
            remove_home: Whether to remove home directory (default: True)

        Raises:
            RuntimeError: If user deletion fails
        """
        username = self.get_username(agent_app_id)

        # Check if user exists
        if not self.user_exists(agent_app_id):
            logger.info("User does not exist, nothing to delete", username=username)
            return

        try:
            logger.info("Deleting Linux user", username=username, remove_home=remove_home)

            cmd = ["userdel"]
            if remove_home:
                cmd.append("--remove")  # Remove home directory and mail spool
            cmd.append(username)

            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            logger.info("Deleted Linux user", username=username)

        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to delete user {username}: {e.stderr}"
            logger.error("User deletion failed", username=username, error=e.stderr)
            raise RuntimeError(error_msg) from e

    def ensure_directories(self, agent_app_id: str) -> dict[str, Path]:
        """Ensure required directories exist for an agent.

        Creates directories for:
        - Virtual environments
        - Package cache
        - Logs

        Args:
            agent_app_id: Agent identifier

        Returns:
            Dictionary mapping directory names to paths
        """
        home_dir = self.get_home_dir(agent_app_id)
        username = self.get_username(agent_app_id)

        directories = {
            "venv": home_dir / "venv",
            "packages": home_dir / "packages",
            "logs": home_dir / "logs",
        }

        for name, path in directories.items():
            if not path.exists():
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    # Set ownership to agent user
                    subprocess.run(
                        ["chown", "-R", f"{username}:{username}", str(path)],
                        capture_output=True,
                        text=True,
                        check=True
                    )
                    logger.info("Created directory", path=str(path), owner=username)
                except Exception as e:
                    logger.error("Failed to create directory", path=str(path), error=str(e))
                    raise

        return directories
