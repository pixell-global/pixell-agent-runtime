"""
Pixell Agent Kit adapter for the Python Execution Agent.

This module provides the standard interface expected by Pixell Kit
while maintaining the A2A gRPC service as the primary interface.
"""

import json
import sys
import asyncio
import os
from typing import Dict, Any

import grpc
from .a2a import python_agent_pb2
from .a2a import python_agent_pb2_grpc


class PixellAdapter:
    """Adapter to make the A2A agent compatible with Pixell Kit."""
    
    def __init__(self):
        self.channel = None
        self.stub = None
        self.connect()
    
    def connect(self):
        """Connect to the local A2A service."""
        # Check if service is running locally
        socket_path = "/tmp/pixell-python-agent-50051.sock"
        if os.path.exists(socket_path):
            self.channel = grpc.insecure_channel(f"unix://{socket_path}")
        else:
            # Fallback to TCP
            port = os.environ.get("A2A_PORT", "50051")
            self.channel = grpc.insecure_channel(f"localhost:{port}")
        
        self.stub = python_agent_pb2_grpc.PythonAgentStub(self.channel)
    
    def process_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process request in Pixell Kit format."""
        action = data.get("action", "execute")
        
        if action == "execute":
            return self.execute_code(data)
        elif action == "get_info":
            return self.get_info()
        elif action == "list_capabilities":
            return self.list_capabilities()
        else:
            return {
                "status": "error",
                "message": f"Unknown action: {action}"
            }
    
    def execute_code(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute code via A2A service."""
        try:
            # Build request
            request = python_agent_pb2.ExecuteRequest(
                code=data.get("code", ""),
                session_id=data.get("session_id", "default"),
                resource_tier=self._get_resource_tier(data.get("resource", "small")),
                timeout_seconds=data.get("timeout", 60)
            )
            
            # Add context if provided
            context = data.get("context", {})
            if context:
                import msgpack
                for key, value in context.items():
                    request.context[key] = msgpack.packb(value, use_bin_type=True)
            
            # Execute
            response = self.stub.Execute(request)
            
            # Format response
            if response.success:
                results = {}
                if response.results:
                    import msgpack
                    for key, value in response.results.items():
                        try:
                            results[key] = msgpack.unpackb(value, raw=False)
                        except:
                            results[key] = value
                
                return {
                    "status": "success",
                    "stdout": response.stdout,
                    "stderr": response.stderr,
                    "results": results,
                    "metrics": {
                        "execution_time_ms": response.metrics.execution_time_ms,
                        "memory_used_bytes": response.metrics.memory_used_bytes,
                        "cpu_percent": response.metrics.cpu_percent
                    }
                }
            else:
                return {
                    "status": "error",
                    "message": response.error,
                    "stdout": response.stdout,
                    "stderr": response.stderr
                }
                
        except grpc.RpcError as e:
            return {
                "status": "error",
                "message": f"gRPC error: {e.details()}"
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e)
            }
    
    def get_info(self) -> Dict[str, Any]:
        """Get agent information."""
        return {
            "status": "success",
            "info": {
                "name": "pixell-python-agent",
                "version": "0.1.0",
                "description": "Comprehensive Python code execution service for all AI Agents",
                "capabilities": [
                    "code-execution",
                    "python-runtime",
                    "data-analysis",
                    "machine-learning",
                    "visualization",
                    "error-recovery",
                    "session-management"
                ],
                "resource_tiers": {
                    "small": "1 CPU, 2GB RAM",
                    "medium": "2 CPU, 4GB RAM",
                    "large": "4 CPU, 16GB RAM"
                },
                "packages": "400+ pre-installed (numpy, pandas, sklearn, etc.)"
            }
        }
    
    def list_capabilities(self) -> Dict[str, Any]:
        """List detailed capabilities."""
        return {
            "status": "success",
            "capabilities": {
                "execution": {
                    "languages": ["python"],
                    "version": "3.11+",
                    "timeout_max": 600,
                    "file_size_max": "1GB"
                },
                "packages": {
                    "data_science": ["numpy", "pandas", "scipy", "statsmodels"],
                    "machine_learning": ["scikit-learn", "xgboost", "lightgbm"],
                    "visualization": ["matplotlib", "seaborn", "plotly"],
                    "deep_learning": ["torch", "tensorflow"],
                    "nlp": ["nltk", "spacy"],
                    "web": ["requests", "beautifulsoup4", "selenium"]
                },
                "features": {
                    "session_persistence": "90 minutes",
                    "error_recovery": "automatic with 2 retries",
                    "streaming": "real-time output",
                    "security": "container isolation"
                }
            }
        }
    
    def _get_resource_tier(self, resource: str) -> int:
        """Convert resource string to tier number."""
        tiers = {
            "small": 0,
            "medium": 1,
            "large": 2
        }
        return tiers.get(resource.lower(), 0)
    
    def cleanup(self):
        """Clean up resources."""
        if self.channel:
            self.channel.close()


def main():
    """Main entry point for Pixell Kit."""
    adapter = PixellAdapter()
    
    try:
        # Read input from stdin (Pixell Kit standard)
        input_data = sys.stdin.read()
        data = json.loads(input_data)
        
        # Process request
        result = adapter.process_request(data)
        
        # Write output to stdout
        print(json.dumps(result))
        
    except Exception as e:
        error_response = {
            "status": "error",
            "message": str(e)
        }
        print(json.dumps(error_response))
        sys.exit(1)
    finally:
        adapter.cleanup()


# Also support running the gRPC service directly
def run_service():
    """Run the A2A gRPC service."""
    from .main import run
    run()


if __name__ == "__main__":
    # Check if we should run as service or adapter
    if len(sys.argv) > 1 and sys.argv[1] == "--service":
        run_service()
    else:
        main()