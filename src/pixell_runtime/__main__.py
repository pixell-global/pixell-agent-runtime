"""CLI entrypoints for PAR local dev (par run, par status)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from pixell_runtime.main import run as run_server
from pixell_runtime.deploy.manager import DeploymentManager
from pixell_runtime.deploy.models import DeploymentRequest, SurfacesConfig


def main():
    parser = argparse.ArgumentParser(prog="par", description="Pixell Agent Runtime CLI")
    sub = parser.add_subparsers(dest="cmd")

    cmd_run = sub.add_parser("run", help="Run a local APKG")
    cmd_run.add_argument("package", help="Path to .apkg file")

    cmd_status = sub.add_parser("status", help="Show running deployments")

    args = parser.parse_args()

    if args.cmd == "run":
        # Quick local single deployment: set env and start three-surface directly
        from pixell_runtime.three_surface.runtime import ThreeSurfaceRuntime
        os.environ.setdefault("BASE_PATH", "/")
        runtime = ThreeSurfaceRuntime(args.package)
        asyncio.run(runtime.start())
        return

    if args.cmd == "status":
        # For now, status via HTTP endpoint
        print("Use /runtime/health and /runtime/deployments/{id}/health endpoints")
        return

    # default: start server
    run_server()


if __name__ == "__main__":
    main()