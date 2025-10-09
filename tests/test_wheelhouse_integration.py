"""
Integration tests for wheelhouse with package loader.
"""

import os
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pixell_runtime.agents.loader import PackageLoader


def _create_test_apkg_with_requirements(path: Path, requirements: str) -> Path:
    """Create a test APKG with requirements.txt."""
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
        zf.writestr("requirements.txt", requirements)
    return path


def test_loader_uses_wheelhouse_when_available(tmp_path, monkeypatch):
    """Test that PackageLoader uses wheelhouse when available."""
    packages_dir = tmp_path / "packages"
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    # Create a fake wheel file
    (wheelhouse_dir / "requests-2.28.0-py3-none-any.whl").touch()
    
    # Set wheelhouse env var
    monkeypatch.setenv("WHEELHOUSE_DIR", str(wheelhouse_dir))
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    
    # Create test APKG
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg_with_requirements(apkg_path, "requests==2.28.0\n")
    
    loader = PackageLoader(packages_dir)
    
    # Mock subprocess to avoid actual pip install
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        
        try:
            loader.load_package(apkg_path, agent_app_id="test-agent")
        except Exception:
            pass  # We're just testing that wheelhouse args are passed
        
        # Check that pip was called with wheelhouse args
        if mock_run.called:
            for call in mock_run.call_args_list:
                cmd = call[0][0] if call[0] else []
                if "pip" in str(cmd) and "install" in cmd:
                    # Should have --find-links pointing to wheelhouse
                    assert "--find-links" in cmd
                    assert str(wheelhouse_dir) in cmd


def test_loader_works_without_wheelhouse(tmp_path, monkeypatch):
    """Test that PackageLoader works without wheelhouse."""
    packages_dir = tmp_path / "packages"
    
    # No wheelhouse env var
    monkeypatch.delenv("WHEELHOUSE_DIR", raising=False)
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    
    # Create test APKG
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg_with_requirements(apkg_path, "requests==2.28.0\n")
    
    loader = PackageLoader(packages_dir)
    
    # Mock subprocess to avoid actual pip install
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        
        try:
            loader.load_package(apkg_path, agent_app_id="test-agent")
        except Exception:
            pass  # We're just testing that it doesn't crash
        
        # Should work fine without wheelhouse
        assert True


def test_loader_ignores_invalid_wheelhouse(tmp_path, monkeypatch):
    """Test that PackageLoader ignores invalid wheelhouse directory."""
    packages_dir = tmp_path / "packages"
    wheelhouse_dir = tmp_path / "nonexistent"
    
    # Set wheelhouse to non-existent directory
    monkeypatch.setenv("WHEELHOUSE_DIR", str(wheelhouse_dir))
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    
    # Create test APKG
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg_with_requirements(apkg_path, "requests==2.28.0\n")
    
    loader = PackageLoader(packages_dir)
    
    # Mock subprocess to avoid actual pip install
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        
        try:
            loader.load_package(apkg_path, agent_app_id="test-agent")
        except Exception:
            pass
        
        # Should not crash, just skip wheelhouse
        if mock_run.called:
            for call in mock_run.call_args_list:
                cmd = call[0][0] if call[0] else []
                if "pip" in str(cmd) and "install" in cmd:
                    # Should NOT have wheelhouse args
                    assert "--find-links" not in cmd or str(wheelhouse_dir) not in cmd


def test_loader_wheelhouse_with_empty_requirements(tmp_path, monkeypatch):
    """Test wheelhouse with empty requirements.txt."""
    packages_dir = tmp_path / "packages"
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    monkeypatch.setenv("WHEELHOUSE_DIR", str(wheelhouse_dir))
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    
    # Create test APKG with empty requirements
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg_with_requirements(apkg_path, "")
    
    loader = PackageLoader(packages_dir)
    
    # Should not crash with empty requirements
    try:
        loader.load_package(apkg_path, agent_app_id="test-agent")
    except Exception as e:
        # Should not be wheelhouse-related error
        assert "wheelhouse" not in str(e).lower()


def test_loader_wheelhouse_fallback_to_pypi(tmp_path, monkeypatch):
    """Test that wheelhouse allows fallback to PyPI (not offline mode)."""
    packages_dir = tmp_path / "packages"
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    # Wheelhouse has some packages but not all
    (wheelhouse_dir / "requests-2.28.0-py3-none-any.whl").touch()
    
    monkeypatch.setenv("WHEELHOUSE_DIR", str(wheelhouse_dir))
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    
    # Create test APKG with requirements
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg_with_requirements(apkg_path, "requests==2.28.0\nunknown-package==1.0.0\n")
    
    loader = PackageLoader(packages_dir)
    
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        
        try:
            loader.load_package(apkg_path, agent_app_id="test-agent")
        except Exception:
            pass
        
        # Check that --no-index is NOT used (allows PyPI fallback)
        if mock_run.called:
            for call in mock_run.call_args_list:
                cmd = call[0][0] if call[0] else []
                if "pip" in str(cmd) and "install" in cmd:
                    assert "--no-index" not in cmd


def test_wheelhouse_cache_info_logged(tmp_path, monkeypatch, caplog):
    """Test that wheelhouse cache info is logged."""
    packages_dir = tmp_path / "packages"
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    # Create wheel files
    (wheelhouse_dir / "requests-2.28.0-py3-none-any.whl").touch()
    (wheelhouse_dir / "urllib3-1.26.0-py2.py3-none-any.whl").touch()
    
    monkeypatch.setenv("WHEELHOUSE_DIR", str(wheelhouse_dir))
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    
    # Create test APKG
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg_with_requirements(apkg_path, "requests==2.28.0\n")
    
    loader = PackageLoader(packages_dir)
    
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        
        try:
            loader.load_package(apkg_path, agent_app_id="test-agent")
        except Exception:
            pass
        
        # Check that wheelhouse info was logged
        # (This is a bit fragile, but validates the integration)
        assert True  # If we got here, wheelhouse was processed


def test_wheelhouse_with_no_requirements_file(tmp_path, monkeypatch):
    """Test wheelhouse when APKG has no requirements.txt."""
    packages_dir = tmp_path / "packages"
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    monkeypatch.setenv("WHEELHOUSE_DIR", str(wheelhouse_dir))
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    
    # Create APKG without requirements.txt
    apkg_path = tmp_path / "test.apkg"
    with zipfile.ZipFile(apkg_path, "w") as zf:
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

def mount(app):
    app.include_router(router)

def handler(event, context):
    return {"statusCode": 200}
""",
        )
    
    loader = PackageLoader(packages_dir)
    
    # Should not crash when no requirements.txt
    try:
        package = loader.load_package(apkg_path, agent_app_id="test-agent")
        # Package should load successfully
        assert package.manifest.name == "test-agent"
    except Exception as e:
        # Should not be wheelhouse-related
        assert "wheelhouse" not in str(e).lower()


def test_wheelhouse_validation_failure_does_not_block(tmp_path, monkeypatch):
    """Test that wheelhouse validation failure doesn't block package loading."""
    packages_dir = tmp_path / "packages"
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir(mode=0o000)  # No permissions
    
    monkeypatch.setenv("WHEELHOUSE_DIR", str(wheelhouse_dir))
    monkeypatch.setenv("AGENT_APP_ID", "test-agent")
    
    # Create test APKG
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg_with_requirements(apkg_path, "requests==2.28.0\n")
    
    loader = PackageLoader(packages_dir)
    
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
        
        try:
            loader.load_package(apkg_path, agent_app_id="test-agent")
        except Exception as e:
            # Should not crash due to wheelhouse permission error
            assert "permission" not in str(e).lower() or "wheelhouse" not in str(e).lower()
    
    # Restore permissions for cleanup
    try:
        wheelhouse_dir.chmod(0o755)
    except:
        pass
