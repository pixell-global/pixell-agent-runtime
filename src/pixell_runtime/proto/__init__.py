"""Proto package for A2A service.

Exports generated gRPC Python modules.
"""

# Ensure generated modules can import each other using unqualified names
import sys as _sys
from . import agent_pb2 as agent_pb2  # noqa: F401

# Alias unqualified name expected by generated stubs
_sys.modules.setdefault("agent_pb2", agent_pb2)

from . import agent_pb2_grpc as agent_pb2_grpc  # noqa: F401

__all__ = [
    "agent_pb2",
    "agent_pb2_grpc",
]


