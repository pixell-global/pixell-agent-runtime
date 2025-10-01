import asyncio
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pixell_runtime.main import create_app
from pixell_runtime.core.config import Settings


@pytest.fixture
def app(tmp_path: Path):
    # Use temp package cache
    settings = Settings(package_cache_dir=str(tmp_path / "packages"))
    os.environ.pop("PAR_RUNTIME_SECRET", None)
    app = create_app(settings)
    return app


def test_deploy_accepts_and_idempotent(app: "TestClient"):
    client = TestClient(app)

    # Build a local URL to test package; we use the example-agent.apkg in repo
    apkg_path = Path(__file__).resolve().parents[1] / "example-agent.apkg"
    assert apkg_path.exists(), "example-agent.apkg should exist in repo root"

    # For download, we provide a file:// URL by starting a local app? Simpler: use s3.signedUrl with file:// which httpx cannot fetch.
    # Instead, we bypass fetch by serving via a simple file URL using data URL not supported. So use absolute file path over file:// by httpx? Not supported.
    # Easiest: Spin a small HTTP server to serve the file.
    import threading
    from http.server import SimpleHTTPRequestHandler
    from socketserver import TCPServer

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

    http_dir = apkg_path.parent
    os.chdir(http_dir)
    httpd = TCPServer(("127.0.0.1", 0), QuietHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{port}/{apkg_path.name}"
        body = {
            "deploymentId": "dep-1",
            "agentAppId": "app-1",
            "orgId": "org-1",
            "version": "0.1.0",
            "packageUrl": url,
            "surfaces": {"mode": "multiplex", "ports": {"rest": 8087, "a2a": 51051, "ui": 3007}},
        }

        # Post deploy
        r = client.post("/deploy", json=body, headers={"Idempotency-Key": "dep-1"})
        assert r.status_code == 202, r.text
        assert r.json()["deploymentId"] == "dep-1"

        # Re-post idempotent
        r2 = client.post("/deploy", json=body, headers={"Idempotency-Key": "dep-1"})
        assert r2.status_code == 202

        # Wait for health to become healthy
        import time
        deadline = time.time() + 30
        healthy = False
        while time.time() < deadline:
            hr = client.get("/deployments/dep-1/health")
            assert hr.status_code == 200
            status = hr.json()["status"]
            if status == "healthy":
                healthy = True
                break
            time.sleep(0.5)
        assert healthy, "deployment did not become healthy"
    finally:
        httpd.shutdown()
        httpd.server_close()


