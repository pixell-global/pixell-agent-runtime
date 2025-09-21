"""Test the worker directly to see errors."""

import subprocess
import sys
from pathlib import Path

# Path to worker
worker_path = Path("src/pixell_agent_runtime/worker.py")

# Path to APKG
apkg_path = Path("pixell-python-agent-0.1.0.apkg")

# Run worker directly
cmd = [
    sys.executable,
    str(worker_path),
    "--port", "9999",
    "--agent-id", "debug-test",
    "--package-path", str(apkg_path)
]

print(f"Running: {' '.join(cmd)}")
result = subprocess.run(cmd, capture_output=True, text=True)

print("\n=== STDOUT ===")
print(result.stdout)

print("\n=== STDERR ===")
print(result.stderr)

print(f"\nExit code: {result.returncode}")