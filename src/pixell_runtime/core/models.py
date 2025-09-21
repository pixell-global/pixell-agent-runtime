"""Core data models for Pixell Runtime."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, HttpUrl, validator


class AgentStatus(str, Enum):
    """Agent status enum."""
    
    PENDING = "pending"
    LOADING = "loading"
    READY = "ready"
    ERROR = "error"
    DISABLED = "disabled"


class AgentExport(BaseModel):
    """Represents an exported agent from a package."""
    
    id: str = Field(..., description="Unique identifier for the agent")
    name: str = Field(..., description="Human-readable name")
    description: Optional[str] = Field(None, description="Agent description")
    version: str = Field(..., description="Semantic version")
    handler: str = Field(..., description="Handler function path")
    private: bool = Field(False, description="Whether agent is private (not routable)")
    role_required: Optional[str] = Field(None, description="Required role for access")
    input_schema: Optional[Dict[str, Any]] = Field(None, description="JSON Schema for input validation")
    output_schema: Optional[Dict[str, Any]] = Field(None, description="JSON Schema for output")


class A2AConfig(BaseModel):
    """A2A (gRPC) configuration."""
    
    service: Optional[str] = Field(None, description="gRPC server entry (exports createGrpcServer())")


class RESTConfig(BaseModel):
    """REST API configuration."""
    
    entry: Optional[str] = Field(None, description="REST entry point (exports mount(app) to attach routes)")


class UIConfig(BaseModel):
    """UI serving configuration."""
    
    path: Optional[str] = Field(None, description="Folder with built static assets (index.html at least)")
    basePath: str = Field("/", description="Optional mount path")


class AgentManifest(BaseModel):
    """Agent package manifest (agent.yaml)."""
    
    name: str = Field(..., description="Package name")
    version: str = Field(..., description="Package version")
    runtime_version: str = Field(..., description="Required runtime version")
    description: Optional[str] = Field(None, description="Package description")
    author: Optional[str] = Field(None, description="Package author")
    exports: List[AgentExport] = Field(..., description="Exported agents")
    dependencies: Optional[List[str]] = Field(default_factory=list, description="Python dependencies")
    entrypoint: Optional[str] = Field(None, description="Main entrypoint (module:function)")
    main_module: Optional[str] = Field(None, description="Main module name")
    
    # Three-surface runtime configuration
    a2a: Optional[A2AConfig] = Field(None, description="A2A (gRPC) configuration")
    rest: Optional[RESTConfig] = Field(None, description="REST API configuration")
    ui: Optional[UIConfig] = Field(None, description="UI serving configuration")
    
    @validator("runtime_version")
    def validate_runtime_version(cls, v: str) -> str:
        """Validate runtime version format."""
        # Simple semver check
        parts = v.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError(f"Invalid runtime version format: {v}")
        return v


class AgentPackage(BaseModel):
    """Represents a loaded agent package."""
    
    id: str = Field(..., description="Package ID (name@version)")
    manifest: AgentManifest = Field(..., description="Package manifest")
    path: str = Field(..., description="Local filesystem path")
    url: HttpUrl = Field(..., description="Source URL")
    sha256: str = Field(..., description="Package SHA256 hash")
    signature: Optional[str] = Field(None, description="GPG signature")
    loaded_at: datetime = Field(default_factory=datetime.utcnow)
    status: AgentStatus = Field(AgentStatus.PENDING)
    error: Optional[str] = Field(None, description="Error message if failed")


class Agent(BaseModel):
    """Represents a mounted agent instance."""
    
    id: str = Field(..., description="Unique agent ID")
    package_id: str = Field(..., description="Parent package ID")
    export: AgentExport = Field(..., description="Export definition")
    handler: Optional[Any] = Field(None, description="Loaded handler function")
    status: AgentStatus = Field(AgentStatus.PENDING)
    invocation_count: int = Field(0, description="Number of invocations")
    last_invoked: Optional[datetime] = Field(None)
    
    class Config:
        arbitrary_types_allowed = True


class InvocationRequest(BaseModel):
    """Agent invocation request."""
    
    agent_id: str = Field(..., description="Target agent ID")
    input: Dict[str, Any] = Field(..., description="Input data")
    context: Optional[Dict[str, Any]] = Field(None, description="Invocation context")
    trace_id: Optional[str] = Field(None, description="Request trace ID")


class InvocationResponse(BaseModel):
    """Agent invocation response."""
    
    agent_id: str = Field(..., description="Agent ID")
    output: Any = Field(..., description="Agent output")
    duration_ms: float = Field(..., description="Execution duration in milliseconds")
    trace_id: Optional[str] = Field(None)
    metadata: Optional[Dict[str, Any]] = Field(None)


class RuntimeInfo(BaseModel):
    """Runtime information."""
    
    version: str = Field(..., description="Runtime version")
    start_time: datetime = Field(..., description="Runtime start time")
    packages_loaded: int = Field(0, description="Number of loaded packages")
    agents_mounted: int = Field(0, description="Number of mounted agents")
    total_invocations: int = Field(0, description="Total invocation count")
    status: str = Field("healthy", description="Runtime status")