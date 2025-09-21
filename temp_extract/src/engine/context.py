from dataclasses import dataclass
from typing import Dict, Any, List


@dataclass
class ExecutionContext:
    """Context for code execution including variables and metadata."""
    code: str
    patterns: List[str]
    full_context: Dict[str, Any]
    files: List[bytes]
    data_info: Dict[str, Any]
    session_id: str