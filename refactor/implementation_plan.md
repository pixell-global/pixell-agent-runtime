# PAR SOCKET REFACTOR - IMPLEMENTATION PLAN

**Repository:** pixell-agent-runtime (PAR)
**Document Version:** 1.0
**Date:** November 11, 2025
**Status:** Phase-by-Phase Implementation Guide
**Approach:** Test-Driven, Incremental, Reversible

**⚠️ CRITICAL PRINCIPLE: No phase proceeds until ALL tests pass**

---

## OVERVIEW

This implementation plan breaks down the PAR socket refactor into **6 distinct phases**, each with:
- **Clear, measurable goals**
- **Complete, independent deliverables**
- **Comprehensive test suites**
- **Validation criteria** (must pass 100% before proceeding)
- **Rollback procedures**

**Timeline:** 6-8 weeks (1-2 weeks per phase)
**Risk Mitigation:** Hybrid mode support (port + socket coexist during migration)

---

## PHASE DEPENDENCIES

```
Phase 1: Foundation (Models & Types)
   ↓
Phase 2: Socket Allocator (Core Logic)
   ↓
Phase 3: Process Management (Spawning)
   ↓
Phase 4: Runtime Configuration (Agent Startup)
   ↓
Phase 5: API & Orchestration (Supervisor)
   ↓
Phase 6: Integration & Migration
```

---

## PHASE 1: FOUNDATION - DATA MODELS & TYPES

**Duration:** 1 week
**Risk Level:** 🟢 LOW - No behavioral changes, type additions only
**Goal:** Add socket mode flag to enable stateless socket deployment

**⚠️ CRITICAL PRINCIPLE: PAR is STATELESS - socket paths are NEVER stored**
- Socket paths are computed on-the-fly from agent_id by SocketAllocator
- Only store `socket_mode` flag (boolean) to indicate deployment type
- This keeps PAR stateless and prevents stale path data

### 1.1 Objectives

- [ ] Add `socket_mode` flag to `DeployRequest` and `AgentInfo`
- [ ] DO NOT add SocketPaths to models.py (not stored, only computed)
- [ ] DO NOT store socket paths in database (computed from agent_id)
- [ ] Maintain 100% backward compatibility with port-based models
- [ ] All existing tests continue to pass unchanged

### 1.2 Files to Modify

**File: `src/pixell_runtime/supervisor/models.py`**

```python
# Modify DeployRequest (around line 50)
class DeployRequest(BaseModel):
    """Request to deploy an agent."""
    agent_id: str
    package_url: str

    # Port mode (legacy) - Keep for backward compatibility
    ports: Optional[Ports] = None

    # Socket mode (new) - Socket paths computed by SocketAllocator, not stored
    socket_mode: bool = False

    environment: Dict[str, str] = {}
    cpu_limit: Optional[int] = None
    memory_limit: Optional[int] = None

    def validate(self):
        """Validate deployment request."""
        # Socket mode: No ports needed, paths computed from agent_id
        # Port mode: Ports required
        if not self.socket_mode and not self.ports:
            raise ValueError("Port mode (socket_mode=False) requires ports")
        if self.socket_mode and self.ports:
            raise ValueError("Socket mode (socket_mode=True) should not specify ports")


# Modify AgentInfo (around line 80)
class AgentInfo(BaseModel):
    """Information about a deployed agent."""
    agent_id: str
    status: str

    # Port mode info (legacy)
    ports: Optional[Ports] = None

    # Socket mode flag (new) - Paths computed on-the-fly, not stored
    socket_mode: bool = False

    pid: Optional[int] = None
    package_url: str
    deployed_at: str

    # Note: Socket paths are NEVER stored here
    # Use SocketAllocator.allocate(agent_id) to compute paths when needed
```

**Database Migration (MUST RUN BEFORE CODE DEPLOYMENT)**

**⚠️ CRITICAL: This migration MUST be backward compatible**
- Old PAR code (still running) must not break
- New PAR code needs the new column
- Use `ADD COLUMN IF NOT EXISTS` for safety

**File: `migrations/001_add_socket_mode.sql`**

```sql
-- Migration: Add socket_mode column to agents table
-- Run this BEFORE deploying Phase 1 code
-- BACKWARD COMPATIBLE: Old code will ignore new column

BEGIN;

-- Add socket_mode column with default FALSE (port mode)
ALTER TABLE agents
  ADD COLUMN IF NOT EXISTS socket_mode BOOLEAN
  NOT NULL DEFAULT FALSE;

-- Add index for querying by mode
CREATE INDEX IF NOT EXISTS idx_agents_socket_mode
  ON agents(socket_mode);

-- Add comment
COMMENT ON COLUMN agents.socket_mode IS
  'TRUE if agent uses Unix sockets, FALSE if using TCP ports (legacy)';

COMMIT;
```

**MySQL Connection Pool Configuration (CRITICAL for 500+ agents)**

**File: `database_config.cnf`** (Apply before Phase 6)

```ini
# MySQL Configuration for Socket Mode
# Each agent opens ~10 database connections
# 500 agents × 10 = 5000 connections minimum

[mysqld]
# ⚠️ CRITICAL: Increase connection limit
max_connections = 10000  # Up from default 151

# Connection pool tuning
max_connect_errors = 1000000
wait_timeout = 28800  # 8 hours
interactive_timeout = 28800

# Memory per connection ~256KB
# 10000 × 256KB = 2.5GB RAM for connections
# Ensure server has sufficient RAM

# Thread cache
thread_cache_size = 100

# Query cache (optional)
query_cache_size = 0  # Disable for better write performance
```

**Apply configuration:**
```bash
# Backup current config
sudo cp /etc/my.cnf /etc/my.cnf.backup

# Apply new config
sudo tee -a /etc/my.cnf < database_config.cnf

# Restart MySQL (during maintenance window!)
sudo systemctl restart mysql

# Verify new limits
mysql -u root -p -e "SHOW VARIABLES LIKE 'max_connections';"
# Expected: max_connections | 10000
```

**Rollback Migration:**

```sql
-- Rollback if Phase 1 fails
BEGIN;
DROP INDEX IF EXISTS idx_agents_socket_mode;
ALTER TABLE agents DROP COLUMN IF EXISTS socket_mode;
COMMIT;
```

### 1.3 Test Requirements

**New Test File: `tests/test_supervisor_models_socket.py`**

```python
import pytest
from pixell_runtime.supervisor.models import DeployRequest, AgentInfo, Ports

class TestDeployRequestSocket:
    """Test DeployRequest with socket mode."""

    def test_deploy_request_port_mode(self):
        """Test DeployRequest in port mode (legacy)."""
        req = DeployRequest(
            agent_id="test-agent",
            package_url="s3://test/package.apkg",
            ports=Ports(rest=63000, a2a=60000, ui=65000),
            socket_mode=False,
            environment={}
        )
        req.validate()
        assert req.ports is not None
        assert req.socket_mode is False

    def test_deploy_request_socket_mode(self):
        """Test DeployRequest in socket mode (stateless)."""
        req = DeployRequest(
            agent_id="test-agent",
            package_url="s3://test/package.apkg",
            socket_mode=True,  # No socket_paths - computed from agent_id
            environment={}
        )
        req.validate()
        assert req.ports is None
        assert req.socket_mode is True
        # Socket paths will be computed by SocketAllocator.allocate(agent_id)

    def test_deploy_request_validation_port_mode_without_ports(self):
        """Test that socket_mode=False requires ports."""
        req = DeployRequest(
            agent_id="test-agent",
            package_url="s3://test/package.apkg",
            socket_mode=False  # Missing ports
        )
        with pytest.raises(ValueError, match="requires ports"):
            req.validate()

    def test_deploy_request_validation_socket_mode_with_ports(self):
        """Test that socket mode should not specify ports."""
        req = DeployRequest(
            agent_id="test-agent",
            package_url="s3://test/package.apkg",
            ports=Ports(rest=63000, a2a=60000, ui=65000),
            socket_mode=True  # Contradictory: socket mode but with ports
        )
        with pytest.raises(ValueError, match="should not specify ports"):
            req.validate()


class TestAgentInfoSocket:
    """Test AgentInfo with socket mode."""

    def test_agent_info_port_mode(self):
        """Test AgentInfo in port mode."""
        info = AgentInfo(
            agent_id="test-agent",
            status="running",
            ports=Ports(rest=63000, a2a=60000, ui=65000),
            socket_mode=False,
            pid=12345,
            package_url="s3://test/package.apkg",
            deployed_at="2025-11-11T12:00:00Z"
        )
        assert info.ports is not None
        assert info.socket_mode is False

    def test_agent_info_socket_mode(self):
        """Test AgentInfo in socket mode (stateless)."""
        info = AgentInfo(
            agent_id="test-agent",
            status="running",
            ports=None,  # No ports in socket mode
            socket_mode=True,
            pid=12345,
            package_url="s3://test/package.apkg",
            deployed_at="2025-11-11T12:00:00Z"
        )
        assert info.ports is None
        assert info.socket_mode is True
        # Socket paths computed on-demand: SocketAllocator.allocate(agent_id)
```

**Existing Tests:**
- [ ] Run ALL existing supervisor tests: `pytest tests/test_supervisor*.py`
- [ ] Verify 100% pass rate (no regressions)

### 1.4 Validation Criteria (Must Pass All)

- [ ] New test file passes: `pytest tests/test_supervisor_models_socket.py -v`
- [ ] All existing tests pass: `pytest tests/ -v`
- [ ] No import errors in supervisor modules
- [ ] Type checking passes: `mypy src/pixell_runtime/supervisor/models.py`
- [ ] Code review approved

### 1.5 Rollback Procedure

```bash
# If Phase 1 fails, revert changes:
git checkout HEAD -- src/pixell_runtime/supervisor/models.py
git checkout HEAD -- tests/test_supervisor_models_socket.py
pytest tests/  # Verify revert successful
```

---

## PHASE 2: SOCKET ALLOCATOR - CORE LOGIC

**Duration:** 1-2 weeks
**Risk Level:** 🟡 MEDIUM - New component, no impact on existing code
**Goal:** Create socket path allocator with comprehensive tests

### 2.1 Objectives

- [ ] Create `socket_allocator.py` with complete implementation
- [ ] Create comprehensive unit tests
- [ ] Test socket directory creation and permissions
- [ ] Test cleanup logic
- [ ] Mark `port_allocator.py` as deprecated (no deletion)

### 2.2 Files to Create

**New File: `src/pixell_runtime/supervisor/socket_allocator.py`**

```python
"""
Socket path allocator for Unix domain socket-based agent deployment.

This module replaces port_allocator.py for socket mode deployments.
Socket paths follow the pattern:
  /var/run/pixell-agents/agent_{short_id}/{surface}.sock

⚠️ CRITICAL: Socket base directory must match Nginx configuration
  - PAR uses: /var/run/pixell-agents
  - Nginx config uses: /var/run/pixell-agents
  - Must be IDENTICAL or routing will fail
"""

import os
import shutil
import grp
from pathlib import Path
from typing import Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

# ⚠️ HARDCODED: Must match nginx/pixell-agents.conf
SOCKET_BASE_DIR = "/var/run/pixell-agents"

@dataclass
class SocketPaths:
    """Socket paths for a deployed agent."""
    base_dir: Path
    rest: Path
    a2a: Path
    ui: Path

    def __post_init__(self):
        """Validate socket paths."""
        for socket_path in [self.rest, self.a2a, self.ui]:
            if not str(socket_path).startswith(str(self.base_dir)):
                raise ValueError(f"Socket {socket_path} must be within {self.base_dir}")


class SocketAllocator:
    """
    Allocates Unix domain socket paths for agents.

    Unlike PortAllocator (200 agent limit), sockets have no practical limit.
    """

    def __init__(self, base_dir: str = SOCKET_BASE_DIR):
        self.base_dir = Path(base_dir)
        self._ensure_base_directory()

    def _ensure_base_directory(self):
        """Create base socket directory with correct permissions."""
        if not self.base_dir.exists():
            logger.info(f"Creating socket base directory: {self.base_dir}")
            self.base_dir.mkdir(parents=True, mode=0o755)

    def allocate(self, agent_id: str) -> SocketPaths:
        """
        Allocate socket paths for an agent.

        ⚠️ CRITICAL FIXES:
        1. Keep short_id for backward compatibility (existing clients expect it)
        2. Add collision detection (warn if duplicate short_id found)
        3. Validate path length (Unix socket limit: 108 chars)

        ⚠️ BACKWARD COMPATIBILITY:
        - URLs use short_id: /rest/4906eeb7/health (8 chars)
        - NOT hash: /rest/a1b2c3d4e5f6g7h8/health (would break clients)
        - Socket path: /var/run/pixell-agents/agent_4906eeb7/

        Args:
            agent_id: Full agent UUID (e.g., 4906eeb7-9959-414e-84c6-f2445822ebe4)

        Returns:
            SocketPaths with allocated paths

        Raises:
            ValueError: If socket path exceeds 108 characters
            RuntimeWarning: If short_id collision detected (very unlikely with UUIDs)
        """
        import warnings

        # Extract short_id (first segment of UUID)
        # This maintains backward compatibility with existing URL structure
        short_id = agent_id.split('-')[0]
        agent_dir = self.base_dir / f"agent_{short_id}"

        # ⚠️ COLLISION DETECTION: Check if directory exists for different agent
        # Collision probability: ~1 in 4 billion (UUID v4 first 32 bits)
        if agent_dir.exists():
            # Read stored agent_id from directory metadata
            metadata_file = agent_dir / ".agent_id"
            if metadata_file.exists():
                stored_id = metadata_file.read_text().strip()
                if stored_id != agent_id:
                    warnings.warn(
                        f"Short ID collision detected! "
                        f"Agent {agent_id} has same short_id ({short_id}) as {stored_id}. "
                        f"This is extremely rare. Consider manual intervention.",
                        RuntimeWarning
                    )
                    # Alternative: Use hash fallback for collision case
                    import hashlib
                    fallback_hash = hashlib.sha256(agent_id.encode()).hexdigest()[:16]
                    agent_dir = self.base_dir / f"agent_{fallback_hash}"
                    logger.warning(f"Using collision-free hash: agent_{fallback_hash}")

        # Construct socket paths
        rest_sock = agent_dir / "rest.sock"
        a2a_sock = agent_dir / "a2a.sock"
        ui_sock = agent_dir / "ui.sock"

        # ⚠️ CRITICAL: Validate path length (Unix socket limit)
        # struct sockaddr_un has 108 byte limit for sun_path
        max_path_len = 108
        for sock_path in [rest_sock, a2a_sock, ui_sock]:
            path_str = str(sock_path)
            if len(path_str) >= max_path_len:
                raise ValueError(
                    f"Socket path too long ({len(path_str)} >= {max_path_len}): {path_str}\n"
                    f"Consider using shorter base_dir or shorter socket names"
                )

        return SocketPaths(
            base_dir=agent_dir,
            rest=rest_sock,
            a2a=a2a_sock,
            ui=ui_sock
        )

    def create_agent_directory(self, sockets: SocketPaths, agent_user: str, agent_id: str):
        """
        Create agent socket directory with proper permissions.

        ⚠️ CRITICAL: This must be called BEFORE spawning the agent process.
        Directory must be writable by agent and readable by nginx.

        Permissions: 750 (rwxr-x---)
        Owner: agent_user (e.g., agent_4906eeb7)
        Group: nginx (so Nginx can access sockets)

        Args:
            sockets: Socket paths to create
            agent_user: Unix user to own the directory
            agent_id: Full agent ID (stored for collision detection)
        """
        # Validate nginx group exists FIRST
        try:
            nginx_gid = grp.getgrnam('nginx').gr_gid
        except KeyError:
            raise RuntimeError(
                "nginx group not found. Install nginx first:\n"
                "  sudo yum install nginx\n"
                "  sudo systemctl enable nginx"
            )

        # Validate agent user exists and is in nginx group
        # ⚠️ RACE CONDITION FIX: User creation may be in progress
        # PAC creates user asynchronously, PAR may be called before user exists
        # Retry for up to 30 seconds
        import pwd
        import time

        agent_uid = None
        for attempt in range(30):
            try:
                agent_uid = pwd.getpwnam(agent_user).pw_uid
                break  # User found!
            except KeyError:
                if attempt == 29:
                    raise RuntimeError(
                        f"Agent user {agent_user} does not exist after 30s. "
                        f"Ensure PAC creates user before calling PAR deploy."
                    )
                logger.debug(f"Agent user {agent_user} not found, retrying... ({attempt + 1}/30)")
                time.sleep(1)

        # Verify user is in nginx group
        agent_groups = os.getgrouplist(agent_user, nginx_gid)
        if nginx_gid not in agent_groups:
            logger.warning(
                f"Agent user {agent_user} not in nginx group. "
                f"Run: sudo usermod -a -G nginx {agent_user}"
            )

        # Create directory
        sockets.base_dir.mkdir(parents=True, exist_ok=True)

        # Set ownership: agent_user:nginx
        try:
            shutil.chown(sockets.base_dir, user=agent_user, group="nginx")
        except LookupError as e:
            logger.error(f"Failed to set ownership to {agent_user}:nginx - {e}")
            raise

        # Set permissions: 750 (rwxr-x---)
        # Agent can write sockets, nginx can read sockets
        sockets.base_dir.chmod(0o750)
        logger.info(f"Created socket directory: {sockets.base_dir} (750 {agent_user}:nginx)")

        # ⚠️ CRITICAL: Store agent_id metadata for collision detection
        # This allows allocate() to detect if different agent has same short_id
        metadata_file = sockets.base_dir / ".agent_id"
        metadata_file.write_text(agent_id)
        metadata_file.chmod(0o640)  # Readable by agent and nginx
        shutil.chown(metadata_file, user=agent_user, group="nginx")

    def cleanup(self, sockets: SocketPaths):
        """Remove agent socket directory and all sockets."""
        if sockets.base_dir.exists():
            logger.info(f"Cleaning up socket directory: {sockets.base_dir}")
            shutil.rmtree(sockets.base_dir)

    def validate_socket_availability(self, sockets: SocketPaths) -> bool:
        """Check if sockets exist and are accessible."""
        for socket_path in [sockets.rest, sockets.a2a, sockets.ui]:
            if not socket_path.exists():
                logger.warning(f"Socket does not exist: {socket_path}")
                return False
            if not socket_path.is_socket():
                logger.error(f"Path exists but is not a socket: {socket_path}")
                return False
        return True
```

**File to Modify: `src/pixell_runtime/supervisor/port_allocator.py`**

```python
# Add at top of file (line 1-10)

import warnings

warnings.warn(
    "port_allocator.py is deprecated. Use socket_allocator.py for new deployments.",
    DeprecationWarning,
    stacklevel=2
)

# ⚠️ LEGACY MODULE: This module is deprecated in favor of socket_allocator.py
# It is kept for backward compatibility during migration to socket-based deployment.
# New deployments should use SocketAllocator instead.

# ... rest of file unchanged ...
```

### 2.3 Test Requirements

**New Test File: `tests/test_supervisor_socket_allocator.py`**

```python
import pytest
import os
import tempfile
import shutil
from pathlib import Path
from pixell_runtime.supervisor.socket_allocator import (
    SocketAllocator,
    SocketPaths,
    SOCKET_BASE_DIR
)

@pytest.fixture
def temp_socket_dir():
    """Create temporary socket directory for testing."""
    temp_dir = tempfile.mkdtemp(prefix="test_sockets_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


class TestSocketAllocator:
    """Test SocketAllocator class."""

    def test_allocate_socket_paths(self, temp_socket_dir):
        """Test socket path generation."""
        allocator = SocketAllocator(base_dir=temp_socket_dir)
        agent_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"

        sockets = allocator.allocate(agent_id)

        assert str(sockets.base_dir) == f"{temp_socket_dir}/agent_4906eeb7"
        assert str(sockets.rest) == f"{temp_socket_dir}/agent_4906eeb7/rest.sock"
        assert str(sockets.a2a) == f"{temp_socket_dir}/agent_4906eeb7/a2a.sock"
        assert str(sockets.ui) == f"{temp_socket_dir}/agent_4906eeb7/ui.sock"

    def test_allocate_deterministic(self, temp_socket_dir):
        """Test that allocation is deterministic for same agent_id."""
        allocator = SocketAllocator(base_dir=temp_socket_dir)
        agent_id = "test-agent-1234"

        sockets1 = allocator.allocate(agent_id)
        sockets2 = allocator.allocate(agent_id)

        assert sockets1.rest == sockets2.rest
        assert sockets1.a2a == sockets2.a2a
        assert sockets1.ui == sockets2.ui

    def test_create_agent_directory(self, temp_socket_dir):
        """Test directory creation with permissions."""
        allocator = SocketAllocator(base_dir=temp_socket_dir)
        agent_id = "test-agent-5678"
        sockets = allocator.allocate(agent_id)

        # Create directory (skip chown in tests)
        sockets.base_dir.mkdir(parents=True, exist_ok=True)
        sockets.base_dir.chmod(0o750)

        assert sockets.base_dir.exists()
        assert sockets.base_dir.is_dir()
        assert oct(sockets.base_dir.stat().st_mode)[-3:] == "750"

    def test_cleanup(self, temp_socket_dir):
        """Test socket cleanup."""
        allocator = SocketAllocator(base_dir=temp_socket_dir)
        agent_id = "test-agent-cleanup"
        sockets = allocator.allocate(agent_id)

        # Create directory and fake socket files
        sockets.base_dir.mkdir(parents=True)
        sockets.rest.touch()
        sockets.a2a.touch()
        sockets.ui.touch()

        assert sockets.base_dir.exists()
        assert sockets.rest.exists()

        # Cleanup
        allocator.cleanup(sockets)

        assert not sockets.base_dir.exists()
        assert not sockets.rest.exists()

    def test_validate_socket_availability_all_exist(self, temp_socket_dir):
        """Test validation when all sockets exist."""
        allocator = SocketAllocator(base_dir=temp_socket_dir)
        agent_id = "test-agent-validate"
        sockets = allocator.allocate(agent_id)

        # Create directory and socket files
        sockets.base_dir.mkdir(parents=True)

        # Create actual Unix sockets (requires socket.socket)
        import socket
        for sock_path in [sockets.rest, sockets.a2a, sockets.ui]:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(str(sock_path))
            sock.close()

        assert allocator.validate_socket_availability(sockets) is True

    def test_validate_socket_availability_missing(self, temp_socket_dir):
        """Test validation when sockets missing."""
        allocator = SocketAllocator(base_dir=temp_socket_dir)
        agent_id = "test-agent-missing"
        sockets = allocator.allocate(agent_id)

        # Don't create sockets
        assert allocator.validate_socket_availability(sockets) is False

    def test_validate_socket_availability_not_socket(self, temp_socket_dir):
        """Test validation when path exists but is not a socket."""
        allocator = SocketAllocator(base_dir=temp_socket_dir)
        agent_id = "test-agent-not-socket"
        sockets = allocator.allocate(agent_id)

        # Create directory and regular files (not sockets)
        sockets.base_dir.mkdir(parents=True)
        sockets.rest.touch()  # Regular file, not socket

        assert allocator.validate_socket_availability(sockets) is False

    def test_socket_paths_within_base_dir(self, temp_socket_dir):
        """Test that socket paths must be within base directory."""
        allocator = SocketAllocator(base_dir=temp_socket_dir)
        agent_id = "test-agent"
        sockets = allocator.allocate(agent_id)

        # Valid: all paths within base_dir
        SocketPaths(
            base_dir=sockets.base_dir,
            rest=sockets.rest,
            a2a=sockets.a2a,
            ui=sockets.ui
        )

        # Invalid: path outside base_dir
        with pytest.raises(ValueError, match="must be within"):
            SocketPaths(
                base_dir=sockets.base_dir,
                rest=Path("/tmp/outside.sock"),  # Outside base_dir
                a2a=sockets.a2a,
                ui=sockets.ui
            )

    def test_base_directory_creation(self, temp_socket_dir):
        """Test that base directory is created if it doesn't exist."""
        base_dir = Path(temp_socket_dir) / "new_dir"
        assert not base_dir.exists()

        allocator = SocketAllocator(base_dir=str(base_dir))

        assert base_dir.exists()
        assert base_dir.is_dir()


class TestPortAllocatorDeprecation:
    """Test that port_allocator shows deprecation warning."""

    def test_deprecation_warning(self):
        """Test that importing port_allocator shows deprecation warning."""
        with pytest.warns(DeprecationWarning, match="deprecated"):
            import pixell_runtime.supervisor.port_allocator
```

### 2.4 Validation Criteria (Must Pass All)

- [ ] New test file passes: `pytest tests/test_supervisor_socket_allocator.py -v`
- [ ] All socket path generation tests pass
- [ ] Directory creation and permission tests pass
- [ ] Cleanup tests pass
- [ ] Validation tests pass
- [ ] Deprecation warning test passes
- [ ] No regressions in existing tests: `pytest tests/test_supervisor*.py -v`
- [ ] Code review approved

### 2.5 Rollback Procedure

```bash
# If Phase 2 fails:
git checkout HEAD -- src/pixell_runtime/supervisor/socket_allocator.py
git checkout HEAD -- src/pixell_runtime/supervisor/port_allocator.py
git checkout HEAD -- tests/test_supervisor_socket_allocator.py
pytest tests/  # Verify revert successful
```

---

## PHASE 3: PROCESS MANAGEMENT - AGENT SPAWNING

**Duration:** 1-2 weeks
**Risk Level:** 🔴 HIGH - Core process spawning logic
**Goal:** Update ProcessManager to support socket mode without breaking port mode

**⚠️ STATELESS PRINCIPLE: Socket paths computed on-the-fly, never passed as parameters**

### 3.1 Objectives

- [ ] Update `process_manager.py` to accept `socket_mode` flag (not socket paths)
- [ ] Compute socket paths from agent_id using SocketAllocator (stateless)
- [ ] Set correct environment variables for socket mode
- [ ] Maintain 100% backward compatibility with port mode
- [ ] Test both modes work correctly
- [ ] Test that socket paths are deterministic

### 3.2 Files to Modify

**File: `src/pixell_runtime/supervisor/process_manager.py`**

```python
# Add import at top of file
from pixell_runtime.supervisor.socket_allocator import SocketAllocator

# Update spawn_agent() signature (around line 85)

def spawn_agent(
    self,
    agent_id: str,
    package_path: str,
    ports: Optional[Ports] = None,      # Legacy port mode
    socket_mode: bool = False,           # New socket mode flag
    environment: dict,
    user: str,
    log_file_path: str
) -> subprocess.Popen:
    """
    Spawn agent process with port or socket configuration.

    ⚠️ CRITICAL:
    - Port mode: Must specify ports
    - Socket mode: Paths computed from agent_id (stateless)

    Socket paths are NEVER passed as parameters - they are computed
    on-the-fly using SocketAllocator.allocate(agent_id)
    """
    # Validate arguments
    if not socket_mode and ports is None:
        raise ValueError("Port mode requires ports parameter")
    if socket_mode and ports is not None:
        raise ValueError("Socket mode should not specify ports")

    # Build environment variables
    env = {
        **os.environ.copy(),
        **environment,
        "AGENT_APP_ID": agent_id,
        "MULTIPLEXED": "true",
        "AGENT_PACKAGE_PATH": package_path,
        "PYTHONUNBUFFERED": "1",
        "HOME": f"/home/{user}",
    }

    if socket_mode:
        # Socket mode: Compute paths from agent_id (stateless)
        socket_allocator = SocketAllocator()
        sockets = socket_allocator.allocate(agent_id)

        # ⚠️ CRITICAL: Create directory BEFORE spawning agent
        # This must happen here (as supervisor) because agent user lacks permission
        logger.info(f"Creating socket directory for agent {agent_id}")
        socket_allocator.create_agent_directory(sockets, agent_user=user)

        # ⚠️ CRITICAL: Remove old socket files if they exist
        # Old sockets from crashed agents will prevent binding
        for socket_path in [sockets.rest, sockets.a2a, sockets.ui]:
            if socket_path.exists():
                logger.warning(f"Removing stale socket: {socket_path}")
                socket_path.unlink()

        env["SOCKET_MODE"] = "true"
        env["REST_SOCKET"] = str(sockets.rest)
        env["A2A_SOCKET"] = str(sockets.a2a)
        env["UI_SOCKET"] = str(sockets.ui)

        logger.info(f"Spawning agent {agent_id} in SOCKET mode")
        logger.info(f"  REST socket: {sockets.rest}")
        logger.info(f"  A2A socket:  {sockets.a2a}")
        logger.info(f"  UI socket:   {sockets.ui}")
    else:
        # Port mode (legacy): Agent will bind to TCP ports
        env["SOCKET_MODE"] = "false"
        env["REST_PORT"] = str(ports.rest)
        env["A2A_PORT"] = str(ports.a2a)
        env["UI_PORT"] = str(ports.ui)

        logger.info(f"Spawning agent {agent_id} in PORT mode (LEGACY)")
        logger.info(f"  REST port: {ports.rest}")
        logger.info(f"  A2A port:  {ports.a2a}")
        logger.info(f"  UI port:   {ports.ui}")

    # Open log file
    log_file = open(log_file_path, "a")

    # Spawn process as target user
    process = subprocess.Popen(
        ["/usr/bin/python3.11", "-m", "pixell_runtime"],
        user=user,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=f"/home/{user}"
    )

    logger.info(f"Agent process started: PID {process.pid}, user {user}")

    # ⚠️ CRITICAL: Wait for agent to create sockets (socket mode only)
    # Prevents race condition where Nginx tries to proxy before socket exists
    if socket_mode:
        logger.info(f"Waiting for agent {agent_id} to create sockets...")
        socket_ready = False

        try:
            for attempt in range(30):  # Wait up to 30 seconds
                time.sleep(1)

                # Check if process is still alive
                if process.poll() is not None:
                    # Agent died before creating sockets
                    # Read last 100 lines of log to understand why
                    try:
                        with open(log_file_path, 'r') as f:
                            log_lines = f.readlines()[-100:]
                            logger.error(f"Agent {agent_id} startup failure log:\n{''.join(log_lines)}")
                    except Exception:
                        pass

                    raise RuntimeError(
                        f"Agent process died during startup (exit code {process.returncode}). "
                        f"Check logs: {log_file_path}"
                    )

                # Check if all sockets exist
                if socket_allocator.validate_socket_availability(sockets):
                    socket_ready = True
                    logger.info(f"Agent {agent_id} sockets ready after {attempt + 1}s")
                    break

            if not socket_ready:
                logger.error(f"Agent {agent_id} failed to create sockets within 30s")
                # Kill process if still running
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                raise RuntimeError(f"Agent {agent_id} failed to create sockets within 30s")

        except Exception as e:
            # ⚠️ CLEANUP ON FAILURE: Remove socket directory if agent startup failed
            # Prevents orphaned directories that block future deployments
            logger.error(f"Agent {agent_id} startup failed: {e}")
            logger.info(f"Cleaning up socket directory: {sockets.base_dir}")

            try:
                socket_allocator.cleanup(sockets)
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup socket directory: {cleanup_error}")

            # Re-raise original error
            raise

    return process
```

### 3.3 Test Requirements

**New Test File: `tests/test_supervisor_process_manager_socket.py`**

```python
import pytest
import os
from unittest.mock import Mock, patch, MagicMock
from pixell_runtime.supervisor.process_manager import ProcessManager
from pixell_runtime.supervisor.models import Ports

@pytest.fixture
def mock_subprocess():
    """Mock subprocess.Popen."""
    with patch('subprocess.Popen') as mock:
        mock_process = Mock()
        mock_process.pid = 12345
        mock.return_value = mock_process
        yield mock

@pytest.fixture
def mock_log_file(tmp_path):
    """Create temporary log file."""
    log_file = tmp_path / "agent.log"
    return str(log_file)


class TestProcessManagerSocket:
    """Test ProcessManager with socket mode (stateless)."""

    def test_spawn_agent_port_mode(self, mock_subprocess, mock_log_file):
        """Test spawning agent in port mode (legacy)."""
        manager = ProcessManager()

        ports = Ports(rest=63000, a2a=60000, ui=65000)
        process = manager.spawn_agent(
            agent_id="test-agent",
            package_path="/tmp/package",
            ports=ports,
            socket_mode=False,
            environment={},
            user="agent_test",
            log_file_path=mock_log_file
        )

        # Verify process spawned
        assert process.pid == 12345

        # Verify environment variables
        call_args = mock_subprocess.call_args
        env = call_args[1]['env']
        assert env['SOCKET_MODE'] == 'false'
        assert env['REST_PORT'] == '63000'
        assert env['A2A_PORT'] == '60000'
        assert env['UI_PORT'] == '65000'
        assert 'REST_SOCKET' not in env

    def test_spawn_agent_socket_mode(self, mock_subprocess, mock_log_file):
        """Test spawning agent in socket mode (paths computed from agent_id)."""
        manager = ProcessManager()

        agent_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"
        process = manager.spawn_agent(
            agent_id=agent_id,
            package_path="/tmp/package",
            ports=None,
            socket_mode=True,  # Socket paths computed internally
            environment={},
            user="agent_test",
            log_file_path=mock_log_file
        )

        # Verify process spawned
        assert process.pid == 12345

        # Verify environment variables
        call_args = mock_subprocess.call_args
        env = call_args[1]['env']
        assert env['SOCKET_MODE'] == 'true'

        # Verify socket paths contain agent short ID
        assert 'agent_4906eeb7' in env['REST_SOCKET']
        assert 'rest.sock' in env['REST_SOCKET']
        assert 'agent_4906eeb7' in env['A2A_SOCKET']
        assert 'a2a.sock' in env['A2A_SOCKET']
        assert 'agent_4906eeb7' in env['UI_SOCKET']
        assert 'ui.sock' in env['UI_SOCKET']

        assert 'REST_PORT' not in env

    def test_spawn_agent_port_mode_without_ports(self, mock_subprocess, mock_log_file):
        """Test that port mode without ports raises error."""
        manager = ProcessManager()

        with pytest.raises(ValueError, match="Port mode requires ports"):
            manager.spawn_agent(
                agent_id="test-agent",
                package_path="/tmp/package",
                ports=None,
                socket_mode=False,  # Port mode but no ports
                environment={},
                user="agent_test",
                log_file_path=mock_log_file
            )

    def test_spawn_agent_socket_mode_with_ports(self, mock_subprocess, mock_log_file):
        """Test that socket mode with ports raises error."""
        manager = ProcessManager()

        ports = Ports(rest=63000, a2a=60000, ui=65000)

        with pytest.raises(ValueError, match="should not specify ports"):
            manager.spawn_agent(
                agent_id="test-agent",
                package_path="/tmp/package",
                ports=ports,  # Shouldn't specify ports in socket mode
                socket_mode=True,
                environment={},
                user="agent_test",
                log_file_path=mock_log_file
            )

    def test_spawn_agent_environment_inheritance(self, mock_subprocess, mock_log_file):
        """Test that agent inherits environment variables."""
        manager = ProcessManager()

        ports = Ports(rest=63000, a2a=60000, ui=65000)
        custom_env = {
            "API_KEY": "test-key-123",
            "DEBUG": "true"
        }

        manager.spawn_agent(
            agent_id="test-agent",
            package_path="/tmp/package",
            ports=ports,
            socket_mode=False,
            environment=custom_env,
            user="agent_test",
            log_file_path=mock_log_file
        )

        call_args = mock_subprocess.call_args
        env = call_args[1]['env']
        assert env['API_KEY'] == 'test-key-123'
        assert env['DEBUG'] == 'true'
        assert env['AGENT_APP_ID'] == 'test-agent'

    def test_socket_paths_computed_deterministically(self, mock_subprocess, mock_log_file):
        """Test that socket paths are computed deterministically from agent_id."""
        manager = ProcessManager()

        agent_id = "test-agent-1234"

        # Spawn twice with same agent_id
        process1 = manager.spawn_agent(
            agent_id=agent_id,
            package_path="/tmp/package",
            socket_mode=True,
            environment={},
            user="agent_test",
            log_file_path=mock_log_file
        )

        call_args1 = mock_subprocess.call_args
        env1 = call_args1[1]['env']

        process2 = manager.spawn_agent(
            agent_id=agent_id,
            package_path="/tmp/package",
            socket_mode=True,
            environment={},
            user="agent_test",
            log_file_path=mock_log_file
        )

        call_args2 = mock_subprocess.call_args
        env2 = call_args2[1]['env']

        # Verify paths are identical (deterministic)
        assert env1['REST_SOCKET'] == env2['REST_SOCKET']
        assert env1['A2A_SOCKET'] == env2['A2A_SOCKET']
        assert env1['UI_SOCKET'] == env2['UI_SOCKET']
```

### 3.4 Validation Criteria (Must Pass All)

- [ ] New test file passes: `pytest tests/test_supervisor_process_manager_socket.py -v`
- [ ] Port mode tests pass (backward compatibility)
- [ ] Socket mode tests pass
- [ ] Validation tests (errors) pass
- [ ] All existing process_manager tests pass: `pytest tests/test_supervisor_process_manager.py -v`
- [ ] Integration test: Actually spawn a test process in both modes
- [ ] Code review approved

### 3.5 Rollback Procedure

```bash
# If Phase 3 fails:
git checkout HEAD -- src/pixell_runtime/supervisor/process_manager.py
git checkout HEAD -- tests/test_supervisor_process_manager_socket.py
pytest tests/test_supervisor_process_manager.py  # Verify rollback
```

---

## PHASE 4: RUNTIME CONFIGURATION - AGENT STARTUP

**Duration:** 1-2 weeks
**Risk Level:** 🔴 HIGH - Agent entry point changes
**Goal:** Update agent runtime to bind to sockets instead of ports

### 4.1 Objectives

- [ ] Update `runtime_config.py` to validate socket configuration
- [ ] Update `main.py` to bind uvicorn to Unix sockets
- [ ] Update `three_surface/runtime.py` for socket orchestration
- [ ] Update `a2a/server.py` for gRPC socket binding
- [ ] Test actual socket binding (not just mocked)

### 4.2 Files to Modify

**(See socket-refactor-impact-analysis.md files 7-10 for complete implementation)**

**File: `src/pixell_runtime/main.py`**

```python
import os
import grp
from pathlib import Path
import uvicorn

# Read configuration from environment
SOCKET_MODE = os.getenv("SOCKET_MODE", "false").lower() == "true"

if SOCKET_MODE:
    # Socket mode
    REST_SOCKET = os.getenv("REST_SOCKET")
    if not REST_SOCKET:
        raise ValueError("SOCKET_MODE=true requires REST_SOCKET environment variable")

    socket_path = Path(REST_SOCKET)

    # ⚠️ CRITICAL: Remove old socket if exists (from crashed agent)
    if socket_path.exists():
        socket_path.unlink()

    # Bind uvicorn to Unix socket
    uvicorn.run(
        app,
        uds=str(socket_path),  # Unix domain socket
        log_level="info"
    )

    # ⚠️ CRITICAL: Set socket permissions AFTER binding
    # Must allow nginx to read/write socket
    if socket_path.exists():
        try:
            # Set permissions to 660 (rw-rw----)
            os.chmod(socket_path, 0o660)

            # Set group to nginx
            nginx_gid = grp.getgrnam('nginx').gr_gid
            os.chown(socket_path, os.getuid(), nginx_gid)

            print(f"Socket permissions set: 660, group=nginx - {socket_path}")
        except Exception as e:
            print(f"Warning: Failed to set socket permissions: {e}")
            # Continue anyway - might work if directory perms are correct

else:
    # Port mode (legacy)
    REST_PORT = int(os.getenv("REST_PORT", 63000))
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=REST_PORT,
        log_level="info"
    )
```

**File: `src/pixell_runtime/a2a/server.py`**

```python
import os
import grp
from pathlib import Path
import grpc
from concurrent import futures

# Read configuration
SOCKET_MODE = os.getenv("SOCKET_MODE", "false").lower() == "true"

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))

    # Add servicers...
    agent_pb2_grpc.add_AgentServiceServicer_to_server(AgentServicer(), server)

    if SOCKET_MODE:
        # Socket mode
        A2A_SOCKET = os.getenv("A2A_SOCKET")
        if not A2A_SOCKET:
            raise ValueError("SOCKET_MODE=true requires A2A_SOCKET environment variable")

        socket_path = Path(A2A_SOCKET)

        # ⚠️ CRITICAL: Remove old socket if exists
        if socket_path.exists():
            socket_path.unlink()

        # Bind to Unix socket (note the "unix:" prefix)
        server.add_insecure_port(f"unix:{socket_path}")

        # ⚠️ CRITICAL: Set socket permissions AFTER binding
        # Wait briefly for gRPC to create the socket file
        import time
        time.sleep(0.1)

        if socket_path.exists():
            try:
                # Set permissions to 660 (rw-rw----)
                os.chmod(socket_path, 0o660)

                # Set group to nginx
                nginx_gid = grp.getgrnam('nginx').gr_gid
                os.chown(socket_path, os.getuid(), nginx_gid)

                print(f"gRPC socket permissions set: 660, group=nginx - {socket_path}")
            except Exception as e:
                print(f"Warning: Failed to set socket permissions: {e}")

        print(f"gRPC server listening on unix:{socket_path}")

    else:
        # Port mode (legacy)
        A2A_PORT = int(os.getenv("A2A_PORT", 60000))
        server.add_insecure_port(f"0.0.0.0:{A2A_PORT}")
        print(f"gRPC server listening on 0.0.0.0:{A2A_PORT}")

    server.start()
    server.wait_for_termination()
```

**Critical Changes:**
1. `runtime_config.py` - Add socket validation
2. `main.py` - Bind uvicorn to Unix socket + set permissions 660
3. `three_surface/runtime.py` - Start all surfaces on sockets
4. `a2a/server.py` - Bind gRPC to socket + set permissions 660

### 4.3 Test Requirements

**Integration Tests (must actually create sockets):**

```python
# tests/test_runtime_socket_integration.py

import pytest
import socket
import time
import subprocess
from pathlib import Path

@pytest.fixture
def temp_socket_dir(tmp_path):
    """Create temporary socket directory."""
    socket_dir = tmp_path / "sockets" / "agent_test"
    socket_dir.mkdir(parents=True)
    yield socket_dir

def test_rest_server_socket_binding(temp_socket_dir):
    """Test that REST server binds to Unix socket."""
    rest_socket = temp_socket_dir / "rest.sock"

    # Start agent in socket mode (subprocess)
    env = {
        "SOCKET_MODE": "true",
        "REST_SOCKET": str(rest_socket),
        "AGENT_APP_ID": "test-agent",
        "MULTIPLEXED": "false"
    }

    process = subprocess.Popen(
        ["python", "-m", "pixell_runtime"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    try:
        # Wait for socket to be created
        for _ in range(10):
            if rest_socket.exists():
                break
            time.sleep(0.5)

        # Verify socket exists
        assert rest_socket.exists()
        assert rest_socket.is_socket()

        # Verify can connect to socket
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.connect(str(rest_socket))
        client.close()

    finally:
        process.terminate()
        process.wait()

# ... more integration tests ...
```

### 4.4 Validation Criteria (Must Pass All)

- [ ] Unit tests pass for all modified files
- [ ] Integration test: REST server binds to socket
- [ ] Integration test: gRPC server binds to socket
- [ ] Integration test: Socket permissions are correct (660)
- [ ] Integration test: Can send HTTP request via socket
- [ ] Integration test: Can send gRPC request via socket
- [ ] Port mode still works (backward compatibility)
- [ ] Code review approved

### 4.5 Rollback Procedure

```bash
# If Phase 4 fails:
git checkout HEAD -- src/pixell_runtime/core/runtime_config.py
git checkout HEAD -- src/pixell_runtime/main.py
git checkout HEAD -- src/pixell_runtime/three_surface/runtime.py
git checkout HEAD -- src/pixell_runtime/a2a/server.py
pytest tests/  # Verify rollback
```

---

## PHASE 5: API & ORCHESTRATION - SUPERVISOR

**Duration:** 1 week
**Risk Level:** 🟡 MEDIUM - Supervisor coordination logic
**Goal:** Update SupervisorState to orchestrate socket deployments

### 5.1 Objectives

- [ ] Update `state.py` to support hybrid mode (port + socket)
- [ ] Update `server.py` health check to report socket capacity
- [ ] Add socket cleanup to delete flow
- [ ] Test hybrid deployments (some port, some socket agents)

### 5.2 Files to Modify

**(See socket-refactor-impact-analysis.md file 5 and 6 for complete implementation)**

**Critical Changes:**
1. `state.py` - Add socket_allocator, hybrid deploy() logic
2. `server.py` - Update /health endpoint, agent status endpoint, **add socket metrics**

**File: `src/pixell_runtime/supervisor/server.py`**

```python
# Add socket monitoring endpoints

@app.get("/health")
def health_check():
    """
    Health check endpoint with socket mode metrics.

    Returns capacity for both port and socket modes.

    ⚠️ CRITICAL: ALB health checks proxy to this endpoint!
    This endpoint MUST return 503 if socket agents exist but none are reachable.
    This ensures ALB catches permission errors and routing failures.
    """
    agents = state.list_agents()

    # Count agents by mode
    port_mode_count = sum(1 for a in agents if not a.socket_mode)
    socket_mode_count = sum(1 for a in agents if a.socket_mode)

    # ⚠️ CRITICAL: If socket agents exist, verify at least 1 is reachable
    # This prevents ALB from routing traffic when all sockets are broken
    socket_errors = 0
    socket_reachable_count = 0
    if socket_mode_count > 0:
        socket_allocator = SocketAllocator()

        # Check first 5 socket agents (don't check all - too slow for health check)
        for agent in [a for a in agents if a.socket_mode][:5]:
            try:
                sockets = socket_allocator.allocate(agent.agent_id)
                if socket_allocator.validate_socket_availability(sockets):
                    socket_reachable_count += 1
                else:
                    socket_errors += 1
            except Exception:
                socket_errors += 1

        # ⚠️ CRITICAL: Return 503 if socket agents exist but NONE are reachable
        # This makes ALB mark target unhealthy and stop routing traffic
        if socket_reachable_count == 0:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unhealthy",
                    "reason": "no_socket_agents_reachable",
                    "total_agents": len(agents),
                    "socket_mode_agents": socket_mode_count,
                    "socket_errors": socket_errors,
                    "socket_reachable": 0
                }
            )

    return {
        "status": "healthy",
        "total_agents": len(agents),
        "port_mode_agents": port_mode_count,
        "socket_mode_agents": socket_mode_count,
        "port_capacity_remaining": 200 - port_mode_count,
        "socket_capacity_remaining": 1000 - socket_mode_count,  # Theoretical
        "socket_permission_errors": socket_errors,
        "socket_reachable": socket_reachable_count  # New metric
    }


@app.get("/agents/sockets/status")
def socket_status():
    """
    Detailed socket status for all socket-mode agents.

    Returns per-agent socket availability and permissions.
    """
    agents = state.list_agents()
    socket_agents = [a for a in agents if a.socket_mode]

    socket_allocator = SocketAllocator()
    results = []

    for agent in socket_agents:
        try:
            sockets = socket_allocator.allocate(agent.agent_id)

            # Check socket availability
            sockets_exist = socket_allocator.validate_socket_availability(sockets)

            # Check permissions
            import stat
            permissions_ok = True
            permission_details = {}

            if sockets_exist:
                for name, sock_path in [("rest", sockets.rest), ("a2a", sockets.a2a), ("ui", sockets.ui)]:
                    st = sock_path.stat()
                    mode = oct(st.st_mode)[-3:]
                    permission_details[name] = {
                        "mode": mode,
                        "uid": st.st_uid,
                        "gid": st.st_gid,
                        "expected_mode": "660"
                    }
                    if mode != "660":
                        permissions_ok = False

            results.append({
                "agent_id": agent.agent_id,
                "status": agent.status,
                "sockets_exist": sockets_exist,
                "permissions_ok": permissions_ok,
                "socket_paths": {
                    "rest": str(sockets.rest),
                    "a2a": str(sockets.a2a),
                    "ui": str(sockets.ui)
                },
                "permission_details": permission_details if sockets_exist else None
            })

        except Exception as e:
            results.append({
                "agent_id": agent.agent_id,
                "status": "error",
                "error": str(e)
            })

    return {
        "total_socket_agents": len(socket_agents),
        "agents": results
    }
```

### 5.3 Supervisor Startup: Orphaned Agent Detection ⭐ NEW

**⚠️ CRITICAL SHOWSTOPPER FIX**

**Problem:** When supervisor restarts (systemctl restart, crash, or EC2 reboot), running agents become "orphaned":
- Agent processes still running but supervisor has no record
- Database shows agents as "running" but supervisor can't manage them
- Socket directories persist but supervisor doesn't know about them
- New deploy attempts fail because sockets already exist

**Solution:** On supervisor startup, scan for existing agents and reconcile with database state.

**File: `src/pixell_runtime/supervisor/state.py`**

Add startup reconciliation method:

```python
class SupervisorState:
    def __init__(self):
        self.db = Database()
        self.process_manager = ProcessManager()
        self.port_allocator = PortAllocator()
        self.socket_allocator = SocketAllocator()

        # ⚠️ CRITICAL: Reconcile orphaned agents on startup
        self.reconcile_agents_on_startup()

    def reconcile_agents_on_startup(self):
        """
        Scan for orphaned agents and reconcile with database state.

        This handles:
        1. Supervisor restart - agents still running but not tracked
        2. EC2 reboot - all agents dead, clean up stale records
        3. Crash recovery - partial state restoration

        ⚠️ CRITICAL: Must run BEFORE accepting any new deploy requests!
        """
        logger.info("Starting agent reconciliation...")

        # Get all agents from database that claim to be "running"
        agents = self.db.list_agents()
        running_agents = [a for a in agents if a.status == "running"]

        orphaned_count = 0
        cleaned_count = 0

        for agent in running_agents:
            agent_id = agent.agent_id
            pid = agent.pid

            # Check 1: Is process still alive?
            process_alive = False
            if pid:
                try:
                    os.kill(pid, 0)  # Signal 0 = check if process exists
                    process_alive = True
                except OSError:
                    process_alive = False

            # Check 2: Do sockets still exist? (for socket mode agents)
            sockets_exist = False
            if agent.socket_mode:
                try:
                    sockets = self.socket_allocator.allocate(agent_id)
                    sockets_exist = self.socket_allocator.validate_socket_availability(sockets)
                except Exception:
                    sockets_exist = False

            # Decision tree
            if not process_alive:
                # Agent died - clean up database record
                logger.warning(f"Agent {agent_id} marked running but process {pid} dead - cleaning up")
                self.db.update_agent_status(agent_id, "failed")

                # Clean up socket directory if it exists
                if agent.socket_mode and sockets_exist:
                    try:
                        sockets = self.socket_allocator.allocate(agent_id)
                        self.socket_allocator.cleanup(sockets)
                        logger.info(f"Cleaned up orphaned socket directory for {agent_id}")
                    except Exception as e:
                        logger.error(f"Failed to cleanup sockets for {agent_id}: {e}")

                cleaned_count += 1

            elif process_alive:
                # Agent still running - mark as orphaned and track it
                logger.warning(f"Agent {agent_id} is orphaned (PID {pid}) - supervisor will track it")

                # TODO: In future, re-attach to process (complex)
                # For now, just track it so we know it exists
                # This prevents deploying duplicate agents with same ID
                orphaned_count += 1

                # Verify sockets exist for socket mode
                if agent.socket_mode and not sockets_exist:
                    logger.error(f"Orphaned agent {agent_id} running but sockets missing - marking failed")
                    self.db.update_agent_status(agent_id, "failed")
                    # Kill the orphaned process since it's not functional
                    try:
                        os.kill(pid, 9)  # SIGKILL
                    except Exception as e:
                        logger.error(f"Failed to kill orphaned agent {pid}: {e}")

        logger.info(
            f"Reconciliation complete: {len(running_agents)} running agents, "
            f"{orphaned_count} orphaned (tracked), {cleaned_count} cleaned up"
        )

        # Return stats for monitoring
        return {
            "total_running": len(running_agents),
            "orphaned": orphaned_count,
            "cleaned": cleaned_count
        }
```

**File: `src/pixell_runtime/supervisor/server.py`**

Add startup reconciliation endpoint for monitoring:

```python
@app.on_event("startup")
async def startup_event():
    """
    Run reconciliation on supervisor startup.
    """
    logger.info("Supervisor starting up...")

    # ⚠️ CRITICAL: Reconcile orphaned agents BEFORE accepting requests
    reconcile_stats = state.reconcile_agents_on_startup()

    logger.info(f"Startup reconciliation: {reconcile_stats}")

@app.get("/admin/reconcile")
def manual_reconcile():
    """
    Manually trigger agent reconciliation.

    Use this if you suspect orphaned agents exist.
    """
    stats = state.reconcile_agents_on_startup()
    return {
        "status": "success",
        "reconciliation": stats
    }
```

**Benefits:**
1. **Prevents duplicate agents** - Can't deploy agent with same ID if process still running
2. **Cleans up stale records** - Marks dead agents as "failed"
3. **Cleans up stale sockets** - Removes socket directories for dead agents
4. **Tracks orphaned agents** - Supervisor knows about running agents even after restart
5. **Idempotent** - Safe to run multiple times

**Alternative Approaches Considered:**

1. **Systemd socket activation** - Too complex, doesn't handle port mode
2. **Supervisor creates PID file** - Doesn't handle EC2 reboot
3. **Database stores socket paths** - Violates stateless principle
4. **Ignore orphaned agents** - ❌ Causes deploy failures and confusion

### 5.3b Deploy Idempotency: Prevent Duplicate Deploys ⭐ NEW

**⚠️ CRITICAL SHOWSTOPPER FIX**

**Problem:** When PAC sends duplicate deploy requests (network retry, timeout retry, user clicks twice):
- Duplicate agents get created with same agent_id
- Second agent fails to bind to socket (address already in use)
- Database has conflicting records
- Port allocator assigns same ports twice (port mode)
- Socket allocator creates conflicting socket directories

**Solution:** Implement "create if not exists" idempotency in deploy flow.

**File: `src/pixell_runtime/supervisor/state.py`**

Update deploy method with idempotency:

```python
def deploy(self, agent_id: str, package_url: str, socket_mode: bool = False,
           ports: Optional[Ports] = None, environment: dict = None) -> dict:
    """
    Deploy agent with idempotency.

    ⚠️ CRITICAL: This method MUST be idempotent!
    If called twice with same agent_id:
    - First call: Deploy agent normally
    - Second call: Return existing agent status (don't fail, don't deploy again)

    This prevents duplicate deploys from network retries, timeouts, or user error.
    """
    # ⚠️ IDEMPOTENCY CHECK: Is agent already deployed?
    try:
        existing_agent = self.db.get_agent(agent_id)

        if existing_agent:
            # Agent already exists

            if existing_agent.status == "running":
                # Agent running - return existing status (IDEMPOTENT)
                logger.info(f"Agent {agent_id} already running - returning existing status")
                return {
                    "agent_id": agent_id,
                    "status": "running",
                    "pid": existing_agent.pid,
                    "socket_mode": existing_agent.socket_mode,
                    "message": "Agent already deployed (idempotent response)"
                }

            elif existing_agent.status in ["deploying", "starting"]:
                # Agent currently being deployed by another request
                # Wait briefly and return status
                logger.warning(f"Agent {agent_id} currently deploying - waiting...")
                time.sleep(2)  # Wait for concurrent deploy to finish

                # Re-check status
                existing_agent = self.db.get_agent(agent_id)
                return {
                    "agent_id": agent_id,
                    "status": existing_agent.status,
                    "pid": existing_agent.pid if hasattr(existing_agent, 'pid') else None,
                    "socket_mode": existing_agent.socket_mode,
                    "message": "Agent deployment in progress"
                }

            elif existing_agent.status in ["failed", "stopped"]:
                # Agent failed or stopped - clean up and redeploy
                logger.info(f"Agent {agent_id} in state {existing_agent.status} - cleaning up and redeploying")

                # Clean up old resources
                if existing_agent.socket_mode:
                    try:
                        sockets = self.socket_allocator.allocate(agent_id)
                        self.socket_allocator.cleanup(sockets)
                    except Exception as e:
                        logger.warning(f"Failed to cleanup old sockets: {e}")

                # Continue with normal deployment below
                # (Don't return - fall through to deployment logic)

    except Exception as e:
        # Agent doesn't exist - continue with deployment
        logger.debug(f"Agent {agent_id} not found in database - proceeding with deployment")

    # ⚠️ ATOMIC DEPLOY: Mark agent as "deploying" BEFORE spawning process
    # This prevents concurrent deploys from racing
    self.db.create_agent(
        agent_id=agent_id,
        status="deploying",  # Not "running" yet!
        socket_mode=socket_mode,
        package_url=package_url
    )

    try:
        # Download package from S3
        package_path = self._download_package(package_url)

        # Allocate resources
        if socket_mode:
            # Socket mode - compute socket paths
            sockets = self.socket_allocator.allocate(agent_id)
            # Create directory and validate permissions
            agent_user = self._get_agent_user(agent_id)
            self.socket_allocator.create_agent_directory(sockets, agent_user, agent_id)
        else:
            # Port mode - allocate ports
            if not ports:
                ports = self.port_allocator.allocate()

        # Spawn agent process
        process = self.process_manager.spawn_agent(
            agent_id=agent_id,
            package_path=package_path,
            ports=ports if not socket_mode else None,
            socket_mode=socket_mode,
            environment=environment or {},
            user=agent_user,
            log_file_path=f"/var/log/pixell-agents/{agent_id}.log"
        )

        # ⚠️ UPDATE DATABASE: Mark as "running" only after successful spawn
        self.db.update_agent(
            agent_id=agent_id,
            status="running",
            pid=process.pid
        )

        logger.info(f"Agent {agent_id} deployed successfully (PID {process.pid})")

        return {
            "agent_id": agent_id,
            "status": "running",
            "pid": process.pid,
            "socket_mode": socket_mode
        }

    except Exception as e:
        # ⚠️ MARK AS FAILED: Update database on error
        logger.error(f"Agent {agent_id} deployment failed: {e}")
        self.db.update_agent_status(agent_id, "failed")

        # Clean up resources
        if socket_mode:
            try:
                sockets = self.socket_allocator.allocate(agent_id)
                self.socket_allocator.cleanup(sockets)
            except Exception as cleanup_error:
                logger.error(f"Failed to cleanup after deployment failure: {cleanup_error}")

        # Re-raise error
        raise
```

**Key Idempotency Behaviors:**

| Scenario | First Call | Second Call (duplicate) | Result |
|----------|-----------|------------------------|---------|
| **New agent** | Deploy normally | Return existing status | ✅ Idempotent |
| **Agent running** | N/A | Return existing status | ✅ Idempotent |
| **Agent deploying** | Deploy normally | Wait 2s, return status | ✅ Idempotent |
| **Agent failed** | N/A | Clean up, redeploy | ✅ Idempotent |
| **Concurrent deploys** | First wins | Second waits | ✅ Idempotent |

**Benefits:**
1. **Network retry safe** - PAC can retry deploy without causing errors
2. **Timeout safe** - If PAC times out, second request won't conflict
3. **User error safe** - User clicking "deploy" twice won't break
4. **Concurrent safe** - Multiple PAC instances won't conflict
5. **Atomic** - Database marked "deploying" BEFORE spawn (prevents race)

**Alternative Approaches Considered:**

1. **Idempotency token** - Too complex, requires token management
2. **Database unique constraint** - ✅ Already have this (agent_id primary key)
3. **Distributed lock** - Too complex for single-instance supervisor
4. **Fail on duplicate** - ❌ Not idempotent, causes PAC retry failures

### 5.4 Test Requirements

```python
# tests/test_supervisor_state_hybrid.py

def test_hybrid_mode_deploy_port_agent():
    """Test deploying agent in port mode."""
    # ... test port deployment ...

def test_hybrid_mode_deploy_socket_agent():
    """Test deploying agent in socket mode."""
    # ... test socket deployment ...

def test_hybrid_mode_both_agents_running():
    """Test that both port and socket agents can run simultaneously."""
    # Deploy agent 1 in port mode
    # Deploy agent 2 in socket mode
    # Verify both running
    # Verify correct resources allocated

def test_delete_agent_socket_cleanup():
    """Test that socket directory is cleaned up when agent deleted."""
    # ... test cleanup ...

def test_reconcile_orphaned_agents():
    """Test orphaned agent detection on startup."""
    # 1. Start supervisor
    # 2. Deploy agent
    # 3. Simulate supervisor restart (but agent still running)
    # 4. Verify reconciliation detects orphaned agent
    # 5. Verify agent marked as orphaned but not killed
    # 6. Verify can't deploy duplicate agent

def test_reconcile_dead_agents():
    """Test cleanup of dead agents on startup."""
    # 1. Deploy agent
    # 2. Kill agent process
    # 3. Simulate supervisor restart
    # 4. Verify reconciliation marks agent as failed
    # 5. Verify socket directory cleaned up

def test_reconcile_missing_sockets():
    """Test cleanup of agents with missing sockets."""
    # 1. Deploy socket mode agent
    # 2. Delete socket directory
    # 3. Simulate supervisor restart
    # 4. Verify reconciliation kills agent and marks failed

def test_deploy_idempotency_already_running():
    """Test deploying agent that's already running."""
    # 1. Deploy agent successfully
    # 2. Call deploy again with same agent_id
    # 3. Verify returns existing status (doesn't fail)
    # 4. Verify only 1 agent process running

def test_deploy_idempotency_concurrent():
    """Test concurrent deploy requests."""
    # 1. Start deploy request (mock to take 5 seconds)
    # 2. Start second deploy request immediately
    # 3. Verify second request waits
    # 4. Verify only 1 agent created
    # 5. Verify both requests return success

def test_deploy_idempotency_failed_agent():
    """Test redeploying failed agent."""
    # 1. Deploy agent
    # 2. Mark agent as failed
    # 3. Deploy again with same agent_id
    # 4. Verify old resources cleaned up
    # 5. Verify new agent deployed successfully
```

### 5.5 Validation Criteria (Must Pass All)

- [ ] All state.py tests pass
- [ ] All server.py tests pass
- [ ] Hybrid mode test passes (port + socket agents)
- [ ] Socket cleanup test passes
- [ ] Health endpoint shows both port and socket capacity
- [ ] No regressions in existing supervisor tests
- [ ] Code review approved

### 5.5 Rollback Procedure

```bash
# If Phase 5 fails:
git checkout HEAD -- src/pixell_runtime/supervisor/state.py
git checkout HEAD -- src/pixell_runtime/supervisor/server.py
pytest tests/test_supervisor*.py  # Verify rollback
```

---

## PHASE 6: INTEGRATION & MIGRATION

**Duration:** 2-3 weeks
**Risk Level:** 🔴 CRITICAL - End-to-end testing and migration
**Goal:** Full E2E testing, deploy to staging, production rollout plan

### 6.1 Objectives

- [ ] E2E test: Deploy real agent in socket mode
- [ ] E2E test: HTTP requests via Nginx proxy
- [ ] E2E test: gRPC requests via Nginx proxy
- [ ] Load test: 100 concurrent requests
- [ ] Deploy to staging environment
- [ ] Create production migration runbook

### 6.2 Test Requirements

**End-to-End Tests:**

```python
# tests/test_e2e_socket_deployment.py

def test_e2e_deploy_socket_agent():
    """Test full deployment flow with socket mode."""
    # 1. Create deployment request (socket mode)
    # 2. Call supervisor API
    # 3. Verify agent starts
    # 4. Verify sockets created
    # 5. Send HTTP request via socket
    # 6. Verify response
    # 7. Delete agent
    # 8. Verify cleanup

def test_e2e_nginx_rest_routing():
    """Test REST requests route through Nginx to socket."""
    # Requires Nginx running on test instance
    # ... test ...

def test_e2e_nginx_grpc_routing():
    """Test gRPC requests route through Nginx to socket."""
    # Requires Nginx running on test instance
    # ... test ...

def test_e2e_load_test_socket_mode():
    """Load test with 100 concurrent requests."""
    # Use locust or similar
    # ... test ...
```

**Migration Tests:**

```python
# tests/test_migration_port_to_socket.py

def test_migrate_port_agent_to_socket():
    """Test migrating existing port-based agent to socket mode."""
    # 1. Deploy agent in port mode
    # 2. Stop agent
    # 3. Redeploy in socket mode
    # 4. Verify works correctly
    # 5. Verify old ports released

def test_rollback_socket_to_port():
    """Test rolling back socket agent to port mode."""
    # 1. Deploy agent in socket mode
    # 2. Simulate failure
    # 3. Rollback to port mode
    # 4. Verify works correctly
```

### 6.3 Validation Criteria (Must Pass All)

- [ ] All E2E tests pass
- [ ] All migration tests pass
- [ ] Load test: 100 concurrent requests, <500ms P99 latency
- [ ] Deployed to staging successfully
- [ ] 1 test agent running in staging (socket mode)
- [ ] 10 test agents running in staging (socket mode)
- [ ] Monitor for 3 days, no issues
- [ ] Production migration runbook reviewed and approved
- [ ] Rollback procedure tested and documented

### 6.4 Staging/Production Deployment - Week 1: Infrastructure Setup

**⚠️ CRITICAL: These steps MUST be done in order to avoid permission and network errors**

```bash
# ═══════════════════════════════════════════════════════════════════════
# STEP 1: Update EC2 Security Group (sg-0c13cfb5da4e67ea7)
# ═══════════════════════════════════════════════════════════════════════
# ⚠️ DO THIS FIRST - Otherwise ALB can't reach Nginx proxy ports

ssh ec2-user@18.119.137.118  # Production EC2 instance

# Allow ALB to reach Nginx proxy ports
aws ec2 authorize-security-group-ingress \
  --group-id sg-0c13cfb5da4e67ea7 \
  --ip-permissions \
    IpProtocol=tcp,FromPort=8080,ToPort=8080,UserIdGroupPairs=[{GroupId=sg-0f5b28ee64419e95d,Description="ALB to Nginx REST proxy"}] \
    IpProtocol=tcp,FromPort=50051,ToPort=50051,UserIdGroupPairs=[{GroupId=sg-0f5b28ee64419e95d,Description="ALB to Nginx gRPC proxy"}] \
    IpProtocol=tcp,FromPort=3000,ToPort=3000,UserIdGroupPairs=[{GroupId=sg-0f5b28ee64419e95d,Description="ALB to Nginx UI proxy"}]

# Verify security group rules added
aws ec2 describe-security-groups \
  --group-ids sg-0c13cfb5da4e67ea7 \
  --query 'SecurityGroups[0].IpPermissions[?FromPort==`8080` || FromPort==`50051` || FromPort==`3000`]'

# Expected output: Should show 3 rules for ports 8080, 50051, 3000


# ═══════════════════════════════════════════════════════════════════════
# STEP 2: Install and Configure Nginx
# ═══════════════════════════════════════════════════════════════════════

# Install Nginx
sudo yum update -y
sudo yum install -y nginx

# Verify nginx group exists
getent group nginx
# Expected: nginx:x:994:
# If not found, create it: sudo groupadd nginx

# Verify Nginx user exists
id nginx
# Expected: uid=995(nginx) gid=994(nginx) groups=994(nginx)

# Deploy Nginx configuration with socket-specific tuning
sudo tee /etc/nginx/conf.d/pixell-agents.conf > /dev/null <<'EOF'
# Pixell Agent Socket Proxy Configuration

# ⚠️ CRITICAL: Tuning for 1000+ agents
worker_processes auto;
worker_rlimit_nofile 32768;  # File descriptor limit per worker

events {
    worker_connections 16384;  # Support 16k concurrent connections
    use epoll;                 # Efficient event handling
}

http {
    # Connection limits
    client_max_body_size 100m;  # Large gRPC messages

    # Timeouts for long-running requests
    proxy_connect_timeout 60s;
    proxy_send_timeout 3600s;
    proxy_read_timeout 3600s;

    # HTTP/2 settings for gRPC
    http2_max_field_size 128k;
    http2_max_header_size 256k;

    # Health endpoint (for ALB health checks)
    server {
        listen 8080;
        listen 50051 http2;
        listen 3000;

        # ⚠️ CRITICAL: ALB health check MUST verify sockets work, not just Nginx!
        # Current implementation: Static 200 response (doesn't verify socket connectivity)
        # Improved implementation: Proxy to supervisor /health endpoint
        location /health {
            # Option 1: Static response (BASIC - only verifies Nginx is alive)
            # return 200 "healthy\n";
            # add_header Content-Type text/plain;

            # Option 2: Proxy to supervisor (RECOMMENDED - verifies full stack)
            proxy_pass http://127.0.0.1:9000/health;
            proxy_http_version 1.1;
            proxy_connect_timeout 5s;
            proxy_read_timeout 5s;

            # If supervisor is down, return 503 (unhealthy)
            # ALB will mark target unhealthy and stop routing traffic
        }

        # ⚠️ IMPORTANT: Supervisor /health endpoint must be enhanced (Phase 5)
        # Current: Returns basic status
        # Required: Must verify at least 1 socket agent is reachable (if any exist)
        #
        # Implementation in src/pixell_runtime/supervisor/server.py:
        # ```python
        # @app.get("/health")
        # def health_check():
        #     agents = state.list_agents()
        #     socket_agents = [a for a in agents if a.socket_mode]
        #
        #     # If socket agents exist, verify at least 1 is reachable
        #     if socket_agents:
        #         socket_allocator = SocketAllocator()
        #         reachable_count = 0
        #         for agent in socket_agents[:5]:  # Check first 5
        #             try:
        #                 sockets = socket_allocator.allocate(agent.agent_id)
        #                 if socket_allocator.validate_socket_availability(sockets):
        #                     reachable_count += 1
        #                     break  # At least 1 works
        #             except:
        #                 continue
        #
        #         if reachable_count == 0:
        #             return JSONResponse(
        #                 status_code=503,
        #                 content={"status": "unhealthy", "reason": "no_socket_agents_reachable"}
        #             )
        #
        #     return {"status": "healthy", "socket_agents": len(socket_agents)}
        # ```
        #
        # This ensures ALB health check catches:
        # - Permission errors (sockets exist but not readable by nginx)
        # - Missing sockets (agents crashed but not cleaned up)
        # - Supervisor down (proxy returns 502/503)
        }

        # REST API proxy (HTTP/1.1)
        location ~ ^/rest/(?<agent_hash>[^/]+)/(.*)$ {
            proxy_pass http://unix:/var/run/pixell-agents/agent_$agent_hash/rest.sock:/$2$is_args$args;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;

            # Retry if socket not ready
            proxy_next_upstream error timeout;
            proxy_next_upstream_tries 3;
            proxy_connect_timeout 5s;
        }

        # gRPC A2A proxy (HTTP/2)
        location ~ ^/a2a/(?<agent_hash>[^/]+)/(.*)$ {
            grpc_pass unix:/var/run/pixell-agents/agent_$agent_hash/a2a.sock;
            grpc_read_timeout 3600s;
            grpc_send_timeout 3600s;

            # HTTP/2 required for gRPC
            http2 on;
        }

        # UI proxy (HTTP/1.1)
        location ~ ^/ui/(?<agent_hash>[^/]+)/(.*)$ {
            proxy_pass http://unix:/var/run/pixell-agents/agent_$agent_hash/ui.sock:/$2$is_args$args;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
        }
    }
}
EOF

# Test Nginx configuration
sudo nginx -t
# Expected: nginx: configuration file /etc/nginx/nginx.conf test is successful

# Start Nginx
sudo systemctl enable nginx
sudo systemctl start nginx

# Verify Nginx is listening on proxy ports
netstat -tlnp | grep -E '(8080|50051|3000)'
# Expected output:
# tcp  0  0 0.0.0.0:8080   0.0.0.0:*  LISTEN  <pid>/nginx
# tcp  0  0 0.0.0.0:50051  0.0.0.0:*  LISTEN  <pid>/nginx
# tcp  0  0 0.0.0.0:3000   0.0.0.0:*  LISTEN  <pid>/nginx

# Test Nginx health endpoint
curl http://localhost:8080/health
# Expected: healthy

# Check Nginx worker connection limits
curl http://localhost:8080/nginx_status
# Verify worker_connections is 16384

# Check Nginx error log for any issues
sudo tail -f /var/log/nginx/error.log


# ═══════════════════════════════════════════════════════════════════════
# STEP 3: Create Base Socket Directory (Persistent Across Reboots)
# ═══════════════════════════════════════════════════════════════════════

# ⚠️ CRITICAL: /var/run is tmpfs (RAM-based, wiped on reboot)
# Must configure systemd-tmpfiles.d to recreate directory on boot

# Create systemd-tmpfiles.d configuration
sudo tee /etc/tmpfiles.d/pixell-agents.conf > /dev/null <<'EOF'
# Pixell Agent Socket Directory
# This ensures /var/run/pixell-agents is recreated on every boot
# Type Path                    Mode UID  GID  Age Argument
d      /var/run/pixell-agents  0755 root root -   -
EOF

# Apply tmpfiles configuration immediately (don't wait for reboot)
sudo systemd-tmpfiles --create /etc/tmpfiles.d/pixell-agents.conf

# Verify directory created
ls -la /var/run/ | grep pixell-agents
# Expected: drwxr-xr-x 2 root root 40 Nov 12 10:00 pixell-agents

# Test reboot persistence (simulate tmpfs clear)
sudo rm -rf /var/run/pixell-agents
sudo systemd-tmpfiles --create
ls -la /var/run/pixell-agents
# Expected: Directory recreated automatically


# ═══════════════════════════════════════════════════════════════════════
# STEP 3b: Configure Supervisor Service (File Descriptor Limits)
# ═══════════════════════════════════════════════════════════════════════

# ⚠️ CRITICAL: Increase file descriptor limits for supervisor
# With 500+ agents, supervisor needs to monitor many sockets
# Default ulimit (1024) is insufficient

# Update systemd service file
sudo tee /etc/systemd/system/pixell-supervisor.service > /dev/null <<'EOF'
[Unit]
Description=Pixell Agent Supervisor
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/pixell-agent-runtime
ExecStart=/usr/bin/python3.11 -m pixell_runtime.supervisor.server
Restart=always
RestartSec=10

# ⚠️ CRITICAL: File descriptor limits for socket mode
# Each agent uses ~3 sockets + logs + database connections
# 500 agents × 10 FDs per agent = 5000 FDs minimum
LimitNOFILE=65536

# Memory limit (optional)
MemoryLimit=4G

# Environment
Environment="PYTHONUNBUFFERED=1"
Environment="LOG_LEVEL=info"

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd to pick up new service file
sudo systemctl daemon-reload

# Verify service configuration
sudo systemctl cat pixell-supervisor.service | grep LimitNOFILE
# Expected: LimitNOFILE=65536

# Check current supervisor process FD limit (if running)
if pgrep -f pixell_runtime.supervisor; then
    sudo cat /proc/$(pgrep -f pixell_runtime.supervisor)/limits | grep "open files"
    # Should show: Max open files  65536  65536  files
fi


# ═══════════════════════════════════════════════════════════════════════
# STEP 4: Verify Agent Users Have nginx Group
# ═══════════════════════════════════════════════════════════════════════

# Check existing agent users
getent passwd | grep agent_

# For each agent user, verify nginx group membership
id agent_4906eeb7
# Expected: uid=... gid=... groups=...,994(nginx)

# If nginx group missing, add it:
sudo usermod -a -G nginx agent_4906eeb7

# Verify agent can write to /var/run/pixell-agents
sudo -u agent_4906eeb7 touch /var/run/pixell-agents/test
sudo rm /var/run/pixell-agents/test


# ═══════════════════════════════════════════════════════════════════════
# STEP 5: Create ALB Target Groups (via PAC)
# ═══════════════════════════════════════════════════════════════════════

# Create 3 shared target groups
aws elbv2 create-target-group \
  --name pixell-rest-proxy \
  --protocol HTTP \
  --port 8080 \
  --vpc-id vpc-0039e5988107ae565 \
  --health-check-enabled \
  --health-check-path /health \
  --health-check-interval-seconds 30 \
  --health-check-timeout-seconds 5 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3

aws elbv2 create-target-group \
  --name pixell-grpc-proxy \
  --protocol HTTP \
  --protocol-version HTTP2 \
  --port 50051 \
  --vpc-id vpc-0039e5988107ae565 \
  --health-check-enabled \
  --health-check-protocol HTTP \
  --health-check-path /health \
  --health-check-port 8080

aws elbv2 create-target-group \
  --name pixell-ui-proxy \
  --protocol HTTP \
  --port 3000 \
  --vpc-id vpc-0039e5988107ae565 \
  --health-check-enabled \
  --health-check-path /health \
  --health-check-interval-seconds 30


# ═══════════════════════════════════════════════════════════════════════
# STEP 5b: Configure ALB Idle Timeout for Long-Running gRPC Streams
# ═══════════════════════════════════════════════════════════════════════

# ⚠️ CRITICAL: ALB default idle timeout is 60s - kills long-running gRPC streams!
# gRPC A2A streams can run for hours (e.g., agent-to-agent real-time communication)
# Must increase idle_timeout to 3600s (1 hour) to prevent disconnections

# Get ALB ARN
ALB_ARN=$(aws elbv2 describe-load-balancers \
  --names pixell-runtime-alb \
  --query 'LoadBalancers[0].LoadBalancerArn' \
  --output text)

# Check current idle timeout
aws elbv2 describe-load-balancer-attributes \
  --load-balancer-arn $ALB_ARN \
  --query 'Attributes[?Key==`idle_timeout.timeout_seconds`].Value' \
  --output text
# Expected: 60 (default)

# Update idle timeout to 3600s (1 hour)
aws elbv2 modify-load-balancer-attributes \
  --load-balancer-arn $ALB_ARN \
  --attributes Key=idle_timeout.timeout_seconds,Value=3600

# Verify updated
aws elbv2 describe-load-balancer-attributes \
  --load-balancer-arn $ALB_ARN \
  --query 'Attributes[?Key==`idle_timeout.timeout_seconds`].Value' \
  --output text
# Expected: 3600

# ⚠️ NOTE: If you need even longer streams (>1 hour), either:
# 1. Increase idle_timeout further (max 4000s ~66 minutes)
# 2. Implement gRPC keepalive in agent code:
#    - client_keepalive_time_ms: 30000 (30s)
#    - client_keepalive_timeout_ms: 10000 (10s)
# This prevents ALB from considering connection idle

# Register EC2 instance to all 3 target groups
TG_REST_ARN=$(aws elbv2 describe-target-groups --names pixell-rest-proxy --query 'TargetGroups[0].TargetGroupArn' --output text)
TG_GRPC_ARN=$(aws elbv2 describe-target-groups --names pixell-grpc-proxy --query 'TargetGroups[0].TargetGroupArn' --output text)
TG_UI_ARN=$(aws elbv2 describe-target-groups --names pixell-ui-proxy --query 'TargetGroups[0].TargetGroupArn' --output text)

aws elbv2 register-targets --target-group-arn $TG_REST_ARN \
  --targets Id=i-09dcb7f387166efd0,Port=8080

aws elbv2 register-targets --target-group-arn $TG_GRPC_ARN \
  --targets Id=i-09dcb7f387166efd0,Port=50051

aws elbv2 register-targets --target-group-arn $TG_UI_ARN \
  --targets Id=i-09dcb7f387166efd0,Port=3000

# Wait for health checks to pass
aws elbv2 describe-target-health --target-group-arn $TG_REST_ARN
# Expected: "TargetHealth": {"State": "healthy"}

aws elbv2 describe-target-health --target-group-arn $TG_GRPC_ARN
aws elbv2 describe-target-health --target-group-arn $TG_UI_ARN


# ═══════════════════════════════════════════════════════════════════════
# STEP 6: Add ALB Listener Rules (LOW priority, catch-all)
# ═══════════════════════════════════════════════════════════════════════

# Get ALB listener ARN
ALB_ARN=$(aws elbv2 describe-load-balancers --names pixell-runtime-alb --query 'LoadBalancers[0].LoadBalancerArn' --output text)
LISTENER_ARN=$(aws elbv2 describe-listeners --load-balancer-arn $ALB_ARN --query 'Listeners[0].ListenerArn' --output text)

# Add wildcard rules with LOW priority (2000+)
# Existing per-agent rules have priority 1-1999
# These catch-all rules catch socket-mode agents

aws elbv2 create-rule \
  --listener-arn $LISTENER_ARN \
  --priority 2000 \
  --conditions Field=path-pattern,Values="/rest/*" \
  --actions Type=forward,TargetGroupArn=$TG_REST_ARN

aws elbv2 create-rule \
  --listener-arn $LISTENER_ARN \
  --priority 2001 \
  --conditions Field=path-pattern,Values="/a2a/*" \
  --actions Type=forward,TargetGroupArn=$TG_GRPC_ARN

aws elbv2 create-rule \
  --listener-arn $LISTENER_ARN \
  --priority 2002 \
  --conditions Field=path-pattern,Values="/ui/*" \
  --actions Type=forward,TargetGroupArn=$TG_UI_ARN

# Verify rules created
aws elbv2 describe-rules --listener-arn $LISTENER_ARN \
  --query 'Rules[?Priority==`2000` || Priority==`2001` || Priority==`2002`]'


# ═══════════════════════════════════════════════════════════════════════
# STEP 7: Test Infrastructure Before Deploying Agents
# ═══════════════════════════════════════════════════════════════════════

# Test Nginx health endpoint locally
curl http://localhost:8080/health
# Expected: healthy

# Test Nginx health endpoint from ALB
curl https://par.pixell.global/health
# Expected: Should fail (no /health rule at root)

# Test that Nginx is accessible from ALB (via target health)
aws elbv2 describe-target-health --target-group-arn $TG_REST_ARN
# Expected: State=healthy

# Check Nginx access logs
sudo tail -f /var/log/nginx/access.log
# Should see health check requests from ALB


# ═══════════════════════════════════════════════════════════════════════
# INFRASTRUCTURE SETUP COMPLETE ✅
# ═══════════════════════════════════════════════════════════════════════
# At this point:
# ✅ Security groups allow ALB → Nginx ports
# ✅ Nginx installed and listening on 8080, 50051, 3000
# ✅ 3 shared target groups created
# ✅ EC2 registered to target groups
# ✅ Health checks passing
# ✅ ALB listener rules ready for socket agents
# ✅ Socket base directory created
# ✅ Agent users have nginx group membership
#
# Now ready to deploy PAR code and test with socket agents
```

### 6.5 Week 2: Code Deployment and First Test Agent

```bash
# ═══════════════════════════════════════════════════════════════════════
# Deploy PAR Code with Socket Support
# ═══════════════════════════════════════════════════════════════════════

ssh ec2-user@18.119.137.118

# Pull latest code with socket support
cd /opt/pixell-agent-runtime
git fetch origin
git checkout feature/socket-deployment
git pull

# Install dependencies
pip install -r requirements.txt

# Restart supervisor (with socket support but default port mode)
sudo systemctl restart pixell-supervisor

# Verify supervisor started successfully
sudo systemctl status pixell-supervisor
curl http://localhost:9000/health
# Expected: {"status": "healthy", "port_capacity": 200, "socket_capacity": 1000}


# ═══════════════════════════════════════════════════════════════════════
# Deploy First Test Agent in Socket Mode
# ═══════════════════════════════════════════════════════════════════════

# Deploy test agent
curl -X POST http://localhost:9000/agents \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "test-socket-001-4906eeb7",
    "package_url": "s3://pixell-packages/test-agent.apkg",
    "socket_mode": true,
    "environment": {
      "LOG_LEVEL": "debug"
    }
  }'

# Expected response:
# {
#   "agent_id": "test-socket-001-4906eeb7",
#   "status": "running",
#   "socket_mode": true,
#   "pid": 12345
# }

# ═══════════════════════════════════════════════════════════════════════
# Verify Socket Creation and Permissions
# ═══════════════════════════════════════════════════════════════════════

# Check socket directory created
ls -la /var/run/pixell-agents/
# Expected: drwxr-x--- agent_test nginx ... agent_test/

ls -la /var/run/pixell-agents/agent_test/
# Expected:
# drwxr-x--- agent_test nginx ... .
# srw-rw---- agent_test nginx ... rest.sock
# srw-rw---- agent_test nginx ... a2a.sock
# srw-rw---- agent_test nginx ... ui.sock

# Verify permissions (660 = rw-rw----)
stat -c "%a %U %G %n" /var/run/pixell-agents/agent_test/*.sock
# Expected:
# 660 agent_test nginx /var/run/.../rest.sock
# 660 agent_test nginx /var/run/.../a2a.sock
# 660 agent_test nginx /var/run/.../ui.sock

# Verify sockets are real Unix sockets
file /var/run/pixell-agents/agent_test/*.sock
# Expected: socket


# ═══════════════════════════════════════════════════════════════════════
# Test Connectivity - Layer by Layer
# ═══════════════════════════════════════════════════════════════════════

# Test 1: Direct socket connection (from EC2)
curl --unix-socket /var/run/pixell-agents/agent_test/rest.sock \
  http://localhost/health
# Expected: {"status": "healthy", "agent_id": "test-socket-001"}

# Test 2: Through Nginx (from EC2)
curl http://localhost:8080/rest/test/health
# Expected: {"status": "healthy", "agent_id": "test-socket-001"}

# Test 3: Through ALB (from internet)
curl https://par.pixell.global/rest/test/health
# Expected: {"status": "healthy", "agent_id": "test-socket-001"}

# Test 4: gRPC through socket (requires grpcurl)
grpcurl -unix /var/run/pixell-agents/agent_test/a2a.sock \
  list
# Expected: agent.AgentService

# Test 5: gRPC through Nginx
grpcurl -plaintext localhost:50051 list
# Expected: agent.AgentService


# ═══════════════════════════════════════════════════════════════════════
# Verify Logs and Troubleshooting
# ═══════════════════════════════════════════════════════════════════════

# Check agent logs
tail -f /var/log/pixell-agents/test-socket-001.log
# Look for:
# - "Socket permissions set: 660, group=nginx"
# - "REST server listening on unix:/var/run/.../rest.sock"
# - "gRPC server listening on unix:/var/run/.../a2a.sock"

# Check Nginx access logs
sudo tail -f /var/log/nginx/access.log
# Should see requests to /rest/test/health

# Check Nginx error logs (should be empty)
sudo tail -f /var/log/nginx/error.log
# If errors:
# - "Permission denied" → Check socket permissions (660)
# - "No such file" → Socket not created yet
# - "Connection refused" → Agent not listening on socket

# Check supervisor logs
sudo journalctl -u pixell-supervisor -f
# Look for:
# - "Creating socket directory for agent test-socket-001"
# - "Agent test-socket-001 sockets ready after Xs"


# ═══════════════════════════════════════════════════════════════════════
# Performance Testing
# ═══════════════════════════════════════════════════════════════════════

# Benchmark socket mode vs port mode
# Deploy same agent in port mode for comparison
curl -X POST http://localhost:9000/agents \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "test-port-001",
    "package_url": "s3://pixell-packages/test-agent.apkg",
    "socket_mode": false,
    "ports": {"rest": 63000, "a2a": 60000, "ui": 65000}
  }'

# Benchmark both
ab -n 1000 -c 10 http://localhost:8080/rest/test/health  # Socket mode
ab -n 1000 -c 10 http://localhost:63000/health            # Port mode

# Compare latency (should be similar, socket might be slightly faster)


# ═══════════════════════════════════════════════════════════════════════
# Validate Cleanup
# ═══════════════════════════════════════════════════════════════════════

# Delete socket agent
curl -X DELETE http://localhost:9000/agents/test-socket-001

# Verify socket directory cleaned up
ls -la /var/run/pixell-agents/agent_test/
# Expected: No such file or directory

# Verify agent process terminated
ps aux | grep test-socket-001
# Expected: (empty)


# ═══════════════════════════════════════════════════════════════════════
# WEEK 2 VALIDATION COMPLETE ✅
# ═══════════════════════════════════════════════════════════════════════
# At this point:
# ✅ PAR code deployed with socket support
# ✅ First test agent deployed in socket mode
# ✅ Socket permissions correct (660 agent:nginx)
# ✅ Connectivity verified at all layers (socket → Nginx → ALB)
# ✅ Logs show no errors
# ✅ Performance comparable to port mode
# ✅ Cleanup works correctly
#
# Ready for gradual production rollout
```

### 6.6 Production Migration Plan

**Week 1: Infrastructure Setup** (See Section 6.4)
- [ ] Update EC2 security group (sg-0c13cfb5da4e67ea7)
- [ ] Install Nginx on prod instance (i-09dcb7f387166efd0)
- [ ] Deploy Nginx config
- [ ] Create 3 shared ALB target groups
- [ ] Register instance with shared TGs
- [ ] Validate infrastructure

**Week 2: Code Deployment and Testing** (See Section 6.5)
- [ ] Deploy PAR socket code to prod
- [ ] Deploy first test agent in socket mode
- [ ] Verify socket permissions (660)
- [ ] Test connectivity (socket → Nginx → ALB)
- [ ] Verify cleanup works
- [ ] Performance benchmarking

**Week 3-4: Gradual Migration**
- [ ] Day 1: Enable socket mode for 1 test agent
- [ ] Day 3: Enable for 5% of agents (10 agents)
- [ ] Day 7: Enable for 25% of agents (50 agents)
- [ ] Day 14: Enable for 100% of agents (200 agents)
- [ ] Monitor metrics at each step

**Week 5: Cleanup** (Gradual TG Deletion with Connection Draining)
```bash
# ⚠️ CRITICAL: ALB connection draining takes 300 seconds (5 minutes)
# Must wait for draining before deleting target groups to avoid 502 errors

# Get list of all old per-agent target groups
OLD_TGS=$(aws elbv2 describe-target-groups \
  --query 'TargetGroups[?starts_with(TargetGroupName, `agent-`)].TargetGroupArn' \
  --output text)

echo "Found $(echo $OLD_TGS | wc -w) old target groups to delete"

# For each old target group:
for TG_ARN in $OLD_TGS; do
  TG_NAME=$(aws elbv2 describe-target-groups \
    --target-group-arns $TG_ARN \
    --query 'TargetGroups[0].TargetGroupName' \
    --output text)

  echo "Processing $TG_NAME..."

  # 1. Find and delete associated listener rule
  LISTENER_ARN=$(aws elbv2 describe-listeners \
    --load-balancer-arn <ALB_ARN> \
    --query 'Listeners[0].ListenerArn' \
    --output text)

  RULE_ARN=$(aws elbv2 describe-rules \
    --listener-arn $LISTENER_ARN \
    --query "Rules[?Actions[0].TargetGroupArn=='$TG_ARN'].RuleArn" \
    --output text)

  if [ -n "$RULE_ARN" ]; then
    echo "  Deleting listener rule: $RULE_ARN"
    aws elbv2 delete-rule --rule-arn $RULE_ARN
  fi

  # 2. Deregister all targets from target group
  TARGETS=$(aws elbv2 describe-target-health \
    --target-group-arn $TG_ARN \
    --query 'TargetHealthDescriptions[].Target' \
    --output json)

  if [ "$TARGETS" != "[]" ]; then
    echo "  Deregistering targets..."
    aws elbv2 deregister-targets \
      --target-group-arn $TG_ARN \
      --targets $TARGETS
  fi

  # 3. ⚠️ CRITICAL: Wait for connection draining (300 seconds)
  echo "  Waiting for connection draining (300s)..."
  sleep 300

  # Verify all targets deregistered
  REMAINING=$(aws elbv2 describe-target-health \
    --target-group-arn $TG_ARN \
    --query 'length(TargetHealthDescriptions)' \
    --output text)

  if [ "$REMAINING" -eq "0" ]; then
    # 4. Delete target group
    echo "  Deleting target group: $TG_NAME"
    aws elbv2 delete-target-group --target-group-arn $TG_ARN
  else
    echo "  WARNING: Targets still registered, skipping deletion"
  fi

  # Rate limit (avoid AWS API throttling)
  sleep 1
done

echo "Cleanup complete!"

# Verify only 3 target groups remain
aws elbv2 describe-target-groups \
  --query 'length(TargetGroups)' \
  --output text
# Expected: 3 (pixell-rest-proxy, pixell-grpc-proxy, pixell-ui-proxy)
```

- [ ] Mark port_allocator as deprecated in code comments
- [ ] Update documentation with socket mode instructions
- [ ] Update monitoring dashboards for socket metrics

### 6.7 Rollback Procedure (Production)

**Emergency Rollback if Socket Deployment Fails**

```bash
# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 1: Socket Permission Errors
# ═══════════════════════════════════════════════════════════════════════
# Symptom: Nginx logs show "Permission denied" when proxying to sockets

# Quick fix (30 seconds):
# Fix socket permissions for all socket agents
for agent_dir in /var/run/pixell-agents/agent_*/; do
  chmod 660 "$agent_dir"/*.sock
  chgrp nginx "$agent_dir"/*.sock
done

# Restart Nginx
sudo systemctl restart nginx

# Verify requests work
curl https://par.pixell.global/rest/<agent_id>/health


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 2: ALB Can't Reach Nginx (Network Blocking)
# ═══════════════════════════════════════════════════════════════════════
# Symptom: Target health checks failing, ALB returns 502/503

# Verify security group rules exist
aws ec2 describe-security-groups \
  --group-ids sg-0c13cfb5da4e67ea7 \
  --query 'SecurityGroups[0].IpPermissions[?FromPort==`8080`]'

# If missing, add rules (see Section 6.4 Step 1)
aws ec2 authorize-security-group-ingress \
  --group-id sg-0c13cfb5da4e67ea7 \
  --ip-permissions IpProtocol=tcp,FromPort=8080,ToPort=8080,UserIdGroupPairs=[{GroupId=sg-0f5b28ee64419e95d}]

# Verify Nginx is listening
netstat -tlnp | grep -E '(8080|50051|3000)'


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 3: Socket Agents Not Starting
# ═══════════════════════════════════════════════════════════════════════
# Symptom: Agents fail to start, no sockets created

# Check supervisor logs
sudo journalctl -u pixell-supervisor -n 100

# Common issues:
# 1. nginx group doesn't exist
getent group nginx || sudo groupadd nginx

# 2. Agent user not in nginx group
sudo usermod -a -G nginx agent_4906eeb7

# 3. Socket directory doesn't exist
sudo mkdir -p /var/run/pixell-agents
sudo chmod 755 /var/run/pixell-agents

# 4. Old socket files blocking bind
sudo rm -rf /var/run/pixell-agents/agent_*/


# ═══════════════════════════════════════════════════════════════════════
# SCENARIO 4: Full Rollback to Port Mode
# ═══════════════════════════════════════════════════════════════════════
# Use this if socket mode is fundamentally broken

# 1. Get list of socket mode agents
curl http://localhost:9000/agents | jq -r '.agents[] | select(.socket_mode==true) | .agent_id'

# 2. Delete all socket mode agents
for agent_id in $(curl -s http://localhost:9000/agents | jq -r '.agents[] | select(.socket_mode==true) | .agent_id'); do
  echo "Deleting socket agent: $agent_id"
  curl -X DELETE http://localhost:9000/agents/$agent_id
done

# 3. Redeploy in port mode
for agent_id in $socket_agents; do
  echo "Redeploying $agent_id in port mode"
  curl -X POST http://localhost:9000/agents \
    -H "Content-Type: application/json" \
    -d "{
      \"agent_id\": \"$agent_id\",
      \"package_url\": \"s3://...\",
      \"socket_mode\": false
    }"
done

# 4. Verify all agents healthy
curl http://localhost:9000/agents | jq '.agents[] | {id: .agent_id, status: .status, mode: (if .socket_mode then "socket" else "port" end)}'

# 5. Investigation
# Collect logs for post-mortem
sudo journalctl -u pixell-supervisor --since "1 hour ago" > /tmp/supervisor.log
sudo cp /var/log/nginx/error.log /tmp/nginx-error.log
tar -czf /tmp/socket-rollback-logs.tar.gz /tmp/*.log

# 6. Optional: Remove Nginx listener rules (keep infrastructure)
# Don't delete TGs or Nginx - keep for next attempt
aws elbv2 delete-rule --rule-arn <rule-arn-2000>
aws elbv2 delete-rule --rule-arn <rule-arn-2001>
aws elbv2 delete-rule --rule-arn <rule-arn-2002>
```

**Rollback Decision Tree:**

```
Socket deployment issue?
│
├─ Permission denied in Nginx logs?
│  └─> Fix socket permissions (Scenario 1)
│
├─ ALB health checks failing?
│  └─> Check security group rules (Scenario 2)
│
├─ Agents not starting?
│  └─> Check nginx group, directory, old sockets (Scenario 3)
│
└─ Everything broken?
   └─> Full rollback to port mode (Scenario 4)
```

---

## SUCCESS CRITERIA (ALL PHASES)

### Functional Requirements

- [ ] Socket mode agents deploy successfully
- [ ] Port mode agents still work (backward compatibility)
- [ ] Hybrid mode works (port + socket agents simultaneously)
- [ ] Nginx routes requests correctly
- [ ] Socket permissions correct (660 agent:nginx)
- [ ] Socket cleanup works on agent deletion
- [ ] Health checks work in both modes

### Performance Requirements

- [ ] Socket mode latency: <0.5ms overhead vs port mode
- [ ] Socket mode throughput: ≥ port mode throughput
- [ ] Load test: 100 concurrent requests, P99 < 500ms
- [ ] 1000 agents can deploy to single instance (theoretical)

### Testing Requirements

- [ ] Unit test coverage: >90%
- [ ] Integration test coverage: >80%
- [ ] All E2E tests pass
- [ ] Load tests pass
- [ ] Migration tests pass
- [ ] Rollback tests pass

### Operational Requirements

- [ ] Documentation complete
- [ ] Runbooks created
- [ ] Monitoring dashboards updated
- [ ] Alerts configured
- [ ] On-call team trained

---

## CRITICAL ERRORS PREVENTED BY THIS PLAN

This plan specifically addresses **23 critical socket deployment failures** (including 5 showstoppers):

### 1. Permission Errors (Phase 2, 3, 4)
**Error:** `connect() to unix:/var/run/.../rest.sock failed (13: Permission denied)`
**Fix:**
- Phase 2: Validate nginx group exists before directory creation
- Phase 3: Create directory with 750 perms, owner agent:nginx BEFORE spawn
- Phase 4: Set socket permissions to 660, group nginx AFTER bind
- Verify agent user has nginx group membership

### 2. Network Blocking Errors (Phase 6 Week 1 Step 1)
**Error:** `Target i-09dcb7f387166efd0:8080 failed health checks: Connection timed out`
**Fix:**
- Week 1 Step 1: Update security group BEFORE Nginx installation
- Allow ports 8080, 50051, 3000 from ALB security group
- Validate with `aws ec2 describe-security-groups`

### 3. Socket Creation Errors (Phase 3, 4)
**Error:** `OSError: [Errno 98] Address already in use`
**Fix:**
- Phase 3: Remove stale socket files BEFORE agent spawn
- Phase 4: Remove old socket in main.py and a2a/server.py BEFORE bind
- Process manager waits for socket creation (30s timeout)

### 4. Race Condition Errors (Phase 3)
**Error:** `connect() to unix:.../rest.sock failed (2: No such file or directory)`
**Fix:**
- Phase 3: process_manager waits for sockets to exist after spawn
- Uses socket_allocator.validate_socket_availability()
- 30 second timeout with process health check

### 5. Nginx Configuration Errors (Phase 6)
**Error:** `nginx: [emerg] bind() to 0.0.0.0:8080 failed (98: Address already in use)`
**Fix:**
- Week 1 Step 2: Check for port conflicts before starting Nginx
- Validate with `netstat -tlnp | grep -E '(8080|50051|3000)'`
- Test Nginx config with `nginx -t` before reload

### 6. ALB Listener Priority Errors (Phase 6)
**Error:** `PriorityInUseException: Priority 100 is already in use`
**Fix:**
- Week 1 Step 6: Use priority 2000+ for wildcard rules
- Existing per-agent rules use priority 1-1999
- Allows hybrid mode (port and socket agents coexist)

### 7. Database Schema Missing (Phase 1) ⭐ NEW
**Error:** `column "socket_mode" of relation "agents" does not exist`
**Fix:**
- Phase 1: SQL migration adds socket_mode column BEFORE code deployment
- Migration runs with `ADD COLUMN IF NOT EXISTS` for safety
- Rollback migration provided

### 8. /var/run tmpfs Reboot Wipe (Phase 6 Week 1 Step 3) ⭐ NEW
**Error:** All sockets disappear after EC2 reboot (/var/run is tmpfs)
**Fix:**
- systemd-tmpfiles.d configuration recreates directory on boot
- `/etc/tmpfiles.d/pixell-agents.conf` ensures persistence
- Supervisor can detect missing sockets and restart agents

### 9. Socket Path Length Limit (Phase 2) ⭐ NEW
**Error:** `OSError: AF_UNIX path too long` (108 char limit)
**Fix:**
- socket_allocator validates all paths < 108 characters
- Raises ValueError with clear message if path too long
- Use fixed-length hash instead of variable-length short_id

### 10. Short ID Collision (Phase 2) ⭐ NEW
**Error:** Two agents with same UUID prefix overwrite each other's sockets
**Fix:**
- Use SHA256 hash[:16] instead of first UUID segment
- Guarantees unique, collision-free directory names
- Prevents agents from accidentally sharing socket directories

### 11. File Descriptor Exhaustion (Phase 6 Week 1 Step 3b) ⭐ NEW
**Error:** `OSError: [Errno 24] Too many open files` (ulimit 1024 too low)
**Fix:**
- systemd service: `LimitNOFILE=65536`
- Supports 500+ agents with multiple sockets each
- Also increases sysctl fs.file-max if needed

### 12. Nginx Worker Connection Limits (Phase 6 Week 1 Step 2) ⭐ NEW
**Error:** `[alert] 1024 worker_connections are not enough`
**Fix:**
- Nginx config: `worker_connections 16384`
- Nginx config: `worker_rlimit_nofile 32768`
- Supports 1000+ concurrent agent connections

### 13. Agent Startup Failure Cleanup (Phase 3) ⭐ NEW
**Error:** Orphaned socket directories after agent crashes during startup
**Fix:**
- process_manager cleanup on exception
- Removes socket directory if agent fails to start
- Prevents blocking future deployment attempts

### 14. Agent User Race Condition (Phase 2) ⭐ NEW
**Error:** `Agent user agent_xxx does not exist` (PAC creates user async)
**Fix:**
- socket_allocator retries user lookup for 30 seconds
- Handles delay between PAC user creation and PAR deployment
- Clear error message if user truly missing

### 15. gRPC HTTP/2 Settings (Phase 6 Week 1 Step 2) ⭐ NEW
**Error:** `StatusCode.RESOURCE_EXHAUSTED` for large gRPC messages
**Fix:**
- Nginx config: `client_max_body_size 100m`
- Nginx config: `http2_max_field_size 128k`
- Nginx config: `http2_max_header_size 256k`
- Supports large gRPC streaming and metadata

### 16. ALB Connection Draining Delay (Phase 6 Week 5) ⭐ NEW
**Error:** 502 errors for 5 minutes when deleting target groups
**Fix:**
- Deregister targets FIRST, wait 300s for draining
- Only delete target group after draining complete
- Script handles 600 TGs with proper rate limiting

### 17. Monitoring Blind Spots (Phase 5) ⭐ NEW
**Error:** Can't debug socket issues - no visibility into socket mode status
**Fix:**
- `/health` endpoint shows socket vs port agent counts
- `/agents/sockets/status` endpoint shows per-agent socket status
- Permission errors, missing sockets visible via API

### 18. Agent Crash Log Visibility (Phase 3) ⭐ NEW
**Error:** Agent dies during startup, no idea why
**Fix:**
- process_manager reads last 100 lines of agent log on failure
- Includes log snippet in error message
- Clear path to log file for debugging

### 19. Database Connection Pool Exhaustion (Phase 1) ⭐ NEW - SHOWSTOPPER
**Error:** `MySQLError: (1040, 'Too many connections')` when 500 agents deployed
**Fix:**
- Phase 1: MySQL configuration `max_connections = 10000` (up from 151)
- Each agent needs ~10 connections (REST, gRPC, background tasks)
- 500 agents × 10 = 5000 connections minimum
- Also increased wait_timeout and thread_cache_size

### 20. ALB Health Check Doesn't Verify Sockets (Phase 5, 6) ⭐ NEW - SHOWSTOPPER
**Error:** ALB marks target healthy but all sockets are broken (permission errors)
**Fix:**
- Phase 6: Nginx /health proxies to supervisor (not static 200)
- Phase 5: Supervisor /health verifies at least 1 socket agent reachable
- Returns 503 if socket agents exist but none are reachable
- ALB marks target unhealthy and stops routing traffic
- Catches permission errors, missing sockets, dead agents

### 21. ALB Idle Timeout Kills Long gRPC Streams (Phase 6) ⭐ NEW - SHOWSTOPPER
**Error:** gRPC A2A streams disconnected after 60s (ALB default idle timeout)
**Fix:**
- Phase 6 Step 5b: Increase ALB idle_timeout to 3600s (1 hour)
- Command: `aws elbv2 modify-load-balancer-attributes --attributes Key=idle_timeout.timeout_seconds,Value=3600`
- Alternative: Implement gRPC keepalive (client_keepalive_time_ms: 30000)
- Supports long-running agent-to-agent communication

### 22. Orphaned Agents After Supervisor Restart (Phase 5) ⭐ NEW - SHOWSTOPPER
**Error:** Supervisor restarts but running agents become "orphaned" (not tracked)
**Fix:**
- Phase 5.3: Supervisor startup reconciliation
- On startup: Scan database for agents marked "running"
- Check if process still alive (os.kill(pid, 0))
- Check if sockets still exist (socket_allocator.validate)
- Clean up dead agents (mark failed, delete sockets)
- Track orphaned agents (prevent duplicate deploys)
- Benefits: Handles supervisor restart, crash, EC2 reboot

### 23. Duplicate Deploy Requests (Phase 5) ⭐ NEW - SHOWSTOPPER
**Error:** PAC sends duplicate deploy request → 2 agents with same ID → socket bind fails
**Fix:**
- Phase 5.3b: Idempotent deploy() method
- Check if agent already exists BEFORE deploying
- If running: Return existing status (idempotent)
- If deploying: Wait 2s, return status (handle concurrent)
- If failed: Clean up, redeploy (recovery)
- Mark as "deploying" BEFORE spawn (atomic, prevents race)
- Benefits: Network retry safe, timeout safe, concurrent safe

---

## CONCLUSION

This phased implementation plan ensures:

### Core Principles
- ✅ Each phase is independently testable
- ✅ No phase proceeds without 100% test pass rate
- ✅ Backward compatibility maintained throughout
- ✅ Clear rollback procedures at each phase
- ✅ Gradual, safe migration to production

### Error Prevention (23 Critical Failures Addressed)
- ✅ **6 Original Errors** - Permission, network, race conditions, etc.
- ✅ **12 Infrastructure Errors** - Database schema, tmpfs reboot, FD exhaustion, etc.
- ✅ **5 Showstopper Errors** - DB connections, health checks, idle timeout, orphaned agents, idempotency
- ✅ **Detailed validation** at every step with expected outputs
- ✅ **Monitoring & debugging** - Socket-specific endpoints and metrics

### Production Safety
- ✅ **Hybrid mode support** - Port and socket agents coexist
- ✅ **Connection draining** - No 502 errors during TG deletion
- ✅ **Graceful degradation** - Retry logic, timeouts, cleanup on failure
- ✅ **Comprehensive rollback** - 4 failure scenarios with recovery steps

### Scalability Improvements
- ✅ **200 → 1000+ agents** capacity increase
- ✅ **600 → 3 target groups** (99.5% reduction)
- ✅ **File descriptor limits** - Supports 500+ agents
- ✅ **Nginx tuning** - 16k worker connections, gRPC optimization

### Documentation Quality
- ✅ **Step-by-step commands** with expected outputs
- ✅ **AWS CLI examples** for all infrastructure changes
- ✅ **Troubleshooting guides** for common errors
- ✅ **Validation checkpoints** at every phase

**This plan is production-ready and addresses all known failure modes.**

**Next Steps:**
1. Review this plan with team
2. Get approval from tech lead and DevOps lead
3. **Run database migration** (Phase 1 prerequisite)
4. Begin Phase 1: Foundation

---

**Document Status:** Complete (with 5 critical showstopper fixes)
**Last Updated:** November 12, 2025
**Approval Required:** Tech Lead, DevOps Lead
