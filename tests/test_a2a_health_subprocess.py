import asyncio
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import grpc
import httpx
import pytest

# Make src importable when running tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pixell_runtime.proto import agent_pb2, agent_pb2_grpc


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
"""
    )

    apkg = tmp / "t.apkg"
    with zipfile.ZipFile(apkg, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in tmp.rglob("*"):
            if p.is_file() and p.name != apkg.name:
                zf.write(p, p.relative_to(tmp))
    return apkg


def test_a2a_health_in_subprocess():
    with tempfile.TemporaryDirectory() as d:
        apkg = _build_apkg(Path(d))

        # Pick free ports
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            rest_port = s.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
            s2.bind(("127.0.0.1", 0))
            a2a_port = s2.getsockname()[1]

        env = os.environ.copy()
        env["BASE_PATH"] = "/agents/test-agent"
        env["REST_PORT"] = str(rest_port)
        env["A2A_PORT"] = str(a2a_port)
        env["MULTIPLEXED"] = "true"

        # Start runtime in a subprocess
        code = (
            "import asyncio, os; "
            "from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime; "
            f"rt=ThreeSurfaceRuntime('{apkg.as_posix()}'); "
            "asyncio.run(rt.start())"
        )
        proc = subprocess.Popen([sys.executable, "-c", code], env=env)
        try:
            # Wait for REST health to become ready
            base = f"http://127.0.0.1:{rest_port}/agents/test-agent"
            deadline = time.time() + 15.0
            last_exc = None
            while time.time() < deadline:
                try:
                    r = httpx.get(f"{base}/health", timeout=1.0)
                    if r.status_code == 200:
                        break
                except Exception as e:
                    last_exc = e
                time.sleep(0.25)
            else:
                raise AssertionError(f"REST health not ready: {last_exc}")

            # Hit gRPC health with a small timeout
            with grpc.insecure_channel(f"localhost:{a2a_port}") as channel:
                stub = agent_pb2_grpc.AgentServiceStub(channel)
                resp = stub.Health(agent_pb2.Empty(), timeout=1.0)
                assert resp.ok is True

            # Verify REST agent route
            r = httpx.get(f"{base}/api/ping", timeout=1.0)
            assert r.status_code == 200
            assert r.json() == {"pong": True}

            # UI config
            r = httpx.get(f"{base}/ui-config.json", timeout=1.0)
            assert r.status_code == 200
            assert r.json()["apiBase"] == "/agents/test-agent/api"
        finally:
            # Terminate subprocess cleanly
            try:
                proc.send_signal(signal.SIGTERM)
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
                proc.wait(timeout=5)


