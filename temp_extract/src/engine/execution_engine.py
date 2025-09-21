import asyncio
import time
import re
from typing import Dict, Any, List, AsyncIterator, Optional
from dataclasses import dataclass
import structlog
import ast

from ..sessions.manager import Session
from ..executor.container import ContainerExecutor
from .patterns import CodePatternDetector
from .context import ExecutionContext

logger = structlog.get_logger()


@dataclass
class ExecutionResult:
    """Result of code execution."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    results: Dict[str, Any] = None
    error: Optional[str] = None
    memory_used: int = 0
    cpu_percent: int = 0
    
    def __post_init__(self):
        if self.results is None:
            self.results = {}


@dataclass
class StreamChunk:
    """Chunk of streaming output."""
    type: int  # Maps to protobuf ChunkType
    data: bytes
    timestamp: int


@dataclass
class ExecutionPlan:
    """Execution plan with optimizations."""
    code: str
    resource_tier: int
    timeout: int
    optimizations: List[str]
    estimated_memory: int


class ExecutionEngine:
    """U-P-E-E (Understand, Plan, Execute, Evaluate) engine."""
    
    def __init__(self):
        self.pattern_detector = CodePatternDetector()
        self.container_executor = ContainerExecutor()
        self.retry_count = 2
        
    async def execute_full(
        self,
        code: str,
        context: Dict[str, Any],
        files: List[bytes],
        session: Session,
        resource_tier: int,
        timeout: int
    ) -> ExecutionResult:
        """Execute code through full U-P-E-E pipeline."""
        try:
            # Understand phase
            exec_context = await self.understand(code, context, files, session)
            
            # Plan phase
            plan = await self.plan(exec_context)
            
            # Execute phase with retries
            result = None
            for attempt in range(self.retry_count + 1):
                result = await self.execute(plan, exec_context, session)
                
                # Evaluate phase
                if result.success:
                    break
                    
                retry_plan = await self.evaluate(result, plan, attempt)
                if not retry_plan:
                    break
                    
                plan = retry_plan
                logger.info("Retrying execution", attempt=attempt + 1)
            
            # Update session state
            if result and result.success:
                session.variables.update(result.results)
                session.execution_count += 1
                session.last_code = code
                session.last_error = None
            elif result:
                session.last_error = result.error
            
            return result or ExecutionResult(success=False, error="No execution result")
            
        except Exception as e:
            logger.exception("Error in execution pipeline")
            return ExecutionResult(success=False, error=str(e))
    
    async def execute_stream(
        self,
        code: str,
        context: Dict[str, Any],
        files: List[bytes],
        session: Session,
        resource_tier: int,
        timeout: int
    ) -> AsyncIterator[StreamChunk]:
        """Execute code with streaming output."""
        try:
            # Understand and plan
            exec_context = await self.understand(code, context, files, session)
            plan = await self.plan(exec_context)
            
            # Stream execution
            async for chunk in self.container_executor.execute_stream(
                plan.code,
                exec_context.full_context,
                plan.resource_tier,
                plan.timeout
            ):
                yield chunk
                
            # Update session on completion
            session.execution_count += 1
            session.last_code = code
            
        except Exception as e:
            logger.exception("Error in streaming execution")
            yield StreamChunk(
                type=4,  # ERROR
                data=str(e).encode(),
                timestamp=int(time.time() * 1000)
            )
    
    async def understand(
        self,
        code: str,
        context: Dict[str, Any],
        files: List[bytes],
        session: Session
    ) -> ExecutionContext:
        """Understand phase: analyze code and context."""
        # Detect patterns in code
        patterns = self.pattern_detector.detect(code)
        
        # Merge contexts
        full_context = {**session.variables, **context}
        
        # Analyze data shapes if present
        data_info = await self._analyze_data(full_context)
        
        return ExecutionContext(
            code=code,
            patterns=patterns,
            full_context=full_context,
            files=files,
            data_info=data_info,
            session_id=session.session_id
        )
    
    async def plan(self, context: ExecutionContext) -> ExecutionPlan:
        """Plan phase: create optimized execution plan."""
        # Determine resource needs
        resource_tier = self._estimate_resource_tier(context)
        
        # Apply optimizations
        optimized_code = self._optimize_code(context.code, context.patterns)
        
        # Set timeout based on complexity
        timeout = self._estimate_timeout(context)
        
        return ExecutionPlan(
            code=optimized_code,
            resource_tier=resource_tier,
            timeout=timeout,
            optimizations=context.patterns,
            estimated_memory=self._estimate_memory(context)
        )
    
    async def execute(
        self,
        plan: ExecutionPlan,
        context: ExecutionContext,
        session: Session
    ) -> ExecutionResult:
        """Execute phase: run code in container."""
        return await self.container_executor.execute(
            plan.code,
            context.full_context,
            plan.resource_tier,
            plan.timeout
        )
    
    async def evaluate(
        self,
        result: ExecutionResult,
        plan: ExecutionPlan,
        attempt: int
    ) -> Optional[ExecutionPlan]:
        """Evaluate phase: analyze errors and create retry plan."""
        if not result.error or attempt >= self.retry_count:
            return None
        
        # Pattern-based error detection
        error_type = self._classify_error(result.error)
        
        # Generate patch based on error type
        patched_code = self._generate_patch(plan.code, error_type, result.error)
        
        if patched_code and patched_code != plan.code:
            return ExecutionPlan(
                code=patched_code,
                resource_tier=plan.resource_tier,
                timeout=plan.timeout * 2,  # Increase timeout on retry
                optimizations=plan.optimizations + [f"retry_{error_type}"],
                estimated_memory=plan.estimated_memory
            )
        
        return None
    
    async def retry_with_patch(self, session: Session, patch: Any) -> ExecutionResult:
        """Apply patch and retry execution."""
        if not session.last_code:
            return ExecutionResult(success=False, error="No previous execution to retry")
        
        # Apply patches to last code
        patched_code = self._apply_patches(session.last_code, patch)
        
        # Re-execute with patched code
        return await self.execute_full(
            code=patched_code,
            context={},
            files=[],
            session=session,
            resource_tier=1,  # MEDIUM
            timeout=60
        )
    
    async def _analyze_data(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze data shapes in context."""
        data_info = {}
        
        for key, value in context.items():
            if hasattr(value, 'shape'):  # numpy/pandas
                data_info[key] = {
                    'type': type(value).__name__,
                    'shape': getattr(value, 'shape', None),
                    'size': getattr(value, 'size', len(value) if hasattr(value, '__len__') else None)
                }
        
        return data_info
    
    def _estimate_resource_tier(self, context: ExecutionContext) -> int:
        """Estimate required resource tier."""
        # Check data sizes
        total_size = sum(
            info.get('size', 0) for info in context.data_info.values()
        )
        
        if total_size > 1_000_000 or 'large_data' in context.patterns:
            return 2  # LARGE
        elif total_size > 100_000 or 'medium_data' in context.patterns:
            return 1  # MEDIUM
        else:
            return 0  # SMALL
    
    def _estimate_timeout(self, context: ExecutionContext) -> int:
        """Estimate execution timeout."""
        if 'ml_training' in context.patterns or 'large_data' in context.patterns:
            return 600  # 10 minutes
        elif 'data_processing' in context.patterns:
            return 300  # 5 minutes
        else:
            return 60  # 1 minute
    
    def _estimate_memory(self, context: ExecutionContext) -> int:
        """Estimate memory requirements."""
        base_memory = 100 * 1024 * 1024  # 100MB base
        
        # Add data sizes
        for info in context.data_info.values():
            size = info.get('size', 0)
            if info.get('type') in ['DataFrame', 'ndarray']:
                base_memory += size * 8 * 2  # Assume 8 bytes per element, 2x for processing
        
        return base_memory
    
    def _optimize_code(self, code: str, patterns: List[str]) -> str:
        """Apply code optimizations based on patterns."""
        optimized = code
        
        # Pandas optimizations
        if 'pandas' in patterns:
            # Use .loc instead of chained indexing
            optimized = re.sub(
                r'(\w+)\[([^\]]+)\]\[([^\]]+)\]',
                r'\1.loc[\2, \3]',
                optimized
            )
        
        # Add more optimizations as needed
        return optimized
    
    def _classify_error(self, error: str) -> str:
        """Classify error type for targeted fixes."""
        error_lower = error.lower()
        
        if 'memoryerror' in error_lower:
            return 'memory_error'
        elif 'keyerror' in error_lower:
            return 'key_error'
        elif 'typeerror' in error_lower:
            return 'type_error'
        elif 'valueerror' in error_lower:
            return 'value_error'
        elif 'filenotfounderror' in error_lower:
            return 'file_not_found'
        else:
            return 'unknown'
    
    def _generate_patch(self, code: str, error_type: str, error_msg: str) -> str:
        """Generate code patch based on error type."""
        if error_type == 'memory_error':
            # Try chunking
            if 'read_csv' in code:
                return code.replace('read_csv(', 'read_csv(chunksize=10000, ')
        
        elif error_type == 'key_error':
            # Extract missing key from error
            match = re.search(r"KeyError: ['\"]([^'\"]+)['\"]", error_msg)
            if match:
                missing_key = match.group(1)
                # Add existence check
                return f"# Added check for missing key\nif '{missing_key}' in df.columns:\n    {code}\nelse:\n    print(f'Column {missing_key} not found')"
        
        # Add more error-specific patches
        return code
    
    def _apply_patches(self, code: str, patch: Any) -> str:
        """Apply line-by-line patches to code."""
        lines = code.splitlines()
        
        for edit in patch.edits:
            if 0 <= edit.line_number - 1 < len(lines):
                lines[edit.line_number - 1] = edit.new_line
        
        return '\n'.join(lines)