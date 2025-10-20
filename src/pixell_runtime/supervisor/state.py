"""Supervisor state management for agent deployments."""

import asyncio
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

        # Track agent processes: agent_app_id -> AgentProcess
        self.agents: Dict[str, AgentProcess] = {}

        # Ensure shared package extraction directory exists with proper permissions
        # This directory is used by all agents (running as different users) to extract packages
        self._initialize_shared_directories()

        logger.info("SupervisorState initialized")

    def _initialize_shared_directories(self) -> None:
        """Initialize shared directories used by all agents.

        Creates /tmp/pixell_packages with world-writable permissions (1777) to allow
        all agent users to extract packages. The sticky bit ensures users can only
        delete their own directories.

        Raises:
            Logs errors but does not fail supervisor startup
        """
        import subprocess

        packages_extract_dir = Path("/tmp/pixell_packages")

        try:
            # Create directory if it doesn't exist
            packages_extract_dir.mkdir(parents=True, exist_ok=True)

            # Set permissions to 1777 (drwxrwxrwt) - world-writable with sticky bit
            # Sticky bit ensures users can only delete their own directories
            subprocess.run(
                ["chmod", "1777", str(packages_extract_dir)],
                capture_output=True,
                text=True,
                check=True,
                timeout=5
            )

            logger.info(
                "Initialized shared package extraction directory",
                path=str(packages_extract_dir),
                permissions="1777 (drwxrwxrwt)",
                note="All agent users can create subdirectories for package extraction"
            )

        except subprocess.CalledProcessError as e:
            logger.error(
                "Failed to set permissions on package extraction directory",
                path=str(packages_extract_dir),
                error=e.stderr,
                note="Agents may fail to extract packages"
            )
        except Exception as e:
            logger.error(
                "Failed to initialize package extraction directory",
                path=str(packages_extract_dir),
                error=str(e),
                note="Agents may fail to extract packages"
            )

    async def deploy(self, request: DeployRequest) -> AgentProcess:
        """Deploy a new agent.

        Steps:
        1. Check if agent already exists
        2. Create Linux user
        3. Allocate ports
        4. Download package
        5. Spawn agent process
        (Health checks handled asynchronously by ALB)

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

        # Check if already deployed (idempotent behavior)
        if agent_app_id in self.agents:
            existing = self.agents[agent_app_id]

            # Case 1: Same deployment_id (idempotent retry)
            if existing.deployment_id == request.deployment_id:
                logger.info(
                    "Agent already deployed with same deployment_id (idempotent)",
                    agent_app_id=agent_app_id,
                    deployment_id=request.deployment_id,
                    status=existing.status.value,
                    ports={"rest": existing.ports.rest, "a2a": existing.ports.a2a, "ui": existing.ports.ui},
                )
                return existing

            # Case 2: Different deployment_id (update/replace)
            if not request.allow_update:
                # Caller doesn't want automatic updates - fail
                raise RuntimeError(
                    f"Agent {agent_app_id} is already deployed with different deployment_id "
                    f"(existing: {existing.deployment_id}, requested: {request.deployment_id}). "
                    f"Set allow_update=true to enable automatic update."
                )

            logger.info(
                "Agent exists with different deployment_id, triggering update",
                agent_app_id=agent_app_id,
                old_deployment_id=existing.deployment_id,
                new_deployment_id=request.deployment_id,
            )
            # Convert deploy request to update request
            update_req = UpdateRequest(
                agent_app_id=agent_app_id,
                deployment_id=request.deployment_id,
                package_url=request.package_url,
                package_sha256=request.package_sha256,
                version=request.version if hasattr(request, 'version') else None,
                max_package_size_mb=request.max_package_size_mb,
                boot_budget_ms=request.boot_budget_ms,
                boot_hard_limit_multiplier=request.boot_hard_limit_multiplier,
                graceful_shutdown_timeout_sec=request.graceful_shutdown_timeout_sec,
                env=request.env,
            )
            return await self.update(update_req)

        try:
            # Step 1: Create Linux user (with short IDs if provided)
            username = self.user_manager.get_username(
                agent_app_id,
                org_short_id=request.org_short_id,
                agent_short_id=request.agent_short_id
            )
            home_dir = self.user_manager.create_user(
                agent_app_id,
                org_short_id=request.org_short_id,
                agent_short_id=request.agent_short_id
            )
            self.user_manager.ensure_directories(
                agent_app_id,
                org_short_id=request.org_short_id,
                agent_short_id=request.agent_short_id
            )

            logger.info("Created Linux user", agent_app_id=agent_app_id, user=username)

            # Step 2: Handle port allocation
            # Use PAC-provided ports if available, otherwise allocate internally
            if request.ports:
                # PAC provided ports - use them directly (DO NOT ALLOCATE)
                ports = request.ports
                logger.info(
                    "Using PAC-provided ports",
                    agent_app_id=agent_app_id,
                    rest=ports.rest,
                    a2a=ports.a2a,
                    ui=ports.ui,
                    source="pac",
                    note="PAC manages port lifecycle"
                )
            else:
                # Backward compatibility: allocate ports internally
                # This path is for old PAC versions or testing without PAC
                ports = self.port_allocator.allocate(agent_app_id)
                logger.warning(
                    "PAC did not provide ports, allocated internally",
                    agent_app_id=agent_app_id,
                    rest=ports.rest,
                    a2a=ports.a2a,
                    ui=ports.ui,
                    source="par_internal",
                    note="Consider upgrading PAC to use centralized allocation"
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
            package_path = self.package_downloader.download(
                request.package_url,
                request.package_sha256,
            )
            agent_process.package_path = str(package_path)

            logger.info("Downloaded package", agent_app_id=agent_app_id, path=str(package_path))

            # Step 4: Spawn agent process
            pid = self.process_manager.spawn_agent(
                agent_app_id=agent_app_id,
                linux_user=username,
                package_path=package_path,
                ports=ports,
                env=request.env,
            )
            agent_process.pid = pid
            agent_process.started_at = datetime.now()
            agent_process.status = AgentStatus.RUNNING

            logger.info(
                "Agent deployment complete - process spawned successfully",
                agent_app_id=agent_app_id,
                pid=pid,
                note="ALB will handle health checks asynchronously"
            )

            return agent_process

        except Exception as e:
            # Clean up on failure
            logger.error(
                "Agent deployment failed",
                agent_app_id=agent_app_id,
                error=str(e),
                exc_info=True,
            )

            # Update status
            if agent_app_id in self.agents:
                self.agents[agent_app_id].status = AgentStatus.FAILED
                self.agents[agent_app_id].error_message = str(e)

            # Try to clean up resources
            try:
                if self.process_manager.is_running(agent_app_id):
                    self.process_manager.stop_agent(agent_app_id, force=True)
            except Exception:
                pass

            raise

    async def update(self, request: UpdateRequest) -> AgentProcess:
        """Update an existing agent (zero-downtime).

        Steps:
        1. Verify agent exists
        2. Download new package
        3. Stop old process
        4. Spawn new process with new package
        (Health checks handled asynchronously by ALB)

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
            package_path = self.package_downloader.download(
                request.package_url,
                request.package_sha256,
                force_refresh=True,  # Force download even if cached
            )

            logger.info("Downloaded new package", agent_app_id=agent_app_id)

            # Stop old process
            if self.process_manager.is_running(agent_app_id):
                self.process_manager.stop_agent(agent_app_id)
                logger.info("Stopped old agent process", agent_app_id=agent_app_id)

            # Update agent info
            agent_process.deployment_id = request.deployment_id
            agent_process.package_url = request.package_url
            agent_process.package_path = str(package_path)
            agent_process.package_sha256 = request.package_sha256

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
                package_path=Path(package_path),
                ports=agent_process.ports,
                env=request.env or {},
            )
            agent_process.pid = pid
            agent_process.started_at = datetime.now()
            agent_process.status = AgentStatus.RUNNING
            agent_process.error_message = None

            logger.info(
                "Agent update complete - new process spawned successfully",
                agent_app_id=agent_app_id,
                pid=pid,
                note="ALB will handle health checks asynchronously"
            )

            return agent_process

        except Exception as e:
            logger.error("Agent update failed", agent_app_id=agent_app_id, error=str(e))
            agent_process.status = AgentStatus.FAILED
            agent_process.error_message = str(e)
            raise

    async def delete(self, request: DeleteRequest) -> bool:
        """Delete an agent deployment.

        Steps:
        1. Stop agent process
        2. Clean agent-specific files (logs, temp files)
        3. Release ports
        4. Delete Linux user (only if cleanup_user=True)
        5. Remove from state

        Args:
            request: Delete request

        Returns:
            True if deleted successfully

        Raises:
            RuntimeError: If delete fails
        """
        agent_app_id = request.agent_app_id

        logger.info("Deleting agent", agent_app_id=agent_app_id, force=request.force, cleanup_user=request.cleanup_user)

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

            # Extract short IDs from linux_user field (format: "agent_ORGID_AGENTID")
            # This allows proper user operations even if we don't have original request
            org_short_id = None
            agent_short_id = None
            if agent_process.linux_user.startswith("agent_") and agent_process.linux_user.count("_") >= 2:
                parts = agent_process.linux_user.split("_", 2)
                if len(parts) == 3:
                    org_short_id = parts[1]
                    agent_short_id = parts[2]

            # Clean agent-specific files (logs, temp files) but preserve user and reusable resources
            self.user_manager.clean_agent_files(
                agent_app_id,
                org_short_id=org_short_id,
                agent_short_id=agent_short_id
            )
            logger.info("Cleaned agent files", agent_app_id=agent_app_id)

            # IMPORTANT: DO NOT release ports - PAC manages port lifecycle
            # Old code removed: self.port_allocator.release(agent_app_id)
            logger.info(
                "Ports NOT released by PAR - PAC manages port lifecycle",
                agent_app_id=agent_app_id,
                ports={"rest": agent_process.ports.rest, "a2a": agent_process.ports.a2a, "ui": agent_process.ports.ui},
                note="PAC will release ports in database"
            )

            # Delete Linux user ONLY if explicitly requested
            if request.cleanup_user:
                self.user_manager.delete_user(
                    agent_app_id,
                    org_short_id=org_short_id,
                    agent_short_id=agent_short_id,
                    remove_home=True
                )
                logger.info("Deleted Linux user", agent_app_id=agent_app_id)
            else:
                logger.info("Preserved Linux user for reuse", agent_app_id=agent_app_id, username=agent_process.linux_user)

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
