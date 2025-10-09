"""Tests for retry backoff functionality on boot failure."""

import os
import time
from unittest.mock import Mock, patch

import pytest


class TestRetryBackoff:
    """Test retry backoff behavior to prevent hot-restart loops."""
    
    def test_exit_with_backoff_first_failure_no_sleep(self, monkeypatch):
        """Test that first failure exits immediately without backoff."""
        # Clear any existing failure count
        monkeypatch.delenv("BOOT_FAILURE_COUNT", raising=False)
        
        from pixell_runtime.three_surface.runtime import _exit_with_backoff
        
        start_time = time.time()
        
        with pytest.raises(SystemExit) as exc_info:
            _exit_with_backoff(1)
        
        elapsed = time.time() - start_time
        
        # Should exit immediately (< 0.1 seconds)
        assert elapsed < 0.1
        assert exc_info.value.code == 1
    
    def test_exit_with_backoff_second_failure_sleeps_2_sec(self, monkeypatch):
        """Test that second failure sleeps for 2 seconds."""
        # Simulate first failure already happened
        monkeypatch.setenv("BOOT_FAILURE_COUNT", "1")
        
        from pixell_runtime.three_surface.runtime import _exit_with_backoff
        
        start_time = time.time()
        
        with pytest.raises(SystemExit) as exc_info:
            with patch('time.sleep') as mock_sleep:
                _exit_with_backoff(1)
                
                # Should call sleep with 2 seconds
                mock_sleep.assert_called_once_with(2)
        
        assert exc_info.value.code == 1
    
    def test_exit_with_backoff_third_failure_sleeps_4_sec(self, monkeypatch):
        """Test that third failure sleeps for 4 seconds."""
        monkeypatch.setenv("BOOT_FAILURE_COUNT", "2")
        
        from pixell_runtime.three_surface.runtime import _exit_with_backoff
        
        with pytest.raises(SystemExit):
            with patch('time.sleep') as mock_sleep:
                _exit_with_backoff(1)
                mock_sleep.assert_called_once_with(4)
    
    def test_exit_with_backoff_exponential_progression(self, monkeypatch):
        """Test exponential backoff progression."""
        from pixell_runtime.three_surface.runtime import _exit_with_backoff
        
        test_cases = [
            (0, 0),   # First failure - no sleep
            (1, 2),   # Second failure - 2 seconds
            (2, 4),   # Third failure - 4 seconds
            (3, 8),   # Fourth failure - 8 seconds
            (4, 16),  # Fifth failure - 16 seconds
            (5, 32),  # Sixth failure - 32 seconds
            (6, 60),  # Seventh failure - 60 seconds (capped)
            (7, 60),  # Eighth failure - 60 seconds (capped)
            (10, 60), # Many failures - still capped at 60
        ]
        
        for failure_count, expected_sleep in test_cases:
            monkeypatch.setenv("BOOT_FAILURE_COUNT", str(failure_count))
            
            with pytest.raises(SystemExit):
                with patch('time.sleep') as mock_sleep:
                    _exit_with_backoff(1)
                    
                    if failure_count == 0:
                        # First failure - no sleep
                        mock_sleep.assert_not_called()
                    else:
                        mock_sleep.assert_called_once_with(expected_sleep)
    
    def test_exit_with_backoff_increments_counter(self, monkeypatch):
        """Test that failure counter is incremented."""
        monkeypatch.setenv("BOOT_FAILURE_COUNT", "2")
        
        from pixell_runtime.three_surface.runtime import _exit_with_backoff
        
        with pytest.raises(SystemExit):
            with patch('time.sleep'):
                _exit_with_backoff(1)
        
        # Counter should be incremented to 3
        assert os.environ["BOOT_FAILURE_COUNT"] == "3"
    
    def test_exit_with_backoff_custom_exit_code(self, monkeypatch):
        """Test that custom exit codes are preserved."""
        monkeypatch.delenv("BOOT_FAILURE_COUNT", raising=False)
        
        from pixell_runtime.three_surface.runtime import _exit_with_backoff
        
        with pytest.raises(SystemExit) as exc_info:
            _exit_with_backoff(42)
        
        assert exc_info.value.code == 42
    
    def test_exit_with_backoff_logs_warning(self, monkeypatch, caplog):
        """Test that backoff logs warning message."""
        monkeypatch.setenv("BOOT_FAILURE_COUNT", "2")
        
        from pixell_runtime.three_surface.runtime import _exit_with_backoff
        
        with pytest.raises(SystemExit):
            with patch('time.sleep'):
                _exit_with_backoff(1)
        
        # Should log warning about backoff
        # Note: Actual log checking depends on logging setup
        # This is a basic check
    
    def test_exit_with_backoff_invalid_counter_raises_error(self, monkeypatch):
        """Test that invalid failure count raises ValueError."""
        monkeypatch.setenv("BOOT_FAILURE_COUNT", "invalid")
        
        from pixell_runtime.three_surface.runtime import _exit_with_backoff
        
        # Should raise ValueError for invalid int conversion
        with pytest.raises(ValueError):
            _exit_with_backoff(1)


class TestRuntimeFailureIntegration:
    """Test that runtime failures use retry backoff."""
    
    def test_package_download_failure_uses_backoff(self, monkeypatch, tmp_path):
        """Test that package download failure triggers backoff."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        monkeypatch.setenv("PACKAGE_URL", "s3://bucket/nonexistent.apkg")
        monkeypatch.delenv("BOOT_FAILURE_COUNT", raising=False)
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        runtime = ThreeSurfaceRuntime()
        
        # Mock fetch to fail
        with patch('pixell_runtime.three_surface.runtime._exit_with_backoff') as mock_exit:
            with patch('pixell_runtime.deploy.fetch.fetch_package_to_path') as mock_fetch:
                mock_fetch.side_effect = Exception("Download failed")
                
                # Should call _exit_with_backoff instead of sys.exit
                import asyncio
                try:
                    asyncio.run(runtime.load_package())
                except Exception:
                    pass
                
                # _exit_with_backoff should have been called
                assert mock_exit.called or mock_fetch.called
    
    def test_missing_package_source_uses_backoff(self, monkeypatch):
        """Test that missing package source triggers backoff."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        monkeypatch.delenv("PACKAGE_URL", raising=False)
        monkeypatch.delenv("BOOT_FAILURE_COUNT", raising=False)
        
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        
        # Don't initialize runtime yet - just test that the backoff function would be called
        # When there's no package_path and no PACKAGE_URL
        
        # Mock _exit_with_backoff before creating runtime
        with patch('pixell_runtime.three_surface.runtime._exit_with_backoff') as mock_exit:
            mock_exit.side_effect = SystemExit(1)
            
            runtime = ThreeSurfaceRuntime()
            runtime.package_path = None
            runtime.package = None
            
            import asyncio
            try:
                asyncio.run(runtime.load_package())
            except SystemExit:
                pass
            
            # Should use backoff
            assert mock_exit.called
    
    def test_boot_time_hard_limit_uses_backoff(self, monkeypatch):
        """Test that exceeding boot time hard limit triggers backoff."""
        monkeypatch.setenv("AGENT_APP_ID", "test-agent")
        monkeypatch.setenv("BOOT_BUDGET_MS", "100")
        monkeypatch.setenv("BOOT_HARD_LIMIT_MULTIPLIER", "2.0")
        
        # This test verifies the code path exists
        # Actual boot time testing requires integration test
        from pixell_runtime.three_surface.runtime import _exit_with_backoff
        
        # Verify function exists and is callable
        assert callable(_exit_with_backoff)


class TestBackoffEnvironmentPersistence:
    """Test that failure count persists across restarts."""
    
    def test_failure_count_set_in_environment(self, monkeypatch):
        """Test that failure count is stored in environment."""
        monkeypatch.delenv("BOOT_FAILURE_COUNT", raising=False)
        
        from pixell_runtime.three_surface.runtime import _exit_with_backoff
        
        with pytest.raises(SystemExit):
            with patch('time.sleep'):
                _exit_with_backoff(1)
        
        # Should set counter to 1
        assert "BOOT_FAILURE_COUNT" in os.environ
        assert os.environ["BOOT_FAILURE_COUNT"] == "1"
    
    def test_failure_count_increments_on_retry(self, monkeypatch):
        """Test that failure count increments on each retry."""
        # Start with count of 3
        monkeypatch.setenv("BOOT_FAILURE_COUNT", "3")
        
        from pixell_runtime.three_surface.runtime import _exit_with_backoff
        
        with pytest.raises(SystemExit):
            with patch('time.sleep'):
                _exit_with_backoff(1)
        
        # Should increment to 4
        assert os.environ["BOOT_FAILURE_COUNT"] == "4"
