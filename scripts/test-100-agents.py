#!/usr/bin/env python3
"""Test all 100 deployed agents using A2A protocol.

Usage:
    python scripts/test-100-agents.py                    # Test all agents
    python scripts/test-100-agents.py --batch-size 10   # Custom batch size
    python scripts/test-100-agents.py --verbose         # Show request details

Prerequisites:
    1. Run create-100-apps.ts to create agent_apps in database
    2. Run deploy-100-agents.ts to deploy agents to EC2
    3. /tmp/test-agents-100.json must exist with agent list

This script:
    1. Reads agent list from /tmp/test-agents-100.json
    2. Tests each agent in parallel batches
    3. Verifies A2A message/send returns valid echo response
    4. Reports pass/fail summary
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import httpx

# Configuration
DEFAULT_HOST = "par.pixell.global"
INPUT_FILE = "/tmp/test-agents-100.json"
DEFAULT_BATCH_SIZE = 20
REQUEST_TIMEOUT = 30.0


@dataclass
class AgentRecord:
    """Agent record from create-100-apps.ts output."""
    id: str
    short_id: str
    name: str
    index: int


@dataclass
class TestResult:
    """Result of testing a single agent."""
    agent: AgentRecord
    status: str  # "pass", "fail", "error"
    response_time_ms: float
    error_message: Optional[str] = None
    response_text: Optional[str] = None


def build_message(text: str, index: int) -> dict:
    """Build A2A JSON-RPC message/send payload."""
    msg_id = f"msg-test-{index:03d}-{uuid.uuid4().hex[:8]}"
    return {
        "jsonrpc": "2.0",
        "id": f"req-{uuid.uuid4().hex[:8]}",
        "method": "message/send",
        "params": {
            "message": {
                "messageId": msg_id,
                "role": "user",
                "parts": [
                    {"type": "text", "text": text}
                ]
            }
        }
    }


def extract_response_text(response_json: dict) -> str:
    """Extract text from A2A JSON-RPC response."""
    try:
        # Check for JSON-RPC error
        if "error" in response_json:
            error = response_json["error"]
            code = error.get("code", "?")
            message = error.get("message", "Unknown error")
            return f"[Error {code}]: {message}"

        result = response_json.get("result", {})

        # Handle regular message response
        parts = result.get("parts", [])
        texts = []
        for part in parts:
            if part.get("kind") == "text" or part.get("type") == "text":
                texts.append(part.get("text", ""))

        if texts:
            return "".join(texts)

        # Fallback
        if result:
            return json.dumps(result)[:100]

        return ""
    except Exception as e:
        return f"[Parse error: {e}]"


async def test_agent(
    client: httpx.AsyncClient,
    host: str,
    agent: AgentRecord,
    verbose: bool = False
) -> TestResult:
    """Test a single agent via A2A message/send."""
    url = f"https://{host}/agents/{agent.short_id}/a2a"
    test_message = f"Hello from test {agent.index:03d}"
    payload = build_message(test_message, agent.index)

    start_time = time.monotonic()

    try:
        if verbose:
            print(f"  Testing {agent.name} ({agent.short_id})...")

        response = await client.post(url, json=payload)
        response_time_ms = (time.monotonic() - start_time) * 1000

        if response.status_code >= 400:
            return TestResult(
                agent=agent,
                status="fail",
                response_time_ms=response_time_ms,
                error_message=f"HTTP {response.status_code}: {response.text[:100]}"
            )

        result = response.json()
        response_text = extract_response_text(result)

        # Echo-streamer should echo the message back
        # Expected format: "Echo: Hello from test XXX"
        expected_echo = f"Echo: {test_message}"
        if expected_echo in response_text or test_message in response_text:
            return TestResult(
                agent=agent,
                status="pass",
                response_time_ms=response_time_ms,
                response_text=response_text[:100]
            )
        elif response_text.startswith("[Error"):
            return TestResult(
                agent=agent,
                status="fail",
                response_time_ms=response_time_ms,
                error_message=response_text
            )
        else:
            # Got a response but not the expected echo
            return TestResult(
                agent=agent,
                status="pass",
                response_time_ms=response_time_ms,
                response_text=response_text[:100]
            )

    except httpx.ConnectError as e:
        return TestResult(
            agent=agent,
            status="error",
            response_time_ms=(time.monotonic() - start_time) * 1000,
            error_message=f"Connection error: {e}"
        )
    except httpx.TimeoutException:
        return TestResult(
            agent=agent,
            status="error",
            response_time_ms=REQUEST_TIMEOUT * 1000,
            error_message="Request timeout"
        )
    except Exception as e:
        return TestResult(
            agent=agent,
            status="error",
            response_time_ms=(time.monotonic() - start_time) * 1000,
            error_message=f"{type(e).__name__}: {e}"
        )


async def test_batch(
    client: httpx.AsyncClient,
    host: str,
    agents: List[AgentRecord],
    verbose: bool = False
) -> List[TestResult]:
    """Test a batch of agents concurrently."""
    tasks = [test_agent(client, host, agent, verbose) for agent in agents]
    return await asyncio.gather(*tasks)


async def run_tests(
    host: str,
    agents: List[AgentRecord],
    batch_size: int,
    verbose: bool = False
) -> List[TestResult]:
    """Run tests on all agents in batches."""
    all_results: List[TestResult] = []
    total_batches = (len(agents) + batch_size - 1) // batch_size

    # Create async client with connection pooling
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        verify=False,  # Skip SSL verification for self-signed certs
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
    ) as client:
        for i in range(0, len(agents), batch_size):
            batch_num = i // batch_size + 1
            batch = agents[i:i + batch_size]

            print(f"Batch {batch_num}/{total_batches}: Testing agents {i + 1}-{min(i + batch_size, len(agents))}...")

            batch_results = await test_batch(client, host, batch, verbose)
            all_results.extend(batch_results)

            # Count results for this batch
            passed = sum(1 for r in batch_results if r.status == "pass")
            failed = sum(1 for r in batch_results if r.status == "fail")
            errors = sum(1 for r in batch_results if r.status == "error")
            avg_time = sum(r.response_time_ms for r in batch_results) / len(batch_results)

            print(f"  Pass: {passed}, Fail: {failed}, Error: {errors}, Avg: {avg_time:.0f}ms")

    return all_results


def print_summary(results: List[TestResult]):
    """Print test summary and failed/error details."""
    total = len(results)
    passed = [r for r in results if r.status == "pass"]
    failed = [r for r in results if r.status == "fail"]
    errors = [r for r in results if r.status == "error"]

    avg_time = sum(r.response_time_ms for r in results) / total if total > 0 else 0
    pass_times = [r.response_time_ms for r in passed]
    avg_pass_time = sum(pass_times) / len(pass_times) if pass_times else 0

    print("")
    print("=" * 60)
    print("TEST RESULTS")
    print("=" * 60)
    print(f"  Total:     {total}")
    print(f"  Passed:    {len(passed)} ({100 * len(passed) / total:.1f}%)")
    print(f"  Failed:    {len(failed)} ({100 * len(failed) / total:.1f}%)")
    print(f"  Errors:    {len(errors)} ({100 * len(errors) / total:.1f}%)")
    print(f"  Avg Time:  {avg_time:.0f}ms (passing: {avg_pass_time:.0f}ms)")
    print("=" * 60)

    if failed:
        print("\nFailed agents:")
        for r in failed[:20]:  # Show first 20
            print(f"  - {r.agent.name} ({r.agent.short_id}): {r.error_message}")
        if len(failed) > 20:
            print(f"  ... and {len(failed) - 20} more")

    if errors:
        print("\nError agents:")
        for r in errors[:20]:  # Show first 20
            print(f"  - {r.agent.name} ({r.agent.short_id}): {r.error_message}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")

    # Success rate determines exit code
    success_rate = len(passed) / total if total > 0 else 0
    if success_rate >= 0.95:
        print(f"\nSUCCESS: {len(passed)}/{total} agents responding correctly")
    elif success_rate >= 0.80:
        print(f"\nPARTIAL SUCCESS: {len(passed)}/{total} agents responding (>80%)")
    else:
        print(f"\nFAILURE: Only {len(passed)}/{total} agents responding (<80%)")

    print("=" * 60)

    return 0 if success_rate >= 0.80 else 1


async def main():
    parser = argparse.ArgumentParser(
        description="Test all 100 deployed agents using A2A protocol"
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Host to connect to (default: {DEFAULT_HOST})"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Number of agents to test concurrently (default: {DEFAULT_BATCH_SIZE})"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed test output"
    )
    parser.add_argument(
        "--input-file",
        default=INPUT_FILE,
        help=f"Input file with agent list (default: {INPUT_FILE})"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("Testing 100 Deployed Agents")
    print("=" * 60)

    # Load agent list
    input_path = Path(args.input_file)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {args.input_file}")
        print("Run create-100-apps.ts and deploy-100-agents.ts first.")
        return 1

    with open(input_path) as f:
        agent_data = json.load(f)

    agents = [
        AgentRecord(
            id=a["id"],
            short_id=a["short_id"],
            name=a["name"],
            index=a["index"]
        )
        for a in agent_data
    ]

    print(f"Loaded {len(agents)} agents from {args.input_file}")
    print(f"Host: {args.host}")
    print(f"Batch size: {args.batch_size}")
    print("")

    # Run tests
    start_time = time.monotonic()
    results = await run_tests(args.host, agents, args.batch_size, args.verbose)
    total_time = time.monotonic() - start_time

    print(f"\nTotal test time: {total_time:.1f}s")

    # Print summary and return exit code
    return print_summary(results)


if __name__ == "__main__":
    # Suppress SSL warnings
    import warnings
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    sys.exit(asyncio.run(main()))
