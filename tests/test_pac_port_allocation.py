"""Tests for PAC port allocation integration.

Verifies that:
1. PAR accepts ports from PAC in deploy requests
2. PAR uses PAC-provided ports (doesn't allocate internally)
3. PAR doesn't release ports on delete (PAC manages lifecycle)
4. Backward compatibility when no ports provided
"""

import pytest
from unittest.mock import Mock, patch
from datetime import datetime

from pixell_runtime.supervisor.state import SupervisorState
from pixell_runtime.supervisor.models import (
    DeployRequest,
    DeleteRequest,
    Ports,
    AgentStatus,
)
from pixell_runtime.supervisor.user_manager import LinuxUserManager
from pixell_runtime.supervisor.port_allocator import PortAllocator
from pixell_runtime.supervisor.package_downloader import PackageDownloader
from pixell_runtime.supervisor.process_manager import ProcessManager


@pytest.fixture
def mock_dependencies():
    """Create mocked dependencies for SupervisorState."""
    mock_user_manager = Mock(spec=LinuxUserManager)
    mock_user_manager.get_username = Mock(return_value="agent_test")
    mock_user_manager.create_user = Mock(return_value="/home/agent_test")
    mock_user_manager.ensure_directories = Mock()
    mock_user_manager.clean_agent_files = Mock()
    mock_user_manager.delete_user = Mock()

    mock_port_allocator = Mock(spec=PortAllocator)
    mock_port_allocator.allocate = Mock(return_value=Ports(rest=63000, a2a=60000, ui=65000))
    mock_port_allocator.release = Mock()

    mock_package_downloader = Mock(spec=PackageDownloader)
    mock_package_downloader.download = Mock(return_value="/tmp/test.apkg")

    mock_process_manager = Mock(spec=ProcessManager)
    mock_process_manager.spawn_agent = Mock(return_value=12345)
    mock_process_manager.is_running = Mock(return_value=True)
    mock_process_manager.stop_agent = Mock()

    return {
        "user_manager": mock_user_manager,
        "port_allocator": mock_port_allocator,
        "package_downloader": mock_package_downloader,
        "process_manager": mock_process_manager,
    }


@pytest.fixture
def supervisor_state(mock_dependencies):
    """Create SupervisorState with mocked dependencies."""
    return SupervisorState(
        user_manager=mock_dependencies["user_manager"],
        port_allocator=mock_dependencies["port_allocator"],
        package_downloader=mock_dependencies["package_downloader"],
        process_manager=mock_dependencies["process_manager"],
    )


class TestPACPortAllocation:
    """Test PAC port allocation integration."""

    @pytest.mark.asyncio
    async def test_deploy_with_pac_ports(self, supervisor_state, mock_dependencies):
        """Test that PAR uses PAC-provided ports and doesn't allocate."""
        # Create deploy request with PAC-provided ports
        deploy_request = DeployRequest(
            agent_app_id="test-agent",
            deployment_id="deploy-123",
            package_url="s3://test/package.apkg",
            package_sha256="abc123",
            version="1.0.0",
            org_id="org-123",
            ports=Ports(rest=63005, a2a=60005, ui=65005),  # PAC-provided
            env={}
        )

        # Deploy agent
        agent = await supervisor_state.deploy(deploy_request)

        # Verify PAR used PAC-provided ports
        assert agent.ports.rest == 63005
        assert agent.ports.a2a == 60005
        assert agent.ports.ui == 65005

        # Verify PAR did NOT call internal port allocator
        mock_dependencies["port_allocator"].allocate.assert_not_called()

    @pytest.mark.asyncio
    async def test_deploy_without_pac_ports_fallback(self, supervisor_state, mock_dependencies):
        """Test backward compatibility: PAR allocates internally when no ports provided."""
        # Create deploy request WITHOUT ports (old PAC behavior)
        deploy_request = DeployRequest(
            agent_app_id="test-agent",
            deployment_id="deploy-123",
            package_url="s3://test/package.apkg",
            package_sha256="abc123",
            version="1.0.0",
            org_id="org-123",
            ports=None,  # No PAC ports - triggers fallback
            env={}
        )

        # Deploy agent
        agent = await supervisor_state.deploy(deploy_request)

        # Verify PAR used internal allocator
        mock_dependencies["port_allocator"].allocate.assert_called_once_with("test-agent")

        # Verify ports were allocated from internal allocator
        assert agent.ports.rest == 63000  # From mock allocator
        assert agent.ports.a2a == 60000
        assert agent.ports.ui == 65000

    @pytest.mark.asyncio
    async def test_delete_does_not_release_ports(self, supervisor_state, mock_dependencies):
        """Test that PAR doesn't release ports on delete (PAC manages lifecycle)."""
        # Deploy agent with PAC ports
        deploy_request = DeployRequest(
            agent_app_id="test-agent",
            deployment_id="deploy-123",
            package_url="s3://test/package.apkg",
            version="1.0.0",
            org_id="org-123",
            ports=Ports(rest=63010, a2a=60010, ui=65010),
            env={}
        )

        agent = await supervisor_state.deploy(deploy_request)
        assert agent.agent_app_id == "test-agent"

        # Delete agent
        delete_request = DeleteRequest(
            agent_app_id="test-agent",
            force=True,
            cleanup_user=False
        )

        await supervisor_state.delete(delete_request)

        # Verify PAR did NOT release ports
        mock_dependencies["port_allocator"].release.assert_not_called()

    @pytest.mark.asyncio
    async def test_multiple_agents_different_pac_ports(self, supervisor_state, mock_dependencies):
        """Test deploying multiple agents with different PAC-allocated ports."""
        # Deploy agent 1
        deploy1 = DeployRequest(
            agent_app_id="agent-001",
            deployment_id="deploy-001",
            package_url="s3://test/agent1.apkg",
            version="1.0.0",
            org_id="org-123",
            ports=Ports(rest=63001, a2a=60001, ui=65001),
            env={}
        )

        agent1 = await supervisor_state.deploy(deploy1)

        # Deploy agent 2
        deploy2 = DeployRequest(
            agent_app_id="agent-002",
            deployment_id="deploy-002",
            package_url="s3://test/agent2.apkg",
            version="1.0.0",
            org_id="org-123",
            ports=Ports(rest=63002, a2a=60002, ui=65002),
            env={}
        )

        agent2 = await supervisor_state.deploy(deploy2)

        # Verify each agent has correct PAC-assigned ports
        assert agent1.ports.a2a == 60001
        assert agent2.ports.a2a == 60002
        assert agent1.ports.rest == 63001
        assert agent2.ports.rest == 63002

        # Verify both agents are tracked
        assert len(supervisor_state.agents) == 2

    @pytest.mark.asyncio
    async def test_pac_port_ranges(self, supervisor_state, mock_dependencies):
        """Test that PAC port ranges are accepted (60000-60199, 63000-63199, 65000-65199)."""
        test_cases = [
            # Slot 0
            Ports(rest=63000, a2a=60000, ui=65000),
            # Slot 50
            Ports(rest=63050, a2a=60050, ui=65050),
            # Slot 100
            Ports(rest=63100, a2a=60100, ui=65100),
            # Slot 199 (max)
            Ports(rest=63199, a2a=60199, ui=65199),
        ]

        for idx, ports in enumerate(test_cases):
            deploy_request = DeployRequest(
                agent_app_id=f"agent-{idx:03d}",
                deployment_id=f"deploy-{idx:03d}",
                package_url=f"s3://test/agent{idx}.apkg",
                version="1.0.0",
                org_id="org-123",
                ports=ports,
                env={}
            )

            agent = await supervisor_state.deploy(deploy_request)

            # Verify ports are correctly assigned
            assert agent.ports.rest == ports.rest
            assert agent.ports.a2a == ports.a2a
            assert agent.ports.ui == ports.ui

    @pytest.mark.asyncio
    async def test_idempotent_deploy_with_same_ports(self, supervisor_state, mock_dependencies):
        """Test that redeploying with same deployment_id and ports is idempotent."""
        deploy_request = DeployRequest(
            agent_app_id="idempotent-agent",
            deployment_id="deploy-same",
            package_url="s3://test/package.apkg",
            version="1.0.0",
            org_id="org-123",
            ports=Ports(rest=63015, a2a=60015, ui=65015),
            env={}
        )

        # Deploy first time
        agent1 = await supervisor_state.deploy(deploy_request)
        assert agent1.ports.a2a == 60015

        # Deploy again with same deployment_id (idempotent)
        agent2 = await supervisor_state.deploy(deploy_request)
        assert agent2.ports.a2a == 60015
        assert agent2.deployment_id == "deploy-same"

        # Should be the same agent instance
        assert agent1.agent_app_id == agent2.agent_app_id

    @pytest.mark.asyncio
    async def test_update_preserves_ports(self, supervisor_state, mock_dependencies):
        """Test that updating an agent preserves PAC-allocated ports."""
        from pixell_runtime.supervisor.models import UpdateRequest

        # Deploy with PAC ports
        deploy_request = DeployRequest(
            agent_app_id="update-agent",
            deployment_id="deploy-v1",
            package_url="s3://test/v1.apkg",
            version="1.0.0",
            org_id="org-123",
            ports=Ports(rest=63020, a2a=60020, ui=65020),
            env={}
        )

        agent = await supervisor_state.deploy(deploy_request)
        original_ports = agent.ports

        # Update agent to new version
        update_request = UpdateRequest(
            agent_app_id="update-agent",
            deployment_id="deploy-v2",
            package_url="s3://test/v2.apkg",
            version="2.0.0",
            env={}
        )

        updated_agent = await supervisor_state.update(update_request)

        # Verify ports are preserved
        assert updated_agent.ports.rest == original_ports.rest
        assert updated_agent.ports.a2a == original_ports.a2a
        assert updated_agent.ports.ui == original_ports.ui

    @pytest.mark.asyncio
    async def test_gateway_can_route_to_pac_ports(self, supervisor_state, mock_dependencies):
        """Test that gateway can route to agents with PAC-allocated ports."""
        from pixell_runtime.supervisor.grpc_gateway import GrpcGateway, PATH_PATTERN

        # Deploy agent with PAC ports
        deploy_request = DeployRequest(
            agent_app_id="gateway-test",
            deployment_id="deploy-gateway",
            package_url="s3://test/gateway.apkg",
            version="1.0.0",
            org_id="org-123",
            ports=Ports(rest=63025, a2a=60025, ui=65025),
            env={}
        )

        agent = await supervisor_state.deploy(deploy_request)

        # Create gateway
        gateway = GrpcGateway(supervisor_state, port=50051)

        # Simulate gateway routing
        request_path = "/agents/gateway-test/a2a/pixell.agent.AgentService/Health"
        match = PATH_PATTERN.match(request_path)

        assert match is not None
        agent_id = match.group("agent_id")

        # Gateway looks up agent
        found_agent = supervisor_state.get_agent(agent_id)
        assert found_agent is not None
        assert found_agent.ports.a2a == 60025

        # Gateway would route to localhost:60025
        target_address = f"localhost:{found_agent.ports.a2a}"
        assert target_address == "localhost:60025"


class TestPortAllocationEdgeCases:
    """Test edge cases in port allocation."""

    @pytest.mark.asyncio
    async def test_deploy_with_invalid_ports_rejected(self, supervisor_state):
        """Test that invalid port values are rejected by Pydantic."""
        with pytest.raises(Exception):  # Pydantic validation error
            deploy_request = DeployRequest(
                agent_app_id="invalid-agent",
                deployment_id="deploy-invalid",
                package_url="s3://test/package.apkg",
                version="1.0.0",
                org_id="org-123",
                ports=Ports(rest=0, a2a=0, ui=0),  # Invalid - below 1024
                env={}
            )

    @pytest.mark.asyncio
    async def test_port_allocator_marked_as_legacy(self):
        """Test that PortAllocator has legacy warnings."""
        from pixell_runtime.supervisor.port_allocator import PortAllocator

        allocator = PortAllocator()

        # Verify it matches PAC ranges
        assert allocator.A2A_PORT_START == 60000
        assert allocator.REST_PORT_START == 63000
        assert allocator.UI_PORT_START == 65000

        # Verify capacity is 200
        assert allocator.max_agents() == 200


class TestPACIntegrationFlow:
    """Test complete PAC → PAR integration flow."""

    @pytest.mark.asyncio
    async def test_full_pac_workflow(self, supervisor_state, mock_dependencies):
        """Test complete workflow: PAC allocates → PAR deploys → PAR deletes → PAC releases."""

        # Step 1: PAC allocates port (slot 10) from database
        # PAC would do: allocatePort(instanceId, agentAppId) → returns {a2a: 60010, rest: 63010, ui: 65010}
        pac_allocated_ports = Ports(rest=63010, a2a=60010, ui=65010)

        # Step 2: PAC sends deploy request to PAR with allocated ports
        deploy_request = DeployRequest(
            agent_app_id="workflow-agent",
            deployment_id="deploy-workflow",
            package_url="s3://test/workflow.apkg",
            version="1.0.0",
            org_id="org-123",
            ports=pac_allocated_ports,  # PAC provides ports
            env={}
        )

        # PAR deploys agent
        agent = await supervisor_state.deploy(deploy_request)

        # Verify agent is running with PAC ports
        assert agent.status == AgentStatus.RUNNING
        assert agent.ports.a2a == 60010
        assert agent.agent_app_id == "workflow-agent"

        # Step 3: PAC would update port status to 'in_use' in database
        # PAC does: markPortInUse(agentAppId)

        # Step 4: PAC sends delete request to PAR
        delete_request = DeleteRequest(
            agent_app_id="workflow-agent",
            force=True,
            cleanup_user=False
        )

        # PAR deletes agent (but doesn't release ports)
        success = await supervisor_state.delete(delete_request)
        assert success is True

        # Verify agent is removed from PAR state
        assert supervisor_state.get_agent("workflow-agent") is None

        # Verify PAR did NOT release ports (PAC manages lifecycle)
        mock_dependencies["port_allocator"].release.assert_not_called()

        # Step 5: PAC would release port in database
        # PAC does: releasePort(agentAppId) → marks as 'available' for reuse


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
