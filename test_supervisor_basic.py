#!/usr/bin/env python3
"""Basic test for supervisor startup."""

import sys
import time
import subprocess
import httpx

# Start supervisor
print("Starting supervisor...")
proc = subprocess.Popen(
    [sys.executable, "src/run_supervisor.py"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True
)

# Read output for 5 seconds
start_time = time.time()
while time.time() - start_time < 5:
    line = proc.stdout.readline()
    if line:
        print(f"SUPERVISOR: {line.strip()}")
    
    # Check if it's running
    try:
        resp = httpx.get("http://localhost:8000/supervisor/status")
        print(f"\nSUCCESS: Supervisor is running! Status code: {resp.status_code}")
        print(f"Response: {resp.json()}")
        break
    except:
        pass
    
    time.sleep(0.5)

# Clean up
print("\nStopping supervisor...")
proc.terminate()
proc.wait()
print("Done")