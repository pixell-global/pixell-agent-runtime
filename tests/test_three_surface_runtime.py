import asyncio
import tempfile
import zipfile
from pathlib import Path

import httpx
import pytest

# Ensure src is importable
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime


def _build_apkg(tmp: Path) -> Path:
    (tmp / "ui").mkdir(parents=True, exist_ok=True)
    (tmp / "ui" / "index.html").write_text("<html><body>OK</body></html>")

    (tmp / "rest_routes.py").write_text(
        """
from fastapi import FastAPI

def mount(app: FastAPI):
    @app.get("/api/ping")
    async def ping():
        return {"pong": True}
"""
    )

    (tmp / "a2a_service.py").write_text(
        """
def create_grpc_server():
    class Svc:
        def __init__(self):
            self.custom_handlers = {"noop": self.noop}
        async def noop(self, params):
            return "ok"
    return Svc()
"""
    )

    (tmp / "main.py").write_text("def handler(x):\n    return x\n")

    (tmp / "agent.yaml").write_text(
        """
name: t
version: 0.1.0
entrypoint: main:handler
rest:
  entry: rest_routes:mount
ui:
  path: ui
a2a:
  service: a2a_service:create_grpc_server
metadata:
  sub_agents:
    - name: t
      description: t
      public: true
"""
    )

    apkg = tmp / "t.apkg"
    with zipfile.ZipFile(apkg, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in tmp.rglob("*"):
            if p.is_file() and p.name != apkg.name:
                zf.write(p, p.relative_to(tmp))
    return apkg


@pytest.mark.anyio
async def test_three_surface_health_and_rest():
    with tempfile.TemporaryDirectory() as d:
        apkg = _build_apkg(Path(d))
        rt = ThreeSurfaceRuntime(str(apkg))
        # Ensure multiplexed single port to keep test simple
        rt.multiplexed = True

        # Use a free port for REST to avoid conflicts when tests run in parallel
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            rt.rest_port = s.getsockname()[1]
        task = asyncio.create_task(rt.start())
        try:
            # Give servers time to start
            await asyncio.sleep(1.5)
            async with httpx.AsyncClient() as client:
                r = await client.get(f"http://127.0.0.1:{rt.rest_port}/health")
                assert r.status_code == 200
                data = r.json()
                assert data["ok"] is True

                r = await client.get(f"http://127.0.0.1:{rt.rest_port}/api/ping")
                assert r.status_code == 200
                assert r.json() == {"pong": True}

                r = await client.get(f"http://127.0.0.1:{rt.rest_port}/ui/health")
                assert r.status_code == 200
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await rt.shutdown()


