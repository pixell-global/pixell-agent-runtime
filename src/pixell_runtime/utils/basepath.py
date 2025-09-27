"""Utilities for handling base path and ports from environment."""

import os


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


def get_ports() -> tuple[int, int, int]:
    """Return (rest_port, a2a_port, ui_port) with defaults."""
    rest_port = int(os.getenv("REST_PORT", "8080"))
    a2a_port = int(os.getenv("A2A_PORT", "50051"))
    ui_port = int(os.getenv("UI_PORT", "3000"))
    return rest_port, a2a_port, ui_port


