# Pixell Runtime - AWS Fargate Deployment Guide

## Overview

This guide provides step-by-step instructions for deploying Pixell Runtime (PAR) on AWS Fargate with S3 as the package registry and ALB for HTTPS support. The deployment is designed to support external developers publishing their own agent packages.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Internet Gateway                             │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│              Application Load Balancer (ALB)                         │
│                    - HTTPS termination                               │
│                    - OIDC authentication                             │
│                    - WAF protection                                  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ECS Fargate Service                               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐   │
│  │  PAR Container  │  │  PAR Container  │  │  PAR Container  │   │
│  │   (Task 1)      │  │   (Task 2)      │  │   (Task 3)      │   │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          S3 Registry                                 │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐   │
│  │  Developer A    │  │  Developer B    │  │  Developer C    │   │
│  │  APKGs          │  │  APKGs          │  │  APKGs          │   │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

## Prerequisites

- AWS Account with appropriate permissions
- AWS CLI configured
- Docker installed locally
- Domain name for HTTPS (optional but recommended)
- SSL certificate in AWS Certificate Manager

## Step 1: Create S3 Registry Infrastructure

### 1.1 Create S3 Bucket for Package Registry

```bash
# Create the main registry bucket
aws s3api create-bucket \
  --bucket pixell-registry \
  --region us-east-1 \
  --acl private

# Enable versioning for package history
aws s3api put-bucket-versioning \
  --bucket pixell-registry \
  --versioning-configuration Status=Enabled

# Enable server-side encryption
aws s3api put-bucket-encryption \
  --bucket pixell-registry \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {
        "SSEAlgorithm": "AES256"
      }
    }]
  }'
```

### 1.2 Configure S3 Bucket Structure

```
pixell-registry/
├── packages/
│   ├── {developer-id}/
│   │   ├── {package-name}/
│   │   │   ├── {version}/
│   │   │   │   ├── package.apkg
│   │   │   │   ├── package.sha256
│   │   │   │   └── package.sig (optional)
│   │   │   └── latest → {version}
├── index/
│   ├── registry.json
│   └── developers.json
└── uploads/
    └── {temp-upload-area}/
```

### 1.3 Set Up Bucket Policies for Multi-Tenant Access

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowPARReadAccess",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::ACCOUNT:role/pixell-runtime-task-role"
      },
      "Action": [
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::pixell-registry/*",
        "arn:aws:s3:::pixell-registry"
      ]
    },
    {
      "Sid": "AllowDeveloperUpload",
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::ACCOUNT:role/pixell-developer-role"
      },
      "Action": [
        "s3:PutObject",
        "s3:PutObjectAcl"
      ],
      "Resource": "arn:aws:s3:::pixell-registry/uploads/*"
    }
  ]
}
```

### 1.4 Create Lambda for Package Processing

Create a Lambda function to validate and move uploaded packages:

```python
# lambda_package_processor.py
import json
import boto3
import hashlib
from datetime import datetime

s3 = boto3.client('s3')

def handler(event, context):
    """Process uploaded APKG files"""
    # 1. Validate package structure
    # 2. Verify SHA256 hash
    # 3. Check signatures if required
    # 4. Move to proper location
    # 5. Update registry index
    # 6. Trigger SNS notification for PAR instances
    pass
```

## Step 2: Build and Push Docker Image

### 2.1 Create Dockerfile

```dockerfile
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Create app user
RUN useradd -m -u 1000 pixell

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=pixell:pixell src/ ./src/

# Switch to non-root user
USER pixell

# Expose ports
EXPOSE 8000 9090

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8000/runtime/health || exit 1

# Run the application
CMD ["python", "-m", "pixell_runtime.main"]
```

### 2.2 Build and Push to ECR

```bash
# Create ECR repository
aws ecr create-repository \
  --repository-name pixell-runtime \
  --region us-east-1

# Get login token
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin \
  ACCOUNT.dkr.ecr.us-east-1.amazonaws.com

# Build image
docker build -t pixell-runtime:latest .

# Tag image
docker tag pixell-runtime:latest \
  ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/pixell-runtime:latest

# Push image
docker push ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/pixell-runtime:latest
```

## Step 3: Create ECS Infrastructure

### 3.1 Create VPC and Networking

```bash
# Create VPC with public and private subnets
aws ec2 create-vpc --cidr-block 10.0.0.0/16

# Create subnets (2 public, 2 private across 2 AZs)
# Public subnets for ALB
# Private subnets for Fargate tasks
```

### 3.2 Create ECS Cluster

```bash
aws ecs create-cluster \
  --cluster-name pixell-runtime-cluster \
  --capacity-providers FARGATE FARGATE_SPOT \
  --default-capacity-provider-strategy \
    capacityProvider=FARGATE,weight=1,base=1 \
    capacityProvider=FARGATE_SPOT,weight=4,base=0
```

### 3.3 Create Task Definition

```json
{
  "family": "pixell-runtime",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "taskRoleArn": "arn:aws:iam::ACCOUNT:role/pixell-runtime-task-role",
  "executionRoleArn": "arn:aws:iam::ACCOUNT:role/pixell-runtime-execution-role",
  "containerDefinitions": [
    {
      "name": "pixell-runtime",
      "image": "ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/pixell-runtime:latest",
      "portMappings": [
        {
          "containerPort": 8000,
          "protocol": "tcp"
        },
        {
          "containerPort": 9090,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "PACKAGES_URLS",
          "value": ""
        },
        {
          "name": "REGISTRY_URL",
          "value": "https://pixell-registry.s3.amazonaws.com/index/registry.json"
        },
        {
          "name": "REGISTRY_POLL_INTERVAL",
          "value": "60"
        },
        {
          "name": "AWS_REGION",
          "value": "us-east-1"
        },
        {
          "name": "LOG_LEVEL",
          "value": "INFO"
        },
        {
          "name": "METRICS_ENABLED",
          "value": "true"
        }
      ],
      "secrets": [
        {
          "name": "OIDC_ISSUER",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:ACCOUNT:secret:pixell/oidc-issuer"
        },
        {
          "name": "STRIPE_API_KEY",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:ACCOUNT:secret:pixell/stripe-key"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/pixell-runtime",
          "awslogs-region": "us-east-1",
          "awslogs-stream-prefix": "ecs"
        }
      },
      "healthCheck": {
        "command": ["CMD-SHELL", "curl -f http://localhost:8000/runtime/health || exit 1"],
        "interval": 30,
        "timeout": 5,
        "retries": 3,
        "startPeriod": 60
      }
    }
  ]
}
```

### 3.4 Create IAM Roles

#### Task Role (pixell-runtime-task-role)
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::pixell-registry/*",
        "arn:aws:s3:::pixell-registry"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "cloudwatch:PutMetricData"
      ],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "sns:Publish"
      ],
      "Resource": "arn:aws:sns:*:*:pixell-*"
    }
  ]
}
```

## Step 4: Configure Application Load Balancer

### 4.1 Create ALB

```bash
# Create ALB
aws elbv2 create-load-balancer \
  --name pixell-runtime-alb \
  --subnets subnet-xxx subnet-yyy \
  --security-groups sg-xxx \
  --scheme internet-facing \
  --type application \
  --ip-address-type ipv4
```

### 4.2 Configure HTTPS Listener

```bash
# Create target group
aws elbv2 create-target-group \
  --name pixell-runtime-tg \
  --protocol HTTP \
  --port 8000 \
  --vpc-id vpc-xxx \
  --target-type ip \
  --health-check-path /runtime/health

# Create HTTPS listener
aws elbv2 create-listener \
  --load-balancer-arn arn:aws:elasticloadbalancing:... \
  --protocol HTTPS \
  --port 443 \
  --certificates CertificateArn=arn:aws:acm:... \
  --default-actions Type=forward,TargetGroupArn=arn:aws:elasticloadbalancing:...
```

### 4.3 Configure WAF

```bash
# Create Web ACL for protection
aws wafv2 create-web-acl \
  --name pixell-runtime-waf \
  --scope REGIONAL \
  --default-action Allow={} \
  --rules file://waf-rules.json
```

## Step 5: Create ECS Service

```bash
aws ecs create-service \
  --cluster pixell-runtime-cluster \
  --service-name pixell-runtime \
  --task-definition pixell-runtime:1 \
  --desired-count 3 \
  --launch-type FARGATE \
  --network-configuration '{
    "awsvpcConfiguration": {
      "subnets": ["subnet-xxx", "subnet-yyy"],
      "securityGroups": ["sg-xxx"],
      "assignPublicIp": "DISABLED"
    }
  }' \
  --load-balancers '[{
    "targetGroupArn": "arn:aws:elasticloadbalancing:...",
    "containerName": "pixell-runtime",
    "containerPort": 8000
  }]' \
  --health-check-grace-period-seconds 60
```

## Step 6: Configure Auto Scaling

### 6.1 Service Auto Scaling

```bash
# Register scalable target
aws application-autoscaling register-scalable-target \
  --service-namespace ecs \
  --resource-id service/pixell-runtime-cluster/pixell-runtime \
  --scalable-dimension ecs:service:DesiredCount \
  --min-capacity 2 \
  --max-capacity 10

# Create scaling policy
aws application-autoscaling put-scaling-policy \
  --policy-name pixell-runtime-scaling \
  --service-namespace ecs \
  --resource-id service/pixell-runtime-cluster/pixell-runtime \
  --scalable-dimension ecs:service:DesiredCount \
  --policy-type TargetTrackingScaling \
  --target-tracking-scaling-policy-configuration '{
    "TargetValue": 75.0,
    "PredefinedMetricSpecification": {
      "PredefinedMetricType": "ECSServiceAverageCPUUtilization"
    },
    "ScaleInCooldown": 300,
    "ScaleOutCooldown": 60
  }'
```

## Step 7: Developer Access Setup

### 7.1 Create Developer IAM Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:PutObjectAcl",
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::pixell-registry/uploads/${aws:username}/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket"
      ],
      "Resource": "arn:aws:s3:::pixell-registry",
      "Condition": {
        "StringLike": {
          "s3:prefix": "uploads/${aws:username}/*"
        }
      }
    }
  ]
}
```

### 7.2 Create Upload Script for Developers

```bash
#!/bin/bash
# upload-apkg.sh - Developer upload script

PACKAGE_FILE=$1
DEVELOPER_ID=$2
S3_BUCKET="pixell-registry"

# Calculate SHA256
SHA256=$(sha256sum "$PACKAGE_FILE" | cut -d' ' -f1)

# Upload package
aws s3 cp "$PACKAGE_FILE" "s3://${S3_BUCKET}/uploads/${DEVELOPER_ID}/"

# Upload SHA256
echo "$SHA256" | aws s3 cp - "s3://${S3_BUCKET}/uploads/${DEVELOPER_ID}/${PACKAGE_FILE}.sha256"

echo "Package uploaded successfully!"
echo "SHA256: $SHA256"
```

## Step 8: Monitoring and Logging

### 8.1 CloudWatch Dashboards

Create a dashboard with:
- ECS service metrics (CPU, memory, task count)
- ALB metrics (request count, target health, response times)
- S3 metrics (request count, bucket size)
- Custom application metrics from Prometheus

### 8.2 Alarms

```bash
# High CPU alarm
aws cloudwatch put-metric-alarm \
  --alarm-name pixell-runtime-cpu-high \
  --alarm-description "Triggers when CPU exceeds 80%" \
  --metric-name CPUUtilization \
  --namespace AWS/ECS \
  --statistic Average \
  --period 300 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2
```

## Step 9: Security Best Practices

### 9.1 Network Security
- Use private subnets for Fargate tasks
- Implement Security Groups with least privilege
- Enable VPC Flow Logs

### 9.2 Application Security
- Enable OIDC authentication on ALB
- Implement API rate limiting
- Use AWS Secrets Manager for sensitive data
- Enable GuardDuty for threat detection

### 9.3 Data Security
- Encrypt S3 bucket with KMS
- Enable S3 access logging
- Implement bucket lifecycle policies
- Use signed URLs for package downloads

## Step 10: Operational Procedures

### 10.1 Deployment Process

```bash
# 1. Build and push new image
docker build -t pixell-runtime:v1.2.3 .
docker tag pixell-runtime:v1.2.3 ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/pixell-runtime:v1.2.3
docker push ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/pixell-runtime:v1.2.3

# 2. Update task definition
aws ecs register-task-definition --cli-input-json file://task-definition.json

# 3. Update service (rolling deployment)
aws ecs update-service \
  --cluster pixell-runtime-cluster \
  --service pixell-runtime \
  --task-definition pixell-runtime:new-revision
```

### 10.2 Backup and Recovery
- Enable S3 versioning for package history
- Create S3 lifecycle policies for archival
- Implement cross-region replication for disaster recovery

### 10.3 Cost Optimization
- Use Fargate Spot for non-critical workloads
- Implement S3 Intelligent-Tiering
- Set up Cost Anomaly Detection
- Use Reserved Capacity for predictable workloads

## Infrastructure as Code

### Terraform Example

```hcl
# main.tf
module "pixell_runtime" {
  source = "./modules/pixell-runtime"
  
  environment = "production"
  vpc_id      = var.vpc_id
  subnet_ids  = var.private_subnet_ids
  
  # Fargate configuration
  cpu    = 1024
  memory = 2048
  desired_count = 3
  
  # S3 configuration
  registry_bucket_name = "pixell-registry"
  
  # ALB configuration
  certificate_arn = var.ssl_certificate_arn
  domain_name     = "runtime.pixell.io"
}
```

## Conclusion

This deployment provides a scalable, secure, and cost-effective solution for running Pixell Runtime on AWS Fargate. The architecture supports multi-tenant usage with proper isolation and security controls while maintaining high availability and performance.