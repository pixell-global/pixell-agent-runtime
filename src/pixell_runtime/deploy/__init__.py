"""
Deployment primitives - LEGACY/DEPRECATED

WARNING: This module contains legacy control-plane code that should NOT be used
in the PAR runtime. PAR is now a pure data-plane runtime that executes a single
agent specified by environment variables.

- DeploymentManager: DEPRECATED - Move to PAC (Pixell Agent Cloud)
- DeploymentRequest/Status/Record: DEPRECATED - PAC models
- fetch_package_to_path: OK to use (data-plane functionality)

Only fetch.py should be used by PAR. All other files are for PAC or legacy compatibility.
"""

from .models import (
    DeploymentRequest,
    DeploymentStatus,
    DeploymentRecord,
    PackageLocation,
)
from .manager import DeploymentManager
from .fetch import fetch_package_to_path

# Only fetch_package_to_path is data-plane and should be used by PAR
__all__ = [
    "fetch_package_to_path",  # Data-plane: OK for PAR
    "PackageLocation",  # Data-plane: OK for PAR
    # DEPRECATED - Control-plane: Move to PAC
    "DeploymentRequest",
    "DeploymentStatus",
    "DeploymentRecord",
    "DeploymentManager",
]


