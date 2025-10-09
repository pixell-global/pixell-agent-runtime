import asyncio
import os
import socket
from pathlib import Path

import httpx
import pytest

from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime


def _free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_boot_time_logged_and_budget_warning(tmp_path: Path, monkeypatch, capsys):
    rest_port = _free_port()
    a2a_port = _free_port()
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")
    # Set a very low budget and patch gRPC stub to add 50ms delay before Health returns
    monkeypatch.setenv("BOOT_BUDGET_MS", "1")
    # Force deterministic delay inside runtime before computing boot_ms
    monkeypatch.setenv("BOOT_TEST_DELAY_MS", "50")

    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "agent.yaml").write_text("name: app\nversion: 1.0.0\nentrypoint: main:handler\na2a: {}\nrest:\n  entry: main:mount\n")
    (pkg_dir / "main.py").write_text(
        """
from fastapi import APIRouter
router = APIRouter()
def mount(app):
    app.include_router(router)
"""
    )

    rt = ThreeSurfaceRuntime(str(pkg_dir))
    task = asyncio.create_task(rt.start())
    try:
        async with httpx.AsyncClient() as client:
            deadline = asyncio.get_event_loop().time() + 5.0
            ok = False
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
                    if r.status_code == 200:
                        ok = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
            assert ok
        # give logger a moment to flush
        await asyncio.sleep(0.2)
        # Fetch structured boot stats with polling to avoid races
        async with httpx.AsyncClient() as client:
            deadline = asyncio.get_event_loop().time() + 1.0
            stats = {}
            while asyncio.get_event_loop().time() < deadline:
                meta = (await client.get(f"http://127.0.0.1:{rest_port}/meta", timeout=0.5)).json()
                stats = meta.get("boot_stats") or {}
                if stats.get("total_ms") is not None and stats.get("total_ms", 0) > 0:
                    break
                await asyncio.sleep(0.05)
        assert stats.get("total_ms", 0) > 1.0
        phases = stats.get("phases_ms", {})
        assert isinstance(phases.get("load", 0), (int, float))
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()

