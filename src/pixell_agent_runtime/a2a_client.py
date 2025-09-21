"""A2A (Agent-to-Agent) client for invoking other agents."""

import grpc
import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class A2AClient:
    """Client for making A2A (Agent-to-Agent) calls."""
    
    def __init__(self, supervisor_url: str = "http://localhost:8000"):
        """Initialize A2A client.
        
        Args:
            supervisor_url: URL of the supervisor for routing
        """
        self.supervisor_url = supervisor_url.rstrip('/')
        
    async def call(self, agent_id: str, method: str, params: Dict[str, Any]) -> Any:
        """Call another agent via A2A protocol.
        
        Args:
            agent_id: Target agent ID
            method: Method/export to invoke
            params: Parameters to pass
            
        Returns:
            Result from the agent
        """
        # For now, use HTTP routing through supervisor
        # In future, this could use gRPC directly
        
        import httpx
        
        url = f"{self.supervisor_url}/agents/{agent_id}/exports/{method}"
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=params)
            
            if response.status_code == 200:
                return response.json()
            else:
                raise Exception(f"A2A call failed: {response.status_code} - {response.text}")
                
    async def call_grpc(self, agent_id: str, service: str, method: str, message: Any) -> Any:
        """Call another agent via gRPC (for agents that support it).
        
        Args:
            agent_id: Target agent ID
            service: gRPC service name
            method: gRPC method name
            message: Protobuf message
            
        Returns:
            Response message
        """
        # This would need the actual protobuf definitions
        # For now, we'll route through HTTP
        params = {
            "service": service,
            "method": method,
            "message": message  # Would need to serialize protobuf
        }
        
        return await self.call(agent_id, "_grpc", params)
        

# Global A2A client instance
_a2a_client = None


def get_a2a_client() -> A2AClient:
    """Get the global A2A client instance."""
    global _a2a_client
    if _a2a_client is None:
        # Get supervisor URL from environment or use default
        import os
        supervisor_url = os.environ.get("PAR_SUPERVISOR_URL", "http://localhost:8000")
        _a2a_client = A2AClient(supervisor_url)
    return _a2a_client