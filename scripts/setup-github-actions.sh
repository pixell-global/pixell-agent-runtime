#!/bin/bash
# GitHub Actions Setup Script for Pixell Runtime

set -e

echo "🚀 Setting up GitHub Actions for Pixell Runtime Deployment"
echo ""

# Check if we're in a git repository
if [ ! -d ".git" ]; then
    echo "❌ Error: Not in a git repository"
    echo "Please run this script from the root of your git repository"
    exit 1
fi

# Check if GitHub remote exists
if ! git remote get-url origin > /dev/null 2>&1; then
    echo "❌ Error: No GitHub remote found"
    echo "Please add a GitHub remote:"
    echo "  git remote add origin https://github.com/yourusername/your-repo.git"
    exit 1
fi

echo "✅ Git repository detected"
echo ""

# Check if AWS CLI is configured
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    echo "❌ Error: AWS CLI not configured"
    echo "Please run: aws configure"
    exit 1
fi

echo "✅ AWS CLI configured"
echo ""

# Get AWS account ID
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "📋 AWS Account ID: $AWS_ACCOUNT_ID"
echo ""

# Check if ECR repository exists
ECR_REPO="pixell-runtime"
ECR_REGION="us-east-2"

if aws ecr describe-repositories --repository-names $ECR_REPO --region $ECR_REGION > /dev/null 2>&1; then
    echo "✅ ECR repository exists: $ECR_REPO"
else
    echo "❌ ECR repository not found: $ECR_REPO"
    echo "Please create it first or update the repository name in the workflows"
    exit 1
fi

echo ""
echo "🔧 Next Steps:"
echo ""
echo "1. Configure GitHub Secrets:"
echo "   Go to: https://github.com/$(git remote get-url origin | sed 's/.*github.com[:/]\([^/]*\/[^/]*\)\.git.*/\1/')/settings/secrets/actions"
echo ""
echo "   Add these secrets:"
echo "   - AWS_ACCESS_KEY_ID: $(aws configure get aws_access_key_id)"
echo "   - AWS_SECRET_ACCESS_KEY: [your-secret-key]"
echo ""
echo "2. Push your code to GitHub:"
echo "   git add ."
echo "   git commit -m 'Add GitHub Actions workflows'"
echo "   git push origin main"
echo ""
echo "3. Monitor the deployment:"
echo "   - Go to Actions tab in your GitHub repository"
echo "   - Watch the 'Build and Push to ECR' workflow"
echo ""
echo "4. Test your deployment:"
echo "   curl http://pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com/runtime/health"
echo ""
echo "📚 For detailed instructions, see: GITHUB_ACTIONS_MIGRATION.md"
echo ""
echo "🎉 Setup complete! Your deployment is now automated with GitHub Actions."
