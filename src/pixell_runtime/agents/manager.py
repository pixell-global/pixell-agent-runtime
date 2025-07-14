"""Agent manager for handling loaded agents."""

import importlib
import sys
from pathlib import Path
from typing import Dict, List, Optional

import structlog

from pixell_runtime.agents.loader import PackageLoader
from pixell_runtime.core.exceptions import AgentNotFoundError, PackageError
from pixell_runtime.core.models import Agent, AgentPackage, AgentStatus, InvocationRequest, InvocationResponse

logger = structlog.get_logger()


class AgentManager:
    """Manages loaded agents and their invocations."""
    
    def __init__(self, packages_dir: Path):
        """Initialize agent manager.
        
        Args:
            packages_dir: Directory for package storage
        """
        self.packages_dir = packages_dir
        self.loader = PackageLoader(packages_dir)
        self.packages: Dict[str, AgentPackage] = {}
        self.agents: Dict[str, Agent] = {}
    
    async def load_package(self, apkg_path: Path) -> AgentPackage:
        """Load an APKG package and mount its agents.
        
        Args:
            apkg_path: Path to APKG file
            
        Returns:
            Loaded package
        """
        # Load package
        package = self.loader.load_package(apkg_path)
        self.packages[package.id] = package
        
        # Mount agents from package
        await self._mount_agents(package)
        
        package.status = AgentStatus.READY
        return package
    
    async def _mount_agents(self, package: AgentPackage):
        """Mount agents from a package."""
        for export in package.manifest.exports:
            agent_id = f"{package.id}/{export.id}"
            
            try:
                # Parse handler path
                if ":" in export.handler:
                    module_path, func_name = export.handler.split(":", 1)
                else:
                    module_path = export.handler
                    func_name = "handler"
                
                # Special handling for Python agent
                if package.manifest.name == "pixell-python-agent":
                    # Use our direct executor adapter
                    from pixell_runtime.agents.adapters import code_executor, stream_executor, data_analyzer, ml_runner
                    
                    handler_map = {
                        "code-executor": code_executor.execute,
                        "stream-executor": stream_executor.execute,
                        "data-analyzer": data_analyzer.execute,
                        "ml-runner": ml_runner.execute
                    }
                    handler = handler_map.get(export.id, code_executor.execute)
                else:
                    # Import module normally
                    logger.info("Importing agent module", module=module_path, func=func_name)
                    module = importlib.import_module(module_path)
                    
                    # Get handler function
                    if hasattr(module, func_name):
                        handler = getattr(module, func_name)
                    elif hasattr(module, "main"):
                        # Fallback to main function
                        handler = getattr(module, "main")
                    else:
                        # Try to get the module itself as handler
                        handler = module
                
                # Create agent instance
                agent = Agent(
                    id=agent_id,
                    package_id=package.id,
                    export=export,
                    handler=handler,
                    status=AgentStatus.READY
                )
                
                self.agents[agent_id] = agent
                logger.info("Agent mounted successfully", agent_id=agent_id)
                
            except Exception as e:
                logger.error("Failed to mount agent", agent_id=agent_id, error=str(e))
                agent = Agent(
                    id=agent_id,
                    package_id=package.id,
                    export=export,
                    status=AgentStatus.ERROR
                )
                self.agents[agent_id] = agent
    
    async def invoke_agent(self, request: InvocationRequest) -> InvocationResponse:
        """Invoke an agent.
        
        Args:
            request: Invocation request
            
        Returns:
            Invocation response
            
        Raises:
            AgentNotFoundError: If agent not found
        """
        agent = self.agents.get(request.agent_id)
        if not agent:
            raise AgentNotFoundError(f"Agent not found: {request.agent_id}")
        
        if agent.status != AgentStatus.READY:
            raise PackageError(f"Agent not ready: {agent.status}")
        
        # For the Python agent, we need to handle it specially
        # since it's designed to work with gRPC
        import time
        start_time = time.time()
        
        try:
            # Check if handler is async
            import inspect
            
            if inspect.iscoroutinefunction(agent.handler):
                output = await agent.handler(request.input)
            elif hasattr(agent.handler, "__call__"):
                output = agent.handler(request.input)
            else:
                # Try to find an invoke method
                if hasattr(agent.handler, "invoke"):
                    if inspect.iscoroutinefunction(agent.handler.invoke):
                        output = await agent.handler.invoke(request.input)
                    else:
                        output = agent.handler.invoke(request.input)
                elif hasattr(agent.handler, "main"):
                    if inspect.iscoroutinefunction(agent.handler.main):
                        output = await agent.handler.main(request.input)
                    else:
                        output = agent.handler.main(request.input)
                else:
                    output = {"status": "error", "message": "No suitable handler found"}
            
            duration_ms = (time.time() - start_time) * 1000
            
            return InvocationResponse(
                agent_id=request.agent_id,
                output=output,
                duration_ms=duration_ms,
                trace_id=request.trace_id
            )
            
        except Exception as e:
            logger.error("Agent invocation failed", agent_id=request.agent_id, error=str(e))
            duration_ms = (time.time() - start_time) * 1000
            
            return InvocationResponse(
                agent_id=request.agent_id,
                output={"error": str(e)},
                duration_ms=duration_ms,
                trace_id=request.trace_id
            )
    
    def list_agents(self) -> List[Agent]:
        """List all loaded agents."""
        return list(self.agents.values())
    
    def get_agent(self, agent_id: str) -> Optional[Agent]:
        """Get agent by ID."""
        return self.agents.get(agent_id)