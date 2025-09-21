"""Proto package for A2A service.

Exports generated gRPC Python modules.
"""

from . import agent_pb2  # noqa: F401
from . import agent_pb2_grpc  # noqa: F401

__all__ = [
    "agent_pb2",
    "agent_pb2_grpc",
]


