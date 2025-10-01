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
            apkg_path: Path to APKG file
            agent_app_id: Optional agent app ID (UUID) for venv isolation

        Returns:
            Loaded package instance

        Raises:
            PackageLoadError: If package cannot be loaded
            PackageValidationError: If package is invalid
        """
        logger.info("Loading package", path=str(apkg_path))
        
        # Validate file exists
        if not apkg_path.exists():
            raise PackageLoadError(f"Package file not found: {apkg_path}")
        
        # Calculate SHA256
        sha256 = self._calculate_sha256(apkg_path)
        
        # Extract to temp directory first
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                # Extract APKG
                with zipfile.ZipFile(apkg_path, 'r') as zf:
                    zf.extractall(temp_dir)
                
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
        
        # Extract basic info
        name = manifest_data.get("name", "unknown")
        version = manifest_data.get("version", "0.0.0")
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
            # Create a default export from entrypoint
            entrypoint = manifest_data.get("entrypoint", "main:handler")
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
            rest_config = RESTConfig(entry=rest_data.get("entry"))
        
        ui_config = None
        if "ui" in manifest_data:
            ui_data = manifest_data["ui"]
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

        # Check if venv already exists and is valid
        if venv_path.exists():
            if self._validate_venv(venv_path):
                logger.info("Reusing existing venv", venv=venv_name)
                # Update access time for LRU
                (venv_path / ".pixell_venv_metadata.json").touch()
                return venv_path
            else:
                logger.warning("Invalid venv found, rebuilding", venv=venv_name)
                shutil.rmtree(venv_path)

        # Create new venv
        logger.info("Creating virtual environment", venv=venv_name, package_id=package_id)

        try:
            # Create venv
            venv.create(venv_path, with_pip=True, clear=True)

            # Install requirements if exists
            requirements_file = package_path / "requirements.txt"
            if requirements_file.exists():
                pip_path = venv_path / "bin" / "pip"

                logger.info("Installing dependencies", venv=venv_name)

                # Run pip install with cache
                result = subprocess.run(
                    [
                        str(pip_path),
                        "install",
                        "--cache-dir", str(self.pip_cache_dir),
                        "-r", str(requirements_file)
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 minutes max
                )

                if result.returncode != 0:
                    logger.error("Failed to install dependencies", venv=venv_name, error=result.stderr)
                    raise PackageLoadError(f"Failed to install dependencies: {result.stderr}")

                logger.info("Dependencies installed successfully", venv=venv_name)
            else:
                logger.info("No requirements.txt found, empty venv created", venv=venv_name)

            # Install pixell-runtime itself into the venv so subprocess can run it
            pip_path = venv_path / "bin" / "pip"
            # Go from loader.py -> agents/ -> pixell_runtime/ -> src/ -> repo root
            par_source_dir = Path(__file__).parent.parent.parent.parent  # /app (in Docker) or repo root

            logger.info("Installing pixell-runtime in venv", venv=venv_name)

            result = subprocess.run(
                [
                    str(pip_path),
                    "install",
                    "--cache-dir", str(self.pip_cache_dir),
                    "-e", str(par_source_dir)
                ],
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