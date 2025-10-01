"""Fetch utilities for downloading APKGs to local cache."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

import httpx
import structlog

from pixell_runtime.deploy.models import PackageLocation


logger = structlog.get_logger()


def fetch_package_to_path(location: PackageLocation, dest_path: Path) -> Path:
    """Fetch package bytes to destination path.

    Prefers signed URL download. S3 IAM-based fetch is intentionally not implemented
    here to keep PAR cloud-agnostic. If a signed URL is present inside the s3 ref,
    we use that; otherwise, raise to the caller.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if location.packageUrl:
        url = str(location.packageUrl)
    elif location.s3 and location.s3.signedUrl:
        url = str(location.s3.signedUrl)
    else:
        raise ValueError("packageUrl or s3.signedUrl is required for fetch")

    logger.info("Downloading package", url=url, dest=str(dest_path))
    with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
        resp = client.get(url)
        resp.raise_for_status()
        tmp_file = dest_path.with_suffix(".downloading")
        with open(tmp_file, "wb") as f:
            f.write(resp.content)
        os.replace(tmp_file, dest_path)

    logger.info("Downloaded package", bytes=len(resp.content))
    return dest_path


