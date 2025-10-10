"""Three-surface runtime implementation."""

import asyncio
import os
import signal
import sys
import time
from typing import Optional

import grpc.aio
import structlog
import uvicorn
from fastapi import FastAPI

from pixell_runtime.a2a.server import create_grpc_server, start_grpc_server
from pixell_runtime.agents.loader import PackageLoader
from pixell_runtime.core.models import AgentPackage
from pixell_runtime.rest.server import create_rest_app
from pixell_runtime.ui.server import setup_ui_routes, validate_ui_assets
from pixell_runtime.utils.basepath import get_base_path
from pixell_runtime.utils.logging import setup_logging

logger = structlog.get_logger()


def _exit_with_backoff(exit_code: int = 1) -> None:
    """Exit with exponential backoff to avoid hot-restart loops.
    
    Reads BOOT_FAILURE_COUNT from environment to track consecutive failures.
    Implements exponential backoff: sleep for min(60, 2^failure_count) seconds.
    
    Args:
        exit_code: The exit code to use (default 1)
    """
    failure_count = int(os.getenv("BOOT_FAILURE_COUNT", "0"))
    
    # Increment failure count for next restart
    os.environ["BOOT_FAILURE_COUNT"] = str(failure_count + 1)
    
    # Exponential backoff: 2^n seconds, capped at 60
    if failure_count > 0:
        sleep_sec = min(60, 2 ** failure_count)
        logger.warning(
            "Boot failed, backing off before exit to avoid hot-restart loop",
            failure_count=failure_count,
            backoff_seconds=sleep_sec,
            exit_code=exit_code
        )
        time.sleep(sleep_sec)
    
    sys.exit(exit_code)


class ThreeSurfaceRuntime:
    """Three-surface runtime that orchestrates A2A, REST, and UI services."""

    def __init__(self, package_path: Optional[str] = None, package: Optional[AgentPackage] = None):
        """Initialize three-surface runtime.

        Args:
            package_path: Path to the agent package (APKG file or extracted directory).
                         If not provided, will check PACKAGE_URL environment variable.
            package: Pre-loaded agent package (optional, will load from path if not provided)
        """
        self.package_path = package_path
        self.package: Optional[AgentPackage] = package
        self._downloaded_package_path: Optional[str] = None  # Track if we downloaded

        # Server instances
        self.rest_app: Optional[FastAPI] = None
        self.grpc_server: Optional[grpc.aio.Server] = None
        self._rest_server: Optional[uvicorn.Server] = None
        self._ui_server: Optional[uvicorn.Server] = None

        # Validate and load configuration
        from pixell_runtime.core.runtime_config import RuntimeConfig
        config = RuntimeConfig()
        
        # Store validated configuration
        self.agent_app_id = config.agent_app_id
        self.deployment_id = config.deployment_id
        self.rest_port = config.rest_port
        self.a2a_port = config.a2a_port
        self.ui_port = config.ui_port
        self.multiplexed = config.multiplexed
        self.base_path = config.base_path
        # Boot budget enforcement settings
        self.boot_budget_ms = getattr(config, "boot_budget_ms", float(os.getenv("BOOT_BUDGET_MS", "5000")))
        self.boot_hard_limit_multiplier = getattr(config, "boot_hard_limit_multiplier", float(os.getenv("BOOT_HARD_LIMIT_MULTIPLIER", "0")))
        
        # Capture if BASE_PATH was explicitly set
        import os as _os
        self._respect_env_base_path = "BASE_PATH" in _os.environ

        # Setup logging and bind correlation context
        setup_logging("INFO", "json")
        from pixell_runtime.utils.logging import bind_runtime_context
        bind_runtime_context(
            agent_app_id=self.agent_app_id,
            deployment_id=self.deployment_id,
        )

        # Setup signal handlers
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        import threading
        def signal_handler(signum, frame):
            logger.info("Received shutdown signal", signal=signum)
            try:
                asyncio.create_task(self.shutdown())
            except RuntimeError:
                # If no running loop, perform sync fallback
                try:
                    loop = asyncio.get_event_loop()
                    loop.create_task(self.shutdown())
                except Exception:
                    pass
        # Only register OS signal handlers in the main thread
        try:
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTERM, signal_handler)
                signal.signal(signal.SIGINT, signal_handler)
        except Exception:
            # Ignore in environments where signal registration is not allowed
            pass

    def _validate_package_url(self, url: str) -> None:
        """Validate PACKAGE_URL for security.
        
        Args:
            url: The package URL to validate
            
        Raises:
            ValueError: If URL is invalid or insecure
        """
        if not url:
            raise ValueError("PACKAGE_URL cannot be empty")
        
        # Strip whitespace for robustness
        url = url.strip()
        
        # Normalize to lowercase for protocol checking
        url_lower = url.lower()
        
        # Block file:// URLs to prevent local file access
        if url_lower.startswith("file://"):
            raise ValueError("file:// URLs are not allowed for security reasons")
        
        # Validate S3 URLs
        if url_lower.startswith("s3://"):
            # Extract bucket name
            s3_bucket = os.getenv("S3_BUCKET", "pixell-agent-packages")
            expected_prefix = f"s3://{s3_bucket}/"
            if not url_lower.startswith(expected_prefix.lower()):
                logger.warning(
                    "S3 URL does not match expected bucket",
                    url=url,
                    expected_bucket=s3_bucket
                )
                # Don't fail, but log warning - bucket might be configurable
            return
        
        # Validate HTTPS URLs
        if url_lower.startswith("https://"):
            # HTTPS URLs are allowed (including S3 signed URLs)
            return
        
        # Block all other protocols
        raise ValueError(f"Only s3:// and https:// URLs are allowed, got: {url[:20]}...")

    async def load_package(self) -> AgentPackage:
        """Load the agent package."""
        if self.package is not None:
            logger.info("Using pre-loaded package",
                       package_id=self.package.id,
                       path=self.package_path)
            return self.package

        from pathlib import Path
        import tempfile
        
        # Determine package source: PACKAGE_URL env var or provided path
        package_url = os.getenv("PACKAGE_URL")
        
        if package_url and not self.package_path:
            # Download package from URL
            logger.info("Downloading package from PACKAGE_URL", url=package_url)
            
            # Validate URL
            self._validate_package_url(package_url)
            
            # Download to temp location
            from pixell_runtime.deploy.fetch import fetch_package_to_path
            from pixell_runtime.deploy.models import PackageLocation, PackageS3Ref
            
            temp_dir = Path(tempfile.mkdtemp(prefix="pixell_apkg_"))
            dest_path = temp_dir / "package.apkg"
            
            try:
                # Get optional SHA256 for validation
                sha256 = os.getenv("PACKAGE_SHA256")
                if sha256:
                    logger.info("SHA256 validation enabled", sha256=sha256[:16] + "...")
                
                # Create PackageLocation based on URL type
                if package_url.lower().startswith("s3://"):
                    # Parse S3 URL: s3://bucket/key
                    s3_parts = package_url[5:].split("/", 1)
                    if len(s3_parts) != 2:
                        raise ValueError(f"Invalid S3 URL format: {package_url}")
                    bucket, key = s3_parts
                    location = PackageLocation(s3=PackageS3Ref(bucket=bucket, key=key))
                else:
                    # HTTPS URL
                    location = PackageLocation(packageUrl=package_url)
                
                # Fetch package with retries
                fetch_package_to_path(
                    location,
                    dest_path,
                    sha256=sha256,
                    max_size_bytes=int(os.getenv("MAX_PACKAGE_SIZE_MB", "100")) * 1024 * 1024
                )
                
                self.package_path = str(dest_path)
                self._downloaded_package_path = str(dest_path)
                logger.info("Package downloaded successfully", path=self.package_path)
                
            except Exception as e:
                logger.error("Failed to download package", error=str(e), url=package_url)
                _exit_with_backoff(1)
        
        elif not self.package_path:
            logger.error("No package source provided: PACKAGE_URL env var or package_path required")
            _exit_with_backoff(1)

        logger.info("Loading agent package", path=self.package_path)

        # Create package loader
        packages_dir = Path("/tmp/pixell_packages")
        loader = PackageLoader(packages_dir)

        # Load package
        self.package = loader.load_package(Path(self.package_path))

        logger.info("Package loaded successfully",
                   package_id=self.package.id,
                   surfaces={
                       "a2a": self.package.manifest.a2a is not None,
                       "rest": self.package.manifest.rest is not None,
                       "ui": self.package.manifest.ui is not None
                   })

        return self.package

    async def start_rest_server(self):
        """Start the REST server."""
        if not self.package:
            raise RuntimeError("Package must be loaded before starting servers")

        logger.info("Starting REST server", port=self.rest_port)

        # Refresh base path from env at start time if explicitly provided
        import os as _os

        from pixell_runtime.utils.basepath import get_base_path as _get_base_path
        if "BASE_PATH" in _os.environ:
            self.base_path = _get_base_path()
        else:
            self.base_path = "/"
        # Create REST app with base path
        self.rest_app = create_rest_app(self.package, base_path=self.base_path)

        # Setup UI routes if multiplexed
        if self.multiplexed and self.package.manifest.ui:
            setup_ui_routes(self.rest_app, self.package)

        # Start server
        config = uvicorn.Config(
            self.rest_app,
            host="0.0.0.0",
            port=self.rest_port,
            log_config=None,  # We handle logging ourselves
            access_log=False,
        )

        server = uvicorn.Server(config)
        self._rest_server = server
        await server.serve()

    async def start_grpc_server(self):
        """Start the A2A gRPC server."""
        if not self.package:
            raise RuntimeError("Package must be loaded before starting servers")

        if not self.package.manifest.a2a:
            logger.info("No A2A configuration found, skipping gRPC server")
            return

        logger.info("Starting A2A gRPC server", port=self.a2a_port)
        if hasattr(self, "_boot_metrics"):
            self._boot_metrics.start_phase("a2a")

        # Create gRPC server
        self.grpc_server = create_grpc_server(self.package, self.a2a_port)

        # Start server
        await start_grpc_server(self.grpc_server)
        # Probe gRPC health before marking ready
        try:
            from pixell_runtime.proto import agent_pb2, agent_pb2_grpc
            deadline = asyncio.get_event_loop().time() + 2.0
            ok = False
            while asyncio.get_event_loop().time() < deadline:
                try:
                    async with grpc.aio.insecure_channel(f"localhost:{self.a2a_port}") as channel:
                        stub = agent_pb2_grpc.AgentServiceStub(channel)
                        await stub.Health(agent_pb2.Empty(), timeout=0.3)
                        ok = True
                        break
                except Exception:
                    await asyncio.sleep(0.1)
            
            if ok and self.rest_app is not None:
                try:
                    # Optional test-only delay to make boot time deterministic in tests
                    test_delay_ms = int(os.getenv("BOOT_TEST_DELAY_MS", "0"))
                    if test_delay_ms > 0:
                        await asyncio.sleep(test_delay_ms / 1000.0)

                    # Finalize metrics and stash to app.state for /meta
                    if hasattr(self, "_boot_metrics"):
                        self._boot_metrics.end_phase("a2a")
                        self._boot_metrics.finish()
                        stats = self._boot_metrics.to_dict()
                        boot_ms = float(stats.get("total_ms") or 0.0)
                    else:
                        stats = {}
                        boot_ms = 0.0
                    
                    # Store boot stats in REST app state for /meta endpoint
                    if self.rest_app is not None:
                        try:
                            self.rest_app.state.boot_stats = stats
                        except Exception:
                            pass

                    logger.info("Runtime ready", rest_port=self.rest_port, a2a_port=self.a2a_port, boot_ms=round(boot_ms, 3))
                    budget_ms = float(self.boot_budget_ms)
                    if boot_ms > budget_ms:
                        logger.warning("Boot time exceeded budget", boot_ms=boot_ms, budget_ms=budget_ms)
                        # Enforce hard limit if configured
                        hard_multiplier = float(self.boot_hard_limit_multiplier or 0.0)
                        if hard_multiplier > 0:
                            hard_limit_ms = budget_ms * hard_multiplier
                            if boot_ms > hard_limit_ms:
                                logger.error("Boot time exceeded hard limit", boot_ms=boot_ms, hard_limit_ms=hard_limit_ms)
                                # Fail fast - exit process with backoff
                                _exit_with_backoff(1)
                    
                    if self.rest_app is not None:
                        self.rest_app.state.runtime_ready = True
                except Exception:
                    pass
        except Exception:
            # Do not block startup on probe errors; readiness will stay false
            pass

    async def start_ui_server(self):
        """Start standalone UI server (if not multiplexed)."""
        if self.multiplexed:
            logger.info("UI is multiplexed with REST server, skipping standalone UI server")
            return

        if not self.package or not self.package.manifest.ui:
            logger.info("No UI configuration found, skipping UI server")
            return

        logger.info("Starting standalone UI server", port=self.ui_port)

        # Validate UI assets
        if not validate_ui_assets(self.package):
            logger.error("UI assets validation failed")
            return

        # Create UI app
        from pixell_runtime.ui.server import create_ui_app
        ui_app = create_ui_app(self.package, self.ui_port)

        # Start server
        config = uvicorn.Config(
            ui_app,
            host="0.0.0.0",
            port=self.ui_port,
            log_config=None,
            access_log=False,
        )

        server = uvicorn.Server(config)
        self._ui_server = server
        await server.serve()

    async def start(self):
        """Start all configured services."""
        from pixell_runtime.utils.boot_metrics import BootMetrics
        self._boot_metrics = BootMetrics()
        logger.info("Starting three-surface runtime",
                   multiplexed=self.multiplexed,
                   ports={
                       "rest": self.rest_port,
                       "a2a": self.a2a_port,
                       "ui": self.ui_port
                   })

        # Load package
        try:
            if hasattr(self, "_boot_metrics"):
                self._boot_metrics.start_phase("load")
            await self.load_package()
            if hasattr(self, "_boot_metrics"):
                self._boot_metrics.end_phase("load")
        except Exception as e:
            logger.error("Runtime failed to load package", error=str(e))
            # keep readiness false and shutdown to signal failure
            await self.shutdown()
            _exit_with_backoff(1)

        # Start services concurrently but do not block on REST server
        rest_task = asyncio.create_task(self.start_rest_server())
        grpc_task = None
        ui_task = None
        
        # Wait a moment for REST app to be created before starting gRPC
        # (gRPC server will store boot_stats in rest_app.state)
        await asyncio.sleep(0.1)
        
        if self.package.manifest.a2a:
            grpc_task = asyncio.create_task(self.start_grpc_server())
        if not self.multiplexed and self.package.manifest.ui:
            ui_task = asyncio.create_task(self.start_ui_server())

        # Allow servers to start and then keep the loop alive until cancelled
        try:
            # Wait until REST is accepting connections
            await asyncio.sleep(0.2)
            # Do not flip readiness here. It will be flipped by start_grpc_server()
            # after gRPC successfully starts (or by REST-only mode elsewhere).
            while True:
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error in runtime", error=str(e))
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Gracefully shutdown all services.
        
        Implements graceful shutdown pattern:
        1. Mark runtime as not ready (health check returns 503)
        2. Stop accepting new requests
        3. Wait for in-flight requests to complete (with timeout)
        4. Close gRPC streams gracefully
        5. Exit
        """
        logger.info("Shutting down three-surface runtime")
        
        # Step 1: Mark runtime as not ready
        if self.rest_app is not None:
            try:
                self.rest_app.state.runtime_ready = False
                logger.info("Marked runtime as not ready, health check will return 503")
            except Exception:
                pass
        
        # Step 2 & 3: Wait for in-flight requests (graceful period)
        graceful_timeout_sec = float(os.getenv("GRACEFUL_SHUTDOWN_TIMEOUT_SEC", "30"))
        logger.info("Waiting for in-flight requests to complete", timeout_sec=graceful_timeout_sec)
        
        # Give servers a moment to stop accepting new requests
        await asyncio.sleep(1)
        
        # Step 4: Shutdown gRPC server with grace period
        if self.grpc_server:
            logger.info("Shutting down gRPC server gracefully", grace_sec=graceful_timeout_sec)
            try:
                # gRPC stop() with grace period waits for in-flight RPCs
                await self.grpc_server.stop(grace=graceful_timeout_sec)
                logger.info("gRPC server shutdown complete")
            except Exception as e:
                logger.warning("Error during gRPC shutdown", error=str(e))
            self.grpc_server = None

        # Signal REST server to exit (uvicorn handles graceful shutdown internally)
        if self._rest_server is not None:
            try:
                logger.info("Signaling REST server to exit")
                self._rest_server.should_exit = True
                # Wait a bit for REST server to drain connections
                await asyncio.sleep(2)
                logger.info("REST server shutdown signaled")
            except Exception as e:
                logger.warning("Error during REST shutdown", error=str(e))

        # Signal UI server to exit
        if self._ui_server is not None:
            try:
                logger.info("Signaling UI server to exit")
                self._ui_server.should_exit = True
                # Wait a bit for UI server to drain connections
                await asyncio.sleep(2)
                logger.info("UI server shutdown signaled")
            except Exception as e:
                logger.warning("Error during UI shutdown", error=str(e))

        # Cleanup downloaded package if we downloaded it
        if self._downloaded_package_path:
            try:
                import shutil
                from pathlib import Path
                # Remove the temp directory containing the downloaded package
                temp_dir = Path(self._downloaded_package_path).parent
                if temp_dir.exists() and "pixell_apkg_" in str(temp_dir):
                    logger.info("Cleaning up downloaded package", path=str(temp_dir))
                    shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception as e:
                logger.warning("Failed to cleanup downloaded package", error=str(e))

        logger.info("Runtime shutdown complete")


def create_runtime(package_path: str) -> ThreeSurfaceRuntime:
    """Create a three-surface runtime instance.

    Args:
        package_path: Path to the agent package (APKG file)

    Returns:
        Configured runtime instance
    """
    return ThreeSurfaceRuntime(package_path)


async def main():
    """Main entry point for three-surface runtime."""
    if len(sys.argv) != 2:
        print("Usage: python -m pixell_runtime.three_surface.runtime <package_path>")
        sys.exit(1)

    package_path = sys.argv[1]
    runtime = create_runtime(package_path)

    try:
        await runtime.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        await runtime.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
