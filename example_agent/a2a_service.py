"""A2A service implementation for example agent."""

import grpc
from concurrent import futures

def create_grpc_server():
    """Create custom gRPC server with example handlers."""
    class ExampleA2AService:
        def __init__(self):
            self.custom_handlers = {
                "process_data": self.handle_process_data,
                "get_status": self.handle_get_status,
                "calculate": self.handle_calculate
            }
        
        async def handle_process_data(self, parameters):
            """Process data action."""
            data = parameters.get("data", "")
            return f"Processed data: {data.upper()}"
        
        async def handle_get_status(self, parameters):
            """Get status action."""
            return {
                "status": "running",
                "uptime": "1 hour",
                "processed_items": 42
            }
        
        async def handle_calculate(self, parameters):
            """Calculate action."""
            a = float(parameters.get("a", 0))
            b = float(parameters.get("b", 0))
            operation = parameters.get("operation", "add")
            
            if operation == "add":
                result = a + b
            elif operation == "multiply":
                result = a * b
            else:
                result = a + b  # default to add
            
            return {
                "operation": operation,
                "a": a,
                "b": b,
                "result": result
            }
    
    return ExampleA2AService()
