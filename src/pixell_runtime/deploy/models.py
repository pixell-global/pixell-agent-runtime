"""Models for deployments in PAR push-only execution model."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, Optional, Union

from pydantic import BaseModel, Field, HttpUrl


class DeploymentStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    LOADING = "loading"
    DEPLOYED = "deployed"      # Package loaded, runtime starting
    STARTING = "starting"      # Runtime starting up
    HEALTHY = "healthy"        # Runtime confirmed healthy
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"


class PackageS3Ref(BaseModel):
    bucket: str
    key: str
    signedUrl: Optional[HttpUrl] = None


class PackageLocation(BaseModel):
    """Package reference - either URL or S3 ref."""

    packageUrl: Optional[HttpUrl] = None
    s3: Optional[PackageS3Ref] = None


class SurfacesConfig(BaseModel):
    mode: str = Field(..., pattern="^(multiplex|multiport)$")
    ports: Optional[Dict[str, int]] = None


class WebhookConfig(BaseModel):
    url: HttpUrl
    secret: Optional[str] = None


class DeploymentRequest(BaseModel):
    deploymentId: str
    agentAppId: str
    orgId: str
    version: str
    packageUrl: Optional[Union[HttpUrl, PackageS3Ref, Dict]] = None
    cpuUnits: Optional[int] = 256
    memoryMB: Optional[int] = 512
    surfaces: Optional[SurfacesConfig] = None
    webhook: Optional[WebhookConfig] = None
    # Cache control flags (Phase 1)
    forceRefresh: Optional[bool] = False
    # Package integrity (Phase 2)
    packageSha256: Optional[str] = None

    @property
    def package_location(self) -> PackageLocation:
        if isinstance(self.packageUrl, str) or self.packageUrl is not None:
            return PackageLocation(packageUrl=self.packageUrl)  # type: ignore[arg-type]
        if isinstance(self.packageUrl, dict):
            # Accept dict forms for flexibility
            if "bucket" in self.packageUrl and "key" in self.packageUrl:
                return PackageLocation(s3=PackageS3Ref(**self.packageUrl))
            if "packageUrl" in self.packageUrl:
                return PackageLocation(packageUrl=self.packageUrl["packageUrl"]) 
        # If none provided, raise
        raise ValueError("packageUrl is required")


class DeploymentRecord(BaseModel):
    deploymentId: str
    agentAppId: str
    orgId: str
    version: str
    status: DeploymentStatus = DeploymentStatus.PENDING
    createdAt: datetime = Field(default_factory=datetime.utcnow)
    updatedAt: datetime = Field(default_factory=datetime.utcnow)
    details: Dict[str, str] = Field(default_factory=dict)
    surfaces: Optional[SurfacesConfig] = None
    webhook: Optional[WebhookConfig] = None
    package_path: Optional[str] = None
    venv_path: Optional[str] = None
    rest_port: Optional[int] = None
    a2a_port: Optional[int] = None
    ui_port: Optional[int] = None

    def update_status(self, status: DeploymentStatus, details: Optional[Dict[str, str]] = None):
        self.status = status
        self.updatedAt = datetime.utcnow()
        if details:
            self.details.update(details)


