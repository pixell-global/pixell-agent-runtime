# PAR Implementation: Install requirements.txt After Agent Package

## Background

Currently, PAR creates a venv for each agent deployment and runs `pip install -e .` to install the agent package in editable mode. This installs the package structure (making imports like `from core.langchain_util import ...` work) but **does NOT install dependencies** listed in requirements.txt.

This causes runtime failures when agent code tries to import third-party libraries:
```python
# core/langchain_util.py
from langchain_openai import ChatOpenAI  # ❌ ModuleNotFoundError
```

Even though `langchain-openai==0.3.28` is in requirements.txt, it's never installed into the venv.

## Problem Statement

**Current state (loader.py:431-483):**
1. Create venv for agent
2. Install setuptools and wheel
3. Run `pip install -e .` (installs package structure only)
4. ✅ Package structure available (core/, app/ importable)
5. ❌ Dependencies NOT installed (langchain_openai, requests, etc.)
6. ❌ Agent fails at runtime with import errors

**Desired state:**
1. Create venv for agent
2. Install setuptools and wheel
3. Run `pip install -e .` (installs package structure)
4. **Run `pip install -r requirements.txt` (installs dependencies)**
5. ✅ Package structure available
6. ✅ Dependencies installed
7. ✅ Agent runs successfully

## Objective

Add requirements.txt installation step after agent package installation in loader.py, with proper error handling, logging, timeout management, and CodeArtifact support.

## Implementation Requirements

### 1. Add Requirements Installation Method

**Location:** New method in `PackageLoader` class (loader.py)

**Purpose:** Install dependencies from requirements.txt into agent venv

**Implementation:**

```python
def _install_requirements(
    self,
    venv_path: Path,
    package_path: Path,
    venv_name: str
) -> bool:
    """Install dependencies from requirements.txt into agent venv.

    Args:
        venv_path: Path to the virtual environment
        package_path: Path to the agent package directory
        venv_name: Name of venv for logging

    Returns:
        True if requirements installed successfully or no requirements file,
        False if installation failed

    Raises:
        PackageLoadError: If requirements installation fails critically
    """
    req_file = package_path / "requirements.txt"

    # If no requirements.txt, nothing to do
    if not req_file.exists():
        logger.info("No requirements.txt found, skipping dependency installation",
                   venv=venv_name,
                   package_path=str(package_path))
        return True

    # Check if requirements.txt is empty
    try:
        with open(req_file, 'r') as f:
            content = f.read().strip()
            # Filter out comments and empty lines
            lines = [line.strip() for line in content.split('\n')
                    if line.strip() and not line.strip().startswith('#')]

            if not lines:
                logger.info("requirements.txt is empty, skipping dependency installation",
                           venv=venv_name)
                return True

            logger.info("Found requirements.txt with dependencies",
                       venv=venv_name,
                       line_count=len(lines))
    except Exception as e:
        logger.warning("Failed to read requirements.txt, attempting install anyway",
                      venv=venv_name,
                      error=str(e))

    # Determine pip path
    if sys.platform == "win32":
        pip_path = venv_path / "Scripts" / "pip"
    else:
        pip_path = venv_path / "bin" / "pip"

    # Build pip install command
    pip_index_url = self._get_codeartifact_pip_index()
    install_cmd = [str(pip_path), "install", "-r", str(req_file)]

    if pip_index_url:
        install_cmd.extend(["--index-url", pip_index_url])
        logger.debug("Using CodeArtifact index for requirements installation",
                    venv=venv_name)

    logger.info("Installing dependencies from requirements.txt",
               venv=venv_name,
               requirements_file=str(req_file),
               command=" ".join(install_cmd))

    try:
        result = subprocess.run(
            install_cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minutes max for dependency installation
        )

        if result.returncode != 0:
            logger.error("Requirements installation failed",
                        venv=venv_name,
                        returncode=result.returncode,
                        stderr=result.stderr,
                        stdout=result.stdout)

            # Check for common errors
            if "Could not find a version" in result.stderr:
                raise PackageLoadError(
                    f"Dependency not found in PyPI/CodeArtifact. "
                    f"Check requirements.txt for typos or unavailable packages: {result.stderr}"
                )
            elif "THESE PACKAGES DO NOT MATCH THE HASHES" in result.stderr:
                raise PackageLoadError(
                    f"Hash verification failed. Remove hash constraints or verify integrity: {result.stderr}"
                )
            elif "ERROR: No matching distribution" in result.stderr:
                raise PackageLoadError(
                    f"Package version not found. Check version constraints: {result.stderr}"
                )
            else:
                raise PackageLoadError(f"Requirements installation failed: {result.stderr}")

        # Log successful installation
        logger.info("Requirements installed successfully",
                   venv=venv_name,
                   requirements_file=str(req_file))

        # Parse and log installed packages (optional, for debugging)
        if result.stdout:
            installed_packages = []
            for line in result.stdout.split('\n'):
                if "Successfully installed" in line:
                    # Extract package names from "Successfully installed pkg1-1.0 pkg2-2.0"
                    parts = line.split("Successfully installed")
                    if len(parts) > 1:
                        installed_packages = parts[1].strip().split()

            if installed_packages:
                logger.info("Installed dependencies",
                           venv=venv_name,
                           packages=installed_packages)

        return True

    except subprocess.TimeoutExpired:
        logger.error("Requirements installation timed out after 300s",
                    venv=venv_name,
                    requirements_file=str(req_file))
        raise PackageLoadError(
            f"Requirements installation timed out after 5 minutes. "
            f"Check for large dependencies or network issues."
        )
    except Exception as e:
        logger.error("Unexpected error during requirements installation",
                    venv=venv_name,
                    error=str(e),
                    error_type=type(e).__name__)
        raise PackageLoadError(f"Unexpected error installing requirements: {e}")
```

### 2. Integrate into _ensure_venv Method

**Location:** `_ensure_venv()` method in loader.py (after line 475)

**Changes needed:**

**Before:**
```python
# Lines 452-483 (current code)
# Install the agent package itself (if setup.py exists)
setup_file = package_path / "setup.py"
if setup_file.exists():
    # ... setuptools/wheel installation ...

    # Now install agent package in editable mode
    logger.info("Installing agent package in editable mode", ...)
    result = subprocess.run([str(pip_path), "install", "-e", str(package_path)], ...)

    if result.returncode != 0:
        logger.error("Agent package installation failed", ...)
        raise PackageLoadError(f"Agent package installation failed: {result.stderr}")

    logger.info("Agent package installed successfully", venv=venv_name)
else:
    logger.info("No setup.py found - skipping agent package installation", ...)

# Continue to next step...
```

**After:**
```python
# Lines 452-483 (current code)
# Install the agent package itself (if setup.py exists)
setup_file = package_path / "setup.py"
if setup_file.exists():
    # ... setuptools/wheel installation ...

    # Now install agent package in editable mode
    logger.info("Installing agent package in editable mode", ...)
    result = subprocess.run([str(pip_path), "install", "-e", str(package_path)], ...)

    if result.returncode != 0:
        logger.error("Agent package installation failed", ...)
        raise PackageLoadError(f"Agent package installation failed: {result.stderr}")

    logger.info("Agent package installed successfully", venv=venv_name)
else:
    logger.info("No setup.py found - skipping agent package installation", ...)

# NEW: Install requirements.txt (if exists)
# This ensures dependencies are available even if setup.py doesn't have install_requires
logger.info("Installing dependencies from requirements.txt",
           venv=venv_name,
           package_path=str(package_path))

self._install_requirements(venv_path, package_path, venv_name)

logger.info("Venv setup complete",
           venv=venv_name,
           venv_path=str(venv_path))

# Continue to next step...
```

### 3. Update Success Logging

**Location:** After requirements installation (line ~490)

**Purpose:** Provide clear feedback about what was installed

**Example logging:**
```python
logger.info("Agent deployment venv ready",
           deployment_id=deployment.id,
           venv=venv_name,
           package_installed=setup_file.exists(),
           requirements_installed=(package_path / "requirements.txt").exists(),
           venv_path=str(venv_path))
```

## Edge Cases to Handle

### Case 1: No requirements.txt File
**Scenario:** Agent has no dependencies (simple agent)
**Behavior:**
```python
if not req_file.exists():
    logger.info("No requirements.txt found, skipping dependency installation")
    return True  # Not an error
```

### Case 2: Empty requirements.txt
**Scenario:** requirements.txt exists but contains only comments/blank lines
**Input:**
```
# Just comments
# No actual dependencies
```
**Behavior:** Parse file, detect empty, skip installation
```python
if not lines:  # After filtering comments/blanks
    logger.info("requirements.txt is empty, skipping dependency installation")
    return True
```

### Case 3: requirements.txt with setup.py install_requires
**Scenario:** Agent uses PAK with `generate_install_requires: true`
**Flow:**
1. `pip install -e .` installs setup.py with populated install_requires → dependencies installed
2. `pip install -r requirements.txt` runs again → pip detects already installed, skips (fast)
**Result:** No duplication, idempotent, works correctly

### Case 4: Malformed requirements.txt
**Scenario:** Invalid package names or syntax errors
**Example:**
```
not-a-real-package==999.999.999
invalid syntax here
```
**Behavior:** pip will fail with clear error
```python
if "Could not find a version" in result.stderr:
    raise PackageLoadError(f"Dependency not found: {result.stderr}")
```

### Case 5: Network Failure During Installation
**Scenario:** PyPI/CodeArtifact unreachable
**Behavior:** pip fails, error logged, deployment fails
```python
except subprocess.TimeoutExpired:
    raise PackageLoadError("Requirements installation timed out")
```

### Case 6: Hash Verification Failures
**Scenario:** requirements.txt has hash constraints that don't match
**Example:**
```
requests==2.28.0 --hash=sha256:abc123...
```
**Behavior:** Detect hash error, raise with helpful message
```python
if "THESE PACKAGES DO NOT MATCH THE HASHES" in result.stderr:
    raise PackageLoadError(f"Hash verification failed: {result.stderr}")
```

### Case 7: Large Dependencies (torch, tensorflow)
**Scenario:** requirements.txt includes multi-GB packages
**Mitigation:** 5-minute timeout (300s) allows for large downloads
```python
timeout=300  # 5 minutes max
```

### Case 8: Conflicting Dependencies
**Scenario:** requirements.txt conflicts with setup.py install_requires
**Example:**
- setup.py: `install_requires=['requests>=2.28.0']`
- requirements.txt: `requests==2.27.0`
**Behavior:** pip's resolver will handle it (may fail or resolve to compatible version)
**Logging:** stderr will show conflicts

### Case 9: Private Package Repositories
**Scenario:** requirements.txt references packages in CodeArtifact/private PyPI
**Behavior:** Use `_get_codeartifact_pip_index()` to inject --index-url
```python
if pip_index_url:
    install_cmd.extend(["--index-url", pip_index_url])
```

### Case 10: Editable Installs in requirements.txt
**Scenario:** requirements.txt has `-e git+https://...`
**Behavior:** pip handles it normally (will clone and install)
**Note:** May be slow, but valid use case

## Error Handling Strategy

### Critical Errors (Raise PackageLoadError)
- Dependency not found in repository
- Hash verification failures
- Version conflicts that can't be resolved
- Timeout after 5 minutes
- Unexpected subprocess errors

### Non-Critical Warnings (Log and Continue)
- requirements.txt doesn't exist (common, not an error)
- requirements.txt is empty
- Can't read requirements.txt to count lines (still attempt install)

### User-Friendly Error Messages
```python
# Bad: Generic error
raise PackageLoadError(f"Failed: {stderr}")

# Good: Specific, actionable error
raise PackageLoadError(
    f"Dependency 'foobar==1.2.3' not found in PyPI or CodeArtifact. "
    f"Check requirements.txt for typos or add package to your private repository."
)
```

## Testing Requirements

### Unit Tests

**File:** `tests/test_loader.py` or new `tests/test_requirements_installation.py`

**Test cases:**

1. **test_install_requirements_success** - Happy path
```python
@pytest.mark.asyncio
async def test_install_requirements_success(tmp_path):
    """Test successful requirements installation."""
    # Create venv and requirements.txt
    venv_path = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_path)])

    package_path = tmp_path / "package"
    package_path.mkdir()
    req_file = package_path / "requirements.txt"
    req_file.write_text("requests>=2.28.0\n")

    loader = PackageLoader(...)
    result = loader._install_requirements(venv_path, package_path, "test-venv")

    assert result == True

    # Verify requests is installed
    pip_path = venv_path / "bin" / "pip"
    check = subprocess.run([str(pip_path), "list"], capture_output=True, text=True)
    assert "requests" in check.stdout
```

2. **test_install_requirements_no_file** - Missing requirements.txt
```python
@pytest.mark.asyncio
async def test_install_requirements_no_file(tmp_path):
    """Test handling missing requirements.txt."""
    venv_path = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_path)])

    package_path = tmp_path / "package"
    package_path.mkdir()
    # No requirements.txt created

    loader = PackageLoader(...)
    result = loader._install_requirements(venv_path, package_path, "test-venv")

    assert result == True  # Not an error
```

3. **test_install_requirements_empty_file** - Empty requirements.txt
```python
@pytest.mark.asyncio
async def test_install_requirements_empty_file(tmp_path):
    """Test handling empty requirements.txt."""
    venv_path = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_path)])

    package_path = tmp_path / "package"
    package_path.mkdir()
    req_file = package_path / "requirements.txt"
    req_file.write_text("# Just comments\n\n")

    loader = PackageLoader(...)
    result = loader._install_requirements(venv_path, package_path, "test-venv")

    assert result == True
```

4. **test_install_requirements_package_not_found** - Invalid package
```python
@pytest.mark.asyncio
async def test_install_requirements_package_not_found(tmp_path):
    """Test error handling for non-existent package."""
    venv_path = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_path)])

    package_path = tmp_path / "package"
    package_path.mkdir()
    req_file = package_path / "requirements.txt"
    req_file.write_text("not-a-real-package==999.999.999\n")

    loader = PackageLoader(...)

    with pytest.raises(PackageLoadError) as exc_info:
        loader._install_requirements(venv_path, package_path, "test-venv")

    assert "Dependency not found" in str(exc_info.value)
```

5. **test_install_requirements_timeout** - Mock timeout
```python
@pytest.mark.asyncio
async def test_install_requirements_timeout(tmp_path, monkeypatch):
    """Test timeout handling during installation."""
    venv_path = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_path)])

    package_path = tmp_path / "package"
    package_path.mkdir()
    req_file = package_path / "requirements.txt"
    req_file.write_text("requests\n")

    # Mock subprocess.run to raise TimeoutExpired
    def mock_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=300)

    monkeypatch.setattr(subprocess, "run", mock_run)

    loader = PackageLoader(...)

    with pytest.raises(PackageLoadError) as exc_info:
        loader._install_requirements(venv_path, package_path, "test-venv")

    assert "timed out" in str(exc_info.value).lower()
```

6. **test_install_requirements_with_comments** - Requirements with comments
```python
@pytest.mark.asyncio
async def test_install_requirements_with_comments(tmp_path):
    """Test installing requirements with comments and blank lines."""
    venv_path = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_path)])

    package_path = tmp_path / "package"
    package_path.mkdir()
    req_file = package_path / "requirements.txt"
    req_file.write_text("""
# Core dependencies
requests>=2.28.0

# Optional
structlog  # Logging
""")

    loader = PackageLoader(...)
    result = loader._install_requirements(venv_path, package_path, "test-venv")

    assert result == True

    # Verify both packages installed
    pip_path = venv_path / "bin" / "pip"
    check = subprocess.run([str(pip_path), "list"], capture_output=True, text=True)
    assert "requests" in check.stdout
    assert "structlog" in check.stdout
```

### Integration Tests

**File:** `tests/test_deployment_integration.py`

**Test case:**
```python
@pytest.mark.asyncio
async def test_deploy_agent_with_requirements(test_agent_apkg):
    """Test full deployment flow with requirements.txt."""
    # Given: APKG with requirements.txt containing real dependencies
    agent_path = create_test_apkg_with_requirements(
        requirements=["requests>=2.28.0", "structlog"]
    )

    # When: Deploy agent
    loader = PackageLoader(...)
    deployment = await loader.load_package(agent_path)

    # Then: Venv should have dependencies installed
    venv_path = deployment.venv_path
    pip_path = venv_path / "bin" / "pip"

    result = subprocess.run(
        [str(pip_path), "list"],
        capture_output=True,
        text=True
    )

    assert "requests" in result.stdout
    assert "structlog" in result.stdout

    # And: Agent should be able to import them
    python_path = venv_path / "bin" / "python"
    import_test = subprocess.run(
        [str(python_path), "-c", "import requests; import structlog; print('OK')"],
        capture_output=True,
        text=True
    )

    assert import_test.returncode == 0
    assert "OK" in import_test.stdout
```

## Validation After Implementation

### 1. Check Loader Code

```python
# Verify new method exists
grep -A 50 "_install_requirements" src/pixell_runtime/agents/loader.py

# Verify integration into _ensure_venv
grep -A 5 "self._install_requirements" src/pixell_runtime/agents/loader.py
```

### 2. Run Unit Tests

```bash
cd /Users/syum/dev/pixell-agent-runtime
pytest tests/test_loader.py::test_install_requirements_success -v
pytest tests/test_loader.py -k requirements -v
```

### 3. Deploy Test Agent Locally

```bash
# Create test agent with requirements.txt
mkdir -p /tmp/test-agent/src
cat > /tmp/test-agent/requirements.txt <<EOF
requests>=2.28.0
structlog
EOF

cat > /tmp/test-agent/agent.yaml <<EOF
name: test-agent
version: 1.0.0
description: Test agent
entrypoint: "src.main:main"
EOF

cat > /tmp/test-agent/src/main.py <<EOF
def main():
    import requests
    import structlog
    print("All imports successful!")
    return {"status": "ok"}
EOF

# Build APKG (using PAK)
cd /tmp/test-agent
pixell build

# Copy to PAR packages directory
cp test-agent-1.0.0.apkg /var/run/pixell/packages/

# Trigger deployment (via API or direct loader call)
# Verify in logs that requirements are installed
```

### 4. Verify Deployment Logs

```bash
# Check PAR logs for requirements installation
tail -f /var/log/pixell-runtime.log | grep -i "requirements"

# Expected log entries:
# "Found requirements.txt with dependencies" line_count=2
# "Installing dependencies from requirements.txt"
# "Requirements installed successfully"
# "Installed dependencies" packages=["requests-2.28.0", "structlog-..."]
```

### 5. Verify Venv Contents

```bash
# After deployment, check venv
source /var/run/pixell/venvs/test-agent-1.0.0/bin/activate
pip list | grep requests  # Should show installed version
pip list | grep structlog # Should show installed version

# Test imports
python -c "import requests; print('requests OK')"
python -c "import structlog; print('structlog OK')"
```

### 6. Deploy vivid-commenter and Test

```bash
# Rebuild vivid-commenter APKG (with latest PAK if using install_requires)
cd /Users/syum/dev/vivid-commenter
pixell build

# Deploy to PAR
pixell deploy vivid-commenter --version 1.0.1 --force

# Check logs
aws ecs execute-command --cluster pixell-runtime-cluster \
  --task <task-id> \
  --container runtime \
  --command "tail -100 /var/log/pixell-runtime.log" \
  --interactive

# Verify langchain_openai installed
aws ecs execute-command --cluster pixell-runtime-cluster \
  --task <task-id> \
  --container runtime \
  --command "bash" \
  --interactive

# Inside container:
source /var/run/pixell/venvs/vivid-commenter-1.0.1/bin/activate
pip list | grep langchain-openai  # Should be installed
python -c "from langchain_openai import ChatOpenAI; print('OK')"

# Test chat endpoint
curl -X POST http://localhost:8080/agents/vivid-commenter/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hello"}'

# Expected: Real AI response (not mock)
# {"status": "success", "response": "Hello! How can I help you?", "mock": false}
```

### 7. Check Performance Impact

**Measure installation time:**
```bash
# Before: Only pip install -e .
# Time: ~2-5 seconds

# After: pip install -e . + pip install -r requirements.txt
# Time: First install ~30-60s (downloads), subsequent ~5s (cache hits)
```

**Optimization note:** Second `pip install -r requirements.txt` is fast if packages already installed by setup.py install_requires (idempotent).

## Performance Considerations

### Cold Start Impact
- **First deployment:** +30-60s for dependency downloads
- **Cached deployments:** +5s (pip checks, skips installed)
- **With setup.py install_requires:** Negligible (+1-2s, pip deduplicates)

### Network Optimization
- Use CodeArtifact for faster regional downloads
- pip caching reduces repeat downloads
- Timeout set to 5min handles large packages (torch, tensorflow)

### Concurrent Deployments
- Each deployment gets its own venv (isolated)
- Parallel pip installs supported (different venvs)
- No locking issues

## Security Considerations

### Malicious requirements.txt
**Risk:** Agent includes malicious package in requirements.txt
**Mitigation:**
- Package validation should happen during APKG upload (registry layer)
- Use private PyPI/CodeArtifact for trusted packages
- Run agent code in isolated containers (already implemented)

### Dependency Confusion Attacks
**Risk:** Attacker uploads malicious package with same name to PyPI
**Mitigation:**
- Use CodeArtifact with upstream priority (private repo first)
- Hash verification in requirements.txt (optional)
- Package scanning in CI/CD pipeline

### Command Injection
**Risk:** Malicious package names in requirements.txt
**Mitigation:**
- subprocess.run with list arguments (not shell=True)
- No string interpolation in subprocess commands
- pip handles sanitization

## Backward Compatibility

### Agents Without requirements.txt
**Behavior:** No change, installation skipped, logs indicate skip
**Impact:** None (backward compatible)

### Agents With setup.py install_requires
**Behavior:** Both setup.py and requirements.txt installed
**Impact:** pip deduplicates, minimal overhead (~1-2s)
**Idempotent:** Safe to run both

### Old APKGs (No setup.py)
**Behavior:** Only requirements.txt installed (if present)
**Impact:** Fixes agents that previously failed due to missing dependencies

## Integration with PAK Changes

### Scenario 1: Agent Uses PAK with generate_install_requires=true
**Flow:**
1. PAK generates setup.py with install_requires populated
2. PAR runs `pip install -e .` → installs package + dependencies
3. PAR runs `pip install -r requirements.txt` → pip sees already installed, skips
**Result:** Dependencies installed once, fast second pass

### Scenario 2: Agent Uses PAK with generate_install_requires=false (default)
**Flow:**
1. PAK generates setup.py with install_requires=[]
2. PAR runs `pip install -e .` → installs package only
3. PAR runs `pip install -r requirements.txt` → installs dependencies
**Result:** Dependencies installed correctly

### Scenario 3: Agent Has Custom setup.py (No PAK generation)
**Flow:**
1. setup.py has custom install_requires
2. PAR runs `pip install -e .` → installs package + custom dependencies
3. PAR runs `pip install -r requirements.txt` → installs additional dependencies (if any)
**Result:** All dependencies available

## Migration Path

### For Existing Deployments
**No action required** - next deployment will pick up the fix automatically

### For Failed Deployments (vivid-commenter)
1. Update PAR with requirements installation (this implementation)
2. Deploy updated PAR to ECS
3. Redeploy vivid-commenter with `--force` flag
4. Verify chat endpoint returns real AI responses

### For New Agents
**No changes needed** - requirements.txt will be installed automatically

## Success Criteria

- ✅ `_install_requirements()` method implemented with error handling
- ✅ Integrated into `_ensure_venv()` after agent package installation
- ✅ Handles all edge cases (no file, empty file, errors, timeouts)
- ✅ CodeArtifact support maintained
- ✅ Comprehensive logging at all stages
- ✅ Unit tests pass
- ✅ Integration tests pass
- ✅ vivid-commenter deploys and responds without mock
- ✅ No regression for agents without requirements.txt
- ✅ Performance impact acceptable (<5s for cached, <60s for fresh)

## Files to Modify

1. `/Users/syum/dev/pixell-agent-runtime/src/pixell_runtime/agents/loader.py`
   - Add `_install_requirements()` method (~150 lines)
   - Call it in `_ensure_venv()` after agent package installation (~3 lines)
   - Update success logging (~5 lines)

2. `/Users/syum/dev/pixell-agent-runtime/tests/test_loader.py`
   - Add unit tests for `_install_requirements()` (~6 test cases)
   - Add integration test for full deployment flow (~1 test case)

## Implementation Order

1. **Phase 1:** Implement `_install_requirements()` method with full error handling
2. **Phase 2:** Integrate into `_ensure_venv()` method
3. **Phase 3:** Add unit tests
4. **Phase 4:** Test locally with test agent
5. **Phase 5:** Deploy to ECS staging
6. **Phase 6:** Deploy vivid-commenter and verify
7. **Phase 7:** Monitor production deployments

## Rollback Plan

If issues arise after deployment:

1. **Immediate:** Revert loader.py to previous version
2. **Deploy:** Updated PAR without requirements installation
3. **Investigate:** Check logs for specific failures
4. **Fix:** Address edge case in dev environment
5. **Redeploy:** With fix and additional tests

## Example Logs (After Implementation)

### Successful Deployment

```
INFO Installing agent package in editable mode package_path=/var/run/pixell/packages/vivid-commenter-1.0.1 venv=vivid-commenter-1.0.1
INFO Agent package installed successfully venv=vivid-commenter-1.0.1
INFO Installing dependencies from requirements.txt venv=vivid-commenter-1.0.1 package_path=/var/run/pixell/packages/vivid-commenter-1.0.1
INFO Found requirements.txt with dependencies venv=vivid-commenter-1.0.1 line_count=5
INFO Installing dependencies from requirements.txt venv=vivid-commenter-1.0.1 requirements_file=/var/run/pixell/packages/vivid-commenter-1.0.1/requirements.txt
INFO Requirements installed successfully venv=vivid-commenter-1.0.1 requirements_file=/var/run/pixell/packages/vivid-commenter-1.0.1/requirements.txt
INFO Installed dependencies venv=vivid-commenter-1.0.1 packages=["langchain-openai-0.3.28", "langchain-core-0.3.28", "requests-2.28.0", "structlog-24.1.0", "fastapi-0.100.0"]
INFO Agent deployment venv ready deployment_id=dep-123 venv=vivid-commenter-1.0.1 package_installed=true requirements_installed=true
```

### Agent Without requirements.txt

```
INFO Installing agent package in editable mode package_path=/var/run/pixell/packages/simple-agent-1.0.0 venv=simple-agent-1.0.0
INFO Agent package installed successfully venv=simple-agent-1.0.0
INFO Installing dependencies from requirements.txt venv=simple-agent-1.0.0 package_path=/var/run/pixell/packages/simple-agent-1.0.0
INFO No requirements.txt found, skipping dependency installation venv=simple-agent-1.0.0 package_path=/var/run/pixell/packages/simple-agent-1.0.0
INFO Agent deployment venv ready deployment_id=dep-124 venv=simple-agent-1.0.0 package_installed=true requirements_installed=false
```

### Error Case

```
INFO Installing dependencies from requirements.txt venv=broken-agent-1.0.0 package_path=/var/run/pixell/packages/broken-agent-1.0.0
INFO Found requirements.txt with dependencies venv=broken-agent-1.0.0 line_count=3
ERROR Requirements installation failed venv=broken-agent-1.0.0 returncode=1 stderr="ERROR: Could not find a version that satisfies the requirement not-a-package==999.999.999"
ERROR Failed to load package package_id=broken-agent-1.0.0 error="Dependency not found in PyPI/CodeArtifact. Check requirements.txt for typos or unavailable packages: ERROR: Could not find a version..."
```

## Notes

- Keep requirements installation after agent package installation (order matters)
- Use same CodeArtifact index URL for consistency
- 5-minute timeout accommodates large packages (torch ~2GB)
- Idempotent with setup.py install_requires (pip handles deduplication)
- Comprehensive error messages guide users to fix issues
- Logging provides visibility into installation process
- No breaking changes to existing deployments
