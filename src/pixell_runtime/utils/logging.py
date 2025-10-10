"""Logging configuration utilities."""

import logging
import sys
from typing import Optional

import structlog
from structlog.contextvars import bind_contextvars


SENSITIVE_KEYS = {
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "access_key",
    "accesskey",
    "secret_key",
    "secretkey",
}


def _redact_sensitive(_, __, event_dict: dict) -> dict:
    """Redact sensitive fields in the structured log."""
    for key in list(event_dict.keys()):
        if key.lower() in SENSITIVE_KEYS:
            event_dict[key] = "[REDACTED]"
    return event_dict


def setup_logging(log_level: str = "INFO", log_format: str = "json") -> None:
    """Configure structured logging."""
    
    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )
    
    # Configure structlog processors
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        _redact_sensitive,
    ]
    
    if log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def bind_runtime_context(agent_app_id: Optional[str] = None, deployment_id: Optional[str] = None) -> None:
    """Bind correlation fields for runtime logs using contextvars."""
    if agent_app_id:
        bind_contextvars(agentAppId=agent_app_id)
    if deployment_id:
        bind_contextvars(deploymentId=deployment_id)