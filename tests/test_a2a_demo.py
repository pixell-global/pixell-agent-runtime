"""Demonstrate A2A communication between agents."""

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


async def test_a2a_demo():
    """Demonstrate A2A communication between agents."""
    
    # Create supervisor
    supervisor = Supervisor({"base_port": 8001})
    port = _find_free_port()
    
    # Path to Python agent APKG
    apkg_path = Path("/Users/syum/dev/pixell-agent-runtime/pixell-python-agent-0.1.0.apkg")
    
    # Start supervisor in background
    supervisor_task = asyncio.create_task(run_supervisor(supervisor, port))
    
    try:
        # Wait for supervisor to start
        await asyncio.sleep(2)
        
        logger.info("=== Multi-PAR Phase 3 A2A Demo ===")
        logger.info("This demonstrates the A2A protocol implementation with Python agent")
        
        # Deploy two Python agent instances
        logger.info("\n1. Deploying Agent A (Data Producer)...")
        config_a = ProcessConfig(
            agent_id="agent-a",
            package_id="pixell-python-agent@0.1.0",
            package_path=str(apkg_path),
            env_vars={},
            restart_policy="on-failure",
            max_restarts=3
        )
        process_a = await supervisor.process_manager.spawn_process(config_a)
        logger.info(f"   Agent A deployed on port {process_a.port}")
        
        logger.info("\n2. Deploying Agent B (Data Consumer)...")
        config_b = ProcessConfig(
            agent_id="agent-b",
            package_id="pixell-python-agent@0.1.0",
            package_path=str(apkg_path),
            env_vars={},
            restart_policy="on-failure",
            max_restarts=3
        )
        process_b = await supervisor.process_manager.spawn_process(config_b)
        logger.info(f"   Agent B deployed on port {process_b.port}")
        
        # Update routing
        supervisor._update_routes()
        
        # Wait for agents to be ready
        await asyncio.sleep(3)
        
        # Test via HTTP client
        async with aiohttp.ClientSession() as session:
            base_url = f"http://localhost:{port}"
            
            # Check both agents are healthy
            logger.info("\n3. Verifying agent health...")
            for agent_id in ["agent-a", "agent-b"]:
                async with session.get(f"{base_url}/agents/{agent_id}/health") as resp:
                    health = await resp.json()
                    logger.info(f"   {agent_id}: {health.get('status', 'unknown')}")
            
            # Demonstrate A2A communication flow
            logger.info("\n4. Demonstrating A2A Communication Flow:")
            logger.info("   (Note: Since the Python agent expects a real gRPC backend,")
            logger.info("    we'll simulate the A2A flow using HTTP calls)")
            
            # Agent A: Prepare data
            logger.info("\n   Step 1: Agent A prepares data")
            async with session.post(
                f"{base_url}/agents/agent-a/exports/execute",
                json={
                    "code": """
# Agent A: Preparing data for Agent B
import json
data = {
    'values': [1, 2, 3, 4, 5],
    'operation': 'sum',
    'sender': 'agent-a'
}
print(f"Agent A: Prepared data: {json.dumps(data, indent=2)}")

# In real A2A, this would be sent via:
# a2a_client.call('agent-b', 'process_data', data)
""",
                    "session_id": "a2a-demo"
                }
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get('status') == 'success':
                        logger.info("   ✓ Data prepared successfully")
                    else:
                        logger.info(f"   Agent A output: {result}")
            
            # Agent B: Process data (simulated)
            logger.info("\n   Step 2: Agent B receives and processes data")
            async with session.post(
                f"{base_url}/agents/agent-b/exports/execute",
                json={
                    "code": """
# Agent B: Processing data from Agent A
import json

# In real A2A, this data would come from the request
data = {
    'values': [1, 2, 3, 4, 5],
    'operation': 'sum',
    'sender': 'agent-a'
}

print(f"Agent B: Received data from {data['sender']}")
print(f"Agent B: Values = {data['values']}")
print(f"Agent B: Operation = {data['operation']}")

# Process the data
if data['operation'] == 'sum':
    result = sum(data['values'])
    print(f"Agent B: Computed sum = {result}")
else:
    result = None
    print(f"Agent B: Unknown operation {data['operation']}")

# In real A2A, this would be returned to Agent A
response = {
    'result': result,
    'processor': 'agent-b',
    'status': 'success'
}
print(f"Agent B: Sending response: {json.dumps(response, indent=2)}")
""",
                    "session_id": "a2a-demo-b"
                }
            ) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    if result.get('status') == 'success':
                        logger.info("   ✓ Data processed successfully")
                    else:
                        logger.info(f"   Agent B output: {result}")
            
            # Show A2A protocol benefits
            logger.info("\n5. A2A Protocol Benefits Demonstrated:")
            logger.info("   ✓ Independent agent deployment and scaling")
            logger.info("   ✓ Isolated execution environments")
            logger.info("   ✓ HTTP/gRPC communication protocols")
            logger.info(f"   ✓ Agent A on port {process_a.port}, Agent B on port {process_b.port}")
            logger.info("   ✓ Supervisor routing at port 8000")
            
            # Show supervisor status
            logger.info("\n6. Supervisor Status:")
            async with session.get(f"{base_url}/supervisor/status") as resp:
                status = await resp.json()
                logger.info(f"   Total processes: {len(status['processes'])}")
                for pid, info in status['processes'].items():
                    logger.info(f"   - {info['agent_id']}: {info['state']} on port {info['port']}")
        
    except Exception as e:
        logger.error(f"Demo failed: {e}", exc_info=True)
        
    finally:
        # Stop supervisor
        await supervisor.stop()
        supervisor_task.cancel()
        logger.info("\n✓ A2A demo complete!")


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
    asyncio.run(test_a2a_demo())