"""Configuration management for Pixell Runtime."""

import os
from typing import List, Optional

from pydantic import Field, HttpUrl, validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Server configuration
    host: str = Field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"), description="Server host")
    port: int = Field(default_factory=lambda: int(os.getenv("PORT", "8000")), description="Server port")
    workers: int = Field(1, description="Number of worker processes")
    reload: bool = Field(False, description="Enable auto-reload in development")
    
    # Package discovery
    packages_urls: Optional[str] = Field(
        None,
        description="Comma-separated list of package URLs",
        env="PACKAGES_URLS"
    )
    registry_url: Optional[HttpUrl] = Field(
        None,
        description="Registry index URL",
        env="REGISTRY_URL"
    )
    registry_poll_interval: int = Field(
        300,
        description="Registry polling interval in seconds",
        env="REGISTRY_POLL_INTERVAL"
    )
    
    # Storage
    package_cache_dir: str = Field(
        "/tmp/pixell-runtime/packages",
        description="Local package cache directory",
        env="PACKAGE_CACHE_DIR"
    )
    max_cache_size_mb: int = Field(
        1024,
        description="Maximum cache size in MB",
        env="MAX_CACHE_SIZE_MB"
    )
    
    # Security
    oidc_issuer: Optional[HttpUrl] = Field(
        None,
        description="OIDC issuer URL",
        env="OIDC_ISSUER"
    )
    oidc_audience: Optional[str] = Field(
        None,
        description="OIDC audience",
        env="OIDC_AUDIENCE"
    )
    verify_signatures: bool = Field(
        True,
        description="Verify package signatures",
        env="VERIFY_SIGNATURES"
    )
    trusted_keys: Optional[str] = Field(
        None,
        description="Comma-separated list of trusted GPG key IDs",
        env="TRUSTED_KEYS"
    )
    
    # AWS Configuration
    aws_region: str = Field("us-east-1", env="AWS_REGION")
    aws_access_key_id: Optional[str] = Field(None, env="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: Optional[str] = Field(None, env="AWS_SECRET_ACCESS_KEY")
    s3_endpoint_url: Optional[HttpUrl] = Field(None, env="S3_ENDPOINT_URL")
    
    # Observability
    log_level: str = Field("INFO", env="LOG_LEVEL")
    log_format: str = Field("json", env="LOG_FORMAT")
    metrics_enabled: bool = Field(True, env="METRICS_ENABLED")
    metrics_port: int = Field(9090, env="METRICS_PORT")
    
    # Usage metering
    stripe_api_key: Optional[str] = Field(None, env="STRIPE_API_KEY")
    usage_reporting_enabled: bool = Field(False, env="USAGE_REPORTING_ENABLED")
    usage_reporting_interval: int = Field(3600, env="USAGE_REPORTING_INTERVAL")
    
    # Runtime limits
    max_packages: int = Field(100, env="MAX_PACKAGES")
    max_package_size_mb: int = Field(100, env="MAX_PACKAGE_SIZE_MB")
    request_timeout_seconds: int = Field(30, env="REQUEST_TIMEOUT_SECONDS")
    max_concurrent_invocations: int = Field(500, env="MAX_CONCURRENT_INVOCATIONS")

    @validator("packages_urls", pre=True)
    def parse_packages_urls(cls, v: Optional[str]) -> Optional[List[str]]:
        """Parse comma-separated package URLs."""
        if v:
            return [url.strip() for url in v.split(",") if url.strip()]
        return None
    
    @validator("trusted_keys", pre=True)
    def parse_trusted_keys(cls, v: Optional[str]) -> Optional[List[str]]:
        """Parse comma-separated trusted keys."""
        if v:
            return [key.strip() for key in v.split(",") if key.strip()]
        return None
    
    @property
    def package_urls_list(self) -> List[str]:
        """Get package URLs as a list."""
        if isinstance(self.packages_urls, str):
            return self.parse_packages_urls(self.packages_urls) or []
        return self.packages_urls or []
    
    @property
    def trusted_keys_list(self) -> List[str]:
        """Get trusted keys as a list."""
        if isinstance(self.trusted_keys, str):
            return self.parse_trusted_keys(self.trusted_keys) or []
        return self.trusted_keys or []