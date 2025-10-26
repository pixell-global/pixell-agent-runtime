"""Port allocation for agent services.

⚠️  DEPRECATION NOTICE ⚠️
This module is LEGACY and should only be used for backward compatibility.

PAC (Pixell Agent Cloud) now manages port allocation centrally via database-backed allocation.
When deploying agents, PAC allocates ports BEFORE calling PAR and sends them in the deploy request.

PAR should:
1. Use ports provided by PAC in DeployRequest.ports (primary path)
2. Fall back to this allocator ONLY if PAC doesn't provide ports (legacy path)

Port lifecycle is managed by PAC:
- PAC allocates ports from database before deploy
- PAC releases ports in database when agent deleted
- PAR should NOT release ports (PAC owns the lifecycle)

See: /Users/syum/dev/pixell-agent-cloud/src/lib/ports/allocator.ts for PAC implementation.
"""

import os
import structlog
from typing import Optional
from pixell_runtime.supervisor.models import Ports

logger = structlog.get_logger()


class PortAllocator:
    """Allocates ports for agent REST/A2A/UI services (LEGACY - use PAC allocation).

    Port ranges (matching PAC's scheme):
    - A2A (gRPC):  60000-60199 (200 agents max)
    - REST API:    63000-63199 (200 agents max)
    - UI Server:   65000-65199 (200 agents max)

    Note: These ranges match PAC's database-backed allocation scheme.
    Port 50051 is reserved for the gRPC gateway.

    Environment Variables:
    - PAR_MAX_AGENTS: Maximum number of agents per instance (default: 200)
    - PAR_A2A_PORT_START: A2A port range start (default: 60000)
    - PAR_REST_PORT_START: REST port range start (default: 63000)
    - PAR_UI_PORT_START: UI port range start (default: 65000)
    """

    # Maximum agents per instance (configurable via environment)
    _MAX_AGENTS = int(os.getenv("PAR_MAX_AGENTS", "200"))

    # Port ranges (matching PAC's allocation scheme)
    # These are configurable via environment for testing/customization
    A2A_PORT_START = int(os.getenv("PAR_A2A_PORT_START", "60000"))
    A2A_PORT_END = A2A_PORT_START + _MAX_AGENTS - 1  # 60000-60199

    REST_PORT_START = int(os.getenv("PAR_REST_PORT_START", "63000"))
    REST_PORT_END = REST_PORT_START + _MAX_AGENTS - 1  # 63000-63199

    UI_PORT_START = int(os.getenv("PAR_UI_PORT_START", "65000"))
    UI_PORT_END = UI_PORT_START + _MAX_AGENTS - 1  # 65000-65199

    def __init__(self):
        """Initialize port allocator with empty allocation tracking.

        ⚠️  DEPRECATION WARNING: This allocator is LEGACY.
        PAC should provide ports in deploy requests instead.
        """
        # Track allocated ports: agent_app_id -> Ports
        self.allocations: dict[str, Ports] = {}

        logger.warning(
            "PortAllocator initialized (LEGACY MODE - PAC should provide ports)",
            max_agents=self.max_agents(),
            a2a_range=f"{self.A2A_PORT_START}-{self.A2A_PORT_END}",
            rest_range=f"{self.REST_PORT_START}-{self.REST_PORT_END}",
            ui_range=f"{self.UI_PORT_START}-{self.UI_PORT_END}",
            note="This allocator should only be used for backward compatibility"
        )

    def max_agents(self) -> int:
        """Get maximum number of agents that can be allocated.

        Returns:
            Maximum number of agents (default: 200, configurable via PAR_MAX_AGENTS)
        """
        # Verify all port ranges can accommodate max agents
        return min(
            self._MAX_AGENTS,
            self.REST_PORT_END - self.REST_PORT_START + 1,
            self.A2A_PORT_END - self.A2A_PORT_START + 1,
            self.UI_PORT_END - self.UI_PORT_START + 1,
        )

    def is_allocated(self, agent_app_id: str) -> bool:
        """Check if ports are allocated for an agent.

        Args:
            agent_app_id: Agent identifier

        Returns:
            True if ports are allocated, False otherwise
        """
        return agent_app_id in self.allocations

    def get_allocation(self, agent_app_id: str) -> Optional[Ports]:
        """Get allocated ports for an agent.

        Args:
            agent_app_id: Agent identifier

        Returns:
            Ports object if allocated, None otherwise
        """
        return self.allocations.get(agent_app_id)

    def allocate(self, agent_app_id: str, reuse: bool = True) -> Ports:
        """Allocate ports for an agent (LEGACY - PAC should provide ports).

        ⚠️  WARNING: This is a legacy fallback path.
        PAC should allocate ports in database and send them in DeployRequest.ports.
        This method should only be used for backward compatibility or testing.

        Args:
            agent_app_id: Agent identifier
            reuse: If True and agent already has allocation, return existing ports

        Returns:
            Allocated Ports object

        Raises:
            RuntimeError: If no ports available or allocation fails
        """
        logger.warning(
            "Using LEGACY port allocation - PAC should provide ports instead",
            agent_app_id=agent_app_id,
            note="Consider upgrading PAC to use centralized database-backed allocation"
        )

        # Check if already allocated
        if agent_app_id in self.allocations:
            if reuse:
                logger.info("Reusing existing port allocation", agent_app_id=agent_app_id)
                return self.allocations[agent_app_id]
            else:
                # Release old allocation first
                self.release(agent_app_id)

        # Find available ports
        allocated_rest_ports = {p.rest for p in self.allocations.values()}
        allocated_a2a_ports = {p.a2a for p in self.allocations.values()}
        allocated_ui_ports = {p.ui for p in self.allocations.values()}

        # Find first available port in each range
        rest_port = self._find_available_port(
            self.REST_PORT_START, self.REST_PORT_END, allocated_rest_ports
        )
        a2a_port = self._find_available_port(
            self.A2A_PORT_START, self.A2A_PORT_END, allocated_a2a_ports
        )
        ui_port = self._find_available_port(
            self.UI_PORT_START, self.UI_PORT_END, allocated_ui_ports
        )

        if rest_port is None or a2a_port is None or ui_port is None:
            error_msg = (
                f"No ports available for agent {agent_app_id}. "
                f"Maximum {self.max_agents()} agents can be deployed."
            )
            logger.error("Port allocation failed", agent_app_id=agent_app_id)
            raise RuntimeError(error_msg)

        # Create allocation
        ports = Ports(rest=rest_port, a2a=a2a_port, ui=ui_port)
        self.allocations[agent_app_id] = ports

        logger.info(
            "Allocated ports",
            agent_app_id=agent_app_id,
            rest=rest_port,
            a2a=a2a_port,
            ui=ui_port,
            total_allocated=len(self.allocations),
        )

        return ports

    def release(self, agent_app_id: str) -> bool:
        """Release allocated ports for an agent.

        Args:
            agent_app_id: Agent identifier

        Returns:
            True if ports were released, False if no allocation existed
        """
        if agent_app_id in self.allocations:
            ports = self.allocations.pop(agent_app_id)
            logger.info(
                "Released ports",
                agent_app_id=agent_app_id,
                rest=ports.rest,
                a2a=ports.a2a,
                ui=ports.ui,
                remaining_allocated=len(self.allocations),
            )
            return True
        else:
            logger.debug("No ports to release", agent_app_id=agent_app_id)
            return False

    def available_slots(self) -> int:
        """Get number of available agent slots.

        Returns:
            Number of agents that can still be deployed
        """
        return self.max_agents() - len(self.allocations)

    def get_all_allocations(self) -> dict[str, Ports]:
        """Get all current port allocations.

        Returns:
            Dictionary mapping agent_app_id to Ports
        """
        return self.allocations.copy()

    def _find_available_port(
        self, start: int, end: int, allocated: set[int]
    ) -> Optional[int]:
        """Find first available port in range.

        Args:
            start: Start of port range (inclusive)
            end: End of port range (inclusive)
            allocated: Set of already allocated ports

        Returns:
            First available port, or None if none available
        """
        for port in range(start, end + 1):
            if port not in allocated:
                return port
        return None
