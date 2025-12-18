"""Pydantic models for supervisor API and internal state."""

from typing import Optional, Dict, Any, Union
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, model_validator


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
    """DEPRECATED: Port allocation for an agent.

    WARNING: Port mode (socket_mode=False) is DEPRECATED and should NEVER be activated.
    All agents MUST use Unix domain sockets (socket_mode=True) instead.
    Port mode has a hard capacity limit of 200 agents per instance and is being phased out.
    This class exists only for backwards compatibility during migration.
    TODO: Remove this class once all agents are migrated to socket mode.

    PAC allocates ports in these ranges (per instance, 200-agent capacity):
    - A2A (gRPC):  60000-60199 (slot_number + 60000)
    - REST API:    63000-63199 (slot_number + 63000)
    - UI Server:   65000-65199 (slot_number + 65000)

    Note: Port constraints removed - PAC manages port ranges.
    """
    rest: int = Field(..., ge=1024, le=65535, description="DEPRECATED: REST API port")
    a2a: int = Field(..., ge=1024, le=65535, description="DEPRECATED: gRPC A2A port")
    ui: int = Field(..., ge=1024, le=65535, description="DEPRECATED: UI server port")


class SocketPathsModel(BaseModel):
    """Unix domain socket paths for an agent (Pydantic model for API).

    Used when socket_mode=True instead of Ports.
    Socket architecture provides unlimited agent capacity.

    Directory structure:
        /var/run/pixell-agents/agent_{short_id}/
        ├── rest.sock  (REST API)
        ├── a2a.sock   (gRPC A2A)
        └── ui.sock    (UI server)
    """
    base_dir: str = Field(..., description="Base directory for agent sockets")
    rest: str = Field(..., description="REST API socket path")
    a2a: str = Field(..., description="gRPC A2A socket path")
    ui: str = Field(..., description="UI server socket path")


class DeployRequest(BaseModel):
    """Request to deploy a new agent."""
    agent_app_id: str = Field(..., description="Agent identifier (e.g., '4906eeb7')")
    deployment_id: str = Field(..., description="Deployment identifier from PAC")
    package_url: str = Field(..., description="S3 or HTTPS URL to APKG")
    package_sha256: Optional[str] = Field(None, description="SHA256 checksum for validation")

    # PAC-required fields (may not be used by PAR internally)
    version: str = Field(..., description="Package version (required by PAC)")
    org_id: str = Field(..., description="Organization ID (required by PAC)")

    # Short IDs for Linux username generation (avoid hyphens and length limits)
    org_short_id: Optional[str] = Field(None, description="Organization short ID (16 chars, e.g., 'x8f2k9m4n7p1q3r5')")
    agent_short_id: Optional[str] = Field(None, description="Agent short ID (8 chars, e.g., 'a7b2c9d4')")

    # Socket mode flag - when True, use Unix domain sockets instead of TCP ports
    # IMPORTANT: socket_mode=True (Unix sockets) is the ONLY supported mode.
    # DEPRECATED: socket_mode=False (TCP ports) is deprecated and should NEVER be activated.
    # Port mode has a hard capacity limit of 200 agents per instance and is being phased out.
    # All new deployments MUST use socket_mode=True.
    socket_mode: bool = Field(
        True,
        description="MUST be True. Use Unix domain sockets instead of TCP ports. "
                    "socket_mode=False (ports) is DEPRECATED and should never be used."
    )

    # DEPRECATED: Port allocation from PAC (database-backed) - used when socket_mode=False
    # WARNING: Port mode is deprecated and should never be activated.
    # This field exists only for backwards compatibility during migration.
    # All agents should use socket_mode=True with Unix sockets instead.
    # TODO: Remove this field once all agents are migrated to socket mode.
    ports: Optional[Ports] = Field(
        None,
        description="DEPRECATED: Port allocation from PAC. Use socket_mode=True instead."
    )

    # Idempotency control
    allow_update: bool = Field(True, description="If true, update agent if already exists with different deployment_id")

    @model_validator(mode='after')
    def validate_socket_mode_ports(self) -> 'DeployRequest':
        """Validate socket_mode and ports are consistent."""
        if self.socket_mode and self.ports is not None:
            raise ValueError(
                "socket_mode=True requires ports=None. "
                "Cannot specify both socket_mode and ports."
            )
        return self

    # Optional configuration
    max_package_size_mb: int = Field(100, description="Maximum package size in MB")
    boot_budget_ms: int = Field(120000, description="Boot time budget in milliseconds (2 minutes)")
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
    cleanup_user: bool = Field(False, description="Delete Linux user after stopping agent (default: False, user is preserved for fast redeployment)")


class AgentProcess(BaseModel):
    """Information about a running agent process."""
    agent_app_id: str
    deployment_id: str
    status: AgentStatus

    # Socket mode flag - mirrors DeployRequest.socket_mode
    # IMPORTANT: socket_mode=True (Unix sockets) is the ONLY supported mode.
    # DEPRECATED: socket_mode=False (TCP ports) is deprecated and should NEVER be activated.
    socket_mode: bool = Field(False, description="Should always be True. socket_mode=False is DEPRECATED.")

    # DEPRECATED: Port allocation (used when socket_mode=False)
    # WARNING: Port mode is deprecated and should never be activated.
    # TODO: Remove this field once all agents are migrated to socket mode.
    ports: Optional[Ports] = Field(None, description="DEPRECATED: Port allocation (None when socket_mode=True)")

    # Socket paths (used when socket_mode=True) - THIS IS THE PREFERRED MODE
    socket_paths: Optional[SocketPathsModel] = Field(
        None,
        description="Socket paths - required for socket_mode=True (the only supported mode)"
    )

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

    @model_validator(mode='after')
    def validate_ports_or_sockets(self) -> 'AgentProcess':
        """Validate that agent has either ports or socket_paths based on socket_mode."""
        if self.socket_mode:
            if self.ports is not None:
                raise ValueError("socket_mode=True but ports is set")
            if self.socket_paths is None:
                raise ValueError("socket_mode=True requires socket_paths")
        else:
            if self.socket_paths is not None:
                raise ValueError("socket_mode=False but socket_paths is set")
            if self.ports is None:
                raise ValueError("socket_mode=False requires ports")
        return self

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class DeployResponse(BaseModel):
    """Response after deploying an agent."""
    agent_app_id: str
    deployment_id: str
    status: str  # String value of AgentStatus enum
    # IMPORTANT: socket_mode=True should always be used. Port mode is DEPRECATED.
    socket_mode: bool = Field(False, description="Should always be True. socket_mode=False is DEPRECATED.")
    # DEPRECATED: ports field - port mode should never be activated
    ports: Optional[Ports] = Field(None, description="DEPRECATED: Port allocation (None when socket_mode=True)")
    socket_paths: Optional[SocketPathsModel] = Field(None, description="Socket paths (required for socket_mode=True)")
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
    # IMPORTANT: socket_mode=True should always be used. Port mode is DEPRECATED.
    socket_mode: bool = Field(False, description="Should always be True. socket_mode=False is DEPRECATED.")
    # DEPRECATED: ports field - port mode should never be activated
    ports: Optional[Ports] = Field(None, description="DEPRECATED: Port allocation (None when socket_mode=True)")
    socket_paths: Optional[SocketPathsModel] = Field(None, description="Socket paths (required for socket_mode=True)")
    health: Dict[str, bool] = Field(default_factory=dict, description="Health status per service type")

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
