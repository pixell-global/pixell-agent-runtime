"""Adapter for Python agent packages with gRPC support."""

import json
import sys
import io
from typing import Any, Dict
from pathlib import Path

import structlog

from pixell_runtime.core.models import AgentPackage
from .direct_python_executor import DirectPythonExecutor

logger = structlog.get_logger()


class PythonAgentAdapter:
    """Adapter for Python agents that use the PixellAdapter pattern."""
    
    def __init__(self, package: AgentPackage, adapter_instance):
        self.package = package
        self.adapter = adapter_instance
        # Replace the gRPC client with direct executor
        self.executor = DirectPythonExecutor()
        
    async def initialize(self):
        """Initialize the adapter."""
        # Adapter is already initialized in __init__
        logger.info(f"Python agent adapter initialized for {self.package.id}")
        
    async def invoke(self, export_name: str, params: Dict[str, Any]) -> Any:
        """Invoke an export."""
        try:
            # Map export name to action if needed
            if export_name == "invoke":
                # Default action
                if "action" not in params:
                    params["action"] = "execute"
            else:
                # Use export name as action
                params["action"] = export_name
            
            # Handle different actions
            action = params.get("action", export_name)
            
            if action == "execute":
                # Use direct executor instead of gRPC
                return self.executor.execute_code(params)
            elif action == "get_info":
                return self.executor.get_info()
            elif action == "list_capabilities":
                return self.executor.list_capabilities()
            else:
                # Fall back to adapter for unknown actions
                result = self.adapter.process_request(params)
                
                # Ensure result is JSON-serializable
                if isinstance(result, dict):
                    return result
                else:
                    return {"result": result}
                
        except Exception as e:
            logger.error(f"Error invoking {export_name}: {e}")
            return {
                "status": "error",
                "message": str(e)
            }
            
    async def cleanup(self):
        """Clean up resources."""
        try:
            if hasattr(self.adapter, 'cleanup'):
                self.adapter.cleanup()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")


class StdinStdoutAdapter:
    """Adapter for agents that use stdin/stdout communication."""
    
    def __init__(self, package: AgentPackage, main_func):
        self.package = package
        self.main_func = main_func
        
    async def initialize(self):
        """Initialize the adapter."""
        logger.info(f"Stdin/stdout adapter initialized for {self.package.id}")
        
    async def invoke(self, export_name: str, params: Dict[str, Any]) -> Any:
        """Invoke via stdin/stdout."""
        # Prepare request
        request = {
            "action": export_name,
            **params
        }
        
        # Mock stdin/stdout for the function
        old_stdin = sys.stdin
        old_stdout = sys.stdout
        
        try:
            # Set up stdin with request data
            sys.stdin = io.StringIO(json.dumps(request))
            sys.stdout = io.StringIO()
            
            # Call main function
            self.main_func()
            
            # Get output
            output = sys.stdout.getvalue()
            return json.loads(output)
            
        except Exception as e:
            logger.error(f"Error invoking {export_name}: {e}")
            return {
                "status": "error",
                "message": str(e)
            }
        finally:
            sys.stdin = old_stdin
            sys.stdout = old_stdout
            
    async def cleanup(self):
        """Clean up resources."""
        pass