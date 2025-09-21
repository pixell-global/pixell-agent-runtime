# GitHub Actions Migration Guide

## 🚀 Migration from Local Deployment to GitHub Actions

This guide will help you migrate from local Docker builds to automated GitHub Actions deployment.

## Prerequisites

1. **GitHub Repository**: Your code must be in a GitHub repository
2. **AWS Account**: With existing ECR repository and ECS infrastructure
3. **AWS Credentials**: For GitHub Actions to access AWS services

## Step 1: Configure GitHub Secrets

### 1.1 Navigate to Repository Settings
1. Go to your GitHub repository
2. Click **Settings** tab
3. In the left sidebar, click **Secrets and variables** → **Actions**

### 1.2 Add Required Secrets
Click **New repository secret** and add:

```
Name: AWS_ACCESS_KEY_ID
Value: your-aws-access-key-id
```

```
Name: AWS_SECRET_ACCESS_KEY  
Value: your-aws-secret-access-key
```

### 1.3 AWS IAM Permissions
The AWS user/role needs these permissions:

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "ecr:GetAuthorizationToken",
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
                "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
                "ecr:PutImage"
            ],
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "ecs:UpdateService",
                "ecs:DescribeServices",
                "ecs:DescribeTaskDefinition",
                "ecs:RegisterTaskDefinition"
            ],
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "iam:PassRole"
            ],
            "Resource": "arn:aws:iam::636212886452:role/pixell-runtime-*"
        }
    ]
}
```

## Step 2: Push Code to GitHub

```bash
# Add all changes
git add .

# Commit changes
git commit -m "Migrate to GitHub Actions deployment"

# Push to GitHub
git push origin main
```

## Step 3: Verify GitHub Actions Setup

### 3.1 Check Workflows
1. Go to your GitHub repository
2. Click **Actions** tab
3. You should see two workflows:
   - **Build and Push to ECR** (build.yml)
   - **Build and Deploy to AWS ECR** (deploy.yml)

### 3.2 Monitor First Build
1. Click on the **Build and Push to ECR** workflow
2. Click **Run workflow** → **Run workflow**
3. Monitor the build progress

## Step 4: Deployment Options

### Option A: Build Only (Recommended for Testing)
- **Workflow**: `build.yml`
- **Triggers**: Push to main/develop, PRs, manual
- **Action**: Builds and pushes to ECR
- **Manual Deploy**: You control when to deploy

### Option B: Full Auto-Deployment
- **Workflow**: `deploy.yml`  
- **Triggers**: Push to main/develop, PRs
- **Action**: Builds, pushes, and deploys automatically
- **Environment**: Production

## Step 5: Manual Deployment (if using build-only)

After GitHub Actions builds your image, deploy manually:

```bash
# Update ECS service to use latest image
aws ecs update-service \
  --cluster pixell-runtime-cluster \
  --service pixell-runtime \
  --force-new-deployment
```

## Step 6: Verify Deployment

### 6.1 Check ECS Service
```bash
aws ecs describe-services \
  --cluster pixell-runtime-cluster \
  --services pixell-runtime \
  --region us-east-2
```

### 6.2 Test Application
```bash
# Health check
curl http://pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com/runtime/health

# View packages
curl http://pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com/runtime/packages
```

## Migration Benefits

✅ **No Local Docker Issues**: No more disk space problems  
✅ **Consistent Builds**: Same environment every time  
✅ **Automatic Deployments**: Deploy on every code change  
✅ **Multiple Tags**: Commit SHA, branch name, latest  
✅ **Rollback Capability**: Deploy any previous commit  
✅ **Build Logs**: Full visibility in GitHub Actions  
✅ **Team Collaboration**: Everyone can trigger deployments  

## Troubleshooting

### Build Failures
- Check GitHub Actions logs
- Verify Dockerfile syntax
- Ensure all dependencies are in requirements.txt

### AWS Permission Issues
- Verify AWS credentials in GitHub secrets
- Check IAM permissions
- Ensure ECR repository exists

### ECS Deployment Issues
- Check ECS service configuration
- Verify task definition
- Review CloudWatch logs

## Next Steps

1. **Set up monitoring**: Configure alerts for deployment failures
2. **Add staging environment**: Create separate workflow for staging
3. **Implement blue-green deployments**: For zero-downtime deployments
4. **Add security scanning**: Scan Docker images for vulnerabilities

## Support

- **GitHub Actions Logs**: Check the Actions tab in your repository
- **AWS CloudWatch**: Monitor ECS service logs
- **Documentation**: See `.github/README.md` for detailed workflow info
