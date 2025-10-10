"""Package loader for APKG files."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import boto3
import structlog
import yaml

from pixell_runtime.core.exceptions import PackageLoadError, PackageValidationError
from pixell_runtime.core.models import AgentExport, AgentManifest, AgentPackage, AgentStatus, A2AConfig, RESTConfig, UIConfig

logger = structlog.get_logger()


class PackageLoader:
    """Loads and validates APKG packages."""

    def __init__(self, packages_dir: Path, venvs_dir: Optional[Path] = None):
        """Initialize package loader.

        Args:
            packages_dir: Directory to store extracted packages
            venvs_dir: Directory to store virtual environments (default: {packages_dir}/../venvs)
        """
        self.packages_dir = packages_dir
        self.packages_dir.mkdir(parents=True, exist_ok=True)

        # Setup venvs directory
        if venvs_dir is None:
            venvs_dir = packages_dir.parent / "venvs"
        self.venvs_dir = venvs_dir
        self.venvs_dir.mkdir(parents=True, exist_ok=True)

        # Setup pip cache directory
        self.pip_cache_dir = packages_dir.parent / "pip-cache"
        self.pip_cache_dir.mkdir(parents=True, exist_ok=True)
    
    def load_package(self, apkg_path: Path, agent_app_id: Optional[str] = None) -> AgentPackage:
        """Load an APKG package.

        Args:
            apkg_path: Path to APKG file or extracted directory
            agent_app_id: Optional agent app ID (UUID) for venv isolation

        Returns:
            Loaded package instance

        Raises:
            PackageLoadError: If package cannot be loaded
            PackageValidationError: If package is invalid
        """
        logger.info("Loading package", path=str(apkg_path))

        # Validate path exists
        if not apkg_path.exists():
            raise PackageLoadError(f"Package path not found: {apkg_path}")

        # If path is a directory, load from extracted package
        if apkg_path.is_dir():
            return self.load_from_directory(apkg_path, agent_app_id)

        # Calculate SHA256
        sha256 = self._calculate_sha256(apkg_path)
        
        # Extract to temp directory first
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                # Extract APKG
                with zipfile.ZipFile(apkg_path, 'r') as zf:
                    self._safe_extract_zip(zf, Path(temp_dir))
                
                # Load and validate manifest
                manifest_path = Path(temp_dir) / "agent.yaml"
                if not manifest_path.exists():
                    raise PackageValidationError("Missing agent.yaml manifest")
                
                with open(manifest_path) as f:
                    manifest_data = yaml.safe_load(f)
                
                # Parse manifest with our model
                manifest = self._parse_manifest(manifest_data)
                
                # Create package ID
                package_id = f"{manifest.name}@{manifest.version}"
                
                # Move to final location
                final_path = self.packages_dir / package_id
                if final_path.exists():
                    logger.warning("Package already exists, replacing", package_id=package_id)
                    shutil.rmtree(final_path)
                
                shutil.move(temp_dir, str(final_path))

                # Create or reuse virtual environment
                venv_path = self._ensure_venv(package_id, final_path, agent_app_id)

                # Create package instance
                package = AgentPackage(
                    id=package_id,
                    manifest=manifest,
                    path=str(final_path),
                    url=f"https://local.pixell.runtime/packages/{package_id}",  # Use a placeholder URL
                    sha256=sha256,
                    status=AgentStatus.LOADING,
                    venv_path=str(venv_path)  # Add venv path
                )

                logger.info("Package loaded successfully", package_id=package_id, venv=venv_path.name)
                return package
                
            except Exception as e:
                logger.error("Failed to load package", error=str(e))
                raise PackageLoadError(f"Failed to load package: {e}")

    def load_from_directory(self, package_dir: Path, agent_app_id: Optional[str] = None) -> AgentPackage:
        """Load a package from an already-extracted directory.

        Args:
            package_dir: Path to extracted package directory
            agent_app_id: Optional agent app ID (UUID) for venv isolation

        Returns:
            Loaded package instance

        Raises:
            PackageLoadError: If package cannot be loaded
            PackageValidationError: If package is invalid
        """
        logger.info("Loading package from directory", path=str(package_dir))

        try:
            # Load and validate manifest
            manifest_path = package_dir / "agent.yaml"
            if not manifest_path.exists():
                raise PackageValidationError("Missing agent.yaml manifest")

            with open(manifest_path) as f:
                manifest_data = yaml.safe_load(f)

            # Parse manifest with our model
            manifest = self._parse_manifest(manifest_data)

            # Extract package ID from directory name (e.g., "vivid-commenter@1.0.1")
            package_id = package_dir.name

            # Get venv path from environment variable if available (subprocess case)
            # Otherwise find/create venv
            venv_path_str = os.environ.get("AGENT_VENV_PATH")
            if venv_path_str:
                venv_path = Path(venv_path_str)
                logger.info("Using venv from environment", venv=venv_path.name)
            else:
                # Find or create virtual environment
                if agent_app_id:
                    # Calculate requirements hash to find existing venv
                    req_hash = self._calculate_requirements_hash(package_dir)
                    venv_name = f"{agent_app_id}_{req_hash}"
                    venv_path = self.venvs_dir / venv_name

                    if not venv_path.exists() or not self._validate_venv(venv_path):
                        logger.warning("Venv not found for extracted package, creating new one", venv=venv_name)
                        venv_path = self._ensure_venv(package_id, package_dir, agent_app_id)
                else:
                    logger.warning("No agent_app_id or venv path provided for directory load, creating venv")
                    venv_path = self._ensure_venv(package_id, package_dir, agent_app_id)

            # Create package instance (no SHA256 since we don't have the original APKG file)
            package = AgentPackage(
                id=package_id,
                manifest=manifest,
                path=str(package_dir),
                url=f"https://local.pixell.runtime/packages/{package_id}",
                sha256="",  # Not available for directory loads
                status=AgentStatus.LOADING,
                venv_path=str(venv_path)
            )

            logger.info("Package loaded from directory", package_id=package_id, venv=venv_path.name)
            return package

        except Exception as e:
            logger.error("Failed to load package from directory", error=str(e))
            raise PackageLoadError(f"Failed to load package from directory: {e}")

    def _calculate_sha256(self, file_path: Path) -> str:
        """Calculate SHA256 hash of file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    
    def _parse_manifest(self, manifest_data: Dict[str, Any]) -> AgentManifest:
        """Parse manifest data into model."""
        # Convert the manifest format
        # The APKG uses a different format than our runtime expects
        
        # Extract basic info (validate required fields)
        name = manifest_data.get("name", "unknown")
        version = manifest_data.get("version")
        if not version:
            raise PackageValidationError("Manifest missing required 'version'")
        description = manifest_data.get("description", "")
        author = manifest_data.get("author", "")
        
        # For this Python agent, we'll create exports based on the sub_agents
        exports = []
        
        # Check if we have sub_agents in metadata
        metadata = manifest_data.get("metadata", {})
        sub_agents = metadata.get("sub_agents", [])
        
        if sub_agents:
            # Use sub_agents to create exports
            for sub_agent in sub_agents:
                export = AgentExport(
                    id=sub_agent["name"],
                    name=sub_agent["description"],
                    description=sub_agent["description"],
                    version=version,
                    handler=f"{manifest_data['entrypoint']}:{sub_agent['name']}",
                    private=not sub_agent.get("public", True)
                )
                exports.append(export)
        else:
            # Create a default export from entrypoint (required)
            entrypoint = manifest_data.get("entrypoint")
            if not entrypoint:
                raise PackageValidationError("Manifest missing required 'entrypoint'")
            export = AgentExport(
                id="default",
                name=name,
                description=description,
                version=version,
                handler=entrypoint,
                private=False
            )
            exports.append(export)
        
        # Parse three-surface configuration
        a2a_config = None
        if "a2a" in manifest_data:
            a2a_data = manifest_data["a2a"]
            a2a_config = A2AConfig(service=a2a_data.get("service"))
        
        rest_config = None
        if "rest" in manifest_data:
            rest_data = manifest_data["rest"]
            if "entry" not in rest_data or not rest_data.get("entry"):
                raise PackageValidationError("REST config missing required 'entry'")
            rest_config = RESTConfig(entry=rest_data.get("entry"))
        
        ui_config = None
        if "ui" in manifest_data:
            ui_data = manifest_data["ui"]
            if "path" not in ui_data or not ui_data.get("path"):
                raise PackageValidationError("UI config missing required 'path'")
            ui_config = UIConfig(
                path=ui_data.get("path"),
                basePath=ui_data.get("basePath", "/")
            )
        
        # Create manifest
        return AgentManifest(
            name=name,
            version=version,
            entrypoint=manifest_data.get("entrypoint"),
            runtime_version="0.1.0",  # Default for now
            description=description,
            author=author,
            exports=exports,
            dependencies=manifest_data.get("dependencies", []),
            a2a=a2a_config,
            rest=rest_config,
            ui=ui_config
        )

    def _safe_extract_zip(self, zf: zipfile.ZipFile, dest_dir: Path):
        """Safely extract a zipfile to dest_dir, preventing zip-slip.

        Raises PackageValidationError on path traversal.
        """
        dest_dir.mkdir(parents=True, exist_ok=True)
        base = dest_dir.resolve()
        for member in zf.infolist():
            member_path = Path(member.filename)
            # Skip absolute paths and parent traversals
            if member_path.is_absolute() or ".." in member_path.parts:
                raise PackageValidationError("Zip contains unsafe paths (zip-slip)")
            target = (base / member_path).resolve()
            if not str(target).startswith(str(base)):
                raise PackageValidationError("Zip extraction escaped destination (zip-slip)")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member, 'r') as src, open(target, 'wb') as dst:
                    shutil.copyfileobj(src, dst)

    def _calculate_requirements_hash(self, package_path: Path) -> str:
        """Calculate SHA256 hash of requirements.txt.

        Args:
            package_path: Path to extracted package

        Returns:
            Short SHA256 hash (7 chars) or 'no-deps' if no requirements.txt
        """
        requirements_file = package_path / "requirements.txt"

        if not requirements_file.exists():
            return "no-deps"

        sha256_hash = hashlib.sha256()
        with open(requirements_file, "rb") as f:
            sha256_hash.update(f.read())

        return sha256_hash.hexdigest()[:7]

    def _get_codeartifact_pip_index(self) -> Optional[str]:
        """Get CodeArtifact pip index URL with auth token.

        Returns:
            CodeArtifact pip index URL with embedded auth token, or None if not configured
        """
        # Opt-in only: require ENABLE_CODEARTIFACT=true to avoid network calls in tests/local
        if os.environ.get("ENABLE_CODEARTIFACT", "false").lower() != "true":
            return None

        # Check if CodeArtifact is configured via environment variables
        aws_region = os.environ.get("AWS_REGION", "us-east-2")
        ca_domain = os.environ.get("CODEARTIFACT_DOMAIN", "pixell")
        ca_repo = os.environ.get("CODEARTIFACT_REPOSITORY", "pypi-store")
        ca_domain_owner = os.environ.get("AWS_ACCOUNT_ID", "636212886452")

        try:
            # Get auth token from CodeArtifact
            client = boto3.client("codeartifact", region_name=aws_region)
            response = client.get_authorization_token(
                domain=ca_domain,
                domainOwner=ca_domain_owner,
                durationSeconds=3600  # 1 hour
            )
            auth_token = response["authorizationToken"]

            # Get repository endpoint
            endpoint_response = client.get_repository_endpoint(
                domain=ca_domain,
                domainOwner=ca_domain_owner,
                repository=ca_repo,
                format="pypi"
            )
            repo_endpoint = endpoint_response["repositoryEndpoint"]

            # Construct pip index URL with embedded token
            # Format: https://aws:{token}@domain-owner.d.codeartifact.region.amazonaws.com/pypi/repo/simple/
            pip_index_url = repo_endpoint.replace("https://", f"https://aws:{auth_token}@") + "simple/"

            logger.info("Using CodeArtifact for pip installs", domain=ca_domain, repository=ca_repo)
            return pip_index_url

        except Exception as e:
            logger.warning("Failed to get CodeArtifact credentials, falling back to PyPI", error=str(e))
            return None

    def _install_requirements(
        self,
        venv_path: Path,
        package_path: Path,
        venv_name: str
    ) -> bool:
        """Install dependencies from requirements.txt into agent venv.

        Args:
            venv_path: Path to the virtual environment
            package_path: Path to the agent package directory
            venv_name: Name of venv for logging

        Returns:
            True if requirements installed successfully or no requirements file,
            False if installation failed

        Raises:
            PackageLoadError: If requirements installation fails critically
        """
        req_file = package_path / "requirements.txt"

        # If no requirements.txt, nothing to do
        if not req_file.exists():
            logger.info("No requirements.txt found, skipping dependency installation",
                       venv=venv_name,
                       package_path=str(package_path))
            return True

        # Check if requirements.txt is empty
        try:
            with open(req_file, 'r') as f:
                content = f.read().strip()
                # Filter out comments and empty lines
                lines = [line.strip() for line in content.split('\n')
                        if line.strip() and not line.strip().startswith('#')]

                if not lines:
                    logger.info("requirements.txt is empty, skipping dependency installation",
                               venv=venv_name)
                    return True

                logger.info("Found requirements.txt with dependencies",
                           venv=venv_name,
                           line_count=len(lines))
        except Exception as e:
            logger.warning("Failed to read requirements.txt, attempting install anyway",
                          venv=venv_name,
                          error=str(e))

        # Determine pip path
        if sys.platform == "win32":
            pip_path = venv_path / "Scripts" / "pip"
        else:
            pip_path = venv_path / "bin" / "pip"

        # Check if uv is available (faster installer)
        uv_available = shutil.which("uv") is not None
        logger.info("Checking for uv availability",
                   venv=venv_name,
                   uv_available=uv_available,
                   uv_path=shutil.which("uv") if uv_available else "not found")

        # Build install command (try uv first, fallback to pip)
        pip_index_url = self._get_codeartifact_pip_index()

        # Optional wheelhouse to speed cold starts (local artifacts)
        from pixell_runtime.core.wheelhouse import get_wheelhouse_manager
        wheelhouse = get_wheelhouse_manager()

        wheelhouse_args = []
        if wheelhouse.is_available() and wheelhouse.validate():
            # Use wheelhouse with fallback to PyPI (not offline mode)
            wheelhouse_args = wheelhouse.get_pip_install_args(offline_mode=False)

            cache_info = wheelhouse.get_cache_info()
            logger.info("Using wheelhouse for requirements installation",
                       venv=venv_name,
                       wheelhouse_dir=cache_info["wheelhouse_dir"],
                       wheel_count=cache_info["wheel_count"],
                       packages_cached=len(cache_info["packages"]))

        # Try uv first for faster installation (10-100x speedup)
        result = None
        install_cmd = None

        if uv_available:
            # uv pip install syntax
            install_cmd = ["uv", "pip", "install", "-r", str(req_file), "--python", str(venv_path / "bin" / "python")]

            if pip_index_url:
                install_cmd.extend(["--index-url", pip_index_url])

            # Add wheelhouse args (uv supports --find-links)
            install_cmd.extend(wheelhouse_args)

            logger.info("Installing dependencies with uv (fast mode)",
                       venv=venv_name,
                       requirements_file=str(req_file),
                       command=" ".join(install_cmd))

            try:
                result = subprocess.run(
                    install_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minutes max for dependency installation
                )

                if result.returncode != 0:
                    logger.warning("uv installation failed, falling back to pip",
                                  venv=venv_name,
                                  stderr=result.stderr[:500])
                    result = None  # Force fallback to pip
                else:
                    logger.info("uv installation succeeded",
                               venv=venv_name)

            except Exception as e:
                logger.warning("uv installation error, falling back to pip",
                              venv=venv_name,
                              error=str(e))
                result = None  # Force fallback to pip

        # Fallback to pip if uv not available or failed
        if result is None or result.returncode != 0:
            install_cmd = [str(pip_path), "install", "-r", str(req_file)]

            if pip_index_url:
                install_cmd.extend(["--index-url", pip_index_url])
                logger.debug("Using CodeArtifact index for requirements installation",
                            venv=venv_name)

            install_cmd.extend(wheelhouse_args)

            logger.info("Installing dependencies from requirements.txt with pip",
                       venv=venv_name,
                       requirements_file=str(req_file),
                       command=" ".join(install_cmd))

            try:
                result = subprocess.run(
                    install_cmd,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minutes max for dependency installation
                )

                if result.returncode != 0:
                    logger.error("Requirements installation failed",
                                venv=venv_name,
                                returncode=result.returncode,
                                stderr=result.stderr,
                                stdout=result.stdout)

                    # Check for common errors
                    if "Could not find a version" in result.stderr:
                        raise PackageLoadError(
                            f"Dependency not found in PyPI/CodeArtifact. "
                            f"Check requirements.txt for typos or unavailable packages: {result.stderr}"
                        )
                    elif "THESE PACKAGES DO NOT MATCH THE HASHES" in result.stderr:
                        raise PackageLoadError(
                            f"Hash verification failed. Remove hash constraints or verify integrity: {result.stderr}"
                        )
                    elif "ERROR: No matching distribution" in result.stderr:
                        raise PackageLoadError(
                            f"Package version not found. Check version constraints: {result.stderr}"
                        )
                    else:
                        raise PackageLoadError(f"Requirements installation failed: {result.stderr}")

                # Log successful installation
                logger.info("Requirements installed successfully",
                           venv=venv_name,
                           requirements_file=str(req_file))

                # Parse and log installed packages (optional, for debugging)
                if result.stdout:
                    installed_packages = []
                    for line in result.stdout.split('\n'):
                        if "Successfully installed" in line:
                            # Extract package names from "Successfully installed pkg1-1.0 pkg2-2.0"
                            parts = line.split("Successfully installed")
                            if len(parts) > 1:
                                installed_packages = parts[1].strip().split()

                    if installed_packages:
                        logger.info("Installed dependencies",
                                   venv=venv_name,
                                   packages=installed_packages)

                return True

            except subprocess.TimeoutExpired:
                logger.error("Requirements installation timed out after 300s",
                            venv=venv_name,
                            requirements_file=str(req_file))
                raise PackageLoadError(
                    f"Requirements installation timed out after 5 minutes. "
                    f"Check for large dependencies or network issues."
                )
            except Exception as e:
                logger.error("Unexpected error during requirements installation",
                            venv=venv_name,
                            error=str(e),
                            error_type=type(e).__name__)
                raise PackageLoadError(f"Unexpected error installing requirements: {e}")

    def _ensure_venv(self, package_id: str, package_path: Path, agent_app_id: Optional[str] = None) -> Path:
        """Create or reuse virtual environment for package.

        Args:
            package_id: Package identifier (e.g., 'vivid-commenter@1.0.0')
            package_path: Path to extracted package
            agent_app_id: Optional agent app ID (UUID) for uniqueness

        Returns:
            Path to virtual environment

        Raises:
            PackageLoadError: If venv creation fails
        """
        # Calculate requirements hash
        req_hash = self._calculate_requirements_hash(package_path)

        # Venv path with agent_app_id for uniqueness across different developers
        if agent_app_id:
            # Use agent_app_id to ensure uniqueness
            venv_name = f"{agent_app_id}_{req_hash}"
        else:
            # Fallback to package_id (for backward compatibility)
            venv_name = f"{package_id}_{req_hash}"

        venv_path = self.venvs_dir / venv_name

        # Reuse existing venv when requirements hash matches and venv validates
        if venv_path.exists():
            if self._validate_venv(venv_path):
                logger.info("Reusing existing venv", venv=venv_name)
                return venv_path
            logger.info("Existing venv invalid or incomplete, recreating", venv=venv_name)
            shutil.rmtree(venv_path)

        # Create new venv
        logger.info("Creating virtual environment", venv=venv_name, package_id=package_id)

        try:
            # Create venv
            venv.create(venv_path, with_pip=True, clear=True)

            # Optionally upgrade pip for production (skip by default to speed tests)
            pip_path = venv_path / "bin" / "pip"
            if os.environ.get("PAR_UPGRADE_PIP", "false").lower() == "true":
                logger.info("Upgrading pip in venv", venv=venv_name)
                result = subprocess.run(
                    [str(pip_path), "install", "--upgrade", "pip"],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if result.returncode != 0:
                    logger.warning("Failed to upgrade pip, continuing with existing version", venv=venv_name, error=result.stderr)

            # Install the agent package itself (if setup.py exists)
            setup_file = package_path / "setup.py"
            if setup_file.exists():
                # First ensure setuptools and wheel are installed (needed for editable installs)
                logger.info("Installing setuptools and wheel for editable install", venv=venv_name)
                try:
                    pip_index_url = self._get_codeartifact_pip_index()
                    setup_cmd = [str(pip_path), "install", "setuptools", "wheel"]
                    if pip_index_url:
                        setup_cmd.extend(["--index-url", pip_index_url])

                    result = subprocess.run(
                        setup_cmd,
                        capture_output=True,
                        text=True,
                        timeout=60
                    )
                    if result.returncode != 0:
                        logger.warning("Failed to install setuptools/wheel, continuing anyway",
                                     venv=venv_name,
                                     error=result.stderr)
                except subprocess.TimeoutExpired:
                    logger.warning("setuptools/wheel installation timed out, continuing anyway",
                                 venv=venv_name)

                # Now install agent package in editable mode
                logger.info("Installing agent package in editable mode",
                           package_path=str(package_path),
                           venv=venv_name)
                try:
                    result = subprocess.run(
                        [str(pip_path), "install", "-e", str(package_path)],
                        capture_output=True,
                        text=True,
                        timeout=120  # 2 minutes max
                    )

                    if result.returncode != 0:
                        logger.error("Agent package installation failed",
                                    venv=venv_name,
                                    stderr=result.stderr,
                                    stdout=result.stdout)
                        raise PackageLoadError(f"Agent package installation failed: {result.stderr}")

                    logger.info("Agent package installed successfully", venv=venv_name)

                except subprocess.TimeoutExpired:
                    logger.error("Agent package installation timed out", venv=venv_name)
                    raise PackageLoadError("Agent package installation timed out after 120s")
            else:
                logger.info("No setup.py found - skipping agent package installation",
                           venv=venv_name,
                           note="Agent may have import issues if it uses root-level packages")

            # Install requirements.txt (if exists)
            # This ensures dependencies are available even if setup.py doesn't have install_requires
            logger.info("Installing dependencies from requirements.txt",
                       venv=venv_name,
                       package_path=str(package_path))

            self._install_requirements(venv_path, package_path, venv_name)

            logger.info("Venv setup complete",
                       venv=venv_name,
                       venv_path=str(venv_path))

            # Optionally install pixell-runtime into the venv (default: disabled; use PYTHONPATH)
            if os.environ.get("PAR_INSTALL_SELF_IN_VENV", "false").lower() == "true":
                pip_path = venv_path / "bin" / "pip"
                # Go from loader.py -> agents/ -> pixell_runtime/ -> src/ -> repo root
                par_source_dir = Path(__file__).parent.parent.parent.parent  # /app (in Docker) or repo root

                logger.info("Installing pixell-runtime in venv", venv=venv_name)

                # Get CodeArtifact index URL if available (reuse from agent dependencies)
                pip_index_url = self._get_codeartifact_pip_index()

                # Build pip install command for pixell-runtime
                par_pip_cmd = [
                    str(pip_path),
                    "install",
                    "--cache-dir", str(self.pip_cache_dir),
                ]

                # Add index URL if CodeArtifact is available
                if pip_index_url:
                    par_pip_cmd.extend(["--index-url", pip_index_url])

                par_pip_cmd.append(str(par_source_dir))

                result = subprocess.run(
                    par_pip_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120  # 2 minutes max
                )

                if result.returncode != 0:
                    logger.error("Failed to install pixell-runtime in venv", venv=venv_name, error=result.stderr)
                    raise PackageLoadError(f"Failed to install pixell-runtime: {result.stderr}")

                logger.info("pixell-runtime installed in venv", venv=venv_name)

            # Store venv metadata
            self._store_venv_metadata(venv_path, package_id, req_hash)

            logger.info("Virtual environment ready", venv=venv_name)
            return venv_path

        except subprocess.TimeoutExpired:
            logger.error("Dependency installation timed out", venv=venv_name)
            if venv_path.exists():
                shutil.rmtree(venv_path)
            raise PackageLoadError("Dependency installation timed out after 5 minutes")
        except Exception as e:
            logger.error("Failed to create venv", venv=venv_name, error=str(e))
            if venv_path.exists():
                shutil.rmtree(venv_path)
            raise PackageLoadError(f"Failed to create virtual environment: {e}")

    def _validate_venv(self, venv_path: Path) -> bool:
        """Validate that venv is complete and functional.

        Args:
            venv_path: Path to virtual environment

        Returns:
            True if venv is valid, False otherwise
        """
        # Check Python executable exists
        python_path = venv_path / "bin" / "python"
        if not python_path.exists():
            logger.warning("Venv missing python executable", venv=venv_path.name)
            return False

        # Check metadata exists
        metadata_path = venv_path / ".pixell_venv_metadata.json"
        if not metadata_path.exists():
            logger.warning("Venv missing metadata", venv=venv_path.name)
            return False

        # Check venv can execute Python
        try:
            result = subprocess.run(
                [str(python_path), "-c", "import sys; print(sys.prefix)"],
                capture_output=True,
                text=True,
                timeout=5
            )

            if result.returncode != 0:
                logger.warning("Venv python execution failed", venv=venv_path.name)
                return False

            # Verify it's using the venv (not system Python)
            if str(venv_path) not in result.stdout:
                logger.warning("Venv not isolated", venv=venv_path.name, prefix=result.stdout.strip())
                return False

            return True

        except Exception as e:
            logger.warning("Venv validation failed", venv=venv_path.name, error=str(e))
            return False

    def _store_venv_metadata(self, venv_path: Path, package_id: str, req_hash: str):
        """Store metadata about the virtual environment.

        Args:
            venv_path: Path to virtual environment
            package_id: Package identifier
            req_hash: Requirements hash
        """
        metadata = {
            "package_id": package_id,
            "requirements_sha256": req_hash,
            "created_at": datetime.utcnow().isoformat(),
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        }

        metadata_path = venv_path / ".pixell_venv_metadata.json"
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)

        logger.debug("Stored venv metadata", venv=venv_path.name)