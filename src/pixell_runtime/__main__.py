"""CLI entrypoints for PAR local dev (par run, par status)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


def main():
    parser = argparse.ArgumentParser(prog="par", description="Pixell Agent Runtime CLI")
    sub = parser.add_subparsers(dest="cmd")

    cmd_run = sub.add_parser("run", help="Run a local APKG")
    cmd_run.add_argument("package", help="Path to .apkg file")

    cmd_status = sub.add_parser("status", help="Show running deployments")

    # Accept and ignore unknown args (e.g., --rest-port) to avoid breaking subprocess invocations
    args, _unknown = parser.parse_known_args()

    # If AGENT_PACKAGE_PATH is set, run three-surface runtime directly (subprocess mode)
    pkg_path = os.getenv("AGENT_PACKAGE_PATH")
    if pkg_path:
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        os.environ.setdefault("BASE_PATH", "/")
        runtime = ThreeSurfaceRuntime(pkg_path)
        asyncio.run(runtime.start())
        return

    if args.cmd == "run":
        # Quick local single deployment: set env and start three-surface directly
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        os.environ.setdefault("BASE_PATH", "/")
        runtime = ThreeSurfaceRuntime(args.package)
        asyncio.run(runtime.start())
        return

    if args.cmd == "status":
        # Status command removed - PAR no longer manages deployments
        # Use PAC (Pixell Agent Cloud) for deployment status
        print("ERROR: 'par status' is no longer supported.")
        print("PAR is now a single-agent runtime. Use PAC for deployment management.")
        sys.exit(1)

    # Check if PACKAGE_URL is set (Fargate/ECS mode)
    package_url = os.getenv("PACKAGE_URL")
    if package_url:
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        # Runtime will download package from PACKAGE_URL during load_package()
        runtime = ThreeSurfaceRuntime(package_path=None)
        asyncio.run(runtime.start())
        return

    # No default server mode - PAR only runs single agents
    print("ERROR: PAR must be run with 'par run <package>' or AGENT_PACKAGE_PATH or PACKAGE_URL env var")
    print("For multi-agent deployment management, use PAC (Pixell Agent Cloud)")
    sys.exit(1)


if __name__ == "__main__":
    main()