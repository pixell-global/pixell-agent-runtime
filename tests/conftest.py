"""
Pytest configuration and fixtures for PAR tests.
"""

import pytest


@pytest.fixture(autouse=True)
def set_default_agent_app_id(monkeypatch):
    """
    Automatically set AGENT_APP_ID for all tests unless explicitly overridden.
    
    This fixture runs automatically for all tests. Tests can override by setting
    AGENT_APP_ID themselves or by deleting it with monkeypatch.delenv().
    """
    # Only set if not already set (allows tests to override)
    import os
    if "AGENT_APP_ID" not in os.environ:
        monkeypatch.setenv("AGENT_APP_ID", "test-agent-default")
