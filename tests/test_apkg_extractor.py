import io
import zipfile
from pathlib import Path
import pytest

from pixell_runtime.agents.loader import PackageLoader
from pixell_runtime.core.exceptions import PackageValidationError, PackageLoadError


def make_zip(tmp_path: Path, files: dict[str, bytes]) -> Path:
    z = tmp_path / "pkg.apkg"
    with zipfile.ZipFile(z, 'w') as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return z


def test_missing_manifest_fails(tmp_path: Path):
    z = make_zip(tmp_path, {"foo.txt": b"bar"})
    loader = PackageLoader(tmp_path / "packages", tmp_path / "venvs")
    with pytest.raises(Exception):
        loader.load_package(z)


def test_zip_slip_detected(tmp_path: Path):
    # Craft zip with path traversal
    z = tmp_path / "bad.apkg"
    with zipfile.ZipFile(z, 'w') as zf:
        zf.writestr("../evil.txt", b"oops")
        zf.writestr("agent.yaml", b"name: a\nversion: 1.0.0\nentrypoint: main:handler\n")
    loader = PackageLoader(tmp_path / "packages", tmp_path / "venvs")
    with pytest.raises(PackageLoadError):
        loader.load_package(z)


def test_manifest_requires_entrypoint_and_version(tmp_path: Path):
    # Missing entrypoint
    z = make_zip(tmp_path, {"agent.yaml": b"name: a\nversion: 1.0.0\n"})
    loader = PackageLoader(tmp_path / "packages", tmp_path / "venvs")
    with pytest.raises(PackageLoadError):
        loader.load_package(z)

    # Missing version
    z2 = make_zip(tmp_path, {"agent.yaml": b"name: a\nentrypoint: main:handler\n"})
    with pytest.raises(PackageLoadError):
        loader.load_package(z2)


def test_rest_and_ui_required_fields(tmp_path: Path):
    # REST missing entry
    yaml1 = b"""\
name: a
version: 1.0.0
entrypoint: main:handler
rest:
  something: nope
"""
    z1 = make_zip(tmp_path, {"agent.yaml": yaml1})
    loader = PackageLoader(tmp_path / "packages", tmp_path / "venvs")
    with pytest.raises(PackageLoadError):
        loader.load_package(z1)

    # UI missing path
    yaml2 = b"""\
name: a
version: 1.0.0
entrypoint: main:handler
ui:
  basePath: /
"""
    z2 = make_zip(tmp_path, {"agent.yaml": yaml2})
    with pytest.raises(PackageLoadError):
        loader.load_package(z2)
