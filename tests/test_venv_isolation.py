"""Tests for virtual environment isolation."""

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest
import yaml

from pixell_runtime.agents.loader import PackageLoader
from pixell_runtime.core.exceptions import PackageLoadError


@pytest.fixture
def temp_dirs():
    """Create temporary directories for testing."""
    temp_root = Path(tempfile.mkdtemp())
    packages_dir = temp_root / "packages"
    venvs_dir = temp_root / "venvs"
    packages_dir.mkdir()
    venvs_dir.mkdir()

    yield packages_dir, venvs_dir

    # Cleanup
    shutil.rmtree(temp_root)


@pytest.fixture
def sample_package(temp_dirs):
    """Create a sample agent package for testing."""
    packages_dir, _ = temp_dirs

    # Create package structure
    pkg_dir = Path(tempfile.mkdtemp())

    # Create agent.yaml
    manifest = {
        "name": "test-agent",
        "version": "1.0.0",
        "description": "Test agent",
        "author": "test",
        "entrypoint": "main:app",
        "a2a": {"service": "grpc_server:AgentService"},
        "rest": {"entry": "http_main:app"},
    }

    with open(pkg_dir / "agent.yaml", "w") as f:
        yaml.dump(manifest, f)

    # Create requirements.txt
    with open(pkg_dir / "requirements.txt", "w") as f:
        f.write("fastapi==0.109.0\n")
        f.write("uvicorn==0.27.0\n")

    # Create source directory
    src_dir = pkg_dir / "src"
    src_dir.mkdir()
    (src_dir / "__init__.py").touch()

    # Create APKG file
    apkg_path = packages_dir / "test-agent.apkg"
    with zipfile.ZipFile(apkg_path, "w") as zf:
        for file_path in pkg_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(pkg_dir)
                zf.write(file_path, arcname)

    # Cleanup temp dir
    shutil.rmtree(pkg_dir)

    return apkg_path


@pytest.fixture
def loader(temp_dirs):
    """Create a PackageLoader instance."""
    packages_dir, venvs_dir = temp_dirs
    return PackageLoader(packages_dir, venvs_dir)


class TestVenvCreation:
    """Test virtual environment creation."""

    def test_venv_created_on_first_load(self, loader, sample_package):
        """Test that venv is created on first package load."""
        # Load package with agent_app_id
        agent_app_id = "test-agent-uuid-123"
        package = loader.load_package(sample_package, agent_app_id=agent_app_id)

        # Check venv was created
        assert package.venv_path is not None
        venv_path = Path(package.venv_path)
        assert venv_path.exists()
        assert (venv_path / "bin" / "python").exists()
        assert (venv_path / "bin" / "pip").exists()

        # Check venv name contains agent_app_id
        assert agent_app_id in venv_path.name

    def test_venv_has_metadata(self, loader, sample_package):
        """Test that venv metadata is stored."""
        package = loader.load_package(sample_package, agent_app_id="test-123")

        venv_path = Path(package.venv_path)
        metadata_file = venv_path / ".pixell_venv_metadata.json"

        assert metadata_file.exists()

        with open(metadata_file) as f:
            metadata = json.load(f)

        assert "package_id" in metadata
        assert "requirements_sha256" in metadata
        assert "created_at" in metadata
        assert "python_version" in metadata

    def test_venv_isolated(self, loader, sample_package):
        """Test that venv is properly isolated."""
        package = loader.load_package(sample_package, agent_app_id="test-123")

        venv_path = Path(package.venv_path)
        python_path = venv_path / "bin" / "python"

        # Run Python and check its prefix
        import subprocess
        result = subprocess.run(
            [str(python_path), "-c", "import sys; print(sys.prefix)"],
            capture_output=True,
            text=True
        )

        assert result.returncode == 0
        assert str(venv_path) in result.stdout


class TestVenvReuse:
    """Test virtual environment reuse."""

    def test_venv_reused_same_requirements(self, loader, sample_package):
        """Test that venv is reused when requirements unchanged."""
        agent_app_id = "test-reuse-123"

        # First load
        package1 = loader.load_package(sample_package, agent_app_id=agent_app_id)
        venv_path1 = Path(package1.venv_path)
        created_time1 = venv_path1.stat().st_ctime

        # Second load (same package, same agent_app_id)
        package2 = loader.load_package(sample_package, agent_app_id=agent_app_id)
        venv_path2 = Path(package2.venv_path)
        created_time2 = venv_path2.stat().st_ctime

        # Should be same venv
        assert venv_path1 == venv_path2
        assert created_time1 == created_time2

    def test_different_agent_app_ids_get_different_venvs(self, loader, sample_package):
        """Test that different agent_app_ids get different venvs."""
        # Load with agent_app_id 1
        package1 = loader.load_package(sample_package, agent_app_id="agent-aaa")
        venv1 = Path(package1.venv_path)

        # Load with agent_app_id 2
        package2 = loader.load_package(sample_package, agent_app_id="agent-bbb")
        venv2 = Path(package2.venv_path)

        # Should be different venvs
        assert venv1 != venv2
        assert venv1.exists()
        assert venv2.exists()


class TestVenvRebuild:
    """Test virtual environment rebuild scenarios."""

    def test_venv_rebuilt_on_requirements_change(self, loader, temp_dirs):
        """Test that venv is rebuilt when requirements.txt changes."""
        packages_dir, _ = temp_dirs

        # Create package with requirements v1
        def create_package(requirements_content):
            pkg_dir = Path(tempfile.mkdtemp())

            manifest = {
                "name": "test-agent",
                "version": "1.0.0",
                "description": "Test",
                "author": "test",
                "entrypoint": "main:app",
            }
            with open(pkg_dir / "agent.yaml", "w") as f:
                yaml.dump(manifest, f)

            with open(pkg_dir / "requirements.txt", "w") as f:
                f.write(requirements_content)

            (pkg_dir / "src").mkdir()
            (pkg_dir / "src" / "__init__.py").touch()

            apkg_path = packages_dir / f"test-{hash(requirements_content)}.apkg"
            with zipfile.ZipFile(apkg_path, "w") as zf:
                for file_path in pkg_dir.rglob("*"):
                    if file_path.is_file():
                        zf.write(file_path, file_path.relative_to(pkg_dir))

            shutil.rmtree(pkg_dir)
            return apkg_path

        agent_app_id = "test-rebuild-123"

        # Load package with requirements v1
        apkg1 = create_package("fastapi==0.109.0\n")
        package1 = loader.load_package(apkg1, agent_app_id=agent_app_id)
        venv1_name = Path(package1.venv_path).name

        # Load package with requirements v2 (different content)
        apkg2 = create_package("fastapi==0.110.0\n")
        package2 = loader.load_package(apkg2, agent_app_id=agent_app_id)
        venv2_name = Path(package2.venv_path).name

        # Should be different venv names (different hash)
        assert venv1_name != venv2_name

    def test_invalid_venv_rebuilt(self, loader, sample_package):
        """Test that invalid venv is detected and rebuilt."""
        agent_app_id = "test-invalid-123"

        # First load (creates valid venv)
        package1 = loader.load_package(sample_package, agent_app_id=agent_app_id)
        venv_path = Path(package1.venv_path)

        # Corrupt the venv (delete python executable)
        (venv_path / "bin" / "python").unlink()

        # Second load (should detect invalid and rebuild)
        package2 = loader.load_package(sample_package, agent_app_id=agent_app_id)

        # Should have rebuilt venv
        assert Path(package2.venv_path).exists()
        assert (Path(package2.venv_path) / "bin" / "python").exists()


class TestRequirementsHash:
    """Test requirements.txt hashing."""

    def test_requirements_hash_changes_with_content(self, loader, temp_dirs):
        """Test that hash changes when requirements.txt content changes."""
        packages_dir, _ = temp_dirs

        # Create package dir
        pkg_dir = Path(tempfile.mkdtemp())

        # Hash with content 1
        with open(pkg_dir / "requirements.txt", "w") as f:
            f.write("fastapi==0.109.0\n")
        hash1 = loader._calculate_requirements_hash(pkg_dir)

        # Hash with content 2
        with open(pkg_dir / "requirements.txt", "w") as f:
            f.write("fastapi==0.110.0\n")
        hash2 = loader._calculate_requirements_hash(pkg_dir)

        # Should be different
        assert hash1 != hash2

        shutil.rmtree(pkg_dir)

    def test_no_requirements_returns_no_deps(self, loader, temp_dirs):
        """Test that missing requirements.txt returns 'no-deps'."""
        pkg_dir = Path(tempfile.mkdtemp())

        hash_result = loader._calculate_requirements_hash(pkg_dir)

        assert hash_result == "no-deps"

        shutil.rmtree(pkg_dir)


class TestVenvValidation:
    """Test venv validation."""

    def test_valid_venv_passes_validation(self, loader, sample_package):
        """Test that a valid venv passes validation."""
        package = loader.load_package(sample_package, agent_app_id="test-123")
        venv_path = Path(package.venv_path)

        # Should be valid
        assert loader._validate_venv(venv_path) is True

    def test_missing_python_fails_validation(self, loader, sample_package):
        """Test that venv without python fails validation."""
        package = loader.load_package(sample_package, agent_app_id="test-123")
        venv_path = Path(package.venv_path)

        # Remove python
        (venv_path / "bin" / "python").unlink()

        # Should fail validation
        assert loader._validate_venv(venv_path) is False

    def test_missing_metadata_fails_validation(self, loader, sample_package):
        """Test that venv without metadata fails validation."""
        package = loader.load_package(sample_package, agent_app_id="test-123")
        venv_path = Path(package.venv_path)

        # Remove metadata
        (venv_path / ".pixell_venv_metadata.json").unlink()

        # Should fail validation
        assert loader._validate_venv(venv_path) is False


class TestErrorHandling:
    """Test error handling in venv creation."""

    def test_invalid_package_fails(self, loader, temp_dirs):
        """Test that invalid package fails to load."""
        packages_dir, _ = temp_dirs

        # Create invalid package (no agent.yaml)
        pkg_dir = Path(tempfile.mkdtemp())
        (pkg_dir / "dummy.txt").touch()

        apkg_path = packages_dir / "invalid.apkg"
        with zipfile.ZipFile(apkg_path, "w") as zf:
            zf.write(pkg_dir / "dummy.txt", "dummy.txt")

        shutil.rmtree(pkg_dir)

        # Should raise error
        with pytest.raises(Exception):
            loader.load_package(apkg_path)


class TestCollisionPrevention:
    """Test that collision prevention works."""

    def test_same_package_name_different_agent_ids_no_collision(self, loader, sample_package):
        """Test that same package name with different agent IDs don't collide."""
        # Developer A's agent
        package_a = loader.load_package(sample_package, agent_app_id="dev-a-uuid")
        venv_a = Path(package_a.venv_path)

        # Developer B's agent (same package name, different UUID)
        package_b = loader.load_package(sample_package, agent_app_id="dev-b-uuid")
        venv_b = Path(package_b.venv_path)

        # Should have different venvs
        assert venv_a != venv_b
        assert venv_a.exists()
        assert venv_b.exists()

        # Both should be functional
        assert (venv_a / "bin" / "python").exists()
        assert (venv_b / "bin" / "python").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
