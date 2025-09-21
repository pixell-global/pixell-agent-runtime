"""Test Python agent via HTTP adapter."""

import asyncio
import json
import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from supervisor.supervisor import Supervisor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_python_agent_http():
    """Test Python agent via HTTP adapter."""
    
    # Create supervisor
    supervisor = Supervisor({"base_port": 8000})
    
    # Path to Python agent APKG
    apkg_path = Path("/Users/syum/dev/pixell-agent-runtime/pixell-python-agent-0.1.0.apkg")
    
    try:
        # Deploy Python agent
        logger.info("Deploying Python agent...")
        agent_info = await supervisor.deploy_agent("python-agent", str(apkg_path))
        logger.info(f"Agent deployed: {agent_info}")
        
        # Wait for agent to be ready
        await asyncio.sleep(2)
        
        # Check health
        health = await supervisor.check_agent_health("python-agent")
        logger.info(f"Agent health: {json.dumps(health, indent=2)}")
        
        # Test get_info
        logger.info("\n=== Testing get_info ===")
        info_result = await supervisor.invoke_agent(
            "python-agent",
            "get_info",
            {}
        )
        logger.info(f"Agent info: {json.dumps(info_result, indent=2)}")
        
        # Test list_capabilities
        logger.info("\n=== Testing list_capabilities ===")
        capabilities_result = await supervisor.invoke_agent(
            "python-agent",
            "list_capabilities",
            {}
        )
        logger.info(f"Agent capabilities: {json.dumps(capabilities_result, indent=2)}")
        
        # Test code execution
        logger.info("\n=== Testing code execution ===")
        
        # Simple math
        exec_result = await supervisor.invoke_agent(
            "python-agent",
            "execute",
            {
                "code": "result = 2 + 2\nprint(f'2 + 2 = {result}')",
                "session_id": "test-1"
            }
        )
        logger.info(f"Math result: {json.dumps(exec_result, indent=2)}")
        
        # Test with data science libraries
        exec_result2 = await supervisor.invoke_agent(
            "python-agent",
            "execute",
            {
                "code": """
import numpy as np
import pandas as pd

# Create sample data
data = pd.DataFrame({
    'x': [1, 2, 3, 4, 5],
    'y': [2, 4, 6, 8, 10]
})

# Calculate correlation
corr = data.corr()
print(f"Correlation matrix:\\n{corr}")

# Basic stats
print(f"\\nMean of x: {data['x'].mean()}")
print(f"Mean of y: {data['y'].mean()}")
""",
                "session_id": "test-2"
            }
        )
        logger.info(f"Data science result: {json.dumps(exec_result2, indent=2)}")
        
        # Test A2A communication simulation
        logger.info("\n=== Testing A2A simulation ===")
        
        # Simulate one agent calling another via supervisor
        # In real A2A, agents would call each other directly
        exec_result3 = await supervisor.invoke_agent(
            "python-agent",
            "execute",
            {
                "code": """
# Simulate agent communication
print("Agent A: Requesting computation from Agent B")
result = 42  # In real A2A, this would come from another agent
print(f"Agent A: Received result from Agent B: {result}")
""",
                "session_id": "test-a2a"
            }
        )
        logger.info(f"A2A simulation: {json.dumps(exec_result3, indent=2)}")
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        
    finally:
        # Shutdown supervisor
        await supervisor.shutdown()
        logger.info("Test complete")


if __name__ == "__main__":
    asyncio.run(test_python_agent_http())