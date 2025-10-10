"""Fetch utilities for downloading APKGs to local cache."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Optional, Tuple

import httpx
import structlog

from pixell_runtime.deploy.models import PackageLocation


logger = structlog.get_logger()


def _parse_s3_url(url: str) -> Tuple[str, str]:
    """Parse s3://bucket/key URL into (bucket, key)."""
    if not url.startswith("s3://"):
        raise ValueError("Not an s3 URL")
    rest = url[len("s3://"):]
    parts = rest.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("Invalid s3 URL; expected s3://bucket/key")
    return parts[0], parts[1]


def _write_stream_to_file(stream_iter, dest_path: Path, max_size_bytes: int) -> int:
    """Write streaming bytes to file with max-size enforcement.

    stream_iter can be an iterator of bytes chunks.
    Returns number of bytes written.
    """
    tmp_file = dest_path.with_suffix(".downloading")
    bytes_written = 0
    with open(tmp_file, "wb") as f:
        for chunk in stream_iter:
            if not chunk:
                continue
            bytes_written += len(chunk)
            if bytes_written > max_size_bytes:
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass
                raise ValueError("Package exceeds maximum allowed size")
            f.write(chunk)
    os.replace(tmp_file, dest_path)
    return bytes_written


def fetch_package_to_path(
    location: PackageLocation,
    dest_path: Path,
    *,
    max_size_bytes: int = 100 * 1024 * 1024,  # 100MB
    total_timeout_sec: float = 60.0,
    max_retries: int = 3,
    backoff_base: float = 0.3,
    sha256: Optional[str] = None,
) -> Path:
    """Fetch package bytes to destination path.

    Supports:
    - https:// URL via httpx streaming
    - s3://bucket/key via boto3 GetObject
    - Signed S3 HTTPS URL (when provided in PackageLocation.s3.signedUrl)

    Enforces a maximum size and a total timeout across retries.
    Optionally validates SHA256 if provided.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Decide source
    url: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_key: Optional[str] = None

    if location.packageUrl:
        url = str(location.packageUrl)
        if url.startswith("s3://"):
            s3_bucket, s3_key = _parse_s3_url(url)
            url = None
    elif location.s3:
        if location.s3.signedUrl:
            url = str(location.s3.signedUrl)
        else:
            s3_bucket = location.s3.bucket
            s3_key = location.s3.key
    else:
        raise ValueError("packageUrl or s3 ref required for fetch")

    start = time.time()
    attempt = 0
    last_error: Optional[Exception] = None

    while attempt < max_retries and (time.time() - start) < total_timeout_sec:
        attempt += 1
        try:
            if url is not None:
                # HTTPS path
                logger.info("Downloading package", url=url, dest=str(dest_path), attempt=attempt)
                timeout = httpx.Timeout(total_timeout_sec - (time.time() - start))
                with httpx.Client(timeout=timeout) as client:
                    resp = client.get(url, follow_redirects=True)
                    resp.raise_for_status()
                    # Stream content respecting size
                    def _iter_chunks():
                        yield resp.content
                    bytes_written = _write_stream_to_file(_iter_chunks(), dest_path, max_size_bytes)
                    logger.info("Downloaded package", bytes=bytes_written)
            else:
                # S3 path
                assert s3_bucket and s3_key
                logger.info("Downloading package from S3", bucket=s3_bucket, key=s3_key, dest=str(dest_path), attempt=attempt)
                import boto3
                s3 = boto3.client("s3")
                obj = s3.get_object(Bucket=s3_bucket, Key=s3_key)
                body = obj["Body"]

                def _s3_iter():
                    # Try to iterate by chunks if available
                    chunk_size = 64 * 1024
                    try:
                        # Some StreamingBody supports iter_chunks
                        for chunk in body.iter_chunks(chunk_size):
                            yield chunk
                    except Exception:
                        # Fallback to reading whole body
                        data = body.read()
                        yield data

                bytes_written = _write_stream_to_file(_s3_iter(), dest_path, max_size_bytes)
                logger.info("Downloaded package from S3", bytes=bytes_written)

            # Optional SHA256 validation
            if sha256:
                hasher = hashlib.sha256()
                with open(dest_path, "rb") as f:
                    for block in iter(lambda: f.read(8192), b""):
                        hasher.update(block)
                actual = hasher.hexdigest()
                if actual != sha256:
                    raise ValueError(f"SHA256 mismatch. expected={sha256} actual={actual}")

            return dest_path
        except Exception as e:
            last_error = e
            # bounded backoff
            elapsed = time.time() - start
            remaining = total_timeout_sec - elapsed
            logger.warning("Fetch attempt failed", attempt=attempt, error=str(e), remaining_time_sec=max(0.0, remaining))
            if attempt >= max_retries or remaining <= 0:
                break
            sleep_for = min(backoff_base * (2 ** (attempt - 1)), max(0.0, remaining))
            time.sleep(sleep_for)

    # Failed after retries/timeout
    raise RuntimeError(f"Failed to fetch package after {attempt} attempts: {last_error}")


