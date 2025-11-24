"""Process management for agent lifecycle."""

import asyncio
import os
import shutil
import subprocess
import signal
import sys
import time
import threading
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
        self._log_threads: Dict[str, list] = {}  # agent_app_id -> list of threads
        self._stderr_files: Dict[str, any] = {}  # agent_app_id -> stderr file handle
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
        venv_path: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> int:
        """Spawn an agent process as a specific Linux user.

        Args:
            agent_app_id: Agent identifier
            linux_user: Linux username to run as
            package_path: Path to extracted agent package directory
            ports: Ports allocation for the agent
            venv_path: Optional path to virtual environment (if None, uses system Python)
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

        # Find pixell_runtime module path for PYTHONPATH
        # This is needed because pixell_runtime is not installed in agent venvs
        # Similar to subprocess_runner.py: repo_src_path = Path(__file__).resolve().parents[2]
        pixell_runtime_path = None
        try:
            # Try to find src directory relative to this file
            # process_manager.py is in src/pixell_runtime/supervisor/
            # Go up: supervisor -> pixell_runtime -> src
            current_file = Path(__file__).resolve()
            src_path = current_file.parent.parent.parent
            if (src_path / "pixell_runtime").exists():
                pixell_runtime_path = str(src_path)
                logger.debug(
                    "Found pixell_runtime src path",
                    agent_app_id=agent_app_id,
                    path=pixell_runtime_path,
                )
            else:
                # Fallback: try importing pixell_runtime to find its location
                import pixell_runtime
                runtime_module_path = Path(pixell_runtime.__file__).parent.parent
                if runtime_module_path.exists():
                    pixell_runtime_path = str(runtime_module_path)
                    logger.debug(
                        "Found pixell_runtime from import",
                        agent_app_id=agent_app_id,
                        path=pixell_runtime_path,
                    )
        except Exception as e:
            logger.warning(
                "Could not find pixell_runtime module path",
                agent_app_id=agent_app_id,
                error=str(e),
            )

        # Build PYTHONPATH: package_path, pixell_runtime_path
        # Note: When using venv Python, venv's site-packages is automatically prepended to sys.path
        # We only need to add package_path and pixell_runtime_path, not system site-packages
        pythonpath_parts = []
        
        # Add package path first (highest priority for agent code)
        if str(package_path) not in pythonpath_parts:
            pythonpath_parts.append(str(package_path))
        
        # Add pixell_runtime path (needed for 'python -m pixell_runtime' command)
        if pixell_runtime_path and pixell_runtime_path not in pythonpath_parts:
            pythonpath_parts.append(pixell_runtime_path)
        
        # Only add existing PYTHONPATH if it doesn't contain system site-packages
        # This prevents system packages from overriding venv packages
        existing_pythonpath = os.getenv("PYTHONPATH", "")
        if existing_pythonpath:
            # Filter out system site-packages paths to avoid conflicts
            system_site_packages = [
                "/usr/local/lib/python",
                "/usr/lib/python",
            ]
            filtered_paths = []
            for path in existing_pythonpath.split(":"):
                if path and not any(sys_path in path for sys_path in system_site_packages):
                    if path not in pythonpath_parts:
                        filtered_paths.append(path)
            
            if filtered_paths:
                pythonpath_parts.extend(filtered_paths)
        
        pythonpath = ":".join(pythonpath_parts)

        # Build environment variables
        process_env = {
            "AGENT_APP_ID": agent_app_id,
            "AGENT_PACKAGE_PATH": str(package_path),
            # Note: PACKAGE_URL is not set here because AGENT_PACKAGE_PATH is used instead.
            # PACKAGE_URL is only used in Fargate/ECS mode where packages are downloaded from URLs.
            "REST_PORT": str(ports.rest),
            "A2A_PORT": str(ports.a2a),
            "UI_PORT": str(ports.ui),
            "BASE_PATH": f"/agents/{agent_app_id}",
            "MULTIPLEXED": "true",
            "PYTHONUNBUFFERED": "1",  # Ensure logs are not buffered
            "PYTHONPATH": pythonpath,  # Add PYTHONPATH so pixell_runtime can be imported
        }
        
        # Add virtual environment path if available
        if venv_path:
            process_env["AGENT_VENV_PATH"] = venv_path
        
        # Forward logging-related environment variables from supervisor to agent
        log_dir = os.getenv("LOG_DIR")
        if log_dir:
            process_env["LOG_DIR"] = log_dir
        log_level = os.getenv("LOG_LEVEL")
        if log_level:
            process_env["LOG_LEVEL"] = log_level
        log_format = os.getenv("LOG_FORMAT")
        if log_format:
            process_env["LOG_FORMAT"] = log_format

        # Ensure log directory exists and has proper permissions before spawning
        # This ensures agent process can write logs even if it crashes early
        log_file_path = None
        if log_dir:
            try:
                log_dir_path = Path(log_dir)
                log_dir_path.mkdir(parents=True, exist_ok=True)
                # Set permissions to allow all users to write (0o1777 = sticky bit + rwx for all)
                try:
                    log_dir_path.chmod(0o1777)
                except Exception:
                    # If we can't set 1777, try 777
                    try:
                        log_dir_path.chmod(0o777)
                    except Exception:
                        pass  # Continue even if permission setting fails
                
                # Pre-create log file with proper permissions
                log_file_path = log_dir_path / f"agent_{agent_app_id}.log"
                try:
                    # Touch the file to create it if it doesn't exist
                    log_file_path.touch(exist_ok=True)
                    # Set permissions so agent user can write
                    log_file_path.chmod(0o666)
                except Exception as e:
                    logger.warning(
                        "Could not pre-create log file",
                        agent_app_id=agent_app_id,
                        log_file=str(log_file_path),
                        error=str(e),
                    )
            except Exception as e:
                logger.warning(
                    "Could not ensure log directory permissions",
                    agent_app_id=agent_app_id,
                    log_dir=log_dir,
                    error=str(e),
                )

        # Add custom environment variables
        if env:
            process_env.update(env)

        # Find Python executable
        # Prefer virtual environment Python if available, otherwise use system Python
        python_exec = None
        
        if venv_path:
            # Use virtual environment Python
            venv_python_path = Path(venv_path) / "bin" / "python"
            if venv_python_path.exists():
                python_exec = str(venv_python_path)
                logger.info(
                    "Using virtual environment Python",
                    agent_app_id=agent_app_id,
                    venv_path=venv_path,
                    python_exec=python_exec,
                )
            else:
                logger.warning(
                    "Virtual environment Python not found, falling back to system Python",
                    agent_app_id=agent_app_id,
                    venv_path=venv_path,
                    expected_path=str(venv_python_path),
                )
        
        # Fallback to system Python if venv not available or not found
        if not python_exec:
            # Try common system paths (these work even with minimal PATH)
            for python_path in ["/usr/bin/python3", "/usr/bin/python", "/usr/local/bin/python3", "/usr/local/bin/python"]:
                if Path(python_path).exists():
                    python_exec = python_path
                    break
            
            # If not found in system paths, try to find using current PATH
            if not python_exec:
                for python_cmd in ["python3", "python"]:
                    python_path = shutil.which(python_cmd)
                    if python_path and Path(python_path).exists():
                        python_exec = python_path
                        break
            
            # Final fallback
            if not python_exec:
                python_exec = "python3"
                logger.warning(
                    "Could not find Python executable in system paths, using 'python3' as fallback",
                    agent_app_id=agent_app_id,
                    note="This may fail if python3 is not in minimal PATH after 'su -'",
                )
            else:
                logger.debug(
                    "Using system Python executable",
                    agent_app_id=agent_app_id,
                    python_exec=python_exec,
                )

        # Convert env dict to string for shell
        env_string = " ".join([f"{k}={v}" for k, v in process_env.items()])

        # Command to run as different user
        # su - <user> -s /bin/bash -c "export ENV_VARS && <python_exec> -m pixell_runtime"
        # Note: Using full path or python3 ensures it's found even with minimal PATH
        cmd = [
            "su",
            "-",
            linux_user,
            "-s",
            "/bin/bash",
            "-c",
            f"{env_string} {python_exec} -m pixell_runtime",
        ]

        try:
            # Determine stdout/stderr redirection
            # Redirect both stdout and stderr to log file to capture all output
            # This ensures we get logs even if process crashes before logging initializes
            stdout_target = subprocess.PIPE
            stderr_target = subprocess.PIPE
            stderr_file = None
            
            if log_file_path:
                try:
                    # Open log file in append mode with line buffering
                    stderr_file = open(log_file_path, "a", buffering=1)
                    stderr_target = stderr_file
                    logger.debug(
                        "Redirecting stderr to log file",
                        agent_app_id=agent_app_id,
                        log_file=str(log_file_path),
                    )
                except Exception as e:
                    logger.warning(
                        "Could not open log file for stderr redirection, using PIPE",
                        agent_app_id=agent_app_id,
                        log_file=str(log_file_path),
                        error=str(e),
                    )
                    stderr_target = subprocess.PIPE
            
            # Spawn process
            process = subprocess.Popen(
                cmd,
                stdout=stdout_target,
                stderr=stderr_target,
                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
            )
            
            # Store process reference
            self.processes[agent_app_id] = process
            
            # If we're using PIPE for stdout, start background thread to forward to log file
            # stderr is already redirected to log file if stderr_file is set
            if log_file_path and stdout_target == subprocess.PIPE:
                self._start_log_forwarding(agent_app_id, process, log_file_path, stdout_target, stderr_target)
            
            # Store stderr_file reference so it can be closed when process stops
            if stderr_file:
                self._stderr_files[agent_app_id] = stderr_file

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

            # Clean up log forwarding threads
            if agent_app_id in self._log_threads:
                del self._log_threads[agent_app_id]
            
            # Close stderr file if it was opened
            if agent_app_id in self._stderr_files:
                try:
                    self._stderr_files[agent_app_id].close()
                except Exception:
                    pass
                del self._stderr_files[agent_app_id]
            
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

    def _start_log_forwarding(
        self,
        agent_app_id: str,
        process: subprocess.Popen,
        log_file_path: Path,
        stdout_target,
        stderr_target,
    ) -> None:
        """Start background threads to forward process stdout to log file.
        
        Note: stderr is already redirected to log file if stderr_target is a file object.
        
        Args:
            agent_app_id: Agent identifier
            process: Subprocess process object
            log_file_path: Path to log file
            stdout_target: stdout target (PIPE or file)
            stderr_target: stderr target (PIPE or file, ignored if file object)
        """
        threads = []
        
        def forward_stream(stream, stream_name: str):
            """Forward stream to log file."""
            try:
                with open(log_file_path, "a", buffering=1) as log_file:
                    for line in iter(stream.readline, ""):
                        if not line:
                            break
                        # Write with prefix to distinguish from structured logs
                        log_file.write(f"[{stream_name}] {line}")
                        log_file.flush()
            except Exception as e:
                logger.warning(
                    "Error forwarding stream to log file",
                    agent_app_id=agent_app_id,
                    stream=stream_name,
                    error=str(e),
                )
            finally:
                stream.close()
        
        # Start thread for stdout if using PIPE
        # stderr is already redirected to log file if stderr_target is a file object
        if stdout_target == subprocess.PIPE and process.stdout:
            stdout_thread = threading.Thread(
                target=forward_stream,
                args=(process.stdout, "stdout"),
                daemon=True,
            )
            stdout_thread.start()
            threads.append(stdout_thread)
        
        # Start thread for stderr only if using PIPE (not if already redirected to file)
        if stderr_target == subprocess.PIPE and process.stderr:
            stderr_thread = threading.Thread(
                target=forward_stream,
                args=(process.stderr, "stderr"),
                daemon=True,
            )
            stderr_thread.start()
            threads.append(stderr_thread)
        
        if threads:
            self._log_threads[agent_app_id] = threads
            logger.debug(
                "Started log forwarding threads",
                agent_app_id=agent_app_id,
                log_file=str(log_file_path),
                threads=len(threads),
            )

    def cleanup(self):
        """Clean up process manager resources."""
        logger.info("Cleaning up ProcessManager")
        self.stop_all(force=True)
        # Clean up log threads
        self._log_threads.clear()
        # Close any remaining stderr files
        for agent_app_id, stderr_file in list(self._stderr_files.items()):
            try:
                stderr_file.close()
            except Exception:
                pass
        self._stderr_files.clear()
