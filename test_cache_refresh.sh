#!/bin/bash
# Test script for cache refresh functionality

ALB_ENDPOINT="pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com"
DEPLOYMENT_BASE_ID="cache-test-v2"
PACKAGE_URL="https://pixell-agent-packages.s3.us-east-2.amazonaws.com/packages/8c829668-8352-4dad-b2eb-8adf73c8cf45/Test%20Agent%20App/v1.0.0/package.apkg"

echo "================================"
echo "Testing forceRefresh Functionality"
echo "================================"
echo ""

# Test 1: Initial deployment (will download and cache)
echo "Test 1: Initial deployment (version 1.0.0)"
echo "-------------------------------------------"
DEPLOYMENT_ID="${DEPLOYMENT_BASE_ID}-001"
curl -X POST "http://${ALB_ENDPOINT}/deploy" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: ${DEPLOYMENT_ID}" \
  -d "{
    \"deploymentId\": \"${DEPLOYMENT_ID}\",
    \"agentAppId\": \"test-agent\",
    \"orgId\": \"test-org\",
    \"version\": \"1.0.0\",
    \"packageUrl\": \"${PACKAGE_URL}\"
  }" | jq '.'

echo ""
echo "Waiting 5 seconds for deployment to complete..."
sleep 5
echo ""

# Test 2: Same version WITHOUT forceRefresh (should use cache)
echo "Test 2: Same version WITHOUT forceRefresh (should use cache)"
echo "--------------------------------------------------------------"
DEPLOYMENT_ID="${DEPLOYMENT_BASE_ID}-002"
curl -X POST "http://${ALB_ENDPOINT}/deploy" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: ${DEPLOYMENT_ID}" \
  -d "{
    \"deploymentId\": \"${DEPLOYMENT_ID}\",
    \"agentAppId\": \"test-agent\",
    \"orgId\": \"test-org\",
    \"version\": \"1.0.0\",
    \"packageUrl\": \"${PACKAGE_URL}\"
  }" | jq '.'

echo ""
echo "Waiting 5 seconds for deployment to complete..."
sleep 5
echo ""

# Test 3: Same version WITH forceRefresh (should re-download)
echo "Test 3: Same version WITH forceRefresh (should re-download)"
echo "------------------------------------------------------------"
DEPLOYMENT_ID="${DEPLOYMENT_BASE_ID}-003"
curl -X POST "http://${ALB_ENDPOINT}/deploy" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: ${DEPLOYMENT_ID}" \
  -d "{
    \"deploymentId\": \"${DEPLOYMENT_ID}\",
    \"agentAppId\": \"test-agent\",
    \"orgId\": \"test-org\",
    \"version\": \"1.0.0\",
    \"packageUrl\": \"${PACKAGE_URL}\",
    \"forceRefresh\": true
  }" | jq '.'

echo ""
echo "================================"
echo "Testing Complete!"
echo "================================"
echo ""
echo "Check CloudWatch logs for these deploymentIds to see caching behavior:"
echo "  - ${DEPLOYMENT_BASE_ID}-001 (initial - should download)"
echo "  - ${DEPLOYMENT_BASE_ID}-002 (cache hit - should use cache)"
echo "  - ${DEPLOYMENT_BASE_ID}-003 (force refresh - should re-download)"
