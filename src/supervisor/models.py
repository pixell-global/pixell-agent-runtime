"""Data models for the PAR Supervisor."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any


class ProcessState(Enum):
    """State of a PAR process."""
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"
    CRASHED = "crashed"


@dataclass
class ProcessConfig:
    """Configuration for a PAR process."""
    
    agent_id: str
    package_id: str
    package_path: str
    env_vars: Dict[str, str]
    memory_limit_mb: Optional[int] = None
    cpu_limit: Optional[float] = None  # CPU shares/cores
    restart_policy: str = "on-failure"  # "always", "on-failure", "never"
    max_restarts: int = 3
    restart_delay_seconds: int = 5
    backoff_multiplier: float = 2.0  # Exponential backoff
    max_restart_delay_seconds: int = 300  # 5 minutes max


@dataclass
class PARProcess:
    """Represents a PAR process instance."""
    
    process_id: str
    agent_id: str
    package_id: str
    port: int
    state: ProcessState
    pid: Optional[int] = None
    started_at: Optional[datetime] = None
    stopped_at: Optional[datetime] = None
    error_message: Optional[str] = None
    restart_count: int = 0
    last_restart_at: Optional[datetime] = None
    exit_code: Optional[int] = None
    config: Optional[ProcessConfig] = None
    
    @property
    def is_running(self) -> bool:
        """Check if process is in running state."""
        return self.state == ProcessState.RUNNING
    
    @property
    def uptime(self) -> Optional[float]:
        """Get process uptime in seconds."""
        if self.started_at and self.is_running:
            return (datetime.utcnow() - self.started_at).total_seconds()
        return None


@dataclass
class PortAllocation:
    """Port allocation for PAR processes."""
    
    start_port: int = 8001
    end_port: int = 8100
    allocated_ports: Dict[int, str] = None  # port -> process_id
    
    def __post_init__(self):
        if self.allocated_ports is None:
            self.allocated_ports = {}
    
    def allocate_port(self, process_id: str) -> Optional[int]:
        """Allocate a free port for a process."""
        for port in range(self.start_port, self.end_port + 1):
            if port not in self.allocated_ports:
                self.allocated_ports[port] = process_id
                return port
        return None
    
    def release_port(self, port: int) -> None:
        """Release an allocated port."""
        if port in self.allocated_ports:
            del self.allocated_ports[port]
    
    def get_process_port(self, process_id: str) -> Optional[int]:
        """Get port allocated to a process."""
        for port, pid in self.allocated_ports.items():
            if pid == process_id:
                return port
        return None