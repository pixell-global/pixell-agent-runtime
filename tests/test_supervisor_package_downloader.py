"""Unit tests for PackageDownloader."""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from pixell_runtime.supervisor.package_downloader import PackageDownloader


@pytest.fixture
def temp_cache_dir():
    """Create temporary cache directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def test_package_downloader_init(temp_cache_dir):
    """Test PackageDownloader initialization."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir, max_package_size_mb=50)

    assert downloader.cache_dir == temp_cache_dir
    assert downloader.max_package_size_bytes == 50 * 1024 * 1024
    assert temp_cache_dir.exists()


def test_get_cache_key_with_sha256(temp_cache_dir):
    """Test cache key generation with SHA256."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    sha256 = "abcdef1234567890" * 4  # 64 chars
    cache_key = downloader._get_cache_key("s3://bucket/key", sha256)

    # Should use first 16 chars of SHA256
    assert cache_key == "abcdef1234567890"


def test_get_cache_key_without_sha256(temp_cache_dir):
    """Test cache key generation without SHA256."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    url = "s3://bucket/test-package.apkg"
    cache_key = downloader._get_cache_key(url, None)

    # Should hash the URL
    assert len(cache_key) == 16
    assert cache_key.isalnum()


def test_get_cache_path(temp_cache_dir):
    """Test cache path generation."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    cache_key = "abc123"
    cache_path = downloader._get_cache_path(cache_key)

    assert cache_path == temp_cache_dir / "abc123.apkg"


def test_is_cached_not_exists(temp_cache_dir):
    """Test is_cached returns False for non-existent package."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    url = "s3://bucket/package.apkg"
    assert downloader.is_cached(url) is False


def test_is_cached_exists(temp_cache_dir):
    """Test is_cached returns True for existing package."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    url = "s3://bucket/package.apkg"
    cache_key = downloader._get_cache_key(url, None)
    cache_path = downloader._get_cache_path(cache_key)

    # Create dummy cached file
    cache_path.write_text("dummy package")

    assert downloader.is_cached(url) is True


def test_get_cached_path_exists(temp_cache_dir):
    """Test get_cached_path returns path for existing package."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    url = "s3://bucket/package.apkg"
    cache_key = downloader._get_cache_key(url, None)
    cache_path = downloader._get_cache_path(cache_key)

    # Create dummy cached file
    cache_path.write_text("dummy package")

    result = downloader.get_cached_path(url)
    assert result == cache_path
    assert result.exists()


def test_get_cached_path_not_exists(temp_cache_dir):
    """Test get_cached_path returns None for non-existent package."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    url = "s3://bucket/package.apkg"
    result = downloader.get_cached_path(url)
    assert result is None


def test_delete_cached_exists(temp_cache_dir):
    """Test delete_cached removes existing package."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    url = "s3://bucket/package.apkg"
    cache_key = downloader._get_cache_key(url, None)
    cache_path = downloader._get_cache_path(cache_key)

    # Create dummy cached file
    cache_path.write_text("dummy package")
    assert cache_path.exists()

    # Delete it
    result = downloader.delete_cached(url)
    assert result is True
    assert not cache_path.exists()


def test_delete_cached_not_exists(temp_cache_dir):
    """Test delete_cached returns False for non-existent package."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    url = "s3://bucket/package.apkg"
    result = downloader.delete_cached(url)
    assert result is False


def test_clear_cache(temp_cache_dir):
    """Test clear_cache removes all packages."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    # Create multiple dummy packages
    for i in range(5):
        path = temp_cache_dir / f"package_{i}.apkg"
        path.write_text(f"dummy package {i}")

    # Verify they exist
    assert len(list(temp_cache_dir.glob("*.apkg"))) == 5

    # Clear cache
    count = downloader.clear_cache()
    assert count == 5
    assert len(list(temp_cache_dir.glob("*.apkg"))) == 0


@patch("pixell_runtime.supervisor.package_downloader.fetch_package_to_path")
def test_download_not_cached(mock_fetch, temp_cache_dir):
    """Test download fetches package when not cached."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    url = "s3://bucket/package.apkg"
    sha256 = "abc123"

    # Mock successful download
    def mock_fetch_side_effect(location, dest_path, **kwargs):
        dest_path.write_text("downloaded package")
        return dest_path

    mock_fetch.side_effect = mock_fetch_side_effect

    # Download
    result = downloader.download(url, sha256)

    # Verify fetch was called
    assert mock_fetch.called
    assert result.exists()
    assert result.read_text() == "downloaded package"


@patch("pixell_runtime.supervisor.package_downloader.fetch_package_to_path")
def test_download_cached_no_force(mock_fetch, temp_cache_dir):
    """Test download uses cache when available and force_refresh=False."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    url = "s3://bucket/package.apkg"
    cache_key = downloader._get_cache_key(url, None)
    cache_path = downloader._get_cache_path(cache_key)

    # Create cached package
    cache_path.write_text("cached package")

    # Download with force_refresh=False (default)
    result = downloader.download(url, force_refresh=False)

    # Should NOT call fetch
    assert not mock_fetch.called
    assert result == cache_path
    assert result.read_text() == "cached package"


@patch("pixell_runtime.supervisor.package_downloader.fetch_package_to_path")
def test_download_cached_with_force(mock_fetch, temp_cache_dir):
    """Test download re-fetches when force_refresh=True."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    url = "s3://bucket/package.apkg"
    cache_key = downloader._get_cache_key(url, None)
    cache_path = downloader._get_cache_path(cache_key)

    # Create cached package
    cache_path.write_text("old cached package")

    # Mock successful download
    def mock_fetch_side_effect(location, dest_path, **kwargs):
        dest_path.write_text("newly downloaded package")
        return dest_path

    mock_fetch.side_effect = mock_fetch_side_effect

    # Download with force_refresh=True
    result = downloader.download(url, force_refresh=True)

    # Should call fetch even though cached
    assert mock_fetch.called
    assert result.read_text() == "newly downloaded package"


@patch("pixell_runtime.supervisor.package_downloader.fetch_package_to_path")
def test_download_failure_cleanup(mock_fetch, temp_cache_dir):
    """Test download cleans up on failure."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    url = "s3://bucket/package.apkg"
    cache_key = downloader._get_cache_key(url, None)
    cache_path = downloader._get_cache_path(cache_key)

    # Mock failed download that creates partial file
    def mock_fetch_side_effect(location, dest_path, **kwargs):
        dest_path.write_text("partial download")
        raise RuntimeError("Download failed")

    mock_fetch.side_effect = mock_fetch_side_effect

    # Attempt download - should fail
    with pytest.raises(RuntimeError, match="Download failed"):
        downloader.download(url)

    # Partial file should be cleaned up
    assert not cache_path.exists()


def test_cache_key_consistency(temp_cache_dir):
    """Test cache keys are consistent for same URL."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    url = "s3://bucket/package.apkg"
    key1 = downloader._get_cache_key(url, None)
    key2 = downloader._get_cache_key(url, None)

    assert key1 == key2


def test_cache_key_different_urls(temp_cache_dir):
    """Test different URLs produce different cache keys."""
    downloader = PackageDownloader(cache_dir=temp_cache_dir)

    url1 = "s3://bucket/package1.apkg"
    url2 = "s3://bucket/package2.apkg"

    key1 = downloader._get_cache_key(url1, None)
    key2 = downloader._get_cache_key(url2, None)

    assert key1 != key2
