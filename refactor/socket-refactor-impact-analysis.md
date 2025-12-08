# SOCKET-BASED DEPLOYMENT REFACTOR - IMPACT ANALYSIS

**Document Version:** 1.0
**Date:** November 11, 2025
**Branch:** docs/socket-deployment-impact-analysis
**Status:** Implementation Guide for AI Agents
**Risk Level:** 🔴 **CRITICAL** - Major architectural change

---

## PURPOSE & AUDIENCE

This document provides a comprehensive impact analysis for transitioning from port-based to socket-based agent deployment. It is specifically written for **AI agents implementing this refactor** with detailed warnings about hardcoded values, dependencies, and critical constraints.

**⚠️ READ THIS FIRST:** This refactor affects 47+ files across 2 repositories and requires preserving specific AWS resource IDs, VPC configurations, and port ranges. Changing these values will break production infrastructure.

---

## EXECUTIVE SUMMARY

| Aspect | Details |
|--------|---------|
| **Total Files Impacted** | 47+ files (23 in PAR, 24+ in PAC) |
| **Risk Level** | 🔴 CRITICAL - Core deployment architecture |
| **Estimated Effort** | 8 weeks (4 phases) |
| **Repositories** | pixell-agent-runtime (PAR), pixell-agent-cloud (PAC) |
| **New Components** | 9 new files to create |
| **Test Updates** | ~30 test files need updates |
| **Migration Strategy** | Phased hybrid mode (support both port and socket) |

**Key Change:** Replace per-agent port allocation (200 agent limit) with Unix domain sockets (1000+ agent capacity).

---

## ⚠️ CRITICAL ARCHITECTURAL PRINCIPLE: STATELESS DESIGN

**PAR (pixell-agent-runtime) MUST remain STATELESS for socket paths:**

- **Socket paths are NEVER stored** in models, database, or state
- **Socket paths are COMPUTED on-the-fly** from agent_id using SocketAllocator
- **Only store `socket_mode` flag** (boolean) to indicate deployment type
- **Formula:** `/var/run/pixell-agents/agent_{short_id}/{surface}.sock`
  - short_id = first UUID segment (e.g., "4906eeb7" from "4906eeb7-9959-414e-84c6-f2445822ebe4")

**Why stateless?**
- Socket paths are deterministic - can always be recomputed from agent_id
- Prevents stale path data in database
- Simplifies code - no need to pass socket paths through multiple layers
- Consistent with Unix philosophy - paths are filesystem locations, not data

**Implementation Impact:**
- `DeployRequest` has `socket_mode: bool`, NOT `socket_paths: SocketPaths`
- `AgentInfo` has `socket_mode: bool`, NOT `socket_paths: SocketPaths`
- `process_manager.spawn_agent()` computes paths internally via SocketAllocator
- Database only stores `socket_mode` column, no socket_path columns

---

## CRITICAL HARDCODED VALUES - DO NOT CHANGE

These values are embedded in production AWS infrastructure. Changing them will cause deployment failures.

### AWS Infrastructure (Production)

```yaml
# ⚠️ CRITICAL: These values are tied to live AWS resources

VPC_ID: "vpc-0039e5988107ae565"
  Location: src/lib/aws/alb.ts line 53, ec2-multi-agent.ts
  Reason: Runtime VPC where EC2 instance i-09dcb7f387166efd0 runs
  Impact: All target groups MUST be in this VPC
  Constraint: Cannot change without recreating entire runtime infrastructure

EC2_INSTANCE_ID: "i-09dcb7f387166efd0"
  Location: ec2-multi-agent.ts, alb.ts
  Private IP: 10.0.1.37
  Public IP: 18.119.137.118
  Reason: Single production EC2 instance running PAR supervisor
  Impact: All ALB target registrations reference this instance
  Constraint: Fixed until instance migration planned

SUBNETS:
  - subnet-0a79126c8f2c8f05c (us-east-2a) - EC2 instance subnet
  - subnet-0b0e8734fc88867f7 (us-east-2b)
  Location: alb.ts line 106
  Reason: ALB and EC2 networking
  Constraint: Must be in same VPC (vpc-0039e5988107ae565)

ALB_NAME: "pixell-runtime-alb"
  DNS: pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com
  Public DNS: par.pixell.global
  Location: alb.ts, terraform
  Reason: Main load balancer for agent traffic
  Constraint: DNS CNAME records point to this ALB

AWS_SECRETS_NAME: "pac/mysql"
  Location: ec2-multi-agent.ts, supervisor client
  Reason: Contains database credentials and AWS config
  Constraint: Secret ARN in IAM policies

SECURITY_GROUPS:
  - sg-0c13cfb5da4e67ea7 (EC2 instance)
  - sg-0f5b28ee64419e95d (ALB)
  Location: alb.ts, terraform
  Reason: Firewall rules for ports
  Constraint: Must allow ports 8080, 50051, 3000 (proxy) in socket mode
```

### Port Ranges (Legacy Port Mode - Keep for Backward Compatibility)

```yaml
# ⚠️ PRESERVE: These ranges are used by existing 200 deployed agents

A2A_PORT_RANGE: 60000-60199
  File: src/pixell_runtime/supervisor/port_allocator.py line 52-53
  File: src/lib/ports/allocator.ts line 13
  Capacity: 200 agents
  Usage: gRPC A2A communication
  Constraint: Security group rules sg-0c13cfb5da4e67ea7 allow this range

REST_PORT_RANGE: 63000-63199
  File: src/pixell_runtime/supervisor/port_allocator.py line 55-56
  File: src/lib/ports/allocator.ts line 14
  Capacity: 200 agents
  Usage: REST API endpoints
  Constraint: ALB health checks configured for this range

UI_PORT_RANGE: 65000-65199
  File: src/pixell_runtime/supervisor/port_allocator.py line 58-59
  File: src/lib/ports/allocator.ts line 15
  Capacity: 200 agents
  Usage: Static UI serving
  Constraint: ALB target groups reference this range

MAX_AGENTS_PER_INSTANCE: 200
  File: port_allocator.py line 48, allocator.ts line 462
  Reason: Limited by port range capacity
  Constraint: Cannot exceed without expanding port ranges
```

### New Proxy Ports (Socket Mode - Add to Security Groups)

```yaml
# ⚠️ NEW INFRASTRUCTURE: Add these ports to security group sg-0c13cfb5da4e67ea7

REST_PROXY_PORT: 8080
  Location: refactor/socket-deployment.md line 89
  Usage: Nginx reverse proxy for REST API
  ALB Target Group: pixell-rest-proxy
  Protocol: HTTP
  Health Check: GET /health

GRPC_PROXY_PORT: 50051
  Location: refactor/socket-deployment.md line 92
  Location: src/pixell_runtime/supervisor/grpc_gateway.py line 38
  Usage: Nginx reverse proxy for gRPC/A2A
  ALB Target Group: pixell-grpc-proxy
  Protocol: HTTP2 (CRITICAL for gRPC!)
  Health Check: GET /health

UI_PROXY_PORT: 3000
  Location: refactor/socket-deployment.md line 95
  Usage: Nginx reverse proxy for UI
  ALB Target Group: pixell-ui-proxy
  Protocol: HTTP
  Health Check: GET /health

SUPERVISOR_HTTP_PORT: 9000
  Location: supervisor/server.py line 485, supervisor/__main__.py
  Usage: PAR supervisor management API (DO NOT CHANGE)
  Protocol: HTTP
  Constraint: PAC supervisor client hardcoded to this port
```

### System Paths (Linux Filesystem)

```yaml
# ⚠️ FILESYSTEM: These paths must match between PAR and Nginx config

SOCKET_BASE_DIR: "/var/run/pixell-agents"
  Location: socket-deployment.md line 207, nginx config
  Permissions: 755 root:root
  Agent Subdirs: 750 agent_{id}:nginx
  Socket Files: 660 agent_{id}:nginx
  Reason: Nginx must read sockets, agents must write
  Constraint: Both PAR and Nginx must use exact same path

SOCKET_PATH_PATTERN: "/var/run/pixell-agents/agent_{short_id}/{rest|a2a|ui}.sock"
  Example: /var/run/pixell-agents/agent_4906eeb7/rest.sock
  short_id: First 8 chars of agent UUID
  Reason: Nginx regex routing depends on this pattern
  Constraint: Change requires Nginx config update

PACKAGE_EXTRACT_DIR: "/tmp/pixell_packages"
  Location: state.py line 92, process_manager.py line 207
  Permissions: 1777 (world-writable with sticky bit)
  Usage: Extract .apkg files before deployment
  Constraint: Shared across all agents

LOG_DIR: "/var/lib/pixell/logs"
  Location: process_manager.py line 126
  Permissions: 755 root:root
  Usage: Agent log files
  Pattern: agent_{short_id}.log
```

---

## REPOSITORY 1: PIXELL-AGENT-RUNTIME (PAR)

### CRITICAL FILES - Core Deployment Logic

#### 1. src/pixell_runtime/supervisor/port_allocator.py

**Current Function:**
- Allocates ports from predefined ranges (60000-60199, 63000-63199, 65000-65199)
- Manages port pool (200 slots)
- Tracks allocated ports in memory

**Required Changes:**
```python
# ⚠️ ACTION: DEPRECATE this entire file (DO NOT DELETE - keep for legacy support)
# Status: Mark as deprecated, add warnings
# Timeline: Remove in Phase 4 after all agents migrated to sockets

# Add deprecation warning at top of file:
import warnings
warnings.warn(
    "port_allocator.py is deprecated. Use socket_allocator.py for new deployments.",
    DeprecationWarning,
    stacklevel=2
)

# Keep all existing code unchanged for backward compatibility during migration
```

**Why Keep It:**
- During Phase 1-3 migration, some agents will still use ports
- Hybrid mode requires both allocators to coexist
- Rollback capability if socket mode has issues

**Hardcoded Values (PRESERVE):**
- Line 48: `MAX_AGENTS = 200` - Do NOT change
- Line 52-53: A2A port range 60000-60199
- Line 55-56: REST port range 63000-63199
- Line 58-59: UI port range 65000-65199

**Dependencies:**
- state.py (uses PortAllocator)
- models.py (Ports dataclass)
- server.py (health check references port_allocator)

**Risk Level:** 🔴 HIGH - Core allocation logic, used by all port-based deployments

---

#### 2. NEW FILE: src/pixell_runtime/supervisor/socket_allocator.py

**Purpose:** Replace port allocation with socket path generation

**Required Implementation:**
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
    base_dir: Path  # e.g., /var/run/pixell-agents/agent_4906eeb7
    rest: Path      # e.g., .../rest.sock
    a2a: Path       # e.g., .../a2a.sock
    ui: Path        # e.g., .../ui.sock

    def __post_init__(self):
        """Validate socket paths."""
        # Ensure all paths are within base directory
        for socket_path in [self.rest, self.a2a, self.ui]:
            if not str(socket_path).startswith(str(self.base_dir)):
                raise ValueError(f"Socket {socket_path} must be within {self.base_dir}")


class SocketAllocator:
    """
    Allocates Unix domain socket paths for agents.

    Unlike PortAllocator (200 agent limit), sockets have no practical limit.
    Theoretical limit: ~10,000 agents per instance (memory/CPU bound, not socket bound).
    """

    def __init__(self, base_dir: str = SOCKET_BASE_DIR):
        """
        Initialize socket allocator.

        Args:
            base_dir: Base directory for agent sockets (default: /var/run/pixell-agents)

        ⚠️ WARNING: base_dir must match Nginx configuration or routing will fail
        """
        self.base_dir = Path(base_dir)
        self._ensure_base_directory()

    def _ensure_base_directory(self):
        """Create base socket directory with correct permissions."""
        if not self.base_dir.exists():
            logger.info(f"Creating socket base directory: {self.base_dir}")
            self.base_dir.mkdir(parents=True, mode=0o755)
            # Owner: root:root (created by supervisor running as root)

    def allocate(self, agent_id: str) -> SocketPaths:
        """
        Allocate socket paths for an agent.

        Args:
            agent_id: Full agent UUID (e.g., "4906eeb7-9959-414e-84c6-f2445822ebe4")

        Returns:
            SocketPaths with allocated paths

        ⚠️ IMPORTANT: Uses first 8 chars of UUID for short_id
           This matches PAC's shortId generation (agentId.substring(0, 8))
        """
        # Extract short ID (first 8 chars before first hyphen)
        short_id = agent_id.split('-')[0]  # e.g., "4906eeb7"

        # Create agent-specific directory
        agent_dir = self.base_dir / f"agent_{short_id}"

        return SocketPaths(
            base_dir=agent_dir,
            rest=agent_dir / "rest.sock",
            a2a=agent_dir / "a2a.sock",
            ui=agent_dir / "ui.sock"
        )

    def create_agent_directory(self, sockets: SocketPaths, agent_user: str):
        """
        Create agent socket directory with proper permissions.

        Args:
            sockets: SocketPaths to create directory for
            agent_user: Linux username (e.g., "agent_4906eeb7")

        Permissions:
            - Directory: 750 (agent_user:nginx)
            - Sockets: 660 (agent_user:nginx) - created by agent process

        ⚠️ CRITICAL: Nginx must be in group to read sockets
           - sudo usermod -aG nginx {agent_user}  (if needed)
           - Or use chgrp nginx after socket creation
        """
        # Create directory
        sockets.base_dir.mkdir(parents=True, exist_ok=True)

        # Set ownership: agent_user:nginx
        try:
            shutil.chown(sockets.base_dir, user=agent_user, group="nginx")
        except LookupError as e:
            logger.warning(f"Failed to set ownership to {agent_user}:nginx - {e}")
            logger.warning("Nginx proxy will not be able to access sockets!")
            raise

        # Set permissions: 750 (rwxr-x---)
        # Agent can read/write/execute
        # Nginx group can read/execute
        # Others cannot access
        sockets.base_dir.chmod(0o750)

        logger.info(f"Created socket directory: {sockets.base_dir} (750 {agent_user}:nginx)")

    def cleanup(self, sockets: SocketPaths):
        """
        Remove agent socket directory and all sockets.

        Args:
            sockets: SocketPaths to clean up

        ⚠️ WARNING: This is destructive. Agent must be stopped first.
        """
        if sockets.base_dir.exists():
            logger.info(f"Cleaning up socket directory: {sockets.base_dir}")
            shutil.rmtree(sockets.base_dir)
        else:
            logger.debug(f"Socket directory already removed: {sockets.base_dir}")

    def validate_socket_availability(self, sockets: SocketPaths) -> bool:
        """
        Check if sockets exist and are accessible.

        Returns:
            True if all sockets exist and are socket files

        Usage: Health checking, deployment validation
        """
        for socket_path in [sockets.rest, sockets.a2a, sockets.ui]:
            if not socket_path.exists():
                logger.warning(f"Socket does not exist: {socket_path}")
                return False

            # Check if it's actually a socket (not a regular file)
            if not socket_path.is_socket():
                logger.error(f"Path exists but is not a socket: {socket_path}")
                return False

        return True
```

**Hardcoded Values:**
- `SOCKET_BASE_DIR = "/var/run/pixell-agents"` - **MUST MATCH NGINX CONFIG**
- short_id extraction: `agent_id.split('-')[0]` - Must match PAC logic
- Directory permissions: 750 (rwxr-x---)
- Socket permissions: 660 (rw-rw----) - Set by agent process
- Group: "nginx" - Hardcoded (could be configurable)

**Dependencies:**
- models.py (needs SocketPaths dataclass)
- state.py (uses SocketAllocator in socket mode)
- process_manager.py (passes sockets to agent)

**Testing Requirements:**
- Test socket path generation for various UUIDs
- Test directory creation with correct permissions
- Test cleanup (remove sockets and directory)
- Test validation of existing sockets
- Test permission errors (wrong user/group)

**Risk Level:** 🔴 HIGH - New core component, critical for socket mode

---

#### 3. src/pixell_runtime/supervisor/models.py

**Current Function:**
- Defines `Ports` dataclass with rest/a2a/ui port numbers
- Used by PortAllocator and throughout supervisor

**Required Changes:**
```python
# Line ~15: Add new SocketPaths model after Ports

@dataclass
class Ports:
    """TCP port allocation for agent (legacy port mode)."""
    rest: int   # REST API port (63000-63199)
    a2a: int    # gRPC A2A port (60000-60199)
    ui: int     # UI server port (65000-65199)

    # ⚠️ DEPRECATION: Port mode limited to 200 agents
    # Use SocketPaths for new deployments


@dataclass
class SocketPaths:
    """Unix socket paths for agent (socket mode)."""
    base_dir: str  # e.g., "/var/run/pixell-agents/agent_4906eeb7"
    rest: str      # e.g., ".../rest.sock"
    a2a: str       # e.g., ".../a2a.sock"
    ui: str        # e.g., ".../ui.sock"

    # ⚠️ IMPORTANT: Paths must be absolute, not relative
    # Nginx proxy uses absolute paths for upstream routing


# Line ~50: Update DeployRequest model

class DeployRequest(BaseModel):
    """Request to deploy an agent."""
    agent_id: str
    package_url: str

    # Port mode (legacy) - Deprecated but kept for backward compatibility
    ports: Optional[Ports] = None

    # Socket mode (new) - Preferred for new deployments
    socket_paths: Optional[SocketPaths] = None
    socket_mode: bool = False  # Flag to indicate which mode to use

    environment: Dict[str, str] = {}
    cpu_limit: Optional[int] = None
    memory_limit: Optional[int] = None

    def validate(self):
        """Validate deployment request."""
        # ⚠️ VALIDATION: Must specify either ports OR sockets, not both
        if self.socket_mode and not self.socket_paths:
            raise ValueError("socket_mode=True requires socket_paths")
        if not self.socket_mode and not self.ports:
            raise ValueError("socket_mode=False requires ports")
        if self.socket_paths and self.ports:
            raise ValueError("Cannot specify both ports and socket_paths")


# Line ~80: Update AgentInfo model

class AgentInfo(BaseModel):
    """Information about a deployed agent."""
    agent_id: str
    status: str  # "running", "stopped", "failed"

    # Port mode info (legacy)
    ports: Optional[Ports] = None

    # Socket mode info (new)
    socket_paths: Optional[SocketPaths] = None
    socket_mode: bool = False

    pid: Optional[int] = None
    package_url: str
    deployed_at: str

    # ⚠️ IMPORTANT: Either ports or socket_paths must be set
    # Use socket_mode to determine which is active
```

**Why These Changes:**
- Maintain backward compatibility (keep Ports model)
- Add socket support without breaking existing deployments
- Clear validation rules (ports XOR sockets)
- socket_mode flag controls which allocator to use

**Hardcoded Values:** None (data models only)

**Dependencies:**
- socket_allocator.py (creates SocketPaths instances)
- port_allocator.py (creates Ports instances)
- state.py (uses these models)
- server.py (serializes/deserializes models)
- ALL supervisor components

**Risk Level:** 🟡 MEDIUM - Model changes affect all supervisor code

---

#### 4. src/pixell_runtime/supervisor/process_manager.py

**Current Function:**
- Spawns agent processes as Linux users
- Sets environment variables REST_PORT, A2A_PORT, UI_PORT
- Manages process lifecycle (start/stop/monitor)

**Required Changes:**
```python
# Line ~85: Update spawn_agent() signature and implementation

def spawn_agent(
    self,
    agent_id: str,
    package_path: str,
    ports: Optional[Ports] = None,           # Legacy port mode
    sockets: Optional[SocketPaths] = None,   # New socket mode
    environment: dict,
    user: str,
    log_file_path: str
) -> subprocess.Popen:
    """
    Spawn agent process with port or socket configuration.

    Args:
        agent_id: Full agent UUID
        package_path: Path to extracted .apkg
        ports: Port allocation (port mode) - LEGACY
        sockets: Socket paths (socket mode) - NEW
        environment: Additional environment variables
        user: Linux username to run as (e.g., "agent_4906eeb7")
        log_file_path: Path to log file

    Returns:
        subprocess.Popen instance

    ⚠️ CRITICAL: Must specify either ports OR sockets, not both
    """
    # Validate arguments
    if ports is None and sockets is None:
        raise ValueError("Must specify either ports or sockets")
    if ports is not None and sockets is not None:
        raise ValueError("Cannot specify both ports and sockets")

    socket_mode = sockets is not None

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
        # ⚠️ SOCKET MODE: Agent will bind to Unix domain sockets
        env["SOCKET_MODE"] = "true"
        env["REST_SOCKET"] = str(sockets.rest)
        env["A2A_SOCKET"] = str(sockets.a2a)
        env["UI_SOCKET"] = str(sockets.ui)

        logger.info(f"Spawning agent {agent_id} in SOCKET mode")
        logger.info(f"  REST socket: {sockets.rest}")
        logger.info(f"  A2A socket:  {sockets.a2a}")
        logger.info(f"  UI socket:   {sockets.ui}")
    else:
        # ⚠️ PORT MODE (LEGACY): Agent will bind to TCP ports
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
        user=user,          # ⚠️ setuid to agent user (requires supervisor runs as root)
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        cwd=f"/home/{user}"
    )

    logger.info(f"Agent process started: PID {process.pid}, user {user}")

    return process
```

**⚠️ CRITICAL NOTES:**
- **Socket permissions:** Agent process creates sockets with default permissions (666 minus umask)
  - Agent must call `os.chmod(socket, 0o660)` after binding
  - Agent must call `os.chown(socket, agent_user, "nginx")` if needed
- **Directory must exist:** Socket directory must be created BEFORE spawning agent
  - Call `socket_allocator.create_agent_directory()` first
  - Agent will fail to start if directory doesn't exist or has wrong permissions

**Hardcoded Values:**
- Python interpreter: `/usr/bin/python3.11` - PRESERVE
- Module: `pixell_runtime` - PRESERVE
- Home directory pattern: `/home/{user}` - PRESERVE

**Dependencies:**
- models.py (Ports, SocketPaths)
- socket_allocator.py (socket creation)
- state.py (calls spawn_agent)
- Agent runtime (reads environment variables)

**Risk Level:** 🔴 HIGH - Core process spawning logic

---

#### 5. src/pixell_runtime/supervisor/state.py

**Current Function:**
- Orchestrates deployment workflow
- Uses PortAllocator to get ports
- Manages SupervisorState (in-memory agent registry)

**Required Changes:**
```python
# Line ~25: Update imports

from .port_allocator import PortAllocator, Ports  # Keep for legacy
from .socket_allocator import SocketAllocator, SocketPaths  # New


# Line ~40: Update SupervisorState initialization

class SupervisorState:
    """In-memory registry of deployed agents."""

    def __init__(self):
        self.agents: Dict[str, AgentInfo] = {}

        # Dual allocators for hybrid mode
        self.port_allocator = PortAllocator()      # Legacy (200 agent limit)
        self.socket_allocator = SocketAllocator()  # New (unlimited)

        self.process_manager = ProcessManager()
        self.user_manager = UserManager()
        self.package_downloader = PackageDownloader()


# Line ~80: Update deploy() method

async def deploy(self, request: DeployRequest) -> AgentInfo:
    """
    Deploy an agent in port or socket mode.

    ⚠️ HYBRID MODE: Supports both port and socket deployments
       - socket_mode=True: Use SocketAllocator (new)
       - socket_mode=False: Use PortAllocator (legacy)
    """
    agent_id = request.agent_id
    socket_mode = request.socket_mode

    logger.info(f"Deploying agent {agent_id} in {'SOCKET' if socket_mode else 'PORT'} mode")

    # 1. Download package
    package_path = await self.package_downloader.download(request.package_url)

    # 2. Extract package
    extract_dir = self._extract_package(package_path, agent_id)

    # 3. Create Linux user
    short_id = agent_id.split('-')[0]
    user = f"agent_{short_id}"
    self.user_manager.create_user(user)

    # 4. Allocate ports OR sockets
    if socket_mode:
        # ⚠️ SOCKET MODE
        sockets = self.socket_allocator.allocate(agent_id)

        # Create socket directory with correct permissions (CRITICAL!)
        self.socket_allocator.create_agent_directory(sockets, user)

        ports = None
    else:
        # ⚠️ PORT MODE (LEGACY)
        ports = self.port_allocator.allocate()
        sockets = None

    # 5. Spawn agent process
    process = self.process_manager.spawn_agent(
        agent_id=agent_id,
        package_path=extract_dir,
        ports=ports,
        sockets=sockets,
        environment=request.environment,
        user=user,
        log_file_path=f"/var/lib/pixell/logs/{user}.log"
    )

    # 6. Store agent info
    agent_info = AgentInfo(
        agent_id=agent_id,
        status="running",
        ports=ports,
        socket_paths=sockets,
        socket_mode=socket_mode,
        pid=process.pid,
        package_url=request.package_url,
        deployed_at=datetime.utcnow().isoformat()
    )
    self.agents[agent_id] = agent_info

    logger.info(f"Agent {agent_id} deployed successfully (PID {process.pid})")

    return agent_info


# Line ~150: Update delete() method

async def delete(self, agent_id: str):
    """
    Delete an agent and clean up resources.

    ⚠️ CLEANUP: Must remove socket directory in socket mode
    """
    agent = self.agents.get(agent_id)
    if not agent:
        raise ValueError(f"Agent {agent_id} not found")

    # 1. Stop process
    self.process_manager.stop(agent.pid)

    # 2. Release resources
    if agent.socket_mode:
        # Clean up socket directory
        self.socket_allocator.cleanup(agent.socket_paths)
        logger.info(f"Cleaned up sockets for agent {agent_id}")
    else:
        # Release ports back to pool
        self.port_allocator.release(agent.ports)
        logger.info(f"Released ports for agent {agent_id}")

    # 3. Remove Linux user (optional - may want to keep for auditing)
    # self.user_manager.delete_user(f"agent_{agent_id.split('-')[0]}")

    # 4. Remove from registry
    del self.agents[agent_id]
```

**⚠️ CRITICAL SEQUENCE:**
1. Create socket directory BEFORE spawning agent
2. Set correct permissions (750 agent:nginx)
3. Spawn agent (agent creates sockets)
4. Agent sets socket permissions (660)
5. Nginx can now connect to sockets

If steps out of order, agent startup will fail!

**Hardcoded Values:** None (uses allocators)

**Dependencies:**
- socket_allocator.py (new)
- port_allocator.py (legacy)
- process_manager.py (spawning)
- models.py (DeployRequest, AgentInfo)

**Risk Level:** 🔴 HIGH - Core orchestration logic

---

#### 6. src/pixell_runtime/supervisor/server.py

**Current Function:**
- FastAPI HTTP server for supervisor management API (port 9000)
- Endpoints: POST /agents (deploy), DELETE /agents/{id}, GET /health, etc.
- Health check references port_allocator to report capacity

**Required Changes:**
```python
# Line ~107-108: Update health check to include socket capacity

@router.get("/health")
async def health_check():
    """
    Supervisor health check.

    Returns capacity for both port mode and socket mode.
    """
    return {
        "status": "healthy",
        "agents_running": len(state.agents),

        # Port mode capacity (LEGACY - 200 agent limit)
        "port_capacity": {
            "current": len([a for a in state.agents.values() if not a.socket_mode]),
            "max": state.port_allocator.max_agents,  # 200
            "available": state.port_allocator.max_agents - len([a for a in state.agents.values() if not a.socket_mode])
        },

        # Socket mode capacity (NEW - no practical limit)
        "socket_capacity": {
            "current": len([a for a in state.agents.values() if a.socket_mode]),
            "max": "unlimited",  # Limited by CPU/memory, not ports
            "available": "unlimited"
        },

        "disk_free_gb": get_disk_free(),
        "memory_free_mb": get_memory_free(),
        "cpu_load": os.getloadavg()
    }


# Line ~150: Update agent status endpoint to include socket info

@router.get("/agents/{agent_id}/status")
async def get_agent_status(agent_id: str):
    """Get detailed agent status."""
    agent = state.agents.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    status = {
        "agent_id": agent.agent_id,
        "status": agent.status,
        "pid": agent.pid,
        "deployed_at": agent.deployed_at,
        "deployment_mode": "socket" if agent.socket_mode else "port",  # NEW
    }

    # Add port or socket info
    if agent.socket_mode:
        status["sockets"] = {
            "rest": str(agent.socket_paths.rest),
            "a2a": str(agent.socket_paths.a2a),
            "ui": str(agent.socket_paths.ui),
            "available": state.socket_allocator.validate_socket_availability(agent.socket_paths)
        }
    else:
        status["ports"] = {
            "rest": agent.ports.rest,
            "a2a": agent.ports.a2a,
            "ui": agent.ports.ui
        }

    return status


# Line ~200: Deploy endpoint already uses state.deploy()
# No changes needed - state.deploy() handles socket vs port mode internally
# Just ensure DeployRequest model validation is enforced
```

**⚠️ CRITICAL NOTES:**
- Health check must report both port and socket capacity during migration
- Agent status should clearly indicate deployment mode
- No changes to deploy/delete endpoints (handled by state.py)

**Hardcoded Values:**
- Port 9000 (supervisor API) - PRESERVE
- Max agents (200 for port mode) - PRESERVE

**Dependencies:**
- state.py (SupervisorState)
- models.py (DeployRequest, AgentInfo)
- socket_allocator.py (validate_socket_availability)

**Risk Level:** 🟡 MEDIUM - API response format changes (backward compatible)

---

#### 7. src/pixell_runtime/core/runtime_config.py

**Current Function:**
- Validates environment variables for agent runtime
- Ensures REST_PORT, A2A_PORT, UI_PORT are set
- Provides configuration to main.py and three_surface/runtime.py

**Required Changes:**
```python
# Line ~20: Add socket configuration fields

class RuntimeConfig:
    """
    Agent runtime configuration from environment variables.

    Supports both port mode (legacy) and socket mode (new).
    """

    def __init__(self):
        # Deployment mode
        self.socket_mode: bool = os.getenv("SOCKET_MODE", "false").lower() == "true"

        # Port mode configuration (LEGACY)
        self.rest_port: Optional[int] = None
        self.a2a_port: Optional[int] = None
        self.ui_port: Optional[int] = None

        # Socket mode configuration (NEW)
        self.rest_socket: Optional[str] = None
        self.a2a_socket: Optional[str] = None
        self.ui_socket: Optional[str] = None

        # Common configuration
        self.agent_id: str = os.getenv("AGENT_APP_ID")
        self.base_path: str = os.getenv("BASE_PATH", "")
        self.multiplexed: bool = os.getenv("MULTIPLEXED", "false").lower() == "true"

        # Validate configuration
        self._validate()

    def _validate(self):
        """
        Validate runtime configuration.

        ⚠️ CRITICAL: Must have either ports OR sockets, not both
        """
        if not self.agent_id:
            raise ValueError("AGENT_APP_ID environment variable is required")

        if self.socket_mode:
            # Socket mode validation
            self.rest_socket = os.getenv("REST_SOCKET")
            self.a2a_socket = os.getenv("A2A_SOCKET")
            self.ui_socket = os.getenv("UI_SOCKET")

            if not all([self.rest_socket, self.a2a_socket, self.ui_socket]):
                raise ValueError(
                    "Socket mode requires REST_SOCKET, A2A_SOCKET, UI_SOCKET env vars"
                )

            # Validate socket paths are absolute
            for socket_path in [self.rest_socket, self.a2a_socket, self.ui_socket]:
                if not socket_path.startswith('/'):
                    raise ValueError(f"Socket path must be absolute: {socket_path}")

            # Check socket parent directory exists
            for socket_path in [self.rest_socket, self.a2a_socket, self.ui_socket]:
                parent_dir = os.path.dirname(socket_path)
                if not os.path.exists(parent_dir):
                    raise ValueError(f"Socket directory does not exist: {parent_dir}")
                if not os.access(parent_dir, os.W_OK):
                    raise ValueError(f"Socket directory not writable: {parent_dir}")

            logger.info(f"Runtime configured in SOCKET mode:")
            logger.info(f"  REST: {self.rest_socket}")
            logger.info(f"  A2A:  {self.a2a_socket}")
            logger.info(f"  UI:   {self.ui_socket}")

        else:
            # Port mode validation (LEGACY)
            self.rest_port = int(os.getenv("REST_PORT", "63000"))
            self.a2a_port = int(os.getenv("A2A_PORT", "60000"))
            self.ui_port = int(os.getenv("UI_PORT", "65000"))

            # Validate port ranges
            if not (60000 <= self.a2a_port <= 60199):
                logger.warning(f"A2A port {self.a2a_port} outside expected range 60000-60199")
            if not (63000 <= self.rest_port <= 63199):
                logger.warning(f"REST port {self.rest_port} outside expected range 63000-63199")
            if not (65000 <= self.ui_port <= 65199):
                logger.warning(f"UI port {self.ui_port} outside expected range 65000-65199")

            logger.info(f"Runtime configured in PORT mode (LEGACY):")
            logger.info(f"  REST: {self.rest_port}")
            logger.info(f"  A2A:  {self.a2a_port}")
            logger.info(f"  UI:   {self.ui_port}")
```

**⚠️ CRITICAL VALIDATIONS:**
1. **Socket paths must be absolute** - Relative paths will fail
2. **Socket directory must exist** - Created by supervisor before spawning agent
3. **Socket directory must be writable** - Agent needs write permission to create sockets
4. **Check permissions early** - Fail fast if misconfigured

**Hardcoded Values:**
- Default ports: 60000 (A2A), 63000 (REST), 65000 (UI) - PRESERVE
- Port ranges for validation - PRESERVE

**Dependencies:**
- main.py (uses RuntimeConfig)
- three_surface/runtime.py (uses RuntimeConfig)

**Risk Level:** 🔴 HIGH - Configuration validation, affects agent startup

---

#### 8. src/pixell_runtime/main.py

**Current Function:**
- Entry point for agent process: `python -m pixell_runtime`
- Starts REST API server using uvicorn on REST_PORT
- Single-surface mode (REST only, no A2A or UI)

**Required Changes:**
```python
# Line ~180: Update uvicorn.run() to support socket binding

def main():
    """
    Main entry point for agent runtime.

    Supports both port mode and socket mode based on SOCKET_MODE env var.
    """
    # Load configuration
    config = RuntimeConfig()

    # Create FastAPI app (unchanged)
    app = create_app(config)

    # Start server based on mode
    if config.socket_mode:
        # ⚠️ SOCKET MODE: Bind to Unix domain socket
        logger.info(f"Starting REST server in SOCKET mode")
        logger.info(f"  Binding to: {config.rest_socket}")

        # Ensure socket doesn't already exist (from previous run)
        if os.path.exists(config.rest_socket):
            logger.warning(f"Removing stale socket: {config.rest_socket}")
            os.unlink(config.rest_socket)

        # Start uvicorn on Unix socket
        uvicorn.run(
            app,
            uds=config.rest_socket,  # ⚠️ KEY CHANGE: Unix domain socket
            loop="uvloop",           # Performance optimization
            log_level="info",
            access_log=True
        )

        # ⚠️ CRITICAL: Set socket permissions after binding
        # uvicorn creates socket with default permissions (666 - umask)
        # We need 660 (rw-rw----) for agent:nginx
        try:
            os.chmod(config.rest_socket, 0o660)
            os.chown(config.rest_socket, -1, grp.getgrnam("nginx").gr_gid)
            logger.info(f"Set socket permissions: 660 {os.getuid()}:nginx")
        except Exception as e:
            logger.error(f"Failed to set socket permissions: {e}")
            logger.error("Nginx proxy will not be able to connect!")
            # Don't fail - socket might still work if umask is permissive

    else:
        # ⚠️ PORT MODE (LEGACY): Bind to TCP port
        logger.info(f"Starting REST server in PORT mode (LEGACY)")
        logger.info(f"  Binding to: 0.0.0.0:{config.rest_port}")

        uvicorn.run(
            app,
            host="0.0.0.0",          # Listen on all interfaces
            port=config.rest_port,   # TCP port
            loop="uvloop",
            log_level="info",
            access_log=True
        )


if __name__ == "__main__":
    main()
```

**⚠️ CRITICAL SOCKET HANDLING:**

1. **Remove stale sockets** - If agent crashes, socket file remains
   - Must `os.unlink()` before binding
   - Otherwise uvicorn fails with "Address already in use"

2. **Set permissions after binding** - uvicorn creates socket with default perms
   - Change to 660 (rw-rw----)
   - Change group to "nginx"
   - Otherwise Nginx cannot connect!

3. **Order matters**:
   ```
   1. Check if socket exists → unlink if yes
   2. Start uvicorn (creates socket)
   3. Immediately set permissions (before handling requests)
   ```

**Hardcoded Values:**
- Host: 0.0.0.0 (port mode) - PRESERVE
- Loop: uvloop (performance) - PRESERVE
- Socket group: "nginx" - HARDCODED (must match Nginx user)

**Dependencies:**
- runtime_config.py (RuntimeConfig)
- uvicorn (supports Unix sockets via `uds` parameter)

**Risk Level:** 🔴 HIGH - Entry point, socket creation and permissions

---

#### 9. src/pixell_runtime/three_surface/runtime.py

**Current Function:**
- Orchestrates three surfaces: REST (port 63000), A2A/gRPC (port 60000), UI (port 65000)
- Starts all three servers concurrently using asyncio
- Used by agents with `MULTIPLEXED=true`

**Required Changes:**
```python
# Line ~40: Update ThreeSurfaceRuntime class

class ThreeSurfaceRuntime:
    """
    Runtime that orchestrates REST, A2A (gRPC), and UI surfaces.

    Supports both port mode and socket mode.
    """

    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.socket_mode = config.socket_mode

        # Server instances
        self._rest_server = None
        self._a2a_server = None
        self._ui_server = None

    async def start(self):
        """
        Start all three surfaces concurrently.

        ⚠️ CRITICAL: Must set socket permissions after binding!
        """
        tasks = []

        # 1. Start REST API server
        tasks.append(self._start_rest_server())

        # 2. Start A2A gRPC server
        tasks.append(self._start_a2a_server())

        # 3. Start UI server (if configured)
        if self.config.ui_enabled:
            tasks.append(self._start_ui_server())

        # Run all servers concurrently
        await asyncio.gather(*tasks)

    async def _start_rest_server(self):
        """Start REST API server on port or socket."""
        app = create_rest_app(self.config)

        if self.socket_mode:
            # Socket mode
            socket_path = self.config.rest_socket

            # Remove stale socket
            if os.path.exists(socket_path):
                os.unlink(socket_path)

            # Configure uvicorn for Unix socket
            server_config = uvicorn.Config(
                app,
                uds=socket_path,
                loop="uvloop",
                log_level="info"
            )
            self._rest_server = uvicorn.Server(server_config)

            # Start server
            await self._rest_server.serve()

            # ⚠️ Set permissions immediately after binding
            os.chmod(socket_path, 0o660)
            try:
                os.chown(socket_path, -1, grp.getgrnam("nginx").gr_gid)
            except:
                logger.warning("Failed to set socket group to nginx")

        else:
            # Port mode (legacy)
            server_config = uvicorn.Config(
                app,
                host="0.0.0.0",
                port=self.config.rest_port,
                loop="uvloop"
            )
            self._rest_server = uvicorn.Server(server_config)
            await self._rest_server.serve()

    async def _start_a2a_server(self):
        """Start A2A gRPC server on port or socket."""
        from .a2a.server import create_grpc_server

        # Create gRPC server
        server = create_grpc_server(self.config)

        if self.socket_mode:
            # ⚠️ SOCKET MODE: Bind to Unix domain socket
            socket_path = self.config.a2a_socket

            # Remove stale socket
            if os.path.exists(socket_path):
                os.unlink(socket_path)

            # Bind to Unix socket
            # ⚠️ IMPORTANT: Use "unix:" prefix for gRPC
            server.add_insecure_port(f"unix:{socket_path}")

            logger.info(f"gRPC server binding to socket: {socket_path}")

        else:
            # ⚠️ PORT MODE (LEGACY): Bind to TCP port
            server.add_insecure_port(f"0.0.0.0:{self.config.a2a_port}")

            logger.info(f"gRPC server binding to port: {self.config.a2a_port}")

        # Start server
        await server.start()
        self._a2a_server = server

        # Set socket permissions (socket mode only)
        if self.socket_mode:
            try:
                os.chmod(self.config.a2a_socket, 0o660)
                os.chown(self.config.a2a_socket, -1, grp.getgrnam("nginx").gr_gid)
                logger.info(f"Set gRPC socket permissions: 660 agent:nginx")
            except Exception as e:
                logger.error(f"Failed to set gRPC socket permissions: {e}")

        # Wait for termination
        await server.wait_for_termination()

    async def _start_ui_server(self):
        """Start UI server on port or socket."""
        # Similar to REST server but serves static files
        # Implementation depends on UI serving mechanism
        pass

    async def shutdown(self):
        """
        Graceful shutdown of all servers.

        ⚠️ CRITICAL: Clean up socket files!
        """
        logger.info("Shutting down three-surface runtime...")

        # Stop servers
        if self._rest_server:
            await self._rest_server.shutdown()
        if self._a2a_server:
            await self._a2a_server.stop(grace=5)
        if self._ui_server:
            await self._ui_server.shutdown()

        # Clean up socket files (socket mode only)
        if self.socket_mode:
            for socket_path in [
                self.config.rest_socket,
                self.config.a2a_socket,
                self.config.ui_socket
            ]:
                if socket_path and os.path.exists(socket_path):
                    try:
                        os.unlink(socket_path)
                        logger.info(f"Cleaned up socket: {socket_path}")
                    except Exception as e:
                        logger.warning(f"Failed to clean up socket {socket_path}: {e}")
```

**⚠️ CRITICAL ORCHESTRATION NOTES:**

1. **Start all servers concurrently** - Use `asyncio.gather()`
2. **Set socket permissions immediately after binding** - Before handling requests
3. **Clean up sockets on shutdown** - Otherwise stale sockets remain

**Socket Permission Sequence:**
```
For EACH server (REST, A2A, UI):
  1. Remove stale socket (if exists)
  2. Bind to socket (server creates socket file)
  3. IMMEDIATELY set permissions (660 agent:nginx)
  4. Then start accepting connections
```

**Hardcoded Values:**
- Socket group: "nginx" - HARDCODED
- Socket permissions: 0o660 - HARDCODED
- Grace period: 5 seconds (gRPC shutdown)

**Dependencies:**
- runtime_config.py (RuntimeConfig)
- a2a/server.py (gRPC server creation)
- rest/server.py (REST app creation)

**Risk Level:** 🔴 HIGH - Orchestration of all surfaces, socket lifecycle

---

#### 10. src/pixell_runtime/a2a/server.py

**Current Function:**
- Creates gRPC server for A2A communication
- Binds to A2A_PORT (60000-60199)
- Implements pixell.agent.AgentService

**Required Changes:**
```python
# Line ~30: Update create_grpc_server() function

def create_grpc_server(config: RuntimeConfig) -> grpc.aio.Server:
    """
    Create gRPC server for A2A communication.

    Supports both port mode and socket mode.

    Returns:
        grpc.aio.Server instance (not started)
    """
    # Create server
    server = grpc.aio.server(
        interceptors=[
            # Add interceptors if needed (logging, auth, etc.)
        ],
        options=[
            # Performance options
            ('grpc.max_send_message_length', 100 * 1024 * 1024),  # 100MB
            ('grpc.max_receive_message_length', 100 * 1024 * 1024),
        ]
    )

    # Register service
    from pixell_runtime.proto import agent_pb2_grpc
    agent_pb2_grpc.add_AgentServiceServicer_to_server(
        AgentServiceImpl(config),
        server
    )

    # ⚠️ NOTE: Binding is done by caller (three_surface/runtime.py)
    # This function only creates the server, does not call add_insecure_port()

    logger.info("Created gRPC server for A2A communication")

    return server


class AgentServiceImpl(agent_pb2_grpc.AgentServiceServicer):
    """
    Implementation of pixell.agent.AgentService.

    Handles A2A requests regardless of transport (port or socket).
    ⚠️ Transport layer is transparent - same handler code for both modes.
    """

    def __init__(self, config: RuntimeConfig):
        self.config = config
        self.agent_id = config.agent_id

    async def Invoke(
        self,
        request: agent_pb2.ActionRequest,
        context: grpc.aio.ServicerContext
    ) -> agent_pb2.ActionResult:
        """
        Handle A2A Invoke request.

        ⚠️ TRANSPORT AGNOSTIC: Works identically for port or socket mode.
        gRPC abstracts the transport layer (TCP vs Unix socket).
        """
        try:
            # Extract A2A message
            a2a_message = request.message

            # Parse JSON-RPC 2.0 params
            params = json.loads(a2a_message.params_json)
            message = params["message"]
            skill = message["metadata"]["skill"]

            # Route to skill handler (same logic for port/socket mode)
            if skill == "chat":
                result = await self.handle_chat(message)
            elif skill == "comment":
                result = await self.handle_comment(message)
            else:
                result = {"error": f"Unknown skill: {skill}"}

            # Build response
            return agent_pb2.ActionResult(
                success=True,
                result=json.dumps(result),
                request_id=a2a_message.id,
                duration_ms=100
            )

        except Exception as e:
            logger.error(f"Error handling A2A request: {e}", exc_info=True)
            return agent_pb2.ActionResult(
                success=False,
                error=str(e),
                request_id=request.message.id if request.message else "unknown"
            )
```

**⚠️ KEY INSIGHTS:**

1. **gRPC abstracts transport** - Agent code SAME for port or socket mode
   - `server.add_insecure_port("0.0.0.0:60000")` → Port mode
   - `server.add_insecure_port("unix:/var/run/.../a2a.sock")` → Socket mode
   - Handler code identical!

2. **Binding done by caller** - three_surface/runtime.py binds to port or socket
   - create_grpc_server() only creates server instance
   - Caller decides where to bind

3. **Unix socket syntax** - Must use `unix:` prefix
   - Correct: `server.add_insecure_port("unix:/var/run/pixell-agents/agent_xxx/a2a.sock")`
   - Wrong: `server.add_insecure_port("/var/run/pixell-agents/agent_xxx/a2a.sock")`

**Hardcoded Values:**
- Max message size: 100MB - PRESERVE
- gRPC options - PRESERVE

**Dependencies:**
- runtime_config.py (RuntimeConfig)
- proto/agent_pb2.py, agent_pb2_grpc.py (generated)
- three_surface/runtime.py (caller)

**Risk Level:** 🟡 MEDIUM - Agent logic unchanged, only binding mechanism changes

---

## IMPLEMENTATION CHECKLIST FOR AI AGENTS

Use this checklist when implementing the refactor:

### Phase 1: Preparation (Do NOT modify any code yet)

- [ ] 1.1: Read this entire document thoroughly
- [ ] 1.2: Verify all hardcoded values match current infrastructure
- [ ] 1.3: Check VPC ID: `vpc-0039e5988107ae565` is correct
- [ ] 1.4: Check EC2 instance: `i-09dcb7f387166efd0` is running
- [ ] 1.5: Verify current port ranges (60000-60199, 63000-63199, 65000-65199)
- [ ] 1.6: Create backup branch before starting

### Phase 2: PAR - Core Components (Week 1-2)

- [ ] 2.1: Create `socket_allocator.py` with exact code from this document
- [ ] 2.2: Update `models.py` to add SocketPaths dataclass
- [ ] 2.3: Update `process_manager.py` spawn_agent() signature
- [ ] 2.4: Update `state.py` deploy() method for hybrid mode
- [ ] 2.5: Add deprecation warning to `port_allocator.py` (DO NOT DELETE)
- [ ] 2.6: Write unit tests for SocketAllocator
- [ ] 2.7: Test hybrid mode (deploy 1 port agent + 1 socket agent)

### Phase 3: PAR - Runtime Components (Week 2-3)

- [ ] 3.1: Update `runtime_config.py` to validate socket paths
- [ ] 3.2: Update `main.py` to support socket binding
- [ ] 3.3: Update `three_surface/runtime.py` for socket orchestration
- [ ] 3.4: Update `a2a/server.py` gRPC socket binding
- [ ] 3.5: Update `grpc_gateway.py` to forward to sockets
- [ ] 3.6: Test agent startup in socket mode

### Phase 4: Infrastructure (Week 3-4)

- [ ] 4.1: Install Nginx on EC2 instance `i-09dcb7f387166efd0`
- [ ] 4.2: Deploy Nginx config from refactor/socket-deployment.md
- [ ] 4.3: Create systemd service for Nginx
- [ ] 4.4: Add security group rules for ports 8080, 50051, 3000
- [ ] 4.5: Test Nginx routing to test socket

### Phase 5: PAC - Deployment Orchestration (Week 4-5)

- [ ] 5.1: Create `src/lib/sockets/allocator.ts`
- [ ] 5.2: Update `ec2-multi-agent.ts` provisionAgent() for socket mode
- [ ] 5.3: Update `alb.ts` to create 3 shared target groups
- [ ] 5.4: Update `supervisor/client.ts` to support socket paths
- [ ] 5.5: Add database migration for socket support
- [ ] 5.6: Test deployment via PAC in socket mode

### Phase 6: Testing (Week 5-6)

- [ ] 6.1: Deploy 1 test agent in socket mode
- [ ] 6.2: Verify Nginx routes requests correctly
- [ ] 6.3: Test ALB health checks
- [ ] 6.4: Test gRPC forwarding via Unix sockets
- [ ] 6.5: Load test (100 concurrent requests)
- [ ] 6.6: Test hybrid mode (50 port agents + 50 socket agents)

### Phase 7: Migration (Week 7-8)

- [ ] 7.1: Migrate 10% of staging agents to sockets
- [ ] 7.2: Monitor for 3 days, fix issues
- [ ] 7.3: Migrate 25% of production agents
- [ ] 7.4: Monitor for 3 days
- [ ] 7.5: Migrate remaining 75% of production agents
- [ ] 7.6: Update documentation

### Phase 8: Cleanup (Week 8+)

- [ ] 8.1: Mark port_allocator.py as fully deprecated
- [ ] 8.2: Remove old ALB target groups (600 → 3)
- [ ] 8.3: Remove port allocation database table
- [ ] 8.4: Update all documentation to prefer socket mode

---

## TESTING REQUIREMENTS

### Unit Tests Required

```python
# tests/test_supervisor_socket_allocator.py

def test_allocate_socket_paths():
    """Test socket path generation."""
    allocator = SocketAllocator()
    agent_id = "4906eeb7-9959-414e-84c6-f2445822ebe4"

    sockets = allocator.allocate(agent_id)

    assert str(sockets.base_dir) == "/var/run/pixell-agents/agent_4906eeb7"
    assert str(sockets.rest) == "/var/run/pixell-agents/agent_4906eeb7/rest.sock"
    assert str(sockets.a2a) == "/var/run/pixell-agents/agent_4906eeb7/a2a.sock"
    assert str(sockets.ui) == "/var/run/pixell-agents/agent_4906eeb7/ui.sock"


def test_create_agent_directory(tmp_path, monkeypatch):
    """Test directory creation with permissions."""
    monkeypatch.setenv("USER", "root")  # Simulate supervisor running as root

    allocator = SocketAllocator(base_dir=str(tmp_path))
    agent_id = "test-agent-1234"
    sockets = allocator.allocate(agent_id)

    allocator.create_agent_directory(sockets, "agent_test")

    # Verify directory exists
    assert sockets.base_dir.exists()
    assert sockets.base_dir.is_dir()

    # Verify permissions (750)
    assert oct(sockets.base_dir.stat().st_mode)[-3:] == "750"


def test_cleanup(tmp_path):
    """Test socket cleanup."""
    allocator = SocketAllocator(base_dir=str(tmp_path))
    agent_id = "test-agent-5678"
    sockets = allocator.allocate(agent_id)

    # Create directory
    sockets.base_dir.mkdir(parents=True)
    (sockets.rest).touch()

    # Cleanup
    allocator.cleanup(sockets)

    # Verify removed
    assert not sockets.base_dir.exists()


def test_hybrid_mode_deploy():
    """Test deploying both port and socket agents simultaneously."""
    state = SupervisorState()

    # Deploy agent 1 in port mode
    port_request = DeployRequest(
        agent_id="agent-port-1234",
        package_url="s3://...",
        ports=Ports(rest=63000, a2a=60000, ui=65000),
        socket_mode=False
    )
    agent1 = await state.deploy(port_request)
    assert agent1.ports is not None
    assert agent1.socket_paths is None

    # Deploy agent 2 in socket mode
    socket_request = DeployRequest(
        agent_id="agent-socket-5678",
        package_url="s3://...",
        socket_paths=SocketPaths(...),
        socket_mode=True
    )
    agent2 = await state.deploy(socket_request)
    assert agent2.socket_paths is not None
    assert agent2.ports is None

    # Both should be running
    assert agent1.status == "running"
    assert agent2.status == "running"
```

---

## ROLLBACK PROCEDURE

If issues occur during migration:

### Step 1: Stop New Socket Deployments (2 minutes)

```bash
# On PAC deployment worker
# Set environment variable to disable socket mode
export SOCKET_MODE_ENABLED=false

# Restart worker
systemctl restart pac-deployment-worker
```

### Step 2: Redeploy Affected Agents in Port Mode (5 minutes per agent)

```python
# Use PAC API to redeploy agents in port mode
import requests

agents_to_redeploy = ["agent-id-1", "agent-id-2", ...]

for agent_id in agents_to_redeploy:
    # Delete socket-based deployment
    requests.delete(f"http://10.0.1.37:9000/agents/{agent_id}")

    # Redeploy in port mode
    requests.post("http://10.0.1.37:9000/agents", json={
        "agent_id": agent_id,
        "package_url": "s3://...",
        "socket_mode": False,  # Force port mode
        ...
    })
```

### Step 3: Update ALB Rules (10 minutes)

```bash
# Point traffic back to per-agent target groups
aws elbv2 modify-rule \
  --rule-arn arn:aws:elasticloadbalancing:... \
  --conditions Field=path-pattern,Values='/agents/agent-id-*/rest/*' \
  --actions Type=forward,TargetGroupArn=arn:aws:elasticloadbalancing:.../pac-agent-xxx-rest
```

### Step 4: Remove Proxy from Routing (5 minutes)

```bash
# Deregister proxy target groups
aws elbv2 deregister-targets \
  --target-group-arn arn:aws:elasticloadbalancing:.../pixell-rest-proxy \
  --targets Id=i-09dcb7f387166efd0
```

### Step 5: Clean Up Sockets (2 minutes)

```bash
# On EC2 instance
sudo rm -rf /var/run/pixell-agents/*
```

**Total Rollback Time:** ~30 minutes per agent + infrastructure changes

---

## VALIDATION CHECKPOINTS

After each major change, validate:

### After Creating SocketAllocator

```bash
# Run unit tests
pytest tests/test_supervisor_socket_allocator.py -v

# Check for test failures
# Expected: All tests pass
```

### After Updating ProcessManager

```bash
# Deploy test agent in socket mode
curl -X POST http://10.0.1.37:9000/agents \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "test-socket-agent",
    "package_url": "s3://pixell-agent-packages/test.apkg",
    "socket_mode": true,
    ...
  }'

# Verify sockets created
ls -la /var/run/pixell-agents/agent_test/

# Expected output:
# drwxr-x--- 2 agent_test nginx   4096 Nov 11 12:00 .
# srw-rw---- 1 agent_test nginx      0 Nov 11 12:00 rest.sock
# srw-rw---- 1 agent_test nginx      0 Nov 11 12:00 a2a.sock
# srw-rw---- 1 agent_test nginx      0 Nov 11 12:00 ui.sock
```

### After Installing Nginx

```bash
# Test Nginx routing
curl -H "Host: par.pixell.global" \
  http://localhost:8080/agents/test-agent/rest/health

# Expected: 200 OK with agent health response

# Test gRPC routing
grpcurl -plaintext \
  -d '{"message": {...}}' \
  localhost:50051 \
  pixell.agent.AgentService/Invoke

# Expected: gRPC response from agent
```

### After ALB Updates

```bash
# Test ALB health check
aws elbv2 describe-target-health \
  --target-group-arn arn:aws:elasticloadbalancing:.../pixell-rest-proxy

# Expected:
# {
#   "TargetHealthDescriptions": [
#     {
#       "Target": {"Id": "i-09dcb7f387166efd0", "Port": 8080},
#       "HealthCheckPort": "8080",
#       "TargetHealth": {"State": "healthy"}
#     }
#   ]
# }
```

---

## CONCLUSION

This refactor is **CRITICAL** and affects 47+ files across 2 repositories. Key risks:

1. **Breaking production infrastructure** - Wrong VPC ID, subnet, or port will cause failures
2. **Permission issues** - Sockets must be 660 agent:nginx or Nginx can't connect
3. **ALB misconfiguration** - HTTP2 required for gRPC, wrong protocol causes 464 errors
4. **Hybrid mode complexity** - Must support both port and socket agents during migration

**Proceed with extreme caution. Test thoroughly in staging before production.**

---

**Document Status:** Complete
**Last Updated:** November 11, 2025
**Next Review:** After Phase 1 POC completion
**Implementation Status:** Not started (planning phase)
