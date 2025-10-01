#!/usr/bin/env bash
set -euo pipefail

# Pixell Agent Runtime - Deployment Script
#
# This script builds and optionally pushes a Docker image to ECR, and can update
# an ECS service by registering a new task definition with the updated image and
# environment variables.
#
# Required tools: docker, aws, jq, git
#
# Required env vars for push/update:
#   AWS_REGION, AWS_ACCOUNT_ID, ECR_REPO
# Optional env vars:
#   IMAGE_TAG (default: git sha + timestamp)
#   BASE_PATH (e.g. /agents/<agent_id>)
#   REST_PORT (default 8080), A2A_PORT (default 50051), UI_PORT (default 3000)
#   ECS_CLUSTER, ECS_SERVICE (for update)
#
# Usage:
#   scripts/deploy_par.sh                 # build, push, update service (if ECS vars set)
#   scripts/deploy_par.sh --build-only    # only build image locally
#   scripts/deploy_par.sh --push-only     # build + push only
#   scripts/deploy_par.sh --update-only   # only register task def + update service (needs IMAGE_TAG)

PROJECT_ROOT="/Users/syum/dev/pixell-agent-runtime"
cd "$PROJECT_ROOT"

# Auto-load .env from project root if present
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  # Safely source .env without failing on unset vars (to allow literal templates)
  # shellcheck disable=SC1091
  set +u
  set -a
  source "$PROJECT_ROOT/.env"
  set +a
  set -u
fi

ACTION="all"
if [[ "${1:-}" == "--build-only" ]]; then ACTION="build"; fi
if [[ "${1:-}" == "--push-only" ]]; then ACTION="push"; fi
if [[ "${1:-}" == "--update-only" ]]; then ACTION="update"; fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Error: $1 is not installed" >&2; exit 1; }
}

require_cmd docker
require_cmd git

git_sha=$(git rev-parse --short HEAD)
ts=$(date +%Y%m%d-%H%M%S)
IMAGE_TAG_DEFAULT="${git_sha}-${ts}"
IMAGE_TAG="${IMAGE_TAG:-$IMAGE_TAG_DEFAULT}"

REST_PORT_ENV="${REST_PORT:-8080}"
A2A_PORT_ENV="${A2A_PORT:-50051}"
UI_PORT_ENV="${UI_PORT:-3000}"
BASE_PATH_ENV="${BASE_PATH:-/}"
SD_NAMESPACE="${SERVICE_DISCOVERY_NAMESPACE:-pixell-runtime.local}"
SD_SERVICE="${SERVICE_DISCOVERY_SERVICE:-agents}"

build_image() {
  if [[ -z "${AWS_ACCOUNT_ID:-}" || -z "${AWS_REGION:-}" || -z "${ECR_REPO:-}" ]]; then
    # Build locally with a local tag if ECR variables are missing
    LOCAL_TAG="par-local:${IMAGE_TAG}"
    echo "Building local image ${LOCAL_TAG}..."
    docker build --platform linux/amd64 -t "${LOCAL_TAG}" "$PROJECT_ROOT"
    echo "Built ${LOCAL_TAG}"
    return 0
  fi

  ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"
  FULL_TAG="${ECR_URI}:${IMAGE_TAG}"
  echo "Building image ${FULL_TAG}..."
  docker build --platform linux/amd64 -t "${FULL_TAG}" "$PROJECT_ROOT"
  echo "Built ${FULL_TAG}"
}

push_image() {
  require_cmd aws
  if [[ -z "${AWS_ACCOUNT_ID:-}" || -z "${AWS_REGION:-}" || -z "${ECR_REPO:-}" ]]; then
    echo "Error: AWS_ACCOUNT_ID, AWS_REGION, and ECR_REPO must be set to push" >&2
    exit 1
  fi
  ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"
  FULL_TAG="${ECR_URI}:${IMAGE_TAG}"
  echo "Logging in to ECR ${ECR_URI}..."
  aws ecr get-login-password --region "${AWS_REGION}" | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
  echo "Pushing ${FULL_TAG}..."
  docker push "${FULL_TAG}"
  echo "Pushed ${FULL_TAG}"
}

update_service() {
  require_cmd aws
  require_cmd jq
  if [[ -z "${AWS_ACCOUNT_ID:-}" || -z "${AWS_REGION:-}" || -z "${ECR_REPO:-}" ]]; then
    echo "Error: AWS_ACCOUNT_ID, AWS_REGION, and ECR_REPO must be set to update ECS" >&2
    exit 1
  fi
  if [[ -z "${ECS_CLUSTER:-}" || -z "${ECS_SERVICE:-}" ]]; then
    echo "ECS_CLUSTER/ECS_SERVICE not set; skipping service update" >&2
    return 0
  fi
  ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPO}"
  FULL_TAG="${ECR_URI}:${IMAGE_TAG}"

  TD_JSON="${PROJECT_ROOT}/deploy/ecs-task-definition-envoy.json"
  if [[ ! -f "$TD_JSON" ]]; then
    echo "Error: Task definition JSON not found at $TD_JSON" >&2
    exit 1
  fi

  echo "Registering new task definition with image ${FULL_TAG} and env overrides..."
  NEW_TD=$(jq \
    --arg IMG "$FULL_TAG" \
    --arg BP "$BASE_PATH_ENV" \
    --arg RP "$REST_PORT_ENV" \
    --arg AP "$A2A_PORT_ENV" \
    --arg UP "$UI_PORT_ENV" \
    --arg SDN "$SD_NAMESPACE" \
    --arg SDS "$SD_SERVICE" \
    '
    .containerDefinitions |= (map(
      if .name == "par" then
        .image = $IMG
        | .environment = ((.environment // [])
            | map(select(.name != "BASE_PATH" and .name != "REST_PORT" and .name != "A2A_PORT" and .name != "UI_PORT" and .name != "SERVICE_DISCOVERY_NAMESPACE" and .name != "SERVICE_DISCOVERY_SERVICE"))
            + [{name:"BASE_PATH", value:$BP}, {name:"REST_PORT", value:$RP}, {name:"A2A_PORT", value:$AP}, {name:"UI_PORT", value:$UP}, {name:"SERVICE_DISCOVERY_NAMESPACE", value:$SDN}, {name:"SERVICE_DISCOVERY_SERVICE", value:$SDS}]
          )
      else
        .
      end
    ))
    ' "$TD_JSON")

  TD_ARN=$(aws ecs register-task-definition --region "$AWS_REGION" --cli-input-json "${NEW_TD}" --query 'taskDefinition.taskDefinitionArn' --output text)
  echo "Registered task definition: ${TD_ARN}"

  echo "Updating service ${ECS_SERVICE} in cluster ${ECS_CLUSTER} to ${TD_ARN}..."

  # Get container name from task definition
  CONTAINER_NAME=$(echo "$NEW_TD" | jq -r '.containerDefinitions[0].name')

  # Check if service already has service registries configured
  EXISTING_REGISTRIES=$(aws ecs describe-services --region "$AWS_REGION" --cluster "$ECS_CLUSTER" --services "$ECS_SERVICE" --query 'services[0].serviceRegistries' --output json 2>/dev/null || echo "[]")

  if [[ "$EXISTING_REGISTRIES" != "[]" ]]; then
    echo "Service already has service registries configured, updating task definition only"
    aws ecs update-service --region "$AWS_REGION" \
      --cluster "$ECS_CLUSTER" \
      --service "$ECS_SERVICE" \
      --task-definition "$TD_ARN" \
      >/dev/null
  else
    # Check if service registry is configured in Cloud Map
    SD_SERVICE_ARN=$(aws servicediscovery list-services --region "$AWS_REGION" --query "Services[?Name=='$SD_SERVICE'].Arn" --output text 2>/dev/null || echo "")

    if [[ -n "$SD_SERVICE_ARN" ]]; then
      echo "Configuring service with service discovery: $SD_SERVICE_ARN (container: $CONTAINER_NAME)"
      aws ecs update-service --region "$AWS_REGION" \
        --cluster "$ECS_CLUSTER" \
        --service "$ECS_SERVICE" \
        --task-definition "$TD_ARN" \
        --service-registries "registryArn=$SD_SERVICE_ARN,containerName=$CONTAINER_NAME,containerPort=50051" \
        >/dev/null
    else
      echo "Warning: Service discovery not found, updating without service registry"
      aws ecs update-service --region "$AWS_REGION" \
        --cluster "$ECS_CLUSTER" \
        --service "$ECS_SERVICE" \
        --task-definition "$TD_ARN" \
        >/dev/null
    fi
  fi

  echo "Service update initiated."
}

case "$ACTION" in
  build)
    build_image
    ;;
  push)
    build_image
    push_image
    ;;
  update)
    update_service
    ;;
  all)
    build_image
    push_image
    update_service
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    exit 1
    ;;
esac

echo "Done."


