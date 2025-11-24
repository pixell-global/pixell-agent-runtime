"""Pydantic models for supervisor API and internal state."""

from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class AgentStatus(str, Enum):
    """Agent process status."""
    PENDING = "pending"      # Deployment requested, not yet started
    STARTING = "starting"    # Process spawning in progress
    RUNNING = "running"      # Agent process is healthy
    STOPPING = "stopping"    # Shutdown in progress
    STOPPED = "stopped"      # Process stopped cleanly
    FAILED = "failed"        # Process crashed or failed health checks
    UPDATING = "updating"    # Update in progress (stopping old, starting new)


class Ports(BaseModel):
    """Port allocation for an agent."""
    rest: int = Field(..., ge=8081, le=8100, description="REST API port (8081-8100)")
    a2a: int = Field(..., ge=50052, le=50071, description="gRPC A2A port (50052-50071)")
    ui: int = Field(..., ge=3001, le=3020, description="UI server port (3001-3020)")


class DeployRequest(BaseModel):
    """Request to deploy a new agent."""
    agent_app_id: str = Field(..., description="Agent identifier (e.g., '4906eeb7')")
    deployment_id: str = Field(..., description="Deployment identifier from PAC")
    package_url: str = Field(..., description="S3 or HTTPS URL to APKG")
    package_sha256: Optional[str] = Field(None, description="SHA256 checksum for validation")

    # PAC-required fields (may not be used by PAR internally)
    version: str = Field(..., description="Package version (required by PAC)")
    org_id: str = Field(..., description="Organization ID (required by PAC)")

    # Optional configuration
    max_package_size_mb: int = Field(100, description="Maximum package size in MB")
    boot_budget_ms: int = Field(5000, description="Boot time budget in milliseconds")
    boot_hard_limit_multiplier: float = Field(2.0, description="Hard limit multiplier for boot time")
    graceful_shutdown_timeout_sec: int = Field(30, description="Graceful shutdown timeout in seconds")

    # Environment variables to pass to agent
    env: Dict[str, str] = Field(default_factory=dict, description="Additional environment variables")


class UpdateRequest(BaseModel):
    """Request to update an existing agent (zero-downtime)."""
    agent_app_id: Optional[str] = Field(None, description="Agent identifier (set from URL path)")
    deployment_id: str = Field(..., description="New deployment identifier")
    package_url: str = Field(..., description="S3 or HTTPS URL to new APKG")
    package_sha256: Optional[str] = Field(None, description="SHA256 checksum for validation")

    # PAC-required fields
    version: Optional[str] = Field(None, description="Package version (required by PAC)")

    # Optional configuration (can override previous values)
    max_package_size_mb: Optional[int] = Field(None, description="Maximum package size in MB")
    boot_budget_ms: Optional[int] = Field(None, description="Boot time budget in milliseconds")
    boot_hard_limit_multiplier: Optional[float] = Field(None, description="Hard limit multiplier for boot time")
    graceful_shutdown_timeout_sec: Optional[int] = Field(None, description="Graceful shutdown timeout in seconds")

    # Environment variables to pass to agent
    env: Optional[Dict[str, str]] = Field(None, description="Additional environment variables")


class DeleteRequest(BaseModel):
    """Request to delete an agent."""
    agent_app_id: str = Field(..., description="Agent identifier")
    force: bool = Field(False, description="Force delete even if agent is running")
    cleanup_user: bool = Field(True, description="Delete Linux user after stopping agent")


class AgentProcess(BaseModel):
    """Information about a running agent process."""
    agent_app_id: str
    deployment_id: str
    status: AgentStatus
    ports: Ports
    pid: Optional[int] = None
    linux_user: str
    package_path: str
    package_url: str
    package_sha256: Optional[str] = None
    venv_path: Optional[str] = Field(None, description="Virtual environment path for agent")

    # Timestamps
    created_at: datetime
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    last_health_check: Optional[datetime] = None

    # Health information
    health_check_failures: int = 0
    error_message: Optional[str] = None

    # Configuration
    config: Dict[str, Any] = Field(default_factory=dict, description="Agent configuration")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class DeployResponse(BaseModel):
    """Response after deploying an agent."""
    agent_app_id: str
    deployment_id: str
    status: str  # String value of AgentStatus enum
    ports: Ports
    linux_user: str
    pid: Optional[int] = None
    message: str
    created_at: datetime

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class AgentStatusResponse(BaseModel):
    """Response for agent status endpoint (detailed metrics)."""
    agent_app_id: str
    status: str  # String value of AgentStatus enum
    process_id: Optional[int] = None  # Note: PAC expects 'process_id', not 'pid'
    uptime_seconds: int = 0
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    ports: Ports
    health: Dict[str, bool] = Field(default_factory=dict, description="Health status per port type")

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class HealthResponse(BaseModel):
    """Health check response for supervisor."""
    ok: bool
    agents_running: int
    agents_total: int
    available_ports: int
    timestamp: datetime

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class ListAgentsResponse(BaseModel):
    """Response listing all agents."""
    agents: list[AgentProcess]
    total: int
    timestamp: datetime

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
