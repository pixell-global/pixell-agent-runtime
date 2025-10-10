#!/usr/bin/env python3
"""Test A2A connectivity via NLB and router."""

import sys
sys.path.insert(0, 'src')

import grpc
from pixell_runtime.proto import agent_pb2, agent_pb2_grpc

# Configuration
nlb_endpoint = "pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com:50051"
deployment_id = "9301911b-b3c9-4017-9e69-c9578f9ee6a8"

print(f"Testing A2A Connectivity")
print(f"========================")
print(f"NLB Endpoint: {nlb_endpoint}")
print(f"Deployment ID: {deployment_id}")
print()

# Create channel
print("Creating gRPC channel...")
channel = grpc.insecure_channel(nlb_endpoint)
stub = agent_pb2_grpc.AgentServiceStub(channel)

# Add x-deployment-id metadata
metadata = (("x-deployment-id", deployment_id),)

print(f"Testing with metadata: x-deployment-id={deployment_id}")
print()

# Test 1: Health Check
try:
    print("Test 1: Health Check")
    print("-" * 40)
    response = stub.Health(agent_pb2.Empty(), metadata=metadata, timeout=10.0)
    print(f"✓ SUCCESS!")
    print(f"  OK: {response.ok}")
    print(f"  Message: {response.message}")
    print(f"  Timestamp: {response.timestamp}")
    print()
except Exception as e:
    print(f"✗ FAILED: {type(e).__name__}: {e}")
    print()

# Test 2: Ping
try:
    print("Test 2: Ping")
    print("-" * 40)
    response = stub.Ping(agent_pb2.Empty(), metadata=metadata, timeout=10.0)
    print(f"✓ SUCCESS!")
    print(f"  Message: {response.message}")
    print(f"  Timestamp: {response.timestamp}")
    print()
except Exception as e:
    print(f"✗ FAILED: {type(e).__name__}: {e}")
    print()

# Test 3: DescribeCapabilities
try:
    print("Test 3: DescribeCapabilities")
    print("-" * 40)
    response = stub.DescribeCapabilities(agent_pb2.Empty(), metadata=metadata, timeout=10.0)
    print(f"✓ SUCCESS!")
    print(f"  Methods: {list(response.methods)}")
    print(f"  Metadata: {dict(response.metadata)}")
    print()
except Exception as e:
    print(f"✗ FAILED: {type(e).__name__}: {e}")
    print()

channel.close()
print("=" * 40)
print("Test Complete")
