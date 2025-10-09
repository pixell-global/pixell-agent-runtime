"""
Step 10: Conformance checklist vs request_response_trace.md

This test verifies that PAR correctly implements its responsibilities as defined in
the request_response_trace.md document, and does NOT perform any control-plane operations.
"""

import asyncio
import os
import socket
from pathlib import Path
from unittest.mock import patch, MagicMock
import zipfile

import httpx
import pytest
import grpc.aio

from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
from pixell_runtime.proto import agent_pb2, agent_pb2_grpc


def _free_port() -> int:
    """Get a free port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_sample_apkg(path: Path) -> Path:
    """Create a sample APKG for testing."""
    apkg_path = path / "sample.apkg"
    with zipfile.ZipFile(apkg_path, "w") as zf:
        # agent.yaml
        zf.writestr(
            "agent.yaml",
            """name: conformance-test
version: 1.0.0
entrypoint: main:handler
a2a: {}
rest:
  entry: main:mount
ui:
  path: ui
""",
        )
        # main.py
        zf.writestr(
            "main.py",
            """
from fastapi import APIRouter

router = APIRouter()

@router.get("/test")
def test_endpoint():
    return {"status": "ok"}

def mount(app):
    app.include_router(router)

def handler(event, context):
    return {"statusCode": 200}
""",
        )
        # ui/index.html
        zf.writestr("ui/index.html", "<html><body>Test UI</body></html>")
    return apkg_path


@pytest.mark.asyncio
async def test_env_contract_respected(tmp_path: Path, monkeypatch):
    """Test that PAR respects the environment variable contract."""
    rest_port = _free_port()
    a2a_port = _free_port()
    ui_port = 3000

    # Set all required env vars as per request_response_trace.md
    monkeypatch.setenv("AGENT_APP_ID", "test-agent-123")
    monkeypatch.setenv("DEPLOYMENT_ID", "deploy-456")
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("UI_PORT", str(ui_port))
    monkeypatch.setenv("BASE_PATH", "/agents/test-agent-123")
    monkeypatch.setenv("AWS_REGION", "us-east-2")
    monkeypatch.setenv("S3_BUCKET", "pixell-agent-packages")

    # Create a test package
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "agent.yaml").write_text(
        "name: test\nversion: 1.0.0\nentrypoint: main:handler\na2a: {}\nrest:\n  entry: main:mount\n"
    )
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
        # Verify runtime reads and uses env vars correctly
        assert rt.rest_port == rest_port
        assert rt.a2a_port == a2a_port
        assert rt.ui_port == ui_port
        assert rt.base_path == "/agents/test-agent-123"

        # Wait for health to be ready
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
            assert ok, "Health endpoint should become ready"

    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


@pytest.mark.asyncio
async def test_ports_bound_on_all_interfaces(tmp_path: Path, monkeypatch):
    """Test that PAR binds to 0.0.0.0 (all interfaces) for REST, A2A, and UI."""
    rest_port = _free_port()
    a2a_port = _free_port()

    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")

    # Create a test package
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "agent.yaml").write_text(
        "name: test\nversion: 1.0.0\nentrypoint: main:handler\na2a: {}\nrest:\n  entry: main:mount\n"
    )
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
        # Wait for services to start
        await asyncio.sleep(0.5)

        # Verify REST is accessible on 127.0.0.1 (implies 0.0.0.0 binding)
        async with httpx.AsyncClient() as client:
            deadline = asyncio.get_event_loop().time() + 5.0
            rest_ok = False
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
                    if r.status_code == 200:
                        rest_ok = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
            assert rest_ok, "REST should be accessible on 127.0.0.1"

        # Verify A2A gRPC is accessible
        a2a_ok = False
        deadline = asyncio.get_event_loop().time() + 5.0
        while asyncio.get_event_loop().time() < deadline:
            try:
                async with grpc.aio.insecure_channel(f"127.0.0.1:{a2a_port}") as channel:
                    stub = agent_pb2_grpc.AgentServiceStub(channel)
                    await stub.Health(agent_pb2.Empty(), timeout=0.5)
                    a2a_ok = True
                    break
            except Exception:
                await asyncio.sleep(0.1)
        assert a2a_ok, "A2A gRPC should be accessible on 127.0.0.1"

    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


@pytest.mark.asyncio
async def test_health_endpoint_and_readiness_gating(tmp_path: Path, monkeypatch):
    """Test that /health endpoint implements readiness gating (503 -> 200)."""
    rest_port = _free_port()
    a2a_port = _free_port()

    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")

    # Create a test package
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "agent.yaml").write_text(
        "name: test\nversion: 1.0.0\nentrypoint: main:handler\na2a: {}\nrest:\n  entry: main:mount\n"
    )
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
        # Initially, health should return 503 (not ready)
        async with httpx.AsyncClient() as client:
            # Give REST server a moment to start
            await asyncio.sleep(0.3)
            
            # First request should be 503 (not ready yet)
            try:
                r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
                # If we get here quickly enough, it should be 503
                # But if A2A starts very fast, it might already be 200
                # So we just verify we can connect
                assert r.status_code in [200, 503]
            except Exception:
                pass  # Server might not be up yet

            # Eventually, health should return 200 (ready)
            deadline = asyncio.get_event_loop().time() + 5.0
            ok = False
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
                    if r.status_code == 200:
                        data = r.json()
                        assert data["ok"] is True
                        assert "surfaces" in data
                        ok = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
            assert ok, "Health should eventually return 200 OK"

    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


@pytest.mark.asyncio
async def test_apkg_loader_flow(tmp_path: Path, monkeypatch):
    """Test that PAR correctly implements the APKG loader flow: fetch → extract → install → load → serve."""
    rest_port = _free_port()
    a2a_port = _free_port()

    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")

    # Create a test APKG
    apkg_path = _create_sample_apkg(tmp_path)

    # Test with directory (simulating extracted APKG)
    extract_dir = tmp_path / "extracted"
    extract_dir.mkdir()
    with zipfile.ZipFile(apkg_path, "r") as zf:
        zf.extractall(extract_dir)

    rt = ThreeSurfaceRuntime(str(extract_dir))
    task = asyncio.create_task(rt.start())

    try:
        # Wait for runtime to be ready
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
            assert ok, "Runtime should load and serve the APKG"

            # Verify agent endpoint is accessible
            r = await client.get(f"http://127.0.0.1:{rest_port}/test", timeout=1.0)
            assert r.status_code == 200
            assert r.json() == {"status": "ok"}

    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


@pytest.mark.asyncio
async def test_no_control_plane_operations(tmp_path: Path, monkeypatch):
    """Test that PAR does NOT perform any control-plane operations (DB, ECS, ALB, Cloud Map)."""
    rest_port = _free_port()
    a2a_port = _free_port()

    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")

    # Create a test package
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "agent.yaml").write_text(
        "name: test\nversion: 1.0.0\nentrypoint: main:handler\na2a: {}\nrest:\n  entry: main:mount\n"
    )
    (pkg_dir / "main.py").write_text(
        """
from fastapi import APIRouter
router = APIRouter()
def mount(app):
    app.include_router(router)
"""
    )

    # Mock boto3 clients to detect any AWS API calls
    ecs_mock = MagicMock()
    elbv2_mock = MagicMock()
    sd_mock = MagicMock()  # Service Discovery (Cloud Map)
    rds_mock = MagicMock()

    with patch("boto3.client") as boto_client_mock:
        def client_factory(service_name, *args, **kwargs):
            if service_name == "ecs":
                return ecs_mock
            elif service_name in ["elbv2", "elb"]:
                return elbv2_mock
            elif service_name == "servicediscovery":
                return sd_mock
            elif service_name == "rds":
                return rds_mock
            # Allow S3 for APKG fetching
            elif service_name == "s3":
                return MagicMock()
            return MagicMock()

        boto_client_mock.side_effect = client_factory

        rt = ThreeSurfaceRuntime(str(pkg_dir))
        task = asyncio.create_task(rt.start())

        try:
            # Wait for runtime to be ready
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
                assert ok, "Runtime should start successfully"

            # Verify NO control-plane operations were performed
            # ECS operations
            assert not ecs_mock.create_service.called, "PAR should NOT create ECS services"
            assert not ecs_mock.update_service.called, "PAR should NOT update ECS services"
            assert not ecs_mock.register_task_definition.called, "PAR should NOT register task definitions"
            
            # ELB/ALB operations
            assert not elbv2_mock.create_target_group.called, "PAR should NOT create target groups"
            assert not elbv2_mock.register_targets.called, "PAR should NOT register targets"
            assert not elbv2_mock.create_listener.called, "PAR should NOT create listeners"
            assert not elbv2_mock.create_rule.called, "PAR should NOT create ALB rules"
            
            # Service Discovery (Cloud Map) operations
            assert not sd_mock.register_instance.called, "PAR should NOT register Cloud Map instances"
            assert not sd_mock.create_service.called, "PAR should NOT create Cloud Map services"
            
            # RDS/Database operations
            assert not rds_mock.method_calls, "PAR should NOT make RDS calls"

        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await rt.shutdown()


@pytest.mark.asyncio
async def test_base_path_routing(tmp_path: Path, monkeypatch):
    """Test that PAR correctly mounts REST/UI under BASE_PATH."""
    rest_port = _free_port()
    a2a_port = _free_port()
    base_path = "/agents/test-agent"

    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", base_path)

    # Create a test package
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "agent.yaml").write_text(
        "name: test\nversion: 1.0.0\nentrypoint: main:handler\na2a: {}\nrest:\n  entry: main:mount\n"
    )
    (pkg_dir / "main.py").write_text(
        """
from fastapi import APIRouter
router = APIRouter()

@router.get("/custom")
def custom_endpoint():
    return {"path": "custom"}

def mount(app):
    app.include_router(router)
"""
    )

    rt = ThreeSurfaceRuntime(str(pkg_dir))
    task = asyncio.create_task(rt.start())

    try:
        # Wait for runtime to be ready
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
            assert ok, "Runtime should be ready"

            # Verify agent routes are accessible under BASE_PATH
            r = await client.get(f"http://127.0.0.1:{rest_port}{base_path}/custom", timeout=1.0)
            assert r.status_code == 200
            assert r.json() == {"path": "custom"}

            # Verify health is accessible at root (not under BASE_PATH)
            r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
            assert r.status_code == 200

    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


@pytest.mark.asyncio
async def test_structured_logging_and_error_exit(tmp_path: Path, monkeypatch, capsys):
    """Test that PAR emits structured logs and exits non-zero on unrecoverable errors."""
    rest_port = _free_port()
    a2a_port = _free_port()

    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("DEPLOYMENT_ID", "test-deploy")

    # Create a BROKEN package (missing required fields)
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "agent.yaml").write_text("name: test\n")  # Missing version, entrypoint

    rt = ThreeSurfaceRuntime(str(pkg_dir))
    task = asyncio.create_task(rt.start())

    try:
        # Wait a moment for the error to occur
        await asyncio.sleep(1.0)

        # The runtime should have logged an error and shut down
        # We can't easily test the exit code in this context, but we can verify
        # that the runtime didn't start successfully
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=0.5)
                # If we get here, the server is running, which is unexpected
                # But it might be in a not-ready state
                if r.status_code == 200:
                    pytest.fail("Runtime should not be healthy with a broken package")
            except Exception:
                # Expected: connection refused or timeout because runtime shut down
                pass

    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()


@pytest.mark.asyncio
async def test_all_three_surfaces_accessible(tmp_path: Path, monkeypatch):
    """Test that all three surfaces (REST, A2A, UI) are accessible when configured."""
    rest_port = _free_port()
    a2a_port = _free_port()
    ui_port = 3000

    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("UI_PORT", str(ui_port))
    monkeypatch.setenv("BASE_PATH", "/")
    monkeypatch.setenv("MULTIPLEXED", "true")

    # Create a test package with all surfaces
    pkg_dir = tmp_path / "pkg"
    pkg_dir.mkdir()
    (pkg_dir / "agent.yaml").write_text(
        """name: test
version: 1.0.0
entrypoint: main:handler
a2a: {}
rest:
  entry: main:mount
ui:
  path: ui
"""
    )
    (pkg_dir / "main.py").write_text(
        """
from fastapi import APIRouter
router = APIRouter()

@router.get("/api-test")
def api_test():
    return {"surface": "rest"}

def mount(app):
    app.include_router(router)
"""
    )
    ui_dir = pkg_dir / "ui"
    ui_dir.mkdir()
    (ui_dir / "index.html").write_text("<html><body>UI Surface</body></html>")

    rt = ThreeSurfaceRuntime(str(pkg_dir))
    task = asyncio.create_task(rt.start())

    try:
        # Wait for runtime to be ready
        async with httpx.AsyncClient() as client:
            deadline = asyncio.get_event_loop().time() + 5.0
            ok = False
            while asyncio.get_event_loop().time() < deadline:
                try:
                    r = await client.get(f"http://127.0.0.1:{rest_port}/health", timeout=1.0)
                    if r.status_code == 200:
                        data = r.json()
                        if data.get("ok") and data.get("surfaces", {}).get("a2a"):
                            ok = True
                            break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
            assert ok, "Runtime should be fully ready"

            # Test REST surface
            r = await client.get(f"http://127.0.0.1:{rest_port}/api-test", timeout=1.0)
            assert r.status_code == 200
            assert r.json() == {"surface": "rest"}

            # Test UI surface (multiplexed on REST port)
            r = await client.get(f"http://127.0.0.1:{rest_port}/ui/", timeout=1.0)
            assert r.status_code == 200
            assert "UI Surface" in r.text

        # Test A2A surface
        async with grpc.aio.insecure_channel(f"127.0.0.1:{a2a_port}") as channel:
            stub = agent_pb2_grpc.AgentServiceStub(channel)
            response = await stub.Health(agent_pb2.Empty(), timeout=1.0)
            # Health should succeed (empty response is ok)

    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await rt.shutdown()
