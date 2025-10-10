#!/bin/bash
# Deploy Envoy-enabled PAR to ECS

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "========================================="
echo "Envoy A2A Deployment Script"
echo "========================================="

# Configuration
CLUSTER="pixell-runtime-cluster"
SERVICE="pixell-runtime-multi-agent"
REGION="us-east-2"
ECR_REPO="636212886452.dkr.ecr.us-east-2.amazonaws.com"
TASK_FAMILY="pixell-runtime-multi-agent"

# Step 1: Build and Push Envoy Image
echo -e "\n${YELLOW}Step 1: Building Envoy Docker image${NC}"
docker build -f Dockerfile.envoy -t pixell-envoy:latest .

echo -e "\n${YELLOW}Step 2: Pushing to ECR${NC}"
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_REPO

# Create ECR repository if it doesn't exist
aws ecr describe-repositories --repository-names pixell-envoy --region $REGION 2>/dev/null || \
  aws ecr create-repository --repository-name pixell-envoy --region $REGION

docker tag pixell-envoy:latest $ECR_REPO/pixell-envoy:latest
docker push $ECR_REPO/pixell-envoy:latest

echo -e "${GREEN}✓ Envoy image pushed${NC}"

# Step 2: Update Target Group Health Check
echo -e "\n${YELLOW}Step 3: Updating A2A target group health check${NC}"
aws elbv2 modify-target-group \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39 \
  --health-check-protocol TCP \
  --health-check-interval-seconds 30 \
  --health-check-timeout-seconds 10 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3

echo -e "${GREEN}✓ Health check updated to TCP${NC}"

# Step 3: Register Task Definition
echo -e "\n${YELLOW}Step 4: Registering new task definition${NC}"

# Create task definition with Envoy
cat > /tmp/par-multi-agent-envoy.json <<'EOF'
{
  "family": "pixell-runtime-multi-agent",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "2048",
  "memory": "4096",
  "executionRoleArn": "arn:aws:iam::636212886452:role/pixell-runtime-execution-role",
  "taskRoleArn": "arn:aws:iam::636212886452:role/pixell-runtime-task-role",
  "containerDefinitions": [
    {
      "name": "envoy",
      "image": "636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-envoy:latest",
      "essential": true,
      "portMappings": [
        {
          "containerPort": 50051,
          "hostPort": 50051,
          "protocol": "tcp"
        },
        {
          "containerPort": 9901,
          "hostPort": 9901,
          "protocol": "tcp"
        }
      ],
      "healthCheck": {
        "command": [
          "CMD-SHELL",
          "curl -s http://localhost:9901/ready | grep -q LIVE || exit 1"
        ],
        "interval": 10,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 15
      },
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/pixell-runtime-multi-agent",
          "awslogs-create-group": "true",
          "awslogs-region": "us-east-2",
          "awslogs-stream-prefix": "envoy"
        }
      }
    },
    {
      "name": "par",
      "image": "636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-runtime-multi-agent:latest",
      "essential": true,
      "dependsOn": [
        {
          "containerName": "envoy",
          "condition": "HEALTHY"
        }
      ],
      "environment": [
        {
          "name": "RUNTIME_MODE",
          "value": "multi-agent"
        },
        {
          "name": "PORT",
          "value": "8080"
        },
        {
          "name": "A2A_PORT",
          "value": "50051"
        },
        {
          "name": "ADMIN_PORT",
          "value": "9090"
        },
        {
          "name": "MAX_AGENTS",
          "value": "20"
        },
        {
          "name": "SERVICE_DISCOVERY_NAMESPACE",
          "value": "pixell-runtime.local"
        },
        {
          "name": "SERVICE_DISCOVERY_SERVICE",
          "value": "agents"
        },
        {
          "name": "ENVOY_ADMIN_URL",
          "value": "http://localhost:9901"
        },
        {
          "name": "A2A_EXTERNAL_ENDPOINT",
          "value": "pixell-runtime-nlb-eb1b66efdcfd482c.elb.us-east-2.amazonaws.com:50051"
        }
      ],
      "portMappings": [
        {
          "containerPort": 8080,
          "hostPort": 8080,
          "protocol": "tcp"
        },
        {
          "containerPort": 9090,
          "hostPort": 9090,
          "protocol": "tcp"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/pixell-runtime-multi-agent",
          "awslogs-create-group": "true",
          "awslogs-region": "us-east-2",
          "awslogs-stream-prefix": "par"
        }
      },
      "healthCheck": {
        "command": [
          "CMD-SHELL",
          "curl -f http://localhost:8080/health || exit 1"
        ],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 60
      }
    }
  ]
}
EOF

TASK_DEF_ARN=$(aws ecs register-task-definition \
  --cli-input-json file:///tmp/par-multi-agent-envoy.json \
  --region $REGION \
  --query 'taskDefinition.taskDefinitionArn' \
  --output text)

echo -e "${GREEN}✓ Task definition registered: $TASK_DEF_ARN${NC}"

# Step 4: Update Service
echo -e "\n${YELLOW}Step 5: Updating ECS service${NC}"
echo "This will register the service with both target groups and deploy new tasks..."

read -p "Continue with deployment? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled"
    exit 1
fi

aws ecs update-service \
  --cluster $CLUSTER \
  --service $SERVICE \
  --task-definition $TASK_DEF_ARN \
  --load-balancers \
    targetGroupArn=arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/par-multi-agent-tg/c28c15d19accbca4,containerName=par,containerPort=8080 \
    targetGroupArn=arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39,containerName=envoy,containerPort=50051 \
  --force-new-deployment \
  --region $REGION

echo -e "${GREEN}✓ Service update initiated${NC}"

# Step 5: Wait for deployment
echo -e "\n${YELLOW}Step 6: Waiting for deployment to complete${NC}"
echo "This may take 5-10 minutes..."

aws ecs wait services-stable \
  --cluster $CLUSTER \
  --services $SERVICE \
  --region $REGION

echo -e "${GREEN}✓ Deployment completed${NC}"

# Step 6: Verify
echo -e "\n${YELLOW}Step 7: Verifying deployment${NC}"

TASK_ARN=$(aws ecs list-tasks \
  --cluster $CLUSTER \
  --service-name $SERVICE \
  --query 'taskArns[0]' \
  --output text \
  --region $REGION)

echo "Task ARN: $TASK_ARN"

TASK_IP=$(aws ecs describe-tasks \
  --cluster $CLUSTER \
  --tasks $TASK_ARN \
  --query 'tasks[0].containers[?name==`par`].networkInterfaces[0].privateIpv4Address' \
  --output text \
  --region $REGION)

echo "Task IP: $TASK_IP"

# Check target health
echo -e "\nChecking NLB target health..."
TARGET_HEALTH=$(aws elbv2 describe-target-health \
  --target-group-arn arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/pixell-runtime-a2a-tg/5718af8130521a39 \
  --query 'TargetHealthDescriptions[0].TargetHealth.State' \
  --output text \
  --region $REGION)

echo "Target health: $TARGET_HEALTH"

if [ "$TARGET_HEALTH" == "healthy" ]; then
    echo -e "${GREEN}✓ All checks passed! Envoy deployment successful${NC}"
else
    echo -e "${YELLOW}⚠ Target not healthy yet. May take a few more minutes.${NC}"
fi

echo ""
echo "========================================="
echo "Deployment Complete"
echo "========================================="
echo "Next steps:"
echo "1. Run: ./scripts/test_a2a_connectivity.sh"
echo "2. Monitor logs: aws logs tail /ecs/pixell-runtime-multi-agent --follow"
echo "3. Check Envoy admin: curl http://$TASK_IP:9901/stats"
echo "========================================="