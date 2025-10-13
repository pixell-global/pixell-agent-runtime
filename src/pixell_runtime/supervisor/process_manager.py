"""Process management for agent lifecycle."""

import asyncio
import subprocess
import signal
import time
from pathlib import Path
from typing import Optional, Dict
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
    ) -> int:
        """Spawn an agent process as a specific Linux user.

        Args:
            agent_app_id: Agent identifier
            linux_user: Linux username to run as
            package_path: Path to agent package (APKG)
            ports: Ports allocation for the agent
            env: Additional environment variables

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

        # Build environment variables
        process_env = {
            "AGENT_APP_ID": agent_app_id,
            "AGENT_PACKAGE_PATH": str(package_path),
            "PACKAGE_URL": f"file://{package_path}",  # For compatibility
            "REST_PORT": str(ports.rest),
            "A2A_PORT": str(ports.a2a),
            "UI_PORT": str(ports.ui),
            "BASE_PATH": f"/agents/{agent_app_id}",
            "MULTIPLEXED": "true",
            "PYTHONUNBUFFERED": "1",  # Ensure logs are not buffered
        }

        # Add custom environment variables
        if env:
            process_env.update(env)

        # Convert env dict to string for shell
        env_string = " ".join([f"{k}={v}" for k, v in process_env.items()])

        # Command to run as different user
        # su - <user> -s /bin/bash -c "export ENV_VARS && python -m pixell_runtime"
        cmd = [
            "su",
            "-",
            linux_user,
            "-s",
            "/bin/bash",
            "-c",
            f"{env_string} python -m pixell_runtime",
        ]

        try:
            # Spawn process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
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

    def is_running(self, agent_app_id: str) -> bool:
        """Check if agent process is running.

        Args:
            agent_app_id: Agent identifier

        Returns:
            True if process is running, False otherwise
        """
        if agent_app_id not in self.processes:
            return False

        process = self.processes[agent_app_id]
        return process.poll() is None

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
