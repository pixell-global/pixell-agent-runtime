"""REST routes for example agent."""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any

class ProcessRequest(BaseModel):
    data: str

class CalculateRequest(BaseModel):
    a: float
    b: float
    operation: str = "add"

def mount(app: FastAPI):
    """Mount custom REST routes."""
    
    @app.get("/api/status")
    async def get_status():
        """Get agent status."""
        return {
            "status": "running",
            "version": "0.1.0",
            "surfaces": ["rest", "a2a", "ui"]
        }
    
    @app.post("/api/process")
    async def process_data(request: ProcessRequest):
        """Process data endpoint."""
        processed = request.data.upper()
        return {
            "original": request.data,
            "processed": processed,
            "length": len(processed)
        }
    
    @app.post("/api/calculate")
    async def calculate(request: CalculateRequest):
        """Calculate endpoint."""
        if request.operation == "add":
            result = request.a + request.b
        elif request.operation == "multiply":
            result = request.a * request.b
        elif request.operation == "subtract":
            result = request.a - request.b
        elif request.operation == "divide":
            if request.b == 0:
                raise HTTPException(status_code=400, detail="Division by zero")
            result = request.a / request.b
        else:
            raise HTTPException(status_code=400, detail="Invalid operation")
        
        return {
            "operation": request.operation,
            "a": request.a,
            "b": request.b,
            "result": result
        }
    
    @app.get("/api/health")
    async def custom_health():
        """Custom health check."""
        return {
            "ok": True,
            "service": "example-agent",
            "custom": True
        }
