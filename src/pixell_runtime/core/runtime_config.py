"""
Runtime configuration validation and parsing.

This module provides comprehensive validation of all runtime configuration
from environment variables, ensuring fail-fast behavior on invalid config.
"""

import os
import re
import sys
from typing import Optional

import structlog

logger = structlog.get_logger()


class RuntimeConfig:
    """
    Runtime configuration with comprehensive validation.
    
    Validates all required and optional environment variables on initialization,
    failing fast with clear error messages if configuration is invalid.
    """
    
    def __init__(self):
        """Initialize and validate runtime configuration."""
        self.errors = []
        
        # Required fields
        self.agent_app_id: Optional[str] = None
        
        # Optional fields
        self.deployment_id: Optional[str] = None
        self.package_url: Optional[str] = None
        self.package_sha256: Optional[str] = None
        
        # Ports
        self.rest_port: int = 8080
        self.a2a_port: int = 50051
        self.ui_port: int = 3000
        
        # AWS configuration
        self.aws_region: Optional[str] = None
        self.s3_bucket: Optional[str] = None
        
        # Path configuration
        self.base_path: str = "/"
        
        # Runtime options
        self.multiplexed: bool = True
        self.max_package_size_mb: int = 100
        
        # Boot budget enforcement
        self.boot_budget_ms: float = 5000.0
        self.boot_hard_limit_multiplier: float = 0.0  # 0 disables hard limit
        
        # Validate all configuration
        self._validate()
        
        # If there are errors, log them and exit
        if self.errors:
            for error in self.errors:
                logger.error("Configuration validation error", error=error)
            logger.error(
                "Runtime configuration validation failed",
                error_count=len(self.errors)
            )
            sys.exit(1)
    
    def _validate(self):
        """Validate all configuration."""
        self._validate_agent_app_id()
        self._validate_deployment_id()
        self._validate_runtime_options()  # Must come before ports for multiplexed check
        self._validate_ports()
        self._validate_aws_config()
        self._validate_package_config()
        self._validate_base_path()
        self._validate_boot_budget()
    
    def _validate_agent_app_id(self):
        """Validate AGENT_APP_ID (required)."""
        agent_app_id = os.getenv("AGENT_APP_ID")
        
        if not agent_app_id:
            self.errors.append("AGENT_APP_ID environment variable is required")
            return
        
        if not agent_app_id.strip():
            self.errors.append("AGENT_APP_ID cannot be empty or whitespace-only")
            return
        
        self.agent_app_id = agent_app_id
    
    def _validate_deployment_id(self):
        """Validate DEPLOYMENT_ID (optional)."""
        self.deployment_id = os.getenv("DEPLOYMENT_ID")
        # DEPLOYMENT_ID is optional, no validation needed
    
    def _validate_ports(self):
        """Validate port configuration."""
        # REST_PORT
        rest_port_str = os.getenv("REST_PORT", "8080")
        try:
            rest_port = int(rest_port_str)
            if rest_port < 1 or rest_port > 65535:
                self.errors.append(
                    f"REST_PORT must be between 1 and 65535, got: {rest_port}"
                )
            elif rest_port == 0:
                self.errors.append("REST_PORT cannot be 0 (dynamic port allocation not allowed)")
            else:
                self.rest_port = rest_port
        except ValueError:
            self.errors.append(
                f"REST_PORT must be a valid integer, got: {rest_port_str}"
            )
        
        # A2A_PORT
        a2a_port_str = os.getenv("A2A_PORT", "50051")
        try:
            a2a_port = int(a2a_port_str)
            if a2a_port < 1 or a2a_port > 65535:
                self.errors.append(
                    f"A2A_PORT must be between 1 and 65535, got: {a2a_port}"
                )
            elif a2a_port == 0:
                self.errors.append("A2A_PORT cannot be 0 (dynamic port allocation not allowed)")
            else:
                self.a2a_port = a2a_port
        except ValueError:
            self.errors.append(
                f"A2A_PORT must be a valid integer, got: {a2a_port_str}"
            )
        
        # UI_PORT
        ui_port_str = os.getenv("UI_PORT", "3000")
        try:
            ui_port = int(ui_port_str)
            if ui_port < 1 or ui_port > 65535:
                self.errors.append(
                    f"UI_PORT must be between 1 and 65535, got: {ui_port}"
                )
            elif ui_port == 0:
                self.errors.append("UI_PORT cannot be 0 (dynamic port allocation not allowed)")
            else:
                self.ui_port = ui_port
        except ValueError:
            self.errors.append(
                f"UI_PORT must be a valid integer, got: {ui_port_str}"
            )
        
        # Check for port conflicts
        if hasattr(self, 'rest_port') and hasattr(self, 'a2a_port'):
            if self.rest_port == self.a2a_port:
                self.errors.append(
                    f"REST_PORT and A2A_PORT cannot be the same: {self.rest_port}"
                )
        
        if hasattr(self, 'rest_port') and hasattr(self, 'ui_port'):
            if self.rest_port == self.ui_port and not self.multiplexed:
                self.errors.append(
                    f"REST_PORT and UI_PORT cannot be the same when not multiplexed: {self.rest_port}"
                )
        
        if hasattr(self, 'a2a_port') and hasattr(self, 'ui_port'):
            if self.a2a_port == self.ui_port:
                self.errors.append(
                    f"A2A_PORT and UI_PORT cannot be the same: {self.a2a_port}"
                )
    
    def _validate_aws_config(self):
        """Validate AWS configuration."""
        # AWS_REGION (optional but recommended)
        aws_region = os.getenv("AWS_REGION")
        if aws_region:
            # Basic validation - AWS regions follow pattern like us-east-1
            if not re.match(r'^[a-z]{2}-[a-z]+-\d+$', aws_region):
                logger.warning(
                    "AWS_REGION does not match expected format (e.g., us-east-1)",
                    aws_region=aws_region
                )
            self.aws_region = aws_region
        
        # S3_BUCKET (optional, validated when used)
        s3_bucket = os.getenv("S3_BUCKET")
        if s3_bucket:
            # Basic S3 bucket name validation
            if len(s3_bucket) < 3 or len(s3_bucket) > 63:
                self.errors.append(
                    f"S3_BUCKET name must be between 3 and 63 characters, got: {len(s3_bucket)}"
                )
            elif not re.match(r'^[a-z0-9][a-z0-9.-]*[a-z0-9]$', s3_bucket):
                self.errors.append(
                    f"S3_BUCKET name contains invalid characters: {s3_bucket}"
                )
            else:
                self.s3_bucket = s3_bucket
    
    def _validate_package_config(self):
        """Validate package-related configuration."""
        # PACKAGE_URL (optional)
        package_url = os.getenv("PACKAGE_URL")
        if package_url:
            # Basic URL validation
            if not package_url.strip():
                self.errors.append("PACKAGE_URL cannot be empty or whitespace-only")
            elif not (package_url.startswith("https://") or package_url.startswith("s3://")):
                self.errors.append(
                    f"PACKAGE_URL must start with https:// or s3://, got: {package_url[:20]}..."
                )
            else:
                self.package_url = package_url.strip()
        
        # PACKAGE_SHA256 (optional)
        package_sha256 = os.getenv("PACKAGE_SHA256")
        if package_sha256:
            # SHA256 should be 64 hex characters
            if not re.match(r'^[a-fA-F0-9]{64}$', package_sha256):
                self.errors.append(
                    f"PACKAGE_SHA256 must be 64 hexadecimal characters, got: {len(package_sha256)} chars"
                )
            else:
                self.package_sha256 = package_sha256
        
        # MAX_PACKAGE_SIZE_MB (optional)
        max_size_str = os.getenv("MAX_PACKAGE_SIZE_MB", "100")
        try:
            max_size = int(max_size_str)
            if max_size < 1:
                self.errors.append(
                    f"MAX_PACKAGE_SIZE_MB must be at least 1, got: {max_size}"
                )
            elif max_size > 10000:  # 10GB limit
                logger.warning(
                    "MAX_PACKAGE_SIZE_MB is very large",
                    max_size_mb=max_size
                )
                self.max_package_size_mb = max_size
            else:
                self.max_package_size_mb = max_size
        except ValueError:
            self.errors.append(
                f"MAX_PACKAGE_SIZE_MB must be a valid integer, got: {max_size_str}"
            )
    
    def _validate_base_path(self):
        """Validate and normalize BASE_PATH."""
        base_path = os.getenv("BASE_PATH", "/")
        
        # Normalize
        base_path = base_path.strip()
        
        # Ensure it starts with /
        if not base_path.startswith("/"):
            base_path = "/" + base_path
        
        # Remove trailing slash except for root
        if len(base_path) > 1 and base_path.endswith("/"):
            base_path = base_path[:-1]
        
        # Validate no double slashes
        if "//" in base_path:
            self.errors.append(
                f"BASE_PATH contains double slashes: {base_path}"
            )
            return
        
        # Validate characters (alphanumeric, -, _, /, .)
        if not re.match(r'^[a-zA-Z0-9/_.-]+$', base_path):
            self.errors.append(
                f"BASE_PATH contains invalid characters: {base_path}"
            )
            return
        
        self.base_path = base_path
    
    def _validate_runtime_options(self):
        """Validate runtime options."""
        # MULTIPLEXED
        multiplexed_str = os.getenv("MULTIPLEXED", "true").lower()
        if multiplexed_str in ("true", "1", "yes", "on"):
            self.multiplexed = True
        elif multiplexed_str in ("false", "0", "no", "off"):
            self.multiplexed = False
        else:
            logger.warning(
                "MULTIPLEXED has unexpected value, defaulting to true",
                value=multiplexed_str
            )
            self.multiplexed = True
    
    def to_dict(self):
        """Convert configuration to dictionary (for logging/debugging)."""
        return {
            "agent_app_id": self.agent_app_id,
            "deployment_id": self.deployment_id,
            "rest_port": self.rest_port,
            "a2a_port": self.a2a_port,
            "ui_port": self.ui_port,
            "aws_region": self.aws_region,
            "s3_bucket": self.s3_bucket,
            "base_path": self.base_path,
            "multiplexed": self.multiplexed,
            "max_package_size_mb": self.max_package_size_mb,
            "has_package_url": self.package_url is not None,
            "has_package_sha256": self.package_sha256 is not None,
            "boot_budget_ms": self.boot_budget_ms,
            "boot_hard_limit_multiplier": self.boot_hard_limit_multiplier,
        }

    def _validate_boot_budget(self):
        """Validate boot time budget configuration."""
        budget_str = os.getenv("BOOT_BUDGET_MS", "5000").strip()
        try:
            budget = float(budget_str)
            if budget <= 0:
                self.errors.append(
                    f"BOOT_BUDGET_MS must be > 0, got: {budget_str}"
                )
            else:
                self.boot_budget_ms = budget
        except ValueError:
            self.errors.append(
                f"BOOT_BUDGET_MS must be a number (ms), got: {budget_str}"
            )

        multiplier_str = os.getenv("BOOT_HARD_LIMIT_MULTIPLIER", "0").strip()
        try:
            multiplier = float(multiplier_str)
            if multiplier < 0:
                self.errors.append(
                    f"BOOT_HARD_LIMIT_MULTIPLIER must be >= 0, got: {multiplier_str}"
                )
            else:
                self.boot_hard_limit_multiplier = multiplier
        except ValueError:
            self.errors.append(
                f"BOOT_HARD_LIMIT_MULTIPLIER must be a number, got: {multiplier_str}"
            )
