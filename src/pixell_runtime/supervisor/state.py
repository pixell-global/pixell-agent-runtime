"""Supervisor state management for agent deployments."""

import asyncio
import os
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict
import structlog

from pixell_runtime.supervisor.models import (
    AgentProcess,
    AgentStatus,
    DeployRequest,
    UpdateRequest,
    DeleteRequest,
    Ports,
)
from pixell_runtime.supervisor.user_manager import LinuxUserManager
from pixell_runtime.supervisor.port_allocator import PortAllocator
from pixell_runtime.supervisor.package_downloader import PackageDownloader
from pixell_runtime.supervisor.process_manager import ProcessManager
from pixell_runtime.agents.loader import PackageLoader

logger = structlog.get_logger()


class SupervisorState:
    """Manages supervisor state and orchestrates agent lifecycle.

    Responsibilities:
    - Coordinate between user manager, port allocator, package downloader, and process manager
    - Track agent deployments
    - Handle deploy/update/delete operations
    - Maintain agent state
    """

    def __init__(
        self,
        user_manager: Optional[LinuxUserManager] = None,
        port_allocator: Optional[PortAllocator] = None,
        package_downloader: Optional[PackageDownloader] = None,
        process_manager: Optional[ProcessManager] = None,
    ):
        """Initialize supervisor state.

        Args:
            user_manager: LinuxUserManager instance (default: creates new)
            port_allocator: PortAllocator instance (default: creates new)
            package_downloader: PackageDownloader instance (default: creates new)
            process_manager: ProcessManager instance (default: creates new)
        """
        self.user_manager = user_manager or LinuxUserManager()
        self.port_allocator = port_allocator or PortAllocator()
        self.package_downloader = package_downloader or PackageDownloader()
        self.process_manager = process_manager or ProcessManager()

        # Setup package extract directory
        extract_dir = Path(os.getenv("PACKAGE_EXTRACT_DIR", "/var/lib/pixell/extracted"))
        extract_dir.mkdir(parents=True, exist_ok=True)
        self.extract_dir = extract_dir

        # Track agent processes: agent_app_id -> AgentProcess
        self.agents: Dict[str, AgentProcess] = {}

        logger.info("SupervisorState initialized", extract_dir=str(extract_dir))

    async def deploy(self, request: DeployRequest) -> AgentProcess:
        """Deploy a new agent.

        Steps:
        1. Check if agent already exists
        2. Create Linux user
        3. Allocate ports
        4. Download package
        5. Spawn agent process
        6. Wait for health check

        Args:
            request: Deployment request

        Returns:
            AgentProcess with deployment info

        Raises:
            RuntimeError: If deployment fails
        """
        agent_app_id = request.agent_app_id

        logger.info(
            "Starting agent deployment",
            agent_app_id=agent_app_id,
            deployment_id=request.deployment_id,
        )

        # Check if already deployed
        if agent_app_id in self.agents:
            raise RuntimeError(f"Agent {agent_app_id} is already deployed")

        try:
            # Step 1: Create Linux user
            username = self.user_manager.get_username(agent_app_id)
            home_dir = self.user_manager.create_user(agent_app_id)
            self.user_manager.ensure_directories(agent_app_id)

            logger.info("Created Linux user", agent_app_id=agent_app_id, user=username)

            # Step 2: Allocate ports
            ports = self.port_allocator.allocate(agent_app_id)

            logger.info(
                "Allocated ports",
                agent_app_id=agent_app_id,
                rest=ports.rest,
                a2a=ports.a2a,
                ui=ports.ui,
            )

            # Create agent process record with allocated resources
            now = datetime.now()
            agent_process = AgentProcess(
                agent_app_id=agent_app_id,
                deployment_id=request.deployment_id,
                status=AgentStatus.STARTING,
                ports=ports,
                linux_user=username,
                package_path="",  # Will update after download
                package_url=request.package_url,
                package_sha256=request.package_sha256,
                created_at=now,
                config={
                    "max_package_size_mb": request.max_package_size_mb,
                    "boot_budget_ms": request.boot_budget_ms,
                    "boot_hard_limit_multiplier": request.boot_hard_limit_multiplier,
                    "graceful_shutdown_timeout_sec": request.graceful_shutdown_timeout_sec,
                },
            )

            self.agents[agent_app_id] = agent_process

            # Step 3: Download package
            apkg_path = self.package_downloader.download(
                request.package_url,
                request.package_sha256,
            )
            logger.info("Downloaded package", agent_app_id=agent_app_id, path=str(apkg_path))

            # Step 4: Extract and load package with PackageLoader
            # Extract to user-specific directory
            extract_path = self.extract_dir / agent_app_id
            if extract_path.exists():
                logger.info("Package already extracted, removing old extraction", agent_app_id=agent_app_id)
                import shutil
                shutil.rmtree(extract_path)
            
            # Extract APKG
            extract_path.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(apkg_path, 'r') as zf:
                zf.extractall(extract_path)
            logger.info("Extracted package", agent_app_id=agent_app_id, path=str(extract_path))

            # Change ownership of extracted package to agent user
            subprocess.run(
                ["chown", "-R", f"{username}:{username}", str(extract_path)],
                capture_output=True,
                text=True,
                check=True,
            )

            # Load package and create virtual environment
            # Use user's home directory for venvs
            venvs_dir = home_dir / "venvs"
            venvs_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["chown", "-R", f"{username}:{username}", str(venvs_dir)],
                capture_output=True,
                text=True,
                check=True,
            )

            loader = PackageLoader(
                packages_dir=self.extract_dir,
                venvs_dir=venvs_dir,
            )
            
            package = loader.load_package(extract_path, agent_app_id=agent_app_id)
            logger.info(
                "Package loaded with virtual environment",
                agent_app_id=agent_app_id,
                package_id=package.id,
                venv_path=package.venv_path,
            )

            # Change ownership of venv to agent user
            if package.venv_path:
                venv_path = Path(package.venv_path)
                subprocess.run(
                    ["chown", "-R", f"{username}:{username}", str(venv_path)],
                    capture_output=True,
                    text=True,
                    check=True,
                )

            agent_process.package_path = str(extract_path)
            agent_process.venv_path = package.venv_path

            # Step 5: Spawn agent process
            pid = self.process_manager.spawn_agent(
                agent_app_id=agent_app_id,
                linux_user=username,
                package_path=extract_path,
                venv_path=package.venv_path,
                ports=ports,
                env=request.env,
            )
            agent_process.pid = pid
            agent_process.started_at = datetime.now()

            logger.info("Spawned agent process", agent_app_id=agent_app_id, pid=pid)

            # Step 6: Wait for health check (with timeout)
            health_timeout = request.boot_budget_ms * request.boot_hard_limit_multiplier / 1000
            health_start = datetime.now()

            while (datetime.now() - health_start).total_seconds() < health_timeout:
                is_healthy = await self.process_manager.health_check(agent_app_id, ports)
                if is_healthy:
                    agent_process.status = AgentStatus.RUNNING
                    agent_process.last_health_check = datetime.now()
                    logger.info("Agent is healthy and running", agent_app_id=agent_app_id)
                    return agent_process

                # Check if process crashed
                if not self.process_manager.is_running(agent_app_id):
                    agent_process.status = AgentStatus.FAILED
                    agent_process.error_message = "Process terminated during startup"
                    raise RuntimeError(f"Agent {agent_app_id} process terminated during startup")

                # Wait before next check
                await asyncio.sleep(0.5)

            # Health check timeout
            agent_process.status = AgentStatus.FAILED
            agent_process.error_message = "Health check timeout"
            raise RuntimeError(f"Agent {agent_app_id} failed health check after {health_timeout}s")

        except Exception as e:
            # Clean up on failure
            logger.error(
                "Agent deployment failed",
                agent_app_id=agent_app_id,
                error=str(e),
                exc_info=True,
            )

            # Try to clean up resources
            try:
                if self.process_manager.is_running(agent_app_id):
                    self.process_manager.stop_agent(agent_app_id, force=True)
            except Exception:
                pass

            # Release ports if allocated
            try:
                if agent_app_id in self.agents:
                    # Ports were allocated, release them
                    self.port_allocator.release(agent_app_id)
            except Exception:
                pass

            # Remove from agents dict to allow retry
            # This ensures failed deployments don't block future deployment attempts
            if agent_app_id in self.agents:
                logger.info(
                    "Removing failed agent from state to allow retry",
                    agent_app_id=agent_app_id,
                    status=self.agents[agent_app_id].status.value if self.agents[agent_app_id].status else "unknown",
                )
                del self.agents[agent_app_id]

            raise

    async def update(self, request: UpdateRequest) -> AgentProcess:
        """Update an existing agent (zero-downtime).

        Steps:
        1. Verify agent exists
        2. Download new package
        3. Stop old process
        4. Spawn new process with new package
        5. Wait for health check

        Args:
            request: Update request

        Returns:
            Updated AgentProcess

        Raises:
            RuntimeError: If update fails
        """
        agent_app_id = request.agent_app_id

        logger.info(
            "Starting agent update",
            agent_app_id=agent_app_id,
            deployment_id=request.deployment_id,
        )

        # Verify agent exists
        if agent_app_id not in self.agents:
            raise RuntimeError(f"Agent {agent_app_id} not found")

        agent_process = self.agents[agent_app_id]
        old_status = agent_process.status

        try:
            agent_process.status = AgentStatus.UPDATING

            # Download new package
            apkg_path = self.package_downloader.download(
                request.package_url,
                request.package_sha256,
                force_refresh=True,  # Force download even if cached
            )

            logger.info("Downloaded new package", agent_app_id=agent_app_id)

            # Stop old process
            if self.process_manager.is_running(agent_app_id):
                self.process_manager.stop_agent(agent_app_id)
                logger.info("Stopped old agent process", agent_app_id=agent_app_id)

            # Extract and load new package
            username = agent_process.linux_user
            home_dir = self.user_manager.get_home_dir(agent_app_id)
            extract_path = self.extract_dir / agent_app_id
            
            # Remove old extraction if exists
            if extract_path.exists():
                import shutil
                shutil.rmtree(extract_path)
            
            # Extract APKG
            extract_path.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(apkg_path, 'r') as zf:
                zf.extractall(extract_path)
            logger.info("Extracted new package", agent_app_id=agent_app_id, path=str(extract_path))

            # Change ownership to agent user
            subprocess.run(
                ["chown", "-R", f"{username}:{username}", str(extract_path)],
                capture_output=True,
                text=True,
                check=True,
            )

            # Load package and create/update virtual environment
            venvs_dir = home_dir / "venvs"
            venvs_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["chown", "-R", f"{username}:{username}", str(venvs_dir)],
                capture_output=True,
                text=True,
                check=True,
            )

            # Clean up old virtual environments for this agent_app_id
            # This ensures requirements changes trigger fresh venv creation
            old_venv_path = agent_process.venv_path
            if old_venv_path:
                old_venv = Path(old_venv_path)
                if old_venv.exists() and old_venv.parent == venvs_dir:
                    logger.info(
                        "Removing old virtual environment",
                        agent_app_id=agent_app_id,
                        old_venv=str(old_venv),
                    )
                    try:
                        shutil.rmtree(old_venv)
                    except Exception as e:
                        logger.warning(
                            "Failed to remove old venv, continuing",
                            agent_app_id=agent_app_id,
                            old_venv=str(old_venv),
                            error=str(e),
                        )
            
            # Also clean up any other venvs for this agent_app_id (in case of multiple)
            # Find all venvs starting with agent_app_id
            if venvs_dir.exists():
                for venv_item in venvs_dir.iterdir():
                    if venv_item.is_dir() and venv_item.name.startswith(f"{agent_app_id}_"):
                        if venv_item != old_venv:  # Don't delete twice
                            logger.info(
                                "Removing old virtual environment",
                                agent_app_id=agent_app_id,
                                old_venv=str(venv_item),
                            )
                            try:
                                shutil.rmtree(venv_item)
                            except Exception as e:
                                logger.warning(
                                    "Failed to remove old venv, continuing",
                                    agent_app_id=agent_app_id,
                                    old_venv=str(venv_item),
                                    error=str(e),
                                )

            loader = PackageLoader(
                packages_dir=self.extract_dir,
                venvs_dir=venvs_dir,
            )
            
            package = loader.load_package(extract_path, agent_app_id=agent_app_id)
            logger.info(
                "Package loaded with virtual environment",
                agent_app_id=agent_app_id,
                package_id=package.id,
                venv_path=package.venv_path,
            )

            # Change ownership of venv to agent user
            if package.venv_path:
                venv_path = Path(package.venv_path)
                subprocess.run(
                    ["chown", "-R", f"{username}:{username}", str(venv_path)],
                    capture_output=True,
                    text=True,
                    check=True,
                )

            # Update agent info
            agent_process.deployment_id = request.deployment_id
            agent_process.package_url = request.package_url
            agent_process.package_path = str(extract_path)
            agent_process.package_sha256 = request.package_sha256
            agent_process.venv_path = package.venv_path

            # Apply config updates if provided
            if request.max_package_size_mb is not None:
                agent_process.config["max_package_size_mb"] = request.max_package_size_mb
            if request.boot_budget_ms is not None:
                agent_process.config["boot_budget_ms"] = request.boot_budget_ms
            if request.boot_hard_limit_multiplier is not None:
                agent_process.config["boot_hard_limit_multiplier"] = request.boot_hard_limit_multiplier
            if request.graceful_shutdown_timeout_sec is not None:
                agent_process.config["graceful_shutdown_timeout_sec"] = request.graceful_shutdown_timeout_sec

            # Spawn new process
            pid = self.process_manager.spawn_agent(
                agent_app_id=agent_app_id,
                linux_user=agent_process.linux_user,
                package_path=extract_path,
                venv_path=package.venv_path,
                ports=agent_process.ports,
                env=request.env or {},
            )
            agent_process.pid = pid
            agent_process.started_at = datetime.now()

            logger.info("Spawned new agent process", agent_app_id=agent_app_id, pid=pid)

            # Wait for health check
            boot_budget_ms = agent_process.config.get("boot_budget_ms", 5000)
            boot_multiplier = agent_process.config.get("boot_hard_limit_multiplier", 2.0)
            health_timeout = boot_budget_ms * boot_multiplier / 1000
            health_start = datetime.now()

            while (datetime.now() - health_start).total_seconds() < health_timeout:
                is_healthy = await self.process_manager.health_check(
                    agent_app_id, agent_process.ports
                )
                if is_healthy:
                    agent_process.status = AgentStatus.RUNNING
                    agent_process.last_health_check = datetime.now()
                    agent_process.error_message = None
                    logger.info("Agent update successful", agent_app_id=agent_app_id)
                    return agent_process

                # Check if process crashed
                if not self.process_manager.is_running(agent_app_id):
                    agent_process.status = AgentStatus.FAILED
                    agent_process.error_message = "Process terminated during update"
                    raise RuntimeError(f"Agent {agent_app_id} process terminated during update")

                await asyncio.sleep(0.5)

            # Health check timeout
            agent_process.status = AgentStatus.FAILED
            agent_process.error_message = "Health check timeout after update"
            raise RuntimeError(f"Agent {agent_app_id} failed health check after update")

        except Exception as e:
            logger.error("Agent update failed", agent_app_id=agent_app_id, error=str(e))
            agent_process.status = AgentStatus.FAILED
            agent_process.error_message = str(e)
            raise

    async def delete(self, request: DeleteRequest) -> bool:
        """Delete an agent deployment.

        Steps:
        1. Stop agent process
        2. Release ports
        3. Delete Linux user (if requested)
        4. Remove from state

        Args:
            request: Delete request

        Returns:
            True if deleted successfully

        Raises:
            RuntimeError: If delete fails
        """
        agent_app_id = request.agent_app_id

        logger.info("Deleting agent", agent_app_id=agent_app_id, force=request.force)

        # Verify agent exists
        if agent_app_id not in self.agents:
            logger.warning("Agent not found for deletion", agent_app_id=agent_app_id)
            return False

        agent_process = self.agents[agent_app_id]

        try:
            agent_process.status = AgentStatus.STOPPING

            # Stop process
            if self.process_manager.is_running(agent_app_id):
                self.process_manager.stop_agent(agent_app_id, force=request.force)
                logger.info("Stopped agent process", agent_app_id=agent_app_id)

            agent_process.status = AgentStatus.STOPPED
            agent_process.stopped_at = datetime.now()

            # Release ports
            self.port_allocator.release(agent_app_id)
            logger.info("Released ports", agent_app_id=agent_app_id)

            # Delete Linux user if requested
            if request.cleanup_user:
                self.user_manager.delete_user(agent_app_id, remove_home=True)
                logger.info("Deleted Linux user", agent_app_id=agent_app_id)

            # Remove from state
            del self.agents[agent_app_id]

            logger.info("Agent deleted successfully", agent_app_id=agent_app_id)
            return True

        except Exception as e:
            logger.error("Agent deletion failed", agent_app_id=agent_app_id, error=str(e))
            raise RuntimeError(f"Failed to delete agent {agent_app_id}: {e}") from e

    def get_agent(self, agent_app_id: str) -> Optional[AgentProcess]:
        """Get agent process info.

        Args:
            agent_app_id: Agent identifier

        Returns:
            AgentProcess if found, None otherwise
        """
        return self.agents.get(agent_app_id)

    def list_agents(self) -> list[AgentProcess]:
        """List all agent processes.

        Returns:
            List of AgentProcess objects
        """
        return list(self.agents.values())

    async def cleanup(self):
        """Clean up supervisor resources."""
        logger.info("Cleaning up SupervisorState")
        self.process_manager.cleanup()
