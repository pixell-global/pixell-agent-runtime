"""
Wheelhouse cache management for faster and more reliable package installations.

A wheelhouse is a directory containing pre-downloaded Python wheel files (.whl)
that can be used for offline or faster pip installations.
"""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Set

import structlog

logger = structlog.get_logger()


class WheelhouseManager:
    """
    Manages a wheelhouse cache for Python packages.
    
    Provides functionality to:
    - Validate wheelhouse directories
    - Download packages to wheelhouse
    - Use wheelhouse for pip installations
    - Manage wheelhouse contents
    """
    
    def __init__(self, wheelhouse_dir: Optional[Path] = None):
        """
        Initialize wheelhouse manager.
        
        Args:
            wheelhouse_dir: Path to wheelhouse directory. If None, uses WHEELHOUSE_DIR env var.
        """
        if wheelhouse_dir is None:
            wheelhouse_dir_str = os.getenv("WHEELHOUSE_DIR")
            if wheelhouse_dir_str:
                wheelhouse_dir = Path(wheelhouse_dir_str)
        
        self.wheelhouse_dir = wheelhouse_dir
        self._validated = False
    
    def is_available(self) -> bool:
        """Check if wheelhouse is available and valid."""
        if self.wheelhouse_dir is None:
            return False
        
        if not self.wheelhouse_dir.exists():
            logger.debug("Wheelhouse directory does not exist", 
                        wheelhouse_dir=str(self.wheelhouse_dir))
            return False
        
        if not self.wheelhouse_dir.is_dir():
            logger.warning("Wheelhouse path exists but is not a directory",
                          wheelhouse_dir=str(self.wheelhouse_dir))
            return False
        
        return True
    
    def validate(self) -> bool:
        """
        Validate wheelhouse directory structure and contents.
        
        Returns:
            True if wheelhouse is valid, False otherwise
        """
        if not self.is_available():
            return False
        
        try:
            # Check if directory is readable
            list(self.wheelhouse_dir.iterdir())
            
            # Count wheel files
            wheel_files = list(self.wheelhouse_dir.glob("*.whl"))
            
            if not wheel_files:
                logger.warning("Wheelhouse directory is empty (no .whl files)",
                              wheelhouse_dir=str(self.wheelhouse_dir))
                # Empty wheelhouse is valid, just not useful
            else:
                logger.info("Wheelhouse validated",
                           wheelhouse_dir=str(self.wheelhouse_dir),
                           wheel_count=len(wheel_files))
            
            self._validated = True
            return True
            
        except PermissionError:
            logger.error("No read permission for wheelhouse directory",
                        wheelhouse_dir=str(self.wheelhouse_dir))
            return False
        except Exception as e:
            logger.error("Failed to validate wheelhouse",
                        wheelhouse_dir=str(self.wheelhouse_dir),
                        error=str(e))
            return False
    
    def get_wheel_files(self) -> List[Path]:
        """
        Get list of wheel files in wheelhouse.
        
        Returns:
            List of paths to .whl files
        """
        if not self.is_available():
            return []
        
        try:
            return sorted(self.wheelhouse_dir.glob("*.whl"))
        except Exception as e:
            logger.error("Failed to list wheel files",
                        wheelhouse_dir=str(self.wheelhouse_dir),
                        error=str(e))
            return []
    
    def get_package_names(self) -> Set[str]:
        """
        Extract package names from wheel files in wheelhouse.
        
        Returns:
            Set of package names (lowercase, normalized)
        """
        packages = set()
        
        for wheel_file in self.get_wheel_files():
            # Wheel filename format: {distribution}-{version}(-{build})?-{python}-{abi}-{platform}.whl
            # We want the distribution name (first part)
            name_parts = wheel_file.stem.split("-")
            if name_parts:
                # Normalize package name (lowercase, replace _ with -)
                package_name = name_parts[0].lower().replace("_", "-")
                packages.add(package_name)
        
        return packages
    
    def get_pip_install_args(self, offline_mode: bool = False) -> List[str]:
        """
        Get pip install arguments for using wheelhouse.
        
        Args:
            offline_mode: If True, use --no-index to prevent network access
        
        Returns:
            List of pip arguments to add to install command
        """
        if not self.is_available():
            return []
        
        args = ["--find-links", str(self.wheelhouse_dir)]
        
        if offline_mode:
            args.insert(0, "--no-index")
            logger.info("Using wheelhouse in offline mode",
                       wheelhouse_dir=str(self.wheelhouse_dir))
        else:
            logger.info("Using wheelhouse with fallback to PyPI",
                       wheelhouse_dir=str(self.wheelhouse_dir))
        
        return args
    
    def download_packages(
        self,
        requirements_file: Path,
        python_executable: Optional[Path] = None
    ) -> bool:
        """
        Download packages from requirements file to wheelhouse.
        
        Args:
            requirements_file: Path to requirements.txt
            python_executable: Path to Python executable (default: sys.executable)
        
        Returns:
            True if successful, False otherwise
        """
        if self.wheelhouse_dir is None:
            logger.error("Cannot download packages: wheelhouse directory not set")
            return False
        
        # Create wheelhouse directory if it doesn't exist
        try:
            self.wheelhouse_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error("Failed to create wheelhouse directory",
                        wheelhouse_dir=str(self.wheelhouse_dir),
                        error=str(e))
            return False
        
        if not requirements_file.exists():
            logger.error("Requirements file does not exist",
                        requirements_file=str(requirements_file))
            return False
        
        # Determine pip executable
        if python_executable is None:
            import sys
            python_executable = Path(sys.executable)
        
        # Build pip download command
        cmd = [
            str(python_executable),
            "-m",
            "pip",
            "download",
            "-r",
            str(requirements_file),
            "-d",
            str(self.wheelhouse_dir)
        ]
        
        logger.info("Downloading packages to wheelhouse",
                   requirements_file=str(requirements_file),
                   wheelhouse_dir=str(self.wheelhouse_dir))
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode == 0:
                logger.info("Successfully downloaded packages to wheelhouse",
                           wheelhouse_dir=str(self.wheelhouse_dir))
                return True
            else:
                logger.error("Failed to download packages to wheelhouse",
                            returncode=result.returncode,
                            stderr=result.stderr[:500])
                return False
                
        except subprocess.TimeoutExpired:
            logger.error("Timeout downloading packages to wheelhouse",
                        timeout_seconds=300)
            return False
        except Exception as e:
            logger.error("Error downloading packages to wheelhouse",
                        error=str(e))
            return False
    
    def get_cache_info(self) -> dict:
        """
        Get information about wheelhouse cache.
        
        Returns:
            Dictionary with cache statistics
        """
        if not self.is_available():
            return {
                "available": False,
                "wheelhouse_dir": str(self.wheelhouse_dir) if self.wheelhouse_dir else None
            }
        
        wheel_files = self.get_wheel_files()
        total_size = sum(f.stat().st_size for f in wheel_files)
        
        return {
            "available": True,
            "validated": self._validated,
            "wheelhouse_dir": str(self.wheelhouse_dir),
            "wheel_count": len(wheel_files),
            "total_size_bytes": total_size,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "packages": sorted(self.get_package_names())
        }
    
    def clear_cache(self) -> bool:
        """
        Clear all files from wheelhouse cache.
        
        Returns:
            True if successful, False otherwise
        """
        if not self.is_available():
            logger.warning("Cannot clear cache: wheelhouse not available")
            return False
        
        try:
            for wheel_file in self.get_wheel_files():
                wheel_file.unlink()
            
            logger.info("Cleared wheelhouse cache",
                       wheelhouse_dir=str(self.wheelhouse_dir))
            return True
            
        except Exception as e:
            logger.error("Failed to clear wheelhouse cache",
                        wheelhouse_dir=str(self.wheelhouse_dir),
                        error=str(e))
            return False


def get_wheelhouse_manager() -> WheelhouseManager:
    """
    Get a WheelhouseManager instance configured from environment.
    
    Returns:
        WheelhouseManager instance
    """
    return WheelhouseManager()
