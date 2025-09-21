# GitHub Actions Deployment

This repository includes automated deployment workflows for the Pixell Agent Runtime.

## Workflows

### 1. `build.yml` - Build and Push to ECR
- **Triggers**: Push to `main`/`develop`, PRs to `main`, manual trigger
- **Purpose**: Builds Docker image and pushes to ECR with multiple tags
- **Tags**: 
  - `{commit-sha}` - Specific commit
  - `{branch-name}` - Branch name (e.g., `main`, `develop`)
  - `latest` - Only for `main` branch

### 2. `deploy.yml` - Full Deployment
- **Triggers**: Push to `main`/`develop`, PRs to `main`
- **Purpose**: Builds image, pushes to ECR, and updates ECS service
- **Environment**: `production`

## Setup Instructions

### 1. Configure GitHub Secrets

Go to your repository → Settings → Secrets and variables → Actions, and add:

```
AWS_ACCESS_KEY_ID=your-access-key
AWS_SECRET_ACCESS_KEY=your-secret-key
```

### 2. AWS IAM Permissions

The AWS credentials need these permissions:
- `ecr:*` - For pushing Docker images
- `ecs:*` - For updating ECS services (deploy.yml only)
- `iam:PassRole` - For ECS task execution

### 3. Push to GitHub

Once you push your code to GitHub, the workflows will automatically:
1. Build your Docker image
2. Push it to ECR
3. (Optional) Deploy to ECS

## Manual Deployment

If you want to manually deploy after the image is built:

```bash
# Update ECS service to use latest image
aws ecs update-service \
  --cluster pixell-runtime-cluster \
  --service pixell-runtime \
  --force-new-deployment
```

## Monitoring

- **GitHub Actions**: Check the Actions tab in your repository
- **AWS ECS**: Monitor service health in AWS Console
- **Application**: Health check at `/runtime/health`

## Troubleshooting

### Build Failures
- Check Dockerfile syntax
- Verify all dependencies in requirements.txt
- Check GitHub Actions logs

### Deployment Failures
- Verify AWS credentials have correct permissions
- Check ECS service configuration
- Review CloudWatch logs

### ECR Push Failures
- Ensure ECR repository exists
- Verify AWS credentials are valid
- Check repository permissions
