#!/usr/bin/env python3
"""Debug worker startup issues."""

import subprocess
import sys
from pathlib import Path

apkg_path = Path("pixell-python-agent-0.1.0.apkg").absolute()

# Run worker with full output
print("Starting worker with debug output...\n")

proc = subprocess.run(
    [
        sys.executable,
        "src/pixell_agent_runtime/worker.py", 
        "--port", "9999",
        "--agent-id", "debug-test",
        "--package-path", str(apkg_path)
    ],
    capture_output=True,
    text=True
)

print("STDOUT:")
print(proc.stdout)
print("\nSTDERR:")
print(proc.stderr)
print(f"\nExit code: {proc.returncode}")