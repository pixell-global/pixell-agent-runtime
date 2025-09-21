"""Adapter for packages that export functions."""

import inspect
from typing import Any, Dict, Callable

import structlog

from pixell_runtime.core.models import AgentPackage

logger = structlog.get_logger()


class ExportsAdapter:
    """Adapter for packages that export callable functions."""
    
    def __init__(self, package: AgentPackage, exports: Dict[str, Callable]):
        self.package = package
        self.exports = exports
        
    async def initialize(self):
        """Initialize the adapter."""
        logger.info(f"Exports adapter initialized for {self.package.id} with exports: {list(self.exports.keys())}")
        
    async def invoke(self, export_name: str, params: Dict[str, Any]) -> Any:
        """Invoke an export."""
        if export_name not in self.exports:
            # Try default export
            if "invoke" in self.exports:
                export_name = "invoke"
            else:
                raise ValueError(f"Export '{export_name}' not found")
                
        handler = self.exports[export_name]
        
        # Check if it's async
        if inspect.iscoroutinefunction(handler):
            result = await handler(params)
        else:
            result = handler(params)
            
        return result
        
    async def cleanup(self):
        """Clean up resources."""
        pass