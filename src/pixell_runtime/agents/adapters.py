"""Adapters for different agent types."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import structlog

logger = structlog.get_logger()


class PythonExecutorAdapter:
    """Direct Python code executor adapter."""
    
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
    
    async def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Execute Python code directly."""
        code = request.get("code", "")
        session_id = request.get("session_id", "default")
        
        # Get or create session
        session = self.sessions.get(session_id, {})
        
        # Create execution context
        exec_globals = session.copy()
        exec_locals = {}
        
        # Capture output
        from io import StringIO
        import contextlib
        
        stdout = StringIO()
        stderr = StringIO()
        
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exec(code, exec_globals, exec_locals)
            
            # Update session with new variables
            session.update(exec_locals)
            self.sessions[session_id] = session
            
            # Get result if available
            result = exec_locals.get("result", None)
            
            return {
                "status": "success",
                "result": result,
                "stdout": stdout.getvalue(),
                "stderr": stderr.getvalue(),
                "session_id": session_id
            }
            
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "stdout": stdout.getvalue(),
                "stderr": stderr.getvalue(),
                "session_id": session_id
            }


# Create global instances for the agent exports
code_executor = PythonExecutorAdapter()
stream_executor = PythonExecutorAdapter()  # For now, same as regular executor
data_analyzer = PythonExecutorAdapter()   # For now, same as regular executor
ml_runner = PythonExecutorAdapter()       # For now, same as regular executor