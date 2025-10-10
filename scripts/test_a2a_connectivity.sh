#!/bin/bash
# Test A2A connectivity through NLB

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
NLB_ENDPOINT="${A2A_EXTERNAL_ENDPOINT:-pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com:50051}"
DEPLOYMENT_ID="${1:-80cef39f-3daf-47bf-93f9-c33f08e51292}"

echo "========================================="
echo "A2A Connectivity Test Suite"
echo "========================================="
echo "NLB Endpoint: $NLB_ENDPOINT"
echo "Deployment ID: $DEPLOYMENT_ID"
echo ""

# Test 1: Verify deployment exists
echo -e "${YELLOW}Test 1: Verify deployment exists${NC}"
HEALTH_URL="http://pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com/deployments/$DEPLOYMENT_ID/health"
echo "Checking: $HEALTH_URL"

HEALTH_RESPONSE=$(curl -s "$HEALTH_URL")
DEPLOYMENT_STATUS=$(echo "$HEALTH_RESPONSE" | jq -r '.status // "unknown"')

if [ "$DEPLOYMENT_STATUS" == "healthy" ]; then
    echo -e "${GREEN}✓ Deployment is healthy${NC}"
    echo "$HEALTH_RESPONSE" | jq .
else
    echo -e "${RED}✗ Deployment is not healthy: $DEPLOYMENT_STATUS${NC}"
    echo "$HEALTH_RESPONSE" | jq .
    exit 1
fi
echo ""

# Test 2: Check NLB target health
echo -e "${YELLOW}Test 2: Check NLB target health${NC}"
TARGET_HEALTH=$(aws elbv2 describe-target-health \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39 \
  --query 'TargetHealthDescriptions[0].TargetHealth.State' \
  --output text 2>/dev/null || echo "unknown")

if [ "$TARGET_HEALTH" == "healthy" ]; then
    echo -e "${GREEN}✓ NLB target is healthy${NC}"
else
    echo -e "${RED}✗ NLB target is not healthy: $TARGET_HEALTH${NC}"
    aws elbv2 describe-target-health \
      --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39 2>/dev/null || true
fi
echo ""

# Test 3: Check if grpcurl is available
echo -e "${YELLOW}Test 3: Testing gRPC connectivity${NC}"
if ! command -v grpcurl &> /dev/null; then
    echo -e "${RED}✗ grpcurl not found. Install with: brew install grpcurl${NC}"
    echo "Skipping gRPC tests..."
else
    echo "Using grpcurl to test Health endpoint..."

    # Test with x-deployment-id header
    if grpcurl -plaintext \
        -H "x-deployment-id: $DEPLOYMENT_ID" \
        -max-time 10 \
        "$NLB_ENDPOINT" \
        pixell.agent.AgentService/Health 2>&1; then
        echo -e "${GREEN}✓ gRPC Health call succeeded${NC}"
    else
        echo -e "${RED}✗ gRPC Health call failed${NC}"
    fi
fi
echo ""

# Test 4: Check Service Discovery
echo -e "${YELLOW}Test 4: Check Service Discovery${NC}"
AGENTS=$(aws servicediscovery discover-instances \
  --namespace-name pixell-runtime.local \
  --service-name agents \
  --query 'Instances[*].{InstanceId:InstanceId,IPv4:Attributes.AWS_INSTANCE_IPV4,Port:Attributes.AWS_INSTANCE_PORT}' \
  --output json 2>/dev/null || echo "[]")

AGENT_COUNT=$(echo "$AGENTS" | jq 'length')
echo "Discovered $AGENT_COUNT agent instances:"
echo "$AGENTS" | jq .
echo ""

# Test 5: Python client test
echo -e "${YELLOW}Test 5: Python A2A client test${NC}"
if [ -f "test_a2a_connection.py" ]; then
    echo "Running Python test..."
    export A2A_EXTERNAL_ENDPOINT="$NLB_ENDPOINT"

    if python test_a2a_connection.py 2>&1 | tail -10; then
        echo -e "${GREEN}✓ Python client test passed${NC}"
    else
        echo -e "${RED}✗ Python client test failed${NC}"
    fi
else
    echo "test_a2a_connection.py not found, skipping..."
fi
echo ""

# Summary
echo "========================================="
echo "Test Summary"
echo "========================================="
echo "1. Deployment Health: $DEPLOYMENT_STATUS"
echo "2. NLB Target Health: $TARGET_HEALTH"
echo "3. Service Discovery: $AGENT_COUNT agents"
echo "========================================="

if [ "$DEPLOYMENT_STATUS" == "healthy" ] && [ "$TARGET_HEALTH" == "healthy" ]; then
    echo -e "${GREEN}All critical tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed. Review output above.${NC}"
    exit 1
fi