#!/usr/bin/env python3
"""Test the runtime API with the Python agent."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx


async def test_runtime_api():
    """Test the runtime API."""
    base_url = "http://localhost:8001"
    
    print("=== Testing Pixell Runtime API ===")
    
    async with httpx.AsyncClient() as client:
        # 1. Check health
        print("\n1. Checking runtime health...")
        try:
            response = await client.get(f"{base_url}/runtime/health")
            print(f"   Health: {response.json()}")
        except Exception as e:
            print(f"   ✗ Runtime not accessible: {e}")
            print("   Make sure the runtime is running: python -m pixell_runtime")
            return
        
        # 2. Load the APKG
        print("\n2. Loading Python agent package...")
        apkg_path = Path("pixell-python-agent-0.1.0.apkg").absolute()
        
        try:
            response = await client.post(
                f"{base_url}/runtime/packages/load",
                json={"path": str(apkg_path)}
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"   ✓ Package loaded: {result['package_id']}")
                print(f"   Agents: {result['agents']}")
            else:
                print(f"   ✗ Failed to load: {response.text}")
                return
                
        except Exception as e:
            print(f"   ✗ Error loading package: {e}")
            return
        
        # 3. List agents
        print("\n3. Listing available agents...")
        response = await client.get(f"{base_url}/runtime/agents")
        agents = response.json()["agents"]
        
        for agent in agents:
            print(f"   - {agent['id']}")
            print(f"     Name: {agent['name']}")
            print(f"     Status: {agent['status']}")
        
        # 4. Test code execution
        print("\n4. Testing code execution...")
        
        # Find code executor
        code_executor_id = None
        for agent in agents:
            if "code-executor" in agent["id"]:
                code_executor_id = agent["id"]
                break
        
        if not code_executor_id:
            print("   ✗ Code executor not found")
            return
        
        # Simple test
        print("\n   a) Simple calculation:")
        # URL encode the agent ID
        from urllib.parse import quote
        encoded_id = quote(code_executor_id, safe='')
        
        response = await client.post(
            f"{base_url}/runtime/agents/{encoded_id}/invoke",
            json={
                "input": {
                    "code": "result = 2 + 2\nprint(f'The answer is {result}')",
                    "session_id": "test-1"
                }
            }
        )
        
        if response.status_code != 200:
            print(f"      ✗ Request failed: {response.status_code} - {response.text}")
            return
        
        result = response.json()
        print(f"      Response: {result}")
        if 'output' in result:
            print(f"      Status: {result['output']['status']}")
            print(f"      Result: {result['output']['result']}")
            print(f"      Output: {result['output']['stdout'].strip()}")
        
        # Data analysis test
        print("\n   b) Data analysis with pandas:")
        response = await client.post(
            f"{base_url}/runtime/agents/{encoded_id}/invoke",
            json={
                "input": {
                    "code": """
import pandas as pd
import numpy as np

# Create sample data
df = pd.DataFrame({
    'A': np.random.randn(5),
    'B': np.random.randn(5),
    'C': np.random.randn(5)
})

print("Data shape:", df.shape)
print("\\nData summary:")
print(df.describe())

result = df.mean().to_dict()
""",
                    "session_id": "test-2"
                }
            }
        )
        
        result = response.json()
        print(f"      Status: {result['output']['status']}")
        if result['output']['status'] == 'success':
            print(f"      Mean values: {json.dumps(result['output']['result'], indent=2)}")
            print(f"      Output:\n{result['output']['stdout']}")
        else:
            print(f"      Error: {result['output'].get('error', 'Unknown error')}")
        
        # Session persistence test
        print("\n   c) Session persistence:")
        
        # First call - set variable
        await client.post(
            f"{base_url}/runtime/agents/{encoded_id}/invoke",
            json={
                "input": {
                    "code": "x = 42\nprint(f'Set x = {x}')",
                    "session_id": "persist-test"
                }
            }
        )
        
        # Second call - use variable
        response = await client.post(
            f"{base_url}/runtime/agents/{encoded_id}/invoke",
            json={
                "input": {
                    "code": "result = x * 2\nprint(f'x * 2 = {result}')",
                    "session_id": "persist-test"
                }
            }
        )
        
        result = response.json()
        print(f"      Session works: {result['output']['status'] == 'success'}")
        if result['output']['status'] == 'success':
            print(f"      Output: {result['output']['stdout'].strip()}")


if __name__ == "__main__":
    # First, start the runtime in the background
    print("Starting Pixell Runtime...")
    import subprocess
    
    # Start runtime
    runtime_proc = subprocess.Popen(
        [sys.executable, "-m", "pixell_runtime"],
        env={**os.environ, "LOG_LEVEL": "INFO", "PYTHONUNBUFFERED": "1", "PORT": "8001"}
    )
    
    # Wait for startup
    time.sleep(2)
    
    try:
        # Run tests
        asyncio.run(test_runtime_api())
    finally:
        # Stop runtime
        print("\nStopping runtime...")
        runtime_proc.terminate()
        runtime_proc.wait()