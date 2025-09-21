"""Test gRPC-based A2A communication between agents."""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pixell_agent_runtime.supervisor import Supervisor
from pixell_agent_runtime.a2a_grpc_client import A2AGrpcClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_grpc_a2a_communication():
    """Test gRPC communication between Python agents."""
    
    # Create supervisor
    supervisor = Supervisor(base_port=8000)
    
    # Path to Python agent APKG
    apkg_path = Path("/Users/syum/dev/pixell-agent-runtime/pixell-python-agent-0.1.0.apkg")
    
    try:
        # Deploy two instances of the Python agent
        logger.info("Deploying Python agent instances...")
        
        # Deploy first agent
        agent1_info = await supervisor.deploy_agent("python-agent-1", str(apkg_path))
        logger.info(f"Agent 1 deployed: {agent1_info}")
        
        # Deploy second agent
        agent2_info = await supervisor.deploy_agent("python-agent-2", str(apkg_path))
        logger.info(f"Agent 2 deployed: {agent2_info}")
        
        # Wait for agents to be ready
        await asyncio.sleep(3)
        
        # Test HTTP health check first
        logger.info("\n=== Testing HTTP endpoints ===")
        
        # Check agent 1 health
        health1 = await supervisor.check_agent_health("python-agent-1")
        logger.info(f"Agent 1 health: {health1}")
        
        # Check agent 2 health
        health2 = await supervisor.check_agent_health("python-agent-2")
        logger.info(f"Agent 2 health: {health2}")
        
        # Now test gRPC communication
        logger.info("\n=== Testing gRPC communication ===")
        
        # Create gRPC client
        grpc_client = A2AGrpcClient()
        
        # Try to call Python agent's Execute method via gRPC
        try:
            # Import protobuf messages
            sys.path.insert(0, "/tmp/pixell-runtime/packages/9999/pixell-python-agent@0.1.0/src")
            from a2a import python_agent_pb2
            
            # Create execute request
            request = python_agent_pb2.ExecuteRequest(
                code='print("Hello from gRPC!")\nresult = 2 + 2',
                session_id="test-session",
                timeout_seconds=30
            )
            
            # Get gRPC port from health check
            grpc_port1 = health1.get("grpc_port", 19999)
            
            logger.info(f"Calling Python agent on gRPC port {grpc_port1}...")
            
            # Make gRPC call
            response = await grpc_client.call_grpc_method(
                agent_id="python-agent-1",
                port=grpc_port1,
                service_name="PythonAgent",
                method_name="Execute",
                request_message=request
            )
            
            logger.info(f"gRPC Response: success={response.success}, stdout={response.stdout}")
            
        except Exception as e:
            logger.error(f"gRPC call failed: {e}")
            
            # Fallback to HTTP for now
            logger.info("\n=== Testing via HTTP adapter ===")
            
            # Test via HTTP endpoint
            result = await supervisor.invoke_agent(
                "python-agent-1",
                "get_info",
                {}
            )
            logger.info(f"Agent info via HTTP: {json.dumps(result, indent=2)}")
            
            # Test code execution via HTTP
            exec_result = await supervisor.invoke_agent(
                "python-agent-1",
                "execute",
                {
                    "action": "execute",
                    "code": "print('Hello from HTTP!')\nresult = 2 + 2\nprint(f'Result: {result}')",
                    "session_id": "test-http"
                }
            )
            logger.info(f"Execution via HTTP: {json.dumps(exec_result, indent=2)}")
        
        # Clean up
        grpc_client.close()
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        
    finally:
        # Shutdown supervisor
        await supervisor.shutdown()
        logger.info("Test complete")


if __name__ == "__main__":
    asyncio.run(test_grpc_a2a_communication())