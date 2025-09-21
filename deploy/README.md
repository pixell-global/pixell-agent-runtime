# Pixell Runtime Deployment Guide

## ⚠️ SECURITY WARNING

**NEVER share AWS credentials in plain text!** If you've accidentally exposed credentials:

1. **Immediately deactivate them**:
   ```bash
   aws iam delete-access-key --access-key-id YOUR_KEY_ID
   ```

2. **Create new credentials** following AWS security best practices

## Prerequisites

1. **AWS CLI** installed and configured:
   ```bash
   aws configure
   ```

2. **Terraform** installed (for infrastructure deployment)

3. **Docker** installed (for building images)

4. **Required AWS Permissions**:
   - ECS full access
   - EC2 (for VPC, subnets, security groups)
   - S3 (for registry bucket)
   - ECR (for Docker images)
   - IAM (for roles)
   - CloudWatch (for logs)

## Deployment Steps

### 1. Configure AWS Credentials Securely

```bash
# Option 1: Use AWS CLI configuration
aws configure

# Option 2: Use environment variables (temporary)
export AWS_ACCESS_KEY_ID="your-key-id"
export AWS_SECRET_ACCESS_KEY="your-secret-key"
export AWS_DEFAULT_REGION="us-east-2"

# Option 3: Use AWS IAM roles (recommended for EC2/automation)
```

### 2. Deploy Infrastructure with Terraform

```bash
cd deploy/terraform

# Initialize Terraform
terraform init

# Create terraform.tfvars
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

# Plan deployment
terraform plan

# Apply infrastructure
terraform apply
```

### 3. Build and Push Docker Image

```bash
# Get ECR login token
aws ecr get-login-password --region us-east-2 | \
  docker login --username AWS --password-stdin \
  $(terraform output -raw ecr_repository_url | cut -d'/' -f1)

# Build image
docker build -t pixell-runtime:latest ../..

# Tag for ECR
docker tag pixell-runtime:latest \
  $(terraform output -raw ecr_repository_url):latest

# Push to ECR
docker push $(terraform output -raw ecr_repository_url):latest
```

### 4. Deploy ECS Service

Create `task-definition.json`:

```json
{
  "family": "pixell-runtime",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "1024",
  "memory": "2048",
  "containerDefinitions": [
    {
      "name": "pixell-runtime",
      "image": "YOUR_ECR_URL:latest",
      "portMappings": [
        {
          "containerPort": 8000,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "REGISTRY_URL",
          "value": "s3://YOUR_BUCKET/index/registry.json"
        }
      ],
      "logConfiguration": {
        "logDriver": "awslogs",
        "options": {
          "awslogs-group": "/ecs/pixell-runtime",
          "awslogs-region": "us-east-2",
          "awslogs-stream-prefix": "ecs"
        }
      }
    }
  ]
}
```

Register task definition and create service:

```bash
# Register task definition
aws ecs register-task-definition --cli-input-json file://task-definition.json

# Create service
aws ecs create-service \
  --cluster pixell-runtime-cluster \
  --service-name pixell-runtime \
  --task-definition pixell-runtime:1 \
  --desired-count 2 \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[subnet-xxx,subnet-yyy],securityGroups=[sg-xxx]}"
```

### 5. Configure HTTPS (Optional)

1. Request SSL certificate in ACM
2. Update ALB listener to use HTTPS
3. Configure domain name in Route53

## Security Best Practices

1. **Use IAM Roles** instead of access keys when possible
2. **Enable MFA** on your AWS account
3. **Rotate credentials** regularly
4. **Use AWS Secrets Manager** for sensitive configuration
5. **Enable CloudTrail** for audit logging
6. **Implement least privilege** IAM policies

## Monitoring

View your deployment:
- ECS Console: Check service health
- CloudWatch: View logs and metrics
- ALB: Monitor target health

## Cleanup

To remove all resources:

```bash
cd deploy/terraform
terraform destroy
```

## Support

For issues or questions, please refer to the main documentation or create an issue in the repository.