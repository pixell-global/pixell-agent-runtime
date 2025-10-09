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
#   AGENT_APP_ID - Agent identifier (required)
#   PACKAGE_URL - s3:// or https:// URL to APKG (required)
#
# Optional env vars:
#   IMAGE_TAG (default: git sha + timestamp)
#   DEPLOYMENT_ID - Deployment identifier
#   PACKAGE_SHA256 - SHA256 checksum for package validation
#   S3_BUCKET (default: pixell-agent-packages)
#   BASE_PATH (default: /agents/${AGENT_APP_ID})
#   REST_PORT (default 8080), A2A_PORT (default 50051), UI_PORT (default 3000)
#   MULTIPLEXED (default: true)
#   MAX_PACKAGE_SIZE_MB (default: 100)
#   BOOT_BUDGET_MS (default: 5000)
#   BOOT_HARD_LIMIT_MULTIPLIER (default: 2.0)
#   GRACEFUL_SHUTDOWN_TIMEOUT_SEC (default: 30)
#   ECS_CLUSTER, ECS_SERVICE (for update)
#
# Usage:
#   scripts/deploy_par.sh                 # build and push generic PAR runtime image
#   scripts/deploy_par.sh --build-only    # only build image locally
#
# NOTE: This script builds the GENERIC PAR runtime image that can run ANY agent.
# It does NOT deploy specific agents - that is PAC's responsibility.
# PAC creates per-agent ECS services with AGENT_APP_ID environment variables.

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

ACTION="push"  # Default: build + push
if [[ "${1:-}" == "--build-only" ]]; then ACTION="build"; fi

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "Error: $1 is not installed" >&2; exit 1; }
}

require_cmd docker
require_cmd git

git_sha=$(git rev-parse --short HEAD)
ts=$(date +%Y%m%d-%H%M%S)
IMAGE_TAG_DEFAULT="${git_sha}-${ts}"
IMAGE_TAG="${IMAGE_TAG:-$IMAGE_TAG_DEFAULT}"

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

# NOTE: Service/agent deployment is PAC's responsibility, not PAR's.
# PAC creates per-agent ECS services with AGENT_APP_ID and PACKAGE_URL.
# This script only builds the generic runtime image.

case "$ACTION" in
  build)
    build_image
    ;;
  push)
    build_image
    push_image
    ;;
  *)
    echo "Unknown action: $ACTION" >&2
    exit 1
    ;;
esac

echo "Done."


