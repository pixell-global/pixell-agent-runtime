import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pixell_runtime.agents.loader import PackageLoader
from pixell_runtime.core.exceptions import PackageLoadError


def test_bad_requirements_fail(tmp_path: Path):
    packages_dir = tmp_path / "packages"
    venvs_dir = tmp_path / "venvs"
    packages_dir.mkdir()
    venvs_dir.mkdir()

    pkg_dir = packages_dir / "app@1.0.0"
    pkg_dir.mkdir()
    (pkg_dir / "agent.yaml").write_text("name: app\nversion: 1.0.0\nentrypoint: main:handler\n")
    (pkg_dir / "requirements.txt").write_text("nonexistent-package-zz==0.0.1\n")

    loader = PackageLoader(packages_dir, venvs_dir)

    with patch("subprocess.run") as run:
        # Fail pip install
        fail = MagicMock(returncode=1, stdout="", stderr="ERROR: No matching distribution")
        run.return_value = fail
        with pytest.raises(PackageLoadError):
            loader._ensure_venv("app@1.0.0", pkg_dir, agent_app_id="aid")


def test_install_timeout_raises(tmp_path: Path):
    packages_dir = tmp_path / "packages"
    venvs_dir = tmp_path / "venvs"
    packages_dir.mkdir()
    venvs_dir.mkdir()

    pkg_dir = packages_dir / "app@1.0.0"
    pkg_dir.mkdir()
    (pkg_dir / "agent.yaml").write_text("name: app\nversion: 1.0.0\nentrypoint: main:handler\n")
    (pkg_dir / "requirements.txt").write_text("dep==1.0.0\n")

    loader = PackageLoader(packages_dir, venvs_dir)

    def side_effect(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=[], timeout=300)

    import subprocess
    with patch("subprocess.run", side_effect=side_effect):
        with pytest.raises(PackageLoadError):
            loader._ensure_venv("app@1.0.0", pkg_dir, agent_app_id="aid")


