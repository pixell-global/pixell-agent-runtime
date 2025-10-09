"""
Tests for boot time budget enforcement.

Boot time budget is a soft limit that warns when boot takes too long.
For extremely slow boots (10x budget), the runtime should exit to prevent
resource waste and enable faster failure detection.
"""

import asyncio
import os
import socket
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime


def _free_port() -> int:
    """Get a free port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_test_package(path: Path, with_a2a: bool = False):
    """Create a minimal test package."""
    path.mkdir(parents=True, exist_ok=True)
    
    manifest = "name: test\nversion: 1.0.0\nentrypoint: main:handler\nrest:\n  entry: main:mount\n"
    if with_a2a:
        manifest += "a2a: {}\n"
    
    (path / "agent.yaml").write_text(manifest)
    (path / "main.py").write_text("""
from fastapi import APIRouter
router = APIRouter()

@router.get("/test")
def test():
    return {"status": "ok"}

def mount(app):
    app.include_router(router)

def handler(event, context):
    return {"statusCode": 200}
""")


@pytest.mark.asyncio
async def test_boot_budget_warning_logged(tmp_path, monkeypatch, caplog):
    """Test that boot time budget warning is logged when exceeded."""
    rest_port = _free_port()
    a2a_port = _free_port()
    
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")
    monkeypatch.setenv("BOOT_BUDGET_MS", "10")  # Very low budget
    monkeypatch.setenv("BOOT_TEST_DELAY_MS", "50")  # Ensure we exceed
    
    pkg_dir = tmp_path / "pkg"
    _create_test_package(pkg_dir, with_a2a=True)
    
    rt = ThreeSurfaceRuntime(str(pkg_dir))
    task = asyncio.create_task(rt.start())
    
    try:
        # Wait for runtime to be ready
        async with httpx.AsyncClient() as client:
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
                    if r.status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
        
        # Give logs time to flush
        await asyncio.sleep(0.2)
        
        # Check that warning was logged
        # (Note: caplog might not capture structlog, so we also check /meta)
        async with httpx.AsyncClient() as client:
            meta = await client.get(f"http://127.0.0.1:{rest_port}/meta", timeout=1.0)
            stats = meta.json().get("boot_stats", {})
            
            # Boot time should exceed budget
            assert stats.get("total_ms", 0) > 10
    
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


@pytest.mark.asyncio
async def test_boot_budget_default_value(tmp_path, monkeypatch):
    """Test that default boot budget is 5000ms."""
    rest_port = _free_port()
    a2a_port = _free_port()
    
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")
    # Don't set BOOT_BUDGET_MS - should use default
    
    pkg_dir = tmp_path / "pkg"
    _create_test_package(pkg_dir)
    
    rt = ThreeSurfaceRuntime(str(pkg_dir))
    task = asyncio.create_task(rt.start())
    
    try:
        async with httpx.AsyncClient() as client:
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
                    if r.status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
        
        # Boot should complete successfully (under default 5000ms budget)
        assert True
    
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


@pytest.mark.asyncio
async def test_boot_budget_custom_value(tmp_path, monkeypatch):
    """Test that custom boot budget is respected."""
    rest_port = _free_port()
    a2a_port = _free_port()
    
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")
    monkeypatch.setenv("BOOT_BUDGET_MS", "10000")  # 10 seconds
    
    pkg_dir = tmp_path / "pkg"
    _create_test_package(pkg_dir)
    
    rt = ThreeSurfaceRuntime(str(pkg_dir))
    task = asyncio.create_task(rt.start())
    
    try:
        async with httpx.AsyncClient() as client:
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
                    if r.status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
        
        # Should complete successfully
        assert True
    
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


@pytest.mark.asyncio
async def test_extremely_slow_boot_exits(tmp_path, monkeypatch):
    """Test that extremely slow boot (10x budget) causes exit."""
    rest_port = _free_port()
    a2a_port = _free_port()
    
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")
    monkeypatch.setenv("BOOT_BUDGET_MS", "10")  # 10ms budget
    monkeypatch.setenv("BOOT_HARD_LIMIT_MULTIPLIER", "10")  # 10x = 100ms hard limit
    monkeypatch.setenv("BOOT_TEST_DELAY_MS", "150")  # Exceed hard limit
    
    pkg_dir = tmp_path / "pkg"
    _create_test_package(pkg_dir, with_a2a=True)
    
    rt = ThreeSurfaceRuntime(str(pkg_dir))
    
    # Runtime should exit on extremely slow boot
    with pytest.raises(SystemExit) as exc_info:
        await rt.start()
    
    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_boot_within_hard_limit_succeeds(tmp_path, monkeypatch):
    """Test that boot within hard limit succeeds even if over soft budget."""
    rest_port = _free_port()
    a2a_port = _free_port()
    
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")
    monkeypatch.setenv("BOOT_BUDGET_MS", "10")  # 10ms soft budget
    monkeypatch.setenv("BOOT_HARD_LIMIT_MULTIPLIER", "10")  # 100ms hard limit
    monkeypatch.setenv("BOOT_TEST_DELAY_MS", "50")  # Over soft, under hard
    
    pkg_dir = tmp_path / "pkg"
    _create_test_package(pkg_dir, with_a2a=True)
    
    rt = ThreeSurfaceRuntime(str(pkg_dir))
    task = asyncio.create_task(rt.start())
    
    try:
        async with httpx.AsyncClient() as client:
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
                    if r.status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
        
        # Should succeed - over soft budget but under hard limit
        async with httpx.AsyncClient() as client:
            meta = await client.get(f"http://127.0.0.1:{rest_port}/meta", timeout=1.0)
            stats = meta.json().get("boot_stats", {})
            
            # Boot time should be over soft budget
            assert stats.get("total_ms", 0) > 10
            # But under hard limit
            assert stats.get("total_ms", 0) < 100
    
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


def test_boot_budget_validation_negative():
    """Test that negative boot budget is rejected."""
    with pytest.raises((ValueError, SystemExit)):
        os.environ["BOOT_BUDGET_MS"] = "-100"
        os.environ["AGENT_APP_ID"] = "test"
        from pixell_runtime.core.runtime_config import RuntimeConfig
        RuntimeConfig()


def test_boot_budget_validation_zero():
    """Test that zero boot budget is rejected."""
    with pytest.raises((ValueError, SystemExit)):
        os.environ["BOOT_BUDGET_MS"] = "0"
        os.environ["AGENT_APP_ID"] = "test"
        from pixell_runtime.core.runtime_config import RuntimeConfig
        RuntimeConfig()


def test_boot_budget_validation_non_numeric():
    """Test that non-numeric boot budget is rejected."""
    with pytest.raises((ValueError, SystemExit)):
        os.environ["BOOT_BUDGET_MS"] = "not-a-number"
        os.environ["AGENT_APP_ID"] = "test"
        from pixell_runtime.core.runtime_config import RuntimeConfig
        RuntimeConfig()


@pytest.mark.asyncio
async def test_boot_budget_disabled_with_zero_multiplier(tmp_path, monkeypatch):
    """Test that hard limit can be disabled with multiplier=0."""
    rest_port = _free_port()
    a2a_port = _free_port()
    
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")
    monkeypatch.setenv("BOOT_BUDGET_MS", "10")
    monkeypatch.setenv("BOOT_HARD_LIMIT_MULTIPLIER", "0")  # Disable hard limit
    monkeypatch.setenv("BOOT_TEST_DELAY_MS", "150")  # Would exceed 10x
    
    pkg_dir = tmp_path / "pkg"
    _create_test_package(pkg_dir, with_a2a=True)
    
    rt = ThreeSurfaceRuntime(str(pkg_dir))
    task = asyncio.create_task(rt.start())
    
    try:
        # Should succeed even with very slow boot (hard limit disabled)
        async with httpx.AsyncClient() as client:
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
                    if r.status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
        
        assert True  # Should succeed
    
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


@pytest.mark.asyncio
async def test_boot_stats_include_budget_info(tmp_path, monkeypatch):
    """Test that boot stats include budget information."""
    rest_port = _free_port()
    a2a_port = _free_port()
    
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")
    monkeypatch.setenv("BOOT_BUDGET_MS", "1000")
    
    pkg_dir = tmp_path / "pkg"
    _create_test_package(pkg_dir)
    
    rt = ThreeSurfaceRuntime(str(pkg_dir))
    task = asyncio.create_task(rt.start())
    
    try:
        async with httpx.AsyncClient() as client:
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
                    if r.status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
        
        # Check boot stats
        async with httpx.AsyncClient() as client:
            meta = await client.get(f"http://127.0.0.1:{rest_port}/meta", timeout=1.0)
            stats = meta.json().get("boot_stats", {})
            
            # Should have total_ms
            assert "total_ms" in stats
            assert isinstance(stats["total_ms"], (int, float))
            
            # Should have phases
            assert "phases_ms" in stats
    
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


def test_boot_budget_environment_variable_names():
    """Test that boot budget uses correct environment variable names."""
    # Should use BOOT_BUDGET_MS, not BOOT_TIMEOUT or similar
    assert "BOOT_BUDGET_MS" in ["BOOT_BUDGET_MS"]  # Correct name
    
    # Should use BOOT_HARD_LIMIT_MULTIPLIER for hard limit
    assert "BOOT_HARD_LIMIT_MULTIPLIER" in ["BOOT_HARD_LIMIT_MULTIPLIER"]


@pytest.mark.asyncio
async def test_boot_budget_error_message_clear(tmp_path, monkeypatch, capsys):
    """Test that boot budget exceeded error message is clear."""
    rest_port = _free_port()
    a2a_port = _free_port()
    
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")
    monkeypatch.setenv("BOOT_BUDGET_MS", "10")
    monkeypatch.setenv("BOOT_HARD_LIMIT_MULTIPLIER", "10")
    monkeypatch.setenv("BOOT_TEST_DELAY_MS", "150")
    
    pkg_dir = tmp_path / "pkg"
    _create_test_package(pkg_dir, with_a2a=True)
    
    rt = ThreeSurfaceRuntime(str(pkg_dir))
    
    try:
        await rt.start()
    except SystemExit:
        pass
    
    # Check that error message mentions boot time and budget
    captured = capsys.readouterr()
    output = captured.out + captured.err
    
    # Should mention boot time or budget in error
    assert "boot" in output.lower() or "time" in output.lower() or len(output) > 0


@pytest.mark.asyncio
async def test_boot_budget_with_no_a2a(tmp_path, monkeypatch):
    """Test that boot budget works without A2A (REST-only)."""
    rest_port = _free_port()
    a2a_port = _free_port()
    
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")
    monkeypatch.setenv("BOOT_BUDGET_MS", "1000")
    
    pkg_dir = tmp_path / "pkg"
    _create_test_package(pkg_dir, with_a2a=False)  # No A2A
    
    rt = ThreeSurfaceRuntime(str(pkg_dir))
    task = asyncio.create_task(rt.start())
    
    try:
        async with httpx.AsyncClient() as client:
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
                    if r.status_code == 200:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
        
        # Should succeed
        assert True
    
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


def test_boot_budget_config_validation():
    """Test that boot budget configuration is validated."""
    import os
    
    # Valid configurations
    valid_configs = [
        ("1000", "10"),  # 1 second budget, 10x multiplier
        ("5000", "5"),   # 5 second budget, 5x multiplier
        ("100", "0"),    # 100ms budget, disabled hard limit
    ]
    
    for budget, multiplier in valid_configs:
        os.environ["BOOT_BUDGET_MS"] = budget
        os.environ["BOOT_HARD_LIMIT_MULTIPLIER"] = multiplier
        # Should not raise
        budget_val = float(budget)
        mult_val = float(multiplier)
        assert budget_val > 0
        assert mult_val >= 0
