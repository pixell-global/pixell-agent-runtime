#!/usr/bin/env python3
"""Interactive client to talk to deployed agents via A2A."""

import asyncio
import json
import sys
from typing import Optional

import httpx
import structlog

logger = structlog.get_logger()


class AgentClient:
    """Client for communicating with agents via PAR's A2A interface."""

    def __init__(self, base_url: str = "https://par.pixell.global"):
        """Initialize the agent client.

        Args:
            base_url: Base URL of the PAR instance
        """
        self.base_url = base_url.rstrip('/')
        self.client = httpx.AsyncClient(timeout=30.0)

    async def list_deployments(self):
        """List all active deployments (if endpoint exists)."""
        try:
            response = await self.client.get(f"{self.base_url}/deployments")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.info("Deployments list endpoint not available")
                return None
            raise

    async def check_health(self, deployment_id: str) -> dict:
        """Check if a deployment is healthy.

        Args:
            deployment_id: The deployment ID to check

        Returns:
            Health status dictionary
        """
        response = await self.client.get(
            f"{self.base_url}/deployments/{deployment_id}/health"
        )
        response.raise_for_status()
        return response.json()

    async def invoke(
        self,
        deployment_id: str,
        action: str,
        context: dict
    ) -> dict:
        """Invoke an action on the agent.

        Args:
            deployment_id: The deployment ID to invoke
            action: The action name (e.g., "comment")
            context: Context data for the action

        Returns:
            Response from the agent
        """
        payload = {
            "action": action,
            "context": json.dumps(context)
        }

        logger.info("Invoking agent",
                   deployment_id=deployment_id,
                   action=action)

        response = await self.client.post(
            f"{self.base_url}/deployments/{deployment_id}/invoke",
            json=payload
        )
        response.raise_for_status()
        result = response.json()

        logger.info("Agent responded",
                   success=result.get('success'),
                   has_error=bool(result.get('error')))

        return result

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


async def interactive_mode(client: AgentClient, deployment_id: str):
    """Run interactive mode to chat with the agent.

    Args:
        client: The agent client
        deployment_id: The deployment to talk to
    """
    print(f"\n🤖 Connected to agent: {deployment_id}")
    print("=" * 60)

    # Check health
    try:
        health = await client.check_health(deployment_id)
        print(f"Status: {health.get('status')}")
        print(f"Surfaces: {health.get('surfaces')}")
        print(f"Ports: {health.get('ports')}")
    except Exception as e:
        print(f"⚠️  Health check failed: {e}")
        return

    print("\n" + "=" * 60)
    print("Chat with the AI agent (or 'quit' to exit)")
    print("")
    print("You can:")
    print("  - Ask questions: What is Python?")
    print("  - Request code comments: comment:python:def hello(): pass")
    print("  - Just chat naturally!")
    print("=" * 60 + "\n")

    while True:
        try:
            # Get input
            user_input = input("\n💬 You: ").strip()

            if not user_input:
                continue

            if user_input.lower() in ['quit', 'exit', 'q']:
                print("\n👋 Goodbye!")
                break

            # Determine action and context based on input format
            # Format: comment:language:code OR just plain text
            if user_input.startswith("comment:") and user_input.count(':') >= 2:
                # Code comment mode: comment:language:code
                parts = user_input.split(':', 2)
                action = "comment"
                language = parts[1].strip()
                code = parts[2].strip()

                if not code:
                    print("⚠️  Code cannot be empty")
                    continue

                context = {
                    "code": code,
                    "language": language
                }
            else:
                # Plain conversation mode
                action = "chat"
                context = {
                    "message": user_input
                }

            # Invoke agent
            print(f"\n🔄 Sending to agent...")
            result = await client.invoke(
                deployment_id=deployment_id,
                action=action,
                context=context
            )

            # Display result
            print("\n" + "=" * 60)
            if result.get('success'):
                print("✅ Success!")
                print(f"\n{result.get('response', 'No response')}")
            else:
                print("❌ Failed!")
                if result.get('error'):
                    print(f"Error: {result['error']}")
            print("=" * 60)

        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            logger.exception("Invocation failed")


async def single_invocation(
    client: AgentClient,
    deployment_id: str,
    language: str,
    code: str
):
    """Make a single invocation to the agent.

    Args:
        client: The agent client
        deployment_id: The deployment to invoke
        language: Programming language
        code: Code to comment
    """
    print(f"\n🤖 Invoking agent: {deployment_id}")
    print(f"Language: {language}")
    print(f"Code: {code}\n")

    result = await client.invoke(
        deployment_id=deployment_id,
        action="comment",
        context={
            "code": code,
            "language": language
        }
    )

    print("=" * 60)
    if result.get('success'):
        print("✅ Success!")
        print(f"\n{result.get('response', 'No response')}")
    else:
        print("❌ Failed!")
        if result.get('error'):
            print(f"Error: {result['error']}")
    print("=" * 60)


async def main():
    """Main entry point."""
    print("🤖 Agent A2A Client")
    print("=" * 60)

    # Prompt for deployment ID with default
    default_deployment_id = "5a8ed496-7f16-4cd5-a718-d45545c74b0f"
    deployment_id = input(f"Enter deployment ID [{default_deployment_id}]: ").strip()

    if not deployment_id:
        deployment_id = default_deployment_id
        print(f"Using default: {deployment_id}")

    # Always use par.pixell.global
    base_url = "https://par.pixell.global"

    client = AgentClient(base_url=base_url)

    try:
        # Always run interactive mode
        await interactive_mode(client, deployment_id)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
