import zipfile
from pathlib import Path

from pixell_runtime.agents.loader import PackageLoader


def write_zip(path: Path, entries: dict[str, bytes]):
    with zipfile.ZipFile(path, 'w') as zf:
        for k, v in entries.items():
            zf.writestr(k, v)


def test_requirements_only_success(tmp_path: Path):
    z = tmp_path / "req.apkg"
    entries = {
        "agent.yaml": b"name: app\nversion: 1.0.0\nentrypoint: main:handler\n",
        "requirements.txt": b"",
        "src/__init__.py": b"",
    }
    write_zip(z, entries)
    loader = PackageLoader(tmp_path / "packages", tmp_path / "venvs")
    pkg = loader.load_package(z, agent_app_id="aid")
    assert pkg.id.startswith("app@1.0.0")
    assert Path(pkg.venv_path).exists()


def test_setup_only_success(tmp_path: Path):
    z = tmp_path / "setup.apkg"
    entries = {
        "agent.yaml": b"name: app2\nversion: 1.0.0\nentrypoint: main:handler\n",
        "setup.py": b"from setuptools import setup; setup(name='app2')\n",
        "requirements.txt": b"",
    }
    write_zip(z, entries)
    loader = PackageLoader(tmp_path / "packages", tmp_path / "venvs")
    pkg = loader.load_package(z, agent_app_id="aid")
    assert pkg.id.startswith("app2@1.0.0")
    assert Path(pkg.venv_path).exists()
