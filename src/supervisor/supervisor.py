"""Main PAR Supervisor implementation."""

import asyncio
import logging
import signal
from datetime import datetime, timezone
from typing import Dict, Optional, List
from fastapi import FastAPI, Request, Response, HTTPException
from contextlib import asynccontextmanager

from .process_manager import ProcessManager
from .router import Router
from .models import ProcessConfig, PARProcess

logger = logging.getLogger(__name__)


class Supervisor:
    """PAR Supervisor - manages multiple PAR processes and routes requests."""
    
    def __init__(self, config: Optional[Dict] = None, **kwargs):
        """Initialize supervisor.
        
        Accepts either a config dict or keyword args like base_port for
        compatibility with various tests.
        """
        self.config = config or {}
        if "base_port" in kwargs:
            self.config["base_port"] = kwargs["base_port"]
        self.process_manager = ProcessManager(
            base_port=self.config.get("base_port", 8001)
        )
        self.router = Router()
        self.app = self._create_app()
        self._shutdown_event = asyncio.Event()
        
    def _create_app(self) -> FastAPI:
        """Create FastAPI application for supervisor."""
        
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            # Startup
            await self.start()
            yield
            # Shutdown
            await self.stop()
            
        app = FastAPI(
            title="PAR Supervisor",
            description="Process manager and router for Multi-PAR architecture",
            version="1.0.0",
            lifespan=lifespan
        )
        
        # Supervisor management endpoints
        @app.get("/supervisor/status")
        async def get_status():
            """Get supervisor and process status."""
            return {
                "supervisor": "running",
                "processes": self.process_manager.get_process_status(),
                "routes": list(self.router._route_cache.keys())
            }
            
        @app.post("/supervisor/spawn")
        async def spawn_process(config: ProcessConfig):
            """Spawn a new PAR process."""
            try:
                process = await self.process_manager.spawn_process(config)
                self._update_routes()
                return {
                    "status": "success",
                    "process_id": process.process_id,
                    "port": process.port
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
                
        @app.post("/supervisor/stop/{process_id}")
        async def stop_process(process_id: str):
            """Stop a PAR process."""
            try:
                await self.process_manager.stop_process(process_id)
                self._update_routes()
                return {"status": "success"}
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
                
        @app.post("/supervisor/restart/{process_id}")
        async def restart_process(process_id: str, config: ProcessConfig):
            """Restart a PAR process."""
            try:
                process = await self.process_manager.restart_process(process_id, config)
                self._update_routes()
                return {
                    "status": "success",
                    "process_id": process.process_id,
                    "port": process.port
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
                
        @app.get("/supervisor/health")
        async def supervisor_health():
            """Supervisor health check."""
            agent_health = await self.router.broadcast_health_check()
            healthy_count = sum(1 for h in agent_health.values() if h["status"] == "healthy")
            return {
                "status": "healthy",
                "total_agents": len(agent_health),
                "healthy_agents": healthy_count,
                "agent_health": agent_health
            }
            
        @app.get("/runtime/health")
        async def runtime_health():
            """Runtime health check for ALB."""
            return {
                "status": "healthy",
                "version": "0.1.0",
                "mode": "supervisor",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
        @app.get("/supervisor/logs")
        async def get_logs(
            process_id: Optional[str] = None,
            level: Optional[str] = None,
            limit: int = 100
        ):
            """Get aggregated logs from processes."""
            logs = self.process_manager.log_aggregator.get_logs(
                process_id=process_id,
                level=level,
                limit=limit
            )
            return {
                "logs": [log.to_dict() for log in logs],
                "count": len(logs)
            }
            
        @app.delete("/supervisor/logs")
        async def clear_logs(process_id: Optional[str] = None):
            """Clear logs for a process or all processes."""
            self.process_manager.log_aggregator.clear_logs(process_id)
            return {"status": "success", "message": f"Logs cleared for {process_id or 'all processes'}"}
            
        # Agent routing - catch all for /agents/{agent_id}/*
        @app.api_route("/agents/{agent_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
        async def route_to_agent(agent_id: str, path: str, request: Request):
            """Route request to appropriate PAR process."""
            return await self.router.route_request(agent_id, f"/{path}", request)
            
        return app
        
    async def start(self):
        """Start the supervisor."""
        logger.info("Starting PAR Supervisor")
        
        # Set up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal)
            
        # Start process manager
        await self.process_manager.start()
        
        # Load initial configuration and spawn processes if configured
        await self._load_initial_config()
        
    async def stop(self):
        """Stop the supervisor and all processes."""
        logger.info("Stopping PAR Supervisor")
        
        # Stop router
        await self.router.close()
        
        # Stop process manager
        await self.process_manager.stop()
        
        # Signal shutdown complete
        self._shutdown_event.set()
        
    def _handle_signal(self):
        """Handle shutdown signals."""
        logger.info("Received shutdown signal")
        asyncio.create_task(self.stop())
        
    def _update_routes(self):
        """Update routing table based on current processes."""
        self.router.update_routes(self.process_manager.processes)
        
    async def _load_initial_config(self):
        """Load initial configuration and spawn configured processes."""
        initial_agents = self.config.get("initial_agents", [])
        
        for agent_config in initial_agents:
            try:
                config = ProcessConfig(**agent_config)
                await self.process_manager.spawn_process(config)
            except Exception as e:
                logger.error(f"Failed to spawn initial agent {agent_config.get('agent_id')}: {e}")
                
        # Update routes after spawning all initial processes
        self._update_routes()
        
    async def wait_for_shutdown(self):
        """Wait for supervisor shutdown."""
        await self._shutdown_event.wait()

    # --- Convenience helpers expected by tests ---
    async def deploy_agent(self, agent_id: str, package_path: str) -> Dict:
        """Deploy a single agent APKG as a managed process.
        
        Returns basic process info including allocated HTTP port.
        """
        from pathlib import Path
        pkg_stem = Path(package_path).stem
        package_id = pkg_stem.replace("-", "@", 1) if "-" in pkg_stem else pkg_stem
        config = ProcessConfig(
            agent_id=agent_id,
            package_id=package_id,
            package_path=package_path,
            env_vars={}
        )
        process = await self.process_manager.spawn_process(config)
        self._update_routes()
        return {
            "agent_id": agent_id,
            "process_id": process.process_id,
            "port": process.port,
        }

    async def check_agent_health(self, agent_id: str) -> Dict:
        """Return agent health via HTTP /health forwarded through router."""
        return await self.router.health_check(agent_id)

    async def invoke_agent(self, agent_id: str, export: str, body: Dict) -> Dict:
        """Invoke an agent export via direct worker HTTP endpoint through router."""
        # Ensure route exists
        if agent_id not in self.router._route_cache:
            self._update_routes()
        target_url = self.router._route_cache.get(agent_id)
        if not target_url:
            raise RuntimeError(f"Agent {agent_id} not found")
        resp = await self.router.client.post(f"{target_url}/exports/{export}", json=body)
        return resp.json()

    async def shutdown(self):
        """Gracefully stop supervisor (compat shim)."""
        await self.stop()