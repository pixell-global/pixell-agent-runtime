"""Supervisor CLI entrypoint.

This module provides the main entrypoint for running the supervisor server.
Used by: python -m pixell_runtime.supervisor
"""

import os
import sys
from pathlib import Path
import structlog
from pixell_runtime.utils.logging import setup_logging

# Setup logging before creating logger
log_level = os.getenv("LOG_LEVEL", "INFO").lower()
log_format = os.getenv("LOG_FORMAT", "json").lower()
setup_logging(log_level=log_level, log_format=log_format)

logger = structlog.get_logger()


def ensure_log_directory(log_dir: str) -> None:
    """Ensure log directory exists with proper permissions for agent processes.
    
    Creates the log directory if it doesn't exist and sets permissions
    to allow all users to write (for agent processes running as different users).
    
    Args:
        log_dir: Path to log directory
    """
    if not log_dir:
        return
    
    log_dir_path = Path(log_dir)
    try:
        log_dir_path.mkdir(parents=True, exist_ok=True)
        # Set permissions to allow all users to write (for agent processes running as different users)
        # Use 0o1777 (sticky bit + rwx for all) so all users can create files but only delete their own
        try:
            log_dir_path.chmod(0o1777)
            logger.info("Log directory created/verified with permissions", path=log_dir, permissions="1777")
        except Exception as perm_error:
            # If we can't set permissions (e.g., not running as root), try 0o777
            try:
                log_dir_path.chmod(0o777)
                logger.info("Log directory created/verified with permissions", path=log_dir, permissions="777")
            except Exception:
                # If permission setting fails, log warning but continue
                logger.warning("Could not set permissions on log directory", path=log_dir, error=str(perm_error))
    except Exception as e:
        logger.error("Failed to create log directory", path=log_dir, error=str(e))


def main():
    """Start the supervisor server."""
    logger.info(
        "Starting Pixell Agent Runtime Supervisor",
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        pid=os.getpid(),
    )

    # Get configuration from environment
    host = os.getenv("SUPERVISOR_HOST", "0.0.0.0")
    port = int(os.getenv("SUPERVISOR_PORT", "9000"))
    log_dir = os.getenv("LOG_DIR")
    
    # Ensure log directory exists with proper permissions for agent processes
    if log_dir:
        ensure_log_directory(log_dir)

    logger.info(
        "Supervisor configuration",
        host=host,
        port=port,
        package_cache_dir=os.getenv("PACKAGE_CACHE_DIR", "/var/lib/pixell/packages"),
        package_extract_dir=os.getenv("PACKAGE_EXTRACT_DIR", "/var/lib/pixell/extracted"),
        max_agents=os.getenv("MAX_AGENTS", "20"),
        log_dir=log_dir or "not set",
        log_level=log_level,
        log_format=log_format,
    )

    # Import and run uvicorn
    try:
        import uvicorn
        from pixell_runtime.supervisor.server import app

        # Run server
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=True,
            server_header=False,
            date_header=False,
        )
    except ImportError as e:
        logger.error(
            "Failed to import required dependencies",
            error=str(e),
            missing_package="uvicorn" if "uvicorn" in str(e) else "unknown",
        )
        sys.exit(1)
    except Exception as e:
        logger.error("Supervisor failed to start", error=str(e), exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
