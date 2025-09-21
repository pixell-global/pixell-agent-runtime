import asyncio
import json
import time
import os
import tempfile
import shutil
from typing import Dict, Any, AsyncIterator, Optional
import structlog
import docker
from docker.errors import ContainerError, ImageNotFound
import aiofiles

from ..engine.execution_engine import ExecutionResult, StreamChunk

logger = structlog.get_logger()


class ContainerExecutor:
    """Execute Python code in isolated Docker containers."""
    
    def __init__(self):
        self.docker_client = docker.from_env()
        self.image_name = "pixell-python-agent:latest"
        self.container_pool = []
        self.pool_size = 10
        self._init_lock = asyncio.Lock()
        self._initialized = False
        
    async def initialize(self):
        """Initialize container pool."""
        async with self._init_lock:
            if self._initialized:
                return
                
            # Build or pull image
            await self._ensure_image()
            
            # Pre-warm container pool
            await self._warm_pool()
            
            self._initialized = True
    
    async def execute(
        self,
        code: str,
        context: Dict[str, Any],
        resource_tier: int,
        timeout: int
    ) -> ExecutionResult:
        """Execute code in container and return results."""
        if not self._initialized:
            await self.initialize()
        
        start_time = time.monotonic()
        container = None
        
        try:
            # Get container from pool or create new
            container = await self._get_container(resource_tier)
            
            # Prepare execution environment
            work_dir = await self._prepare_workdir(code, context)
            
            # Execute code
            result = await self._run_in_container(
                container,
                work_dir,
                timeout
            )
            
            # Parse results
            execution_result = await self._parse_results(result, work_dir)
            
            # Calculate metrics
            execution_time = time.monotonic() - start_time
            execution_result.memory_used = await self._get_memory_usage(container)
            execution_result.cpu_percent = await self._get_cpu_usage(container)
            
            return execution_result
            
        except Exception as e:
            logger.exception("Container execution error")
            return ExecutionResult(
                success=False,
                error=str(e)
            )
        finally:
            # Cleanup
            if container:
                await self._return_container(container)
            if 'work_dir' in locals():
                shutil.rmtree(work_dir, ignore_errors=True)
    
    async def execute_stream(
        self,
        code: str,
        context: Dict[str, Any],
        resource_tier: int,
        timeout: int
    ) -> AsyncIterator[StreamChunk]:
        """Execute code with streaming output."""
        if not self._initialized:
            await self.initialize()
        
        container = None
        
        try:
            # Get container
            container = await self._get_container(resource_tier)
            
            # Prepare execution
            work_dir = await self._prepare_workdir(code, context)
            
            # Stream execution
            async for chunk in self._stream_execution(
                container,
                work_dir,
                timeout
            ):
                yield chunk
                
        except Exception as e:
            logger.exception("Streaming execution error")
            yield StreamChunk(
                type=4,  # ERROR
                data=str(e).encode(),
                timestamp=int(time.time() * 1000)
            )
        finally:
            if container:
                await self._return_container(container)
            if 'work_dir' in locals():
                shutil.rmtree(work_dir, ignore_errors=True)
    
    async def _ensure_image(self):
        """Ensure Docker image exists."""
        try:
            self.docker_client.images.get(self.image_name)
        except ImageNotFound:
            logger.info("Building Docker image", image=self.image_name)
            await self._build_image()
    
    async def _build_image(self):
        """Build Docker image for Python execution."""
        dockerfile_content = '''
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc g++ \
    libhdf5-dev \
    libatlas-base-dev \
    gfortran \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
RUN pip install --no-cache-dir \
    numpy==1.24.3 \
    pandas==2.0.3 \
    matplotlib==3.7.2 \
    scikit-learn==1.3.0 \
    scipy==1.11.1 \
    seaborn==0.12.2 \
    plotly==5.15.0 \
    polars==0.18.15 \
    duckdb==0.8.1 \
    pyarrow==12.0.1 \
    openpyxl==3.1.2 \
    xlrd==2.0.1 \
    requests==2.31.0 \
    beautifulsoup4==4.12.2 \
    lxml==4.9.3 \
    sqlalchemy==2.0.19 \
    psutil==5.9.5 \
    tqdm==4.65.0 \
    joblib==1.3.1 \
    numba==0.57.1 \
    cython==3.0.0

# Set working directory
WORKDIR /workspace

# Set Python to unbuffered mode
ENV PYTHONUNBUFFERED=1

# Create non-root user
RUN useradd -m -s /bin/bash runner
USER runner

CMD ["python"]
'''
        
        # Write Dockerfile
        with tempfile.TemporaryDirectory() as build_dir:
            dockerfile_path = os.path.join(build_dir, 'Dockerfile')
            with open(dockerfile_path, 'w') as f:
                f.write(dockerfile_content)
            
            # Build image
            self.docker_client.images.build(
                path=build_dir,
                tag=self.image_name,
                rm=True
            )
    
    async def _warm_pool(self):
        """Pre-warm container pool."""
        logger.info("Warming container pool", size=self.pool_size)
        
        for _ in range(self.pool_size):
            container = await self._create_container(0)  # SMALL tier
            self.container_pool.append(container)
    
    async def _get_container(self, resource_tier: int):
        """Get container from pool or create new."""
        if self.container_pool:
            return self.container_pool.pop()
        
        return await self._create_container(resource_tier)
    
    async def _create_container(self, resource_tier: int):
        """Create new container with resource limits."""
        # Resource configurations
        resources = {
            0: {'cpu_quota': 100000, 'mem_limit': '2g'},   # SMALL
            1: {'cpu_quota': 200000, 'mem_limit': '4g'},   # MEDIUM
            2: {'cpu_quota': 400000, 'mem_limit': '16g'},  # LARGE
        }
        
        config = resources.get(resource_tier, resources[0])
        
        container = self.docker_client.containers.create(
            self.image_name,
            command='sleep infinity',
            detach=True,
            remove=True,
            network_mode='none',  # No network access
            read_only=True,       # Read-only root filesystem
            tmpfs={'/tmp': 'size=1G,exec'},  # Writable /tmp
            **config
        )
        
        container.start()
        return container
    
    async def _return_container(self, container):
        """Return container to pool or destroy."""
        try:
            # Check if container is healthy
            container.reload()
            if container.status == 'running' and len(self.container_pool) < self.pool_size:
                # Clean container state
                container.exec_run('rm -rf /tmp/*')
                self.container_pool.append(container)
            else:
                container.remove(force=True)
        except:
            # Container might already be removed
            pass
    
    async def _prepare_workdir(self, code: str, context: Dict[str, Any]) -> str:
        """Prepare working directory with code and context."""
        work_dir = tempfile.mkdtemp(prefix='ppa_')
        
        # Write main execution script
        main_script = f'''
import sys
import json
import traceback
import pickle

# Load context
with open('/tmp/context.pkl', 'rb') as f:
    globals().update(pickle.load(f))

# Capture outputs
_results = {{}}

try:
    # Execute user code
{self._indent_code(code)}
    
    # Capture variables
    for name, value in list(locals().items()):
        if not name.startswith('_'):
            try:
                # Try to serialize for transfer
                json.dumps(value)
                _results[name] = value
            except:
                # Store string representation
                _results[name] = str(value)
    
    # Write results
    with open('/tmp/results.pkl', 'wb') as f:
        pickle.dump({{'success': True, 'results': _results}}, f)
        
except Exception as e:
    # Write error
    with open('/tmp/results.pkl', 'wb') as f:
        pickle.dump({{
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }}, f)
'''
        
        script_path = os.path.join(work_dir, 'main.py')
        async with aiofiles.open(script_path, 'w') as f:
            await f.write(main_script)
        
        # Write context
        context_path = os.path.join(work_dir, 'context.pkl')
        import pickle
        async with aiofiles.open(context_path, 'wb') as f:
            await f.write(pickle.dumps(context))
        
        return work_dir
    
    def _indent_code(self, code: str, indent: int = 4) -> str:
        """Indent code block."""
        lines = code.splitlines()
        return '\n'.join(' ' * indent + line for line in lines)
    
    async def _run_in_container(
        self,
        container,
        work_dir: str,
        timeout: int
    ) -> Dict[str, Any]:
        """Run code in container."""
        # Copy files to container
        os.system(f"docker cp {work_dir}/. {container.id}:/tmp/")
        
        # Execute
        result = container.exec_run(
            'python /tmp/main.py',
            stdout=True,
            stderr=True,
            demux=True
        )
        
        # Copy results back
        os.system(f"docker cp {container.id}:/tmp/results.pkl {work_dir}/")
        
        return {
            'stdout': result.output[0].decode() if result.output[0] else '',
            'stderr': result.output[1].decode() if result.output[1] else '',
            'exit_code': result.exit_code
        }
    
    async def _parse_results(self, result: Dict[str, Any], work_dir: str) -> ExecutionResult:
        """Parse execution results."""
        import pickle
        
        results_path = os.path.join(work_dir, 'results.pkl')
        
        if os.path.exists(results_path):
            async with aiofiles.open(results_path, 'rb') as f:
                data = pickle.loads(await f.read())
                
            if data.get('success'):
                return ExecutionResult(
                    success=True,
                    stdout=result['stdout'],
                    stderr=result['stderr'],
                    results=data.get('results', {})
                )
            else:
                return ExecutionResult(
                    success=False,
                    stdout=result['stdout'],
                    stderr=result['stderr'],
                    error=data.get('error', 'Unknown error')
                )
        else:
            return ExecutionResult(
                success=False,
                stdout=result['stdout'],
                stderr=result['stderr'],
                error='No results file generated'
            )
    
    async def _stream_execution(
        self,
        container,
        work_dir: str,
        timeout: int
    ) -> AsyncIterator[StreamChunk]:
        """Stream execution output."""
        # Copy files to container
        os.system(f"docker cp {work_dir}/. {container.id}:/tmp/")
        
        # Start execution
        exec_id = container.exec_run(
            'python /tmp/main.py',
            stdout=True,
            stderr=True,
            stream=True,
            detach=True
        )
        
        # Stream output
        for output in exec_id.output:
            if output:
                yield StreamChunk(
                    type=0,  # STDOUT
                    data=output,
                    timestamp=int(time.time() * 1000)
                )
    
    async def _get_memory_usage(self, container) -> int:
        """Get container memory usage."""
        try:
            stats = container.stats(stream=False)
            return stats['memory_stats'].get('usage', 0)
        except:
            return 0
    
    async def _get_cpu_usage(self, container) -> int:
        """Get container CPU usage percentage."""
        try:
            stats = container.stats(stream=False)
            # Calculate CPU percentage
            cpu_delta = stats['cpu_stats']['cpu_usage']['total_usage'] - \
                       stats['precpu_stats']['cpu_usage']['total_usage']
            system_delta = stats['cpu_stats']['system_cpu_usage'] - \
                          stats['precpu_stats']['system_cpu_usage']
            
            if system_delta > 0:
                cpu_percent = (cpu_delta / system_delta) * 100
                return int(cpu_percent)
            return 0
        except:
            return 0