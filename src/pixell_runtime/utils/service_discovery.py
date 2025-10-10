"""AWS Cloud Map service discovery integration."""

import os
from typing import Optional

import boto3
import structlog

logger = structlog.get_logger()


class ServiceDiscoveryClient:
    """Client for AWS Cloud Map service discovery operations."""

    def __init__(self):
        self.client = boto3.client('servicediscovery')
        self.namespace_name = os.getenv('SERVICE_DISCOVERY_NAMESPACE', 'pixell-runtime.local')
        self.service_name = os.getenv('SERVICE_DISCOVERY_SERVICE', 'agents')
        self._namespace_id: Optional[str] = None
        self._service_id: Optional[str] = None

    def _get_namespace_id(self) -> Optional[str]:
        """Get namespace ID by name."""
        if self._namespace_id:
            return self._namespace_id

        try:
            response = self.client.list_namespaces()
            for ns in response.get('Namespaces', []):
                if ns['Name'] == self.namespace_name:
                    self._namespace_id = ns['Id']
                    return self._namespace_id
            logger.warning("Service discovery namespace not found", namespace=self.namespace_name)
            return None
        except Exception as e:
            logger.error("Failed to get namespace ID", error=str(e))
            return None

    def _get_service_id(self) -> Optional[str]:
        """Get service ID by name."""
        if self._service_id:
            return self._service_id

        namespace_id = self._get_namespace_id()
        if not namespace_id:
            return None

        try:
            response = self.client.list_services(
                Filters=[
                    {'Name': 'NAMESPACE_ID', 'Values': [namespace_id], 'Condition': 'EQ'}
                ]
            )
            for svc in response.get('Services', []):
                if svc['Name'] == self.service_name:
                    self._service_id = svc['Id']
                    return self._service_id
            logger.warning("Service discovery service not found", service=self.service_name)
            return None
        except Exception as e:
            logger.error("Failed to get service ID", error=str(e))
            return None

    def register_instance(
        self,
        instance_id: str,
        ipv4: str,
        port: int,
        attributes: Optional[dict] = None
    ) -> bool:
        """Register an instance with service discovery.

        Args:
            instance_id: Unique identifier for the instance (e.g., deployment_id)
            ipv4: IPv4 address of the instance
            port: Port number for A2A gRPC
            attributes: Additional custom attributes

        Returns:
            True if registration successful, False otherwise
        """
        service_id = self._get_service_id()
        if not service_id:
            logger.error("Cannot register instance: service discovery not configured")
            return False

        try:
            instance_attributes = {
                'AWS_INSTANCE_IPV4': ipv4,
                'AWS_INSTANCE_PORT': str(port),
            }

            if attributes:
                instance_attributes.update(attributes)

            self.client.register_instance(
                ServiceId=service_id,
                InstanceId=instance_id,
                Attributes=instance_attributes
            )

            logger.info(
                "Registered instance in service discovery",
                instance_id=instance_id,
                ipv4=ipv4,
                port=port,
                dns=f"{instance_id}.{self.service_name}.{self.namespace_name}"
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to register instance in service discovery",
                instance_id=instance_id,
                error=str(e)
            )
            return False

    def deregister_instance(self, instance_id: str) -> bool:
        """Deregister an instance from service discovery.

        Args:
            instance_id: Unique identifier for the instance

        Returns:
            True if deregistration successful, False otherwise
        """
        service_id = self._get_service_id()
        if not service_id:
            logger.warning("Cannot deregister instance: service discovery not configured")
            return False

        try:
            self.client.deregister_instance(
                ServiceId=service_id,
                InstanceId=instance_id
            )

            logger.info("Deregistered instance from service discovery", instance_id=instance_id)
            return True

        except Exception as e:
            logger.error(
                "Failed to deregister instance from service discovery",
                instance_id=instance_id,
                error=str(e)
            )
            return False

    def update_instance_health(self, instance_id: str, healthy: bool) -> bool:
        """Update health status of an instance.

        Args:
            instance_id: Unique identifier for the instance
            healthy: True if healthy, False otherwise

        Returns:
            True if update successful, False otherwise
        """
        service_id = self._get_service_id()
        if not service_id:
            return False

        try:
            status = 'HEALTHY' if healthy else 'UNHEALTHY'
            self.client.update_instance_custom_health_status(
                ServiceId=service_id,
                InstanceId=instance_id,
                Status=status
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to update instance health",
                instance_id=instance_id,
                error=str(e)
            )
            return False

    def discover_agents(self, max_results: int = 10) -> list[dict]:
        """Discover healthy agent instances via Cloud Map.

        Args:
            max_results: Maximum number of instances to return

        Returns:
            List of dicts with keys: ipv4, port, instance_id, attributes
        """
        try:
            response = self.client.discover_instances(
                NamespaceName=self.namespace_name,
                ServiceName=self.service_name,
                MaxResults=max_results,
                HealthStatus='HEALTHY'
            )

            instances = []
            for inst in response.get('Instances', []):
                attrs = inst.get('Attributes', {})
                instances.append({
                    'ipv4': attrs.get('AWS_INSTANCE_IPV4'),
                    'port': int(attrs.get('AWS_INSTANCE_PORT', 50051)),
                    'instance_id': inst.get('InstanceId'),
                    'attributes': attrs
                })

            logger.info(
                "Discovered agent instances",
                count=len(instances),
                namespace=self.namespace_name,
                service=self.service_name
            )
            return instances

        except Exception as e:
            logger.error("Failed to discover instances", error=str(e))
            return []

    def discover_agent_by_id(self, deployment_id: str) -> Optional[dict]:
        """Discover specific agent instance by deployment ID.

        Args:
            deployment_id: Deployment/instance ID to find

        Returns:
            Dict with ipv4, port if found, None otherwise
        """
        agents = self.discover_agents(max_results=100)
        for agent in agents:
            if agent['instance_id'] == deployment_id:
                return agent
        return None


# Global singleton instance
_service_discovery_client: Optional[ServiceDiscoveryClient] = None


def get_service_discovery_client() -> Optional[ServiceDiscoveryClient]:
    """Get or create the global service discovery client."""
    global _service_discovery_client

    # Only initialize if service discovery env vars are set
    if not os.getenv('SERVICE_DISCOVERY_NAMESPACE'):
        return None

    if _service_discovery_client is None:
        try:
            _service_discovery_client = ServiceDiscoveryClient()
        except Exception as e:
            logger.warning("Failed to initialize service discovery client", error=str(e))
            return None

    return _service_discovery_client