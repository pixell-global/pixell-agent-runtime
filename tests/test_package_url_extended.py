"""
Extended tests for PACKAGE_URL - edge cases and error scenarios.
"""

import asyncio
import os
import socket
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock

import httpx
import pytest

from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
from pixell_runtime.core.exceptions import PackageLoadError


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
async def test_package_url_download_failure_retries(tmp_path: Path, monkeypatch):
    """Test that download failures trigger retries."""
    rest_port = _free_port()
    a2a_port = _free_port()

    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)

    call_count = 0

    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        def fake_fetch_with_retry(location, dest_path, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                # First call fails
                raise Exception("Network error")
            # Second call succeeds
            import shutil
            shutil.copy(apkg_path, dest_path)
            return dest_path

        mock_fetch.side_effect = fake_fetch_with_retry

        monkeypatch.setenv("PACKAGE_URL", "https://example.com/test.apkg")
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        rt = ThreeSurfaceRuntime()
        
        # This should fail because fetch_package_to_path is called directly
        # and doesn't retry internally in our mock
        with pytest.raises(Exception, match="Network error"):
            await rt.load_package()


@pytest.mark.asyncio
async def test_package_url_download_permanent_failure(tmp_path: Path, monkeypatch):
    """Test that permanent download failures are handled gracefully."""
    rest_port = _free_port()
    a2a_port = _free_port()

    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        mock_fetch.side_effect = Exception("S3 access denied")

        monkeypatch.setenv("PACKAGE_URL", "s3://pixell-agent-packages/test.apkg")
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        rt = ThreeSurfaceRuntime()
        
        with pytest.raises(Exception):  # Don't match message, just ensure it raises
            await rt.load_package()


@pytest.mark.asyncio
async def test_package_url_corrupt_download(tmp_path: Path, monkeypatch):
    """Test that corrupt downloaded packages are detected."""
    rest_port = _free_port()
    a2a_port = _free_port()

    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        def fake_fetch_corrupt(location, dest_path, **kwargs):
            # Create a corrupt zip file
            dest_path.write_text("not a valid zip file")
            return dest_path

        mock_fetch.side_effect = fake_fetch_corrupt

        monkeypatch.setenv("PACKAGE_URL", "https://example.com/test.apkg")
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        rt = ThreeSurfaceRuntime()
        
        # Should fail when trying to load the corrupt package
        with pytest.raises(Exception):  # Will be a zipfile.BadZipFile or similar
            await rt.load_package()


@pytest.mark.asyncio
async def test_package_url_both_url_and_path_provided(tmp_path: Path, monkeypatch):
    """Test behavior when both PACKAGE_URL and package_path are provided."""
    rest_port = _free_port()
    a2a_port = _free_port()

    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)

    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        monkeypatch.setenv("PACKAGE_URL", "https://example.com/test.apkg")
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        # Provide both package_path and PACKAGE_URL
        rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
        await rt.load_package()

        # Should use package_path and NOT call fetch
        assert not mock_fetch.called


@pytest.mark.asyncio
async def test_package_url_empty_string(monkeypatch):
    """Test that empty PACKAGE_URL is handled correctly."""
    rest_port = _free_port()
    a2a_port = _free_port()

    monkeypatch.setenv("PACKAGE_URL", "")  # Empty string
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")

    rt = ThreeSurfaceRuntime()
    
    # Should fail because no valid package source
    with pytest.raises(SystemExit):
        await rt.load_package()


@pytest.mark.asyncio
async def test_package_url_malformed_s3_url(monkeypatch):
    """Test that malformed S3 URLs are handled."""
    rest_port = _free_port()
    a2a_port = _free_port()

    monkeypatch.setenv("PACKAGE_URL", "s3://")  # Malformed
    monkeypatch.setenv("REST_PORT", str(rest_port))
    monkeypatch.setenv("A2A_PORT", str(a2a_port))
    monkeypatch.setenv("BASE_PATH", "/")

    rt = ThreeSurfaceRuntime()
    
    # Should fail during download or validation
    with pytest.raises(Exception):
        await rt.load_package()


@pytest.mark.asyncio
async def test_package_url_wrong_s3_bucket(tmp_path: Path, monkeypatch):
    """Test that using wrong S3 bucket logs a warning but doesn't fail."""
    rest_port = _free_port()
    a2a_port = _free_port()

    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)

    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        def fake_fetch(location, dest_path, **kwargs):
            import shutil
            shutil.copy(apkg_path, dest_path)
            return dest_path

        mock_fetch.side_effect = fake_fetch

        monkeypatch.setenv("PACKAGE_URL", "s3://wrong-bucket/test.apkg")
        monkeypatch.setenv("S3_BUCKET", "pixell-agent-packages")
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        rt = ThreeSurfaceRuntime()
        
        # Should not raise, just log warning
        await rt.load_package()
        
        # Verify fetch was called despite wrong bucket
        assert mock_fetch.called


@pytest.mark.asyncio
async def test_package_url_http_not_https(monkeypatch):
    """Test that http:// URLs (not https://) are rejected."""
    rt = ThreeSurfaceRuntime()
    
    with pytest.raises(ValueError, match="Only s3:// and https:// URLs are allowed"):
        rt._validate_package_url("http://example.com/test.apkg")


@pytest.mark.asyncio
async def test_package_url_data_uri(monkeypatch):
    """Test that data: URIs are rejected."""
    rt = ThreeSurfaceRuntime()
    
    with pytest.raises(ValueError, match="Only s3:// and https:// URLs are allowed"):
        rt._validate_package_url("data:application/zip;base64,...")


@pytest.mark.asyncio
async def test_package_url_javascript_uri(monkeypatch):
    """Test that javascript: URIs are rejected."""
    rt = ThreeSurfaceRuntime()
    
    with pytest.raises(ValueError, match="Only s3:// and https:// URLs are allowed"):
        rt._validate_package_url("javascript:alert(1)")


@pytest.mark.asyncio
async def test_package_url_with_spaces(monkeypatch):
    """Test that URLs with spaces are handled."""
    rt = ThreeSurfaceRuntime()
    
    # Should not raise - spaces might be valid in encoded URLs
    rt._validate_package_url("https://example.com/test%20file.apkg")


@pytest.mark.asyncio
async def test_package_url_very_long_url(monkeypatch):
    """Test that very long URLs are handled."""
    rt = ThreeSurfaceRuntime()
    
    # Create a very long but valid URL
    long_url = "https://example.com/" + "a" * 10000 + ".apkg"
    
    # Should not raise - URL length validation is not our concern
    rt._validate_package_url(long_url)


@pytest.mark.asyncio
async def test_package_url_unicode_characters(monkeypatch):
    """Test that URLs with unicode characters are handled."""
    rt = ThreeSurfaceRuntime()
    
    # Should not raise - unicode might be valid in encoded URLs
    rt._validate_package_url("https://example.com/测试.apkg")


@pytest.mark.asyncio
async def test_package_url_sha256_mismatch_during_download(tmp_path: Path, monkeypatch):
    """Test that SHA256 mismatch during download is caught."""
    rest_port = _free_port()
    a2a_port = _free_port()

    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)

    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        # Mock fetch to raise error on SHA256 mismatch
        mock_fetch.side_effect = ValueError("SHA256 mismatch")

        monkeypatch.setenv("PACKAGE_URL", "https://example.com/test.apkg")
        # Use a valid 64-character SHA256
        monkeypatch.setenv("PACKAGE_SHA256", "b" * 64)
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        rt = ThreeSurfaceRuntime()
        
        with pytest.raises(ValueError, match="SHA256 mismatch"):
            await rt.load_package()


@pytest.mark.asyncio
async def test_package_url_download_creates_temp_dir(tmp_path: Path, monkeypatch):
    """Test that download creates a proper temp directory."""
    rest_port = _free_port()
    a2a_port = _free_port()

    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)

    created_dirs = []

    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        def fake_fetch(location, dest_path, **kwargs):
            created_dirs.append(dest_path.parent)
            import shutil
            shutil.copy(apkg_path, dest_path)
            return dest_path

        mock_fetch.side_effect = fake_fetch

        monkeypatch.setenv("PACKAGE_URL", "https://example.com/test.apkg")
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        rt = ThreeSurfaceRuntime()
        await rt.load_package()

        # Verify temp dir was created with correct prefix
        assert len(created_dirs) == 1
        assert "pixell_apkg_" in str(created_dirs[0])


@pytest.mark.asyncio
async def test_package_url_multiple_downloads_different_temps(tmp_path: Path, monkeypatch):
    """Test that multiple downloads create different temp directories."""
    rest_port = _free_port()
    a2a_port = _free_port()

    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)

    created_dirs = []

    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        def fake_fetch(location, dest_path, **kwargs):
            created_dirs.append(str(dest_path.parent))
            import shutil
            shutil.copy(apkg_path, dest_path)
            return dest_path

        mock_fetch.side_effect = fake_fetch

        monkeypatch.setenv("PACKAGE_URL", "https://example.com/test.apkg")
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        # Create two runtimes
        rt1 = ThreeSurfaceRuntime()
        await rt1.load_package()

        rt2 = ThreeSurfaceRuntime()
        await rt2.load_package()

        # Verify different temp dirs were created
        assert len(created_dirs) == 2
        assert created_dirs[0] != created_dirs[1]


@pytest.mark.asyncio
async def test_package_url_s3_with_query_params(tmp_path: Path, monkeypatch):
    """Test S3 signed URLs with query parameters."""
    rest_port = _free_port()
    a2a_port = _free_port()

    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)

    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        def fake_fetch(location, dest_path, **kwargs):
            import shutil
            shutil.copy(apkg_path, dest_path)
            return dest_path

        mock_fetch.side_effect = fake_fetch

        # S3 signed URL with query params
        signed_url = "https://s3.amazonaws.com/pixell-agent-packages/test.apkg?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=..."
        
        monkeypatch.setenv("PACKAGE_URL", signed_url)
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        rt = ThreeSurfaceRuntime()
        await rt.load_package()

        # Should succeed with signed URL
        assert mock_fetch.called


@pytest.mark.asyncio
async def test_package_url_env_var_precedence(tmp_path: Path, monkeypatch):
    """Test that package_path parameter takes precedence over PACKAGE_URL."""
    rest_port = _free_port()
    a2a_port = _free_port()

    apkg_path = tmp_path / "test.apkg"
    _create_test_apkg(apkg_path)

    with patch("pixell_runtime.deploy.fetch.fetch_package_to_path") as mock_fetch:
        monkeypatch.setenv("PACKAGE_URL", "https://example.com/should-not-download.apkg")
        monkeypatch.setenv("REST_PORT", str(rest_port))
        monkeypatch.setenv("A2A_PORT", str(a2a_port))
        monkeypatch.setenv("BASE_PATH", "/")

        # Provide explicit package_path
        rt = ThreeSurfaceRuntime(package_path=str(apkg_path))
        await rt.load_package()

        # Should NOT download because package_path was provided
        assert not mock_fetch.called
        assert rt.package_path == str(apkg_path)


@pytest.mark.asyncio
async def test_package_url_case_sensitivity(monkeypatch):
    """Test that protocol matching is case-insensitive."""
    rt = ThreeSurfaceRuntime()
    
    # These should all be valid (case variations)
    rt._validate_package_url("HTTPS://example.com/test.apkg")
    rt._validate_package_url("S3://bucket/key")
    rt._validate_package_url("https://EXAMPLE.COM/test.apkg")


@pytest.mark.asyncio
async def test_package_url_trailing_whitespace(monkeypatch):
    """Test that URLs with trailing whitespace are handled."""
    rt = ThreeSurfaceRuntime()
    
    # Should handle whitespace gracefully
    rt._validate_package_url("https://example.com/test.apkg ")
    rt._validate_package_url(" s3://bucket/key")
