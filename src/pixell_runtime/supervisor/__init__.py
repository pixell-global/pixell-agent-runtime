"""Supervisor module for managing multiple agents on EC2.

The supervisor is responsible for:
- Managing Linux users for agent isolation
- Allocating ports for REST/A2A/UI services
- Downloading and caching agent packages
- Spawning and managing agent processes
- Providing HTTP API for deployment management

This module enables running N agents per EC2 instance with proper isolation.
"""

from pixell_runtime.supervisor.models import (
    DeployRequest,
    UpdateRequest,
    DeleteRequest,
    DeployResponse,
    AgentStatusResponse,
    AgentStatus,
    Ports,
    AgentProcess,
)

__all__ = [
    "DeployRequest",
    "UpdateRequest",
    "DeleteRequest",
    "DeployResponse",
    "AgentStatusResponse",
    "AgentStatus",
    "Ports",
    "AgentProcess",
]
