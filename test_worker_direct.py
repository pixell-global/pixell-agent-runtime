#!/usr/bin/env python3
"""Test worker directly with Python agent APKG."""

import asyncio
import sys
import subprocess
from pathlib import Path

# Test loading the package directly
print("Testing package loading...")

apkg_path = Path("pixell-python-agent-0.1.0.apkg")
if not apkg_path.exists():
    print(f"ERROR: APKG not found at {apkg_path}")
    sys.exit(1)

print(f"Found APKG at {apkg_path}")

# Start a worker directly
print("\nStarting worker process...")
proc = subprocess.Popen(
    [
        sys.executable,
        "src/pixell_agent_runtime/worker.py",
        "--port", "9001",
        "--agent-id", "test-python-agent",
        "--package-path", str(apkg_path.absolute())
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True
)

# Read output for 10 seconds
import time
start_time = time.time()
while time.time() - start_time < 10:
    line = proc.stdout.readline()
    if line:
        print(f"WORKER: {line.strip()}")
    
    # Check if process died
    if proc.poll() is not None:
        print(f"\nWorker exited with code: {proc.returncode}")
        break
        
    time.sleep(0.1)

# Kill the process
proc.terminate()
proc.wait()
print("\nTest completed")