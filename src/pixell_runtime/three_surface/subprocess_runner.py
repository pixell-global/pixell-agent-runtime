"""Subprocess runner for agent runtimes with venv isolation."""

import asyncio
import subprocess
from pathlib import Path
from typing import Optional

import structlog

from pixell_runtime.core.models import AgentPackage

logger = structlog.get_logger()


class SubprocessAgentRunner:
    """Runs agent runtime in subprocess with venv isolation."""

    def __init__(self, package: AgentPackage, rest_port: int, a2a_port: int, ui_port: int, multiplexed: bool = True):
        """Initialize subprocess runner.

        Args:
            package: Agent package to run
            rest_port: REST API port
            a2a_port: A2A gRPC port
            ui_port: UI port
            multiplexed: Whether to multiplex surfaces on single port
        """
        self.package = package
        self.rest_port = rest_port
        self.a2a_port = a2a_port
        self.ui_port = ui_port
        self.multiplexed = multiplexed
        self.process: Optional[subprocess.Popen] = None

    async def start(self):
        """Start agent runtime in subprocess."""
        if not self.package.venv_path:
            raise ValueError("Package does not have venv_path - venv isolation required")

        venv_python = Path(self.package.venv_path) / "bin" / "python"
        if not venv_python.exists():
            raise FileNotFoundError(f"Venv python not found: {venv_python}")

        # Build command to run agent runtime
        # Use environment variable approach - main.py checks AGENT_PACKAGE_PATH
        cmd = [
            str(venv_python),
            "-m", "pixell_runtime",  # Run as module
        ]

        # Set environment variables for ThreeSurfaceRuntime
        env = {
            **subprocess.os.environ,
            "AGENT_PACKAGE_PATH": self.package.path,  # Triggers three-surface mode
            "REST_PORT": str(self.rest_port),
            "A2A_PORT": str(self.a2a_port),
            "UI_PORT": str(self.ui_port),
            "MULTIPLEXED": "true" if self.multiplexed else "false",
        }

        logger.info(
            "Starting agent subprocess",
            package_id=self.package.id,
            venv=Path(self.package.venv_path).name,
            command=" ".join(cmd),
            agent_package_path=self.package.path
        )

        # Start subprocess
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
            env=env
        )

        logger.info(
            "Agent subprocess started",
            package_id=self.package.id,
            pid=self.process.pid
        )

        # Start log forwarding tasks
        asyncio.create_task(self._forward_logs("stdout", self.process.stdout))
        asyncio.create_task(self._forward_logs("stderr", self.process.stderr))

    async def _forward_logs(self, stream_name: str, stream):
        """Forward subprocess logs to PAR logger."""
        try:
            for line in stream:
                line = line.rstrip()
                if line:
                    logger.info(
                        f"Agent {stream_name}",
                        package_id=self.package.id,
                        pid=self.process.pid if self.process else None,
                        log=line
                    )
        except Exception as e:
            logger.error(
                "Error forwarding logs",
                package_id=self.package.id,
                stream=stream_name,
                error=str(e)
            )

    async def stop(self, timeout: int = 30):
        """Stop agent subprocess gracefully.

        Args:
            timeout: Seconds to wait for graceful shutdown before killing
        """
        if not self.process:
            return

        logger.info("Stopping agent subprocess", package_id=self.package.id, pid=self.process.pid)

        try:
            # Send SIGTERM for graceful shutdown
            self.process.terminate()

            # Wait for process to exit
            try:
                self.process.wait(timeout=timeout)
                logger.info("Agent subprocess stopped gracefully", package_id=self.package.id)
            except subprocess.TimeoutExpired:
                logger.warning("Agent subprocess did not stop gracefully, killing", package_id=self.package.id)
                self.process.kill()
                self.process.wait()
                logger.info("Agent subprocess killed", package_id=self.package.id)

        except Exception as e:
            logger.error("Error stopping agent subprocess", package_id=self.package.id, error=str(e))

    async def wait(self):
        """Wait for subprocess to exit."""
        if not self.process:
            return

        # Run wait in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.process.wait)

    @property
    def is_running(self) -> bool:
        """Check if subprocess is still running."""
        if not self.process:
            return False
        return self.process.poll() is None

    @property
    def exit_code(self) -> Optional[int]:
        """Get exit code if process has exited."""
        if not self.process:
            return None
        return self.process.poll()
