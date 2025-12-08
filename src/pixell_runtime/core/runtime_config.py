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
        
        # =============================================================================
        # SOCKET MODE IS MANDATORY - DO NOT SET TO FALSE
        # =============================================================================
        # Port mode (TCP ports) is DEPRECATED and REMOVED.
        # All agents MUST use Unix domain sockets.
        # Socket paths: /var/run/pixell-agents/agent_{short_id}/*.sock
        # DO NOT change this default to False under any circumstances.
        # =============================================================================
        self.socket_mode: bool = True  # ALWAYS TRUE - SOCKET MODE IS MANDATORY

        # Socket paths (used when socket_mode=True) - THIS IS THE PREFERRED MODE
        self.rest_socket: Optional[str] = None
        self.a2a_socket: Optional[str] = None
        self.ui_socket: Optional[str] = None

        # DEPRECATED: Ports (used when socket_mode=False) - DO NOT USE
        # Port mode is deprecated and should never be activated in production.
        # These defaults exist only for backwards compatibility during migration.
        # All agents should use Unix domain sockets (socket_mode=True) instead.
        self.rest_port: int = 63000  # DEPRECATED - REST API (range: 63000-63199)
        self.a2a_port: int = 60000   # DEPRECATED - A2A gRPC (range: 60000-60199)
        self.ui_port: int = 65000    # DEPRECATED - UI Server (range: 65000-65199)
        # Note: Gateway listens on 50051 for external access
        
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
        self._validate_socket_mode()  # Must come before ports/sockets
        self._validate_sockets()  # Validate socket paths if socket_mode=True
        self._validate_ports()  # Only validates if socket_mode=False
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
        """Validate port configuration (skipped if socket_mode=True)."""
        if self.socket_mode:
            # In socket mode, ports are not used
            logger.debug("Skipping port validation - socket mode enabled")
            return

        # REST_PORT (PAC allocates from range 63000-63199)
        rest_port_str = os.getenv("REST_PORT", "63000")
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

        # A2A_PORT (PAC allocates from range 60000-60199)
        # Note: Gateway listens on 50051, agents use 60000-60199
        a2a_port_str = os.getenv("A2A_PORT", "60000")
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
        
        # UI_PORT (PAC allocates from range 65000-65199)
        ui_port_str = os.getenv("UI_PORT", "65000")
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

    def _validate_socket_mode(self):
        """Validate SOCKET_MODE environment variable.

        IMPORTANT: socket_mode=True (Unix sockets) is the ONLY supported mode.
        DEPRECATED: socket_mode=False (TCP ports) is deprecated and should NEVER be activated.
        Port mode has hard capacity limits (200 agents max) and is being phased out.
        The default of False exists only for backwards compatibility during migration.
        All new deployments MUST use SOCKET_MODE=true.
        """
        socket_mode_str = os.getenv("SOCKET_MODE", "false").lower()
        if socket_mode_str in ("true", "1", "yes", "on"):
            self.socket_mode = True
        elif socket_mode_str in ("false", "0", "no", "off"):
            # DEPRECATED: Port mode (socket_mode=False) should never be used.
            # This fallback exists only for backwards compatibility.
            # TODO: Remove port mode support entirely once all agents are migrated to sockets.
            self.socket_mode = False
            logger.warning(
                "DEPRECATED: Port mode (SOCKET_MODE=false) is deprecated. "
                "All agents should use SOCKET_MODE=true for Unix sockets. "
                "Port mode will be removed in a future release."
            )
        else:
            logger.warning(
                "SOCKET_MODE has unexpected value, defaulting to false (DEPRECATED)",
                value=socket_mode_str
            )
            self.socket_mode = False

    def _validate_sockets(self):
        """Validate socket paths if socket_mode is enabled."""
        if not self.socket_mode:
            # Not in socket mode - skip socket validation
            return

        # In socket mode, all socket paths are required
        self.rest_socket = os.getenv("REST_SOCKET")
        self.a2a_socket = os.getenv("A2A_SOCKET")
        self.ui_socket = os.getenv("UI_SOCKET")

        # Validate REST_SOCKET
        if not self.rest_socket:
            self.errors.append(
                "REST_SOCKET is required when SOCKET_MODE=true"
            )
        elif not self.rest_socket.startswith("/"):
            self.errors.append(
                f"REST_SOCKET must be an absolute path, got: {self.rest_socket}"
            )

        # Validate A2A_SOCKET
        if not self.a2a_socket:
            self.errors.append(
                "A2A_SOCKET is required when SOCKET_MODE=true"
            )
        elif not self.a2a_socket.startswith("/"):
            self.errors.append(
                f"A2A_SOCKET must be an absolute path, got: {self.a2a_socket}"
            )

        # Validate UI_SOCKET
        if not self.ui_socket:
            self.errors.append(
                "UI_SOCKET is required when SOCKET_MODE=true"
            )
        elif not self.ui_socket.startswith("/"):
            self.errors.append(
                f"UI_SOCKET must be an absolute path, got: {self.ui_socket}"
            )

        # Validate socket directory exists (parent directory of sockets)
        if self.rest_socket and self.rest_socket.startswith("/"):
            from pathlib import Path
            socket_dir = Path(self.rest_socket).parent
            if not socket_dir.exists():
                logger.warning(
                    "Socket directory does not exist yet",
                    socket_dir=str(socket_dir),
                    note="Directory should be created before agent starts"
                )

        if self.socket_mode and not self.errors:
            logger.info(
                "Socket mode enabled",
                rest_socket=self.rest_socket,
                a2a_socket=self.a2a_socket,
                ui_socket=self.ui_socket,
            )

    def to_dict(self):
        """Convert configuration to dictionary (for logging/debugging)."""
        config = {
            "agent_app_id": self.agent_app_id,
            "deployment_id": self.deployment_id,
            "socket_mode": self.socket_mode,
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

        if self.socket_mode:
            config["rest_socket"] = self.rest_socket
            config["a2a_socket"] = self.a2a_socket
            config["ui_socket"] = self.ui_socket
        else:
            config["rest_port"] = self.rest_port
            config["a2a_port"] = self.a2a_port
            config["ui_port"] = self.ui_port

        return config

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
