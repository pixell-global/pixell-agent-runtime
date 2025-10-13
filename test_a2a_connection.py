#!/usr/bin/env python3
"""Test A2A connection to a specific deployment."""

import asyncio
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from pixell_runtime.a2a.client import get_a2a_client
from pixell_runtime.utils.service_discovery import get_service_discovery_client
import structlog

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
    ]
)

logger = structlog.get_logger()


async def test_a2a_connection(deployment_id: str):
    """Test A2A connection to a deployment."""
    logger.info("Testing A2A connection", deployment_id=deployment_id)

    # Check Service Discovery
    sd_client = get_service_discovery_client()
    if sd_client:
        logger.info("Service Discovery client available")
        agents = sd_client.discover_agents()
        logger.info("Discovered agents", count=len(agents), agents=agents)

        # Try to find specific deployment
        agent = sd_client.discover_agent_by_id(deployment_id)
        if agent:
            logger.info("Found specific deployment", agent=agent)
        else:
            logger.warning("Deployment not found in Service Discovery")
    else:
        logger.warning("Service Discovery not configured")

    # Test A2A client
    client = get_a2a_client(prefer_internal=True)

    try:
        logger.info("Attempting health check...")
        is_healthy = await client.health_check(deployment_id=deployment_id)

        if is_healthy:
            logger.info("✓ A2A health check PASSED")
            return True
        else:
            logger.error("✗ A2A health check FAILED")
            return False

    except Exception as e:
        logger.error("✗ A2A health check exception", error=str(e), exc_info=True)
        return False


if __name__ == "__main__":
    deployment_id = "80cef39f-3daf-47bf-93f9-c33f08e51292"

    result = asyncio.run(test_a2a_connection(deployment_id))
    sys.exit(0 if result else 1)