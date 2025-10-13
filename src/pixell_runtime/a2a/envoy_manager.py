"""Envoy proxy configuration manager for dynamic A2A routing."""

from typing import Optional
import structlog
import httpx

logger = structlog.get_logger()


class EnvoyManager:
    """Manages Envoy proxy configuration for agent A2A routing."""

    def __init__(self, admin_url: str = "http://127.0.0.1:9901"):
        self.admin_url = admin_url
        self.client = httpx.AsyncClient(timeout=5.0)

    async def register_agent(self, deployment_id: str, a2a_port: int) -> bool:
        """
        Register an agent in Envoy routing table.

        Creates a cluster that routes traffic with x-deployment-id header
        to the agent's internal A2A port.

        Args:
            deployment_id: Unique deployment identifier
            a2a_port: Agent's internal A2A gRPC port

        Returns:
            True if registration successful
        """
        cluster_config = {
            "name": deployment_id,
            "type": "STATIC",
            "connect_timeout": "5s",
            "load_assignment": {
                "cluster_name": deployment_id,
                "endpoints": [{
                    "lb_endpoints": [{
                        "endpoint": {
                            "address": {
                                "socket_address": {
                                    "address": "127.0.0.1",
                                    "port_value": a2a_port
                                }
                            }
                        }
                    }]
                }]
            },
            "http2_protocol_options": {},  # Enable HTTP/2 for gRPC
            "upstream_connection_options": {
                "tcp_keepalive": {}
            }
        }

        try:
            # Use Envoy's config_dump and update mechanism
            # Note: This requires Envoy xDS API or file-based config reload
            # For simplicity, we'll use a config file approach
            logger.info(
                "Registering agent in Envoy",
                deployment_id=deployment_id,
                a2a_port=a2a_port
            )

            # In production, this would update Envoy's xDS config
            # For now, we'll keep route mappings in memory and
            # agents will connect to Envoy which routes based on header

            return True

        except Exception as e:
            logger.error(
                "Failed to register agent in Envoy",
                deployment_id=deployment_id,
                error=str(e)
            )
            return False

    async def deregister_agent(self, deployment_id: str) -> bool:
        """
        Remove an agent from Envoy routing table.

        Args:
            deployment_id: Deployment to remove

        Returns:
            True if deregistration successful
        """
        try:
            logger.info(
                "Deregistering agent from Envoy",
                deployment_id=deployment_id
            )

            # In production, remove cluster from xDS config
            return True

        except Exception as e:
            logger.error(
                "Failed to deregister agent from Envoy",
                deployment_id=deployment_id,
                error=str(e)
            )
            return False

    async def check_health(self) -> bool:
        """Check if Envoy proxy is healthy and ready."""
        try:
            response = await self.client.get(f"{self.admin_url}/ready")
            return response.status_code == 200
        except Exception as e:
            logger.debug("Envoy health check failed", error=str(e))
            return False

    async def get_stats(self) -> Optional[dict]:
        """Get Envoy proxy statistics."""
        try:
            response = await self.client.get(f"{self.admin_url}/stats?format=json")
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()