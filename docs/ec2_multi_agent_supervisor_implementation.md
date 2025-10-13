# PAR Supervisor Implementation Plan
## EC2 Multi-Agent Architecture - Runtime Perspective

**Date**: 2025-10-12
**Status**: Implementation Plan
**Component**: PAR (Pixell Agent Runtime) Supervisor
**Duration**: 3-4 weeks for supervisor + testing

---

## Executive Summary

This document details **PAR's responsibility** in the EC2 multi-agent architecture migration. PAR will implement a **Supervisor** component that runs on EC2 instances and manages multiple agent processes with Linux user isolation.

**PAR's Role**: Data plane - receives deployment requests from PAC, manages agent lifecycle locally
**PAC's Role**: Control plane - orchestrates deployments, manages ALB routing (separate document)

---

## Current PAR Architecture (Fargate Per-Agent)

```
┌────────────────────────────────────────────────────┐
│ Fargate Container (1 agent = 1 container)         │
│                                                    │
│  ┌──────────────────────────────────────────────┐ │
│  │ PAR Three-Surface Runtime                    │ │
│  │ - ThreeSurfaceRuntime                        │ │
│  │ - PackageLoader (creates venv)               │ │
│  │ - REST server (port 8080)                    │ │
│  │ - gRPC server (port 50051)                   │ │
│  │ - UI server (port 3000)                      │ │
│  │                                               │ │
│  │ Process runs as: root (or container user)    │ │
│  │ Package: /tmp/pixell_packages/{name}@{ver}   │ │
│  │ Venv: /tmp/venvs/{agent_id}_{hash}           │ │
│  └──────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────┘
```

**Issues**:
- Venv created but agent imports use PAR's Python (sqlalchemy error!)
- No isolation needed (1 agent per container)
- Expensive ($17.77/agent/month)

---

## Target PAR Architecture (EC2 Multi-Agent)

```
┌────────────────────────────────────────────────────────────────┐
│ EC2 Instance (i-0bcf73bc143a8bb64)                             │
│                                                                 │
│ ┌─────────────────────────────────────────────────────────────┐│
│ │ PAR Supervisor (systemd service as root)                   ││
│ │                                                              ││
│ │  FastAPI HTTP Server (port 9000)                           ││
│ │  ├─ POST /agents - deploy new agent                        ││
│ │  ├─ PUT /agents/{id} - update agent                        ││
│ │  ├─ DELETE /agents/{id} - delete agent                     ││
│ │  ├─ GET /agents - list agents                              ││
│ │  └─ GET /health - supervisor health                        ││
│ │                                                              ││
│ │  Components:                                                 ││
│ │  ├─ SupervisorState - track all agents                     ││
│ │  ├─ LinuxUserManager - create/delete users                 ││
│ │  ├─ PortAllocator - assign unique ports                    ││
│ │  ├─ PackageDownloader - fetch APKGs from S3                ││
│ │  ├─ PackageLoader (reuse!) - venvs per user               ││
│ │  └─ ProcessManager - spawn/stop agent processes            ││
│ └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│ ┌─────────────────────────────────────────────────────────────┐│
│ │ Agent Processes (isolated Linux users)                     ││
│ │                                                              ││
│ │ agent_4906eeb7 (user: agent_4906eeb7, UID: 2001)          ││
│ │   ├─ Home: /home/agent_4906eeb7/                           ││
│ │   ├─ Venv: /home/agent_4906eeb7/venv/                      ││
│ │   ├─ Package: symlink → /var/lib/pixell/extracted/...      ││
│ │   ├─ Process: su agent_4906eeb7 -c "python -m ..."        ││
│ │   ├─ Ports: REST=8081, A2A=50052, UI=3001                  ││
│ │   └─ Runs PAR ThreeSurfaceRuntime in subprocess            ││
│ │                                                              ││
│ │ agent_abc123de (user: agent_abc123de, UID: 2002)          ││
│ │   ├─ Home: /home/agent_abc123de/                           ││
│ │   ├─ Venv: /home/agent_abc123de/venv/                      ││
│ │   ├─ Ports: REST=8082, A2A=50053, UI=3002                  ││
│ │   └─ ...                                                    ││
│ └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│ Shared Resources:                                               │
│ ├─ /var/lib/pixell/packages/ - APKG cache (all agents)        │
│ ├─ /var/lib/pixell/extracted/ - Extracted packages            │
│ └─ /var/lib/pixell/logs/ - Supervisor logs                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Phase 0: Quick Fix for Current Fargate (Week 1, Day 1-2)

### Problem: Agent Import Failures

**Current Issue**: `ModuleNotFoundError: No module named 'sqlalchemy'`
**Root Cause**: PAR creates venv but imports agent code using PAR's Python interpreter

**File**: `src/pixell_runtime/a2a/server.py:159`

```python
def _load_agent_service(package: AgentPackage) -> tuple:
    """Load agent's gRPC service or handlers."""
    # ... existing code ...

    # Add package path to sys.path for imports
    import sys
    from pathlib import Path
    package_path = Path(package.path)
    if str(package_path) not in sys.path:
        sys.path.insert(0, str(package_path))

    # ✅ NEW: Add venv site-packages to sys.path BEFORE importing
    if hasattr(package, 'venv_path') and package.venv_path:
        venv_site_packages = (
            Path(package.venv_path) / "lib" /
            f"python{sys.version_info.major}.{sys.version_info.minor}" /
            "site-packages"
        )
        if venv_site_packages.exists():
            sys.path.insert(0, str(venv_site_packages))
            logger.info("Added venv site-packages to sys.path",
                       path=str(venv_site_packages))

    # Now imports will find venv dependencies!
    module = __import__(module_path, fromlist=[function_name])
    # ... rest of function ...
```

**Apply same fix to**:
- `src/pixell_runtime/rest/server.py:234` (mount_agent_routes function)
- Any other locations that use `__import__` for agent code

**Testing**:
```bash
# Build and deploy
cd /Users/syum/dev/pixell-agent-runtime
./scripts/deploy_par.sh

# Test on running agent
curl https://agents.pixell.ai/agents/4906eeb7/health
# Should work without sqlalchemy errors
```

**Timeline**: 2-4 hours

---

## Phase 1: Supervisor Module Structure (Week 1, Day 3-5)

### 1.1 Create Supervisor Directory Structure

```
src/pixell_runtime/supervisor/
├── __init__.py
├── server.py           # FastAPI server, main entry point
├── state.py            # SupervisorState class
├── user_manager.py     # LinuxUserManager class
├── port_allocator.py   # PortAllocator class
├── package_downloader.py  # S3 download logic
├── process_manager.py  # Process spawning/monitoring
└── models.py           # Pydantic models
```

### 1.2 Supervisor Models (`models.py`)

```python
"""Pydantic models for supervisor API."""

from pydantic import BaseModel
from typing import Optional

class DeployRequest(BaseModel):
    """Request to deploy a new agent."""
    agent_app_id: str  # e.g., "4906eeb7-..."
    deployment_id: str  # e.g., "deploy-789"
    package_url: str  # s3://pixell-agent-packages/...
    version: str  # e.g., "1.0.0"
    org_id: str  # e.g., "org-123"

class UpdateRequest(BaseModel):
    """Request to update an existing agent."""
    package_url: str
    version: str

class Ports(BaseModel):
    """Port assignments for an agent."""
    rest: int  # 8081-8100
    a2a: int   # 50052-50071
    ui: int    # 3001-3020

class AgentProcess(BaseModel):
    """Represents a running agent process."""
    agent_id: str
    user_name: str
    ports: Ports
    process_id: int
    package_id: str
    venv_path: str
    status: str  # "running" | "stopped" | "failed"
    uptime_seconds: int

    class Config:
        arbitrary_types_allowed = True

class AgentStatus(BaseModel):
    """Agent status response."""
    agent_id: str
    status: str
    process_id: Optional[int]
    uptime_seconds: int
    memory_mb: float
    cpu_percent: float
    ports: Ports
    health: dict  # {rest: bool, a2a: bool, ui: bool}

class InstanceStatus(BaseModel):
    """Supervisor instance status."""
    agents_running: int
    capacity: dict  # {current: 8, max: 20, available: 12}
    instance_id: str
    uptime_seconds: int
    disk_free_gb: float
    memory_free_mb: float
    cpu_load: list  # [1.2, 1.5, 1.8]
```

### 1.3 Linux User Manager (`user_manager.py`)

```python
"""Linux user management for agent isolation."""

import subprocess
import structlog
from pathlib import Path

logger = structlog.get_logger()

class LinuxUserManager:
    """Manages Linux users for agent isolation."""

    def ensure_user(self, user_name: str) -> None:
        """Create Linux user if not exists (idempotent).

        Args:
            user_name: Username (e.g., "agent_4906eeb7")

        Raises:
            RuntimeError: If user creation fails
        """
        # Check if user exists
        result = subprocess.run(
            ["id", user_name],
            capture_output=True,
            check=False
        )

        if result.returncode == 0:
            logger.info("User already exists", user=user_name)
            return

        # Create user with home directory
        logger.info("Creating Linux user", user=user_name)

        try:
            subprocess.run([
                "useradd",
                "-m",              # Create home directory
                "-s", "/bin/bash", # Set shell
                "-U",              # Create group with same name
                user_name
            ], check=True, capture_output=True)

            logger.info("User created successfully",
                       user=user_name,
                       home=f"/home/{user_name}")

        except subprocess.CalledProcessError as e:
            logger.error("Failed to create user",
                        user=user_name,
                        error=e.stderr.decode())
            raise RuntimeError(f"Failed to create user {user_name}: {e.stderr.decode()}")

    def delete_user(self, user_name: str, delete_home: bool = False) -> None:
        """Delete Linux user.

        Args:
            user_name: Username to delete
            delete_home: If True, also delete home directory
        """
        logger.info("Deleting Linux user",
                   user=user_name,
                   delete_home=delete_home)

        cmd = ["userdel"]
        if delete_home:
            cmd.append("-r")  # Remove home directory and mail spool
        cmd.append(user_name)

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            logger.info("User deleted successfully", user=user_name)
        except subprocess.CalledProcessError as e:
            logger.warning("Failed to delete user",
                          user=user_name,
                          error=e.stderr.decode())
            # Don't raise - user deletion is best-effort

    def get_user_home(self, user_name: str) -> Path:
        """Get home directory for user.

        Args:
            user_name: Username

        Returns:
            Path to user's home directory
        """
        return Path(f"/home/{user_name}")
```

### 1.4 Port Allocator (`port_allocator.py`)

```python
"""Port allocation for agents."""

import structlog
from typing import Dict, Tuple, Optional
from .models import Ports

logger = structlog.get_logger()

class PortAllocator:
    """Allocates unique ports for agents."""

    def __init__(
        self,
        rest_range: Tuple[int, int] = (8081, 8100),
        a2a_range: Tuple[int, int] = (50052, 50071),
        ui_range: Tuple[int, int] = (3001, 3020)
    ):
        """Initialize port allocator.

        Args:
            rest_range: (min, max) for REST ports
            a2a_range: (min, max) for A2A/gRPC ports
            ui_range: (min, max) for UI ports
        """
        self.rest_range = rest_range
        self.a2a_range = a2a_range
        self.ui_range = ui_range
        self.allocated: Dict[str, Ports] = {}  # agent_id -> Ports

    def allocate(self, agent_id: str) -> Ports:
        """Allocate unique ports for agent.

        Args:
            agent_id: Agent app ID

        Returns:
            Allocated ports

        Raises:
            RuntimeError: If no ports available
        """
        # Find available ports
        used_rest = {p.rest for p in self.allocated.values()}
        used_a2a = {p.a2a for p in self.allocated.values()}
        used_ui = {p.ui for p in self.allocated.values()}

        rest_port = self._find_available_port(self.rest_range, used_rest)
        a2a_port = self._find_available_port(self.a2a_range, used_a2a)
        ui_port = self._find_available_port(self.ui_range, used_ui)

        ports = Ports(rest=rest_port, a2a=a2a_port, ui=ui_port)
        self.allocated[agent_id] = ports

        logger.info("Allocated ports",
                   agent_id=agent_id,
                   ports=ports.dict())

        return ports

    def release(self, agent_id: str) -> None:
        """Release ports for agent.

        Args:
            agent_id: Agent app ID
        """
        if agent_id in self.allocated:
            ports = self.allocated.pop(agent_id)
            logger.info("Released ports",
                       agent_id=agent_id,
                       ports=ports.dict())

    def get_available_capacity(self) -> int:
        """Get number of agents that can still be deployed.

        Returns:
            Number of available port sets
        """
        max_capacity = min(
            self.rest_range[1] - self.rest_range[0] + 1,
            self.a2a_range[1] - self.a2a_range[0] + 1,
            self.ui_range[1] - self.ui_range[0] + 1
        )
        return max_capacity - len(self.allocated)

    def _find_available_port(self, port_range: Tuple[int, int], used_ports: set) -> int:
        """Find first available port in range.

        Args:
            port_range: (min, max) port range
            used_ports: Set of already-used ports

        Returns:
            Available port number

        Raises:
            RuntimeError: If no ports available
        """
        for port in range(port_range[0], port_range[1] + 1):
            if port not in used_ports:
                return port

        raise RuntimeError(f"No available ports in range {port_range}")
```

**Timeline**: 3-4 days

---

## Phase 2: Package Management (Week 2, Day 1-3)

### 2.1 Package Downloader (`package_downloader.py`)

**Reuse existing PAR logic**:
- `src/pixell_runtime/deploy/fetch.py` - S3 download with retries
- `src/pixell_runtime/deploy/models.py` - PackageLocation models

```python
"""Package downloading for supervisor."""

import boto3
import structlog
from pathlib import Path
from typing import Optional

from pixell_runtime.deploy.fetch import fetch_package_to_path
from pixell_runtime.deploy.models import PackageLocation, PackageS3Ref

logger = structlog.get_logger()

class PackageDownloader:
    """Downloads agent packages from S3."""

    def __init__(self, cache_dir: Path):
        """Initialize downloader.

        Args:
            cache_dir: Directory to cache downloaded packages
        """
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def download(
        self,
        package_url: str,
        agent_id: str,
        version: str,
        sha256: Optional[str] = None
    ) -> Path:
        """Download package from S3 to local cache.

        Args:
            package_url: S3 URL (s3://bucket/key)
            agent_id: Agent app ID
            version: Package version
            sha256: Optional SHA256 for validation

        Returns:
            Path to downloaded package file

        Raises:
            RuntimeError: If download fails
        """
        # Check cache first
        cache_file = self.cache_dir / f"{agent_id}@{version}.apkg"
        if cache_file.exists():
            logger.info("Package already cached",
                       cache_file=str(cache_file))
            return cache_file

        # Parse S3 URL
        if not package_url.startswith("s3://"):
            raise ValueError(f"Only s3:// URLs supported, got: {package_url}")

        s3_parts = package_url[5:].split("/", 1)
        if len(s3_parts) != 2:
            raise ValueError(f"Invalid S3 URL: {package_url}")

        bucket, key = s3_parts

        # Download using existing fetch logic
        logger.info("Downloading package",
                   url=package_url,
                   dest=str(cache_file))

        try:
            location = PackageLocation(s3=PackageS3Ref(bucket=bucket, key=key))
            fetch_package_to_path(
                location,
                cache_file,
                sha256=sha256,
                max_size_bytes=100 * 1024 * 1024  # 100MB default
            )

            size_mb = cache_file.stat().st_size / 1024 / 1024
            logger.info("Package downloaded successfully",
                       cache_file=str(cache_file),
                       size_mb=round(size_mb, 2))

            return cache_file

        except Exception as e:
            logger.error("Package download failed",
                        url=package_url,
                        error=str(e))
            # Clean up partial download
            if cache_file.exists():
                cache_file.unlink()
            raise RuntimeError(f"Package download failed: {e}")
```

### 2.2 Reuse PackageLoader with User-Specific Venvs

**Key Insight**: PAR's existing `PackageLoader` already supports `agent_app_id` parameter!

```python
# From src/pixell_runtime/agents/loader.py:586
def _ensure_venv(self, package_id: str, package_path: Path, agent_app_id: Optional[str] = None) -> Path:
    """Create or reuse virtual environment for package."""
    # ...
    if agent_app_id:
        # Use agent_app_id to ensure uniqueness
        venv_name = f"{agent_app_id}_{req_hash}"  # ✅ Already does this!
    else:
        venv_name = f"{package_id}_{req_hash}"
    # ...
```

**Supervisor will**:
1. Extract package to `/var/lib/pixell/extracted/{agent_id}@{version}/`
2. Create `PackageLoader` with venvs_dir = `/home/agent_{id[:8]}/`
3. Call `loader.load_package(extracted_path, agent_app_id=agent_app_id)`
4. Loader creates venv at `/home/agent_{id[:8]}/venv/` ✅

**No changes needed to PackageLoader!** It already supports our use case.

**Timeline**: 2-3 days

---

## Phase 3: Process Management (Week 2, Day 4-5 + Week 3, Day 1-2)

### 3.1 Process Manager (`process_manager.py`)

```python
"""Agent process management."""

import asyncio
import os
import signal
import subprocess
import psutil
import structlog
from pathlib import Path
from typing import Optional, Dict

logger = structlog.get_logger()

class ProcessManager:
    """Manages agent process lifecycle."""

    async def spawn_agent(
        self,
        user_name: str,
        venv_path: Path,
        package_path: Path,
        agent_app_id: str,
        ports: 'Ports',
        env: dict
    ) -> subprocess.Popen:
        """Spawn agent process as specific Linux user.

        Args:
            user_name: Linux username (e.g., "agent_4906eeb7")
            venv_path: Path to virtual environment
            package_path: Path to extracted package
            agent_app_id: Agent app ID
            ports: Port assignments
            env: Environment variables

        Returns:
            Running process

        Raises:
            RuntimeError: If process spawn fails
        """
        logger.info("Spawning agent process",
                   user=user_name,
                   agent_id=agent_app_id,
                   ports=ports.dict())

        # Build command to run PAR as user
        python_bin = venv_path / "bin" / "python"

        # Agent runs: python -m pixell_runtime (subprocess mode)
        # Sets AGENT_PACKAGE_PATH to trigger __main__.py subprocess branch
        cmd = [
            "su", user_name, "-c",
            f"cd {package_path} && {python_bin} -m pixell_runtime"
        ]

        # Merge environment variables
        process_env = os.environ.copy()
        process_env.update({
            "AGENT_APP_ID": agent_app_id,
            "AGENT_PACKAGE_PATH": str(package_path),  # Triggers subprocess mode
            "AGENT_VENV_PATH": str(venv_path),  # Used by PackageLoader
            "BASE_PATH": env.get("BASE_PATH", f"/agents/{agent_app_id}"),
            "REST_PORT": str(ports.rest),
            "A2A_PORT": str(ports.a2a),
            "UI_PORT": str(ports.ui),
            "MULTIPLEXED": "true",  # Always multiplex
            # Pass through AWS credentials for S3 access
            "AWS_REGION": os.getenv("AWS_REGION", "us-east-2"),
            "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID", ""),
            "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        })

        # Add deployment metadata if provided
        if "DEPLOYMENT_ID" in env:
            process_env["DEPLOYMENT_ID"] = env["DEPLOYMENT_ID"]

        try:
            # Spawn process
            process = subprocess.Popen(
                cmd,
                env=process_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid  # Create new process group
            )

            logger.info("Process spawned",
                       user=user_name,
                       agent_id=agent_app_id,
                       pid=process.pid)

            # Wait for health check
            await self._wait_for_health(ports.rest, timeout=60)

            logger.info("Agent is healthy and ready",
                       agent_id=agent_app_id,
                       pid=process.pid)

            return process

        except Exception as e:
            logger.error("Failed to spawn process",
                        user=user_name,
                        agent_id=agent_app_id,
                        error=str(e))
            raise RuntimeError(f"Failed to spawn agent process: {e}")

    async def stop_agent(
        self,
        process: subprocess.Popen,
        timeout: int = 30
    ) -> None:
        """Gracefully stop agent process.

        Args:
            process: Process to stop
            timeout: Seconds to wait before force kill
        """
        if not process or process.poll() is not None:
            logger.warning("Process already stopped or invalid")
            return

        logger.info("Stopping agent process",
                   pid=process.pid,
                   timeout=timeout)

        try:
            # Send SIGTERM to process group
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)

            # Wait for graceful shutdown
            try:
                process.wait(timeout=timeout)
                logger.info("Process stopped gracefully", pid=process.pid)
            except subprocess.TimeoutExpired:
                # Force kill
                logger.warning("Graceful shutdown timeout, force killing",
                              pid=process.pid)
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                process.wait(timeout=5)
                logger.info("Process force killed", pid=process.pid)

        except Exception as e:
            logger.error("Error stopping process",
                        pid=process.pid,
                        error=str(e))

    async def _wait_for_health(self, rest_port: int, timeout: int = 60):
        """Wait for agent health endpoint to respond.

        Args:
            rest_port: REST port to check
            timeout: Seconds to wait

        Raises:
            TimeoutError: If health check fails
        """
        import httpx

        url = f"http://localhost:{rest_port}/health"
        deadline = asyncio.get_event_loop().time() + timeout

        logger.info("Waiting for agent health check", url=url, timeout=timeout)

        while asyncio.get_event_loop().time() < deadline:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=2.0)
                    if response.status_code == 200:
                        logger.info("Health check passed", url=url)
                        return
            except Exception:
                pass

            await asyncio.sleep(1)

        raise TimeoutError(f"Agent health check failed after {timeout}s")

    def get_process_stats(self, process: subprocess.Popen) -> dict:
        """Get process resource usage stats.

        Args:
            process: Process to check

        Returns:
            Dict with cpu_percent, memory_mb, uptime_seconds
        """
        try:
            ps = psutil.Process(process.pid)
            return {
                "cpu_percent": ps.cpu_percent(interval=0.1),
                "memory_mb": ps.memory_info().rss / 1024 / 1024,
                "uptime_seconds": ps.create_time() - psutil.boot_time()
            }
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return {
                "cpu_percent": 0.0,
                "memory_mb": 0.0,
                "uptime_seconds": 0
            }
```

**Timeline**: 3-4 days

---

## Phase 4: Supervisor State & Server (Week 3, Day 3-5)

### 4.1 Supervisor State (`state.py`)

```python
"""Supervisor state management."""

import asyncio
import structlog
from pathlib import Path
from typing import Dict, Optional

from .models import DeployRequest, UpdateRequest, AgentProcess, Ports
from .user_manager import LinuxUserManager
from .port_allocator import PortAllocator
from .package_downloader import PackageDownloader
from .process_manager import ProcessManager

from pixell_runtime.agents.loader import PackageLoader

logger = structlog.get_logger()

class SupervisorState:
    """Maintains state of all agents on this instance."""

    def __init__(
        self,
        packages_dir: Path,
        extracted_dir: Path
    ):
        """Initialize supervisor state.

        Args:
            packages_dir: Directory for package cache
            extracted_dir: Directory for extracted packages
        """
        self.agents: Dict[str, AgentProcess] = {}
        self.port_allocator = PortAllocator()
        self.user_manager = LinuxUserManager()
        self.package_downloader = PackageDownloader(packages_dir)
        self.process_manager = ProcessManager()
        self.packages_dir = packages_dir
        self.extracted_dir = extracted_dir

        # Create directories
        packages_dir.mkdir(parents=True, exist_ok=True)
        extracted_dir.mkdir(parents=True, exist_ok=True)

    async def deploy_agent(self, request: DeployRequest) -> AgentProcess:
        """Deploy new agent on this instance.

        Args:
            request: Deployment request

        Returns:
            Running agent process

        Raises:
            RuntimeError: If deployment fails
        """
        agent_id = request.agent_app_id

        logger.info("Starting agent deployment",
                   agent_id=agent_id,
                   version=request.version)

        # 1. Allocate ports
        ports = self.port_allocator.allocate(agent_id)
        logger.info("Ports allocated", agent_id=agent_id, ports=ports.dict())

        # 2. Create Linux user (idempotent)
        user_name = f"agent_{agent_id[:8]}"
        self.user_manager.ensure_user(user_name)
        logger.info("User ready", user=user_name)

        try:
            # 3. Download package from S3
            cache_file = await self.package_downloader.download(
                request.package_url,
                agent_id,
                request.version
            )
            logger.info("Package downloaded", cache_file=str(cache_file))

            # 4. Extract package
            package_id = f"{agent_id}@{request.version}"
            extract_path = self.extracted_dir / package_id

            if extract_path.exists():
                logger.info("Package already extracted", path=str(extract_path))
            else:
                import zipfile
                with zipfile.ZipFile(cache_file, 'r') as zf:
                    zf.extractall(extract_path)
                logger.info("Package extracted", path=str(extract_path))

            # 5. Load package and create venv (in user's home directory)
            user_home = self.user_manager.get_user_home(user_name)
            loader = PackageLoader(
                packages_dir=self.packages_dir,
                venvs_dir=user_home  # ✅ Venv will be created in user's home!
            )

            package = loader.load_package(extract_path, agent_app_id=agent_id)
            logger.info("Package loaded",
                       package_id=package.id,
                       venv=package.venv_path)

            # 6. Change venv ownership to user
            import subprocess
            venv_path = Path(package.venv_path)
            subprocess.run([
                "chown", "-R",
                f"{user_name}:{user_name}",
                str(venv_path)
            ], check=True)
            logger.info("Venv ownership changed", user=user_name, venv=str(venv_path))

            # 7. Spawn agent process as user
            process = await self.process_manager.spawn_agent(
                user_name=user_name,
                venv_path=venv_path,
                package_path=Path(package.path),
                agent_app_id=agent_id,
                ports=ports,
                env={
                    "DEPLOYMENT_ID": request.deployment_id,
                    "BASE_PATH": f"/agents/{agent_id}",
                }
            )

            # 8. Track agent
            agent_process = AgentProcess(
                agent_id=agent_id,
                user_name=user_name,
                ports=ports,
                process_id=process.pid,
                package_id=package.id,
                venv_path=str(venv_path),
                status="running",
                uptime_seconds=0
            )
            self.agents[agent_id] = agent_process

            logger.info("Agent deployed successfully",
                       agent_id=agent_id,
                       pid=process.pid,
                       ports=ports.dict())

            return agent_process

        except Exception as e:
            # Cleanup on failure
            logger.error("Deployment failed, cleaning up",
                        agent_id=agent_id,
                        error=str(e))
            self.port_allocator.release(agent_id)
            # Don't delete user - can be reused
            raise RuntimeError(f"Agent deployment failed: {e}")

    async def update_agent(
        self,
        agent_id: str,
        request: UpdateRequest
    ) -> None:
        """Update agent to new version (zero downtime).

        Args:
            agent_id: Agent app ID
            request: Update request

        Raises:
            RuntimeError: If update fails
        """
        agent = self.agents.get(agent_id)
        if not agent:
            raise RuntimeError(f"Agent {agent_id} not found")

        logger.info("Starting agent update",
                   agent_id=agent_id,
                   old_version=agent.package_id,
                   new_version=request.version)

        # 1. Download new package
        cache_file = await self.package_downloader.download(
            request.package_url,
            agent_id,
            request.version
        )

        # 2. Extract new package
        package_id = f"{agent_id}@{request.version}"
        extract_path = self.extracted_dir / package_id

        import zipfile
        with zipfile.ZipFile(cache_file, 'r') as zf:
            zf.extractall(extract_path)

        # 3. Load new package (may recreate venv if requirements changed)
        user_home = self.user_manager.get_user_home(agent.user_name)
        loader = PackageLoader(
            packages_dir=self.packages_dir,
            venvs_dir=user_home
        )
        new_package = loader.load_package(extract_path, agent_app_id=agent_id)

        # 4. Stop old process
        old_process = psutil.Process(agent.process_id)
        await self.process_manager.stop_agent(old_process)

        # 5. Spawn new process (reuse same user & ports)
        process = await self.process_manager.spawn_agent(
            user_name=agent.user_name,
            venv_path=Path(new_package.venv_path),
            package_path=Path(new_package.path),
            agent_app_id=agent_id,
            ports=agent.ports,
            env={"BASE_PATH": f"/agents/{agent_id}"}
        )

        # 6. Update tracking
        agent.process_id = process.pid
        agent.package_id = new_package.id
        agent.venv_path = new_package.venv_path

        logger.info("Agent updated successfully",
                   agent_id=agent_id,
                   new_pid=process.pid,
                   new_version=request.version)

    async def delete_agent(self, agent_id: str) -> None:
        """Delete agent and cleanup resources.

        Args:
            agent_id: Agent app ID
        """
        agent = self.agents.get(agent_id)
        if not agent:
            logger.warning("Agent not found for deletion", agent_id=agent_id)
            return

        logger.info("Deleting agent", agent_id=agent_id)

        # Stop process
        try:
            process = psutil.Process(agent.process_id)
            await self.process_manager.stop_agent(process)
        except psutil.NoSuchProcess:
            logger.warning("Process already stopped", agent_id=agent_id)

        # Release ports
        self.port_allocator.release(agent_id)

        # Remove from tracking
        del self.agents[agent_id]

        # NOTE: We keep the Linux user and venv for fast redeployment
        # They will be reused if agent is deployed again

        logger.info("Agent deleted", agent_id=agent_id)
```

### 4.2 Supervisor Server (`server.py`)

```python
"""Supervisor HTTP API server."""

import os
import asyncio
import psutil
import structlog
from fastapi import FastAPI, HTTPException
from pathlib import Path

from .state import SupervisorState
from .models import (
    DeployRequest, UpdateRequest, AgentStatus, InstanceStatus
)

logger = structlog.get_logger()

# Create FastAPI app
app = FastAPI(title="PAR Supervisor", version="0.1.0")

# Global state
state: SupervisorState = None

@app.on_event("startup")
async def startup():
    """Initialize supervisor state on startup."""
    global state

    packages_dir = Path(os.getenv("PACKAGE_CACHE_DIR", "/var/lib/pixell/packages"))
    extracted_dir = Path(os.getenv("PACKAGE_EXTRACT_DIR", "/var/lib/pixell/extracted"))

    state = SupervisorState(packages_dir, extracted_dir)
    logger.info("Supervisor initialized",
               packages_dir=str(packages_dir),
               extracted_dir=str(extracted_dir))

@app.post("/agents")
async def deploy_agent(request: DeployRequest):
    """Deploy new agent on this instance."""
    try:
        agent = await state.deploy_agent(request)
        return {
            "agent_id": agent.agent_id,
            "status": "running",
            "ports": agent.ports.dict(),
            "linux_user": agent.user_name,
            "process_id": agent.process_id
        }
    except Exception as e:
        logger.error("Deployment failed", agent_id=request.agent_app_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/agents/{agent_id}")
async def update_agent(agent_id: str, request: UpdateRequest):
    """Update agent to new version."""
    try:
        await state.update_agent(agent_id, request)
        return {"agent_id": agent_id, "status": "updated"}
    except RuntimeError as e:
        raise HTTPException(status_code=404 if "not found" in str(e) else 500, detail=str(e))

@app.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str):
    """Delete agent."""
    await state.delete_agent(agent_id)
    return {"agent_id": agent_id, "status": "deleted"}

@app.get("/agents/{agent_id}/status")
async def get_agent_status(agent_id: str):
    """Get agent status."""
    agent = state.agents.get(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Get process stats
    try:
        stats = state.process_manager.get_process_stats(
            psutil.Process(agent.process_id)
        )
    except:
        stats = {"cpu_percent": 0, "memory_mb": 0, "uptime_seconds": 0}

    return AgentStatus(
        agent_id=agent.agent_id,
        status=agent.status,
        process_id=agent.process_id,
        uptime_seconds=stats["uptime_seconds"],
        memory_mb=stats["memory_mb"],
        cpu_percent=stats["cpu_percent"],
        ports=agent.ports,
        health={"rest": True, "a2a": True, "ui": True}  # TODO: actual health checks
    )

@app.get("/agents")
async def list_agents():
    """List all agents on this instance."""
    agents = []
    for agent_id, agent in state.agents.items():
        agents.append({
            "agent_id": agent.agent_id,
            "status": agent.status,
            "ports": agent.ports.dict(),
            "user": agent.user_name
        })

    capacity = state.port_allocator.get_available_capacity()

    return {
        "agents": agents,
        "capacity": {
            "current": len(agents),
            "max": 20,  # TODO: make configurable
            "available": capacity
        }
    }

@app.get("/health")
async def health():
    """Supervisor health check."""
    disk = psutil.disk_usage("/")
    memory = psutil.virtual_memory()

    return {
        "status": "healthy",
        "agents_running": len(state.agents),
        "disk_free_gb": disk.free / 1024 / 1024 / 1024,
        "memory_free_mb": memory.available / 1024 / 1024,
        "cpu_load": psutil.getloadavg()
    }
```

**Start supervisor** (systemd service will use this):
```python
# Add to supervisor/__init__.py
def start_supervisor():
    """Start supervisor server (called by systemd)."""
    import uvicorn
    uvicorn.run(
        "pixell_runtime.supervisor.server:app",
        host="0.0.0.0",
        port=9000,
        log_config=None
    )
```

**Timeline**: 3-4 days

---

## Phase 5: Testing (Week 4)

### 5.1 Unit Tests

**Location**: `tests/supervisor/`

```python
# tests/supervisor/test_user_manager.py
def test_create_user():
    """Test Linux user creation."""
    manager = LinuxUserManager()
    manager.ensure_user("test_agent_12345678")
    # Verify user exists: id test_agent_12345678

def test_user_creation_idempotent():
    """Test user creation is idempotent."""
    manager = LinuxUserManager()
    manager.ensure_user("test_agent_12345678")
    manager.ensure_user("test_agent_12345678")  # Should not fail

# tests/supervisor/test_port_allocator.py
def test_port_allocation():
    """Test port allocation is unique."""
    allocator = PortAllocator()
    ports1 = allocator.allocate("agent1")
    ports2 = allocator.allocate("agent2")
    assert ports1.rest != ports2.rest
    assert ports1.a2a != ports2.a2a

# tests/supervisor/test_deployment_flow.py
@pytest.mark.asyncio
async def test_deploy_agent_end_to_end():
    """Test complete deployment flow."""
    # Mock S3 download
    # Create test package
    # Deploy via supervisor API
    # Verify agent is running
    # Verify health endpoint works
```

### 5.2 Integration Tests on EC2

**Prerequisites**:
- PAR supervisor installed on `i-0bcf73bc143a8bb64`
- Security groups configured
- SSH access set up

**Test Script**:
```bash
#!/bin/bash
# tests/integration/test_supervisor_ec2.sh

# Test 1: Supervisor health
curl http://172.31.0.148:9000/health

# Test 2: Deploy test agent
curl -X POST http://172.31.0.148:9000/agents \
  -H "Content-Type: application/json" \
  -d '{
    "agent_app_id": "test-12345678-abcd",
    "deployment_id": "test-deploy-1",
    "package_url": "s3://pixell-agent-packages/test/package.apkg",
    "version": "1.0.0",
    "org_id": "org-test"
  }'

# Test 3: Check agent health
sleep 10
curl http://172.31.0.148:8081/health

# Test 4: Update agent
curl -X PUT http://172.31.0.148:9000/agents/test-12345678-abcd \
  -H "Content-Type: application/json" \
  -d '{
    "package_url": "s3://pixell-agent-packages/test/package-v2.apkg",
    "version": "1.0.1"
  }'

# Test 5: Delete agent
curl -X DELETE http://172.31.0.148:9000/agents/test-12345678-abcd
```

**Timeline**: 5-7 days

---

## Installation & Deployment

### Systemd Service

**File**: `/etc/systemd/system/pixell-supervisor.service`

```ini
[Unit]
Description=Pixell Agent Runtime Supervisor
After=network.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/pixell-agent-runtime

# Environment variables
Environment="PYTHONUNBUFFERED=1"
Environment="PACKAGE_CACHE_DIR=/var/lib/pixell/packages"
Environment="PACKAGE_EXTRACT_DIR=/var/lib/pixell/extracted"
Environment="SUPERVISOR_PORT=9000"
Environment="SUPERVISOR_HOST=0.0.0.0"
Environment="AWS_REGION=us-east-2"
Environment="S3_BUCKET=pixell-agent-packages"
Environment="MAX_AGENTS=20"

# Start supervisor
ExecStart=/usr/bin/python3.11 -m pixell_runtime.supervisor.server

# Restart policy
Restart=always
RestartSec=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=pixell-supervisor

# Security (running as root for useradd)
NoNewPrivileges=false
PrivateTmp=true
ProtectSystem=strict
ProtectHome=false
ReadWritePaths=/var/lib/pixell /home

[Install]
WantedBy=multi-user.target
```

### Installation Commands

```bash
# On EC2 instance (i-0bcf73bc143a8bb64)
ssh syum@18.191.128.137

# Update PAR code
cd /opt/pixell-agent-runtime
git pull origin feat/ec2-multi-agent

# Install PAR
pip3.11 install -e .

# Restart supervisor
sudo systemctl daemon-reload
sudo systemctl restart pixell-supervisor

# Check status
sudo systemctl status pixell-supervisor
sudo journalctl -u pixell-supervisor -f

# Test health
curl http://172.31.0.148:9000/health
```

---

## Success Metrics

### Performance
- ✅ Agent deployment time: <30s
- ✅ Agent update time: <5s for code-only changes
- ✅ Zero downtime updates: 100% success rate
- ✅ Process isolation: Cannot access other agents' files

### Reliability
- ✅ Supervisor uptime: >99.9%
- ✅ Agent crash recovery: Automatic restart via supervisor
- ✅ Port allocation: No conflicts, 100% unique

### Resource Usage
- ✅ Supervisor memory: <100MB
- ✅ Supervisor CPU: <5% average
- ✅ Max agents per instance: 20 (configurable)

---

## Key Design Decisions

### 1. Reuse Existing PAR Components
- ✅ `PackageLoader` - already supports agent_app_id and venv isolation
- ✅ `fetch_package_to_path` - S3 download with retries
- ✅ `ThreeSurfaceRuntime` - agents run this in subprocess mode
- ❌ Don't reinvent the wheel - leverage existing tested code!

### 2. User Persistence
- ✅ Keep Linux users after agent deletion
- ✅ Fast redeployment (user already exists)
- ❌ Don't delete users on every update - unnecessary overhead

### 3. Venv Location
- ✅ `/home/agent_{id[:8]}/venv/` - owned by user, isolated
- ❌ Not `/tmp/` - would be lost on updates
- ✅ PackageLoader's `venvs_dir` parameter supports this!

### 4. Process Spawning
- ✅ Use `su` command to run as specific user
- ✅ Agent runs `python -m pixell_runtime` (existing entrypoint)
- ✅ Set `AGENT_PACKAGE_PATH` to trigger subprocess mode

### 5. Port Management
- ✅ Dynamic allocation by supervisor
- ✅ PAC registers ports with ALB target groups
- ✅ Ports persist across updates (same agent = same ports)

---

## Next Steps

1. **Review this plan** with team
2. **Apply Phase 0 quick fix** for sqlalchemy import
3. **Create feature branch**: `feat/ec2-supervisor`
4. **Start Phase 1**: Implement supervisor modules
5. **Weekly sync** to track progress

---

## Appendix: PAR Codebase Structure

```
pixell-agent-runtime/
├── src/pixell_runtime/
│   ├── __main__.py              # CLI entrypoint (par run, subprocess mode)
│   ├── three_surface/
│   │   └── runtime.py           # ThreeSurfaceRuntime (agents run this)
│   ├── agents/
│   │   └── loader.py            # PackageLoader (reuse for supervisor!)
│   ├── deploy/
│   │   ├── fetch.py             # S3 download (reuse!)
│   │   └── models.py            # PackageLocation models
│   ├── a2a/
│   │   └── server.py            # gRPC server (FIX: add venv site-packages to sys.path)
│   ├── rest/
│   │   └── server.py            # REST server (FIX: same as a2a/server.py)
│   └── supervisor/              # ✅ NEW - implement in this plan
│       ├── server.py
│       ├── state.py
│       ├── user_manager.py
│       ├── port_allocator.py
│       ├── package_downloader.py
│       ├── process_manager.py
│       └── models.py
└── tests/
    └── supervisor/               # ✅ NEW - unit tests
```

---

**Document Version**: 1.0
**Last Updated**: 2025-10-12
**Author**: PAR Team
**Reviewers**: [To be filled]
