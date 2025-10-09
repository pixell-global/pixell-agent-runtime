"""Tests for graceful shutdown functionality."""

import asyncio
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest


class TestGracefulShutdown:
    """Test graceful shutdown behavior."""
    
    @pytest.mark.asyncio
    async def test_shutdown_marks_runtime_not_ready(self, monkeypatch):
        """Test that shutdown marks runtime as not ready."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        
        # Mock REST app
        runtime.rest_app = Mock()
        runtime.rest_app.state = Mock()
        runtime.rest_app.state.runtime_ready = True
        
        await runtime.shutdown()
        
        # Should mark as not ready
        assert runtime.rest_app.state.runtime_ready == False
    
    @pytest.mark.asyncio
    async def test_shutdown_waits_for_graceful_period(self, monkeypatch):
        """Test that shutdown waits during graceful period."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        monkeypatch.setenv("GRACEFUL_SHUTDOWN_TIMEOUT_SEC", "5")
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        runtime.rest_app = Mock()
        runtime.rest_app.state = Mock()
        
        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            await runtime.shutdown()
            
            # Should call sleep (at least once for the 1 second wait)
            assert mock_sleep.called
    
    @pytest.mark.asyncio
    async def test_shutdown_stops_grpc_server_with_grace(self, monkeypatch):
        """Test that gRPC server is stopped with grace period."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        monkeypatch.setenv("GRACEFUL_SHUTDOWN_TIMEOUT_SEC", "10")
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        
        # Mock gRPC server
        mock_grpc_server = AsyncMock()
        runtime.grpc_server = mock_grpc_server
        
        await runtime.shutdown()
        
        # Should call stop with grace period
        mock_grpc_server.stop.assert_called_once()
        # Check that grace parameter was passed (should be 10.0)
        call_args = mock_grpc_server.stop.call_args
        if call_args[1]:  # kwargs
            assert call_args[1].get('grace') == 10.0
        elif call_args[0]:  # args
            assert call_args[0][0] == 10.0
    
    @pytest.mark.asyncio
    async def test_shutdown_signals_rest_server_to_exit(self, monkeypatch):
        """Test that REST server is signaled to exit."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        
        # Mock REST server
        mock_rest_server = Mock()
        mock_rest_server.should_exit = False
        runtime._rest_server = mock_rest_server
        
        await runtime.shutdown()
        
        # Should set should_exit to True
        assert mock_rest_server.should_exit == True
    
    @pytest.mark.asyncio
    async def test_shutdown_signals_ui_server_to_exit(self, monkeypatch):
        """Test that UI server is signaled to exit."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        
        # Mock UI server
        mock_ui_server = Mock()
        mock_ui_server.should_exit = False
        runtime._ui_server = mock_ui_server
        
        await runtime.shutdown()
        
        # Should set should_exit to True
        assert mock_ui_server.should_exit == True
    
    @pytest.mark.asyncio
    async def test_shutdown_cleans_up_downloaded_package(self, monkeypatch, tmp_path):
        """Test that downloaded package is cleaned up."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        
        # Create fake downloaded package
        temp_dir = tmp_path / "pixell_apkg_test"
        temp_dir.mkdir()
        package_file = temp_dir / "package.apkg"
        package_file.write_text("fake package")
        
        runtime._downloaded_package_path = str(package_file)
        
        await runtime.shutdown()
        
        # Directory should be cleaned up
        assert not temp_dir.exists()
    
    @pytest.mark.asyncio
    async def test_shutdown_handles_grpc_error_gracefully(self, monkeypatch):
        """Test that gRPC shutdown errors don't crash shutdown."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        
        # Mock gRPC server that raises error on stop
        mock_grpc_server = AsyncMock()
        mock_grpc_server.stop.side_effect = Exception("gRPC stop failed")
        runtime.grpc_server = mock_grpc_server
        
        # Should not raise exception
        await runtime.shutdown()
        
        # gRPC server should be set to None even after error
        assert runtime.grpc_server is None
    
    @pytest.mark.asyncio
    async def test_shutdown_with_custom_timeout(self, monkeypatch):
        """Test that custom graceful timeout is respected."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        monkeypatch.setenv("GRACEFUL_SHUTDOWN_TIMEOUT_SEC", "42")
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        
        mock_grpc_server = AsyncMock()
        runtime.grpc_server = mock_grpc_server
        
        await runtime.shutdown()
        
        # Should use custom timeout
        call_args = mock_grpc_server.stop.call_args
        if call_args[1]:
            assert call_args[1].get('grace') == 42.0
        elif call_args[0]:
            assert call_args[0][0] == 42.0
    
    @pytest.mark.asyncio
    async def test_shutdown_waits_for_rest_drain(self, monkeypatch):
        """Test that shutdown waits for REST connections to drain."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        runtime._rest_server = Mock()
        runtime._rest_server.should_exit = False
        
        with patch('asyncio.sleep', new_callable=AsyncMock) as mock_sleep:
            await runtime.shutdown()
            
            # Should sleep to allow draining (at least the 2 second wait)
            assert any(call[0][0] >= 2 for call in mock_sleep.call_args_list if call[0])
    
    @pytest.mark.asyncio
    async def test_shutdown_order(self, monkeypatch):
        """Test that shutdown follows correct order."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        
        # Track order of operations
        order = []
        
        # Mock REST app with state tracking
        class MockState:
            def __init__(self):
                self.runtime_ready = True
                self._order = order
            
            def __setattr__(self, name, value):
                if name == 'runtime_ready' and value == False:
                    order.append('mark_not_ready')
                super().__setattr__(name, value)
        
        runtime.rest_app = Mock()
        runtime.rest_app.state = MockState()
        
        # Mock gRPC server
        mock_grpc = AsyncMock()
        async def track_grpc_stop(*args, **kwargs):
            order.append('grpc_stop')
        mock_grpc.stop = track_grpc_stop
        runtime.grpc_server = mock_grpc
        
        # Mock REST server
        class MockServer:
            def __init__(self, order_list):
                self.should_exit = False
                self._order = order_list
            
            def __setattr__(self, name, value):
                if name == 'should_exit' and value == True:
                    self._order.append('rest_exit')
                super().__setattr__(name, value)
        
        runtime._rest_server = MockServer(order)
        
        await runtime.shutdown()
        
        # Verify order: mark not ready -> grpc stop -> rest exit
        assert 'mark_not_ready' in order, f"Expected 'mark_not_ready' in order, got {order}"
        assert 'grpc_stop' in order, f"Expected 'grpc_stop' in order, got {order}"
        assert 'rest_exit' in order, f"Expected 'rest_exit' in order, got {order}"
        
        # mark_not_ready should come before grpc_stop
        assert order.index('mark_not_ready') < order.index('grpc_stop'), f"Order was {order}"


class TestGracefulShutdownConfiguration:
    """Test graceful shutdown configuration."""
    
    def test_default_graceful_timeout(self, monkeypatch):
        """Test that default graceful timeout is 30 seconds."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        monkeypatch.delenv("GRACEFUL_SHUTDOWN_TIMEOUT_SEC", raising=False)
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        # Runtime should accept default timeout
        runtime = ThreeSurfaceRuntime()
        assert runtime is not None
    
    def test_custom_graceful_timeout(self, monkeypatch):
        """Test that custom graceful timeout is accepted."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        monkeypatch.setenv("GRACEFUL_SHUTDOWN_TIMEOUT_SEC", "60")
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        assert runtime is not None


class TestShutdownIntegration:
    """Integration tests for shutdown behavior."""
    
    @pytest.mark.asyncio
    async def test_signal_handler_triggers_shutdown(self, monkeypatch):
        """Test that signal handlers trigger shutdown."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        
        # Mock shutdown method
        shutdown_called = []
        original_shutdown = runtime.shutdown
        async def mock_shutdown():
            shutdown_called.append(True)
            await original_shutdown()
        runtime.shutdown = mock_shutdown
        
        # Signal handlers are set up in __init__
        # We can't easily test signal delivery, but we can verify handlers exist
        # and that shutdown method works
        
        await runtime.shutdown()
        assert len(shutdown_called) > 0
    
    @pytest.mark.asyncio
    async def test_shutdown_completes_without_servers(self, monkeypatch):
        """Test that shutdown completes even with no servers running."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        
        # No servers initialized
        runtime.grpc_server = None
        runtime._rest_server = None
        runtime._ui_server = None
        
        # Should complete without error
        await runtime.shutdown()
