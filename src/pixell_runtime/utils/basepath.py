"""Utilities for handling base path and ports from environment."""

import asyncio
import os
import socket


def normalize_base_path(raw_base_path: str | None) -> str:
    """Normalize BASE_PATH.
    - Ensure it begins with '/'
    - Remove trailing slash except when it is just '/'
    """
    base_path = (raw_base_path or "/").strip()
    if not base_path.startswith("/"):
        base_path = "/" + base_path
    if len(base_path) > 1 and base_path.endswith("/"):
        base_path = base_path[:-1]
    return base_path


def get_base_path() -> str:
    return normalize_base_path(os.getenv("BASE_PATH", "/"))


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            return True
    except OSError:
        return False


def find_free_port(start_port: int, end_port: int, host: str = "127.0.0.1") -> int:
    """Find the first available port in the given range."""
    for port in range(start_port, end_port + 1):
        if is_port_free(port, host):
            return port
    raise RuntimeError(f"No free ports found in range {start_port}-{end_port}")


async def wait_for_port_free(port: int, timeout: int = 30, host: str = "127.0.0.1") -> bool:
    """Wait for a port to become free."""
    deadline = asyncio.get_event_loop().time() + timeout

    while asyncio.get_event_loop().time() < deadline:
        if is_port_free(port, host):
            return True
        await asyncio.sleep(0.5)

    return False


def get_ports(prefer_fixed: bool = True) -> tuple[int, int, int]:
    """Return (rest_port, a2a_port, ui_port) with conflict detection.

    Args:
        prefer_fixed: If True, try to use fixed ports from env first.
                     If False, always scan for free ports.
    """
    # Default port ranges
    default_rest = int(os.getenv("REST_PORT", "8080"))
    default_a2a = int(os.getenv("A2A_PORT", "50051"))
    default_ui = int(os.getenv("UI_PORT", "3000"))

    if prefer_fixed:
        # Try fixed ports first
        if (is_port_free(default_rest) and
            is_port_free(default_a2a) and
            is_port_free(default_ui)):
            return default_rest, default_a2a, default_ui

    # Find available ports in ranges
    try:
        rest_port = find_free_port(default_rest, default_rest + 100)
        a2a_port = find_free_port(default_a2a, default_a2a + 100)
        ui_port = find_free_port(default_ui, default_ui + 100)
        return rest_port, a2a_port, ui_port
    except RuntimeError:
        # Fallback to system-assigned ports
        rest_port = find_free_port(8080, 8180)
        a2a_port = find_free_port(50051, 50151)
        ui_port = find_free_port(3000, 3100)
        return rest_port, a2a_port, ui_port


