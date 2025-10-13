"""Supervisor CLI entrypoint.

This module provides the main entrypoint for running the supervisor server.
Used by: python -m pixell_runtime.supervisor
"""

import os
import sys
import structlog

logger = structlog.get_logger()


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

    logger.info(
        "Supervisor configuration",
        host=host,
        port=port,
        package_cache_dir=os.getenv("PACKAGE_CACHE_DIR", "/var/lib/pixell/packages"),
        package_extract_dir=os.getenv("PACKAGE_EXTRACT_DIR", "/var/lib/pixell/extracted"),
        max_agents=os.getenv("MAX_AGENTS", "20"),
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
