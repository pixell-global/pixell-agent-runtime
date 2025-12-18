"""
A2A HTTP Wrapper - Converts handlers dict to ASGI app for A2A JSON-RPC over HTTP.

When an agent's create_service() returns {"custom_handlers": {...}}, this module
provides an ASGI-compatible Starlette app that wraps those handlers in a JSON-RPC
interface conforming to the A2A protocol.

Supports both synchronous message/send and streaming via SSE.
"""
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from typing import Dict, Any, Callable, Optional, AsyncIterator
import asyncio
import inspect
import json
import uuid
import structlog

logger = structlog.get_logger("a2a.http_wrapper")


def create_a2a_http_app(
    handlers: Dict[str, Callable],
    agent_name: str = "Agent",
    agent_version: str = "1.0.0",
    agent_description: Optional[str] = None,
    streaming_handlers: Optional[Dict[str, Callable]] = None,
) -> Starlette:
    """
    Create an ASGI app that exposes handlers via A2A JSON-RPC over HTTP.

    Args:
        handlers: Dict mapping action names to async handler functions.
                  Each handler should accept a dict of parameters and return a dict.
        agent_name: Name for the agent card
        agent_version: Version for the agent card
        agent_description: Optional description for the agent card
        streaming_handlers: Optional dict of handlers that return AsyncIterator for streaming.
                           If provided, streaming will be enabled in the agent card.

    Returns:
        Starlette ASGI application
    """
    # Determine if streaming is supported
    has_streaming = streaming_handlers is not None and len(streaming_handlers) > 0

    async def agent_card(request: Request) -> JSONResponse:
        """Return A2A agent card for protocol discovery."""
        skills = [
            {
                "id": action,
                "name": action,
                "description": f"Execute {action} action",
                "inputModes": ["text"],
                "outputModes": ["text"]
            }
            for action in handlers.keys()
        ]

        # Add streaming skills if available
        if streaming_handlers:
            for action in streaming_handlers.keys():
                if action not in handlers:  # Don't duplicate
                    skills.append({
                        "id": action,
                        "name": action,
                        "description": f"Execute {action} action (streaming)",
                        "inputModes": ["text"],
                        "outputModes": ["text"]
                    })

        return JSONResponse({
            "name": agent_name,
            "description": agent_description or f"{agent_name} A2A Agent",
            "version": agent_version,
            "capabilities": {
                "streaming": has_streaming,
                "pushNotifications": False
            },
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": skills
        })

    async def handle_jsonrpc(request: Request) -> JSONResponse:
        """Handle non-streaming A2A JSON-RPC requests."""
        try:
            body = await request.json()
        except Exception as e:
            logger.warning("Failed to parse JSON-RPC request", error=str(e))
            return JSONResponse({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None
            })

        jsonrpc_id = body.get("id")
        method = body.get("method", "")
        params = body.get("params", {})

        logger.debug(
            "Received A2A JSON-RPC request",
            method=method,
            jsonrpc_id=jsonrpc_id
        )

        # Extract skill/action and parameters from the request
        skill, action_params = _extract_action_params(method, params)

        # Find handler
        handler = handlers.get(skill)
        if not handler:
            # Try fallback handlers
            handler = handlers.get("chat") or handlers.get("default")
            if handler:
                logger.debug(
                    "Using fallback handler",
                    requested_skill=skill,
                    fallback="chat" if "chat" in handlers else "default"
                )

        if not handler:
            logger.warning("No handler found for skill", skill=skill, available=list(handlers.keys()))
            return JSONResponse({
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {skill}"},
                "id": jsonrpc_id
            })

        # Call handler
        try:
            result = await handler(action_params)
            logger.debug(
                "Handler completed successfully",
                skill=skill,
                result_keys=list(result.keys()) if isinstance(result, dict) else type(result).__name__
            )
            return JSONResponse({
                "jsonrpc": "2.0",
                "result": result,
                "id": jsonrpc_id
            })
        except Exception as e:
            logger.error("Handler error", skill=skill, error=str(e), exc_info=True)
            return JSONResponse({
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": str(e)},
                "id": jsonrpc_id
            })

    async def handle_streaming(request: Request):
        """Handle streaming A2A requests via SSE."""
        if not streaming_handlers:
            return JSONResponse({
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": "Streaming not supported"},
                "id": None
            }, status_code=400)

        try:
            body = await request.json()
        except Exception as e:
            logger.warning("Failed to parse streaming request", error=str(e))
            return JSONResponse({
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None
            })

        jsonrpc_id = body.get("id", str(uuid.uuid4()))
        method = body.get("method", "")
        params = body.get("params", {})

        logger.info(
            "Received streaming A2A request",
            method=method,
            jsonrpc_id=jsonrpc_id
        )

        # Extract skill/action and parameters
        skill, action_params = _extract_action_params(method, params)

        # Find streaming handler
        handler = streaming_handlers.get(skill)
        if not handler:
            handler = streaming_handlers.get("chat") or streaming_handlers.get("default")

        if not handler:
            logger.warning("No streaming handler found", skill=skill)
            return JSONResponse({
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Streaming method not found: {skill}"},
                "id": jsonrpc_id
            })

        async def generate_sse() -> AsyncIterator[bytes]:
            """Generate SSE events from handler."""
            task_id = str(uuid.uuid4())

            try:
                # Check if handler returns an async generator
                result = handler(action_params)

                if inspect.isasyncgen(result):
                    # Handler is an async generator - stream events
                    async for event in result:
                        sse_event = _format_sse_event(event, task_id, jsonrpc_id)
                        yield sse_event.encode('utf-8')
                else:
                    # Handler returns awaitable - get result and send as single event
                    if inspect.isawaitable(result):
                        result = await result

                    # Send result as content event
                    content_event = {
                        "jsonrpc": "2.0",
                        "method": "tasks/status",
                        "params": {
                            "taskId": task_id,
                            "status": {
                                "state": "completed",
                                "message": {
                                    "role": "agent",
                                    "parts": [{"type": "text", "text": json.dumps(result) if isinstance(result, dict) else str(result)}]
                                }
                            }
                        }
                    }
                    yield f"data: {json.dumps(content_event)}\n\n".encode('utf-8')

                # Send done event
                done_event = {
                    "jsonrpc": "2.0",
                    "method": "tasks/status",
                    "params": {
                        "taskId": task_id,
                        "status": {"state": "completed", "final": True}
                    }
                }
                yield f"data: {json.dumps(done_event)}\n\n".encode('utf-8')

            except Exception as e:
                logger.error("Streaming handler error", skill=skill, error=str(e), exc_info=True)
                error_event = {
                    "jsonrpc": "2.0",
                    "method": "tasks/status",
                    "params": {
                        "taskId": task_id,
                        "status": {
                            "state": "failed",
                            "error": {"code": -32000, "message": str(e)}
                        }
                    }
                }
                yield f"data: {json.dumps(error_event)}\n\n".encode('utf-8')

        return StreamingResponse(
            generate_sse(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    async def health(request: Request) -> JSONResponse:
        """Health check endpoint."""
        return JSONResponse({"ok": True, "status": "healthy"})

    routes = [
        # Agent card discovery endpoints
        Route("/.well-known/agent.json", agent_card, methods=["GET"]),
        Route("/a2a/.well-known/agent.json", agent_card, methods=["GET"]),
        # JSON-RPC endpoints (non-streaming)
        Route("/", handle_jsonrpc, methods=["POST"]),
        Route("/a2a", handle_jsonrpc, methods=["POST"]),
        # Streaming endpoint
        Route("/stream", handle_streaming, methods=["POST"]),
        Route("/a2a/stream", handle_streaming, methods=["POST"]),
        # Health check
        Route("/health", health, methods=["GET"]),
    ]

    app = Starlette(routes=routes)
    logger.info(
        "Created A2A HTTP wrapper app",
        agent_name=agent_name,
        handlers=list(handlers.keys()),
        streaming_handlers=list(streaming_handlers.keys()) if streaming_handlers else [],
        streaming_enabled=has_streaming
    )
    return app


def _format_sse_event(event: Dict[str, Any], task_id: str, jsonrpc_id: str) -> str:
    """Format an event dict as SSE data line."""
    # Handle different event formats
    event_type = event.get("event") or event.get("type", "content")
    data = event.get("data", event)

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except:
            pass

    # Format as A2A task status event
    if event_type in ("content", "CONTENT"):
        content = data.get("content", data) if isinstance(data, dict) else data
        sse_payload = {
            "jsonrpc": "2.0",
            "method": "tasks/status",
            "params": {
                "taskId": task_id,
                "status": {
                    "state": "working",
                    "message": {
                        "role": "agent",
                        "parts": [{"type": "text", "text": str(content)}]
                    }
                }
            }
        }
    elif event_type in ("thinking", "THINKING"):
        content = data.get("content", data) if isinstance(data, dict) else data
        sse_payload = {
            "jsonrpc": "2.0",
            "method": "tasks/status",
            "params": {
                "taskId": task_id,
                "status": {
                    "state": "working",
                    "message": {
                        "role": "agent",
                        "metadata": {"thinking": True},
                        "parts": [{"type": "text", "text": str(content)}]
                    }
                }
            }
        }
    elif event_type in ("complete", "COMPLETE", "done", "DONE"):
        sse_payload = {
            "jsonrpc": "2.0",
            "method": "tasks/status",
            "params": {
                "taskId": task_id,
                "status": {
                    "state": "completed",
                    "metadata": data if isinstance(data, dict) else {}
                }
            }
        }
    elif event_type in ("error", "ERROR"):
        error_msg = data.get("error", str(data)) if isinstance(data, dict) else str(data)
        sse_payload = {
            "jsonrpc": "2.0",
            "method": "tasks/status",
            "params": {
                "taskId": task_id,
                "status": {
                    "state": "failed",
                    "error": {"code": -32000, "message": error_msg}
                }
            }
        }
    else:
        # Generic event
        sse_payload = {
            "jsonrpc": "2.0",
            "method": "tasks/status",
            "params": {
                "taskId": task_id,
                "status": {
                    "state": "working",
                    "message": {
                        "role": "agent",
                        "parts": [{"type": "text", "text": json.dumps(data) if isinstance(data, dict) else str(data)}]
                    }
                }
            }
        }

    return f"data: {json.dumps(sse_payload)}\n\n"


def _extract_action_params(method: str, params: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """
    Extract the skill/action name and parameters from A2A request.

    Supports multiple A2A message formats:
    1. message/send with metadata.skill and metadata.params
    2. message/send with parts containing text
    3. tasks/sendSubscribe for streaming
    4. Direct method calls like "chat", "health", etc.

    Args:
        method: The JSON-RPC method name
        params: The JSON-RPC params dict

    Returns:
        Tuple of (skill_name, action_params)
    """
    if method in ("message/send", "tasks/send", "tasks/sendSubscribe"):
        message = params.get("message", {})
        metadata = message.get("metadata", {})
        skill = metadata.get("skill", "chat")
        action_params = metadata.get("params", {})

        # Also check message parts for text content
        parts = message.get("parts", [])
        for part in parts:
            if part.get("type") == "text" and "message" not in action_params:
                action_params["message"] = part.get("text", "")

        return skill, action_params
    else:
        # Direct method call (e.g., "chat", "health")
        # or namespaced method (e.g., "agent/chat")
        skill = params.get("skill", method.split("/")[-1] if "/" in method else method)
        action_params = params.get("params", params)
        return skill, action_params
