#!/usr/bin/env python3
"""Run the example agent with three-surface runtime."""

import asyncio
import os
import sys
import zipfile
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / "src"))

from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime


def _load_env_from_apkg(apkg_path: Path) -> None:
    """Populate os.environ with values from the APKG's bundled .env file."""
    try:
        with zipfile.ZipFile(apkg_path, "r") as zf:
            if ".env" not in zf.namelist():
                print("No .env found inside APKG; skipping env sync.")
                return

            raw_env = zf.read(".env").decode("utf-8")
    except (zipfile.BadZipFile, OSError) as exc:
        print(f"Failed to read .env from {apkg_path}: {exc}")
        return

    def clean_value(value: str) -> str:
        value = value.strip()
        if not value:
            return value
        if value[0] in ('"', "'") and value[-1] == value[0]:
            return value[1:-1]
        if "#" in value:
            value = value.split("#", 1)[0].rstrip()
        return value

    for line in raw_env.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        value = clean_value(value)

        if not key:
            continue

        existing = os.environ.get(key)
        if existing and existing.strip().lower() not in ("", "none", "null"):
            continue

        os.environ[key] = value


async def main():
    """Run the example agent."""
    apkg_path = Path("vivid-commenter-v2-1.0.9.apkg")
    
    if not apkg_path.exists():
        print(f"Example agent package not found: {apkg_path}")
        print("Run 'python build_example_agent.py' first to build the package.")
        return

    # Mirror production by loading the package's .env into the current process.
    _load_env_from_apkg(apkg_path)
    
    print("Starting Example Three-Surface Agent")
    print("=" * 50)
    print(f"Package: {apkg_path}")
    print("Surfaces:")
    print("  REST API: http://localhost:8080")
    print("  A2A gRPC: localhost:50051")
    print("  UI: http://localhost:3000")
    print("=" * 50)
    print("Press Ctrl+C to stop")
    print()
    
    # Set environment variables
    os.environ["AGENT_APP_ID"] = "example-agent-001"
    os.environ["REST_PORT"] = "8080"
    os.environ["A2A_PORT"] = "50051"
    os.environ["UI_PORT"] = "3000"
    os.environ["MULTIPLEXED"] = "false"
    
    # Create and start runtime
    runtime = ThreeSurfaceRuntime(str(apkg_path))
    
    try:
        await runtime.start()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await runtime.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
