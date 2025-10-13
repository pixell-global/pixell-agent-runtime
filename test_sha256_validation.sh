#!/bin/bash
# Test script for SHA256 validation functionality

ALB_ENDPOINT="pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com"
PACKAGE_URL="https://pixell-agent-packages.s3.us-east-2.amazonaws.com/packages/8c829668-8352-4dad-b2eb-8adf73c8cf45/Test%20Agent%20App/v1.0.0/package.apkg"
CORRECT_SHA256="c1d7959cfa91161889b5ed6a9a781a99dd3be0a8258c81de9dce6b690173da5c"
WRONG_SHA256="0000000000000000000000000000000000000000000000000000000000000000"

echo "================================"
echo "Testing SHA256 Validation"
echo "================================"
echo ""
echo "Correct SHA256: ${CORRECT_SHA256}"
echo ""

# Test 1: Deploy with correct SHA256 (should succeed)
echo "Test 1: Deploy with correct SHA256 (should succeed)"
echo "-----------------------------------------------------"
DEPLOYMENT_ID="sha256-test-001"
curl -X POST "http://${ALB_ENDPOINT}/deploy" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: ${DEPLOYMENT_ID}" \
  -d "{
    \"deploymentId\": \"${DEPLOYMENT_ID}\",
    \"agentAppId\": \"test-agent-sha\",
    \"orgId\": \"test-org\",
    \"version\": \"2.0.0\",
    \"packageUrl\": \"${PACKAGE_URL}\",
    \"packageSha256\": \"${CORRECT_SHA256}\"
  }" | jq '.'

echo ""
echo "Waiting 5 seconds for deployment to complete..."
sleep 5
echo ""

# Test 2: Same version with correct SHA256 (should validate cache)
echo "Test 2: Same version with correct SHA256 (should validate cache)"
echo "-----------------------------------------------------------------"
DEPLOYMENT_ID="sha256-test-002"
curl -X POST "http://${ALB_ENDPOINT}/deploy" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: ${DEPLOYMENT_ID}" \
  -d "{
    \"deploymentId\": \"${DEPLOYMENT_ID}\",
    \"agentAppId\": \"test-agent-sha\",
    \"orgId\": \"test-org\",
    \"version\": \"2.0.0\",
    \"packageUrl\": \"${PACKAGE_URL}\",
    \"packageSha256\": \"${CORRECT_SHA256}\"
  }" | jq '.'

echo ""
echo "Waiting 5 seconds for deployment to complete..."
sleep 5
echo ""

# Test 3: Same version with WRONG SHA256 (should detect mismatch and re-download, then fail)
echo "Test 3: Same version with WRONG SHA256 (should detect mismatch)"
echo "----------------------------------------------------------------"
DEPLOYMENT_ID="sha256-test-003"
curl -X POST "http://${ALB_ENDPOINT}/deploy" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: ${DEPLOYMENT_ID}" \
  -d "{
    \"deploymentId\": \"${DEPLOYMENT_ID}\",
    \"agentAppId\": \"test-agent-sha\",
    \"orgId\": \"test-org\",
    \"version\": \"2.0.0\",
    \"packageUrl\": \"${PACKAGE_URL}\",
    \"packageSha256\": \"${WRONG_SHA256}\"
  }" | jq '.'

echo ""
echo "================================"
echo "Testing Complete!"
echo "================================"
echo ""
echo "Check CloudWatch logs for these deploymentIds to see SHA256 validation:"
echo "  - sha256-test-001 (correct SHA256 - should download and verify)"
echo "  - sha256-test-002 (correct SHA256 - should validate cache)"
echo "  - sha256-test-003 (wrong SHA256 - should detect mismatch and fail)"
