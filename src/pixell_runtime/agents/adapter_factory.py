"""Factory for creating agent adapters."""

import importlib
import sys
import json
from pathlib import Path
from typing import Any, Dict, Optional

import structlog

from pixell_runtime.core.models import AgentPackage
from pixell_runtime.agents.adapters.exports_adapter import ExportsAdapter

logger = structlog.get_logger()


async def create_adapter(package: AgentPackage):
    """Create an adapter for the given package.
    
    Args:
        package: The agent package
        
    Returns:
        An initialized adapter
    """
    # Load the main module
    src_path = Path(package.path) / "src"
    if src_path.exists():
        sys.path.insert(0, str(src_path))
        
    # Also add the a2a directory for protobuf imports
    a2a_path = src_path / "a2a"
    if a2a_path.exists():
        sys.path.insert(0, str(a2a_path))
    
    try:
        # Check for entrypoint first (Python agent style)
        if hasattr(package.manifest, 'entrypoint') and package.manifest.entrypoint:
            module_name, func_name = package.manifest.entrypoint.split(":")
            module = importlib.import_module(module_name)
            
            # Check if it's the Python agent with PixellAdapter
            if hasattr(module, 'PixellAdapter'):
                from pixell_runtime.agents.adapters.python_agent_adapter import PythonAgentAdapter
                
                # Create instance of the PixellAdapter
                adapter_instance = module.PixellAdapter()
                return PythonAgentAdapter(package, adapter_instance)
                
            elif hasattr(module, func_name) and func_name == 'main':
                # Stdin/stdout style adapter
                from pixell_runtime.agents.adapters.python_agent_adapter import StdinStdoutAdapter
                
                main_func = getattr(module, func_name)
                return StdinStdoutAdapter(package, main_func)
            else:
                # Simple callable
                handler = getattr(module, func_name)
                return ExportsAdapter(package, {"invoke": handler})
                
        else:
            # Exports-based package
            main_module = package.manifest.main_module or "main"
            module = importlib.import_module(main_module)
            
            exports = {}
            if hasattr(package.manifest, 'exports'):
                for export in package.manifest.exports:
                    handler_parts = export.handler.split(".")
                    obj = module
                    
                    # Navigate to the handler
                    for part in handler_parts:
                        obj = getattr(obj, part)
                        
                    exports[export.name] = obj
                    logger.info(f"Mapped export '{export.name}' to handler '{export.handler}'")
                    
            return ExportsAdapter(package, exports)
            
    except Exception as e:
        logger.error(f"Failed to create adapter: {e}")
        raise