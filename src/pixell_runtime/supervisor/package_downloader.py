"""Package downloading and caching for supervisor."""

import hashlib
import structlog
from pathlib import Path
from typing import Optional

from pixell_runtime.deploy.fetch import fetch_package_to_path
from pixell_runtime.deploy.models import PackageLocation

logger = structlog.get_logger()


class PackageDownloader:
    """Downloads and caches agent packages from S3/HTTPS.

    Features:
    - Downloads APKGs from S3 or HTTPS URLs
    - Caches packages by SHA256 hash
    - Validates package checksums
    - Enforces size limits
    - Supports retries with backoff
    """

    def __init__(
        self,
        cache_dir: Path = Path("/var/lib/pixell/packages"),
        max_package_size_mb: int = 100,
    ):
        """Initialize package downloader.

        Args:
            cache_dir: Directory for package cache (default: /var/lib/pixell/packages)
            max_package_size_mb: Maximum package size in MB (default: 100)
        """
        self.cache_dir = cache_dir
        self.max_package_size_bytes = max_package_size_mb * 1024 * 1024
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "PackageDownloader initialized",
            cache_dir=str(cache_dir),
            max_size_mb=max_package_size_mb,
        )

    def _get_cache_key(self, package_url: str, package_sha256: Optional[str]) -> str:
        """Generate cache key for a package.

        Uses SHA256 if provided, otherwise hashes the URL.

        Args:
            package_url: URL to the package
            package_sha256: Optional SHA256 checksum

        Returns:
            Cache key (filename without extension)
        """
        if package_sha256:
            return package_sha256[:16]  # Use first 16 chars of SHA256
        else:
            # Hash the URL to create a stable cache key
            url_hash = hashlib.sha256(package_url.encode()).hexdigest()
            return url_hash[:16]

    def _get_cache_path(self, cache_key: str) -> Path:
        """Get cache file path for a cache key.

        Args:
            cache_key: Cache key from _get_cache_key

        Returns:
            Full path to cached package file
        """
        return self.cache_dir / f"{cache_key}.apkg"

    def is_cached(self, package_url: str, package_sha256: Optional[str] = None) -> bool:
        """Check if package is already cached.

        Args:
            package_url: URL to the package
            package_sha256: Optional SHA256 checksum

        Returns:
            True if package exists in cache, False otherwise
        """
        cache_key = self._get_cache_key(package_url, package_sha256)
        cache_path = self._get_cache_path(cache_key)
        exists = cache_path.exists()

        if exists:
            logger.debug(
                "Package found in cache",
                cache_key=cache_key,
                path=str(cache_path),
            )
        else:
            logger.debug(
                "Package not in cache",
                cache_key=cache_key,
                path=str(cache_path),
            )

        return exists

    def download(
        self,
        package_url: str,
        package_sha256: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Path:
        """Download and cache a package.

        Args:
            package_url: URL to the package (s3:// or https://)
            package_sha256: Optional SHA256 checksum for validation
            force_refresh: If True, download even if cached

        Returns:
            Path to cached package file

        Raises:
            RuntimeError: If download fails
            ValueError: If package exceeds size limit or checksum fails
        """
        cache_key = self._get_cache_key(package_url, package_sha256)
        cache_path = self._get_cache_path(cache_key)

        # Check cache unless force refresh
        if not force_refresh and cache_path.exists():
            logger.info(
                "Using cached package",
                cache_key=cache_key,
                path=str(cache_path),
                url=package_url,
            )
            return cache_path

        # Download to cache
        logger.info(
            "Downloading package",
            url=package_url,
            cache_key=cache_key,
            dest=str(cache_path),
            sha256=package_sha256,
        )

        try:
            # Create PackageLocation for fetch utility
            # Handle S3 URLs differently from HTTPS URLs
            if package_url.startswith("s3://"):
                # Parse S3 URL: s3://bucket/key
                from pixell_runtime.deploy.models import PackageS3Ref
                parts = package_url[5:].split("/", 1)  # Remove s3://
                if len(parts) != 2:
                    raise ValueError(f"Invalid S3 URL: {package_url}")
                bucket, key = parts
                location = PackageLocation(s3=PackageS3Ref(bucket=bucket, key=key))
            else:
                # HTTPS URL
                location = PackageLocation(packageUrl=package_url)

            # Download with retry and validation
            fetch_package_to_path(
                location=location,
                dest_path=cache_path,
                max_size_bytes=self.max_package_size_bytes,
                total_timeout_sec=120.0,  # 2 minutes total
                max_retries=3,
                sha256=package_sha256,
            )

            logger.info(
                "Package downloaded successfully",
                cache_key=cache_key,
                path=str(cache_path),
            )

            return cache_path

        except Exception as e:
            # Clean up partial download if exists
            if cache_path.exists():
                try:
                    cache_path.unlink()
                except Exception:
                    pass

            logger.error(
                "Package download failed",
                url=package_url,
                error=str(e),
                exc_info=True,
            )
            raise

    def get_cached_path(
        self, package_url: str, package_sha256: Optional[str] = None
    ) -> Optional[Path]:
        """Get path to cached package if it exists.

        Args:
            package_url: URL to the package
            package_sha256: Optional SHA256 checksum

        Returns:
            Path to cached package, or None if not cached
        """
        cache_key = self._get_cache_key(package_url, package_sha256)
        cache_path = self._get_cache_path(cache_key)

        if cache_path.exists():
            return cache_path
        return None

    def delete_cached(
        self, package_url: str, package_sha256: Optional[str] = None
    ) -> bool:
        """Delete a cached package.

        Args:
            package_url: URL to the package
            package_sha256: Optional SHA256 checksum

        Returns:
            True if package was deleted, False if not found
        """
        cache_key = self._get_cache_key(package_url, package_sha256)
        cache_path = self._get_cache_path(cache_key)

        if cache_path.exists():
            try:
                cache_path.unlink()
                logger.info("Deleted cached package", cache_key=cache_key)
                return True
            except Exception as e:
                logger.error(
                    "Failed to delete cached package",
                    cache_key=cache_key,
                    error=str(e),
                )
                raise
        else:
            logger.debug("Package not cached, nothing to delete", cache_key=cache_key)
            return False

    def clear_cache(self) -> int:
        """Clear all cached packages.

        Returns:
            Number of packages deleted
        """
        count = 0
        try:
            for file_path in self.cache_dir.glob("*.apkg"):
                try:
                    file_path.unlink()
                    count += 1
                except Exception as e:
                    logger.error(
                        "Failed to delete cache file",
                        path=str(file_path),
                        error=str(e),
                    )

            logger.info("Cleared package cache", deleted_count=count)
            return count

        except Exception as e:
            logger.error("Failed to clear cache", error=str(e))
            raise
