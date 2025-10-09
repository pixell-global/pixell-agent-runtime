import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pixell_runtime.core.models import AgentPackage, AgentManifest, RESTConfig, UIConfig, A2AConfig
from pixell_runtime.rest.server import create_rest_app
from pixell_runtime.ui.server import setup_ui_routes
from pixell_runtime.a2a.server import create_grpc_server, start_grpc_server


def _make_package(tmp_path: Path, with_rest: bool = False, with_ui: bool = False, with_a2a: bool = False) -> AgentPackage:
    pkg_dir = tmp_path / "pkg"
    (pkg_dir / "src").mkdir(parents=True, exist_ok=True)

    rest_cfg = None
    if with_rest:
        # Create a simple rest module exposing mount(app)
        rest_py = pkg_dir / "restmod.py"
        rest_py.write_text(
            """
from fastapi import APIRouter

router = APIRouter()

@router.get('/api/ping')
async def ping():
    return {'ok': True, 'pong': True}

def mount(app):
    app.include_router(router)
"""
        )
        rest_cfg = RESTConfig(entry="restmod:mount")

    ui_cfg = None
    if with_ui:
        ui_dir = pkg_dir / "ui"
        ui_dir.mkdir(parents=True, exist_ok=True)
        (ui_dir / "index.html").write_text("<html><body>OK</body></html>")
        ui_cfg = UIConfig(path=str(ui_dir), basePath="/")

    a2a_cfg = A2AConfig(service="") if with_a2a else None

    manifest = AgentManifest(
        name="app",
        version="1.0.0",
        entrypoint="main:handler",
        runtime_version="0.1.0",
        description="",
        author="",
        exports=[],
        dependencies=[],
        a2a=a2a_cfg,
        rest=rest_cfg,
        ui=ui_cfg,
    )
    return AgentPackage(
        id="app@1.0.0",
        manifest=manifest,
        path=str(pkg_dir),
        url="https://example.com/app.apkg",
        sha256="",
        status="pending",
        venv_path=str(tmp_path / "venv")
    )


def test_rest_route_mounted_under_base_path(tmp_path: Path):
    pkg = _make_package(tmp_path, with_rest=True)
    app = create_rest_app(pkg, base_path="/agents/app")
    client = TestClient(app)
    # route available under base path
    r = client.get("/agents/app/api/ping")
    assert r.status_code == 200
    assert r.json().get("pong") is True


@pytest.mark.asyncio
async def test_grpc_health_starts_and_responds(tmp_path: Path):
    pkg = _make_package(tmp_path, with_a2a=True)
    # Pick a high random port in range
    port = 59000
    server = create_grpc_server(package=pkg, port=port)
    try:
        await start_grpc_server(server)
        import grpc
        from pixell_runtime.proto import agent_pb2, agent_pb2_grpc
        async with grpc.aio.insecure_channel(f"localhost:{port}") as channel:
            stub = agent_pb2_grpc.AgentServiceStub(channel)
            resp = await stub.Health(agent_pb2.Empty(), timeout=1.0)
            assert resp.ok is True
    finally:
        await server.stop(grace=0)


def test_ui_multiplexed_static_served(tmp_path: Path):
    pkg = _make_package(tmp_path, with_ui=True)
    app = create_rest_app(pkg, base_path="/agents/app")
    setup_ui_routes(app, pkg, base_path_override="/agents/app")
    client = TestClient(app)
    r = client.get("/agents/app/ui/index.html")
    assert r.status_code == 200
    assert b"OK" in r.content
