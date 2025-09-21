"""gRPC-based A2A client for agent-to-agent communication."""

import grpc
import logging
import os
from typing import Any, Dict, Optional
from concurrent import futures

logger = logging.getLogger(__name__)


class A2AGrpcClient:
    """gRPC client for A2A communication."""
    
    def __init__(self):
        self.channels = {}
        self.stubs = {}
        
    def get_channel(self, agent_id: str, port: int) -> grpc.Channel:
        """Get or create a gRPC channel to an agent."""
        key = f"{agent_id}:{port}"
        if key not in self.channels:
            # Create insecure channel for now
            # In production, this should use TLS
            self.channels[key] = grpc.insecure_channel(f"localhost:{port}")
        return self.channels[key]
        
    async def call_grpc_method(
        self, 
        agent_id: str, 
        port: int,
        service_name: str,
        method_name: str,
        request_message: Any
    ) -> Any:
        """Call a gRPC method on another agent.
        
        Args:
            agent_id: Target agent ID
            port: gRPC port of the target agent
            service_name: gRPC service name
            method_name: Method to call
            request_message: Protobuf request message
            
        Returns:
            Response message
        """
        channel = self.get_channel(agent_id, port)
        
        # For Python agent, we'd need to dynamically import the stub
        # This is a simplified version
        try:
            # Import the appropriate stub based on service
            if service_name == "PythonAgent":
                # Dynamically import if available
                try:
                    from src.a2a import python_agent_pb2_grpc
                    stub = python_agent_pb2_grpc.PythonAgentStub(channel)
                    
                    # Call the method
                    method = getattr(stub, method_name)
                    response = method(request_message)
                    return response
                except ImportError:
                    logger.error("Python agent gRPC stubs not available")
                    raise
            else:
                raise ValueError(f"Unknown service: {service_name}")
                
        except Exception as e:
            logger.error(f"gRPC call failed: {e}")
            raise
            
    def close(self):
        """Close all channels."""
        for channel in self.channels.values():
            channel.close()
        self.channels.clear()


class A2AGrpcServer:
    """gRPC server for receiving A2A calls."""
    
    def __init__(self, port: int = 50051):
        self.port = port
        self.server = None
        
    def start(self, service_impl: Any, add_service_fn: Any):
        """Start the gRPC server.
        
        Args:
            service_impl: The service implementation
            add_service_fn: Function to add service to server (e.g., add_PythonAgentServicer_to_server)
        """
        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        add_service_fn(service_impl, self.server)
        
        # Add insecure port
        self.server.add_insecure_port(f"[::]:{self.port}")
        
        self.server.start()
        logger.info(f"gRPC server started on port {self.port}")
        
    def stop(self):
        """Stop the gRPC server."""
        if self.server:
            self.server.stop(0)
            logger.info("gRPC server stopped")