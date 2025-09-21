"""Test Python agent via supervisor."""

import asyncio
import json
import logging
import sys
from pathlib import Path
import aiohttp

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from supervisor.supervisor import Supervisor
from supervisor.models import ProcessConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


import socket


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_python_agent():
    """Test Python agent deployment via supervisor."""
    
    # Create supervisor
    supervisor = Supervisor({"base_port": 8001})
    
    # Path to Python agent APKG
    apkg_path = Path("/Users/syum/dev/pixell-agent-runtime/pixell-python-agent-0.1.0.apkg")
    
    # Start supervisor in background
    port = _find_free_port()
    supervisor_task = asyncio.create_task(run_supervisor(supervisor, port))
    
    try:
        # Wait for supervisor to start
        await asyncio.sleep(2)
        
        # Create process config for Python agent
        config = ProcessConfig(
            agent_id="python-agent",
            package_id="pixell-python-agent@0.1.0",
            package_path=str(apkg_path),
            env_vars={},
            restart_policy="on-failure",
            max_restarts=3
        )
        
        # Spawn process
        logger.info("Spawning Python agent process...")
        process = await supervisor.process_manager.spawn_process(config)
        logger.info(f"Process spawned: {process}")
        
        # Update routing
        supervisor._update_routes()
        
        # Wait for agent to be ready
        logger.info("Waiting for agent to be ready...")
        await asyncio.sleep(5)
        
        # Check process logs
        logs = supervisor.process_manager.log_aggregator.get_logs(process_id="par-python-agent", limit=20)
        logger.info("=== Process logs ===")
        for log in logs:
            logger.info(f"{log.timestamp} [{log.level}] {log.message}")
        
        # Test via HTTP client
        async with aiohttp.ClientSession() as session:
            base_url = f"http://localhost:{port}"
            
            # Check agent health
            logger.info("\n=== Checking agent health ===")
            async with session.get(f"{base_url}/agents/python-agent/health") as resp:
                health = await resp.json()
                logger.info(f"Agent health: {json.dumps(health, indent=2)}")
            
            # Test get_info
            logger.info("\n=== Testing get_info ===")
            async with session.post(
                f"{base_url}/agents/python-agent/exports/get_info",
                json={}
            ) as resp:
                info = await resp.json()
                logger.info(f"Agent info: {json.dumps(info, indent=2)}")
            
            # Test list_capabilities
            logger.info("\n=== Testing list_capabilities ===")
            async with session.post(
                f"{base_url}/agents/python-agent/exports/list_capabilities",
                json={}
            ) as resp:
                capabilities = await resp.json()
                logger.info(f"Agent capabilities: {json.dumps(capabilities, indent=2)}")
            
            # Test code execution
            logger.info("\n=== Testing code execution ===")
            
            # Simple math
            async with session.post(
                f"{base_url}/agents/python-agent/exports/execute",
                json={
                    "code": "result = 2 + 2\nprint(f'2 + 2 = {result}')",
                    "session_id": "test-1"
                }
            ) as resp:
                exec_result = await resp.json()
                logger.info(f"Math result: {json.dumps(exec_result, indent=2)}")
            
            # Test with numpy
            async with session.post(
                f"{base_url}/agents/python-agent/exports/execute",
                json={
                    "code": """
import numpy as np

# Create array
arr = np.array([1, 2, 3, 4, 5])
print(f"Array: {arr}")
print(f"Mean: {arr.mean()}")
print(f"Std: {arr.std()}")
""",
                    "session_id": "test-2"
                }
            ) as resp:
                exec_result2 = await resp.json()
                logger.info(f"Numpy result: {json.dumps(exec_result2, indent=2)}")
            
            # Check supervisor status
            logger.info("\n=== Supervisor status ===")
            async with session.get(f"{base_url}/supervisor/status") as resp:
                status = await resp.json()
                logger.info(f"Supervisor status: {json.dumps(status, indent=2)}")
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        
    finally:
        # Stop supervisor
        await supervisor.stop()
        supervisor_task.cancel()
        logger.info("Test complete")


async def run_supervisor(supervisor, port: int):
    """Run supervisor app."""
    import uvicorn
    
    config = uvicorn.Config(
        app=supervisor.app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(test_python_agent())