"""Agent registry for managing known agents and their connection details.

Stores agent configurations in ~/.pixell/agents.json for easy access and selection.
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Optional


class AgentConfig:
    """Configuration for a single agent."""

    def __init__(
        self,
        id: str,
        name: str,
        description: str = "",
        host: str = "par.pixell.global",
        port: int = 443,
        shortname: str = ""
    ):
        self.id = id
        self.name = name
        self.description = description
        self.host = host
        self.port = port
        self.shortname = shortname or name.lower().replace(" ", "-")

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "host": self.host,
            "port": self.port
        }

    @staticmethod
    def from_dict(shortname: str, data: dict) -> "AgentConfig":
        """Create from dictionary."""
        return AgentConfig(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            host=data.get("host", "par.pixell.global"),
            port=data.get("port", 443),
            shortname=shortname
        )

    def __repr__(self) -> str:
        return f"AgentConfig(shortname='{self.shortname}', name='{self.name}', id='{self.id}')"


class AgentRegistry:
    """Registry for managing agent configurations."""

    def __init__(self, config_path: Optional[Path] = None):
        """Initialize the agent registry.

        Args:
            config_path: Path to config file (default: ~/.pixell/agents.json)
        """
        if config_path is None:
            config_path = Path.home() / ".pixell" / "agents.json"
        self.config_path = Path(config_path)
        self.agents: Dict[str, AgentConfig] = {}
        self.default_agent: Optional[str] = None

    def load(self) -> None:
        """Load agents from config file."""
        if not self.config_path.exists():
            self.initialize_default_config()
            return

        try:
            with open(self.config_path, "r") as f:
                data = json.load(f)

            self.agents = {}
            for shortname, agent_data in data.get("agents", {}).items():
                self.agents[shortname] = AgentConfig.from_dict(shortname, agent_data)

            self.default_agent = data.get("default")

        except Exception as e:
            print(f"⚠️  Error loading agent registry: {e}")
            print("   Creating default configuration...")
            self.initialize_default_config()

    def save(self) -> None:
        """Save agents to config file."""
        # Ensure directory exists
        self.config_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "agents": {
                shortname: agent.to_dict()
                for shortname, agent in self.agents.items()
            },
            "default": self.default_agent
        }

        with open(self.config_path, "w") as f:
            json.dump(data, f, indent=2)

    def initialize_default_config(self) -> None:
        """Initialize config file with default agents (core and vivid)."""
        self.agents = {
            "core": AgentConfig(
                id="ed8784f3-b602-481c-8701-3b6406c8fd98",
                name="PAF Core Agent",
                description="UPEE orchestrator with multi-agent coordination",
                host="par.pixell.global",
                port=443,
                shortname="core"
            ),
            "vivid": AgentConfig(
                id="4906eeb7-9959-414e-84c6-f2445822ebe4",
                name="Vivid Commenter",
                description="Code commenting agent",
                host="par.pixell.global",
                port=443,
                shortname="vivid"
            )
        }
        self.default_agent = "core"
        self.save()

    def get_agent(self, shortname: str) -> Optional[AgentConfig]:
        """Get agent by shortname.

        Args:
            shortname: Short identifier (e.g., "core", "vivid")

        Returns:
            AgentConfig if found, None otherwise
        """
        return self.agents.get(shortname)

    def get_agent_by_id(self, agent_id: str) -> Optional[AgentConfig]:
        """Get agent by ID.

        Args:
            agent_id: Agent UUID

        Returns:
            AgentConfig if found, None otherwise
        """
        for agent in self.agents.values():
            if agent.id == agent_id:
                return agent
        return None

    def list_agents(self) -> List[AgentConfig]:
        """List all registered agents.

        Returns:
            List of AgentConfig objects
        """
        return list(self.agents.values())

    def add_agent(
        self,
        shortname: str,
        agent_id: str,
        name: str,
        description: str = "",
        host: str = "par.pixell.global",
        port: int = 443
    ) -> AgentConfig:
        """Add or update an agent.

        Args:
            shortname: Short identifier (e.g., "myagent")
            agent_id: Agent UUID
            name: Display name
            description: Description
            host: Hostname
            port: Port number

        Returns:
            The created/updated AgentConfig
        """
        agent = AgentConfig(
            id=agent_id,
            name=name,
            description=description,
            host=host,
            port=port,
            shortname=shortname
        )
        self.agents[shortname] = agent
        self.save()
        return agent

    def remove_agent(self, shortname: str) -> bool:
        """Remove an agent from registry.

        Args:
            shortname: Short identifier

        Returns:
            True if removed, False if not found
        """
        if shortname in self.agents:
            del self.agents[shortname]
            if self.default_agent == shortname:
                self.default_agent = None
            self.save()
            return True
        return False

    def set_default(self, shortname: str) -> bool:
        """Set default agent.

        Args:
            shortname: Short identifier

        Returns:
            True if set, False if agent not found
        """
        if shortname in self.agents:
            self.default_agent = shortname
            self.save()
            return True
        return False

    def get_default(self) -> Optional[AgentConfig]:
        """Get default agent.

        Returns:
            Default AgentConfig if set, None otherwise
        """
        if self.default_agent:
            return self.get_agent(self.default_agent)
        return None


# Singleton instance
_registry: Optional[AgentRegistry] = None


def get_registry(config_path: Optional[Path] = None) -> AgentRegistry:
    """Get the global agent registry instance.

    Args:
        config_path: Optional config path (only used on first call)

    Returns:
        The global AgentRegistry instance
    """
    global _registry
    if _registry is None:
        _registry = AgentRegistry(config_path)
        _registry.load()
    return _registry
