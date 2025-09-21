#!/usr/bin/env python3
"""Build example agent package."""

import zipfile
from pathlib import Path

def build_agent_package():
    """Build the example agent package."""
    example_dir = Path("example_agent")
    apkg_path = Path("example-agent.apkg")
    
    if not example_dir.exists():
        print(f"Example agent directory not found: {example_dir}")
        return
    
    print(f"Building agent package: {apkg_path}")
    
    with zipfile.ZipFile(apkg_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for file_path in example_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(example_dir)
                zf.write(file_path, arcname)
                print(f"  Added: {arcname}")
    
    print(f"Package built successfully: {apkg_path}")

if __name__ == "__main__":
    build_agent_package()
