import io
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pixell_runtime.deploy.models import PackageLocation, PackageS3Ref
from pixell_runtime.deploy.fetch import fetch_package_to_path
import time


def test_https_download(tmp_path: Path):
    dest = tmp_path / "pkg.apkg"
    data = b"hello world"

    class Resp:
        def __init__(self, content: bytes):
            self.content = content
        def raise_for_status(self):
            return None

    with patch("httpx.Client") as Client:
        client = MagicMock()
        client.get.return_value = Resp(data)
        Client.return_value.__enter__.return_value = client
        loc = PackageLocation(packageUrl="https://example.com/p.pkg")
        out = fetch_package_to_path(loc, dest)
        assert out.exists()
        assert out.read_bytes() == data


def test_https_download_sha256_mismatch(tmp_path: Path):
    dest = tmp_path / "pkg.apkg"
    data = b"abc"

    class Resp:
        def __init__(self, content: bytes):
            self.content = content
        def raise_for_status(self):
            return None

    with patch("httpx.Client") as Client:
        client = MagicMock()
        client.get.return_value = Resp(data)
        Client.return_value.__enter__.return_value = client
        loc = PackageLocation(packageUrl="https://example.com/p.pkg")
        with pytest.raises(Exception):
            fetch_package_to_path(loc, dest, sha256="deadbeef")


def test_s3_download_via_signed_url(tmp_path: Path):
    dest = tmp_path / "pkg.apkg"
    data = b"xyz"

    class Resp:
        def __init__(self, content: bytes):
            self.content = content
        def raise_for_status(self):
            return None

    with patch("httpx.Client") as Client:
        client = MagicMock()
        client.get.return_value = Resp(data)
        Client.return_value.__enter__.return_value = client
        loc = PackageLocation(s3=PackageS3Ref(bucket="b", key="k", signedUrl="https://signed"))
        out = fetch_package_to_path(loc, dest)
        assert out.exists()
        assert out.read_bytes() == data


def test_s3_download_via_boto(tmp_path: Path):
    dest = tmp_path / "pkg.apkg"
    data = b"payload"

    class StreamingBody:
        def __init__(self, buf: bytes):
            self._buf = io.BytesIO(buf)
        def iter_chunks(self, size):
            while True:
                chunk = self._buf.read(size)
                if not chunk:
                    break
                yield chunk
        def read(self):
            return self._buf.getvalue()

    with patch("boto3.client") as bclient:
        s3 = MagicMock()
        s3.get_object.return_value = {"Body": StreamingBody(data)}
        bclient.return_value = s3
        loc = PackageLocation(s3=PackageS3Ref(bucket="myb", key="myk"))
        out = fetch_package_to_path(loc, dest)
        assert out.exists()
        assert out.read_bytes() == data


def test_download_size_limit(tmp_path: Path):
    dest = tmp_path / "pkg.apkg"
    data = b"a" * 1024

    class Resp:
        def __init__(self, content: bytes):
            self.content = content
        def raise_for_status(self):
            return None

    with patch("httpx.Client") as Client:
        client = MagicMock()
        client.get.return_value = Resp(data)
        Client.return_value.__enter__.return_value = client
        loc = PackageLocation(packageUrl="https://example.com/large")
        with pytest.raises(Exception):
            fetch_package_to_path(loc, dest, max_size_bytes=10)


def test_https_retry_then_success(tmp_path: Path):
    dest = tmp_path / "pkg.apkg"
    data = b"ok"

    class Resp:
        def __init__(self, content: bytes, status: int = 200):
            self.content = content
            self._status = status
        def raise_for_status(self):
            if self._status >= 400:
                raise Exception("bad status")

    seq = [Resp(b"", 500), Resp(data, 200)]

    def get_side_effect(*args, **kwargs):
        return seq.pop(0)

    with patch("httpx.Client") as Client:
        client = MagicMock()
        client.get.side_effect = get_side_effect
        Client.return_value.__enter__.return_value = client
        loc = PackageLocation(packageUrl="https://example.com/p.pkg")
        out = fetch_package_to_path(loc, dest, total_timeout_sec=5.0, max_retries=2, backoff_base=0.01)
        assert out.exists() and out.read_bytes() == data


def test_https_403_fails_fast(tmp_path: Path):
    dest = tmp_path / "pkg.apkg"

    class Resp:
        def __init__(self, status: int):
            self._status = status
            self.content = b""
        def raise_for_status(self):
            raise Exception("403")

    with patch("httpx.Client") as Client:
        client = MagicMock()
        client.get.return_value = Resp(403)
        Client.return_value.__enter__.return_value = client
        loc = PackageLocation(packageUrl="https://example.com/p.pkg")
        with pytest.raises(Exception):
            fetch_package_to_path(loc, dest, total_timeout_sec=1.0, max_retries=1)


def test_https_total_timeout(tmp_path: Path):
    dest = tmp_path / "pkg.apkg"

    class Resp:
        def __init__(self):
            self.content = b""
        def raise_for_status(self):
            raise Exception("timeout")

    with patch("httpx.Client") as Client:
        client = MagicMock()
        client.get.return_value = Resp()
        Client.return_value.__enter__.return_value = client
        loc = PackageLocation(packageUrl="https://example.com/p.pkg")
        with pytest.raises(RuntimeError):
            fetch_package_to_path(loc, dest, total_timeout_sec=0.01, max_retries=1, backoff_base=0.005)
