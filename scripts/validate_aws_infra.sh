#!/bin/bash
# Validate AWS infrastructure before Envoy deployment

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "========================================="
echo "AWS Infrastructure Validation"
echo "========================================="
echo ""

ERRORS=0

# Check 1: NLB exists
echo -e "${YELLOW}Check 1: NLB exists${NC}"
NLB_DNS=$(aws elbv2 describe-load-balancers \
  --names pixell-runtime-nlb \
  --query 'LoadBalancers[0].DNSName' \
  --output text 2>/dev/null || echo "")

if [ -n "$NLB_DNS" ]; then
    echo -e "${GREEN}✓ NLB found: $NLB_DNS${NC}"
else
    echo -e "${RED}✗ NLB not found${NC}"
    ERRORS=$((ERRORS + 1))
fi
echo ""

# Check 2: Target Groups exist
echo -e "${YELLOW}Check 2: Target Groups exist${NC}"

REST_TG=$(aws elbv2 describe-target-groups \
  --names par-multi-agent-tg \
  --query 'TargetGroups[0].TargetGroupArn' \
  --output text 2>/dev/null || echo "")

if [ -n "$REST_TG" ]; then
    echo -e "${GREEN}✓ REST target group found${NC}"
else
    echo -e "${RED}✗ REST target group not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

A2A_TG=$(aws elbv2 describe-target-groups \
  --names pixell-runtime-a2a-tg \
  --query 'TargetGroups[0].TargetGroupArn' \
  --output text 2>/dev/null || echo "")

if [ -n "$A2A_TG" ]; then
    echo -e "${GREEN}✓ A2A target group found${NC}"
else
    echo -e "${RED}✗ A2A target group not found${NC}"
    ERRORS=$((ERRORS + 1))
fi
echo ""

# Check 3: NLB Listener on port 50051
echo -e "${YELLOW}Check 3: NLB Listener on port 50051${NC}"
LISTENER=$(aws elbv2 describe-listeners \
  --load-balancer-arn $(aws elbv2 describe-load-balancers --names pixell-runtime-nlb --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null) \
  --query 'Listeners[?Port==`50051`].ListenerArn' \
  --output text 2>/dev/null || echo "")

if [ -n "$LISTENER" ]; then
    echo -e "${GREEN}✓ Listener on port 50051 exists${NC}"
else
    echo -e "${YELLOW}⚠ Listener on port 50051 not found (will be created if needed)${NC}"
fi
echo ""

# Check 4: ECS Cluster and Service
echo -e "${YELLOW}Check 4: ECS Cluster and Service${NC}"
SERVICE_STATUS=$(aws ecs describe-services \
  --cluster pixell-runtime-cluster \
  --services pixell-runtime-multi-agent \
  --query 'services[0].{Desired:desiredCount,Running:runningCount,Status:status}' \
  --output json 2>/dev/null || echo "{}")

DESIRED=$(echo "$SERVICE_STATUS" | jq -r '.Desired // 0')
RUNNING=$(echo "$SERVICE_STATUS" | jq -r '.Running // 0')

if [ "$DESIRED" -eq "$RUNNING" ] && [ "$RUNNING" -gt 0 ]; then
    echo -e "${GREEN}✓ Service healthy: $RUNNING/$DESIRED tasks running${NC}"
else
    echo -e "${RED}✗ Service unhealthy: $RUNNING/$DESIRED tasks${NC}"
    ERRORS=$((ERRORS + 1))
fi
echo ""

# Check 5: IAM Roles
echo -e "${YELLOW}Check 5: IAM Roles${NC}"

EXEC_ROLE=$(aws iam get-role \
  --role-name pixell-runtime-execution-role \
  --query 'Role.RoleName' \
  --output text 2>/dev/null || echo "")

if [ -n "$EXEC_ROLE" ]; then
    echo -e "${GREEN}✓ Execution role exists${NC}"
else
    echo -e "${RED}✗ Execution role not found${NC}"
    ERRORS=$((ERRORS + 1))
fi

TASK_ROLE=$(aws iam get-role \
  --role-name pixell-runtime-task-role \
  --query 'Role.RoleName' \
  --output text 2>/dev/null || echo "")

if [ -n "$TASK_ROLE" ]; then
    echo -e "${GREEN}✓ Task role exists${NC}"
else
    echo -e "${RED}✗ Task role not found${NC}"
    ERRORS=$((ERRORS + 1))
fi
echo ""

# Check 6: Service Discovery
echo -e "${YELLOW}Check 6: Service Discovery${NC}"
NAMESPACE=$(aws servicediscovery list-namespaces \
  --query 'Namespaces[?Name==`pixell-runtime.local`].Id' \
  --output text 2>/dev/null || echo "")

if [ -n "$NAMESPACE" ]; then
    echo -e "${GREEN}✓ Service Discovery namespace exists${NC}"

    SERVICE_COUNT=$(aws servicediscovery list-services \
      --filters Name=NAMESPACE_ID,Values=$NAMESPACE \
      --query 'Services | length(@)' \
      --output text 2>/dev/null || echo "0")

    echo "  Found $SERVICE_COUNT services registered"
else
    echo -e "${YELLOW}⚠ Service Discovery namespace not found (optional)${NC}"
fi
echo ""

# Check 7: Current Task Definition
echo -e "${YELLOW}Check 7: Current Task Definition${NC}"
CURRENT_TASK_DEF=$(aws ecs describe-services \
  --cluster pixell-runtime-cluster \
  --services pixell-runtime-multi-agent \
  --query 'services[0].taskDefinition' \
  --output text 2>/dev/null || echo "")

if [ -n "$CURRENT_TASK_DEF" ]; then
    echo "Current task definition: $CURRENT_TASK_DEF"

    CONTAINER_COUNT=$(aws ecs describe-task-definition \
      --task-definition $CURRENT_TASK_DEF \
      --query 'taskDefinition.containerDefinitions | length(@)' \
      --output text 2>/dev/null || echo "0")

    echo "Container count: $CONTAINER_COUNT"

    if [ "$CONTAINER_COUNT" -eq 1 ]; then
        echo -e "${YELLOW}⚠ Only 1 container (no Envoy sidecar yet)${NC}"
    elif [ "$CONTAINER_COUNT" -eq 2 ]; then
        echo -e "${GREEN}✓ 2 containers (Envoy sidecar already deployed)${NC}"
    fi
fi
echo ""

# Check 8: Security Groups (basic check)
echo -e "${YELLOW}Check 8: Task Network Configuration${NC}"
TASK_ARN=$(aws ecs list-tasks \
  --cluster pixell-runtime-cluster \
  --service-name pixell-runtime-multi-agent \
  --query 'taskArns[0]' \
  --output text 2>/dev/null || echo "")

if [ -n "$TASK_ARN" ]; then
    TASK_IP=$(aws ecs describe-tasks \
      --cluster pixell-runtime-cluster \
      --tasks $TASK_ARN \
      --query 'tasks[0].containers[0].networkInterfaces[0].privateIpv4Address' \
      --output text 2>/dev/null || echo "")

    if [ -n "$TASK_IP" ]; then
        echo -e "${GREEN}✓ Task running at: $TASK_IP${NC}"
    fi
fi
echo ""

# Summary
echo "========================================="
echo "Validation Summary"
echo "========================================="

if [ $ERRORS -eq 0 ]; then
    echo -e "${GREEN}✓ All checks passed!${NC}"
    echo ""
    echo "Ready to proceed with Envoy deployment."
    echo "Run: ./scripts/deploy_envoy.sh"
    exit 0
else
    echo -e "${RED}✗ $ERRORS checks failed${NC}"
    echo ""
    echo "Please fix the issues above before proceeding."
    exit 1
fi