# PAR Implementation: Install Agent Package in Isolated Venv

## Background

Currently, when PAR creates an isolated venv for an agent and installs dependencies, it doesn't install the agent's own code as a package. This causes import errors for root-level modules like `core/`, `app/`.

## Objective

After creating the venv and installing `requirements.txt`, install the agent itself as an editable Python package using `pip install -e .` so all agent code becomes importable.

## Current State

### Existing Package Loading Flow
File: `/Users/syum/dev/pixell-agent-runtime/src/pixell_runtime/agents/loader.py`

**Current process:**
1. Extract APKG to `/tmp/pixell-runtime/packages/{app_id}@{version}/`
2. Create isolated venv at `/tmp/pixell-runtime/venvs/{app_id}_{hash}/`
3. Install dependencies: `venv/bin/pip install -r requirements.txt`
4. Store package metadata (path, venv_path, manifest)
5. Return `AgentPackage` object

**Current code (simplified):**
```python
def load_package(self, apkg_path: Path, agent_app_id: Optional[str] = None) -> AgentPackage:
    # 1. Extract APKG
    package_path = self._extract_package(apkg_path)

    # 2. Load manifest
    manifest = self._load_manifest(package_path)

    # 3. Create/reuse venv
    venv_path = self._ensure_venv(package_path, manifest, agent_app_id)

    # 4. Install dependencies
    if venv_path:
        self._install_dependencies(package_path, venv_path)  # ← MODIFY THIS

    # 5. Return package
    return AgentPackage(...)
```

### Problem
After step 4, dependencies are installed but the agent code itself is NOT installed. Python cannot import `core`, `app`, etc.

## Implementation Requirements

### 1. Modify `_install_dependencies()` Method

**Location:** `/Users/syum/dev/pixell-agent-runtime/src/pixell_runtime/agents/loader.py`

**Current implementation:**
```python
def _install_dependencies(self, package_path: Path, venv_path: str):
    """Install package dependencies in venv."""
    req_file = package_path / "requirements.txt"
    if not req_file.exists():
        logger.debug("No requirements.txt found", package_path=str(package_path))
        return

    pip_path = Path(venv_path) / "bin" / "pip"
    logger.info("Installing dependencies", venv=Path(venv_path).name)

    try:
        subprocess.run(
            [str(pip_path), "install", "-r", str(req_file)],
            check=True,
            capture_output=True,
            text=True,
            timeout=300
        )
        logger.info("Dependencies installed successfully")
    except subprocess.TimeoutExpired:
        logger.error("Dependency installation timed out")
        raise
    except subprocess.CalledProcessError as e:
        logger.error("Dependency installation failed", stderr=e.stderr)
        raise
```

**NEW implementation:**
```python
def _install_dependencies(self, package_path: Path, venv_path: str):
    """Install package dependencies and the agent package itself in venv."""
    pip_path = Path(venv_path) / "bin" / "pip"

    # Step 1: Install external dependencies from requirements.txt
    req_file = package_path / "requirements.txt"
    if req_file.exists():
        logger.info("Installing dependencies from requirements.txt", venv=Path(venv_path).name)
        try:
            subprocess.run(
                [str(pip_path), "install", "-r", str(req_file)],
                check=True,
                capture_output=True,
                text=True,
                timeout=300
            )
            logger.info("External dependencies installed successfully")
        except subprocess.TimeoutExpired:
            logger.error("Dependency installation timed out")
            raise
        except subprocess.CalledProcessError as e:
            logger.error("Dependency installation failed", stderr=e.stderr)
            raise
    else:
        logger.debug("No requirements.txt found", package_path=str(package_path))

    # Step 2: Install the agent package itself (if setup.py exists)
    setup_file = package_path / "setup.py"
    if setup_file.exists():
        logger.info("Installing agent package in editable mode",
                   package_path=str(package_path),
                   venv=Path(venv_path).name)
        try:
            subprocess.run(
                [str(pip_path), "install", "-e", str(package_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120
            )
            logger.info("Agent package installed successfully")
        except subprocess.TimeoutExpired:
            logger.error("Agent package installation timed out")
            raise
        except subprocess.CalledProcessError as e:
            logger.error("Agent package installation failed",
                        stderr=e.stderr,
                        stdout=e.stdout)
            raise
    else:
        logger.info("No setup.py found - agent package not installed as Python package",
                   package_path=str(package_path))
        # This is OK for backward compatibility with old APKGs
```

### 2. Enhanced Logging

**Add detailed logging for debugging:**

```python
# Before installing agent package
logger.info("Checking for agent package metadata",
           setup_py_exists=setup_file.exists(),
           package_path=str(package_path))

# After successful install
logger.info("Agent package installed successfully",
           installed_packages=self._list_installed_packages(venv_path))

# Helper method to list installed packages (optional, for debugging)
def _list_installed_packages(self, venv_path: str) -> list[str]:
    """List packages installed in venv (for debugging)."""
    pip_path = Path(venv_path) / "bin" / "pip"
    try:
        result = subprocess.run(
            [str(pip_path), "list", "--format=freeze"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout.strip().split('\n')
    except Exception as e:
        logger.debug("Could not list packages", error=str(e))
        return []
```

### 3. Error Handling

**Scenarios to handle:**

#### Scenario A: setup.py doesn't exist (old APKGs)
**Behavior:** Log info message, continue without error
**Reason:** Backward compatibility

#### Scenario B: setup.py exists but is invalid
**Behavior:** Log error, raise exception
**Reason:** This is a build-time error that should be fixed

#### Scenario C: Agent package install times out
**Behavior:** Raise TimeoutExpired, abort deployment
**Reason:** Something is seriously wrong

#### Scenario D: Dependencies install fails but agent package succeeds
**Behavior:** Deployment still fails (dependencies are required)

**Implementation:**
```python
def _install_dependencies(self, package_path: Path, venv_path: str):
    """Install package dependencies and the agent package itself in venv."""
    pip_path = Path(venv_path) / "bin" / "pip"
    errors = []

    # Step 1: Install external dependencies
    req_file = package_path / "requirements.txt"
    if req_file.exists():
        logger.info("Installing dependencies from requirements.txt")
        try:
            result = subprocess.run(
                [str(pip_path), "install", "-r", str(req_file)],
                check=True,
                capture_output=True,
                text=True,
                timeout=300
            )
            logger.info("External dependencies installed successfully",
                       packages_count=len([l for l in result.stdout.split('\n') if 'Successfully installed' in l]))
        except subprocess.TimeoutExpired as e:
            error_msg = "Dependency installation timed out after 300s"
            logger.error(error_msg)
            errors.append(error_msg)
        except subprocess.CalledProcessError as e:
            error_msg = f"Dependency installation failed: {e.stderr}"
            logger.error("Dependency installation failed",
                        stderr=e.stderr,
                        stdout=e.stdout)
            errors.append(error_msg)

    # Step 2: Install agent package
    setup_file = package_path / "setup.py"
    if setup_file.exists():
        logger.info("Installing agent package in editable mode")
        try:
            result = subprocess.run(
                [str(pip_path), "install", "-e", str(package_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120
            )
            logger.info("Agent package installed successfully")
        except subprocess.TimeoutExpired as e:
            error_msg = "Agent package installation timed out after 120s"
            logger.error(error_msg)
            errors.append(error_msg)
        except subprocess.CalledProcessError as e:
            error_msg = f"Agent package installation failed: {e.stderr}"
            logger.error("Agent package installation failed",
                        stderr=e.stderr,
                        stdout=e.stdout,
                        package_path=str(package_path))
            errors.append(error_msg)
    else:
        logger.info("No setup.py found - skipping agent package installation",
                   note="Agent may have import issues if it uses root-level packages")

    # Raise if any errors occurred
    if errors:
        raise RuntimeError(f"Package installation failed: {'; '.join(errors)}")
```

### 4. Backward Compatibility

**For old APKGs without setup.py:**

The code gracefully handles this by:
1. Checking if `setup.py` exists
2. If not, logging an info message and continuing
3. Agent runs with old import behavior (may still have issues, but doesn't crash PAR)

**Migration path:**
- Old agents continue to work (with potential import issues)
- Rebuild with new PAK → get setup.py → imports work
- No breaking changes to PAR API

### 5. Validation After Installation

**Optional enhancement:** Verify agent package is importable

```python
def _verify_agent_package(self, package_path: Path, venv_path: str, manifest) -> bool:
    """Verify the agent package is properly installed and importable."""
    python_path = Path(venv_path) / "bin" / "python"

    # Try to import the entrypoint module
    if hasattr(manifest, 'entrypoint') and manifest.entrypoint:
        module_name, _ = manifest.entrypoint.split(':')

        try:
            result = subprocess.run(
                [str(python_path), "-c", f"import {module_name}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10
            )
            logger.info("Agent package verification successful", module=module_name)
            return True
        except subprocess.CalledProcessError as e:
            logger.error("Agent package verification failed",
                        module=module_name,
                        stderr=e.stderr)
            return False
        except subprocess.TimeoutExpired:
            logger.error("Agent package verification timed out")
            return False

    return True  # Skip verification if no entrypoint
```

**Call in `load_package()` after `_install_dependencies()`:**
```python
# Install dependencies and agent package
if venv_path:
    self._install_dependencies(package_path, venv_path)

    # Optional: Verify installation
    if not self._verify_agent_package(package_path, venv_path, manifest):
        logger.warning("Agent package verification failed - imports may not work")
```

## Integration Points

### Where This Fits in Deployment Flow

```
User runs: pixell deploy -f agent.apkg

  ↓ PAC receives request
  ↓ PAC sends to PAR: POST /deploy

  ↓ PAR DeploymentManager._execute_deployment()

    1. Download APKG to cache
    2. Load package via PackageLoader.load_package() ← CHANGES HERE
        a. Extract APKG to /tmp/pixell-runtime/packages/
        b. Load agent.yaml manifest
        c. Create/reuse venv
        d. Install requirements.txt ← CURRENT
        e. Install agent package (pip install -e .) ← NEW
        f. (Optional) Verify imports work ← NEW
        g. Return AgentPackage object
    3. Allocate ports
    4. Start subprocess with SubprocessAgentRunner
        - Subprocess runs: venv/bin/python -m pixell_runtime
        - AGENT_PACKAGE_PATH env var set
        - ThreeSurfaceRuntime starts
        - Loads agent via adapter_factory ← No longer needs sys.path hacks!
        - Agent imports now "just work" ✅

  ↓ Agent healthy, deployment complete
```

### Interaction with adapter_factory.py

**Current hack (can be removed later):**
```python
# In adapter_factory.py
sys.path.insert(0, str(package_root))  # Add package root
sys.path.insert(0, str(src_path))      # Add src/
```

**After this implementation:**
These `sys.path` manipulations become unnecessary because the agent is properly installed. However, keep them for backward compatibility with old APKGs.

**Future optimization:**
```python
# Only add sys.path for packages without setup.py
setup_file = Path(package.path) / "setup.py"
if not setup_file.exists():
    # Old-style agent, use sys.path hacks
    sys.path.insert(0, str(package_root))
    sys.path.insert(0, str(src_path))
else:
    # New-style agent, properly installed
    logger.debug("Agent package installed, skipping sys.path manipulation")
```

## Testing Requirements

### Unit Tests
File: `tests/test_loader.py`

**Test cases:**

1. **test_install_dependencies_with_setup_py**
   - Mock APKG with setup.py
   - Verify `pip install -e .` is called
   - Verify success logging

2. **test_install_dependencies_without_setup_py**
   - Mock APKG without setup.py
   - Verify only requirements.txt installed
   - Verify info logging (not error)

3. **test_install_dependencies_setup_py_fails**
   - Mock APKG with invalid setup.py
   - Verify exception raised
   - Verify error logged

4. **test_install_dependencies_timeout**
   - Mock slow pip install
   - Verify TimeoutExpired raised

5. **test_backward_compatibility**
   - Load old APKG (no setup.py)
   - Verify still works

**Example test:**
```python
def test_install_agent_package_with_setup_py(tmp_path, mocker):
    """Test that agent package is installed when setup.py exists."""
    # Create mock APKG
    package_path = tmp_path / "agent"
    package_path.mkdir()
    (package_path / "setup.py").write_text("from setuptools import setup\nsetup(name='test')")
    (package_path / "requirements.txt").write_text("requests==2.28.0")

    # Create mock venv
    venv_path = tmp_path / "venv"
    (venv_path / "bin").mkdir(parents=True)

    # Mock subprocess.run
    mock_run = mocker.patch('subprocess.run')
    mock_run.return_value = mocker.Mock(stdout="Success", stderr="")

    # Test
    loader = PackageLoader(tmp_path)
    loader._install_dependencies(package_path, str(venv_path))

    # Verify pip install -r requirements.txt was called
    assert mock_run.call_count == 2

    # Verify pip install -e . was called
    editable_call = [call for call in mock_run.call_args_list if '-e' in str(call)]
    assert len(editable_call) == 1
    assert str(package_path) in str(editable_call[0])
```

### Integration Tests
File: `tests/test_package_loading_integration.py`

**Test case:**
1. Create real APKG with setup.py (using PAK)
2. Load it with PackageLoader
3. Verify venv has agent installed
4. Verify can import agent modules from venv Python

**Example:**
```python
def test_load_package_with_setup_py_integration(tmp_path):
    """Integration test: Load APKG with setup.py and verify imports work."""
    # Build real APKG
    agent_dir = create_test_agent_with_core_module(tmp_path / "agent")
    apkg_path = build_apkg(agent_dir, tmp_path / "build")

    # Load package
    loader = PackageLoader(tmp_path / "packages")
    package = loader.load_package(apkg_path, agent_app_id="test-app")

    # Verify setup.py was installed
    venv_python = Path(package.venv_path) / "bin" / "python"

    # Try importing core module from venv
    result = subprocess.run(
        [str(venv_python), "-c", "import core; print('SUCCESS')"],
        cwd=package.path,
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "SUCCESS" in result.stdout
```

## Logging Output Examples

### Successful Installation
```
[INFO] Loading agent package: /tmp/packages/vivid-commenter@1.0.1.apkg
[INFO] Extracting package to: /tmp/pixell-runtime/packages/4906eeb7-9959-414e-84c6-f2445822ebe4@1.0.1
[INFO] Creating venv: /tmp/pixell-runtime/venvs/4906eeb7_f2ca88b
[INFO] Installing dependencies from requirements.txt (venv=4906eeb7_f2ca88b)
[INFO] External dependencies installed successfully (packages_count=15)
[INFO] Installing agent package in editable mode (package_path=/tmp/.../vivid-commenter, venv=4906eeb7_f2ca88b)
[INFO] Agent package installed successfully
[INFO] Package loaded successfully (package_id=vivid-commenter@1.0.1, venv=4906eeb7_f2ca88b)
```

### Old APKG (No setup.py)
```
[INFO] Loading agent package: /tmp/packages/old-agent@1.0.0.apkg
[INFO] Installing dependencies from requirements.txt
[INFO] External dependencies installed successfully
[INFO] No setup.py found - skipping agent package installation (note=Agent may have import issues if it uses root-level packages)
[INFO] Package loaded successfully (package_id=old-agent@1.0.0)
```

### Installation Failure
```
[ERROR] Agent package installation failed (stderr=error: invalid command 'bdist_wheel', stdout=..., package_path=/tmp/.../agent)
[ERROR] Package installation failed: Agent package installation failed: error: invalid command 'bdist_wheel'
```

## Success Criteria

1. ✅ After venv creation, agent package is installed with `pip install -e .`
2. ✅ Agent modules (core/, app/) are importable from venv Python
3. ✅ Old APKGs without setup.py continue to work (backward compatible)
4. ✅ Errors are properly logged and raise exceptions
5. ✅ No more `sys.path` hacks needed in adapter_factory.py (optional cleanup)
6. ✅ Integration tests pass with real APKGs

## Files to Modify

1. `/Users/syum/dev/pixell-agent-runtime/src/pixell_runtime/agents/loader.py`
   - Modify `_install_dependencies()` method
   - Add optional `_verify_agent_package()` method
   - Update logging

2. `/Users/syum/dev/pixell-agent-runtime/tests/test_loader.py`
   - Add unit tests for agent package installation
   - Add tests for error cases

3. (Optional) `/Users/syum/dev/pixell-agent-runtime/src/pixell_runtime/agents/adapter_factory.py`
   - Add conditional sys.path manipulation (only for old APKGs)

## Dependencies

**No new external dependencies required.**

Existing dependencies used:
- `subprocess` (already imported)
- `pathlib.Path` (already imported)
- `structlog` (already imported)

## Implementation Order

1. **Phase 1:** Modify `_install_dependencies()` to install agent package
2. **Phase 2:** Add enhanced logging
3. **Phase 3:** Add error handling for all scenarios
4. **Phase 4:** Add unit tests
5. **Phase 5:** Integration testing with real APKGs
6. **Phase 6:** (Optional) Add verification step
7. **Phase 7:** (Optional) Clean up sys.path hacks in adapter_factory.py

## Performance Considerations

**Impact on deployment time:**
- `pip install -e .` is very fast (<5 seconds typically)
- Only installs symbolic links, no file copying
- Minimal impact on overall deployment time

**Venv size:**
- Editable install doesn't copy files
- Venv size unchanged (just metadata)

## Security Considerations

**setup.py execution:**
- setup.py runs in isolated venv (already sandboxed)
- Same security boundary as requirements.txt
- If malicious APKG uploaded, already compromised

**No new security risks introduced.**

## Migration Guide

### For Existing Deployments

**Automatic migration:**
1. Redeploy agent with new PAK (includes setup.py)
2. PAR automatically installs agent package on next deployment
3. Imports start working immediately

**No manual intervention required.**

### For Agent Developers

**Before:**
```python
# Agent had import issues
from core.langchain_util import model_default  # ❌ ModuleNotFoundError
```

**After:**
```python
# Imports just work
from core.langchain_util import model_default  # ✅ Works!
```

**No code changes needed** - just rebuild with new PAK.

## Rollback Plan

If issues discovered:

1. **Code rollback:** Revert `_install_dependencies()` changes
2. **Behavior:** Falls back to old behavior (no agent package install)
3. **Impact:** New APKGs with setup.py will have unused file, but no breakage
4. **Recovery:** Old deployments unaffected

## Notes

- Keep implementation simple and robust
- Preserve backward compatibility at all costs
- Log extensively for debugging
- Consider adding metrics (installation time, success rate)
- Future: Could parallelize dependency + agent install for speed
