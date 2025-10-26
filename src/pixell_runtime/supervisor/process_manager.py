"""Process management for agent lifecycle."""

import asyncio
import os
import subprocess
import signal
import time
from pathlib import Path
from typing import Optional, Dict, Any
import structlog

from pixell_runtime.supervisor.models import AgentProcess, AgentStatus, Ports

logger = structlog.get_logger()


class ProcessManager:
    """Manages agent process lifecycle.

    Responsibilities:
    - Spawn agents as specific Linux users using `su`
    - Monitor process health
    - Stop processes gracefully
    - Track process PIDs and status
    """

    def __init__(self, graceful_shutdown_timeout_sec: int = 30):
        """Initialize process manager.

        Args:
            graceful_shutdown_timeout_sec: Timeout for graceful shutdown (default: 30)
        """
        self.graceful_shutdown_timeout_sec = graceful_shutdown_timeout_sec
        self.processes: Dict[str, subprocess.Popen] = {}  # agent_app_id -> Popen
        self.log_files: Dict[str, 'IO[str]'] = {}  # agent_app_id -> log file handle
        logger.info(
            "ProcessManager initialized",
            graceful_shutdown_timeout_sec=graceful_shutdown_timeout_sec,
        )

    def spawn_agent(
        self,
        agent_app_id: str,
        linux_user: str,
        package_path: Path,
        ports: Ports,
        env: Optional[Dict[str, str]] = None,
        package_env: Optional[Dict[str, str]] = None,
    ) -> int:
        """Spawn an agent process as a specific Linux user.

        Args:
            agent_app_id: Agent identifier
            linux_user: Linux username to run as
            package_path: Path to agent package (APKG)
            ports: Ports allocation for the agent
            env: Additional environment variables from DeployRequest (highest priority)
            package_env: Environment variables from agent.yaml + deploy.json (medium priority)

        Returns:
            Process ID (PID)

        Raises:
            RuntimeError: If process spawn fails
        """
        logger.info(
            "Spawning agent process",
            agent_app_id=agent_app_id,
            user=linux_user,
            package=str(package_path),
            ports={"rest": ports.rest, "a2a": ports.a2a, "ui": ports.ui},
        )

        # Determine home directory for agent user
        # Format: /home/agent_xxx where xxx matches linux_user suffix
        home_dir = f"/home/{linux_user}"

        # Build environment variables - start from supervisor's environment
        process_env = {
            **os.environ,  # Inherit supervisor's PATH and system environment
            # Agent-specific variables
            "AGENT_APP_ID": agent_app_id,
            "AGENT_PACKAGE_PATH": str(package_path),
            # NOTE: PACKAGE_URL not set - runtime will use AGENT_PACKAGE_PATH instead
            # Runtime validation rejects file:// URLs, and AGENT_PACKAGE_PATH is preferred
            "REST_PORT": str(ports.rest),
            "A2A_PORT": str(ports.a2a),
            "UI_PORT": str(ports.ui),
            "BASE_PATH": f"/agents/{agent_app_id}",
            "MULTIPLEXED": "true",
            "PYTHONUNBUFFERED": "1",  # Ensure logs are not buffered
            "HOME": home_dir,  # Set HOME for agent user (UV/pip need this for cache)
        }

        # Add environment variables from agent.yaml + deploy.json (medium priority)
        if package_env:
            process_env.update(package_env)
            logger.info(
                "Added package environment variables",
                agent_app_id=agent_app_id,
                package_env_count=len(package_env)
            )

        # Add custom environment variables from DeployRequest (highest priority)
        if env:
            process_env.update(env)
            logger.info(
                "Added deployment environment variables",
                agent_app_id=agent_app_id,
                deploy_env_count=len(env)
            )

        # Fix ownership of extracted package directory if it exists
        # This prevents permission errors when packages were extracted by a different user
        self._ensure_package_ownership(agent_app_id, linux_user, package_path)

        # Command to run - direct Python invocation (no shell, no su)
        cmd = [
            "/usr/bin/python3.11",
            "-m",
            "pixell_runtime",
        ]

        try:
            # Create log directory and file for agent output
            log_dir = Path("/var/lib/pixell/logs")
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / f"agent_{agent_app_id}.log"

            # Open log file with line buffering for real-time output
            log_handle = open(log_file, "w", buffering=1)
            self.log_files[agent_app_id] = log_handle

            logger.info(
                "Created log file for agent",
                agent_app_id=agent_app_id,
                log_file=str(log_file),
            )

            # Spawn process as target user using native Python API (Python 3.9+)
            # This is the production-standard way to spawn processes as different users.
            # Much more reliable than using 'su' - no shell involved, env vars work correctly.
            process = subprocess.Popen(
                cmd,
                user=linux_user,  # Python 3.9+ native user switching
                stdout=log_handle,
                stderr=subprocess.STDOUT,  # Merge stderr into stdout for unified log
                env=process_env,
                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
            )

            # Store process reference
            self.processes[agent_app_id] = process

            logger.info(
                "Agent process spawned",
                agent_app_id=agent_app_id,
                pid=process.pid,
                user=linux_user,
            )

            return process.pid

        except Exception as e:
            logger.error(
                "Failed to spawn agent process",
                agent_app_id=agent_app_id,
                error=str(e),
                exc_info=True,
            )
            raise RuntimeError(f"Failed to spawn agent {agent_app_id}: {e}") from e

    def _ensure_package_ownership(
        self, agent_app_id: str, linux_user: str, package_path: Path
    ) -> None:
        """Ensure extracted package directory is owned by the agent user.

        This fixes permission issues when packages are extracted by different users
        (e.g., supervisor as root, or previously by another agent user).

        Args:
            agent_app_id: Agent identifier
            linux_user: Linux username to set as owner
            package_path: Path to the .apkg file

        Note:
            This is a best-effort operation. Failures are logged but don't prevent
            agent startup, as the agent will attempt extraction anyway.
        """
        import zipfile
        import yaml

        try:
            # Read manifest from .apkg to get package_id
            with zipfile.ZipFile(package_path, 'r') as zf:
                if 'agent.yaml' not in zf.namelist():
                    logger.debug(
                        "Cannot determine package_id - agent.yaml not in .apkg",
                        agent_app_id=agent_app_id
                    )
                    return

                with zf.open('agent.yaml') as f:
                    manifest = yaml.safe_load(f)
                    package_id = f"{manifest['name']}@{manifest['version']}"
                    # Use same shared extraction directory as supervisor
                    packages_extract_dir = Path("/tmp/pixell_packages")
                    extracted_dir = packages_extract_dir / package_id

                    # If directory exists, fix ownership
                    if extracted_dir.exists():
                        logger.info(
                            "Fixing ownership of existing extracted package",
                            agent_app_id=agent_app_id,
                            path=str(extracted_dir),
                            owner=linux_user
                        )

                        result = subprocess.run(
                            ["chown", "-R", f"{linux_user}:{linux_user}", str(extracted_dir)],
                            capture_output=True,
                            text=True,
                            check=False,  # Don't fail if this doesn't work
                            timeout=10
                        )

                        if result.returncode == 0:
                            logger.info(
                                "Successfully fixed package directory ownership",
                                agent_app_id=agent_app_id,
                                path=str(extracted_dir)
                            )
                        else:
                            logger.warning(
                                "Failed to fix package directory ownership",
                                agent_app_id=agent_app_id,
                                path=str(extracted_dir),
                                error=result.stderr,
                                note="Agent will attempt extraction anyway"
                            )

        except zipfile.BadZipFile:
            logger.debug(
                "Package file is not a valid zip",
                agent_app_id=agent_app_id,
                path=str(package_path)
            )
        except Exception as e:
            logger.debug(
                "Could not pre-fix package directory ownership",
                agent_app_id=agent_app_id,
                error=str(e),
                note="Agent will attempt extraction anyway"
            )

    def is_process_zombie(self, pid: Optional[int]) -> bool:
        """Check if a process is a zombie.

        A zombie process is a terminated process that hasn't been reaped by its parent.
        Zombies remain in the process table with state 'Z' until reaped via wait()/waitpid().

        Args:
            pid: Process ID to check

        Returns:
            True if process is a zombie, False otherwise (including if process doesn't exist)

        Notes:
            - Uses psutil for cross-platform compatibility (Linux, macOS)
            - Returns False if process doesn't exist or psutil unavailable
            - Zombies have status psutil.STATUS_ZOMBIE or 'zombie' string
        """
        if pid is None:
            return False

        try:
            import psutil

            process = psutil.Process(pid)
            status = process.status()

            # Cross-platform zombie detection
            # psutil.STATUS_ZOMBIE is a constant on all platforms
            is_zombie = (
                status == psutil.STATUS_ZOMBIE
                or status == "zombie"
                or status == "Z"
            )

            if is_zombie:
                logger.debug(
                    "Detected zombie process",
                    pid=pid,
                    status=status,
                )

            return is_zombie

        except ImportError:
            logger.warning(
                "psutil not available - cannot detect zombie processes",
                pid=pid,
            )
            return False
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # Process doesn't exist or we can't access it
            logger.debug("Process not found or access denied", pid=pid)
            return False
        except Exception as e:
            logger.warning(
                "Error checking if process is zombie",
                pid=pid,
                error=str(e),
            )
            return False

    def get_process_health(self, agent_app_id: str) -> Dict[str, Any]:
        """Get comprehensive process health information.

        This method performs real-time checks to determine if a process is:
        - Alive and running
        - A zombie (crashed but not reaped)
        - Stopped/terminated

        Args:
            agent_app_id: Agent identifier

        Returns:
            Dictionary with:
            - is_alive: bool - Process is running and not a zombie
            - is_zombie: bool - Process is a zombie
            - memory_mb: float - Memory usage in MB (0.0 if zombie/stopped)
            - cpu_percent: float - CPU usage percent (0.0 if zombie/stopped)
            - pid: Optional[int] - Process ID

        Notes:
            - This is the authoritative health check for status endpoints
            - Zombies return is_alive=False, is_zombie=True, metrics=0
            - Gracefully handles psutil unavailable or process not found
        """
        if agent_app_id not in self.processes:
            return {
                "is_alive": False,
                "is_zombie": False,
                "memory_mb": 0.0,
                "cpu_percent": 0.0,
                "pid": None,
            }

        process = self.processes[agent_app_id]
        pid = process.pid

        # Check if process has terminated
        if process.poll() is not None:
            return {
                "is_alive": False,
                "is_zombie": False,
                "memory_mb": 0.0,
                "cpu_percent": 0.0,
                "pid": pid,
            }

        # Check if process is a zombie
        is_zombie = self.is_process_zombie(pid)

        if is_zombie:
            return {
                "is_alive": False,
                "is_zombie": True,
                "memory_mb": 0.0,
                "cpu_percent": 0.0,
                "pid": pid,
            }

        # Process is alive - get metrics
        memory_mb = 0.0
        cpu_percent = 0.0

        try:
            import psutil

            ps_process = psutil.Process(pid)
            memory_mb = ps_process.memory_info().rss / (1024 * 1024)  # bytes to MB
            cpu_percent = ps_process.cpu_percent(interval=0.1)

        except ImportError:
            logger.debug("psutil not available for metrics", agent_app_id=agent_app_id)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            logger.debug(
                "Cannot get process metrics",
                agent_app_id=agent_app_id,
                pid=pid,
            )
        except Exception as e:
            logger.warning(
                "Error getting process metrics",
                agent_app_id=agent_app_id,
                pid=pid,
                error=str(e),
            )

        return {
            "is_alive": True,
            "is_zombie": False,
            "memory_mb": memory_mb,
            "cpu_percent": cpu_percent,
            "pid": pid,
        }

    def is_running(self, agent_app_id: str) -> bool:
        """Check if agent process is running AND not a zombie.

        Args:
            agent_app_id: Agent identifier

        Returns:
            True if process is running and alive (not zombie), False otherwise

        Notes:
            - Zombies are NOT considered running (they're dead but not reaped)
            - This method now uses real-time zombie detection
            - Changed from old behavior which considered zombies as "running"
        """
        if agent_app_id not in self.processes:
            return False

        process = self.processes[agent_app_id]

        # Check if process has terminated
        if process.poll() is not None:
            return False

        # Check if process is a zombie (terminated but not reaped)
        if self.is_process_zombie(process.pid):
            return False

        return True

    def get_pid(self, agent_app_id: str) -> Optional[int]:
        """Get process ID for an agent.

        Args:
            agent_app_id: Agent identifier

        Returns:
            PID if process exists, None otherwise
        """
        if agent_app_id not in self.processes:
            return None

        return self.processes[agent_app_id].pid

    def stop_agent(
        self, agent_app_id: str, force: bool = False, timeout: Optional[int] = None
    ) -> bool:
        """Stop an agent process.

        Args:
            agent_app_id: Agent identifier
            force: If True, send SIGKILL immediately instead of SIGTERM
            timeout: Timeout for graceful shutdown (default: uses class setting)

        Returns:
            True if process was stopped, False if not found

        Raises:
            RuntimeError: If stop fails
        """
        if agent_app_id not in self.processes:
            logger.warning("Agent process not found", agent_app_id=agent_app_id)
            return False

        process = self.processes[agent_app_id]
        pid = process.pid

        # Check if already stopped
        if process.poll() is not None:
            logger.info("Agent process already stopped", agent_app_id=agent_app_id, pid=pid)
            # Close log file if open
            if agent_app_id in self.log_files:
                try:
                    self.log_files[agent_app_id].close()
                except Exception:
                    pass
                del self.log_files[agent_app_id]
            del self.processes[agent_app_id]
            return True

        timeout = timeout or self.graceful_shutdown_timeout_sec

        try:
            if force:
                # Force kill
                logger.info("Force killing agent process", agent_app_id=agent_app_id, pid=pid)
                process.kill()
                process.wait(timeout=5)
            else:
                # Graceful shutdown
                logger.info(
                    "Stopping agent process gracefully",
                    agent_app_id=agent_app_id,
                    pid=pid,
                    timeout=timeout,
                )
                process.terminate()

                # Wait for graceful shutdown
                try:
                    process.wait(timeout=timeout)
                    logger.info("Agent process stopped gracefully", agent_app_id=agent_app_id, pid=pid)
                except subprocess.TimeoutExpired:
                    # Force kill if graceful shutdown times out
                    logger.warning(
                        "Agent process did not stop gracefully, force killing",
                        agent_app_id=agent_app_id,
                        pid=pid,
                    )
                    process.kill()
                    process.wait(timeout=5)

            # Clean up
            # Close log file if open
            if agent_app_id in self.log_files:
                try:
                    self.log_files[agent_app_id].close()
                except Exception:
                    pass
                del self.log_files[agent_app_id]
            del self.processes[agent_app_id]
            return True

        except Exception as e:
            logger.error(
                "Failed to stop agent process",
                agent_app_id=agent_app_id,
                pid=pid,
                error=str(e),
                exc_info=True,
            )
            raise RuntimeError(f"Failed to stop agent {agent_app_id}: {e}") from e

    async def health_check(self, agent_app_id: str, ports: Ports) -> bool:
        """Perform health check on agent.

        Checks if agent's REST API responds to health endpoint.

        Args:
            agent_app_id: Agent identifier
            ports: Ports allocation for the agent

        Returns:
            True if healthy, False otherwise
        """
        # Check if process is running
        if not self.is_running(agent_app_id):
            logger.debug("Agent process not running", agent_app_id=agent_app_id)
            return False

        try:
            # Check REST health endpoint
            import httpx

            async with httpx.AsyncClient(timeout=2.0) as client:
                url = f"http://localhost:{ports.rest}/agents/{agent_app_id}/health"
                response = await client.get(url)

                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == "healthy":
                        logger.debug("Agent health check passed", agent_app_id=agent_app_id)
                        return True

                logger.debug(
                    "Agent health check failed",
                    agent_app_id=agent_app_id,
                    status_code=response.status_code,
                )
                return False

        except Exception as e:
            logger.debug(
                "Agent health check exception",
                agent_app_id=agent_app_id,
                error=str(e),
            )
            return False

    def get_process_status(self, agent_app_id: str) -> AgentStatus:
        """Get current status of agent process.

        Args:
            agent_app_id: Agent identifier

        Returns:
            AgentStatus enum value
        """
        if agent_app_id not in self.processes:
            return AgentStatus.STOPPED

        process = self.processes[agent_app_id]

        # Check if process is running
        if process.poll() is None:
            return AgentStatus.RUNNING
        else:
            # Process has terminated
            return AgentStatus.FAILED

    def stop_all(self, force: bool = False) -> int:
        """Stop all agent processes.

        Args:
            force: If True, force kill all processes

        Returns:
            Number of processes stopped
        """
        agent_ids = list(self.processes.keys())
        count = 0

        logger.info("Stopping all agent processes", count=len(agent_ids), force=force)

        for agent_id in agent_ids:
            try:
                if self.stop_agent(agent_id, force=force):
                    count += 1
            except Exception as e:
                logger.error(
                    "Error stopping agent during stop_all",
                    agent_app_id=agent_id,
                    error=str(e),
                )

        logger.info("Stopped all agent processes", stopped_count=count)
        return count

    def cleanup(self):
        """Clean up process manager resources."""
        logger.info("Cleaning up ProcessManager")
        self.stop_all(force=True)
        # Close any remaining log files
        for agent_id, log_handle in list(self.log_files.items()):
            try:
                log_handle.close()
            except Exception:
                pass
        self.log_files.clear()
