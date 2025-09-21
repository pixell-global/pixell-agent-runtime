"""Direct Python executor for the runtime environment."""

import sys
import io
import traceback
import contextlib
from typing import Dict, Any
import ast
import builtins

import structlog

logger = structlog.get_logger()


class DirectPythonExecutor:
    """Execute Python code directly in the runtime environment."""
    
    def __init__(self):
        self.sessions = {}  # session_id -> globals dict
        
    def execute_code(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute Python code and return results."""
        code = data.get("code", "")
        session_id = data.get("session_id", "default")
        
        # Get or create session globals
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "__builtins__": builtins,
                "__name__": "__main__"
            }
        
        session_globals = self.sessions[session_id]
        
        # Capture stdout and stderr
        stdout_capture = io.StringIO()
        stderr_capture = io.StringIO()
        
        try:
            # Parse the code to check for syntax errors
            ast.parse(code)
            
            # Execute with captured output
            with contextlib.redirect_stdout(stdout_capture), \
                 contextlib.redirect_stderr(stderr_capture):
                
                # Execute the code
                exec(code, session_globals)
            
            # Extract any new variables (results)
            results = {}
            # Get variables that were created/modified (excluding built-ins and private)
            for name, value in session_globals.items():
                if not name.startswith('_') and name not in ['__builtins__', '__name__']:
                    try:
                        # Try to make it JSON serializable
                        if isinstance(value, (str, int, float, bool, list, dict)):
                            results[name] = value
                        else:
                            results[name] = str(value)
                    except:
                        results[name] = f"<{type(value).__name__} object>"
            
            return {
                "status": "success",
                "stdout": stdout_capture.getvalue(),
                "stderr": stderr_capture.getvalue(),
                "results": results,
                "metrics": {
                    "execution_time_ms": 0,  # Could add timing
                    "memory_used_bytes": 0,  # Could add memory tracking
                    "cpu_percent": 0
                }
            }
            
        except SyntaxError as e:
            return {
                "status": "error",
                "message": f"Syntax error: {e}",
                "stdout": stdout_capture.getvalue(),
                "stderr": stderr_capture.getvalue() + f"\nSyntaxError: {e}"
            }
        except Exception as e:
            tb = traceback.format_exc()
            return {
                "status": "error",
                "message": str(e),
                "stdout": stdout_capture.getvalue(),
                "stderr": stderr_capture.getvalue() + f"\n{tb}"
            }
    
    def get_info(self) -> Dict[str, Any]:
        """Get executor information."""
        return {
            "status": "success",
            "info": {
                "name": "direct-python-executor",
                "version": "1.0.0",
                "description": "Direct Python execution in runtime environment",
                "python_version": sys.version,
                "capabilities": [
                    "code-execution",
                    "session-management",
                    "direct-execution"
                ]
            }
        }
    
    def list_capabilities(self) -> Dict[str, Any]:
        """List detailed capabilities."""
        import pkg_resources
        
        # Get installed packages
        installed_packages = [pkg.key for pkg in pkg_resources.working_set]
        
        return {
            "status": "success",
            "capabilities": {
                "execution": {
                    "mode": "direct",
                    "python_version": sys.version,
                    "platform": sys.platform
                },
                "packages": {
                    "total": len(installed_packages),
                    "available": sorted(installed_packages)[:20]  # First 20 as sample
                },
                "sessions": {
                    "active": len(self.sessions),
                    "session_ids": list(self.sessions.keys())
                }
            }
        }