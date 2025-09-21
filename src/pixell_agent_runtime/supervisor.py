"""Compatibility shim to expose Supervisor under pixell_agent_runtime.supervisor.

Some tests import Supervisor using `from pixell_agent_runtime.supervisor import Supervisor`.
This module re-exports the implementation from the new `supervisor` package.
"""

from supervisor.supervisor import Supervisor  # noqa: F401

__all__ = ["Supervisor"]


