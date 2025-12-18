"""Logging configuration utilities."""

import logging
import os
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
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


def setup_logging(log_level: str = "INFO", log_format: str = "json", log_file_path: Optional[str] = None) -> None:
    """Configure structured logging.
    
    Args:
        log_level: Logging level (INFO, DEBUG, etc.)
        log_format: Log format ("json" or "console")
        log_file_path: Optional path to log file. If provided, logs will be written to file in addition to stdout.
                      If LOG_DIR env var is set and log_file_path is None, will try to determine file path from context.
    """
    
    # Determine log file path
    if log_file_path is None:
        log_dir = os.getenv("LOG_DIR")
        if log_dir:
            log_dir_path = Path(log_dir)
            try:
                log_dir_path.mkdir(parents=True, exist_ok=True)
                # Set permissions to allow all users to write (for agent processes running as different users)
                # Use 0o1777 (sticky bit + rwx for all) so all users can create files but only delete their own
                try:
                    log_dir_path.chmod(0o1777)
                except Exception as perm_error:
                    # If we can't set permissions (e.g., not running as root), try 0o777
                    try:
                        log_dir_path.chmod(0o777)
                    except Exception:
                        # If permission setting fails, log warning but continue
                        print(f"[LOGGING WARNING] Could not set permissions on log directory {log_dir_path}: {perm_error}", file=sys.stderr, flush=True)
            except Exception as e:
                print(f"[LOGGING ERROR] Failed to create log directory {log_dir_path}: {e}", file=sys.stderr, flush=True)
                log_dir = None  # Disable file logging if directory creation fails
            
            if log_dir:
                # Determine filename based on context
                agent_app_id = os.getenv("AGENT_APP_ID")
                if agent_app_id:
                    # Agent runtime: agent_{agent_app_id}.log
                    log_file_path = str(log_dir_path / f"agent_{agent_app_id}.log")
                else:
                    # Supervisor: supervisor.log
                    log_file_path = str(log_dir_path / "supervisor.log")
        else:
            print("[LOGGING] LOG_DIR environment variable not set, file logging disabled", file=sys.stderr, flush=True)
    
    # Setup root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper()))
    
    # Clear existing handlers to avoid duplicates
    root_logger.handlers.clear()
    
    # Add stdout handler (always)
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(stdout_handler)
    
    # Add file handler if log_file_path is provided
    if log_file_path:
        try:
            log_file = Path(log_file_path)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Use RotatingFileHandler for log rotation (10MB per file, keep 5 backups)
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
                encoding='utf-8'
            )
            file_handler.setFormatter(logging.Formatter("%(message)s"))
            file_handler.setLevel(getattr(logging, log_level.upper()))
            root_logger.addHandler(file_handler)
            
            # Log file handler setup (use print since logger might not be ready yet)
            print(f"[LOGGING] File handler added: {log_file_path}", file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[LOGGING ERROR] Failed to add file handler for {log_file_path}: {e}", file=sys.stderr, flush=True)
    
    # Configure structlog processors
    # Use stdlib logger factory to integrate with Python's logging system (for file handlers)
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_sensitive,
    ]
    
    if log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    
    # Use stdlib LoggerFactory to integrate with Python's logging handlers (including file handlers)
    structlog.configure(
        processors=processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def bind_runtime_context(agent_app_id: Optional[str] = None, deployment_id: Optional[str] = None) -> None:
    """Bind correlation fields for runtime logs using contextvars."""
    if agent_app_id:
        bind_contextvars(agentAppId=agent_app_id)
    if deployment_id:
        bind_contextvars(deploymentId=deployment_id)