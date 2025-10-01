import asyncio
import os
import socket
import tempfile
import zipfile
from pathlib import Path

import httpx
import pytest
import anyio

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
  basePath: /
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


@pytest.fixture
def anyio_backend():
	# Force asyncio backend to avoid trio param causing hangs
	return "asyncio"

@pytest.mark.anyio
async def test_base_path_routing_rest_ui_and_grpc_health():
	with tempfile.TemporaryDirectory() as d:
		apkg = _build_apkg(Path(d))
		rt = ThreeSurfaceRuntime(str(apkg))
		# Ensure multiplexed
		rt.multiplexed = True

		# Assign free ports
		with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
			s.bind(("127.0.0.1", 0))
			rt.rest_port = s.getsockname()[1]
		with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
			s2.bind(("127.0.0.1", 0))
			rt.a2a_port = s2.getsockname()[1]

		# Use BASE_PATH
		os.environ["BASE_PATH"] = "/agents/test-agent"
		os.environ["REST_PORT"] = str(rt.rest_port)
		os.environ["A2A_PORT"] = str(rt.a2a_port)

		task = asyncio.create_task(rt.start())
		try:
			# Guard the whole test with a timeout to avoid indefinite hangs
			with anyio.fail_after(20.0):
				base = f"http://127.0.0.1:{rt.rest_port}/agents/test-agent"
				async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=2.0)) as client:
					last_exc = None
					for i in range(40):
						try:
							print(f"attempt {i+1}: GET {base}/health")
							r = await client.get(f"{base}/health")
							if r.status_code < 500:
								break
						except Exception as e:
							last_exc = e
						await asyncio.sleep(0.25)
					if 'r' not in locals():
						raise AssertionError(f"Server did not respond: {last_exc}")
					assert r.status_code == 200, r.text
					data = r.json()
					assert data["ok"] is True
					assert data["surfaces"]["rest"] is True
					# a2a can be true only after server starts; accept bool
					assert isinstance(data["surfaces"]["a2a"], bool)

					# Agent REST route mounted under {BASE_PATH}/api
					r = await client.get(f"{base}/api/ping")
					assert r.status_code == 200
					assert r.json() == {"pong": True}

					# gRPC shim health
					r = await client.get(f"{base}/a2a/health")
					assert r.status_code in (200, 503)

					# UI root and ui-config
					r = await client.get(f"{base}/")
					assert r.status_code == 200
					r = await client.get(f"{base}/ui-config.json")
					assert r.status_code == 200
					assert r.json()["apiBase"] == "/agents/test-agent/api"
		finally:
			task.cancel()
			try:
				await task
			except asyncio.CancelledError:
				pass
			await rt.shutdown()
