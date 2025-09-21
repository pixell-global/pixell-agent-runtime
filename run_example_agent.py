#!/usr/bin/env python3
"""Run the example agent with three-surface runtime."""

import asyncio
import os
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime

async def main():
    """Run the example agent."""
    apkg_path = Path("example-agent.apkg")
    
    if not apkg_path.exists():
        print(f"Example agent package not found: {apkg_path}")
        print("Run 'python build_example_agent.py' first to build the package.")
        return
    
    print("🚀 Starting Example Three-Surface Agent")
    print("=" * 50)
    print(f"Package: {apkg_path}")
    print("Surfaces:")
    print("  📡 REST API: http://localhost:8080")
    print("  🔗 A2A gRPC: localhost:50051")
    print("  🎨 UI: http://localhost:8080/")
    print("=" * 50)
    print("Press Ctrl+C to stop")
    print()
    
    # Set environment variables
    os.environ["REST_PORT"] = "8080"
    os.environ["A2A_PORT"] = "50051"
    os.environ["UI_PORT"] = "3000"
    os.environ["MULTIPLEXED"] = "true"
    
    # Create and start runtime
    runtime = ThreeSurfaceRuntime(str(apkg_path))
    
    try:
        await runtime.start()
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
    finally:
        await runtime.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
