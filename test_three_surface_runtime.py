#!/usr/bin/env python3
"""Test script for three-surface runtime."""

import asyncio
import json
import os
import tempfile
import zipfile
from pathlib import Path

import httpx
import grpc
import structlog

# Add src to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent / "src"))

from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
from pixell_runtime.agents.loader import PackageLoader
from pixell_runtime.proto import agent_pb2_grpc, agent_pb2

logger = structlog.get_logger()


def create_test_agent_package():
    """Create a test agent package with all three surfaces."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create agent.yaml
        agent_yaml = {
            "name": "test-three-surface-agent",
            "version": "0.1.0",
            "description": "Test agent with all three surfaces",
            "entrypoint": "main:handler",
            "a2a": {
                "service": "a2a_service:create_grpc_server"
            },
            "rest": {
                "entry": "rest_routes:mount"
            },
            "ui": {
                "path": "ui",
                "basePath": "/"
            },
            "metadata": {
                "sub_agents": [
                    {
                        "name": "test_agent",
                        "description": "Test agent",
                        "public": True
                    }
                ]
            }
        }
        
        # Write agent.yaml
        with open(temp_path / "agent.yaml", "w") as f:
            import yaml
            yaml.dump(agent_yaml, f)
        
        # Create A2A service
        a2a_service_code = '''
import grpc
from concurrent import futures

def create_grpc_server():
    """Create custom gRPC server."""
    class CustomService:
        def __init__(self):
            self.custom_handlers = {
                "custom_action": self.handle_custom_action
            }
        
        async def handle_custom_action(self, parameters):
            return f"Custom action executed with: {parameters}"
    
    return CustomService()
'''
        
        (temp_path / "a2a_service.py").write_text(a2a_service_code)
        
        # Create REST routes
        rest_routes_code = '''
from fastapi import FastAPI

def mount(app: FastAPI):
    """Mount custom REST routes."""
    @app.get("/api/custom")
    async def custom_endpoint():
        return {"message": "Custom REST endpoint"}
    
    @app.post("/api/echo")
    async def echo(data: dict):
        return {"echo": data}
'''
        
        (temp_path / "rest_routes.py").write_text(rest_routes_code)
        
        # Create UI assets
        ui_dir = temp_path / "ui"
        ui_dir.mkdir()
        
        index_html = '''
<!DOCTYPE html>
<html>
<head>
    <title>Test Agent UI</title>
</head>
<body>
    <h1>Test Agent UI</h1>
    <p>This is a test UI for the three-surface runtime.</p>
</body>
</html>
'''
        
        (ui_dir / "index.html").write_text(index_html)
        
        # Create main handler
        main_code = '''
def handler(input_data):
    """Main agent handler."""
    return {"result": f"Processed: {input_data}"}
'''
        
        (temp_path / "main.py").write_text(main_code)
        
        # Create APKG file
        apkg_path = temp_path / "test-agent.apkg"
        with zipfile.ZipFile(apkg_path, 'w') as zf:
            for file_path in temp_path.rglob("*"):
                if file_path.is_file() and file_path.name != "test-agent.apkg":
                    arcname = file_path.relative_to(temp_path)
                    zf.write(file_path, arcname)
        
        return apkg_path


async def test_rest_endpoints():
    """Test REST endpoints."""
    logger.info("Testing REST endpoints")
    
    async with httpx.AsyncClient() as client:
        # Test health endpoint
        response = await client.get("http://localhost:8080/health")
        assert response.status_code == 200
        health_data = response.json()
        assert health_data["ok"] is True
        assert "surfaces" in health_data
        logger.info("Health endpoint test passed", data=health_data)
        
        # Test metadata endpoint
        response = await client.get("http://localhost:8080/meta")
        assert response.status_code == 200
        meta_data = response.json()
        assert meta_data["name"] == "test-three-surface-agent"
        logger.info("Metadata endpoint test passed", data=meta_data)
        
        # Test custom REST endpoint
        response = await client.get("http://localhost:8080/api/custom")
        assert response.status_code == 200
        custom_data = response.json()
        assert custom_data["message"] == "Custom REST endpoint"
        logger.info("Custom REST endpoint test passed", data=custom_data)
        
        # Test echo endpoint
        test_data = {"test": "data"}
        response = await client.post("http://localhost:8080/api/echo", json=test_data)
        assert response.status_code == 200
        echo_data = response.json()
        assert echo_data["echo"] == test_data
        logger.info("Echo endpoint test passed", data=echo_data)


async def test_grpc_endpoints():
    """Test gRPC endpoints."""
    logger.info("Testing gRPC endpoints")
    
    # Create gRPC channel
    channel = grpc.aio.insecure_channel("localhost:50051")
    stub = agent_pb2_grpc.AgentServiceStub(channel)
    
    try:
        # Test health endpoint
        response = await stub.Health(agent_pb2.Empty())
        assert response.ok is True
        logger.info("gRPC health test passed", message=response.message)
        
        # Test capabilities endpoint
        response = await stub.DescribeCapabilities(agent_pb2.Empty())
        assert len(response.methods) > 0
        logger.info("gRPC capabilities test passed", methods=list(response.methods))
        
        # Test ping endpoint
        response = await stub.Ping(agent_pb2.Empty())
        assert response.message == "pong"
        logger.info("gRPC ping test passed", message=response.message)
        
        # Test invoke endpoint
        request = agent_pb2.ActionRequest(
            action="custom_action",
            parameters={"param1": "value1"},
            request_id="test-123"
        )
        response = await stub.Invoke(request)
        assert response.success is True
        assert "Custom action executed" in response.result
        logger.info("gRPC invoke test passed", result=response.result)
        
    finally:
        await channel.close()


async def test_ui_endpoints():
    """Test UI endpoints."""
    logger.info("Testing UI endpoints")
    
    async with httpx.AsyncClient() as client:
        # Test UI health endpoint
        response = await client.get("http://localhost:8080/ui/health")
        assert response.status_code == 200
        ui_health = response.json()
        assert ui_health["ok"] is True
        logger.info("UI health test passed", data=ui_health)
        
        # Test UI serving
        response = await client.get("http://localhost:8080/")
        assert response.status_code == 200
        assert "Test Agent UI" in response.text
        logger.info("UI serving test passed")


async def main():
    """Main test function."""
    logger.info("Starting three-surface runtime test")
    
    # Create test package
    apkg_path = create_test_agent_package()
    logger.info("Created test package", path=str(apkg_path))
    
    # Start runtime
    runtime = ThreeSurfaceRuntime(str(apkg_path))
    
    # Start runtime in background
    runtime_task = asyncio.create_task(runtime.start())
    
    # Wait a bit for services to start
    await asyncio.sleep(3)
    
    try:
        # Test all endpoints
        await test_rest_endpoints()
        await test_grpc_endpoints()
        await test_ui_endpoints()
        
        logger.info("All tests passed!")
        
    except Exception as e:
        logger.error("Test failed", error=str(e))
        raise
    
    finally:
        # Cleanup
        runtime_task.cancel()
        try:
            await runtime_task
        except asyncio.CancelledError:
            pass
        
        await runtime.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
