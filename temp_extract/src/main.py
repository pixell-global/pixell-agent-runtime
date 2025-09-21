import asyncio
import argparse
import signal
import sys
from typing import Optional
import structlog

from .a2a.server import serve as serve_grpc
from .sessions.manager import SessionManager

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.dev.ConsoleRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Global session manager
session_manager: Optional[SessionManager] = None


async def shutdown(sig: signal.Signals):
    """Graceful shutdown handler."""
    logger.info("Received shutdown signal", signal=sig.name)
    
    if session_manager:
        await session_manager.stop()
    
    # Cancel all running tasks
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Shutdown complete")


async def main(args):
    """Main entry point."""
    global session_manager
    
    # Initialize session manager
    session_manager = SessionManager()
    await session_manager.start()
    
    # Start gRPC server
    logger.info("Starting Pixell Python Agent", 
                port=args.port,
                use_unix_socket=args.unix_socket)
    
    try:
        await serve_grpc(
            port=args.port,
            use_unix_socket=args.unix_socket
        )
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        await shutdown(signal.SIGTERM)


def run():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Pixell Python Agent - High-performance code execution service"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=50051,
        help="Port to listen on (default: 50051)"
    )
    parser.add_argument(
        "--tcp",
        dest="unix_socket",
        action="store_false",
        help="Use TCP instead of Unix socket"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )
    
    args = parser.parse_args()
    
    # Set log level
    if args.debug:
        structlog.configure(
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=lambda: structlog.PrintLogger(file=sys.stderr),
            cache_logger_on_first_use=False,
        )
    
    # Setup signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda s, f: asyncio.create_task(shutdown(s)))
    
    # Run main
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()