"""Unit tests for PAR gRPC routing interceptor."""

import pytest
import asyncio
import grpc
from unittest.mock import Mock, AsyncMock, MagicMock
from concurrent import futures

from pixell_runtime.a2a.interceptor import PARRoutingInterceptor, PARLoggingInterceptor


class TestPARRoutingInterceptor:
    """Test suite for PARRoutingInterceptor."""

    def test_initialization_valid(self):
        """Test interceptor initializes correctly with valid agent_id."""
        agent_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
        interceptor = PARRoutingInterceptor(agent_id=agent_id)

        assert interceptor.agent_id == agent_id
        assert interceptor.prefix == f"/agents/{agent_id}/a2a"
        assert interceptor.prefix_len == len(interceptor.prefix)

    def test_initialization_invalid_empty(self):
        """Test interceptor rejects empty agent_id."""
        with pytest.raises(ValueError, match="agent_id cannot be None or empty"):
            PARRoutingInterceptor(agent_id="")

    def test_initialization_invalid_none(self):
        """Test interceptor rejects None agent_id."""
        with pytest.raises(ValueError, match="agent_id cannot be None or empty"):
            PARRoutingInterceptor(agent_id=None)

    @pytest.mark.asyncio
    async def test_strips_valid_prefix(self):
        """Test that interceptor correctly strips ALB routing prefix."""
        agent_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
        interceptor = PARRoutingInterceptor(agent_id=agent_id)

        # Create mock handler call details with prefixed path
        original_method = f"/agents/{agent_id}/a2a/pixell.agent.AgentService/Health"
        expected_method = "/pixell.agent.AgentService/Health"

        handler_details = Mock()
        handler_details.method = original_method
        handler_details.invocation_metadata = ()

        # Create mock continuation that captures the modified details
        modified_details_captured = None

        async def mock_continuation(details):
            nonlocal modified_details_captured
            modified_details_captured = details
            return AsyncMock()

        # Call interceptor
        await interceptor.intercept_service(mock_continuation, handler_details)

        # Verify prefix was stripped
        assert modified_details_captured is not None
        assert modified_details_captured.method == expected_method

    @pytest.mark.asyncio
    async def test_passthrough_without_prefix(self):
        """Test that clean paths pass through unchanged (local dev scenario)."""
        agent_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
        interceptor = PARRoutingInterceptor(agent_id=agent_id)

        # Create mock handler call details with clean path (no prefix)
        clean_method = "/pixell.agent.AgentService/Health"

        handler_details = Mock()
        handler_details.method = clean_method
        handler_details.invocation_metadata = ()

        # Create mock continuation that captures the details
        passed_details = None

        async def mock_continuation(details):
            nonlocal passed_details
            passed_details = details
            return AsyncMock()

        # Call interceptor
        await interceptor.intercept_service(mock_continuation, handler_details)

        # Verify path was NOT modified (passed through)
        assert passed_details is not None
        assert passed_details.method == clean_method

    @pytest.mark.asyncio
    async def test_wrong_agent_id_not_stripped(self):
        """Test that wrong agent ID prefix is not stripped (pass through)."""
        agent_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
        wrong_agent_id = "DIFFERENT-AGENT-ID-1234567890"
        interceptor = PARRoutingInterceptor(agent_id=agent_id)

        # Create path with WRONG agent ID
        wrong_prefixed_method = f"/agents/{wrong_agent_id}/a2a/pixell.agent.AgentService/Health"

        handler_details = Mock()
        handler_details.method = wrong_prefixed_method
        handler_details.invocation_metadata = ()

        # Create mock continuation
        passed_details = None

        async def mock_continuation(details):
            nonlocal passed_details
            passed_details = details
            return AsyncMock()

        # Call interceptor
        await interceptor.intercept_service(mock_continuation, handler_details)

        # Verify path was NOT modified (passed through unchanged)
        # This is correct behavior - agent will get wrong path and fail,
        # but interceptor shouldn't strip prefixes it doesn't own
        assert passed_details is not None
        assert passed_details.method == wrong_prefixed_method

    @pytest.mark.asyncio
    async def test_exception_handling_doesnt_crash(self):
        """Test that exceptions in interceptor don't crash the server."""
        agent_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
        interceptor = PARRoutingInterceptor(agent_id=agent_id)

        # Create handler details that will cause exception (invalid type)
        handler_details = Mock()
        handler_details.method = None  # This will cause AttributeError on startswith()

        # Create mock continuation
        continuation_called = False

        async def mock_continuation(details):
            nonlocal continuation_called
            continuation_called = True
            return AsyncMock()

        # Call interceptor - should NOT raise exception
        result = await interceptor.intercept_service(mock_continuation, handler_details)

        # Verify continuation was called (pass-through on error)
        assert continuation_called
        assert result is not None

    @pytest.mark.asyncio
    async def test_strips_all_grpc_methods(self):
        """Test that interceptor works for all gRPC method types."""
        agent_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
        interceptor = PARRoutingInterceptor(agent_id=agent_id)

        # Test multiple gRPC methods
        test_methods = [
            "/pixell.agent.AgentService/Health",
            "/pixell.agent.AgentService/Invoke",
            "/pixell.agent.AgentService/DescribeCapabilities",
            "/pixell.agent.AgentService/Ping",
            "/custom.package.CustomService/CustomMethod",
        ]

        for method in test_methods:
            prefixed_method = f"/agents/{agent_id}/a2a{method}"

            handler_details = Mock()
            handler_details.method = prefixed_method
            handler_details.invocation_metadata = ()

            modified_details = None

            async def mock_continuation(details):
                nonlocal modified_details
                modified_details = details
                return AsyncMock()

            await interceptor.intercept_service(mock_continuation, handler_details)

            # Verify each method was stripped correctly
            assert modified_details is not None
            assert modified_details.method == method, \
                f"Method {method} not stripped correctly from {prefixed_method}"

    @pytest.mark.asyncio
    async def test_preserves_invocation_metadata(self):
        """Test that interceptor preserves gRPC metadata."""
        agent_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
        interceptor = PARRoutingInterceptor(agent_id=agent_id)

        # Create metadata
        test_metadata = (
            ("authorization", "Bearer token123"),
            ("x-request-id", "req-456"),
        )

        handler_details = Mock()
        handler_details.method = f"/agents/{agent_id}/a2a/pixell.agent.AgentService/Health"
        handler_details.invocation_metadata = test_metadata

        modified_details = None

        async def mock_continuation(details):
            nonlocal modified_details
            modified_details = details
            return AsyncMock()

        await interceptor.intercept_service(mock_continuation, handler_details)

        # Verify metadata was preserved
        assert modified_details is not None
        assert modified_details.invocation_metadata == test_metadata

    @pytest.mark.asyncio
    async def test_handles_bytes_method(self):
        """Test that interceptor handles method as bytes (gRPC can send either type).

        This tests the fix for issue #13 where gRPC intermittently sends method as bytes
        instead of string, causing TypeError when comparing bytes.startswith(str).
        """
        agent_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
        interceptor = PARRoutingInterceptor(agent_id=agent_id)

        # Create mock handler call details with BYTES method (not string!)
        original_method_bytes = f"/agents/{agent_id}/a2a/pixell.agent.AgentService/Health".encode('utf-8')
        expected_method = "/pixell.agent.AgentService/Health"

        handler_details = Mock()
        handler_details.method = original_method_bytes  # BYTES!
        handler_details.invocation_metadata = ()

        modified_details = None

        async def mock_continuation(details):
            nonlocal modified_details
            modified_details = details
            return AsyncMock()

        # Call interceptor - should handle bytes gracefully
        await interceptor.intercept_service(mock_continuation, handler_details)

        # Verify prefix was stripped correctly even with bytes input
        assert modified_details is not None
        assert modified_details.method == expected_method

    @pytest.mark.asyncio
    async def test_handles_mixed_bytes_and_string(self):
        """Test that interceptor handles both bytes and string methods interchangeably.

        This simulates the real-world scenario where gRPC alternates between sending
        method as bytes or string depending on connection state and internal caching.
        """
        agent_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
        interceptor = PARRoutingInterceptor(agent_id=agent_id)

        test_cases = [
            # (input_method, expected_output, description)
            (f"/agents/{agent_id}/a2a/pixell.agent.AgentService/Health", "/pixell.agent.AgentService/Health", "string"),
            (f"/agents/{agent_id}/a2a/pixell.agent.AgentService/Health".encode('utf-8'), "/pixell.agent.AgentService/Health", "bytes"),
            (f"/agents/{agent_id}/a2a/pixell.agent.AgentService/Invoke", "/pixell.agent.AgentService/Invoke", "string"),
            (f"/agents/{agent_id}/a2a/pixell.agent.AgentService/Invoke".encode('utf-8'), "/pixell.agent.AgentService/Invoke", "bytes"),
            (f"/agents/{agent_id}/a2a/pixell.agent.AgentService/DescribeCapabilities", "/pixell.agent.AgentService/DescribeCapabilities", "string"),
            (f"/agents/{agent_id}/a2a/pixell.agent.AgentService/DescribeCapabilities".encode('utf-8'), "/pixell.agent.AgentService/DescribeCapabilities", "bytes"),
        ]

        for input_method, expected_method, desc in test_cases:
            handler_details = Mock()
            handler_details.method = input_method
            handler_details.invocation_metadata = ()

            modified_details = None

            async def mock_continuation(details):
                nonlocal modified_details
                modified_details = details
                return AsyncMock()

            await interceptor.intercept_service(mock_continuation, handler_details)

            assert modified_details is not None, f"Failed for {desc}: no modified_details"
            assert modified_details.method == expected_method, \
                f"Failed for {desc} input: expected {expected_method}, got {modified_details.method}"


class TestPARLoggingInterceptor:
    """Test suite for PARLoggingInterceptor (optional logging)."""

    @pytest.mark.asyncio
    async def test_logs_and_forwards(self):
        """Test that logging interceptor logs call and forwards."""
        interceptor = PARLoggingInterceptor()

        handler_details = Mock()
        handler_details.method = "/pixell.agent.AgentService/Health"
        handler_details.invocation_metadata = (("test-header", "test-value"),)

        continuation_called = False

        async def mock_continuation(details):
            nonlocal continuation_called
            continuation_called = True
            return AsyncMock()

        # Call interceptor - should log and forward
        result = await interceptor.intercept_service(mock_continuation, handler_details)

        # Verify continuation was called
        assert continuation_called
        assert result is not None

    @pytest.mark.asyncio
    async def test_logging_exception_doesnt_crash(self):
        """Test that logging errors don't crash the interceptor."""
        interceptor = PARLoggingInterceptor()

        # Handler details that might cause logging issues
        handler_details = Mock()
        handler_details.method = "/test/method"
        handler_details.invocation_metadata = "invalid"  # Not a tuple!

        continuation_called = False

        async def mock_continuation(details):
            nonlocal continuation_called
            continuation_called = True
            return AsyncMock()

        # Should NOT raise exception even with bad metadata
        result = await interceptor.intercept_service(mock_continuation, handler_details)

        # Verify continuation was still called
        assert continuation_called
        assert result is not None


class TestInterceptorIntegration:
    """Integration tests for interceptor with real gRPC server."""

    @pytest.mark.asyncio
    async def test_interceptor_with_real_server_stub(self):
        """Test interceptor with a real gRPC server (integration)."""
        # This test would require a full gRPC server setup
        # Skipping for now as it requires proto definitions and server
        pytest.skip("Integration test - requires full gRPC server setup")


if __name__ == "__main__":
    # Run tests with: pytest tests/test_a2a_interceptor.py -v
    pytest.main([__file__, "-v", "-s"])
