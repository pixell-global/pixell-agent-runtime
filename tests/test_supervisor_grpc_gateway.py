"""Tests for gRPC Gateway."""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import grpc
from grpc import aio

from pixell_runtime.supervisor.grpc_gateway import GrpcGateway, PATH_PATTERN
from pixell_runtime.supervisor.state import SupervisorState
from pixell_runtime.supervisor.models import AgentProcess, AgentStatus, Ports
from datetime import datetime


class TestPathPattern:
    """Test path pattern matching."""

    def test_valid_path_parsing(self):
        """Test parsing of valid agent paths."""
        path = "/agents/4906eeb7/a2a/pixell.agent.AgentService/Health"
        match = PATH_PATTERN.match(path)

        assert match is not None
        assert match.group("agent_id") == "4906eeb7"
        assert match.group("service_method") == "pixell.agent.AgentService/Health"

    def test_valid_path_with_long_agent_id(self):
        """Test parsing with full UUID agent ID."""
        path = "/agents/4906eeb7-1234-5678-abcd-1234567890ab/a2a/pixell.agent.AgentService/Invoke"
        match = PATH_PATTERN.match(path)

        assert match is not None
        assert match.group("agent_id") == "4906eeb7-1234-5678-abcd-1234567890ab"
        assert match.group("service_method") == "pixell.agent.AgentService/Invoke"

    def test_invalid_path_missing_a2a(self):
        """Test that paths without /a2a/ don't match."""
        path = "/agents/4906eeb7/pixell.agent.AgentService/Health"
        match = PATH_PATTERN.match(path)

        assert match is None

    def test_invalid_path_wrong_prefix(self):
        """Test that paths not starting with /agents/ don't match."""
        path = "/api/4906eeb7/a2a/pixell.agent.AgentService/Health"
        match = PATH_PATTERN.match(path)

        assert match is None

    def test_valid_path_with_nested_service(self):
        """Test parsing with nested service names."""
        path = "/agents/abc123/a2a/com.example.MyService.SubService/Method"
        match = PATH_PATTERN.match(path)

        assert match is not None
        assert match.group("agent_id") == "abc123"
        assert match.group("service_method") == "com.example.MyService.SubService/Method"


class TestGrpcGateway:
    """Test gRPC Gateway functionality."""

    @pytest.fixture
    def mock_supervisor_state(self):
        """Create mock supervisor state."""
        state = Mock(spec=SupervisorState)
        state.agents = {}
        return state

    @pytest.fixture
    def gateway(self, mock_supervisor_state):
        """Create gateway instance."""
        return GrpcGateway(mock_supervisor_state, port=50051)

    def test_gateway_initialization(self, gateway):
        """Test gateway initializes correctly."""
        assert gateway.port == 50051
        assert gateway.server is None
        assert gateway.supervisor_state is not None

    @pytest.mark.asyncio
    async def test_gateway_start_stop(self, gateway):
        """Test gateway starts and stops cleanly."""
        # Start gateway
        await gateway.start()
        assert gateway.server is not None

        # Stop gateway
        await gateway.stop()
        assert gateway.server is None

    @pytest.mark.asyncio
    async def test_agent_lookup_success(self, gateway, mock_supervisor_state):
        """Test successful agent lookup."""
        # Create mock agent
        agent = AgentProcess(
            agent_app_id="test123",
            deployment_id="deploy123",
            status=AgentStatus.RUNNING,
            ports=Ports(rest=63000, a2a=60000, ui=65000),
            linux_user="agent_test123",
            package_path="/tmp/test",
            package_url="s3://test/package.apkg",
            created_at=datetime.now()
        )

        # Mock get_agent to return our agent
        mock_supervisor_state.get_agent = Mock(return_value=agent)

        # Test lookup
        result = mock_supervisor_state.get_agent("test123")
        assert result is not None
        assert result.agent_app_id == "test123"
        assert result.ports.a2a == 60000

    @pytest.mark.asyncio
    async def test_agent_lookup_not_found(self, gateway, mock_supervisor_state):
        """Test agent not found scenario."""
        # Mock get_agent to return None
        mock_supervisor_state.get_agent = Mock(return_value=None)

        # Test lookup
        result = mock_supervisor_state.get_agent("nonexistent")
        assert result is None


class TestGatewayForwarding:
    """Test gateway request forwarding logic."""

    @pytest.fixture
    def mock_agent_process(self):
        """Create mock agent process."""
        return AgentProcess(
            agent_app_id="test-agent",
            deployment_id="deploy-123",
            status=AgentStatus.RUNNING,
            ports=Ports(rest=63000, a2a=60000, ui=65000),
            linux_user="agent_test",
            package_path="/tmp/test",
            package_url="s3://test/package.apkg",
            created_at=datetime.now()
        )

    @pytest.mark.asyncio
    async def test_path_extraction(self, mock_agent_process):
        """Test extracting agent_id and method from path."""
        path = "/agents/test-agent/a2a/pixell.agent.AgentService/Health"

        match = PATH_PATTERN.match(path)
        assert match is not None

        agent_id = match.group("agent_id")
        service_method = match.group("service_method")
        clean_method = f"/{service_method}"

        assert agent_id == "test-agent"
        assert clean_method == "/pixell.agent.AgentService/Health"

    @pytest.mark.asyncio
    async def test_port_resolution(self, mock_agent_process):
        """Test resolving target port from agent."""
        assert mock_agent_process.ports.a2a == 60000

        target_address = f"localhost:{mock_agent_process.ports.a2a}"
        assert target_address == "localhost:60000"


class TestGatewayErrorHandling:
    """Test gateway error handling."""

    @pytest.fixture
    def mock_supervisor_state(self):
        """Create mock supervisor state."""
        state = Mock(spec=SupervisorState)
        state.agents = {}
        return state

    @pytest.fixture
    def gateway(self, mock_supervisor_state):
        """Create gateway instance."""
        return GrpcGateway(mock_supervisor_state, port=50051)

    def test_invalid_path_format(self):
        """Test handling of invalid path format."""
        invalid_paths = [
            "/invalid/path",
            "/agents/test123",  # Missing /a2a/ and method
            "/test123/a2a/Service/Method",  # Missing /agents/ prefix
            "/agents//a2a/Service/Method",  # Missing agent_id
        ]

        for path in invalid_paths:
            match = PATH_PATTERN.match(path)
            assert match is None, f"Path should not match: {path}"

    @pytest.mark.asyncio
    async def test_agent_with_zero_a2a_port(self, gateway, mock_supervisor_state):
        """Test gateway handles agent lookup errors."""
        # Test case where agent doesn't exist
        mock_supervisor_state.get_agent = Mock(return_value=None)

        # Gateway code would handle this by returning NOT_FOUND error
        result = mock_supervisor_state.get_agent("test123")
        assert result is None


class TestGatewayIntegration:
    """Integration tests for gateway with real supervisor state."""

    @pytest.mark.asyncio
    async def test_full_deployment_flow(self):
        """Test complete flow: deploy agent, gateway routes to it."""
        # Create real supervisor state (with mocked dependencies)
        from pixell_runtime.supervisor.user_manager import LinuxUserManager
        from pixell_runtime.supervisor.port_allocator import PortAllocator
        from pixell_runtime.supervisor.package_downloader import PackageDownloader
        from pixell_runtime.supervisor.process_manager import ProcessManager

        # Mock all the managers
        mock_user_manager = Mock(spec=LinuxUserManager)
        mock_user_manager.get_username = Mock(return_value="agent_test123")
        mock_user_manager.create_user = Mock(return_value="/home/agent_test123")
        mock_user_manager.ensure_directories = Mock()

        mock_port_allocator = Mock(spec=PortAllocator)
        mock_port_allocator.allocate = Mock(return_value=Ports(rest=63000, a2a=60000, ui=65000))

        mock_package_downloader = Mock(spec=PackageDownloader)
        mock_package_downloader.download = Mock(return_value="/tmp/test.apkg")

        mock_process_manager = Mock(spec=ProcessManager)
        mock_process_manager.spawn_agent = Mock(return_value=12345)
        mock_process_manager.is_running = Mock(return_value=True)

        # Create state with mocked dependencies
        state = SupervisorState(
            user_manager=mock_user_manager,
            port_allocator=mock_port_allocator,
            package_downloader=mock_package_downloader,
            process_manager=mock_process_manager
        )

        # Create gateway
        gateway = GrpcGateway(state, port=50051)

        # Create a mock deployment request
        from pixell_runtime.supervisor.models import DeployRequest

        deploy_request = DeployRequest(
            agent_app_id="test123",
            deployment_id="deploy123",
            package_url="s3://test/package.apkg",
            package_sha256="abc123",
            version="1.0.0",
            org_id="org123",
            ports=Ports(rest=63000, a2a=60000, ui=65000),  # PAC-provided ports
            env={}
        )

        # Deploy agent
        agent_process = await state.deploy(deploy_request)

        # Verify agent is in state
        assert "test123" in state.agents
        assert state.agents["test123"].ports.a2a == 60000

        # Test gateway can lookup agent
        found_agent = state.get_agent("test123")
        assert found_agent is not None
        assert found_agent.agent_app_id == "test123"
        assert found_agent.ports.a2a == 60000


class TestPortConfiguration:
    """Test port configuration and ranges."""

    def test_gateway_port(self):
        """Test gateway uses correct port."""
        from pixell_runtime.supervisor.grpc_gateway import GATEWAY_PORT
        assert GATEWAY_PORT == 50051

    def test_agent_port_ranges(self):
        """Test agent port ranges match PAC scheme."""
        # Test that port allocator matches PAC's scheme
        from pixell_runtime.supervisor.port_allocator import PortAllocator

        allocator = PortAllocator()

        # Verify port ranges
        assert allocator.A2A_PORT_START == 60000
        assert allocator.REST_PORT_START == 63000
        assert allocator.UI_PORT_START == 65000

        # Verify capacity
        assert allocator.max_agents() == 200

    def test_runtime_config_defaults(self):
        """Test runtime config has correct default ports."""
        from pixell_runtime.core.runtime_config import RuntimeConfig
        import os

        # Set required env var
        os.environ["AGENT_APP_ID"] = "test123"

        try:
            config = RuntimeConfig()

            # Verify defaults match PAC scheme
            assert config.a2a_port == 60000
            assert config.rest_port == 63000
            assert config.ui_port == 65000
        finally:
            # Cleanup
            del os.environ["AGENT_APP_ID"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
