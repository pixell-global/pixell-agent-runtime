"""Process management for PAR instances."""

import asyncio
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime
from typing import Dict, Optional, List
from pathlib import Path

from .models import PARProcess, ProcessState, ProcessConfig, PortAllocation
from .resource_manager import ResourceManager
from .log_aggregator import LogAggregator

logger = logging.getLogger(__name__)


class ProcessManager:
    """Manages lifecycle of PAR processes."""
    
    def __init__(self, base_port: int = 8001):
        self.processes: Dict[str, PARProcess] = {}
        self.port_allocation = PortAllocation(start_port=base_port)
        self._process_handles: Dict[str, subprocess.Popen] = {}
        self._monitor_task: Optional[asyncio.Task] = None
        self.log_aggregator = LogAggregator()
        
    async def start(self):
        """Start the process manager."""
        logger.info("Starting ProcessManager")
        await self.log_aggregator.start()
        self._monitor_task = asyncio.create_task(self._monitor_processes())
        
    async def stop(self):
        """Stop the process manager and all managed processes."""
        logger.info("Stopping ProcessManager")
        
        # Cancel monitoring
        if self._monitor_task:
            self._monitor_task.cancel()
            
        # Stop all processes
        for process_id in list(self.processes.keys()):
            await self.stop_process(process_id)
            
        # Stop log aggregator
        await self.log_aggregator.stop()
            
    async def spawn_process(self, config: ProcessConfig) -> PARProcess:
        """Spawn a new PAR process."""
        process_id = f"par-{config.agent_id}"
        
        # Check if already running
        if process_id in self.processes and self.processes[process_id].is_running:
            raise ValueError(f"Process {process_id} already running")
            
        # Allocate port
        port = self.port_allocation.allocate_port(process_id)
        if not port:
            raise RuntimeError("No available ports")
            
        # Create process record
        process = PARProcess(
            process_id=process_id,
            agent_id=config.agent_id,
            package_id=config.package_id,
            port=port,
            state=ProcessState.STARTING,
            started_at=datetime.utcnow(),
            config=config
        )
        
        self.processes[process_id] = process
        
        try:
            # Prepare environment
            env = os.environ.copy()
            env.update(config.env_vars)
            env.update({
                "PAR_PORT": str(port),
                "PAR_AGENT_ID": config.agent_id,
                "PAR_PACKAGE_ID": config.package_id,
                "PAR_PACKAGE_PATH": config.package_path,
                "PAR_MODE": "worker",  # Indicate this is a worker process
                "PAR_SUPERVISOR_URL": "http://localhost:8000"  # For A2A calls
            })
            
            # Start process
            worker_path = Path(__file__).parent.parent / "pixell_agent_runtime" / "worker.py"
            cmd = [
                sys.executable,
                str(worker_path),
                "--port", str(port),
                "--agent-id", config.agent_id,
                "--package-path", config.package_path
            ]
            
            logger.info(f"Spawning process {process_id} on port {port}")
            
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            self._process_handles[process_id] = proc
            process.pid = proc.pid
            process.state = ProcessState.RUNNING
            
            logger.info(f"Process {process_id} started with PID {proc.pid}")
            
            # Add to log aggregator
            self.log_aggregator.add_process(process_id, proc.stdout, proc.stderr)
            
            # Apply resource limits if configured
            if config.memory_limit_mb:
                ResourceManager.apply_memory_limit(proc.pid, config.memory_limit_mb)
            
            if config.cpu_limit:
                ResourceManager.apply_cpu_limit(proc.pid, config.cpu_limit)
                
            # Set process priority
            ResourceManager.set_process_nice(proc.pid, nice_value=10)
            
            return process
            
        except Exception as e:
            logger.error(f"Failed to spawn process {process_id}: {e}")
            process.state = ProcessState.FAILED
            process.error_message = str(e)
            self.port_allocation.release_port(port)
            raise
            
    async def stop_process(self, process_id: str, timeout: float = 30.0) -> None:
        """Stop a PAR process gracefully."""
        process = self.processes.get(process_id)
        if not process:
            return
            
        if process.state != ProcessState.RUNNING:
            return
            
        logger.info(f"Stopping process {process_id}")
        process.state = ProcessState.STOPPING
        
        proc = self._process_handles.get(process_id)
        if proc:
            try:
                # Send SIGTERM for graceful shutdown
                proc.terminate()
                
                # Wait for process to exit
                try:
                    await asyncio.wait_for(proc.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning(f"Process {process_id} didn't stop gracefully, killing")
                    proc.kill()
                    await proc.wait()
                    
            except Exception as e:
                logger.error(f"Error stopping process {process_id}: {e}")
                
            finally:
                del self._process_handles[process_id]
                
        process.state = ProcessState.STOPPED
        process.stopped_at = datetime.utcnow()
        self.port_allocation.release_port(process.port)
        
        # Remove from log aggregator
        self.log_aggregator.remove_process(process_id)
        
        # Clean up cgroup if it was created
        if process.pid:
            ResourceManager.cleanup_cgroup(process.pid)
        
    async def restart_process(self, process_id: str, config: ProcessConfig) -> PARProcess:
        """Restart a PAR process."""
        logger.info(f"Restarting process {process_id}")
        
        # Stop the process
        await self.stop_process(process_id)
        
        # Increment restart count
        old_process = self.processes.get(process_id)
        if old_process:
            config.max_restarts = old_process.restart_count + 1
            
        # Spawn new process
        return await self.spawn_process(config)
        
    async def _monitor_processes(self):
        """Monitor process health and restart if needed."""
        while True:
            try:
                for process_id, process in list(self.processes.items()):
                    if process.state != ProcessState.RUNNING:
                        continue
                        
                    proc = self._process_handles.get(process_id)
                    if not proc:
                        continue
                        
                    # Check if process is still running
                    if proc.returncode is not None:
                        logger.warning(f"Process {process_id} exited with code {proc.returncode}")
                        process.state = ProcessState.CRASHED
                        process.stopped_at = datetime.utcnow()
                        process.exit_code = proc.returncode
                        
                        # Clean up
                        if process_id in self._process_handles:
                            del self._process_handles[process_id]
                        self.port_allocation.release_port(process.port)
                        
                        # Check restart policy
                        if process.config and await self._should_restart(process):
                            asyncio.create_task(self._handle_restart(process))
                        
                await asyncio.sleep(5)  # Check every 5 seconds
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in process monitor: {e}")
                
    async def _should_restart(self, process: PARProcess) -> bool:
        """Determine if a process should be restarted based on policy."""
        if not process.config:
            return False
            
        policy = process.config.restart_policy
        
        # Never restart
        if policy == "never":
            return False
            
        # Always restart (up to max_restarts)
        if policy == "always":
            return process.restart_count < process.config.max_restarts
            
        # On-failure only
        if policy == "on-failure":
            # Consider non-zero exit codes as failures
            if process.exit_code and process.exit_code != 0:
                return process.restart_count < process.config.max_restarts
                
        return False
        
    async def _handle_restart(self, process: PARProcess):
        """Handle process restart with backoff."""
        try:
            # Calculate backoff delay
            delay = process.config.restart_delay_seconds
            if process.restart_count > 0:
                # Exponential backoff
                delay = min(
                    delay * (process.config.backoff_multiplier ** process.restart_count),
                    process.config.max_restart_delay_seconds
                )
                
            logger.info(f"Restarting process {process.process_id} in {delay} seconds (attempt {process.restart_count + 1})")
            
            # Wait before restart
            await asyncio.sleep(delay)
            
            # Update restart tracking
            process.restart_count += 1
            process.last_restart_at = datetime.utcnow()
            
            # Spawn new process
            new_process = await self.spawn_process(process.config)
            
            # Preserve restart count
            new_process.restart_count = process.restart_count
            new_process.last_restart_at = process.last_restart_at
            
            logger.info(f"Successfully restarted process {process.process_id}")
            
        except Exception as e:
            logger.error(f"Failed to restart process {process.process_id}: {e}")
            process.state = ProcessState.FAILED
            process.error_message = f"Restart failed: {str(e)}"
                
    def get_process_status(self) -> Dict[str, Dict]:
        """Get status of all processes."""
        status = {}
        for process_id, process in self.processes.items():
            process_status = {
                "agent_id": process.agent_id,
                "package_id": process.package_id,
                "port": process.port,
                "state": process.state.value,
                "pid": process.pid,
                "uptime": process.uptime,
                "restart_count": process.restart_count,
                "error": process.error_message
            }
            
            # Add resource usage if process is running
            if process.pid and process.is_running:
                resource_stats = ResourceManager.get_process_stats(process.pid)
                if "error" not in resource_stats:
                    process_status["resources"] = resource_stats
                    
            status[process_id] = process_status
            
        return status