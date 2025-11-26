"""Linux user management for agent isolation."""

import os
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

    def get_username(self, agent_app_id: str, org_short_id: Optional[str] = None, agent_short_id: Optional[str] = None) -> str:
        """Get username for an agent.

        Args:
            agent_app_id: Agent identifier (e.g., '4906eeb7' or '4906eeb7-9959-414e-84c6-f2445822ebe4')
            org_short_id: Optional organization short ID (16 chars, no hyphens, e.g., 'x8f2k9m4n7p1q3r5')
            agent_short_id: Optional agent short ID (8 chars, no hyphens, e.g., 'a7b2c9d4')

        Returns:
            Username (e.g., 'agent_x8f2k9m4n7p1q3r5_a7b2c9d4' with short IDs,
                     or 'agent_4906eeb7_9959_414e_84c6_f2445822ebe4' without)

        Note:
            If org_short_id and agent_short_id are provided, they are used to generate
            a shorter username (31 chars total): agent_{org_short_id}_{agent_short_id}

            Otherwise, falls back to sanitizing agent_app_id by replacing hyphens with
            underscores (44 chars for full UUID). This maintains backward compatibility.
        """
        # NEW: If short IDs provided, use them for compact username generation
        if org_short_id and agent_short_id:
            return f"agent_{org_short_id}_{agent_short_id}"

        # LEGACY: Sanitize agent_app_id: replace hyphens with underscores for valid Linux usernames
        sanitized_id = "_".join(agent_app_id.split("-")[0:2])
        return f"agent_{sanitized_id}"

    def get_home_dir(self, agent_app_id: str, org_short_id: Optional[str] = None, agent_short_id: Optional[str] = None) -> Path:
        """Get home directory path for an agent.

        Args:
            agent_app_id: Agent identifier
            org_short_id: Optional organization short ID (16 chars)
            agent_short_id: Optional agent short ID (8 chars)

        Returns:
            Home directory path (e.g., /home/agent_x8f2k9m4n7p1q3r5_a7b2c9d4)
        """
        return self.home_base / self.get_username(agent_app_id, org_short_id, agent_short_id)

    def user_exists(self, agent_app_id: str, org_short_id: Optional[str] = None, agent_short_id: Optional[str] = None) -> bool:
        """Check if user already exists.

        Args:
            agent_app_id: Agent identifier
            org_short_id: Optional organization short ID (16 chars)
            agent_short_id: Optional agent short ID (8 chars)

        Returns:
            True if user exists, False otherwise
        """
        username = self.get_username(agent_app_id, org_short_id, agent_short_id)
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

    def create_user(self, agent_app_id: str, org_short_id: Optional[str] = None, agent_short_id: Optional[str] = None) -> Path:
        """Create a new Linux user for an agent.

        Args:
            agent_app_id: Agent identifier
            org_short_id: Optional organization short ID for username generation
            agent_short_id: Optional agent short ID for username generation

        Returns:
            Home directory path

        Raises:
            RuntimeError: If user creation fails
        """
        username = self.get_username(agent_app_id, org_short_id, agent_short_id)
        home_dir = self.get_home_dir(agent_app_id, org_short_id, agent_short_id)

        # Check if user already exists
        if self.user_exists(agent_app_id, org_short_id, agent_short_id):
            logger.info("User already exists", username=username)

            # Verify and repair home directory ownership if needed
            if home_dir.exists():
                try:
                    stat_info = home_dir.stat()

                    # Check if home directory is owned by root (UID 0)
                    if stat_info.st_uid == 0:
                        logger.warning(
                            "Home directory owned by root, repairing ownership",
                            username=username,
                            home_dir=str(home_dir)
                        )

                        # Fix ownership: chown -R username:username home_dir
                        subprocess.run(
                            ["chown", "-R", f"{username}:{username}", str(home_dir)],
                            capture_output=True,
                            text=True,
                            check=True,
                            timeout=30
                        )

                        # Fix permissions: chmod 0700 home_dir
                        subprocess.run(
                            ["chmod", "0700", str(home_dir)],
                            capture_output=True,
                            text=True,
                            check=True,
                            timeout=5
                        )

                        logger.info(
                            "Successfully repaired home directory ownership",
                            username=username,
                            home_dir=str(home_dir)
                        )
                    else:
                        logger.debug(
                            "Home directory ownership correct",
                            username=username,
                            uid=stat_info.st_uid
                        )

                except subprocess.CalledProcessError as e:
                    # Non-blocking: Log error but continue
                    logger.error(
                        "Failed to repair home directory ownership",
                        username=username,
                        home_dir=str(home_dir),
                        error=e.stderr
                    )
                except Exception as e:
                    # Non-blocking: Log error but continue
                    logger.error(
                        "Error checking home directory ownership",
                        username=username,
                        home_dir=str(home_dir),
                        error=str(e)
                    )

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

    def delete_user(self, agent_app_id: str, org_short_id: Optional[str] = None, agent_short_id: Optional[str] = None, remove_home: bool = True) -> None:
        """Delete a Linux user.

        Args:
            agent_app_id: Agent identifier
            org_short_id: Optional organization short ID (16 chars)
            agent_short_id: Optional agent short ID (8 chars)
            remove_home: Whether to remove home directory (default: True)

        Raises:
            RuntimeError: If user deletion fails
        """
        username = self.get_username(agent_app_id, org_short_id, agent_short_id)

        # Check if user exists
        if not self.user_exists(agent_app_id, org_short_id, agent_short_id):
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

    def ensure_directories(self, agent_app_id: str, org_short_id: Optional[str] = None, agent_short_id: Optional[str] = None) -> dict[str, Path]:
        """Ensure required directories exist for an agent.

        Creates directories for:
        - Virtual environments
        - Package cache
        - Logs

        Args:
            agent_app_id: Agent identifier
            org_short_id: Optional organization short ID (16 chars)
            agent_short_id: Optional agent short ID (8 chars)

        Returns:
            Dictionary mapping directory names to paths
        """
        home_dir = self.get_home_dir(agent_app_id, org_short_id, agent_short_id)
        username = self.get_username(agent_app_id, org_short_id, agent_short_id)

        directories = {
            "venv": home_dir / "venv",
            "packages": home_dir / "packages",
            "logs": home_dir / "logs",
            "cache": home_dir / ".cache",
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

    def clean_agent_files(self, agent_app_id: str, org_short_id: Optional[str] = None, agent_short_id: Optional[str] = None) -> None:
        """Clean agent-specific files without removing the Linux user.

        This is called during agent deletion to clean up temporary files and logs,
        while preserving the user account and reusable resources (venv, packages)
        for fast redeployment.

        Cleans:
        - Log files (can be regenerated)
        - Temporary files and runtime state

        Preserves:
        - Linux user account
        - Home directory structure
        - Virtual environment (for fast redeployment)
        - Package cache (for fast redeployment)

        Args:
            agent_app_id: Agent identifier
            org_short_id: Optional organization short ID (16 chars)
            agent_short_id: Optional agent short ID (8 chars)
        """
        home_dir = self.get_home_dir(agent_app_id, org_short_id, agent_short_id)
        username = self.get_username(agent_app_id, org_short_id, agent_short_id)

        logger.info("Cleaning agent files", agent_app_id=agent_app_id, username=username, home_dir=str(home_dir))

        # Clean log files
        logs_dir = home_dir / "logs"
        if logs_dir.exists():
            try:
                # Remove all files in logs directory but keep the directory
                for log_file in logs_dir.glob("*"):
                    if log_file.is_file():
                        log_file.unlink()
                        logger.debug("Removed log file", file=str(log_file))
                logger.info("Cleaned logs directory", path=str(logs_dir))
            except Exception as e:
                logger.warning("Failed to clean logs directory", path=str(logs_dir), error=str(e))

        # Clean any temporary runtime files (if they exist)
        # Note: We preserve venv/ and packages/ for fast redeployment

        logger.info("Agent files cleaned successfully", agent_app_id=agent_app_id, username=username)
