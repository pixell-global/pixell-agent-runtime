"""
DEPRECATED - In-memory deployment manager implementing the push-only model.

WARNING: This file contains CONTROL-PLANE code that should NOT be used in PAR.
PAR is now a pure data-plane runtime that executes a single agent.

This code should be moved to PAC (Pixell Agent Cloud) for deployment management.
It is kept here only for backward compatibility with legacy code.

DO NOT USE DeploymentManager in PAR runtime code.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import structlog

from pixell_runtime.agents.loader import PackageLoader
from pixell_runtime.core.models import AgentPackage
from pixell_runtime.deploy.fetch import fetch_package_to_path
from pixell_runtime.deploy.models import (
    DeploymentRecord,
    DeploymentRequest,
    DeploymentStatus,
)
from pixell_runtime.utils.basepath import get_ports, is_port_free
from pixell_runtime.utils.service_discovery import get_service_discovery_client

logger = structlog.get_logger()


@dataclass
class DeploymentProcess:
    record: DeploymentRecord
    package: Optional[AgentPackage] = None
    runtime: Optional[object] = None  # In-process runtime (legacy)
    runtime_task: Optional[asyncio.Task] = None
    monitor_task: Optional[asyncio.Task] = None
    subprocess_runner: Optional[object] = None  # DEPRECATED - Subprocess runner (not used in container model)


class DeploymentManager:
    """Manages deployments requested via /deploy and CLI."""

    def __init__(self, packages_dir: Path):
        self.packages_dir = packages_dir
        self.loader = PackageLoader(packages_dir)

        self.deployments: Dict[str, DeploymentProcess] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _compute_file_sha256(file_path: Path) -> str:
        """Compute SHA256 hash of a file.

        Args:
            file_path: Path to file to hash

        Returns:
            Lowercase hex string of SHA256 hash
        """
        import hashlib

        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            # Read file in chunks to handle large files efficiently
            for byte_block in iter(lambda: f.read(8192), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    def list(self) -> Dict[str, DeploymentRecord]:
        return {k: v.record for k, v in self.deployments.items()}

    def get(self, deployment_id: str) -> Optional[DeploymentRecord]:
        proc = self.deployments.get(deployment_id)
        return proc.record if proc else None

    async def deploy(self, req: DeploymentRequest) -> DeploymentRecord:
        async with self._lock:
            # Check for existing deployment with same ID
            if req.deploymentId in self.deployments:
                existing = self.deployments[req.deploymentId]
                if existing.record.status in [DeploymentStatus.HEALTHY, DeploymentStatus.STARTING]:
                    logger.info("Replacing existing deployment",
                               deploymentId=req.deploymentId,
                               current_status=existing.record.status)
                    # Shutdown existing deployment outside the lock
                    asyncio.create_task(self._replace_deployment(req, existing))
                    # Return a new pending record immediately
                    record = DeploymentRecord(
                        deploymentId=req.deploymentId,
                        agentAppId=req.agentAppId,
                        orgId=req.orgId,
                        version=req.version,
                        status=DeploymentStatus.PENDING,
                        surfaces=req.surfaces,
                        webhook=req.webhook,
                    )
                    process = DeploymentProcess(record=record)
                    self.deployments[req.deploymentId] = process
                    return record
                else:
                    logger.info("Deployment already exists", deploymentId=req.deploymentId)
                    return existing.record

            record = DeploymentRecord(
                deploymentId=req.deploymentId,
                agentAppId=req.agentAppId,
                orgId=req.orgId,
                version=req.version,
                status=DeploymentStatus.PENDING,
                surfaces=req.surfaces,
                webhook=req.webhook,
            )
            process = DeploymentProcess(record=record)
            self.deployments[req.deploymentId] = process

        # Start async workflow outside lock
        asyncio.create_task(self._execute_deployment(req))
        return record

    async def _replace_deployment(self, req: DeploymentRequest, existing: DeploymentProcess):
        """Replace an existing deployment by shutting it down and starting a new one."""
        try:
            # Delete cached package to force fresh download on replacement
            cache_file = self.packages_dir / f"{req.agentAppId}@{req.version}.apkg"
            if cache_file.exists():
                logger.info("Deleting cached package for replacement",
                           deploymentId=req.deploymentId,
                           cache_file=str(cache_file))
                cache_file.unlink()

            # Shutdown the existing deployment
            logger.info("Shutting down existing deployment for replacement",
                       deploymentId=req.deploymentId)
            await self.shutdown_deployment(req.deploymentId)

            # Wait a moment for cleanup
            await asyncio.sleep(1.0)

            # Start the new deployment
            await self._execute_deployment(req)

        except Exception as exc:
            logger.exception("Failed to replace deployment", deploymentId=req.deploymentId)
            proc = self.deployments.get(req.deploymentId)
            if proc:
                proc.record.update_status(DeploymentStatus.FAILED, {"error": f"Replacement failed: {exc}"})

    async def _check_port_conflicts(self, req: DeploymentRequest) -> tuple[int, int, int]:
        """Check for port conflicts and find available ports."""
        # Try to get ports, preferring fixed ones if available
        try:
            rest_port, a2a_port, ui_port = get_ports(prefer_fixed=True)
            logger.info("Using available ports",
                       deploymentId=req.deploymentId,
                       ports={"rest": rest_port, "a2a": a2a_port, "ui": ui_port})
            return rest_port, a2a_port, ui_port
        except RuntimeError as e:
            logger.error("Failed to find available ports", deploymentId=req.deploymentId, error=str(e))
            raise

    def _find_deployment_using_port(self, port: int) -> Optional[DeploymentProcess]:
        """Find which deployment is using a specific port."""
        for proc in self.deployments.values():
            record = proc.record
            if (record.rest_port == port or
                record.a2a_port == port or
                record.ui_port == port):
                return proc
        return None

    async def _execute_deployment(self, req: DeploymentRequest):
        proc = self.deployments[req.deploymentId]
        rec = proc.record
        try:
            # 1) Download package
            rec.update_status(DeploymentStatus.DOWNLOADING)
            cache_file = self.packages_dir / f"{req.agentAppId}@{req.version}.apkg"
            location = req.package_location

            # Always delete cache when forceRefresh is set (fixes stale cache issue)
            if req.forceRefresh and cache_file.exists():
                logger.info("Force refresh - deleting cached package",
                           deploymentId=req.deploymentId,
                           cache_file=str(cache_file))
                cache_file.unlink()

            # Check cache and decide whether to download
            should_download = req.forceRefresh or not cache_file.exists()

            if cache_file.exists() and not req.forceRefresh:
                # Phase 2: Validate SHA256 if provided
                if req.packageSha256:
                    # Run SHA256 in executor to avoid blocking for large files
                    loop = asyncio.get_event_loop()
                    cached_sha256 = await loop.run_in_executor(None, self._compute_file_sha256, cache_file)
                    if cached_sha256 != req.packageSha256:
                        logger.warning(
                            "Cache SHA256 mismatch - re-downloading",
                            deploymentId=req.deploymentId,
                            cached_sha256=cached_sha256,
                            expected_sha256=req.packageSha256
                        )
                        should_download = True
                    else:
                        logger.info(
                            "Cache validated - using cached package",
                            deploymentId=req.deploymentId,
                            sha256=cached_sha256
                        )
                else:
                    logger.info(
                        "Using cached package (no SHA256 validation)",
                        deploymentId=req.deploymentId,
                        cache_file=str(cache_file)
                    )

            if should_download:
                if req.forceRefresh:
                    logger.info(
                        "Force refresh requested - bypassing cache",
                        deploymentId=req.deploymentId
                    )
                # Run download in executor to avoid blocking event loop
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, fetch_package_to_path, location, cache_file)
                logger.info(
                    "Package downloaded",
                    deploymentId=req.deploymentId,
                    size=cache_file.stat().st_size
                )

                # Phase 2: Verify SHA256 after download
                if req.packageSha256:
                    # Run SHA256 in executor to avoid blocking for large files
                    loop = asyncio.get_event_loop()
                    downloaded_sha256 = await loop.run_in_executor(None, self._compute_file_sha256, cache_file)
                    if downloaded_sha256 != req.packageSha256:
                        rec.update_status(
                            DeploymentStatus.FAILED,
                            {"error": f"SHA256 verification failed. Expected: {req.packageSha256}, Got: {downloaded_sha256}"}
                        )
                        logger.error(
                            "Downloaded package SHA256 verification failed",
                            deploymentId=req.deploymentId,
                            expected=req.packageSha256,
                            actual=downloaded_sha256
                        )
                        return
                    logger.info(
                        "Downloaded package SHA256 verified",
                        deploymentId=req.deploymentId,
                        sha256=downloaded_sha256
                    )

            # 2) Load package with agent_app_id for venv isolation
            # Run in executor to avoid blocking event loop (pip installs are synchronous)
            rec.update_status(DeploymentStatus.LOADING)
            loop = asyncio.get_event_loop()
            package = await loop.run_in_executor(
                None,
                self.loader.load_package,
                cache_file,
                req.agentAppId
            )
            proc.package = package
            rec.package_path = package.path
            rec.venv_path = package.venv_path
            rec.update_status(DeploymentStatus.DEPLOYED)
            logger.info(
                "Package loaded successfully",
                deploymentId=req.deploymentId,
                package_id=f"{package.manifest.name}@{package.manifest.version}"
            )

            # 3) Check for port conflicts and allocate ports
            rest_port, a2a_port, ui_port = await self._check_port_conflicts(req)
            if rec.surfaces and rec.surfaces.ports:
                # Override with custom ports if specified, but warn if they're not free
                custom_rest = rec.surfaces.ports.get("rest", rest_port)
                custom_a2a = rec.surfaces.ports.get("a2a", a2a_port)
                custom_ui = rec.surfaces.ports.get("ui", ui_port)

                if custom_rest != rest_port and not is_port_free(custom_rest):
                    logger.warning("Custom REST port not available, using allocated port",
                                 deploymentId=req.deploymentId,
                                 requested=custom_rest, allocated=rest_port)
                else:
                    rest_port = custom_rest

                if custom_a2a != a2a_port and not is_port_free(custom_a2a):
                    logger.warning("Custom A2A port not available, using allocated port",
                                 deploymentId=req.deploymentId,
                                 requested=custom_a2a, allocated=a2a_port)
                else:
                    a2a_port = custom_a2a

                if custom_ui != ui_port and not is_port_free(custom_ui):
                    logger.warning("Custom UI port not available, using allocated port",
                                 deploymentId=req.deploymentId,
                                 requested=custom_ui, allocated=ui_port)
                else:
                    ui_port = custom_ui

            rec.rest_port, rec.a2a_port, rec.ui_port = rest_port, a2a_port, ui_port

            # Set environment for three-surface runtime
            import os
            multiplex = True
            if rec.surfaces and rec.surfaces.mode == "multiport":
                multiplex = False
            os.environ["BASE_PATH"] = f"/agents/{req.deploymentId}"
            os.environ["REST_PORT"] = str(rest_port)
            os.environ["A2A_PORT"] = str(a2a_port)
            os.environ["UI_PORT"] = str(ui_port)
            os.environ["MULTIPLEXED"] = "true" if multiplex else "false"

            # 4) Start three-surface runtime
            rec.update_status(DeploymentStatus.STARTING)
            # For test stability, run in-process runtime (subprocess runner covered by dedicated tests)
            from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
            runtime = ThreeSurfaceRuntime(package.path, package)
            proc.runtime = runtime
            # Start runtime in background task
            proc.runtime_task = asyncio.create_task(self._run_runtime(runtime, rec))
            logger.info(
                "Runtime task started",
                deploymentId=req.deploymentId,
                ports={"rest": rest_port, "a2a": a2a_port, "ui": ui_port}
            )

            # Give the runtime a moment to start before health checking
            await asyncio.sleep(0.5)

            # 5) Mark healthy after runtime task is launched (network health is validated elsewhere)
            rec.update_status(DeploymentStatus.HEALTHY)
            logger.info("Deployment healthy (runtime task launched)", deploymentId=req.deploymentId)

            # 6) Register in service discovery for A2A (best-effort)
            await self._register_service_discovery(req.deploymentId, a2a_port)

            # Start background monitoring
            proc.monitor_task = asyncio.create_task(
                self._monitor_deployment(req.deploymentId)
            )

        except Exception as exc:
            logger.exception("Deployment failed", deploymentId=req.deploymentId)
            rec.update_status(DeploymentStatus.FAILED, {"error": str(exc)})

    async def _wait_for_health(self, rest_port: int, timeout_seconds: int = 30, base_path: str | None = None) -> bool:
        """Wait for agent to be healthy with exponential backoff."""
        import httpx

        deadline = asyncio.get_event_loop().time() + timeout_seconds
        backoff = 0.1

        async with httpx.AsyncClient() as client:
            while asyncio.get_event_loop().time() < deadline:
                try:
                    # Test top-level health alias
                    resp = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=2.0)
                    if resp.status_code == 200:
                        logger.info("Agent health check passed", port=rest_port, path="/health")
                        return True
                    # If base_path provided, test base-path health
                    if base_path:
                        resp2 = await client.get(f"http://127.0.0.1:{rest_port}{base_path}/health", timeout=2.0)
                        if resp2.status_code == 200:
                            logger.info("Agent health check passed", port=rest_port, path=f"{base_path}/health")
                            return True
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass
                except Exception as e:
                    logger.debug("Health check exception", port=rest_port, error=str(e))

                await asyncio.sleep(min(backoff, 2.0))
                backoff *= 1.5

        logger.warning("Agent health check failed", port=rest_port, timeout=timeout_seconds)
        return False

    async def _run_runtime(self, runtime, record: DeploymentRecord):
        """Run three-surface runtime with proper error handling."""
        try:
            # This call runs indefinitely - it starts all servers and waits
            await runtime.start()
        except asyncio.CancelledError:
            logger.info("Runtime task cancelled", deploymentId=record.deploymentId)
            await runtime.shutdown()
            raise
        except OSError as exc:
            if "address already in use" in str(exc):
                # Extract port number from error message if possible
                port_info = ""
                error_str = str(exc)
                if "8080" in error_str:
                    port_info = " (port 8080 - REST)"
                elif "50051" in error_str:
                    port_info = " (port 50051 - A2A)"
                elif "3000" in error_str:
                    port_info = " (port 3000 - UI)"

                logger.error("Port conflict during runtime start",
                           deploymentId=record.deploymentId,
                           error=str(exc),
                           port_info=port_info)
                record.update_status(DeploymentStatus.FAILED, {
                    "error": f"Port conflict{port_info} - another service is using the required port",
                    "error_type": "port_conflict",
                    "retry_suggested": True,
                    "original_error": str(exc)
                })
            else:
                logger.exception("OS error during runtime start", deploymentId=record.deploymentId)
                record.update_status(DeploymentStatus.FAILED, {
                    "error": f"OS error: {exc}",
                    "error_type": "os_error",
                    "original_error": str(exc)
                })
            await runtime.shutdown()
            raise
        except Exception as exc:
            logger.exception("Runtime execution failed", deploymentId=record.deploymentId)
            record.update_status(DeploymentStatus.FAILED, {
                "error": str(exc),
                "error_type": "runtime_error",
                "original_error": str(exc)
            })
            await runtime.shutdown()
            raise

    async def _monitor_deployment(self, deployment_id: str):
        """Continuously monitor deployment health."""
        proc = self.deployments.get(deployment_id)
        if not proc:
            return

        while proc.record.status == DeploymentStatus.HEALTHY:
            try:
                # Check if runtime task is still running (it should be running indefinitely)
                if proc.runtime_task and proc.runtime_task.done():
                    exception = proc.runtime_task.exception()
                    if exception and not isinstance(exception, asyncio.CancelledError):
                        logger.error(
                            "Runtime task failed",
                            deploymentId=deployment_id,
                            error=str(exception)
                        )
                        proc.record.update_status(
                            DeploymentStatus.FAILED,
                            {"error": "Runtime crashed"}
                        )
                        break

                # Periodic health check
                if not await self._wait_for_health(proc.record.rest_port, timeout_seconds=5):
                    proc.record.update_status(
                        DeploymentStatus.FAILED,
                        {"error": "Health check failed"}
                    )
                    break

                await asyncio.sleep(30)  # Check every 30 seconds

            except Exception:
                logger.exception("Monitoring failed", deploymentId=deployment_id)
                break

    async def _register_service_discovery(self, deployment_id: str, a2a_port: int):
        """Register deployment in AWS Cloud Map for service discovery."""
        sd_client = get_service_discovery_client()
        if not sd_client:
            logger.debug("Service discovery not configured, skipping registration")
            return

        # Get task metadata to find our IP
        import os
        task_ip = self._get_task_ip()
        if not task_ip:
            logger.warning("Could not determine task IP for service discovery", deploymentId=deployment_id)
            return

        # Register instance
        success = sd_client.register_instance(
            instance_id=deployment_id,
            ipv4=task_ip,
            port=a2a_port,
            attributes={
                'deployment_id': deployment_id,
                'runtime_instance': os.getenv('RUNTIME_INSTANCE_ID', 'unknown')
            }
        )

        if success:
            logger.info(
                "Registered deployment in service discovery",
                deploymentId=deployment_id,
                dns=f"{deployment_id}.agents.pixell-runtime.local",
                port=a2a_port
            )

    def _get_task_ip(self) -> Optional[str]:
        """Get the task's private IP address."""
        import os
        import socket

        # Try ECS metadata endpoint first (Fargate)
        try:
            import urllib.request
            import json
            metadata_uri = os.getenv('ECS_CONTAINER_METADATA_URI_V4')
            if metadata_uri:
                with urllib.request.urlopen(f"{metadata_uri}/task", timeout=1) as response:
                    data = json.loads(response.read())
                    # Get first private IP from task network
                    containers = data.get('Containers', [])
                    for container in containers:
                        networks = container.get('Networks', [])
                        for network in networks:
                            ipv4 = network.get('IPv4Addresses', [])
                            if ipv4:
                                return ipv4[0]
        except Exception as e:
            logger.debug("Could not get IP from ECS metadata", error=str(e))

        # Fallback: get hostname's IP
        try:
            hostname = socket.gethostname()
            return socket.gethostbyname(hostname)
        except Exception:
            return None

    async def shutdown_deployment(self, deployment_id: str):
        """Gracefully shutdown a specific deployment."""
        proc = self.deployments.get(deployment_id)
        if not proc:
            return

        proc.record.update_status(DeploymentStatus.STOPPING)

        try:
            # Deregister from service discovery
            sd_client = get_service_discovery_client()
            if sd_client:
                sd_client.deregister_instance(deployment_id)

            # Cancel monitor task
            if proc.monitor_task and not proc.monitor_task.done():
                proc.monitor_task.cancel()
                try:
                    await proc.monitor_task
                except asyncio.CancelledError:
                    pass

            # Stop subprocess runner (DEPRECATED - not used in container model)
            if proc.subprocess_runner:
                # Legacy code path - not used in production
                await proc.subprocess_runner.stop()

            # Cancel runtime task (if using in-process runtime)
            if proc.runtime_task and not proc.runtime_task.done():
                proc.runtime_task.cancel()
                try:
                    await proc.runtime_task
                except asyncio.CancelledError:
                    pass

            # Shutdown runtime (if using in-process runtime)
            if proc.runtime:
                await proc.runtime.shutdown()

            proc.record.update_status(DeploymentStatus.STOPPED)

        except Exception as exc:
            logger.exception("Shutdown failed", deploymentId=deployment_id)
            proc.record.update_status(DeploymentStatus.FAILED, {"shutdown_error": str(exc)})

    async def shutdown_all(self):
        """Gracefully shutdown all running deployments."""
        shutdown_tasks = []
        for deployment_id in list(self.deployments.keys()):
            shutdown_tasks.append(self.shutdown_deployment(deployment_id))

        if shutdown_tasks:
            await asyncio.gather(*shutdown_tasks, return_exceptions=True)


