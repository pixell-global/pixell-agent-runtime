#!/usr/bin/env python3
"""Interactive CLI to talk to deployed A2A agents with streaming support.

Usage:
    python scripts/talk_to_agent.py                    # Uses default agent
    python scripts/talk_to_agent.py -a <agent-id>     # Specific agent
    python scripts/talk_to_agent.py -v                 # Verbose mode
    python scripts/talk_to_agent.py --no-stream       # Disable streaming
"""

import argparse
import json
import sys
import uuid
from typing import Optional, Tuple

try:
    import readline  # noqa: F401 - enables input history
except ImportError:
    pass  # readline not available on Windows

import httpx

DEFAULT_HOST = "par.pixell.global"
DEFAULT_AGENT_ID = "ed8784f3-b602-481c-8701-3b6406c8fd98"  # PAF Core Agent


def get_agent_info(host: str, agent_id: str) -> Tuple[str, str, bool, bool]:
    """Fetch agent card to get agent name, description, and capabilities.

    Returns:
        Tuple of (agent_name, agent_description, is_connected, supports_streaming)
    """
    # Agent card is served at /a2a/.well-known/agent.json (via a2a.sock)
    url = f"https://{host}/agents/{agent_id}/a2a/.well-known/agent.json"

    try:
        with httpx.Client(timeout=10.0, verify=False) as client:
            response = client.get(url)
            if response.status_code == 502:
                return "Agent Not Running", f"502 Bad Gateway - agent may not be started", False, False
            elif response.status_code == 404:
                return "Agent Not Found", f"404 Not Found - check agent ID", False, False
            response.raise_for_status()
            card = response.json()
            name = card.get("name", "Unknown Agent")
            description = card.get("description", "")
            # Check if agent supports streaming
            capabilities = card.get("capabilities", {})
            supports_streaming = capabilities.get("streaming", False)
            return name, description, True, supports_streaming
    except httpx.ConnectError as e:
        return "Connection Failed", f"Cannot connect to {host}: {e}", False, False
    except httpx.TimeoutException:
        return "Connection Timeout", f"Timeout connecting to {host}", False, False
    except Exception as e:
        return "Unknown Agent", str(e), False, False


def build_message(text: str, message_id: Optional[str] = None) -> dict:
    """Build A2A JSON-RPC message in the format agents expect."""
    msg_id = message_id or f"msg-{uuid.uuid4().hex[:8]}"
    return {
        "jsonrpc": "2.0",
        "id": f"req-{uuid.uuid4().hex[:8]}",
        "method": "message/send",
        "params": {
            "message": {
                "messageId": msg_id,
                "role": "user",
                "metadata": {
                    "params": {
                        "query": text
                    }
                },
                "parts": [
                    {"type": "text", "text": text}
                ]
            }
        }
    }


def build_streaming_message(text: str, message_id: Optional[str] = None) -> dict:
    """Build A2A JSON-RPC message for streaming (message/stream)."""
    msg_id = message_id or f"msg-{uuid.uuid4().hex[:8]}"
    return {
        "jsonrpc": "2.0",
        "id": f"req-{uuid.uuid4().hex[:8]}",
        "method": "message/stream",
        "params": {
            "message": {
                "messageId": msg_id,
                "role": "user",
                "parts": [
                    {"type": "text", "text": text}
                ]
            }
        }
    }


def extract_response_text(response_json: dict) -> str:
    """Extract text from A2A JSON-RPC response."""
    try:
        # Check for JSON-RPC error first
        if "error" in response_json:
            error = response_json["error"]
            code = error.get("code", "?")
            message = error.get("message", "Unknown error")
            return f"[Error {code}]: {message}"

        # Handle A2A SSE streaming format: params.status.message.parts
        if "method" in response_json and response_json.get("method") == "tasks/status":
            params = response_json.get("params", {})
            status = params.get("status", {})
            state = status.get("state", "")

            # Skip thinking events and status-only events
            message = status.get("message", {})
            if message.get("metadata", {}).get("thinking"):
                return ""  # Skip thinking events in display

            # Extract text from message parts
            parts = message.get("parts", [])
            texts = []
            for part in parts:
                if part.get("type") == "text":
                    texts.append(part.get("text", ""))

            if texts:
                return "".join(texts)

            # Status-only event (completed, failed, etc)
            if state in ("completed", "failed") and not message:
                return f"[Status: {state}]"

            return ""

        result = response_json.get("result", {})

        # Handle streaming events
        if "kind" in result:
            if result["kind"] == "status-update":
                state = result.get("status", {}).get("state", "unknown")
                return f"[Status: {state}]"
            elif result["kind"] == "artifact-update":
                artifact = result.get("artifact", {})
                parts = artifact.get("parts", [])
                texts = []
                for part in parts:
                    if part.get("kind") == "text" or part.get("type") == "text":
                        texts.append(part.get("text", ""))
                return "".join(texts)

        # Handle regular message response
        parts = result.get("parts", [])
        texts = []
        for part in parts:
            if part.get("kind") == "text" or part.get("type") == "text":
                texts.append(part.get("text", ""))

        if texts:
            return "".join(texts)

        # Fallback: return raw result if not empty
        if result:
            return json.dumps(result, indent=2)

        return ""
    except Exception as e:
        return f"[Parse error: {e}]"


def send_message_sync(host: str, agent_id: str, text: str, verbose: bool = False) -> str:
    """Send message and return response (non-streaming)."""
    url = f"https://{host}/agents/{agent_id}/a2a"
    payload = build_message(text)

    if verbose:
        print(f"\n>>> POST {url}")
        print(f">>> {json.dumps(payload, indent=2)}")

    try:
        with httpx.Client(timeout=60.0, verify=False) as client:
            response = client.post(url, json=payload)

            if response.status_code >= 400:
                return f"[HTTP Error {response.status_code}]: {response.text[:200]}"

            result = response.json()

            if verbose:
                print(f"<<< {json.dumps(result, indent=2)}")

            return extract_response_text(result) or "[Empty response]"

    except httpx.ConnectError as e:
        return f"[Connection Error]: Cannot connect to {host}: {e}"
    except httpx.TimeoutException:
        return f"[Timeout]: Request timed out"
    except httpx.RequestError as e:
        return f"[Request Error]: {e}"
    except json.JSONDecodeError as e:
        return f"[JSON Error]: {e}"


def send_message_streaming(host: str, agent_id: str, text: str, verbose: bool = False) -> str:
    """Send message with streaming and return accumulated response."""
    # A2A SDK serves SSE through the same endpoint as JSON-RPC (not /stream)
    url = f"https://{host}/agents/{agent_id}/a2a"
    payload = build_streaming_message(text)

    if verbose:
        print(f"\n>>> POST {url} (streaming)")
        print(f">>> {json.dumps(payload, indent=2)}")

    accumulated_text = []
    got_any_response = False

    try:
        with httpx.Client(timeout=120.0, verify=False) as client:
            with client.stream("POST", url, json=payload, headers={"Accept": "text/event-stream"}) as response:
                # Check for HTTP errors first
                if response.status_code >= 400:
                    error_body = response.read().decode('utf-8', errors='replace')[:300]
                    return f"[HTTP Error {response.status_code}]: {error_body}"

                # Check if actually streaming
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" not in content_type:
                    # Not streaming, read as regular JSON
                    body = response.read()
                    if verbose:
                        print(f"<<< Content-Type: {content_type}")
                        print(f"<<< Body: {body[:500]}")

                    if not body:
                        return "[Empty response from server]"

                    try:
                        result = json.loads(body)
                        if verbose:
                            print(f"<<< (non-stream) {json.dumps(result, indent=2)}")
                        text_result = extract_response_text(result)
                        return text_result if text_result else "[Empty response]"
                    except json.JSONDecodeError:
                        return f"[Invalid JSON]: {body.decode('utf-8', errors='replace')[:200]}"

                # Process SSE stream
                buffer = ""
                for chunk in response.iter_text():
                    buffer += chunk
                    got_any_response = True

                    # Process complete lines
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        line = line.strip()

                        if not line:
                            continue

                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if data == "[DONE]":
                                continue

                            try:
                                event = json.loads(data)
                                if verbose:
                                    print(f"<<< {json.dumps(event, indent=2)}")

                                text_part = extract_response_text(event)
                                if text_part and not text_part.startswith("[Status"):
                                    # Print streaming text incrementally
                                    print(text_part, end="", flush=True)
                                    accumulated_text.append(text_part)
                                elif text_part.startswith("[Error"):
                                    # Print errors
                                    print(text_part, end="", flush=True)
                                    accumulated_text.append(text_part)
                                elif verbose and text_part.startswith("[Status"):
                                    print(text_part)

                            except json.JSONDecodeError:
                                if verbose:
                                    print(f"<<< (raw) {data}")

                print()  # Newline after streaming

                if accumulated_text:
                    return "".join(accumulated_text)
                elif got_any_response:
                    return "[No text in response]"
                else:
                    return "[No response from server]"

    except httpx.ConnectError as e:
        return f"[Connection Error]: Cannot connect to {host}: {e}"
    except httpx.TimeoutException:
        return f"[Timeout]: Request timed out"
    except httpx.RequestError as e:
        return f"[Request Error]: {e}"
    except Exception as e:
        return f"[Error]: {type(e).__name__}: {e}"


def send_message(host: str, agent_id: str, text: str, stream: bool = True, verbose: bool = False) -> str:
    """Send message and return response."""
    if stream:
        return send_message_streaming(host, agent_id, text, verbose)
    else:
        return send_message_sync(host, agent_id, text, verbose)


def main():
    parser = argparse.ArgumentParser(
        description="Interactive CLI to talk to deployed A2A agents"
    )
    parser.add_argument(
        "-a", "--agent-id",
        default=DEFAULT_AGENT_ID,
        help=f"Agent ID to connect to (default: {DEFAULT_AGENT_ID})"
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Host to connect to (default: {DEFAULT_HOST})"
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Disable streaming (use message/send instead of tasks/sendSubscribe)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show request/response details"
    )

    args = parser.parse_args()

    # Fetch agent info
    print(f"Connecting to agent...")
    agent_name, agent_description, is_connected, supports_streaming = get_agent_info(args.host, args.agent_id)

    # Determine actual streaming mode: --no-stream forces off, otherwise use agent capability
    use_streaming = supports_streaming and not args.no_stream

    print(f"\n{'=' * 50}")
    print(f"Agent: {agent_name}")
    print(f"ID: {args.agent_id}")
    if agent_description:
        print(f"Info: {agent_description}")
    print(f"Host: {args.host}")
    print(f"Streaming: {'enabled' if use_streaming else 'disabled'}")
    print(f"Status: {'Connected' if is_connected else 'NOT CONNECTED'}")
    print(f"{'=' * 50}")

    if not is_connected:
        print(f"\nWARNING: Could not connect to agent. Messages will likely fail.")
        print(f"Check that the agent is running on EC2 with socket mode enabled.\n")

    print("Type 'exit' or 'quit' to end the conversation\n")

    while True:
        try:
            user_input = input("You: ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "q"):
                print("Goodbye!")
                break

            print("\nAgent: ", end="" if use_streaming else "")
            response = send_message(
                host=args.host,
                agent_id=args.agent_id,
                text=user_input,
                stream=use_streaming,
                verbose=args.verbose
            )

            # For non-streaming, print the response
            if not use_streaming:
                print(response)

            print()  # Extra newline for readability

        except KeyboardInterrupt:
            print("\n\nInterrupted. Goodbye!")
            break
        except EOFError:
            print("\n\nGoodbye!")
            break


if __name__ == "__main__":
    sys.exit(main() or 0)
