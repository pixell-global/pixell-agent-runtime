import asyncio
import json
import os
import socket
from pathlib import Path

import httpx
import pytest

from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
from pixell_runtime.utils.logging import setup_logging, bind_runtime_context


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_log_sequence_to_ready(tmp_path: Path, monkeypatch, capsys):
    setup_logging("INFO", "json")
    monkeypatch.setenv("AGENT_APP_ID", "app-seq")
    monkeypatch.setenv("DEPLOYMENT_ID", "dep-seq")
    rest_port = _free_port()
    a2a_port = _free_port()
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")

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
        # Wait until health is 200
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
            assert ok, "health did not become 200"

        # give logger a tick to flush
        await asyncio.sleep(0.1)

        # Capture logs and check for key events
        out = capsys.readouterr().out
        # Must see runtime start
        assert "\"event\": \"Starting three-surface runtime\"" in out
        # Accept either explicit ready marker or evidence A2A started
        assert (
            "\"event\": \"Runtime ready\"" in out
            or "\"event\": \"Starting A2A gRPC server\"" in out
        )
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


def test_no_sensitive_envs_in_logs(monkeypatch, capsys):
    setup_logging("INFO", "json")
    monkeypatch.setenv("SECRET", "should_not_leak")
    monkeypatch.setenv("API_KEY", "should_not_leak")
    bind_runtime_context("app", "dep")
    import structlog
    structlog.get_logger().info("env_log", SECRET="should_not_leak", api_key="should_not_leak")
    out = capsys.readouterr().out.strip().splitlines()[-1]
    data = json.loads(out)
    # Redaction is case-insensitive on keys
    assert data.get("SECRET") == "[REDACTED]" or data.get("SECRET") is None
    assert data.get("api_key") == "[REDACTED]"

