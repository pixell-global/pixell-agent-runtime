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
        self._http_a2a_server: Optional[uvicorn.Server] = None

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
        # Use environment variables from Supervisor to locate packages/venvs correctly
        packages_dir = Path("/tmp/pixell_packages")
        venvs_dir = None
        
        # If package path is known (from AGENT_PACKAGE_PATH or download), use its parent as packages_dir
        if self.package_path:
            packages_dir = Path(self.package_path).parent
            
        # If AGENT_VENV_PATH is provided by Supervisor, use its parent as venvs_dir
        # This ensures we use the Supervisor-created venvs in the user's home directory
        # instead of trying to create new ones in /tmp/venvs (which causes Permission denied)
        agent_venv_path = os.getenv("AGENT_VENV_PATH")
        if agent_venv_path:
            venvs_dir = Path(agent_venv_path).parent
            logger.info("Using venvs directory from environment", venvs_dir=str(venvs_dir))

        loader = PackageLoader(packages_dir, venvs_dir=venvs_dir)

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
        logger.info("Checking UI setup", 
                   multiplexed=self.multiplexed,
                   has_ui_config=bool(self.package.manifest.ui),
                   package_id=self.package.id)
        
        if self.multiplexed and self.package.manifest.ui:
            logger.info("Setting up UI routes for multiplexed mode", package_id=self.package.id)
            setup_ui_routes(self.rest_app, self.package)
        else:
            logger.info("Skipping UI setup", 
                       multiplexed=self.multiplexed,
                       has_ui_config=bool(self.package.manifest.ui))

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

    async def start_http_a2a_server(self):
        """Start the A2A HTTP server (A2AStarletteApplication)."""
        if not self.package:
            raise RuntimeError("Package must be loaded before starting servers")

        if not self.package.manifest.a2a or not self.package.manifest.a2a.http_server:
            logger.info("No HTTP A2A configuration found, skipping HTTP A2A server")
            return

        logger.info("Starting A2A HTTP server", port=self.a2a_port, entry=self.package.manifest.a2a.http_server)
        if hasattr(self, "_boot_metrics"):
            self._boot_metrics.start_phase("a2a_http")

        try:
            # Parse HTTP server entry point
            http_entry = self.package.manifest.a2a.http_server
            if ":" in http_entry:
                module_path, function_name = http_entry.split(":", 1)
            else:
                module_path = http_entry
                function_name = "main"  # Default to main() function

            # Add package path to sys.path
            import sys
            from pathlib import Path
            package_path = Path(self.package.path)
            
            # IMPORTANT: Add venv site-packages FIRST, before package path
            # This ensures that dependencies like a2a-sdk are found before
            # the agent's own modules are imported
            if hasattr(self.package, 'venv_path') and self.package.venv_path:
                venv_path = Path(self.package.venv_path)
                # Windows uses "Lib", Unix uses "lib"
                lib_dir = "Lib" if sys.platform == "win32" else "lib"
                
                # Try two possible paths for site-packages:
                # 1. Lib/site-packages (Windows venv standard)
                # 2. Lib/pythonX.Y/site-packages (some venv configurations)
                venv_site_packages = None
                possible_paths = [
                    venv_path / lib_dir / "site-packages",
                    venv_path / lib_dir / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
                ]
                
                for possible_path in possible_paths:
                    if possible_path.exists():
                        venv_site_packages = possible_path
                        break
                
                if venv_site_packages:
                    if str(venv_site_packages) not in sys.path:
                        sys.path.insert(0, str(venv_site_packages))
                        logger.info(
                            "Added venv site-packages to sys.path for A2A",
                            path=str(venv_site_packages),
                            exists=True
                        )
                    else:
                        logger.debug("Venv site-packages already in sys.path", path=str(venv_site_packages))
                    
                    # Verify a2a module is available before importing agent module
                    a2a_module_path = venv_site_packages / "a2a"
                    if a2a_module_path.exists() or (venv_site_packages / "a2a.py").exists():
                        logger.info("a2a module found in venv site-packages", path=str(venv_site_packages))
                    else:
                        logger.warning(
                            "a2a module not found in venv site-packages",
                            venv_site_packages=str(venv_site_packages),
                            hint="Ensure a2a-sdk is installed in venv"
                        )
                else:
                    logger.warning(
                        "Venv site-packages directory does not exist",
                        tried_paths=[str(p) for p in possible_paths],
                        venv_path=str(venv_path),
                        lib_dir=lib_dir
                    )
            else:
                logger.warning("No venv_path available for package", package_id=self.package.id)
            
            # Add package path to sys.path (after venv, so dependencies take precedence)
            if str(package_path) not in sys.path:
                sys.path.insert(0, str(package_path))
                logger.debug("Added package path to sys.path", path=str(package_path))

            # Sanitize environment variables before importing agent module
            # Some agent modules (like src/config.py) may try to convert env vars to int
            # at module level, and "None" string will cause errors
            import os
            env_vars_to_sanitize = ["DB_PORT", "DB_HOST", "DB_NAME"]
            
            # Check for .env file in package directory and sanitize it
            env_file = package_path / ".env"
            if env_file.exists():
                logger.debug("Found .env file in package, checking for invalid values", path=str(env_file))
                try:
                    with open(env_file, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    
                    # Check if .env file contains invalid values
                    modified = False
                    new_lines = []
                    for line in lines:
                        original_line = line
                        line = line.strip()
                        # Skip comments and empty lines
                        if not line or line.startswith('#'):
                            new_lines.append(original_line)
                            continue
                        
                        # Parse KEY=VALUE format
                        if '=' in line:
                            key, value = line.split('=', 1)
                            key = key.strip()
                            value = value.strip()
                            
                            # Remove quotes if present
                            if value.startswith('"') and value.endswith('"'):
                                value = value[1:-1]
                            elif value.startswith("'") and value.endswith("'"):
                                value = value[1:-1]
                            
                            # Check if this is a variable we need to sanitize
                            if key in env_vars_to_sanitize:
                                value_lower = value.lower()
                                if value_lower in ("none", "null") or value == "":
                                    # Comment out the line instead of removing it
                                    new_lines.append(f"# {original_line.strip()}  # Commented out by runtime: invalid value\n")
                                    modified = True
                                    logger.info(
                                        "Commented out invalid value in .env file",
                                        var_name=key,
                                        original_value=value,
                                        file=str(env_file)
                                    )
                                    continue
                        
                        new_lines.append(original_line)
                    
                    # Write back if modified
                    if modified:
                        with open(env_file, 'w', encoding='utf-8') as f:
                            f.writelines(new_lines)
                        logger.info("Updated .env file to remove invalid values", file=str(env_file))
                except Exception as e:
                    logger.warning("Failed to sanitize .env file", file=str(env_file), error=str(e))
            
            # Log current values before sanitization (check os.environ directly)
            logger.debug(
                "Checking environment variables before sanitization",
                db_port=os.environ.get("DB_PORT"),
                db_host=os.environ.get("DB_HOST"),
                db_name=os.environ.get("DB_NAME")
            )
            
            for var_name in env_vars_to_sanitize:
                # Check os.environ directly to catch "None" strings
                if var_name in os.environ:
                    value = os.environ[var_name]
                    if value is not None:
                        value_stripped = str(value).strip()
                        value_lower = value_stripped.lower()
                        if value_lower in ("none", "null") or value_stripped == "":
                            # Remove invalid values so default is used
                            del os.environ[var_name]
                            logger.info(
                                "Removed invalid environment variable",
                                var_name=var_name,
                                original_value=value
                            )
            
            # Log values after sanitization
            logger.debug(
                "Environment variables after sanitization",
                db_port=os.environ.get("DB_PORT"),
                db_host=os.environ.get("DB_HOST"),
                db_name=os.environ.get("DB_NAME")
            )

            # Log current sys.path for debugging
            logger.debug(
                "Importing A2A module",
                module_path=module_path,
                function_name=function_name,
                sys_path_length=len(sys.path),
                sys_path_first_few=sys.path[:5] if len(sys.path) > 5 else sys.path
            )

            # Pre-import a2a module to ensure it's available before importing agent module
            # This helps catch import errors early and ensures venv dependencies are found
            # The agent's main.py will try to import a2a at module level, so we need it available
            try:
                import importlib.util
                a2a_spec = importlib.util.find_spec("a2a")
                if a2a_spec is None:
                    logger.warning(
                        "a2a module not found in sys.path",
                        sys_path=sys.path[:10],
                        hint="a2a-sdk may not be installed in venv"
                    )
                else:
                    logger.info("a2a module found", location=a2a_spec.origin if a2a_spec.origin else "builtin")
                    # Actually import a2a to ensure it's in sys.modules before agent module imports it
                    try:
                        import a2a
                        logger.debug("Pre-imported a2a module successfully", a2a_path=getattr(a2a, '__file__', None))
                    except ImportError as e:
                        logger.warning("Failed to pre-import a2a module", error=str(e))
            except Exception as e:
                logger.debug("Could not check for a2a module", error=str(e))

            # Import and call the HTTP server function
            # Note: load_dotenv() in config.py may set env vars during import,
            # so we need to catch and handle that
            # The problem is that Config class fields are evaluated at module level,
            # so we can't clean env vars after import - we need to prevent the error
            # by ensuring env vars are clean BEFORE Config class fields are evaluated
            
            # Try importing with multiple retries, cleaning env vars each time
            max_retries = 3
            last_error = None
            
            for attempt in range(max_retries):
                try:
                    # Clean env vars before each import attempt
                    for var_name in env_vars_to_sanitize:
                        if var_name in os.environ:
                            value = os.environ[var_name]
                            value_stripped = str(value).strip()
                            value_lower = value_stripped.lower()
                            if value_lower in ("none", "null") or value_stripped == "":
                                del os.environ[var_name]
                                if attempt > 0:
                                    logger.debug(
                                        "Removed invalid environment variable (before import attempt)",
                                        var_name=var_name,
                                        original_value=value,
                                        attempt=attempt + 1
                                    )
                    
                    # Try importing
                    module = __import__(module_path, fromlist=[function_name])
                    
                    # If we get here, import succeeded
                    # Clean env vars one more time in case load_dotenv() set them
                    for var_name in env_vars_to_sanitize:
                        if var_name in os.environ:
                            value = os.environ[var_name]
                            value_stripped = str(value).strip()
                            value_lower = value_stripped.lower()
                            if value_lower in ("none", "null") or value_stripped == "":
                                del os.environ[var_name]
                                logger.info(
                                    "Removed invalid environment variable (post-import)",
                                    var_name=var_name,
                                    original_value=value
                                )
                    
                    # Success - break out of retry loop
                    break
                    
                except Exception as e:
                    last_error = e
                    error_str = str(e)
                    
                    # Check if this is the error we're trying to fix
                    if "invalid literal for int()" in error_str and "None" in error_str:
                        if attempt < max_retries - 1:
                            logger.warning(
                                "Import failed due to invalid env var, will retry",
                                error=error_str,
                                attempt=attempt + 1,
                                max_retries=max_retries,
                                exc_info=True
                            )
                            
                            # Remove module from cache
                            modules_to_remove = [module_path]
                            for key in list(sys.modules.keys()):
                                if key == module_path or key.startswith(module_path + "."):
                                    modules_to_remove.append(key)
                            
                            for mod_name in modules_to_remove:
                                if mod_name in sys.modules:
                                    del sys.modules[mod_name]
                                    logger.debug("Removed module from cache", module=mod_name)
                            
                            # Clean env vars before retry
                            for var_name in env_vars_to_sanitize:
                                if var_name in os.environ:
                                    value = os.environ[var_name]
                                    value_stripped = str(value).strip()
                                    value_lower = value_stripped.lower()
                                    if value_lower in ("none", "null") or value_stripped == "":
                                        del os.environ[var_name]
                                        logger.info(
                                            "Removed invalid environment variable (before retry)",
                                            var_name=var_name,
                                            original_value=value,
                                            attempt=attempt + 2
                                        )
                        else:
                            # Last attempt failed
                            logger.error(
                                "Import failed after all retries",
                                error=error_str,
                                max_retries=max_retries,
                                exc_info=True
                            )
                            raise
                    else:
                        # Different error - don't retry
                        # For ImportError, log and re-raise
                        if isinstance(e, ImportError):
                            logger.error(
                                "Failed to import A2A module",
                                module_path=module_path,
                                error=str(e),
                                sys_path=sys.path[:10],  # First 10 entries
                                exc_info=True
                            )
                        raise
            
            # If we get here, import succeeded (module is defined)
            if not hasattr(module, function_name):
                logger.error(
                    "HTTP A2A server function not found",
                    module=module_path,
                    function=function_name
                )
                return

            server_fn = getattr(module, function_name)
            
            # Call the function - it should return an ASGI app (A2AStarletteApplication.build())
            # or be a callable that takes no args and returns an ASGI app
            try:
                # Try calling with no args first
                app = server_fn()
            except TypeError:
                # If it needs arguments, try with port
                app = server_fn(port=self.a2a_port)

            # If it's an A2AStarletteApplication, call build()
            if hasattr(app, 'build'):
                app = app.build()

            # Start uvicorn server
            config = uvicorn.Config(
                app,
                host="0.0.0.0",
                port=self.a2a_port,
                log_config=None,
                access_log=False,
            )
            self._http_a2a_server = uvicorn.Server(config)
            logger.info("A2A HTTP server created", port=self.a2a_port)
            
            # Start server in background
            asyncio.create_task(self._http_a2a_server.serve())
            
            if hasattr(self, "_boot_metrics"):
                self._boot_metrics.end_phase("a2a_http")
                
            logger.info("A2A HTTP server started", port=self.a2a_port)
            
        except Exception as e:
            logger.error("Failed to start A2A HTTP server", error=str(e), exc_info=True)
            if hasattr(self, "_boot_metrics"):
                self._boot_metrics.end_phase("a2a_http")

    async def start_grpc_server(self):
        """Start the A2A gRPC server."""
        if not self.package:
            raise RuntimeError("Package must be loaded before starting servers")

        if not self.package.manifest.a2a:
            logger.info("No A2A configuration found, skipping gRPC server")
            return
        
        # Skip gRPC if HTTP server is configured
        if self.package.manifest.a2a.http_server:
            logger.info("HTTP A2A server configured, skipping gRPC server")
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
            # Start HTTP A2A server if configured, otherwise start gRPC server
            if self.package.manifest.a2a.http_server:
                http_a2a_task = asyncio.create_task(self.start_http_a2a_server())
            else:
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
        
        # Step 4: Shutdown A2A servers (gRPC or HTTP) with grace period
        if self._http_a2a_server:
            logger.info("Shutting down HTTP A2A server gracefully", grace_sec=graceful_timeout_sec)
            try:
                self._http_a2a_server.should_exit = True
                # Wait for server to stop
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning("Error shutting down HTTP A2A server", error=str(e))
        
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
