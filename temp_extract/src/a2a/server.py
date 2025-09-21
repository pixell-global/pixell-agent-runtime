import asyncio
import grpc
from concurrent import futures
from typing import AsyncIterator, Dict, Any
import structlog
import msgpack
import time

from . import python_agent_pb2
from . import python_agent_pb2_grpc
from ..engine.execution_engine import ExecutionEngine
from ..sessions.manager import SessionManager

logger = structlog.get_logger()


class PythonAgentServicer(python_agent_pb2_grpc.PythonAgentServicer):
    """gRPC service implementation for Python Agent A2A protocol."""
    
    def __init__(self):
        self.engine = ExecutionEngine()
        self.session_manager = SessionManager()
        
    async def Execute(
        self, 
        request: python_agent_pb2.ExecuteRequest, 
        context: grpc.aio.ServicerContext
    ) -> python_agent_pb2.ExecuteResponse:
        """Execute code synchronously and return results."""
        start_time = time.monotonic()
        
        try:
            # Deserialize context
            exec_context = {}
            for key, value in request.context.items():
                exec_context[key] = msgpack.unpackb(value, raw=False)
            
            # Get or create session
            session = await self.session_manager.get_or_create(request.session_id)
            
            # Execute through U-P-E-E engine
            result = await self.engine.execute_full(
                code=request.code,
                context=exec_context,
                files=request.files,
                session=session,
                resource_tier=request.resource_tier,
                timeout=request.timeout_seconds
            )
            
            # Serialize results
            serialized_results = {}
            for key, value in result.results.items():
                serialized_results[key] = msgpack.packb(value, use_bin_type=True)
            
            execution_time_ms = int((time.monotonic() - start_time) * 1000)
            
            return python_agent_pb2.ExecuteResponse(
                success=result.success,
                stdout=result.stdout,
                stderr=result.stderr,
                results=serialized_results,
                error=result.error or "",
                metrics=python_agent_pb2.ExecutionMetrics(
                    execution_time_ms=execution_time_ms,
                    memory_used_bytes=result.memory_used,
                    cpu_percent=result.cpu_percent
                )
            )
            
        except Exception as e:
            logger.exception("Error executing code", session_id=request.session_id)
            return python_agent_pb2.ExecuteResponse(
                success=False,
                error=str(e),
                metrics=python_agent_pb2.ExecutionMetrics(
                    execution_time_ms=int((time.monotonic() - start_time) * 1000)
                )
            )
    
    async def StreamExecute(
        self,
        request: python_agent_pb2.ExecuteRequest,
        context: grpc.aio.ServicerContext
    ) -> AsyncIterator[python_agent_pb2.StreamChunk]:
        """Execute code with streaming output."""
        try:
            # Deserialize context
            exec_context = {}
            for key, value in request.context.items():
                exec_context[key] = msgpack.unpackb(value, raw=False)
            
            # Get or create session
            session = await self.session_manager.get_or_create(request.session_id)
            
            # Stream execution through engine
            async for chunk in self.engine.execute_stream(
                code=request.code,
                context=exec_context,
                files=request.files,
                session=session,
                resource_tier=request.resource_tier,
                timeout=request.timeout_seconds
            ):
                yield python_agent_pb2.StreamChunk(
                    type=chunk.type,
                    data=chunk.data,
                    timestamp=chunk.timestamp
                )
                
        except Exception as e:
            logger.exception("Error in streaming execution", session_id=request.session_id)
            yield python_agent_pb2.StreamChunk(
                type=python_agent_pb2.StreamChunk.ChunkType.ERROR,
                data=str(e).encode(),
                timestamp=int(time.time() * 1000)
            )
    
    async def GetSessionState(
        self,
        request: python_agent_pb2.SessionStateRequest,
        context: grpc.aio.ServicerContext
    ) -> python_agent_pb2.SessionStateResponse:
        """Get current session state."""
        try:
            session = await self.session_manager.get(request.session_id)
            if not session:
                context.abort(grpc.StatusCode.NOT_FOUND, f"Session {request.session_id} not found")
            
            # Serialize session variables
            serialized_vars = {}
            for key, value in session.variables.items():
                serialized_vars[key] = msgpack.packb(value, use_bin_type=True)
            
            return python_agent_pb2.SessionStateResponse(
                variables=serialized_vars,
                created_at=session.created_at,
                last_accessed=session.last_accessed
            )
            
        except Exception as e:
            logger.exception("Error getting session state", session_id=request.session_id)
            context.abort(grpc.StatusCode.INTERNAL, str(e))
    
    async def RetryWithPatch(
        self,
        request: python_agent_pb2.RetryRequest,
        context: grpc.aio.ServicerContext
    ) -> python_agent_pb2.ExecuteResponse:
        """Retry execution with code patches."""
        try:
            session = await self.session_manager.get(request.session_id)
            if not session:
                context.abort(grpc.StatusCode.NOT_FOUND, f"Session {request.session_id} not found")
            
            # Apply patches and re-execute
            result = await self.engine.retry_with_patch(
                session=session,
                patch=request.patch
            )
            
            # Serialize results
            serialized_results = {}
            for key, value in result.results.items():
                serialized_results[key] = msgpack.packb(value, use_bin_type=True)
            
            return python_agent_pb2.ExecuteResponse(
                success=result.success,
                stdout=result.stdout,
                stderr=result.stderr,
                results=serialized_results,
                error=result.error or ""
            )
            
        except Exception as e:
            logger.exception("Error retrying with patch", session_id=request.session_id)
            return python_agent_pb2.ExecuteResponse(
                success=False,
                error=str(e)
            )


async def serve(port: int = 50051, use_unix_socket: bool = True):
    """Start the gRPC server."""
    server = grpc.aio.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=[
            ('grpc.max_send_message_length', 1024 * 1024 * 100),  # 100MB
            ('grpc.max_receive_message_length', 1024 * 1024 * 100),
        ]
    )
    
    python_agent_pb2_grpc.add_PythonAgentServicer_to_server(
        PythonAgentServicer(), server
    )
    
    if use_unix_socket:
        # Unix domain socket for local communication
        address = f"unix:///tmp/pixell-python-agent-{port}.sock"
    else:
        # TCP socket for remote communication
        address = f"[::]:{port}"
    
    server.add_insecure_port(address)
    
    logger.info("Starting Python Agent gRPC server", address=address)
    await server.start()
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())