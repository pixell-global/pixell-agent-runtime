import ast
import os
from pathlib import Path


def test_forbidden_imports_runtime_package():
    # Ensure runtime code doesn’t import cloud control-plane SDKs
    # boto3 is allowed for S3 GetObject; forbid control-plane SDKs
    forbidden = {"awscli", "kubernetes", "google.cloud", "azure"}
    root = Path(__file__).resolve().parents[1] / "src" / "pixell_runtime"
    assert root.exists()
    for py in root.rglob("*.py"):
        code = py.read_text(encoding="utf-8")
        try:
            tree = ast.parse(code, filename=str(py))
        except Exception:
            # Skip files that fail to parse in this context
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = []
                if isinstance(node, ast.Import):
                    names = [n.name for n in node.names]
                else:
                    names = [node.module or ""]
                for name in names:
                    for f in forbidden:
                        assert not name.startswith(f)

