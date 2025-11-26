"""Package downloading and caching for supervisor."""

import hashlib
import shutil
import structlog
import zipfile
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
        extracted_dir: Path = Path("/var/lib/pixell/extracted"),
        max_package_size_mb: int = 100,
    ):
        """Initialize package downloader.

        Args:
            cache_dir: Directory for package cache (default: /var/lib/pixell/packages)
            extracted_dir: Directory for extracted packages (default: /var/lib/pixell/extracted)
            max_package_size_mb: Maximum package size in MB (default: 100)
        """
        self.cache_dir = cache_dir
        self.extracted_dir = extracted_dir
        self.max_package_size_bytes = max_package_size_mb * 1024 * 1024
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.extracted_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "PackageDownloader initialized",
            cache_dir=str(cache_dir),
            extracted_dir=str(extracted_dir),
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

    def extract_package(
        self,
        apkg_path: Path,
        package_sha256: Optional[str] = None,
        force_extract: bool = False,
    ) -> Path:
        """Extract .apkg (ZIP) to directory.

        Uses SHA256 for cache key if provided to enable cache invalidation.
        Extracts to /var/lib/pixell/extracted/{cache_key}/ for persistence.

        Args:
            apkg_path: Path to .apkg file (ZIP archive)
            package_sha256: Optional SHA256 checksum for cache key
            force_extract: If True, extract even if already extracted

        Returns:
            Path to extracted directory

        Raises:
            RuntimeError: If extraction fails
            ValueError: If .apkg is not a valid ZIP file or contains unsafe paths (zip-slip)
        """
        # Validate apkg_path exists and is a file
        if not apkg_path.exists():
            raise ValueError(f"Package file not found: {apkg_path}")
        if not apkg_path.is_file():
            raise ValueError(f"Package path is not a file: {apkg_path}")

        # Generate cache key for extracted directory
        # Use SHA256 if provided (recommended), otherwise hash the file
        if package_sha256:
            cache_key = package_sha256[:16]  # First 16 chars of SHA256
        else:
            # Hash file contents to generate stable cache key
            sha256_hash = hashlib.sha256()
            with open(apkg_path, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            cache_key = sha256_hash.hexdigest()[:16]

        extract_dir = self.extracted_dir / cache_key

        # Handle force_extract - clean up existing directory
        if force_extract and extract_dir.exists():
            logger.info(
                "Force extract requested, removing existing extraction",
                cache_key=cache_key,
                extract_dir=str(extract_dir),
            )
            shutil.rmtree(extract_dir, ignore_errors=True)

        # Skip extraction if already extracted (unless force_extract=True)
        if not force_extract and extract_dir.exists():
            # Verify extraction is complete by checking for agent.yaml
            agent_yaml = extract_dir / "agent.yaml"
            if agent_yaml.exists():
                logger.info(
                    "Package already extracted, reusing",
                    cache_key=cache_key,
                    extract_dir=str(extract_dir),
                )
                return extract_dir
            else:
                # Incomplete extraction - clean up and re-extract
                logger.warning(
                    "Extracted directory exists but incomplete, re-extracting",
                    cache_key=cache_key,
                    extract_dir=str(extract_dir),
                )
                shutil.rmtree(extract_dir, ignore_errors=True)

        logger.info(
            "Extracting package",
            apkg_path=str(apkg_path),
            cache_key=cache_key,
            extract_dir=str(extract_dir),
        )

        try:
            # Create extraction directory
            extract_dir.mkdir(parents=True, exist_ok=True)

            # Extract with zip-slip protection
            with zipfile.ZipFile(apkg_path, 'r') as zf:
                # Validate all paths before extracting (prevent zip-slip attacks)
                extract_base = extract_dir.resolve()
                for member in zf.namelist():
                    member_path = Path(member)

                    # Reject absolute paths
                    if member_path.is_absolute():
                        raise ValueError(
                            f"Zip-slip attack detected: member has absolute path: {member}"
                        )

                    # Reject parent directory traversals
                    if ".." in member_path.parts:
                        raise ValueError(
                            f"Zip-slip attack detected: member contains '..': {member}"
                        )

                    # Verify resolved path is within extraction directory
                    target = (extract_dir / member_path).resolve()
                    if not str(target).startswith(str(extract_base)):
                        raise ValueError(
                            f"Zip-slip attack detected: member escapes extraction directory: {member}"
                        )

                # All paths validated - safe to extract
                zf.extractall(extract_dir)

            logger.info(
                "Package extracted successfully",
                cache_key=cache_key,
                extract_dir=str(extract_dir),
            )

            return extract_dir

        except zipfile.BadZipFile as e:
            # Clean up partial extraction
            if extract_dir.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)

            logger.error(
                "Package is not a valid ZIP file",
                apkg_path=str(apkg_path),
                error=str(e),
            )
            raise ValueError(f"Invalid .apkg file (not a valid ZIP): {e}") from e

        except ValueError:
            # Zip-slip or validation error - already logged, re-raise as-is
            if extract_dir.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)
            raise

        except Exception as e:
            # Clean up partial extraction
            if extract_dir.exists():
                shutil.rmtree(extract_dir, ignore_errors=True)

            logger.error(
                "Package extraction failed",
                apkg_path=str(apkg_path),
                error=str(e),
                exc_info=True,
            )
            raise RuntimeError(f"Failed to extract package: {e}") from e

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
