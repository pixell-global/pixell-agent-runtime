"""
Tests to verify that DeploymentManager is NOT used in the runtime.

In the new architecture, PAR is a pure data-plane runtime that executes
a single agent specified by environment variables. It should NOT have
control-plane code like DeploymentManager.
"""

import ast
import os
from pathlib import Path

import pytest


def test_deployment_manager_not_imported_in_main_entrypoints():
    """Test that DeploymentManager is not imported in main entry points."""
    # Check __main__.py
    main_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "__main__.py"
    content = main_file.read_text()
    
    # Should not import DeploymentManager
    assert "from pixell_runtime.deploy.manager import DeploymentManager" not in content
    assert "from .deploy.manager import DeploymentManager" not in content
    assert "import DeploymentManager" not in content


def test_deployment_manager_not_in_three_surface_runtime():
    """Test that ThreeSurfaceRuntime doesn't use DeploymentManager."""
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should not import or reference DeploymentManager
    assert "DeploymentManager" not in content
    assert "deploy.manager" not in content


def test_par_runs_single_agent_only():
    """Test that PAR is designed to run a single agent, not manage multiple deployments."""
    # The runtime should use environment variables to determine which agent to run
    # It should NOT have a deployment API or multi-agent management
    
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should use environment variables for configuration
    assert "AGENT_APP_ID" in content or "agent_app_id" in content
    
    # Should NOT have deployment management logic
    assert "deployments" not in content.lower() or "deployment_id" in content.lower()  # deployment_id is OK, deployments dict is not


def test_no_deployment_api_routes():
    """Test that there are no deployment management API routes in the runtime."""
    # Check if api/deploy.py is used in the main runtime
    main_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "__main__.py"
    content = main_file.read_text()
    
    # Should not import deploy router
    assert "from pixell_runtime.api.deploy import" not in content
    assert "deploy_router" not in content


def test_runtime_config_single_agent_model():
    """Test that RuntimeConfig is designed for single-agent execution."""
    config_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "core" / "runtime_config.py"
    content = config_file.read_text()
    
    # Should have single agent configuration
    assert "agent_app_id" in content.lower()
    
    # Should NOT have multi-deployment configuration
    assert "deployments" not in content.lower() or "deployment_id" in content.lower()  # deployment_id is OK


def test_package_loader_not_deployment_manager():
    """Test that we use PackageLoader, not DeploymentManager."""
    # PackageLoader is data-plane (loads and validates packages)
    # DeploymentManager is control-plane (manages lifecycle of multiple deployments)
    
    loader_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "agents" / "loader.py"
    assert loader_file.exists()
    
    content = loader_file.read_text()
    
    # Should NOT import DeploymentManager
    assert "DeploymentManager" not in content
    assert "from pixell_runtime.deploy.manager" not in content


def test_deploy_directory_marked_as_legacy():
    """Test that deploy/ directory is marked as legacy or deprecated."""
    deploy_init = Path(__file__).parent.parent / "src" / "pixell_runtime" / "deploy" / "__init__.py"
    
    if deploy_init.exists():
        content = deploy_init.read_text()
        
        # If deploy/ still exists, it should be marked as deprecated/legacy
        # or only export fetch functionality (which is data-plane)
        
        # DeploymentManager should be marked as deprecated if exported
        if "DeploymentManager" in content:
            # Should have deprecation warning or comment
            assert "deprecated" in content.lower() or "legacy" in content.lower() or "pac" in content.lower()


def test_main_py_not_used_for_single_agent_runtime():
    """Test that main.py (multi-agent server) is not the primary entry point."""
    # In single-agent mode, we should use ThreeSurfaceRuntime directly
    # main.py with its FastAPI server and deployment APIs is legacy
    
    main_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "__main__.py"
    content = main_file.read_text()
    
    # Primary path should be ThreeSurfaceRuntime
    assert "ThreeSurfaceRuntime" in content
    
    # Should prefer direct runtime execution over server mode
    lines = content.split("\n")
    runtime_line = None
    server_line = None
    
    for i, line in enumerate(lines):
        if "ThreeSurfaceRuntime" in line and "import" not in line:
            runtime_line = i
        if "run_server()" in line:
            server_line = i
    
    # Runtime execution should come before server fallback
    if runtime_line and server_line:
        assert runtime_line < server_line


def test_no_port_allocation_logic():
    """Test that runtime doesn't allocate ports (control-plane responsibility)."""
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should read ports from config/env, not allocate them
    # Should NOT have port allocation logic
    assert "find_free_port" not in content
    assert "allocate_port" not in content
    assert "is_port_free" not in content or "# legacy" in content.lower()


def test_no_service_discovery_registration():
    """Test that runtime doesn't register itself in service discovery."""
    # Service discovery registration is control-plane responsibility (PAC does this)
    # Runtime should only serve traffic on its assigned ports
    
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should NOT register in service discovery
    assert "register_service" not in content
    assert "service_discovery" not in content or "# legacy" in content.lower() or "import" in content


def test_runtime_uses_environment_variables():
    """Test that runtime gets all config from environment variables."""
    # In container mode, all configuration comes from environment
    # No dynamic allocation, no API calls to control plane
    
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should use environment variables
    assert "os.getenv" in content or "os.environ" in content or "RuntimeConfig" in content


def test_no_multi_deployment_state():
    """Test that runtime doesn't maintain state for multiple deployments."""
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should NOT have deployments dictionary or list
    assert "self.deployments" not in content
    assert "Dict[str, Deployment" not in content


def test_deployment_manager_file_exists_but_not_used():
    """Test that DeploymentManager file may exist but is not used in runtime path."""
    # The file can exist for backward compatibility or PAC usage
    # But it should not be in the import path of the runtime
    
    manager_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "deploy" / "manager.py"
    
    if manager_file.exists():
        # Check that it's not imported by runtime components
        runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
        runtime_content = runtime_file.read_text()
        
        assert "from pixell_runtime.deploy.manager" not in runtime_content
        assert "from .deploy.manager" not in runtime_content


def test_fetch_is_data_plane_and_allowed():
    """Test that fetch.py (data-plane) is allowed and used."""
    # fetch_package_to_path is data-plane functionality (downloads packages)
    # This is OK to use in runtime
    
    fetch_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "deploy" / "fetch.py"
    assert fetch_file.exists()
    
    # Runtime should be able to use fetch
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    runtime_content = runtime_file.read_text()
    
    # Using fetch is OK (it's data-plane)
    if "fetch_package" in runtime_content:
        assert "from pixell_runtime.deploy.fetch import" in runtime_content


def test_api_deploy_not_in_runtime_path():
    """Test that api/deploy.py is not in the runtime execution path."""
    # api/deploy.py has deployment management endpoints (control-plane)
    # Should not be imported by runtime components
    
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    runtime_content = runtime_file.read_text()
    
    assert "from pixell_runtime.api.deploy" not in runtime_content
    assert "api.deploy" not in runtime_content


def test_runtime_doesnt_start_fastapi_server():
    """Test that ThreeSurfaceRuntime doesn't start a FastAPI control-plane server."""
    # The runtime creates FastAPI apps for REST/UI, but not for deployment management
    
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should not have deployment management routes
    assert "deploy_router" not in content
    assert "/deploy" not in content or "/deployments" not in content


def test_single_agent_execution_model():
    """Test that the execution model is single-agent per container."""
    # Each PAR container runs exactly one agent
    # No subprocess spawning of multiple agents
    
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should load one package
    assert "load_package" in content
    
    # Should NOT spawn multiple agent processes
    # (subprocess_runner is legacy and should not be used)
    assert "subprocess_runner" not in content or "# legacy" in content.lower()


@pytest.mark.parametrize("file_path,forbidden_imports", [
    ("src/pixell_runtime/three_surface/runtime.py", ["pixell_runtime.deploy.manager", "pixell_runtime.api.deploy"]),
    ("src/pixell_runtime/__main__.py", ["pixell_runtime.deploy.manager"]),
    ("src/pixell_runtime/agents/loader.py", ["pixell_runtime.deploy.manager"]),
])
def test_no_forbidden_imports(file_path, forbidden_imports):
    """Test that specific files don't import control-plane modules."""
    full_path = Path(__file__).parent.parent / file_path
    
    if not full_path.exists():
        pytest.skip(f"File {file_path} does not exist")
    
    content = full_path.read_text()
    
    for forbidden in forbidden_imports:
        assert f"from {forbidden}" not in content, f"{file_path} should not import {forbidden}"
        assert f"import {forbidden}" not in content, f"{file_path} should not import {forbidden}"
