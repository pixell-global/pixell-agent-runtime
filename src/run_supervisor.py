"""Run the PAR Supervisor."""

import asyncio
import logging
import uvicorn
from pathlib import Path
import sys

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from supervisor import Supervisor

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
)

logger = logging.getLogger(__name__)


def main():
    """Run the supervisor."""
    
    # Example configuration
    config = {
        "base_port": 8001,
        "initial_agents": [
            # Add initial agents here if needed
            # {
            #     "agent_id": "example-agent",
            #     "package_id": "com.example.agent",
            #     "package_path": "/path/to/agent.apkg",
            #     "env_vars": {}
            # }
        ]
    }
    
    # Create supervisor
    supervisor = Supervisor(config)
    
    # Run with uvicorn
    logger.info("Starting PAR Supervisor on port 8000")
    uvicorn.run(
        supervisor.app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        access_log=True
    )


if __name__ == "__main__":
    main()