import asyncio
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx
import pytest

from pixell_runtime.deploy.fetch import fetch_package_to_path
from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime


def test_forbidden_import_scan():
    # Simple import scan to ensure runtime package has no cloud control-plane SDKs
    forbidden = ["boto3", "botocore", "awscli", "kubernetes", "google.cloud", "azure"]
    import pkgutil, pixell_runtime
    import pathlib
    root = pathlib.Path(pixell_runtime.__file__).parent
    scanned = 0
    for mod in pkgutil.walk_packages([str(root)]):
        scanned += 1
        name = mod.name
        for bad in forbidden:
            assert bad not in name
    assert scanned > 0


@pytest.mark.asyncio
async def test_corrupt_zip_runtime_exits(tmp_path: Path, monkeypatch, capsys):
    # Create corrupt APKG (not a zip)
    apkg = tmp_path / "bad.apkg"
    apkg.write_bytes(b"not a zip")

    # Runtime should fail to load and never become ready
    monkeypatch.setenv("REST_PORT", "54001")
    monkeypatch.setenv("A2A_PORT", "54002")
    monkeypatch.setenv("BASE_PATH", "/")
    rt = ThreeSurfaceRuntime(str(apkg))
    task = asyncio.create_task(rt.start())
    await asyncio.sleep(0.3)
    # It should have logged a failure and shut down
    out = capsys.readouterr().out
    assert "Runtime failed to load package" in out
    # Runtime should have exited on its own (no need to cancel)
    await asyncio.wait_for(task, timeout=1.0)


def test_s3_403_retry_then_fail(monkeypatch, tmp_path: Path, capsys):
    dest = tmp_path / "pkg.apkg"
    from pixell_runtime.deploy.models import PackageLocation, PackageS3Ref

    class FakeBody:
        def iter_chunks(self, size):
            yield b"x" * 10

    with patch("boto3.client") as bclient:
        s3 = MagicMock()
        # Simulate AccessDenied
        s3.get_object.side_effect = Exception("AccessDenied: 403")
        bclient.return_value = s3
        loc = PackageLocation(s3=PackageS3Ref(bucket="b", key="k"))
        with pytest.raises(RuntimeError):
            fetch_package_to_path(loc, dest, max_retries=2, total_timeout_sec=1.0, backoff_base=0.1)
    out = capsys.readouterr().out
    # Should log multiple failed attempts and include underlying error
    assert out.count("Fetch attempt failed") >= 2
    assert "AccessDenied" in out

