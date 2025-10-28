"""Unit tests for A2A server Invoke() method.

Tests both A2A JSON-RPC 2.0 format and legacy format parsing,
including critical subprocess forwarding normalization.
"""

import json
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import grpc

from pixell_runtime.a2a.server import AgentServiceImpl
from pixell_runtime.proto import agent_pb2


class TestA2AFormatParsing:
    """Test A2A JSON-RPC 2.0 format parsing."""

    @pytest.mark.asyncio
    async def test_parse_a2a_chat_action(self):
        """Test parsing A2A format with chat action."""
        # Create service with mock custom handler
        service = AgentServiceImpl()
        mock_handler = AsyncMock(return_value="Hello from chat handler")
        service.custom_handlers = {"chat": mock_handler}

        # Build A2A format request
        message_id = "msg-123"
        request_id = "req-456"
        params = {"message": "Hello agent"}

        a2a_params = {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": message_id,
                "metadata": {
                    "skill": "chat",
                    "params": params
                },
                "parts": [
                    {
                        "kind": "text",
                        "text": json.dumps(params, ensure_ascii=False)
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

        # Invoke
        context = Mock()
        response = await service.Invoke(request, context)

        # Assertions
        assert response.success is True
        assert response.error == ""
        assert response.request_id == request_id
        assert "Hello from chat handler" in response.result

        # Verify handler was called with correct params
        mock_handler.assert_called_once_with(params)

    @pytest.mark.asyncio
    async def test_parse_a2a_comment_action(self):
        """Test parsing A2A format with comment action."""
        service = AgentServiceImpl()
        mock_handler = AsyncMock(return_value="# Comment added")
        service.custom_handlers = {"comment": mock_handler}

        # Build A2A request with comment action
        params = {
            "code": "def hello():\n    print('hi')",
            "language": "python"
        }

        a2a_params = {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "msg-789",
                "metadata": {
                    "skill": "comment",
                    "params": params
                },
                "parts": [{"kind": "text", "text": json.dumps(params)}]
            }
        }

        request = agent_pb2.ActionRequest(
            message=agent_pb2.A2AMessage(
                jsonrpc="2.0",
                id="req-789",
                method="message/send",
                params_json=json.dumps(a2a_params)
            )
        )

        context = Mock()
        response = await service.Invoke(request, context)

        assert response.success is True
        assert response.request_id == "req-789"
        mock_handler.assert_called_once_with(params)

    @pytest.mark.asyncio
    async def test_parse_a2a_complex_parameters(self):
        """Test A2A format with complex nested parameters."""
        service = AgentServiceImpl()
        mock_handler = AsyncMock(return_value="Processed")
        service.custom_handlers = {"analyze": mock_handler}

        # Complex nested params
        params = {
            "data": {
                "nested": {
                    "values": [1, 2, 3]
                }
            },
            "options": ["verbose", "detailed"],
            "unicode": "안녕하세요 こんにちは"
        }

        a2a_params = {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "msg-complex",
                "metadata": {
                    "skill": "analyze",
                    "params": params
                },
                "parts": [{"kind": "text", "text": json.dumps(params, ensure_ascii=False)}]
            }
        }

        request = agent_pb2.ActionRequest(
            message=agent_pb2.A2AMessage(
                jsonrpc="2.0",
                id="req-complex",
                method="message/send",
                params_json=json.dumps(a2a_params, ensure_ascii=False)
            )
        )

        context = Mock()
        response = await service.Invoke(request, context)

        assert response.success is True
        mock_handler.assert_called_once_with(params)

    @pytest.mark.asyncio
    async def test_a2a_missing_metadata(self):
        """Test A2A format with missing metadata."""
        service = AgentServiceImpl()

        # A2A request without metadata
        a2a_params = {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "msg-bad"
                # No metadata!
            }
        }

        request = agent_pb2.ActionRequest(
            message=agent_pb2.A2AMessage(
                jsonrpc="2.0",
                id="req-bad",
                method="message/send",
                params_json=json.dumps(a2a_params)
            )
        )

        context = Mock()
        response = await service.Invoke(request, context)

        # Should fail with no action
        assert response.success is False
        assert "No action/skill specified" in response.error

    @pytest.mark.asyncio
    async def test_a2a_invalid_json(self):
        """Test A2A format with invalid JSON in params_json."""
        service = AgentServiceImpl()

        request = agent_pb2.ActionRequest(
            message=agent_pb2.A2AMessage(
                jsonrpc="2.0",
                id="req-invalid",
                method="message/send",
                params_json="{ invalid json }"
            )
        )

        context = Mock()
        response = await service.Invoke(request, context)

        assert response.success is False
        assert "Invalid JSON" in response.error


class TestLegacyFormatParsing:
    """Test legacy format parsing for backward compatibility."""

    @pytest.mark.asyncio
    async def test_parse_legacy_format(self):
        """Test parsing legacy action/parameters format."""
        service = AgentServiceImpl()
        mock_handler = AsyncMock(return_value="Legacy response")
        service.custom_handlers = {"legacy_action": mock_handler}

        # Legacy format request
        request = agent_pb2.ActionRequest(
            action="legacy_action",
            parameters={"key": "value", "num": "42"},
            request_id="legacy-123"
        )

        context = Mock()
        response = await service.Invoke(request, context)

        assert response.success is True
        assert response.request_id == "legacy-123"
        assert "Legacy response" in response.result

        # Verify handler called with dict params
        mock_handler.assert_called_once()
        call_args = mock_handler.call_args[0][0]
        assert call_args["key"] == "value"

    @pytest.mark.asyncio
    async def test_legacy_format_no_handler(self):
        """Test legacy format with no handler found."""
        service = AgentServiceImpl()
        service.custom_handlers = {}

        request = agent_pb2.ActionRequest(
            action="missing_action",
            parameters={"test": "data"},
            request_id="req-missing"
        )

        context = Mock()
        response = await service.Invoke(request, context)

        assert response.success is False
        assert "No handler found for action: missing_action" in response.error


class TestSubprocessForwarding:
    """Test critical subprocess forwarding with format normalization."""

    @pytest.mark.asyncio
    async def test_a2a_format_normalized_before_forwarding(self):
        """Test A2A format is normalized to legacy before forwarding to subprocess."""
        service = AgentServiceImpl(agent_a2a_port=50053)

        # Build A2A request
        params = {"message": "Test message"}
        a2a_params = {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "msg-forward",
                "metadata": {
                    "skill": "chat",
                    "params": params
                },
                "parts": [{"kind": "text", "text": json.dumps(params)}]
            }
        }

        request = agent_pb2.ActionRequest(
            message=agent_pb2.A2AMessage(
                jsonrpc="2.0",
                id="req-forward",
                method="message/send",
                params_json=json.dumps(a2a_params)
            )
        )

        # Mock the gRPC channel and stub
        mock_response = agent_pb2.ActionResult(
            success=True,
            result="Subprocess handled it",
            error="",
            request_id="req-forward",
            duration_ms=100,
            metadata={}
        )

        mock_stub = AsyncMock()
        mock_stub.Invoke = AsyncMock(return_value=mock_response)

        mock_channel = AsyncMock()
        mock_channel.__aenter__ = AsyncMock(return_value=mock_channel)
        mock_channel.__aexit__ = AsyncMock(return_value=None)

        with patch('grpc.aio.insecure_channel', return_value=mock_channel):
            with patch('pixell_runtime.proto.agent_pb2_grpc.AgentServiceStub', return_value=mock_stub):
                context = Mock()
                response = await service.Invoke(request, context)

        # Verify response
        assert response.success is True
        assert "Subprocess handled it" in response.result

        # CRITICAL: Verify the forwarded request was normalized to legacy format
        mock_stub.Invoke.assert_called_once()
        forwarded_request = mock_stub.Invoke.call_args[0][0]

        # Check it's legacy format, not A2A
        assert forwarded_request.action == "chat"
        assert forwarded_request.request_id == "req-forward"
        assert "message" in forwarded_request.parameters
        assert forwarded_request.parameters["message"] == "Test message"
        # Verify NO A2A message field
        assert not forwarded_request.HasField("message")

    @pytest.mark.asyncio
    async def test_legacy_format_forwarded_as_is(self):
        """Test legacy format is forwarded directly to subprocess."""
        service = AgentServiceImpl(agent_a2a_port=50053)

        # Legacy format request
        request = agent_pb2.ActionRequest(
            action="test_action",
            parameters={"key": "value"},
            request_id="legacy-forward"
        )

        mock_response = agent_pb2.ActionResult(
            success=True,
            result="Handled",
            error="",
            request_id="legacy-forward",
            duration_ms=50,
            metadata={}
        )

        mock_stub = AsyncMock()
        mock_stub.Invoke = AsyncMock(return_value=mock_response)

        mock_channel = AsyncMock()
        mock_channel.__aenter__ = AsyncMock(return_value=mock_channel)
        mock_channel.__aexit__ = AsyncMock(return_value=None)

        with patch('grpc.aio.insecure_channel', return_value=mock_channel):
            with patch('pixell_runtime.proto.agent_pb2_grpc.AgentServiceStub', return_value=mock_stub):
                context = Mock()
                response = await service.Invoke(request, context)

        assert response.success is True

        # Verify forwarded request
        mock_stub.Invoke.assert_called_once()
        forwarded_request = mock_stub.Invoke.call_args[0][0]
        assert forwarded_request.action == "test_action"
        assert forwarded_request.parameters["key"] == "value"

    @pytest.mark.asyncio
    async def test_parameters_stringified_for_forwarding(self):
        """Test parameters are stringified when forwarding (protobuf requirement)."""
        service = AgentServiceImpl(agent_a2a_port=50053)

        # A2A with various param types
        params = {
            "string": "text",
            "number": 42,
            "bool": True,
            "nested": {"key": "value"}
        }

        a2a_params = {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "msg-types",
                "metadata": {
                    "skill": "test",
                    "params": params
                },
                "parts": [{"kind": "text", "text": "test"}]
            }
        }

        request = agent_pb2.ActionRequest(
            message=agent_pb2.A2AMessage(
                jsonrpc="2.0",
                id="req-types",
                method="message/send",
                params_json=json.dumps(a2a_params)
            )
        )

        mock_response = agent_pb2.ActionResult(
            success=True,
            result="OK",
            error="",
            request_id="req-types",
            duration_ms=10,
            metadata={}
        )

        mock_stub = AsyncMock()
        mock_stub.Invoke = AsyncMock(return_value=mock_response)

        mock_channel = AsyncMock()
        mock_channel.__aenter__ = AsyncMock(return_value=mock_channel)
        mock_channel.__aexit__ = AsyncMock(return_value=None)

        with patch('grpc.aio.insecure_channel', return_value=mock_channel):
            with patch('pixell_runtime.proto.agent_pb2_grpc.AgentServiceStub', return_value=mock_stub):
                context = Mock()
                await service.Invoke(request, context)

        # Verify all parameters were stringified
        forwarded_request = mock_stub.Invoke.call_args[0][0]
        for key, value in forwarded_request.parameters.items():
            assert isinstance(value, str), f"Parameter {key} should be string, got {type(value)}"

    @pytest.mark.asyncio
    async def test_subprocess_forwarding_error(self):
        """Test error handling when subprocess forwarding fails."""
        service = AgentServiceImpl(agent_a2a_port=50053)

        params = {"test": "data"}
        a2a_params = {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "msg-err",
                "metadata": {
                    "skill": "test",
                    "params": params
                },
                "parts": [{"kind": "text", "text": "test"}]
            }
        }

        request = agent_pb2.ActionRequest(
            message=agent_pb2.A2AMessage(
                jsonrpc="2.0",
                id="req-err",
                method="message/send",
                params_json=json.dumps(a2a_params)
            )
        )

        # Mock channel to raise exception
        mock_channel = AsyncMock()
        mock_channel.__aenter__ = AsyncMock(side_effect=Exception("Connection failed"))
        mock_channel.__aexit__ = AsyncMock(return_value=None)

        with patch('grpc.aio.insecure_channel', return_value=mock_channel):
            context = Mock()
            response = await service.Invoke(request, context)

        assert response.success is False
        assert "Failed to forward to agent" in response.error


class TestErrorHandling:
    """Test error handling for various edge cases."""

    @pytest.mark.asyncio
    async def test_empty_action(self):
        """Test handling of empty action/skill."""
        service = AgentServiceImpl()

        # A2A with empty skill
        a2a_params = {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "msg-empty",
                "metadata": {
                    "skill": "",
                    "params": {}
                },
                "parts": [{"kind": "text", "text": "test"}]
            }
        }

        request = agent_pb2.ActionRequest(
            message=agent_pb2.A2AMessage(
                jsonrpc="2.0",
                id="req-empty",
                method="message/send",
                params_json=json.dumps(a2a_params)
            )
        )

        context = Mock()
        response = await service.Invoke(request, context)

        assert response.success is False
        assert "No action/skill specified" in response.error

    @pytest.mark.asyncio
    async def test_handler_not_found_shows_correct_action(self):
        """Test that error message shows correct action name for both formats."""
        service = AgentServiceImpl()
        service.custom_handlers = {}

        # Test A2A format
        a2a_params = {
            "message": {
                "kind": "message",
                "role": "user",
                "messageId": "msg-404",
                "metadata": {
                    "skill": "nonexistent_skill",
                    "params": {}
                },
                "parts": [{"kind": "text", "text": "test"}]
            }
        }

        request = agent_pb2.ActionRequest(
            message=agent_pb2.A2AMessage(
                jsonrpc="2.0",
                id="req-404",
                method="message/send",
                params_json=json.dumps(a2a_params)
            )
        )

        context = Mock()
        response = await service.Invoke(request, context)

        assert response.success is False
        assert "nonexistent_skill" in response.error


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
