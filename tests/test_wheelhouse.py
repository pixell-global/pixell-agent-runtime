"""
Tests for wheelhouse cache management.
"""

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pixell_runtime.core.wheelhouse import WheelhouseManager, get_wheelhouse_manager


def test_wheelhouse_manager_no_dir(monkeypatch):
    """Test WheelhouseManager with no directory specified."""
    monkeypatch.delenv("WHEELHOUSE_DIR", raising=False)
    
    wh = WheelhouseManager()
    
    assert wh.wheelhouse_dir is None
    assert not wh.is_available()
    assert not wh.validate()


def test_wheelhouse_manager_from_env(tmp_path, monkeypatch):
    """Test WheelhouseManager reads from WHEELHOUSE_DIR env var."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    monkeypatch.setenv("WHEELHOUSE_DIR", str(wheelhouse_dir))
    
    wh = WheelhouseManager()
    
    assert wh.wheelhouse_dir == wheelhouse_dir
    assert wh.is_available()


def test_wheelhouse_manager_explicit_dir(tmp_path):
    """Test WheelhouseManager with explicit directory."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    
    assert wh.wheelhouse_dir == wheelhouse_dir
    assert wh.is_available()


def test_wheelhouse_manager_nonexistent_dir(tmp_path):
    """Test WheelhouseManager with non-existent directory."""
    wheelhouse_dir = tmp_path / "nonexistent"
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    
    assert not wh.is_available()
    assert not wh.validate()


def test_wheelhouse_manager_file_not_dir(tmp_path):
    """Test WheelhouseManager when path is a file, not directory."""
    wheelhouse_file = tmp_path / "wheelhouse.txt"
    wheelhouse_file.write_text("not a directory")
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_file)
    
    assert not wh.is_available()


def test_wheelhouse_validate_empty(tmp_path):
    """Test validate with empty wheelhouse directory."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    
    # Empty wheelhouse is valid, just not useful
    assert wh.validate()
    assert wh._validated


def test_wheelhouse_validate_with_wheels(tmp_path):
    """Test validate with wheel files present."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    # Create some fake wheel files
    (wheelhouse_dir / "package1-1.0.0-py3-none-any.whl").touch()
    (wheelhouse_dir / "package2-2.0.0-py3-none-any.whl").touch()
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    
    assert wh.validate()
    assert wh._validated


def test_wheelhouse_get_wheel_files(tmp_path):
    """Test getting list of wheel files."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    # Create wheel files
    wheel1 = wheelhouse_dir / "package1-1.0.0-py3-none-any.whl"
    wheel2 = wheelhouse_dir / "package2-2.0.0-py3-none-any.whl"
    wheel1.touch()
    wheel2.touch()
    
    # Create non-wheel file (should be ignored)
    (wheelhouse_dir / "readme.txt").touch()
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    wheels = wh.get_wheel_files()
    
    assert len(wheels) == 2
    assert wheel1 in wheels
    assert wheel2 in wheels


def test_wheelhouse_get_wheel_files_empty(tmp_path):
    """Test getting wheel files from empty wheelhouse."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    wheels = wh.get_wheel_files()
    
    assert wheels == []


def test_wheelhouse_get_wheel_files_not_available():
    """Test getting wheel files when wheelhouse not available."""
    wh = WheelhouseManager(wheelhouse_dir=None)
    wheels = wh.get_wheel_files()
    
    assert wheels == []


def test_wheelhouse_get_package_names(tmp_path):
    """Test extracting package names from wheel files."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    # Create wheel files with various naming conventions
    (wheelhouse_dir / "requests-2.28.0-py3-none-any.whl").touch()
    (wheelhouse_dir / "urllib3-1.26.0-py2.py3-none-any.whl").touch()
    (wheelhouse_dir / "my_package-1.0.0-py3-none-any.whl").touch()
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    packages = wh.get_package_names()
    
    assert "requests" in packages
    assert "urllib3" in packages
    # Package names are normalized (underscore -> hyphen)
    assert "my-package" in packages


def test_wheelhouse_get_package_names_empty(tmp_path):
    """Test getting package names from empty wheelhouse."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    packages = wh.get_package_names()
    
    assert packages == set()


def test_wheelhouse_get_pip_install_args_online(tmp_path):
    """Test getting pip install args for online mode."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    args = wh.get_pip_install_args(offline_mode=False)
    
    assert "--find-links" in args
    assert str(wheelhouse_dir) in args
    assert "--no-index" not in args


def test_wheelhouse_get_pip_install_args_offline(tmp_path):
    """Test getting pip install args for offline mode."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    args = wh.get_pip_install_args(offline_mode=True)
    
    assert "--no-index" in args
    assert "--find-links" in args
    assert str(wheelhouse_dir) in args


def test_wheelhouse_get_pip_install_args_not_available():
    """Test getting pip install args when wheelhouse not available."""
    wh = WheelhouseManager(wheelhouse_dir=None)
    args = wh.get_pip_install_args()
    
    assert args == []


def test_wheelhouse_download_packages_no_dir():
    """Test downloading packages with no wheelhouse directory."""
    wh = WheelhouseManager(wheelhouse_dir=None)
    
    result = wh.download_packages(Path("requirements.txt"))
    
    assert result is False


def test_wheelhouse_download_packages_creates_dir(tmp_path):
    """Test that download_packages creates wheelhouse directory."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("requests==2.28.0\n")
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    
    # Mock subprocess to avoid actual download
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stderr="")
        
        result = wh.download_packages(req_file)
        
        assert result is True
        assert wheelhouse_dir.exists()
        assert wheelhouse_dir.is_dir()


def test_wheelhouse_download_packages_nonexistent_requirements(tmp_path):
    """Test downloading packages with non-existent requirements file."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    req_file = tmp_path / "nonexistent.txt"
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    
    result = wh.download_packages(req_file)
    
    assert result is False


def test_wheelhouse_download_packages_success(tmp_path):
    """Test successful package download."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("requests==2.28.0\n")
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stderr="")
        
        result = wh.download_packages(req_file)
        
        assert result is True
        assert mock_run.called
        
        # Check command structure
        cmd = mock_run.call_args[0][0]
        assert "pip" in str(cmd)
        assert "download" in cmd
        assert str(req_file) in cmd
        assert str(wheelhouse_dir) in cmd


def test_wheelhouse_download_packages_failure(tmp_path):
    """Test failed package download."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("nonexistent-package==999.999.999\n")
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=1, stderr="Package not found")
        
        result = wh.download_packages(req_file)
        
        assert result is False


def test_wheelhouse_download_packages_timeout(tmp_path):
    """Test package download timeout."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("requests==2.28.0\n")
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired("pip", 300)
        
        result = wh.download_packages(req_file)
        
        assert result is False


def test_wheelhouse_get_cache_info_not_available():
    """Test getting cache info when wheelhouse not available."""
    wh = WheelhouseManager(wheelhouse_dir=None)
    
    info = wh.get_cache_info()
    
    assert info["available"] is False
    assert info["wheelhouse_dir"] is None


def test_wheelhouse_get_cache_info(tmp_path):
    """Test getting cache info."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    # Create some wheel files
    wheel1 = wheelhouse_dir / "requests-2.28.0-py3-none-any.whl"
    wheel2 = wheelhouse_dir / "urllib3-1.26.0-py2.py3-none-any.whl"
    wheel1.write_bytes(b"x" * 1000)  # 1KB
    wheel2.write_bytes(b"y" * 2000)  # 2KB
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    wh.validate()
    
    info = wh.get_cache_info()
    
    assert info["available"] is True
    assert info["validated"] is True
    assert info["wheel_count"] == 2
    assert info["total_size_bytes"] == 3000
    assert "requests" in info["packages"]
    assert "urllib3" in info["packages"]


def test_wheelhouse_clear_cache(tmp_path):
    """Test clearing wheelhouse cache."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    # Create wheel files
    wheel1 = wheelhouse_dir / "package1-1.0.0-py3-none-any.whl"
    wheel2 = wheelhouse_dir / "package2-2.0.0-py3-none-any.whl"
    wheel1.touch()
    wheel2.touch()
    
    # Create non-wheel file (should not be deleted)
    readme = wheelhouse_dir / "readme.txt"
    readme.touch()
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    
    result = wh.clear_cache()
    
    assert result is True
    assert not wheel1.exists()
    assert not wheel2.exists()
    assert readme.exists()  # Non-wheel files preserved


def test_wheelhouse_clear_cache_not_available():
    """Test clearing cache when wheelhouse not available."""
    wh = WheelhouseManager(wheelhouse_dir=None)
    
    result = wh.clear_cache()
    
    assert result is False


def test_get_wheelhouse_manager_from_env(tmp_path, monkeypatch):
    """Test get_wheelhouse_manager factory function."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    monkeypatch.setenv("WHEELHOUSE_DIR", str(wheelhouse_dir))
    
    wh = get_wheelhouse_manager()
    
    assert wh.wheelhouse_dir == wheelhouse_dir
    assert wh.is_available()


def test_get_wheelhouse_manager_no_env(monkeypatch):
    """Test get_wheelhouse_manager with no env var."""
    monkeypatch.delenv("WHEELHOUSE_DIR", raising=False)
    
    wh = get_wheelhouse_manager()
    
    assert wh.wheelhouse_dir is None
    assert not wh.is_available()


def test_wheelhouse_validate_permission_error(tmp_path, monkeypatch):
    """Test validate with permission error."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    
    # Mock iterdir to raise PermissionError
    with patch.object(Path, "iterdir", side_effect=PermissionError("No access")):
        result = wh.validate()
        
        assert result is False
        assert not wh._validated


def test_wheelhouse_package_name_normalization(tmp_path):
    """Test that package names are normalized correctly."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    # Create wheels with various naming conventions
    (wheelhouse_dir / "My_Package-1.0.0-py3-none-any.whl").touch()
    (wheelhouse_dir / "another_package-2.0.0-py3-none-any.whl").touch()
    (wheelhouse_dir / "YetAnother-3.0.0-py3-none-any.whl").touch()
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    packages = wh.get_package_names()
    
    # All should be lowercase and use hyphens
    assert "my-package" in packages
    assert "another-package" in packages
    assert "yetanother" in packages


def test_wheelhouse_download_with_custom_python(tmp_path):
    """Test downloading packages with custom Python executable."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    req_file = tmp_path / "requirements.txt"
    req_file.write_text("requests==2.28.0\n")
    
    custom_python = Path("/usr/bin/python3.9")
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = Mock(returncode=0, stderr="")
        
        result = wh.download_packages(req_file, python_executable=custom_python)
        
        assert result is True
        
        # Check that custom Python was used
        cmd = mock_run.call_args[0][0]
        assert str(custom_python) in cmd


def test_wheelhouse_get_cache_info_size_calculation(tmp_path):
    """Test cache info size calculation."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    # Create wheel with known size
    wheel = wheelhouse_dir / "package-1.0.0-py3-none-any.whl"
    wheel.write_bytes(b"x" * (5 * 1024 * 1024))  # 5MB
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    info = wh.get_cache_info()
    
    assert info["total_size_bytes"] == 5 * 1024 * 1024
    assert info["total_size_mb"] == 5.0


def test_wheelhouse_multiple_versions_same_package(tmp_path):
    """Test wheelhouse with multiple versions of same package."""
    wheelhouse_dir = tmp_path / "wheelhouse"
    wheelhouse_dir.mkdir()
    
    # Create multiple versions
    (wheelhouse_dir / "requests-2.28.0-py3-none-any.whl").touch()
    (wheelhouse_dir / "requests-2.27.0-py3-none-any.whl").touch()
    (wheelhouse_dir / "requests-2.26.0-py3-none-any.whl").touch()
    
    wh = WheelhouseManager(wheelhouse_dir=wheelhouse_dir)
    
    wheels = wh.get_wheel_files()
    assert len(wheels) == 3
    
    # Package name should appear once (deduplicated)
    packages = wh.get_package_names()
    assert len([p for p in packages if p == "requests"]) == 1
