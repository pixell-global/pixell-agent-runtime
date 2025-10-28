"""Unit tests for PackageDownloader."""

import pytest
import tempfile
import zipfile
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


# Tests for extract_package() method (Issue #18)

@pytest.fixture
def temp_extracted_dir():
    """Create temporary extracted directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_apkg(temp_cache_dir):
    """Create a sample .apkg (ZIP) file for testing."""
    apkg_path = temp_cache_dir / "test-package.apkg"

    # Create a valid ZIP file with agent.yaml and deploy.json
    with zipfile.ZipFile(apkg_path, 'w') as zf:
        zf.writestr("agent.yaml", "name: test-agent\nversion: 1.0.0\n")
        zf.writestr("deploy.json", '{"environment": {"TEST_VAR": "test_value"}}')
        zf.writestr("requirements.txt", "requests==2.28.0\n")

    return apkg_path


def test_extract_package_success(temp_cache_dir, temp_extracted_dir, sample_apkg):
    """Test successful package extraction."""
    downloader = PackageDownloader(
        cache_dir=temp_cache_dir,
        extracted_dir=temp_extracted_dir
    )

    sha256 = "abc123def456"
    extracted_dir = downloader.extract_package(sample_apkg, package_sha256=sha256)

    # Verify extraction directory created
    assert extracted_dir.exists()
    assert extracted_dir.is_dir()

    # Verify files extracted
    assert (extracted_dir / "agent.yaml").exists()
    assert (extracted_dir / "deploy.json").exists()
    assert (extracted_dir / "requirements.txt").exists()

    # Verify content
    agent_yaml = (extracted_dir / "agent.yaml").read_text()
    assert "test-agent" in agent_yaml


def test_extract_package_reuse_cached(temp_cache_dir, temp_extracted_dir, sample_apkg):
    """Test extraction reuses cached extracted directory."""
    downloader = PackageDownloader(
        cache_dir=temp_cache_dir,
        extracted_dir=temp_extracted_dir
    )

    sha256 = "abc123def456"

    # First extraction
    extracted_dir1 = downloader.extract_package(sample_apkg, package_sha256=sha256)
    assert extracted_dir1.exists()

    # Add marker file to verify reuse
    marker_file = extracted_dir1 / "marker.txt"
    marker_file.write_text("first extraction")

    # Second extraction with same SHA256 - should reuse
    extracted_dir2 = downloader.extract_package(sample_apkg, package_sha256=sha256)

    # Should return same directory
    assert extracted_dir2 == extracted_dir1

    # Marker should still exist (not re-extracted)
    assert marker_file.exists()
    assert marker_file.read_text() == "first extraction"


def test_extract_package_force_extract(temp_cache_dir, temp_extracted_dir, sample_apkg):
    """Test force_extract re-extracts even if cached."""
    downloader = PackageDownloader(
        cache_dir=temp_cache_dir,
        extracted_dir=temp_extracted_dir
    )

    sha256 = "abc123def456"

    # First extraction
    extracted_dir1 = downloader.extract_package(sample_apkg, package_sha256=sha256)

    # Add marker file
    marker_file = extracted_dir1 / "marker.txt"
    marker_file.write_text("old marker")

    # Force re-extraction
    extracted_dir2 = downloader.extract_package(
        sample_apkg,
        package_sha256=sha256,
        force_extract=True
    )

    # Should return same directory path
    assert extracted_dir2 == extracted_dir1

    # Marker should NOT exist (re-extracted)
    assert not marker_file.exists()


def test_extract_package_incomplete_cached(temp_cache_dir, temp_extracted_dir, sample_apkg):
    """Test extraction re-extracts if cached directory is incomplete."""
    downloader = PackageDownloader(
        cache_dir=temp_cache_dir,
        extracted_dir=temp_extracted_dir
    )

    sha256 = "abc123def456"
    cache_key = sha256[:16]
    incomplete_dir = temp_extracted_dir / cache_key

    # Create incomplete extracted directory (missing agent.yaml)
    incomplete_dir.mkdir(parents=True)
    (incomplete_dir / "some_file.txt").write_text("incomplete")

    # Extract - should detect incomplete and re-extract
    extracted_dir = downloader.extract_package(sample_apkg, package_sha256=sha256)

    # Should have agent.yaml now (complete extraction)
    assert (extracted_dir / "agent.yaml").exists()
    assert (extracted_dir / "deploy.json").exists()


def test_extract_package_without_sha256(temp_cache_dir, temp_extracted_dir, sample_apkg):
    """Test extraction works without SHA256 (uses file hash)."""
    downloader = PackageDownloader(
        cache_dir=temp_cache_dir,
        extracted_dir=temp_extracted_dir
    )

    # Extract without providing SHA256
    extracted_dir = downloader.extract_package(sample_apkg)

    # Should still work
    assert extracted_dir.exists()
    assert (extracted_dir / "agent.yaml").exists()


def test_extract_package_invalid_zip(temp_cache_dir, temp_extracted_dir):
    """Test extraction fails gracefully for invalid ZIP file."""
    downloader = PackageDownloader(
        cache_dir=temp_cache_dir,
        extracted_dir=temp_extracted_dir
    )

    # Create invalid ZIP file
    invalid_apkg = temp_cache_dir / "invalid.apkg"
    invalid_apkg.write_text("This is not a ZIP file")

    # Should raise ValueError
    with pytest.raises(ValueError, match="not a valid ZIP"):
        downloader.extract_package(invalid_apkg)


def test_extract_package_missing_file(temp_cache_dir, temp_extracted_dir):
    """Test extraction fails for non-existent file."""
    downloader = PackageDownloader(
        cache_dir=temp_cache_dir,
        extracted_dir=temp_extracted_dir
    )

    missing_apkg = temp_cache_dir / "missing.apkg"

    # Should raise ValueError
    with pytest.raises(ValueError, match="not found"):
        downloader.extract_package(missing_apkg)


def test_extract_package_zipslip_absolute_path(temp_cache_dir, temp_extracted_dir):
    """Test extraction rejects absolute paths (zip-slip protection)."""
    downloader = PackageDownloader(
        cache_dir=temp_cache_dir,
        extracted_dir=temp_extracted_dir
    )

    # Create malicious ZIP with absolute path
    malicious_apkg = temp_cache_dir / "malicious.apkg"
    with zipfile.ZipFile(malicious_apkg, 'w') as zf:
        zf.writestr("agent.yaml", "name: evil\n")
        # Try to write to absolute path (zip-slip attack)
        zf.writestr("/etc/passwd", "evil content")

    # Should raise ValueError
    with pytest.raises(ValueError, match="Zip-slip"):
        downloader.extract_package(malicious_apkg)


def test_extract_package_zipslip_parent_traversal(temp_cache_dir, temp_extracted_dir):
    """Test extraction rejects parent directory traversal (zip-slip protection)."""
    downloader = PackageDownloader(
        cache_dir=temp_cache_dir,
        extracted_dir=temp_extracted_dir
    )

    # Create malicious ZIP with parent traversal
    malicious_apkg = temp_cache_dir / "malicious.apkg"
    with zipfile.ZipFile(malicious_apkg, 'w') as zf:
        zf.writestr("agent.yaml", "name: evil\n")
        # Try to escape extraction directory
        zf.writestr("../../../etc/passwd", "evil content")

    # Should raise ValueError
    with pytest.raises(ValueError, match="Zip-slip"):
        downloader.extract_package(malicious_apkg)


def test_extract_package_cleanup_on_failure(temp_cache_dir, temp_extracted_dir):
    """Test extraction cleans up partial extraction on failure."""
    downloader = PackageDownloader(
        cache_dir=temp_cache_dir,
        extracted_dir=temp_extracted_dir
    )

    # Create invalid ZIP that will fail during extraction
    invalid_apkg = temp_cache_dir / "invalid.apkg"
    invalid_apkg.write_text("corrupted zip data")

    sha256 = "test_cleanup_123"

    # Attempt extraction - should fail
    with pytest.raises(ValueError):
        downloader.extract_package(invalid_apkg, package_sha256=sha256)

    # Verify extraction directory was cleaned up
    cache_key = sha256[:16]
    extract_dir = temp_extracted_dir / cache_key
    assert not extract_dir.exists()
