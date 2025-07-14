"""Custom exceptions for Pixell Runtime."""

from typing import Optional


class PixellRuntimeError(Exception):
    """Base exception for all runtime errors."""
    
    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


class PackageError(PixellRuntimeError):
    """Package-related errors."""
    pass


class PackageNotFoundError(PackageError):
    """Package not found in registry."""
    pass


class PackageValidationError(PackageError):
    """Package validation failed."""
    pass


class PackageLoadError(PackageError):
    """Failed to load package."""
    pass


class AgentError(PixellRuntimeError):
    """Agent-related errors."""
    pass


class AgentNotFoundError(AgentError):
    """Agent not found."""
    pass


class AgentInvocationError(AgentError):
    """Agent invocation failed."""
    pass


class AuthenticationError(PixellRuntimeError):
    """Authentication failed."""
    pass


class AuthorizationError(PixellRuntimeError):
    """Authorization failed."""
    pass


class ConfigurationError(PixellRuntimeError):
    """Configuration error."""
    pass


class RegistryError(PixellRuntimeError):
    """Registry communication error."""
    pass