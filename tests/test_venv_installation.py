import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from pixell_runtime.agents.loader import PackageLoader


def test_install_uses_wheelhouse_when_configured(tmp_path: Path, monkeypatch):
    packages_dir = tmp_path / "packages"
    venvs_dir = tmp_path / "venvs"
    packages_dir.mkdir()
    venvs_dir.mkdir()

    # Create a minimal extracted package with requirements.txt
    pkg_dir = packages_dir / "app@1.0.0"
    pkg_dir.mkdir()
    (pkg_dir / "agent.yaml").write_text("name: app\nversion: 1.0.0\nentrypoint: main:handler\n")
    (pkg_dir / "requirements.txt").write_text("depA==1.0.0\n")

    # Configure wheelhouse env
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    monkeypatch.setenv("WHEELHOUSE_DIR", str(wheelhouse))

    loader = PackageLoader(packages_dir, venvs_dir)

    with patch("subprocess.run") as run:
        # Fake venv.create by patching venv creation subprocess later if needed; here focus pip args
        run.return_value = MagicMock(returncode=0, stdout="Successfully installed depA-1.0.0", stderr="")
        # Trigger venv creation and requirements install
        _ = loader._ensure_venv("app@1.0.0", pkg_dir, agent_app_id="aid")

        # Find the pip install call that includes requirements
        calls = [c for c in run.call_args_list if "install" in c[0][0]]
        assert calls, "expected pip install calls"
        pip_args = calls[-1][0][0]
        # Wheelhouse should be used with --find-links
        assert "--find-links" in pip_args and str(wheelhouse) in pip_args
        # By default, wheelhouse uses online mode (no --no-index) for PyPI fallback
        # This is intentional to allow packages not in wheelhouse to be installed
