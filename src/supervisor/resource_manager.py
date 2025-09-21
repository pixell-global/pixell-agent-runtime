"""Resource management for PAR processes."""

import os
import logging
import resource
import psutil
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)


class ResourceManager:
    """Manages resource limits and monitoring for PAR processes."""
    
    @staticmethod
    def apply_memory_limit(pid: int, limit_mb: int) -> bool:
        """Apply memory limit to a process (Linux only)."""
        try:
            # Check if we're on Linux and have cgroup v2
            cgroup_path = Path("/sys/fs/cgroup")
            if not cgroup_path.exists():
                logger.warning("cgroups not available, cannot apply memory limits")
                return False
                
            # Create a cgroup for the process
            par_cgroup = cgroup_path / "par" / str(pid)
            par_cgroup.mkdir(parents=True, exist_ok=True)
            
            # Set memory limit
            memory_max = par_cgroup / "memory.max"
            if memory_max.exists():
                memory_max.write_text(str(limit_mb * 1024 * 1024))
                
                # Add process to cgroup
                procs_file = par_cgroup / "cgroup.procs"
                procs_file.write_text(str(pid))
                
                logger.info(f"Applied memory limit of {limit_mb}MB to process {pid}")
                return True
            else:
                logger.warning("cgroup v2 memory controller not available")
                return False
                
        except Exception as e:
            logger.error(f"Failed to apply memory limit: {e}")
            return False
            
    @staticmethod
    def apply_cpu_limit(pid: int, cpu_limit: float) -> bool:
        """Apply CPU limit to a process (Linux only)."""
        try:
            # For Linux, use cgroups
            cgroup_path = Path("/sys/fs/cgroup")
            if not cgroup_path.exists():
                logger.warning("cgroups not available, cannot apply CPU limits")
                return False
                
            # Create a cgroup for the process
            par_cgroup = cgroup_path / "par" / str(pid)
            par_cgroup.mkdir(parents=True, exist_ok=True)
            
            # Set CPU limit (cpu.max format: "quota period")
            # cpu_limit of 0.5 = 50% of one CPU = 50000 100000
            cpu_max = par_cgroup / "cpu.max"
            if cpu_max.exists():
                quota = int(cpu_limit * 100000)
                cpu_max.write_text(f"{quota} 100000")
                
                # Add process to cgroup
                procs_file = par_cgroup / "cgroup.procs"
                procs_file.write_text(str(pid))
                
                logger.info(f"Applied CPU limit of {cpu_limit} cores to process {pid}")
                return True
            else:
                logger.warning("cgroup v2 CPU controller not available")
                return False
                
        except Exception as e:
            logger.error(f"Failed to apply CPU limit: {e}")
            return False
            
    @staticmethod
    def get_process_stats(pid: int) -> Dict[str, Any]:
        """Get resource usage statistics for a process."""
        try:
            process = psutil.Process(pid)
            
            # Get memory info
            memory_info = process.memory_info()
            memory_percent = process.memory_percent()
            
            # Get CPU info
            cpu_percent = process.cpu_percent(interval=0.1)
            cpu_times = process.cpu_times()
            
            # Get IO info (if available)
            try:
                io_counters = process.io_counters()
                io_stats = {
                    "read_bytes": io_counters.read_bytes,
                    "write_bytes": io_counters.write_bytes,
                    "read_count": io_counters.read_count,
                    "write_count": io_counters.write_count
                }
            except:
                io_stats = None
                
            return {
                "pid": pid,
                "status": process.status(),
                "memory": {
                    "rss_bytes": memory_info.rss,
                    "vms_bytes": memory_info.vms,
                    "percent": memory_percent
                },
                "cpu": {
                    "percent": cpu_percent,
                    "user_time": cpu_times.user,
                    "system_time": cpu_times.system
                },
                "io": io_stats,
                "num_threads": process.num_threads(),
                "num_fds": process.num_fds() if hasattr(process, 'num_fds') else None
            }
            
        except psutil.NoSuchProcess:
            return {"error": "Process not found"}
        except Exception as e:
            return {"error": str(e)}
            
    @staticmethod
    def cleanup_cgroup(pid: int):
        """Clean up cgroup after process termination."""
        try:
            cgroup_path = Path(f"/sys/fs/cgroup/par/{pid}")
            if cgroup_path.exists():
                # Remove the cgroup directory
                import shutil
                shutil.rmtree(cgroup_path)
                logger.info(f"Cleaned up cgroup for process {pid}")
        except Exception as e:
            logger.error(f"Failed to cleanup cgroup: {e}")
            
    @staticmethod
    def set_process_nice(pid: int, nice_value: int = 10):
        """Set process nice value for scheduling priority."""
        try:
            process = psutil.Process(pid)
            process.nice(nice_value)
            logger.info(f"Set nice value {nice_value} for process {pid}")
        except Exception as e:
            logger.error(f"Failed to set nice value: {e}")