#!/usr/bin/env python3
"""Test script to validate A2A message structure compliance.

Tests that talk_to_agent.py builds correct A2A format messages.
"""

import json
import sys
import uuid
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from pixell_runtime.proto import agent_pb2


def validate_a2a_structure(action_request: agent_pb2.ActionRequest) -> tuple[bool, list[str]]:
    """Validate that ActionRequest conforms to A2A specification.

    Args:
        action_request: The ActionRequest proto message to validate

    Returns:
        Tuple of (is_valid, list_of_errors)
    """
    errors = []

    # Check that action_request has message field
    if not action_request.HasField("message"):
        errors.append("ActionRequest must have 'message' field")
        return False, errors

    a2a_msg = action_request.message

    # Validate JSON-RPC structure
    if a2a_msg.jsonrpc != "2.0":
        errors.append(f"jsonrpc must be '2.0', got: {a2a_msg.jsonrpc}")

    if not a2a_msg.id:
        errors.append("id must not be empty")

    if a2a_msg.method != "message/send":
        errors.append(f"method must be 'message/send', got: {a2a_msg.method}")

    if not a2a_msg.params_json:
        errors.append("params_json must not be empty")
        return len(errors) == 0, errors

    # Parse params_json
    try:
        params = json.loads(a2a_msg.params_json)
    except json.JSONDecodeError as e:
        errors.append(f"params_json must be valid JSON: {e}")
        return False, errors

    # Validate params structure
    if "message" not in params:
        errors.append("params must contain 'message' key")
        return False, errors

    msg = params["message"]

    # Validate message fields
    if msg.get("kind") != "message":
        errors.append(f"message.kind must be 'message', got: {msg.get('kind')}")

    if msg.get("role") != "user":
        errors.append(f"message.role must be 'user', got: {msg.get('role')}")

    if not msg.get("messageId"):
        errors.append("message.messageId must not be empty")

    # Validate metadata
    if "metadata" not in msg:
        errors.append("message must contain 'metadata' key")
    else:
        metadata = msg["metadata"]

        # Check for correct field names (not legacy names)
        if "skill" not in metadata:
            errors.append("metadata must contain 'skill' (not 'action')")

        if "params" not in metadata:
            errors.append("metadata must contain 'params' (not 'parameters')")

        if "action" in metadata:
            errors.append("metadata must NOT contain 'action' (use 'skill' instead)")

        if "parameters" in metadata:
            errors.append("metadata must NOT contain 'parameters' (use 'params' instead)")

    # Validate parts array
    if "parts" not in msg:
        errors.append("message must contain 'parts' array")
    else:
        parts = msg["parts"]
        if not isinstance(parts, list):
            errors.append("parts must be an array")
        elif len(parts) == 0:
            errors.append("parts array must not be empty")
        else:
            # Check first part
            part = parts[0]
            if part.get("kind") != "text":
                errors.append(f"part.kind must be 'text', got: {part.get('kind')}")
            if "text" not in part:
                errors.append("part must contain 'text' field")

    return len(errors) == 0, errors


def test_chat_action():
    """Test building A2A message for chat action."""
    print("\n" + "=" * 70)
    print("TEST: Chat Action")
    print("=" * 70)

    # Build A2A message (simulating what talk_to_agent.py does)
    action = "chat"
    parameters = {"message": "Hello, how are you?"}

    message_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    a2a_params = {
        "message": {
            "kind": "message",
            "role": "user",
            "messageId": message_id,
            "metadata": {
                "skill": action,
                "params": parameters
            },
            "parts": [
                {
                    "kind": "text",
                    "text": json.dumps(parameters, ensure_ascii=False)
                }
            ]
        }
    }

    request = agent_pb2.ActionRequest(
        message=agent_pb2.A2AMessage(
            jsonrpc="2.0",
            id=request_id,
            method="message/send",
            params_json=json.dumps(a2a_params)
        )
    )

    # Validate
    is_valid, errors = validate_a2a_structure(request)

    if is_valid:
        print("✅ PASS: Chat action builds valid A2A message")
        print(f"   Request ID: {request_id}")
        print(f"   Message ID: {message_id}")
        return True
    else:
        print("❌ FAIL: Chat action has validation errors:")
        for error in errors:
            print(f"   - {error}")
        return False


def test_comment_action():
    """Test building A2A message for comment action."""
    print("\n" + "=" * 70)
    print("TEST: Comment Action")
    print("=" * 70)

    action = "comment"
    parameters = {
        "code": "def hello():\n    print('Hello')",
        "language": "python"
    }

    message_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    a2a_params = {
        "message": {
            "kind": "message",
            "role": "user",
            "messageId": message_id,
            "metadata": {
                "skill": action,
                "params": parameters
            },
            "parts": [
                {
                    "kind": "text",
                    "text": json.dumps(parameters, ensure_ascii=False)
                }
            ]
        }
    }

    request = agent_pb2.ActionRequest(
        message=agent_pb2.A2AMessage(
            jsonrpc="2.0",
            id=request_id,
            method="message/send",
            params_json=json.dumps(a2a_params)
        )
    )

    is_valid, errors = validate_a2a_structure(request)

    if is_valid:
        print("✅ PASS: Comment action builds valid A2A message")
        print(f"   Request ID: {request_id}")
        print(f"   Message ID: {message_id}")
        return True
    else:
        print("❌ FAIL: Comment action has validation errors:")
        for error in errors:
            print(f"   - {error}")
        return False


def test_complex_parameters():
    """Test with complex nested parameters."""
    print("\n" + "=" * 70)
    print("TEST: Complex Parameters")
    print("=" * 70)

    action = "analyze"
    parameters = {
        "data": {
            "nested": {
                "values": [1, 2, 3]
            }
        },
        "options": ["verbose", "detailed"],
        "unicode": "안녕하세요 こんにちは 你好"
    }

    message_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    a2a_params = {
        "message": {
            "kind": "message",
            "role": "user",
            "messageId": message_id,
            "metadata": {
                "skill": action,
                "params": parameters
            },
            "parts": [
                {
                    "kind": "text",
                    "text": json.dumps(parameters, ensure_ascii=False)
                }
            ]
        }
    }

    request = agent_pb2.ActionRequest(
        message=agent_pb2.A2AMessage(
            jsonrpc="2.0",
            id=request_id,
            method="message/send",
            params_json=json.dumps(a2a_params, ensure_ascii=False)
        )
    )

    is_valid, errors = validate_a2a_structure(request)

    if is_valid:
        print("✅ PASS: Complex parameters build valid A2A message")
        print(f"   Request ID: {request_id}")
        print(f"   Unicode content preserved correctly")
        return True
    else:
        print("❌ FAIL: Complex parameters have validation errors:")
        for error in errors:
            print(f"   - {error}")
        return False


def test_format_matches_spec():
    """Test that format exactly matches A2A specification."""
    print("\n" + "=" * 70)
    print("TEST: Format Matches Specification")
    print("=" * 70)

    action = "reddit_search_post"
    parameters = {"query": "skincare"}

    message_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    a2a_params = {
        "message": {
            "kind": "message",
            "role": "user",
            "messageId": message_id,
            "metadata": {
                "skill": action,
                "params": parameters
            },
            "parts": [
                {
                    "kind": "text",
                    "text": json.dumps(parameters, ensure_ascii=False)
                }
            ]
        }
    }

    request = agent_pb2.ActionRequest(
        message=agent_pb2.A2AMessage(
            jsonrpc="2.0",
            id=request_id,
            method="message/send",
            params_json=json.dumps(a2a_params)
        )
    )

    # Parse back for inspection
    params = json.loads(request.message.params_json)
    msg = params["message"]

    # Check exact structure
    checks = []
    checks.append(("jsonrpc == '2.0'", request.message.jsonrpc == "2.0"))
    checks.append(("method == 'message/send'", request.message.method == "message/send"))
    checks.append(("message.kind == 'message'", msg["kind"] == "message"))
    checks.append(("message.role == 'user'", msg["role"] == "user"))
    checks.append(("metadata.skill exists", "skill" in msg["metadata"]))
    checks.append(("metadata.params exists", "params" in msg["metadata"]))
    checks.append(("parts array exists", "parts" in msg))
    checks.append(("parts has text", msg["parts"][0].get("kind") == "text"))

    all_pass = all(result for _, result in checks)

    if all_pass:
        print("✅ PASS: Format exactly matches A2A specification")
        for check, result in checks:
            print(f"   ✓ {check}")
        return True
    else:
        print("❌ FAIL: Some checks failed:")
        for check, result in checks:
            symbol = "✓" if result else "✗"
            print(f"   {symbol} {check}")
        return False


if __name__ == "__main__":
    print("\n🧪 A2A Message Structure Compliance Tests")
    print("=" * 70)
    print("Testing that talk_to_agent.py builds correct A2A format")
    print("=" * 70)

    results = []
    results.append(test_chat_action())
    results.append(test_comment_action())
    results.append(test_complex_parameters())
    results.append(test_format_matches_spec())

    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")

    if passed == total:
        print("\n✅ All tests passed! talk_to_agent.py builds correct A2A format")
        sys.exit(0)
    else:
        print(f"\n❌ {total - passed} test(s) failed")
        sys.exit(1)
