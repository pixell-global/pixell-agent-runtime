"""
Tests to verify that SubprocessAgentRunner is NOT used in the runtime.

In the new architecture, each agent runs in its own container/ECS task.
The subprocess runner pattern is legacy from when PAR spawned multiple agents
as subprocesses. This is no longer the execution model.
"""

from pathlib import Path

import pytest


def test_subprocess_runner_not_imported_in_runtime():
    """Test that SubprocessAgentRunner is not imported in ThreeSurfaceRuntime."""
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should not import SubprocessAgentRunner
    assert "from pixell_runtime.three_surface.subprocess_runner import" not in content
    assert "from .subprocess_runner import" not in content
    assert "SubprocessAgentRunner" not in content


def test_subprocess_runner_not_imported_in_main():
    """Test that SubprocessAgentRunner is not imported in __main__.py."""
    main_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "__main__.py"
    content = main_file.read_text()
    
    # Should not import SubprocessAgentRunner
    assert "SubprocessAgentRunner" not in content
    assert "subprocess_runner" not in content


def test_subprocess_runner_not_in_package_loader():
    """Test that PackageLoader doesn't use SubprocessAgentRunner."""
    loader_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "agents" / "loader.py"
    content = loader_file.read_text()
    
    # Should not import or use SubprocessAgentRunner
    assert "SubprocessAgentRunner" not in content
    assert "subprocess_runner" not in content


def test_runtime_uses_direct_execution_not_subprocess():
    """Test that runtime executes agents directly, not via subprocess."""
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should start servers directly
    assert "start_grpc_server" in content or "grpc_server" in content
    assert "uvicorn" in content or "create_rest_app" in content
    
    # Should NOT spawn subprocesses
    assert "subprocess.Popen" not in content
    assert "subprocess.run" not in content or "# test" in content.lower()


def test_subprocess_runner_file_marked_as_deprecated():
    """Test that subprocess_runner.py is marked as deprecated."""
    runner_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "subprocess_runner.py"
    
    if runner_file.exists():
        content = runner_file.read_text()
        
        # Should have deprecation warning
        assert "deprecated" in content.lower() or "legacy" in content.lower()


def test_deployment_manager_doesnt_use_subprocess_runner():
    """Test that DeploymentManager (legacy) marks subprocess_runner as deprecated."""
    manager_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "deploy" / "manager.py"
    
    if manager_file.exists():
        content = manager_file.read_text()
        
        # If it references subprocess_runner, check that it's marked as deprecated
        if "subprocess_runner" in content:
            # The file itself should be marked as deprecated
            first_50_lines = "\n".join(content.split("\n")[:50])
            assert "DEPRECATED" in first_50_lines or "deprecated" in first_50_lines.lower(), \
                "DeploymentManager should be marked as DEPRECATED"
            
            # subprocess_runner field should have DEPRECATED comment
            lines = content.split("\n")
            for i, line in enumerate(lines):
                if "subprocess_runner:" in line and "Optional" in line:
                    # Check the comment on this line
                    assert "DEPRECATED" in line or "not used" in line.lower(), \
                        "subprocess_runner field should be marked as DEPRECATED"


def test_container_execution_model():
    """Test that the execution model is container-based, not subprocess-based."""
    # In container model:
    # - Each agent runs in its own container
    # - PAR is the entrypoint of the container
    # - No subprocess spawning needed
    
    main_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "__main__.py"
    content = main_file.read_text()
    
    # Should use ThreeSurfaceRuntime directly
    assert "ThreeSurfaceRuntime" in content
    
    # Should not spawn subprocesses for agents
    assert "Popen" not in content


def test_single_agent_per_process():
    """Test that PAR runs a single agent per process."""
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should load one package
    assert "load_package" in content
    
    # Should NOT have logic for multiple agent processes
    assert "for agent in" not in content.lower() or "# test" in content.lower()
    assert "spawn" not in content.lower() or "# test" in content.lower()


def test_no_process_management_in_runtime():
    """Test that runtime doesn't manage child processes."""
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should NOT have process management
    assert "process.wait()" not in content
    assert "process.kill()" not in content
    assert "process.terminate()" not in content


def test_venv_isolation_not_via_subprocess():
    """Test that venv isolation doesn't require subprocess spawning."""
    # In the new model, venv isolation is achieved by:
    # 1. Installing packages into venv during package loading
    # 2. Importing from venv in the same process
    # NOT by spawning a subprocess with venv python
    
    loader_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "agents" / "loader.py"
    content = loader_file.read_text()
    
    # Should create venvs
    assert "venv" in content.lower()
    
    # Should NOT spawn subprocess with venv python
    assert "subprocess.Popen" not in content


def test_agent_package_path_triggers_direct_runtime():
    """Test that AGENT_PACKAGE_PATH triggers direct runtime, not subprocess."""
    main_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "__main__.py"
    content = main_file.read_text()
    
    # When AGENT_PACKAGE_PATH is set, should run ThreeSurfaceRuntime directly
    lines = content.split("\n")
    
    found_agent_package_path = False
    found_three_surface = False
    found_subprocess = False
    
    for i, line in enumerate(lines):
        if "AGENT_PACKAGE_PATH" in line:
            found_agent_package_path = True
            # Check next few lines for ThreeSurfaceRuntime
            for j in range(i, min(i + 10, len(lines))):
                if "ThreeSurfaceRuntime" in lines[j]:
                    found_three_surface = True
                if "subprocess" in lines[j].lower() and "Popen" in lines[j]:
                    found_subprocess = True
    
    if found_agent_package_path:
        assert found_three_surface, "AGENT_PACKAGE_PATH should trigger ThreeSurfaceRuntime"
        assert not found_subprocess, "AGENT_PACKAGE_PATH should NOT spawn subprocess"


def test_no_log_forwarding_from_subprocess():
    """Test that runtime doesn't forward logs from subprocess."""
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should NOT have subprocess log forwarding
    assert "_forward_logs" not in content
    assert "stdout.readline" not in content
    assert "stderr.readline" not in content


def test_no_subprocess_wait_pattern():
    """Test that runtime doesn't wait for subprocess completion."""
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Should NOT wait for subprocess
    assert "process.wait(" not in content
    assert "await loop.run_in_executor(None, self.process.wait)" not in content


def test_subprocess_runner_only_in_legacy_code():
    """Test that SubprocessAgentRunner only exists in legacy/deprecated files."""
    # Check all Python files in src
    src_dir = Path(__file__).parent.parent / "src" / "pixell_runtime"
    
    files_with_subprocess_runner = []
    
    for py_file in src_dir.rglob("*.py"):
        # Skip __pycache__
        if "__pycache__" in str(py_file):
            continue
        
        content = py_file.read_text()
        
        if "SubprocessAgentRunner" in content:
            # Should only be in:
            # 1. subprocess_runner.py itself
            # 2. deploy/manager.py (legacy)
            # 3. __init__.py files (imports)
            
            relative_path = py_file.relative_to(src_dir)
            
            if relative_path.name == "subprocess_runner.py":
                # OK - the file itself
                continue
            elif "deploy/manager.py" in str(relative_path):
                # OK - legacy DeploymentManager
                continue
            elif relative_path.name == "__init__.py":
                # Check if it's just importing
                if "from" in content and "import SubprocessAgentRunner" in content:
                    # OK - just importing
                    continue
            
            files_with_subprocess_runner.append(str(relative_path))
    
    assert len(files_with_subprocess_runner) == 0, \
        f"SubprocessAgentRunner found in unexpected files: {files_with_subprocess_runner}"


@pytest.mark.parametrize("file_path", [
    "src/pixell_runtime/three_surface/runtime.py",
    "src/pixell_runtime/__main__.py",
    "src/pixell_runtime/agents/loader.py",
])
def test_no_subprocess_imports(file_path):
    """Test that core runtime files don't import subprocess module for agent spawning."""
    full_path = Path(__file__).parent.parent / file_path
    
    if not full_path.exists():
        pytest.skip(f"File {file_path} does not exist")
    
    content = full_path.read_text()
    
    # If subprocess is imported, it should only be for specific purposes
    # (like running pip install, not spawning agent processes)
    if "import subprocess" in content:
        # Check that it's not used for agent spawning
        assert "subprocess.Popen" not in content or "pip" in content, \
            f"{file_path} uses subprocess.Popen (should not spawn agent processes)"


def test_container_model_documented():
    """Test that the container execution model is documented."""
    # Check that subprocess_runner.py has clear deprecation notice
    runner_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "subprocess_runner.py"
    
    if runner_file.exists():
        content = runner_file.read_text()
        
        # Should explain the new model
        first_100_lines = "\n".join(content.split("\n")[:20])
        
        assert (
            "deprecated" in first_100_lines.lower() or
            "legacy" in first_100_lines.lower() or
            "container" in first_100_lines.lower()
        ), "subprocess_runner.py should document deprecation and new container model"


def test_runtime_init_doesnt_create_subprocess_runner():
    """Test that ThreeSurfaceRuntime.__init__ doesn't create SubprocessAgentRunner."""
    runtime_file = Path(__file__).parent.parent / "src" / "pixell_runtime" / "three_surface" / "runtime.py"
    content = runtime_file.read_text()
    
    # Find __init__ method
    lines = content.split("\n")
    in_init = False
    init_content = []
    
    for line in lines:
        if "def __init__" in line:
            in_init = True
        elif in_init and line.strip() and not line.strip().startswith("#") and line[0] not in (" ", "\t"):
            # End of __init__
            break
        
        if in_init:
            init_content.append(line)
    
    init_text = "\n".join(init_content)
    
    # Should NOT create SubprocessAgentRunner
    assert "SubprocessAgentRunner" not in init_text
    assert "subprocess_runner" not in init_text.lower()
