"""Test Python agent directly without supervisor."""

import asyncio
import json
import sys
from pathlib import Path
import subprocess
import time
import requests

# Start worker in background
worker_path = Path("src/pixell_agent_runtime/worker.py")
apkg_path = Path("pixell-python-agent-0.1.0.apkg")

cmd = [
    sys.executable,
    str(worker_path),
    "--port", "9999",
    "--agent-id", "python-agent",
    "--package-path", str(apkg_path)
]

print(f"Starting worker: {' '.join(cmd)}")
process = subprocess.Popen(cmd)

# Wait for startup
print("Waiting for worker to start...")
time.sleep(3)

try:
    # Test health
    print("\n=== Testing health ===")
    resp = requests.get("http://localhost:9999/health")
    print(f"Health: {json.dumps(resp.json(), indent=2)}")
    
    # Test get_info
    print("\n=== Testing get_info ===")
    resp = requests.post("http://localhost:9999/exports/get_info", json={})
    print(f"Info: {json.dumps(resp.json(), indent=2)}")
    
    # Test code execution
    print("\n=== Testing code execution ===")
    resp = requests.post("http://localhost:9999/exports/execute", json={
        "code": "print('Hello from Python agent!')\nresult = 2 + 2\nprint(f'Result: {result}')",
        "session_id": "test-1"
    })
    print(f"Execution result: {json.dumps(resp.json(), indent=2)}")
    
    # Test with numpy
    print("\n=== Testing numpy ===")
    resp = requests.post("http://localhost:9999/exports/execute", json={
        "code": """
import numpy as np
arr = np.array([1, 2, 3, 4, 5])
print(f"Array: {arr}")
print(f"Mean: {arr.mean()}")
print(f"Sum: {arr.sum()}")
""",
        "session_id": "test-2"
    })
    print(f"Numpy result: {json.dumps(resp.json(), indent=2)}")

finally:
    # Clean up
    print("\nStopping worker...")
    process.terminate()
    process.wait(timeout=5)
    print("Test complete!")