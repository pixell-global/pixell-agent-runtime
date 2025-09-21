"""Compatibility package for legacy imports used by tests.

Exposes selected runtime and supervisor shims under the historical
`pixell_agent_runtime` namespace expected by some tests.
"""

# Re-export Supervisor under this namespace for tests that import
# `from pixell_agent_runtime.supervisor import Supervisor`
try:
    from supervisor.supervisor import Supervisor  # noqa: F401
except Exception:  # pragma: no cover
    Supervisor = None  # type: ignore

__all__ = ["Supervisor"]


