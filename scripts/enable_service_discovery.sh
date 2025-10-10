#!/usr/bin/env bash
set -euo pipefail

# Enable Service Discovery for existing ECS service
# This script recreates the service with service discovery enabled

AWS_REGION="${AWS_REGION:-us-east-2}"
ECS_CLUSTER="${ECS_CLUSTER:-pixell-runtime-cluster}"
ECS_SERVICE="${ECS_SERVICE:-pixell-runtime}"
SD_SERVICE_NAME="${SERVICE_DISCOVERY_SERVICE:-agents}"

echo "Fetching current service configuration..."

# Get current service details
SERVICE_JSON=$(aws ecs describe-services \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --services "$ECS_SERVICE" \
  --output json)

TASK_DEF=$(echo "$SERVICE_JSON" | jq -r '.services[0].taskDefinition')
DESIRED_COUNT=$(echo "$SERVICE_JSON" | jq -r '.services[0].desiredCount')
LOAD_BALANCERS=$(echo "$SERVICE_JSON" | jq -c '.services[0].loadBalancers')
NETWORK_CONFIG=$(echo "$SERVICE_JSON" | jq -c '.services[0].networkConfiguration')
LAUNCH_TYPE=$(echo "$SERVICE_JSON" | jq -r '.services[0].launchType')

echo "Current configuration:"
echo "  Task Definition: $TASK_DEF"
echo "  Desired Count: $DESIRED_COUNT"
echo "  Launch Type: $LAUNCH_TYPE"

# Get service discovery ARN
SD_SERVICE_ARN=$(aws servicediscovery list-services \
  --region "$AWS_REGION" \
  --query "Services[?Name=='$SD_SERVICE_NAME'].Arn" \
  --output text 2>/dev/null || echo "")

if [[ -z "$SD_SERVICE_ARN" ]]; then
  echo "Error: Service discovery service '$SD_SERVICE_NAME' not found"
  exit 1
fi

echo "  Service Discovery: $SD_SERVICE_ARN"

# Get container name from task definition
CONTAINER_NAME=$(aws ecs describe-task-definition \
  --region "$AWS_REGION" \
  --task-definition "$TASK_DEF" \
  --query 'taskDefinition.containerDefinitions[0].name' \
  --output text)

echo "  Container Name: $CONTAINER_NAME"

# Check if service already has service registries
EXISTING_REGISTRIES=$(echo "$SERVICE_JSON" | jq -r '.services[0].serviceRegistries | length')

if [[ "$EXISTING_REGISTRIES" -gt 0 ]]; then
  echo "Service already has service registries configured!"
  echo "$SERVICE_JSON" | jq '.services[0].serviceRegistries'
  exit 0
fi

echo ""
echo "⚠️  WARNING: This will recreate the service with service discovery enabled."
echo "    There will be a brief service interruption."
echo ""
read -p "Continue? (yes/no): " CONFIRM

if [[ "$CONFIRM" != "yes" ]]; then
  echo "Aborted."
  exit 0
fi

echo ""
echo "Step 1: Deleting existing service..."
aws ecs update-service \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --service "$ECS_SERVICE" \
  --desired-count 0 \
  >/dev/null

echo "Waiting for service to drain..."
sleep 10

aws ecs delete-service \
  --region "$AWS_REGION" \
  --cluster "$ECS_CLUSTER" \
  --service "$ECS_SERVICE" \
  >/dev/null

echo "Service deleted."

echo ""
echo "Step 2: Creating new service with service discovery..."

# Build create-service command
CREATE_CMD="aws ecs create-service \
  --region $AWS_REGION \
  --cluster $ECS_CLUSTER \
  --service-name $ECS_SERVICE \
  --task-definition $TASK_DEF \
  --desired-count $DESIRED_COUNT \
  --launch-type $LAUNCH_TYPE"

# Add load balancers if present
if [[ "$LOAD_BALANCERS" != "[]" && "$LOAD_BALANCERS" != "null" ]]; then
  CREATE_CMD="$CREATE_CMD --load-balancers '$LOAD_BALANCERS'"
fi

# Add network configuration
if [[ "$NETWORK_CONFIG" != "null" ]]; then
  CREATE_CMD="$CREATE_CMD --network-configuration '$NETWORK_CONFIG'"
fi

# Add service registry
CREATE_CMD="$CREATE_CMD --service-registries registryArn=$SD_SERVICE_ARN,containerName=$CONTAINER_NAME,containerPort=50051"

# Execute
eval "$CREATE_CMD" >/dev/null

echo "✅ Service recreated with service discovery enabled!"
echo ""
echo "Service: $ECS_SERVICE"
echo "Service Discovery: $SD_SERVICE_NAME.$SD_NAMESPACE"
echo "Container: $CONTAINER_NAME:50051"