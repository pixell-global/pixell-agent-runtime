#!/bin/bash
# Pixell Runtime AWS Deployment Script
# Run this script with your own AWS credentials configured

set -e

# Configuration
REGION="us-east-2"
CLUSTER_NAME="pixell-runtime-cluster"
SERVICE_NAME="pixell-runtime"
REGISTRY_BUCKET="pixell-registry-${RANDOM}"
ECR_REPO="pixell-runtime"

echo "=== Pixell Runtime AWS Deployment ==="
echo "Region: $REGION"
echo ""

# Check AWS credentials
echo "Checking AWS credentials..."
aws sts get-caller-identity > /dev/null || {
    echo "ERROR: AWS credentials not configured"
    echo "Please run: aws configure"
    exit 1
}

echo "✓ AWS credentials configured"
echo ""

# Create deployment
echo "This script will create the following resources:"
echo "- S3 bucket for package registry"
echo "- ECR repository for Docker images"
echo "- ECS cluster and service"
echo "- Application Load Balancer"
echo ""
read -p "Continue? (y/N) " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled"
    exit 0
fi

# Create S3 bucket
echo "Creating S3 registry bucket..."
aws s3api create-bucket \
    --bucket "$REGISTRY_BUCKET" \
    --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION" || {
    echo "Failed to create S3 bucket"
    exit 1
}

echo "✓ S3 bucket created: $REGISTRY_BUCKET"

# Output next steps
echo ""
echo "=== Next Steps ==="
echo "1. Build and push Docker image:"
echo "   ./deploy/build-and-push.sh"
echo ""
echo "2. Deploy infrastructure:"
echo "   ./deploy/deploy-infrastructure.sh"
echo ""
echo "3. Configure DNS and SSL certificate"
echo ""
echo "S3 Registry Bucket: $REGISTRY_BUCKET"