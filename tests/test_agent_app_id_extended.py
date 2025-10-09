"""
Extended tests for AGENT_APP_ID validation - integration and edge cases.
"""

import asyncio
import os
import socket
import tempfile
import zipfile
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


def _create_test_apkg(path: Path, with_a2a: bool = False) -> Path:
    """Create a minimal test APKG."""
    manifest = """name: test-agent
version: 1.0.0
entrypoint: main:handler
rest:
  entry: main:mount
"""
    if with_a2a:
        manifest += "a2a: {}\n"
    
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("agent.yaml", manifest)
        
        main_py = """
from fastapi import APIRouter
router = APIRouter()

@router.get("/test")
def test():
    return {"status": "ok"}

@router.get("/agent-id")
def get_agent_id():
    import os
    return {"agent_app_id": os.getenv("AGENT_APP_ID")}

def mount(app):
    app.include_router(router)

def handler(event, context):
    return {"statusCode": 200}
"""
        if with_a2a:
            main_py += """
import grpc
from pixell_runtime.proto import agent_pb2, agent_pb2_grpc

class AgentServiceImpl(agent_pb2_grpc.AgentServiceServicer):
    async def Health(self, request, context):
        return agent_pb2.HealthResponse(status="SERVING")

def handler(server: grpc.aio.Server):
    agent_pb2_grpc.add_AgentServiceServicer_to_server(AgentServiceImpl(), server)
"""
        zf.writestr("main.py", main_py)
    return path


def test_agent_app_id_available_in_runtime_instance(tmp_path, monkeypatch):
    """Test that AGENT_APP_ID is stored and accessible in runtime instance."""
    agent_id = "integration-test-agent"
    
    monkeypatch.setenv("AGENT_APP_ID", agent_id)
    monkeypatch.setenv("DEPLOYMENT_ID", "test-deployment")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    
    # Verify AGENT_APP_ID is stored and accessible
    assert rt.agent_app_id == agent_id
    assert hasattr(rt, "agent_app_id")
    
    # Verify it's the same value as the environment
    import os
    assert rt.agent_app_id == os.getenv("AGENT_APP_ID")


def test_agent_app_id_stored_correctly(tmp_path, monkeypatch):
    """Test that AGENT_APP_ID is stored correctly in runtime."""
    agent_id = "meta-test-agent"
    deployment_id = "test-deployment-123"
    
    monkeypatch.setenv("AGENT_APP_ID", agent_id)
    monkeypatch.setenv("DEPLOYMENT_ID", deployment_id)
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    
    # Verify both IDs are stored
    assert rt.agent_app_id == agent_id
    assert rt.deployment_id == deployment_id


def test_agent_app_id_validation_happens_before_package_load(tmp_path, monkeypatch):
    """Test that AGENT_APP_ID is validated before attempting to load package."""
    monkeypatch.delenv("AGENT_APP_ID", raising=False)
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    # Don't even create a valid APKG - validation should fail first
    with pytest.raises(SystemExit) as exc_info:
        ThreeSurfaceRuntime(package_path="/nonexistent/path")
    
    assert exc_info.value.code == 1


def test_agent_app_id_validation_with_package_url(monkeypatch):
    """Test that AGENT_APP_ID validation works with PACKAGE_URL."""
    monkeypatch.delenv("AGENT_APP_ID", raising=False)
    monkeypatch.setenv("PACKAGE_URL", "https://example.com/test.apkg")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    # Should fail before trying to download
    with pytest.raises(SystemExit) as exc_info:
        ThreeSurfaceRuntime()
    
    assert exc_info.value.code == 1


def test_agent_app_id_with_null_byte(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with null byte fails (Python limitation)."""
    # Python's os.environ doesn't support null bytes in values
    with pytest.raises(ValueError, match="embedded null byte"):
        monkeypatch.setenv("AGENT_APP_ID", "test\x00agent")


def test_agent_app_id_with_newlines(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with newlines is accepted (stored as-is)."""
    monkeypatch.setenv("AGENT_APP_ID", "test\nagent")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert "\n" in rt.agent_app_id


def test_agent_app_id_with_tabs(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with tabs is accepted."""
    monkeypatch.setenv("AGENT_APP_ID", "test\tagent")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert "\t" in rt.agent_app_id


def test_agent_app_id_only_tabs_fails(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with only tabs fails."""
    monkeypatch.setenv("AGENT_APP_ID", "\t\t\t")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    # Should fail - tabs are whitespace
    with pytest.raises(SystemExit) as exc_info:
        ThreeSurfaceRuntime(package_path=str(apkg_path))
    
    assert exc_info.value.code == 1


def test_agent_app_id_mixed_whitespace_fails(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with mixed whitespace fails."""
    monkeypatch.setenv("AGENT_APP_ID", " \t\n\r ")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    with pytest.raises(SystemExit) as exc_info:
        ThreeSurfaceRuntime(package_path=str(apkg_path))
    
    assert exc_info.value.code == 1


def test_agent_app_id_single_character(monkeypatch, tmp_path):
    """Test that single character AGENT_APP_ID is valid."""
    monkeypatch.setenv("AGENT_APP_ID", "a")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "a"


def test_agent_app_id_with_equals_sign(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with equals sign is accepted."""
    monkeypatch.setenv("AGENT_APP_ID", "agent=123")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "agent=123"


def test_agent_app_id_with_quotes(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with quotes is accepted."""
    monkeypatch.setenv("AGENT_APP_ID", 'agent"test"')
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert '"' in rt.agent_app_id


def test_agent_app_id_with_brackets(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with brackets is accepted."""
    monkeypatch.setenv("AGENT_APP_ID", "agent[test]")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "agent[test]"


def test_agent_app_id_with_braces(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with braces is accepted."""
    monkeypatch.setenv("AGENT_APP_ID", "agent{test}")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "agent{test}"


def test_agent_app_id_with_percent(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with percent sign is accepted."""
    monkeypatch.setenv("AGENT_APP_ID", "agent%20test")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "agent%20test"


def test_agent_app_id_with_ampersand(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with ampersand is accepted."""
    monkeypatch.setenv("AGENT_APP_ID", "agent&test")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "agent&test"


def test_agent_app_id_with_dollar_sign(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with dollar sign is accepted."""
    monkeypatch.setenv("AGENT_APP_ID", "agent$test")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "agent$test"


def test_agent_app_id_with_hash(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with hash is accepted."""
    monkeypatch.setenv("AGENT_APP_ID", "agent#test")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "agent#test"


def test_agent_app_id_with_at_sign(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with @ sign is accepted."""
    monkeypatch.setenv("AGENT_APP_ID", "agent@test")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "agent@test"


def test_agent_app_id_with_plus(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with plus sign is accepted."""
    monkeypatch.setenv("AGENT_APP_ID", "agent+test")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "agent+test"


def test_deployment_id_empty_string_accepted(monkeypatch, tmp_path):
    """Test that empty DEPLOYMENT_ID is accepted (it's optional)."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("DEPLOYMENT_ID", "")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "test-agent"
    assert rt.deployment_id == ""


def test_deployment_id_whitespace_accepted(monkeypatch, tmp_path):
    """Test that whitespace DEPLOYMENT_ID is accepted (it's optional)."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("DEPLOYMENT_ID", "   ")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "test-agent"
    assert rt.deployment_id == "   "


@pytest.mark.asyncio
async def test_agent_app_id_survives_runtime_lifecycle(tmp_path, monkeypatch):
    """Test that AGENT_APP_ID persists throughout runtime lifecycle."""
    rest_port = _free_port()
    a2a_port = _free_port()
    agent_id = "lifecycle-test-agent"
    
    monkeypatch.setenv("AGENT_APP_ID", agent_id)
    monkeypatch.setenv("DEPLOYMENT_ID", "test-deployment")
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    
    # Before start
    assert rt.agent_app_id == agent_id
    
    task = asyncio.create_task(rt.start())
    
    try:
        # During runtime
        await asyncio.sleep(0.5)
        assert rt.agent_app_id == agent_id
        
        # Wait for ready
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
        
        # After ready
        assert rt.agent_app_id == agent_id
    
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        
        # After shutdown
        await rt.shutdown()
        assert rt.agent_app_id == agent_id
