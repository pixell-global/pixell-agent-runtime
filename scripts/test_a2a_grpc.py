#!/usr/bin/env python3
"""
Test the agent A2A gRPC connection for this agent app.

Features:
- Health, Ping, DescribeCapabilities calls
- Optional Invoke with --action and --params JSON
- Optional x-deployment-id metadata (for router/NLB scenarios)
- Configurable endpoint via --endpoint or --host/--port
"""

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, Optional, Tuple
from pathlib import Path

# Ensure local src/ is importable for protobuf stubs
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import grpc
from pixell_runtime.proto import agent_pb2, agent_pb2_grpc


def parse_metadata(deployment_id: Optional[str]) -> Optional[Tuple[Tuple[str, str], ...]]:
    if not deployment_id:
        return None
    return (("x-deployment-id", deployment_id),)


def parse_params_json(params: Optional[str]) -> Dict[str, Any]:
    if not params:
        return {}
    try:
        return json.loads(params)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Invalid JSON for --params: {e}")


def resolve_deployment_id(args: argparse.Namespace) -> Optional[str]:
    if args.deployment_id:
        return args.deployment_id
    if args.deployment_json:
        try:
            data = json.loads(args.deployment_json)
            # Accept either {"deployment": {"id": "..."}} or {"id": "..."}
            if isinstance(data, dict):
                if "deployment" in data and isinstance(data["deployment"], dict):
                    return data["deployment"].get("id")
                return data.get("id")
        except json.JSONDecodeError as e:
            raise SystemExit(f"Invalid JSON for --deployment-json: {e}")
    return None


def resolve_endpoint(args: argparse.Namespace) -> str:
    if args.endpoint:
        return args.endpoint
    host = args.host or "127.0.0.1"
    port = int(args.port or 50051)
    return f"{host}:{port}"


async def run_tests(endpoint: str, metadata: Optional[Tuple[Tuple[str, str], ...]], timeout: float,
                    action: Optional[str], params: Dict[str, Any]) -> int:
    print(f"🔗 Connecting to A2A gRPC at {endpoint}")
    if metadata:
        md_print = ", ".join([f"{k}={v}" for k, v in metadata])
        print(f"🧾 Using metadata: {md_print}")
    print("=" * 60)

    channel = grpc.aio.insecure_channel(endpoint)
    stub = agent_pb2_grpc.AgentServiceStub(channel)

    exit_code = 0

    # 1) Health
    print("1️⃣  Health")
    try:
        resp = await stub.Health(agent_pb2.Empty(), timeout=timeout, metadata=metadata)
        print(f"   ✅ ok={resp.ok}, message='{resp.message}', ts={resp.timestamp}")
    except Exception as e:
        print(f"   ❌ {type(e).__name__}: {e}")
        exit_code = 1

    # 2) Ping
    print("2️⃣  Ping")
    try:
        resp = await stub.Ping(agent_pb2.Empty(), timeout=timeout, metadata=metadata)
        print(f"   ✅ message='{resp.message}', ts={resp.timestamp}")
    except Exception as e:
        print(f"   ❌ {type(e).__name__}: {e}")
        exit_code = 1

    # 3) DescribeCapabilities
    print("3️⃣  DescribeCapabilities")
    try:
        resp = await stub.DescribeCapabilities(agent_pb2.Empty(), timeout=timeout, metadata=metadata)
        print(f"   ✅ methods={list(resp.methods)}")
        print(f"   ✅ metadata={dict(resp.metadata)}")
    except Exception as e:
        print(f"   ❌ {type(e).__name__}: {e}")
        exit_code = 1

    # 4) Optional Invoke
    if action:
        print("4️⃣  Invoke")
        try:
            request = agent_pb2.ActionRequest(
                action=action,
                parameters={k: str(v) for k, v in params.items()},
                request_id=f"test-{int(time.time())}"
            )
            resp = await stub.Invoke(request, timeout=timeout, metadata=metadata)
            print("   ✅ Invoke result:")
            print(f"      success={resp.success}")
            print(f"      result={resp.result}")
            if resp.error:
                print(f"      error={resp.error}")
            print(f"      duration_ms={resp.duration_ms}")
        except Exception as e:
            print(f"   ❌ {type(e).__name__}: {e}")
            exit_code = 1

    await channel.close()
    print("=" * 60)
    print("Done")
    return exit_code


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Test agent A2A gRPC connectivity")
    p.add_argument("--endpoint", help="gRPC endpoint host:port (overrides --host/--port)")
    p.add_argument("--host", default="127.0.0.1", help="gRPC host (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=50051, help="gRPC port (default: 50051)")
    p.add_argument("--deployment-id", help="Deployment ID to send as x-deployment-id metadata")
    p.add_argument("--deployment-json", help="Deployment JSON string to extract id from")
    p.add_argument("--timeout", type=float, default=10.0, help="RPC timeout seconds (default: 10.0)")
    p.add_argument("--action", help="Optional action name to Invoke, e.g. 'process_data'")
    p.add_argument("--params", help="Optional JSON dict for --action params, e.g. '{\"data\": \"hello\"}'")
    return p


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    endpoint = resolve_endpoint(args)
    deployment_id = resolve_deployment_id(args)
    metadata = parse_metadata(deployment_id)
    params = parse_params_json(args.params)

    # Convenience: allow providing deployment JSON via env var for CI
    if not deployment_id:
        env_json = os.getenv("DEPLOYMENT_JSON")
        if env_json:
            try:
                deployment_id = json.loads(env_json).get("deployment", {}).get("id") or json.loads(env_json).get("id")
                metadata = parse_metadata(deployment_id)
            except Exception:
                pass

    print("🤖 A2A gRPC Connectivity Test")
    print(f"📍 Endpoint: {endpoint}")
    if deployment_id:
        print(f"🆔 Deployment ID: {deployment_id}")

    return asyncio.run(
        run_tests(
            endpoint=endpoint,
            metadata=metadata,
            timeout=float(args.timeout),
            action=args.action,
            params=params,
        )
    )


if __name__ == "__main__":
    sys.exit(main())


