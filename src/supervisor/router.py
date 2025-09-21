"""HTTP request routing to PAR processes."""

import httpx
import logging
from typing import Dict, Optional
from fastapi import Request, Response, HTTPException
from fastapi.responses import StreamingResponse

from .models import PARProcess

logger = logging.getLogger(__name__)


class Router:
    """Routes HTTP requests to appropriate PAR processes."""
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        self._route_cache: Dict[str, str] = {}  # agent_id -> process_url
        
    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()
        
    def update_routes(self, processes: Dict[str, PARProcess]):
        """Update routing table based on active processes."""
        self._route_cache.clear()
        for process_id, process in processes.items():
            if process.is_running:
                url = f"http://localhost:{process.port}"
                self._route_cache[process.agent_id] = url
                
    async def route_request(
        self,
        agent_id: str,
        path: str,
        request: Request
    ) -> Response:
        """Route a request to the appropriate PAR process."""
        
        # Find target URL
        target_url = self._route_cache.get(agent_id)
        if not target_url:
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
            
        # Build full URL
        full_url = f"{target_url}{path}"
        
        # Get request body
        body = await request.body()
        
        # Prepare headers (exclude host and content-length)
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)
        
        try:
            # Forward request
            response = await self.client.request(
                method=request.method,
                url=full_url,
                headers=headers,
                content=body,
                params=dict(request.query_params)
            )
            
            # Return response
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers)
            )
            
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="Request timeout")
        except httpx.ConnectError:
            raise HTTPException(status_code=503, detail=f"Agent {agent_id} unavailable")
        except Exception as e:
            logger.error(f"Error routing request to {agent_id}: {e}")
            raise HTTPException(status_code=500, detail="Internal routing error")
            
    async def health_check(self, agent_id: str) -> Dict:
        """Check health of a specific agent."""
        target_url = self._route_cache.get(agent_id)
        if not target_url:
            return {"status": "not_found", "agent_id": agent_id}
            
        try:
            response = await self.client.get(f"{target_url}/health")
            if response.status_code == 200:
                return {
                    "status": "healthy",
                    "agent_id": agent_id,
                    "details": response.json()
                }
            else:
                return {
                    "status": "unhealthy",
                    "agent_id": agent_id,
                    "status_code": response.status_code
                }
        except Exception as e:
            return {
                "status": "error",
                "agent_id": agent_id,
                "error": str(e)
            }
            
    async def broadcast_health_check(self) -> Dict[str, Dict]:
        """Check health of all agents."""
        results = {}
        for agent_id in self._route_cache:
            results[agent_id] = await self.health_check(agent_id)
        return results