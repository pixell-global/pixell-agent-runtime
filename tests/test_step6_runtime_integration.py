import asyncio
import os
import socket
from pathlib import Path

import pytest
import httpx

from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_pkg_dir(tmp_path: Path, with_a2a: bool = True, with_rest: bool = True) -> Path:
    d = tmp_path / "pkgdir"
    d.mkdir()
    # agent.yaml with a2a and rest present
    yaml = [
        "name: app",
        "version: 1.0.0",
        "entrypoint: main:handler",
    ]
    if with_a2a:
        yaml.append("a2a: {}")
    if with_rest:
        yaml.append("rest:\n  entry: main:mount")
    (d / "agent.yaml").write_text("\n".join(yaml) + "\n")
    # minimal rest mount module
    if with_rest:
        (d / "main.py").write_text(
            """
from fastapi import APIRouter
router = APIRouter()
def mount(app):
    app.include_router(router)
"""
        )
    return d


@pytest.mark.asyncio
async def test_a2a_success_transitions_health_to_200(tmp_path: Path, monkeypatch):
    rest_port = _free_port()
    a2a_port = _free_port()
    pkg_dir = _make_pkg_dir(tmp_path, with_a2a=True, with_rest=True)

    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")

    rt = ThreeSurfaceRuntime(str(pkg_dir))
    task = asyncio.create_task(rt.start())
    try:
        # Poll up to 5s for REST server to come up and become healthy
        async with httpx.AsyncClient() as client:
            deadline = asyncio.get_event_loop().time() + 5.0
            ok = False
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
                    if r.status_code == 200 and r.json().get("surfaces", {}).get("a2a") is True:
                        ok = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
            assert ok, "health did not become 200"
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


@pytest.mark.asyncio
async def test_a2a_failure_keeps_health_503(tmp_path: Path, monkeypatch):
    rest_port = _free_port()
    a2a_port = _free_port()
    pkg_dir = _make_pkg_dir(tmp_path, with_a2a=True, with_rest=True)

    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")

    # Patch the bound symbol used inside runtime to raise an error
    from pixell_runtime.three_surface import runtime as _rt_mod
    orig_create = _rt_mod.create_grpc_server
    def _raise(*args, **kwargs):
        raise RuntimeError("boom")
    _rt_mod.create_grpc_server = _raise

    rt = ThreeSurfaceRuntime(str(pkg_dir))
    task = asyncio.create_task(rt.start())
    try:
        async with httpx.AsyncClient() as client:
            # Poll for 1.5s; a2a health endpoint should report 503 since gRPC failed
            deadline = asyncio.get_event_loop().time() + 1.5
            saw_a2a_503 = False
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await client.get(f"http://127.0.0.1:{rest_port}/a2a/health", timeout=1.0)
                    if r.status_code == 503:
                        saw_a2a_503 = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
            assert saw_a2a_503
    finally:
        _rt_mod.create_grpc_server = orig_create
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


