"""Package loader for APKG files."""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

import structlog
import yaml

from pixell_runtime.core.exceptions import PackageLoadError, PackageValidationError
from pixell_runtime.core.models import AgentExport, AgentManifest, AgentPackage, AgentStatus, A2AConfig, RESTConfig, UIConfig

logger = structlog.get_logger()


class PackageLoader:
    """Loads and validates APKG packages."""
    
    def __init__(self, packages_dir: Path):
        """Initialize package loader.
        
        Args:
            packages_dir: Directory to store extracted packages
        """
        self.packages_dir = packages_dir
        self.packages_dir.mkdir(parents=True, exist_ok=True)
    
    def load_package(self, apkg_path: Path) -> AgentPackage:
        """Load an APKG package.
        
        Args:
            apkg_path: Path to APKG file
            
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
                
                # Create package instance
                package = AgentPackage(
                    id=package_id,
                    manifest=manifest,
                    path=str(final_path),
                    url=f"https://local.pixell.runtime/packages/{package_id}",  # Use a placeholder URL
                    sha256=sha256,
                    status=AgentStatus.LOADING
                )
                
                # Add package path to Python path for imports
                sys.path.insert(0, str(final_path))
                
                logger.info("Package loaded successfully", package_id=package_id)
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