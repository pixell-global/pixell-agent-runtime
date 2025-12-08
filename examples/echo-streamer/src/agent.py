"""
Echo Streamer Agent - Minimal A2A streaming agent for load testing.

This agent echoes back user messages word by word with streaming,
demonstrating A2A protocol support without external dependencies.
"""

import asyncio
from typing import Dict, Any, AsyncGenerator


def create_service():
    """
    Create A2A service handlers for PAR.

    PAR will call this to get custom handlers for A2A communication.

    Returns:
        Dict with 'custom_handlers' and 'streaming_handlers'
    """

    async def handle_chat_request(parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle incoming A2A chat request (non-streaming).

        Supports both formats:
        - Legacy: parameters = {"message": "..."}
        - A2A: parameters = {"params": {"message": "..."}, "skill": "chat"}

        Returns:
            Dict with success, result, and metadata
        """
        # Extract message from A2A or legacy format
        if "params" in parameters and isinstance(parameters.get("params"), dict):
            inner_params = parameters["params"]
            message = inner_params.get("message") or inner_params.get("query", "")
        else:
            message = parameters.get("message", "")

        if not message:
            return {
                "success": False,
                "error": "Missing required parameter: message"
            }

        # Echo the message back
        response = f"Echo: {message}"

        return {
            "success": True,
            "result": response,
            "metadata": {
                "agent": "echo-streamer",
                "version": "1.0.0"
            }
        }

    async def handle_health_check(parameters: Dict[str, str]) -> Dict[str, Any]:
        """Handle health check request."""
        return {
            "success": True,
            "result": "healthy",
            "metadata": {
                "service": "Echo Streamer Agent",
                "version": "1.0.0",
                "streaming_enabled": True
            }
        }

    async def stream_chat_request(parameters: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Handle streaming A2A chat request.

        Yields events word by word for real-time streaming demonstration.

        Supports both formats:
        - Legacy: parameters = {"message": "..."}
        - A2A: parameters = {"params": {"message": "..."}, "skill": "chat"}
        """
        # Extract message from A2A or legacy format
        if "params" in parameters and isinstance(parameters.get("params"), dict):
            inner_params = parameters["params"]
            message = inner_params.get("message") or inner_params.get("query", "")
        else:
            message = parameters.get("message", "")

        if not message:
            yield {"event": "error", "data": {"error": "Missing required parameter: message"}}
            return

        # Stream "Echo: " first
        yield {"event": "content", "data": {"content": "Echo: "}}
        await asyncio.sleep(0.1)  # Small delay for streaming effect

        # Stream each word with a small delay
        words = message.split()
        for i, word in enumerate(words):
            # Add space before word (except first)
            if i > 0:
                yield {"event": "content", "data": {"content": " "}}

            yield {"event": "content", "data": {"content": word}}
            await asyncio.sleep(0.1)  # 100ms delay between words

        # Complete event
        yield {
            "event": "complete",
            "data": {
                "agent": "echo-streamer",
                "version": "1.0.0",
                "total_words": len(words)
            }
        }

    # Return handler dictionary
    # PAR will map these to A2A actions
    return {
        "custom_handlers": {
            "chat": handle_chat_request,
            "echo": handle_chat_request,  # Alias
            "health": handle_health_check,
        },
        "streaming_handlers": {
            "chat": stream_chat_request,
            "echo": stream_chat_request,  # Alias
        }
    }
