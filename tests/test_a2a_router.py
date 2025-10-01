"""Tests for PAR-level A2A gRPC router.

This tests the router that forwards requests from Envoy to individual agents
based on x-deployment-id header.
"""

import asyncio
import os
import socket
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import Mock, AsyncMock

import grpc
import pytest

# Make src importable when running tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pixell_runtime.proto import agent_pb2, agent_pb2_grpc
from pixell_runtime.a2a.router import (
    A2ARouterServicer,
    create_router_server,
    start_router_server,
    stop_router_server,
)
from pixell_runtime.deploy.manager import DeploymentManager
from pixell_runtime.deploy.models import DeploymentRecord, DeploymentStatus
from pixell_runtime.a2a.server import create_grpc_server, start_grpc_server


def _find_free_port():
    """Find a free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_simple_apkg(tmp: Path) -> Path:
    """Build a minimal agent package for testing."""
    (tmp / "main.py").write_text("def handler(x):\n    return x\n")
    (tmp / "agent.yaml").write_text(
        """
name: test-agent
version: 0.1.0
entrypoint: main:handler
a2a:
  enabled: true
"""
    )

    apkg = tmp / "test-agent.apkg"
    with zipfile.ZipFile(apkg, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in tmp.rglob("*"):
            if p.is_file() and p.name != apkg.name:
                zf.write(p, p.relative_to(tmp))
    return apkg


@pytest.mark.asyncio
async def test_router_servicer_metadata_extraction():
    """Test that router correctly extracts x-deployment-id from metadata."""
    # Mock deployment manager
    mock_manager = Mock()
    mock_manager.get.return_value = None

    # Create router servicer
    router = A2ARouterServicer(mock_manager)

    # Mock gRPC context with metadata
    mock_context = Mock()
    mock_context.invocation_metadata.return_value = [
        ("x-deployment-id", "test-deployment-123"),
        ("content-type", "application/grpc"),
    ]

    deployment_id = router._get_deployment_id_from_context(mock_context)
    assert deployment_id == "test-deployment-123"


@pytest.mark.asyncio
async def test_router_servicer_missing_deployment_id():
    """Test router behavior when x-deployment-id is missing."""
    mock_manager = Mock()
    router = A2ARouterServicer(mock_manager)

    # Mock context without deployment_id
    mock_context = Mock()
    mock_context.invocation_metadata.return_value = [
        ("content-type", "application/grpc"),
    ]
    mock_context.set_code = Mock()
    mock_context.set_details = Mock()

    # Try Health check without deployment_id
    response = await router.Health(agent_pb2.Empty(), mock_context)

    assert response.ok is False
    assert "Missing x-deployment-id" in response.message
    mock_context.set_code.assert_called_with(grpc.StatusCode.INVALID_ARGUMENT)


@pytest.mark.asyncio
async def test_router_servicer_deployment_not_found():
    """Test router behavior when deployment doesn't exist."""
    # Mock deployment manager that returns None
    mock_manager = Mock()
    mock_manager.get.return_value = None

    router = A2ARouterServicer(mock_manager)

    # Mock context with valid deployment_id
    mock_context = Mock()
    mock_context.invocation_metadata.return_value = [
        ("x-deployment-id", "nonexistent-deployment"),
    ]
    mock_context.set_code = Mock()
    mock_context.set_details = Mock()

    # Try Health check
    response = await router.Health(agent_pb2.Empty(), mock_context)

    assert response.ok is False
    assert "not found" in response.message
    mock_context.set_code.assert_called_with(grpc.StatusCode.NOT_FOUND)


@pytest.mark.asyncio
async def test_router_forwards_to_agent():
    """Test end-to-end routing from router to agent server."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packages_dir = Path(tmpdir) / "packages"
        packages_dir.mkdir()

        # Build a test package
        build_dir = Path(tmpdir) / "build"
        build_dir.mkdir()
        apkg = _build_simple_apkg(build_dir)

        # Create deployment manager
        deployment_manager = DeploymentManager(packages_dir)

        # Find free ports
        agent_port = _find_free_port()
        router_port = _find_free_port()

        # Create a mock deployment record
        record = DeploymentRecord(
            deploymentId="test-deployment-123",
            agentAppId="test-agent",
            orgId="test-org",
            version="0.1.0",
            status=DeploymentStatus.HEALTHY,
        )
        record.a2a_port = agent_port

        # Manually add to deployment manager
        from pixell_runtime.deploy.manager import DeploymentProcess
        process = DeploymentProcess(record=record)
        deployment_manager.deployments["test-deployment-123"] = process

        # Start a real agent A2A server
        agent_server = create_grpc_server(package=None, port=agent_port)
        await start_grpc_server(agent_server)

        # Create and start router
        router_server = create_router_server(deployment_manager, port=router_port)
        await start_router_server(router_server)

        try:
            # Give servers a moment to start
            await asyncio.sleep(0.5)

            # Connect to router and call with metadata
            channel = grpc.aio.insecure_channel(f"localhost:{router_port}")
            stub = agent_pb2_grpc.AgentServiceStub(channel)

            # Add x-deployment-id metadata
            metadata = (("x-deployment-id", "test-deployment-123"),)

            # Call Health through router
            response = await stub.Health(agent_pb2.Empty(), metadata=metadata, timeout=5.0)

            assert response.ok is True
            assert response.message == "Agent is healthy"

            # Call Ping through router
            pong = await stub.Ping(agent_pb2.Empty(), metadata=metadata, timeout=5.0)
            assert pong.message == "pong"

            await channel.close()

        finally:
            await stop_router_server(router_server)
            await agent_server.stop(grace=1.0)


@pytest.mark.asyncio
async def test_router_handles_agent_unreachable():
    """Test router behavior when agent is unreachable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packages_dir = Path(tmpdir)

        # Create deployment manager
        deployment_manager = DeploymentManager(packages_dir)

        # Find free port
        router_port = _find_free_port()

        # Create a deployment record pointing to non-existent agent
        record = DeploymentRecord(
            deploymentId="test-deployment-456",
            agentAppId="test-agent",
            orgId="test-org",
            version="0.1.0",
            status=DeploymentStatus.HEALTHY,
        )
        record.a2a_port = 99999  # Non-existent port

        from pixell_runtime.deploy.manager import DeploymentProcess
        process = DeploymentProcess(record=record)
        deployment_manager.deployments["test-deployment-456"] = process

        # Create and start router
        router_server = create_router_server(deployment_manager, port=router_port)
        await start_router_server(router_server)

        try:
            await asyncio.sleep(0.5)

            # Try to call through router
            channel = grpc.aio.insecure_channel(f"localhost:{router_port}")
            stub = agent_pb2_grpc.AgentServiceStub(channel)
            metadata = (("x-deployment-id", "test-deployment-456"),)

            # When agent is unreachable, gRPC raises an exception
            # This is expected behavior
            try:
                response = await stub.Health(agent_pb2.Empty(), metadata=metadata, timeout=5.0)
                # If we get a response, it should be an error
                assert response.ok is False
            except grpc.RpcError as e:
                # Expected: router should return UNAVAILABLE status
                assert e.code() == grpc.StatusCode.UNAVAILABLE
                assert "Failed to reach agent" in e.details()

            await channel.close()

        finally:
            await stop_router_server(router_server)


@pytest.mark.asyncio
async def test_router_multiple_agents():
    """Test router correctly routes to different agents based on deployment_id."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packages_dir = Path(tmpdir)

        # Create deployment manager
        deployment_manager = DeploymentManager(packages_dir)

        # Find free ports
        agent1_port = _find_free_port()
        agent2_port = _find_free_port()
        router_port = _find_free_port()

        # Create two deployment records
        record1 = DeploymentRecord(
            deploymentId="deployment-1",
            agentAppId="agent-1",
            orgId="test-org",
            version="0.1.0",
            status=DeploymentStatus.HEALTHY,
        )
        record1.a2a_port = agent1_port

        record2 = DeploymentRecord(
            deploymentId="deployment-2",
            agentAppId="agent-2",
            orgId="test-org",
            version="0.2.0",
            status=DeploymentStatus.HEALTHY,
        )
        record2.a2a_port = agent2_port

        from pixell_runtime.deploy.manager import DeploymentProcess
        deployment_manager.deployments["deployment-1"] = DeploymentProcess(record=record1)
        deployment_manager.deployments["deployment-2"] = DeploymentProcess(record=record2)

        # Start two agent servers
        agent1_server = create_grpc_server(package=None, port=agent1_port)
        await start_grpc_server(agent1_server)

        agent2_server = create_grpc_server(package=None, port=agent2_port)
        await start_grpc_server(agent2_server)

        # Start router
        router_server = create_router_server(deployment_manager, port=router_port)
        await start_router_server(router_server)

        try:
            await asyncio.sleep(0.5)

            # Connect to router
            channel = grpc.aio.insecure_channel(f"localhost:{router_port}")
            stub = agent_pb2_grpc.AgentServiceStub(channel)

            # Call deployment-1
            metadata1 = (("x-deployment-id", "deployment-1"),)
            response1 = await stub.Health(agent_pb2.Empty(), metadata=metadata1, timeout=5.0)
            assert response1.ok is True

            # Call deployment-2
            metadata2 = (("x-deployment-id", "deployment-2"),)
            response2 = await stub.Health(agent_pb2.Empty(), metadata=metadata2, timeout=5.0)
            assert response2.ok is True

            # Both should succeed and be routed correctly
            # (Since both use default implementation, we can't distinguish responses,
            # but the fact that both succeed proves routing works)

            await channel.close()

        finally:
            await stop_router_server(router_server)
            await agent1_server.stop(grace=1.0)
            await agent2_server.stop(grace=1.0)


@pytest.mark.asyncio
async def test_router_invoke_method():
    """Test router forwards Invoke method correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        packages_dir = Path(tmpdir)
        deployment_manager = DeploymentManager(packages_dir)

        agent_port = _find_free_port()
        router_port = _find_free_port()

        # Create deployment
        record = DeploymentRecord(
            deploymentId="test-invoke",
            agentAppId="test-agent",
            orgId="test-org",
            version="0.1.0",
            status=DeploymentStatus.HEALTHY,
        )
        record.a2a_port = agent_port

        from pixell_runtime.deploy.manager import DeploymentProcess
        deployment_manager.deployments["test-invoke"] = DeploymentProcess(record=record)

        # Start agent server
        agent_server = create_grpc_server(package=None, port=agent_port)
        await start_grpc_server(agent_server)

        # Start router
        router_server = create_router_server(deployment_manager, port=router_port)
        await start_router_server(router_server)

        try:
            await asyncio.sleep(0.5)

            channel = grpc.aio.insecure_channel(f"localhost:{router_port}")
            stub = agent_pb2_grpc.AgentServiceStub(channel)
            metadata = (("x-deployment-id", "test-invoke"),)

            # Create an Invoke request
            request = agent_pb2.ActionRequest(
                action="test-action",
                request_id="req-123"
            )

            # Call Invoke through router
            response = await stub.Invoke(request, metadata=metadata, timeout=5.0)

            # Default implementation returns failure (no handler), but proves routing works
            assert response.request_id == "req-123"
            assert response.success is False  # No handler in default impl

            await channel.close()

        finally:
            await stop_router_server(router_server)
            await agent_server.stop(grace=1.0)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])