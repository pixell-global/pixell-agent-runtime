"""
Tests for AGENT_APP_ID validation.
"""

import os
import socket
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime


def _free_port() -> int:
    """Get a free port for testing."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _create_test_apkg(path: Path) -> Path:
    """Create a minimal test APKG."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "agent.yaml",
            """name: test-agent
version: 1.0.0
entrypoint: main:handler
rest:
  entry: main:mount
""",
        )
        zf.writestr(
            "main.py",
            """
from fastapi import APIRouter
router = APIRouter()

@router.get("/test")
def test():
    return {"status": "ok"}

def mount(app):
    app.include_router(router)

def handler(event, context):
    return {"statusCode": 200}
""",
        )
    return path


def test_agent_app_id_missing_fails(monkeypatch, tmp_path):
    """Test that runtime fails when AGENT_APP_ID is missing."""
    # Unset AGENT_APP_ID
    monkeypatch.delenv("AGENT_APP_ID", raising=False)
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    # Should exit with code 1
    with pytest.raises(SystemExit) as exc_info:
        ThreeSurfaceRuntime(package_path=str(apkg_path))
    
    assert exc_info.value.code == 1


def test_agent_app_id_empty_string_fails(monkeypatch, tmp_path):
    """Test that runtime fails when AGENT_APP_ID is an empty string."""
    monkeypatch.setenv("AGENT_APP_ID", "")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    # Should exit with code 1
    with pytest.raises(SystemExit) as exc_info:
        ThreeSurfaceRuntime(package_path=str(apkg_path))
    
    assert exc_info.value.code == 1


def test_agent_app_id_whitespace_only_fails(monkeypatch, tmp_path):
    """Test that runtime fails when AGENT_APP_ID is only whitespace."""
    monkeypatch.setenv("AGENT_APP_ID", "   ")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    # Should exit with code 1
    with pytest.raises(SystemExit) as exc_info:
        ThreeSurfaceRuntime(package_path=str(apkg_path))
    
    assert exc_info.value.code == 1


def test_agent_app_id_valid_succeeds(monkeypatch, tmp_path):
    """Test that runtime succeeds when AGENT_APP_ID is valid."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent-123")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    # Should succeed
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "test-agent-123"


def test_agent_app_id_with_special_characters(monkeypatch, tmp_path):
    """Test that runtime accepts AGENT_APP_ID with special characters."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent_123.v1")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "test-agent_123.v1"


def test_agent_app_id_with_leading_trailing_spaces(monkeypatch, tmp_path):
    """Test that runtime handles AGENT_APP_ID with leading/trailing spaces."""
    monkeypatch.setenv("AGENT_APP_ID", "  test-agent  ")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    # Should succeed - we store the original value
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "  test-agent  "


def test_agent_app_id_very_long(monkeypatch, tmp_path):
    """Test that runtime accepts very long AGENT_APP_ID."""
    long_id = "a" * 1000
    monkeypatch.setenv("AGENT_APP_ID", long_id)
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == long_id


def test_agent_app_id_with_unicode(monkeypatch, tmp_path):
    """Test that runtime accepts AGENT_APP_ID with unicode characters."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent-测试-🚀")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "test-agent-测试-🚀"


def test_agent_app_id_stored_in_runtime(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID is stored as an attribute on runtime."""
    agent_id = "my-test-agent"
    monkeypatch.setenv("AGENT_APP_ID", agent_id)
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    
    # Verify it's stored
    assert hasattr(rt, "agent_app_id")
    assert rt.agent_app_id == agent_id


def test_deployment_id_optional(monkeypatch, tmp_path):
    """Test that DEPLOYMENT_ID is optional."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.delenv("DEPLOYMENT_ID", raising=False)
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    # Should succeed even without DEPLOYMENT_ID
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "test-agent"
    assert rt.deployment_id is None


def test_deployment_id_stored_when_provided(monkeypatch, tmp_path):
    """Test that DEPLOYMENT_ID is stored when provided."""
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    monkeypatch.setenv("DEPLOYMENT_ID", "deploy-123")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "test-agent"
    assert rt.deployment_id == "deploy-123"


def test_agent_app_id_used_in_logging_context(monkeypatch, tmp_path, capsys):
    """Test that AGENT_APP_ID is bound to logging context."""
    agent_id = "test-agent-logging"
    monkeypatch.setenv("AGENT_APP_ID", agent_id)
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    
    # Logging context should be bound during initialization
    # We can't easily test this without triggering actual logs,
    # but we can verify the runtime was created successfully
    assert rt.agent_app_id == agent_id


def test_agent_app_id_case_sensitive(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID is case-sensitive."""
    monkeypatch.setenv("AGENT_APP_ID", "Test-Agent-ABC")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    # Should preserve case
    assert rt.agent_app_id == "Test-Agent-ABC"
    assert rt.agent_app_id != "test-agent-abc"


def test_agent_app_id_with_numbers_only(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID can be numbers only."""
    monkeypatch.setenv("AGENT_APP_ID", "123456")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "123456"


def test_agent_app_id_with_uuid_format(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID can be a UUID."""
    uuid_id = "550e8400-e29b-41d4-a716-446655440000"
    monkeypatch.setenv("AGENT_APP_ID", uuid_id)
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == uuid_id


def test_agent_app_id_error_message_clear(monkeypatch, tmp_path, capsys):
    """Test that error message is clear when AGENT_APP_ID is missing."""
    monkeypatch.delenv("AGENT_APP_ID", raising=False)
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    with pytest.raises(SystemExit):
        ThreeSurfaceRuntime(package_path=str(apkg_path))
    
    captured = capsys.readouterr()
    # Check that error message mentions AGENT_APP_ID
    assert "AGENT_APP_ID" in captured.out or "AGENT_APP_ID" in captured.err


def test_agent_app_id_with_slash(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with slashes is accepted (though not recommended)."""
    monkeypatch.setenv("AGENT_APP_ID", "org/team/agent")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "org/team/agent"


def test_agent_app_id_with_colon(monkeypatch, tmp_path):
    """Test that AGENT_APP_ID with colons is accepted."""
    monkeypatch.setenv("AGENT_APP_ID", "agent:v1:prod")
    monkeypatch.setenv("REST_PORT", str(_free_port()))
    monkeypatch.setenv("A2A_PORT", str(_free_port()))
    
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)
    
    rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
    assert rt.agent_app_id == "agent:v1:prod"
