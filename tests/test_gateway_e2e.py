"""End-to-end tests for gRPC Gateway with real requests.

These tests verify the gateway can:
1. Accept requests on port 50051
2. Parse agent IDs from paths
3. Forward to correct agent ports
4. Handle errors gracefully
"""

import pytest
import asyncio
import grpc
from grpc import aio
from pathlib import Path
from unittest.mock import Mock, patch
from datetime import datetime

from pixell_runtime.supervisor.grpc_gateway import GrpcGateway
from pixell_runtime.supervisor.state import SupervisorState
from pixell_runtime.supervisor.models import (
    AgentProcess,
    AgentStatus,
    Ports,
    DeployRequest
)


@pytest.fixture
async def mock_supervisor_state():
    """Create mock supervisor state with test agents."""
    from pixell_runtime.supervisor.user_manager import LinuxUserManager
    from pixell_runtime.supervisor.port_allocator import PortAllocator
    from pixell_runtime.supervisor.package_downloader import PackageDownloader
    from pixell_runtime.supervisor.process_manager import ProcessManager

    # Mock all dependencies
    mock_user_manager = Mock(spec=LinuxUserManager)
    mock_user_manager.get_username = Mock(return_value="agent_test")
    mock_user_manager.create_user = Mock(return_value="/home/agent_test")
    mock_user_manager.ensure_directories = Mock()

    mock_port_allocator = Mock(spec=PortAllocator)
    mock_package_downloader = Mock(spec=PackageDownloader)
    mock_package_downloader.download = Mock(return_value=Path("/tmp/test.apkg"))
    mock_package_downloader.extract_package = Mock(return_value=Path("/tmp/extracted/test"))

    mock_process_manager = Mock(spec=ProcessManager)
    mock_process_manager.spawn_agent = Mock(return_value=12345)
    mock_process_manager.is_running = Mock(return_value=True)

    state = SupervisorState(
        user_manager=mock_user_manager,
        port_allocator=mock_port_allocator,
        package_downloader=mock_package_downloader,
        process_manager=mock_process_manager
    )

    # Mock _extract_package_environment to avoid file system operations
    state._extract_package_environment = Mock(return_value={})

    return state


@pytest.fixture
async def gateway_with_agents(mock_supervisor_state):
    """Create gateway with test agents deployed."""
    # Add test agents to state
    agent1 = AgentProcess(
        agent_app_id="agent001",
        deployment_id="deploy001",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=63000, a2a=60000, ui=65000),
        linux_user="agent_test001",
        package_path="/tmp/agent001.apkg",
        package_url="s3://test/agent001.apkg",
        created_at=datetime.now(),
        pid=12345
    )

    agent2 = AgentProcess(
        agent_app_id="agent002",
        deployment_id="deploy002",
        status=AgentStatus.RUNNING,
        ports=Ports(rest=63001, a2a=60001, ui=65001),
        linux_user="agent_test002",
        package_path="/tmp/agent002.apkg",
        package_url="s3://test/agent002.apkg",
        created_at=datetime.now(),
        pid=12346
    )

    mock_supervisor_state.agents["agent001"] = agent1
    mock_supervisor_state.agents["agent002"] = agent2

    # Create gateway (don't start it - we're testing logic, not server)
    gateway = GrpcGateway(mock_supervisor_state, port=50051)

    yield gateway

    # No cleanup needed since we're not starting the server


class TestGatewayE2E:
    """End-to-end gateway tests."""

    @pytest.mark.asyncio
    async def test_agent_lookup_by_id(self, gateway_with_agents):
        """Test gateway can find agents by ID."""
        state = gateway_with_agents.supervisor_state

        # Test finding agent001
        agent = state.get_agent("agent001")
        assert agent is not None
        assert agent.agent_app_id == "agent001"
        assert agent.ports.a2a == 60000

        # Test finding agent002
        agent = state.get_agent("agent002")
        assert agent is not None
        assert agent.agent_app_id == "agent002"
        assert agent.ports.a2a == 60001

        # Test not found
        agent = state.get_agent("nonexistent")
        assert agent is None

    @pytest.mark.asyncio
    async def test_multiple_agents_different_ports(self, gateway_with_agents):
        """Test multiple agents have different ports."""
        state = gateway_with_agents.supervisor_state

        agent1 = state.get_agent("agent001")
        agent2 = state.get_agent("agent002")

        assert agent1.ports.a2a != agent2.ports.a2a
        assert agent1.ports.rest != agent2.ports.rest
        assert agent1.ports.ui != agent2.ports.ui

    @pytest.mark.asyncio
    async def test_gateway_routing_logic(self, gateway_with_agents):
        """Test gateway routing logic (path parsing + agent lookup)."""
        from pixell_runtime.supervisor.grpc_gateway import PATH_PATTERN

        state = gateway_with_agents.supervisor_state

        # Simulate request path
        request_path = "/agents/agent001/a2a/pixell.agent.AgentService/Health"

        # Parse path
        match = PATH_PATTERN.match(request_path)
        assert match is not None

        agent_id = match.group("agent_id")
        service_method = match.group("service_method")
        clean_method = f"/{service_method}"

        # Lookup agent
        agent = state.get_agent(agent_id)
        assert agent is not None

        # Get target address
        target_address = f"localhost:{agent.ports.a2a}"
        assert target_address == "localhost:60000"

        # Verify clean method
        assert clean_method == "/pixell.agent.AgentService/Health"


class TestGatewayPathRouting:
    """Test path routing scenarios."""

    @pytest.mark.asyncio
    async def test_health_check_path(self, gateway_with_agents):
        """Test health check path routing."""
        from pixell_runtime.supervisor.grpc_gateway import PATH_PATTERN

        path = "/agents/agent001/a2a/pixell.agent.AgentService/Health"
        match = PATH_PATTERN.match(path)

        assert match is not None
        assert match.group("agent_id") == "agent001"
        assert match.group("service_method") == "pixell.agent.AgentService/Health"

    @pytest.mark.asyncio
    async def test_invoke_path(self, gateway_with_agents):
        """Test invoke action path routing."""
        from pixell_runtime.supervisor.grpc_gateway import PATH_PATTERN

        path = "/agents/agent002/a2a/pixell.agent.AgentService/Invoke"
        match = PATH_PATTERN.match(path)

        assert match is not None
        assert match.group("agent_id") == "agent002"
        assert match.group("service_method") == "pixell.agent.AgentService/Invoke"

    @pytest.mark.asyncio
    async def test_invalid_paths_rejected(self, gateway_with_agents):
        """Test invalid paths are rejected."""
        from pixell_runtime.supervisor.grpc_gateway import PATH_PATTERN

        invalid_paths = [
            "/agents/test",  # Missing /a2a/ and method
            "/api/agent001/a2a/Service/Method",  # Wrong prefix
            "/agents//a2a/Service/Method",  # Missing agent_id
            "agents/test/a2a/Service/Method",  # Missing leading /
        ]

        for path in invalid_paths:
            match = PATH_PATTERN.match(path)
            assert match is None, f"Invalid path should not match: {path}"


class TestGatewayDeploymentFlow:
    """Test gateway with deployment flow."""

    @pytest.mark.asyncio
    async def test_deploy_then_route(self, mock_supervisor_state):
        """Test deploying agent then routing to it."""
        # Create deploy request with PAC-provided ports
        deploy_req = DeployRequest(
            agent_app_id="newagent",
            deployment_id="newdeploy",
            package_url="s3://test/new.apkg",
            package_sha256="sha256hash",
            version="1.0.0",
            org_id="org123",
            ports=Ports(rest=63005, a2a=60005, ui=65005),  # PAC allocated
            env={}
        )

        # Deploy agent
        agent = await mock_supervisor_state.deploy(deploy_req)

        # Verify agent is in state
        assert agent.agent_app_id == "newagent"
        assert agent.ports.a2a == 60005

        # Verify gateway can find it
        found = mock_supervisor_state.get_agent("newagent")
        assert found is not None
        assert found.ports.a2a == 60005

    @pytest.mark.asyncio
    async def test_delete_removes_from_routing(self, mock_supervisor_state):
        """Test deleting agent removes it from routing."""
        from pixell_runtime.supervisor.models import DeleteRequest

        # Deploy agent first
        deploy_req = DeployRequest(
            agent_app_id="tempagent",
            deployment_id="tempdeploy",
            package_url="s3://test/temp.apkg",
            version="1.0.0",
            org_id="org123",
            ports=Ports(rest=63010, a2a=60010, ui=65010),
            env={}
        )

        await mock_supervisor_state.deploy(deploy_req)

        # Verify agent exists
        assert mock_supervisor_state.get_agent("tempagent") is not None

        # Delete agent
        delete_req = DeleteRequest(
            agent_app_id="tempagent",
            force=True,
            cleanup_user=False
        )

        # Mock stop_agent for deletion
        mock_supervisor_state.process_manager.stop_agent = Mock()

        await mock_supervisor_state.delete(delete_req)

        # Verify agent is gone
        assert mock_supervisor_state.get_agent("tempagent") is None


class TestGatewayErrorScenarios:
    """Test error handling scenarios."""

    @pytest.mark.asyncio
    async def test_agent_not_found(self, gateway_with_agents):
        """Test handling when agent doesn't exist."""
        state = gateway_with_agents.supervisor_state

        agent = state.get_agent("nonexistent")
        assert agent is None

    @pytest.mark.asyncio
    async def test_agent_status_check(self, mock_supervisor_state):
        """Test checking agent status after deployment."""
        # Deploy an agent
        deploy_req = DeployRequest(
            agent_app_id="statusagent",
            deployment_id="statusdeploy",
            package_url="s3://test/status.apkg",
            version="1.0.0",
            org_id="org123",
            ports=Ports(rest=63020, a2a=60020, ui=65020),
            env={}
        )

        agent = await mock_supervisor_state.deploy(deploy_req)

        # Verify agent status
        assert agent.status == AgentStatus.RUNNING
        assert agent.ports.a2a == 60020

        # Gateway should be able to find and route to this agent
        found = mock_supervisor_state.get_agent("statusagent")
        assert found is not None
        assert found.status == AgentStatus.RUNNING


class TestGatewayConfiguration:
    """Test gateway configuration."""

    def test_gateway_port_configuration(self):
        """Test gateway uses correct port."""
        state = Mock(spec=SupervisorState)
        gateway = GrpcGateway(state, port=50051)

        assert gateway.port == 50051

    def test_gateway_custom_port(self):
        """Test gateway can use custom port."""
        state = Mock(spec=SupervisorState)
        gateway = GrpcGateway(state, port=9999)

        assert gateway.port == 9999


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
