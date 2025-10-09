"""
Tests for PACKAGE_URL environment variable support.
"""

import asyncio
import os
import socket
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx
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
a2a: {}
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


@pytest.mark.asyncio
async def test_package_url_https_download(tmp_path: Path, monkeypatch):
    """Test downloading package from HTTPS URL."""
    rest_port = _free_port()
    a2a_port = _free_port()

    # Create a test APKG
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)

    # Mock fetch_package_to_path to simulate download
    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        def fake_fetch(location, dest_path, **kwargs):
            # Copy our test APKG to the destination
            import shutil
            shutil.copy(apkg_path, dest_path)
            return dest_path

        mock_fetch.side_effect = fake_fetch

        # Set environment
        monkeypatch.setenv("PACKAGE_URL", "https://example.com/test.apkg")
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        # Create runtime without package_path (should use PACKAGE_URL)
        rt = ThreeSurfaceRuntime()
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
                assert ok, "Runtime should start with PACKAGE_URL"

            # Verify fetch was called
            assert mock_fetch.called
            call_args = mock_fetch.call_args
            assert str(call_args[0][0].packageUrl) == "https://example.com/test.apkg"

        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await rt.shutdown()


@pytest.mark.asyncio
async def test_package_url_s3_download(tmp_path: Path, monkeypatch):
    """Test downloading package from S3 URL."""
    rest_port = _free_port()
    a2a_port = _free_port()

    # Create a test APKG
    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)

    # Mock fetch_package_to_path
    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        def fake_fetch(location, dest_path, **kwargs):
            import shutil
            shutil.copy(apkg_path, dest_path)
            return dest_path

        mock_fetch.side_effect = fake_fetch

        # Set environment with S3 URL
        monkeypatch.setenv("PACKAGE_URL", "s3://pixell-agent-packages/test.apkg")
        monkeypatch.setenv("S3_BUCKET", "pixell-agent-packages")
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        rt = ThreeSurfaceRuntime()
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
                assert ok, "Runtime should start with S3 PACKAGE_URL"

            # Verify fetch was called with S3 URL
            assert mock_fetch.called
            call_args = mock_fetch.call_args
            location = call_args[0][0]
            # S3 URLs are now in the s3 field, not packageUrl
            assert location.s3 is not None
            assert location.s3.bucket == "pixell-agent-packages"
            assert location.s3.key == "test.apkg"

        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await rt.shutdown()


@pytest.mark.asyncio
async def test_package_url_with_sha256(tmp_path: Path, monkeypatch):
    """Test package download with SHA256 validation."""
    rest_port = _free_port()
    a2a_port = _free_port()

    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)

    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        # Use a valid 64-character SHA256
        valid_sha256 = "a" * 64
        
        def fake_fetch(location, dest_path, **kwargs):
            import shutil
            shutil.copy(apkg_path, dest_path)
            # Verify SHA256 was passed
            assert "sha256" in kwargs
            assert kwargs["sha256"] == valid_sha256
            return dest_path

        mock_fetch.side_effect = fake_fetch

        monkeypatch.setenv("PACKAGE_URL", "https://example.com/test.apkg")
        monkeypatch.setenv("PACKAGE_SHA256", valid_sha256)
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        rt = ThreeSurfaceRuntime()
        task = asyncio.create_task(rt.start())

        try:
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
                assert ok

            # Verify SHA256 was passed to fetch
            assert mock_fetch.called

        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await rt.shutdown()


def test_package_url_validation_file_protocol():
    """Test that file:// URLs are rejected."""
    rt = ThreeSurfaceRuntime()
    
    with pytest.raises(ValueError, match="file:// URLs are not allowed"):
        rt._validate_package_url("file:///etc/passwd")


def test_package_url_validation_invalid_protocol():
    """Test that invalid protocols are rejected."""
    rt = ThreeSurfaceRuntime()
    
    with pytest.raises(ValueError, match="Only s3:// and https:// URLs are allowed"):
        rt._validate_package_url("ftp://example.com/test.apkg")


def test_package_url_validation_empty():
    """Test that empty URLs are rejected."""
    rt = ThreeSurfaceRuntime()
    
    with pytest.raises(ValueError, match="PACKAGE_URL cannot be empty"):
        rt._validate_package_url("")


def test_package_url_validation_s3_valid():
    """Test that valid S3 URLs are accepted."""
    rt = ThreeSurfaceRuntime()
    
    # Should not raise
    rt._validate_package_url("s3://pixell-agent-packages/test.apkg")


def test_package_url_validation_https_valid():
    """Test that valid HTTPS URLs are accepted."""
    rt = ThreeSurfaceRuntime()
    
    # Should not raise
    rt._validate_package_url("https://example.com/test.apkg")
    rt._validate_package_url("https://s3.amazonaws.com/bucket/key?signature=...")


@pytest.mark.asyncio
async def test_no_package_source_fails(monkeypatch):
    """Test that runtime fails if no package source is provided."""
    rest_port = _free_port()
    a2a_port = _free_port()

    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")
    # No PACKAGE_URL set

    rt = ThreeSurfaceRuntime()  # No package_path provided
    
    with pytest.raises(SystemExit):
        await rt.load_package()


@pytest.mark.asyncio
async def test_package_url_cleanup_on_shutdown(tmp_path: Path, monkeypatch):
    """Test that downloaded packages are cleaned up on shutdown."""
    rest_port = _free_port()
    a2a_port = _free_port()

    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)

    downloaded_dir = None

    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        def fake_fetch(location, dest_path, **kwargs):
            nonlocal downloaded_dir
            downloaded_dir = dest_path.parent
            import shutil
            shutil.copy(apkg_path, dest_path)
            return dest_path

        mock_fetch.side_effect = fake_fetch

        monkeypatch.setenv("PACKAGE_URL", "https://example.com/test.apkg")
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        rt = ThreeSurfaceRuntime()
        task = asyncio.create_task(rt.start())

        try:
            # Wait for runtime to start
            await asyncio.sleep(1.0)
            
            # Verify temp directory was created
            assert downloaded_dir is not None
            assert downloaded_dir.exists()

        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await rt.shutdown()

        # Verify temp directory was cleaned up
        assert not downloaded_dir.exists(), "Downloaded package should be cleaned up"


@pytest.mark.asyncio
async def test_max_package_size_configurable(tmp_path: Path, monkeypatch):
    """Test that MAX_PACKAGE_SIZE_MB is configurable."""
    rest_port = _free_port()
    a2a_port = _free_port()

    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)

    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        def fake_fetch(location, dest_path, **kwargs):
            import shutil
            shutil.copy(apkg_path, dest_path)
            # Verify max_size_bytes was passed correctly
            assert "max_size_bytes" in kwargs
            assert kwargs["max_size_bytes"] == 200 * 1024 * 1024  # 200MB
            return dest_path

        mock_fetch.side_effect = fake_fetch

        monkeypatch.setenv("PACKAGE_URL", "https://example.com/test.apkg")
        monkeypatch.setenv("MAX_PACKAGE_SIZE_MB", "200")
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        rt = ThreeSurfaceRuntime()
        task = asyncio.create_task(rt.start())

        try:
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
                assert ok

        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await rt.shutdown()
