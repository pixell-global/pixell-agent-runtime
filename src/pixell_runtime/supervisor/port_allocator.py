"""Port allocation for agent services."""

import structlog
from typing import Optional
from pixell_runtime.supervisor.models import Ports

logger = structlog.get_logger()


class PortAllocator:
    """Allocates ports for agent REST/A2A/UI services.

    Port ranges:
    - REST: 8081-8100 (20 agents max)
    - A2A:  50052-50071 (20 agents max)
    - UI:   3001-3020 (20 agents max)

    Note: Port 8080 (REST), 50051 (A2A), 3000 (UI) are reserved for supervisor itself.
    """

    # Port ranges
    REST_PORT_START = 8081
    REST_PORT_END = 8100
    A2A_PORT_START = 50052
    A2A_PORT_END = 50071
    UI_PORT_START = 3001
    UI_PORT_END = 3020

    def __init__(self):
        """Initialize port allocator with empty allocation tracking."""
        # Track allocated ports: agent_app_id -> Ports
        self.allocations: dict[str, Ports] = {}
        logger.info("PortAllocator initialized", max_agents=self.max_agents())

    def max_agents(self) -> int:
        """Get maximum number of agents that can be allocated.

        Returns:
            Maximum number of agents (20)
        """
        return min(
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
        """Allocate ports for an agent.

        Args:
            agent_app_id: Agent identifier
            reuse: If True and agent already has allocation, return existing ports

        Returns:
            Allocated Ports object

        Raises:
            RuntimeError: If no ports available or allocation fails
        """
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
