# IAM Policies for Pixell Agent Runtime (PAR)

This document describes the IAM roles and policies required to run PAR in ECS.

## Overview

PAR requires two IAM roles:
1. **Execution Role**: Used by ECS to pull container images and write logs
2. **Task Role**: Used by the running container to access AWS services (S3 only)

## Task Role (Runtime Permissions)

The Task Role is used by the PAR container during execution. It should have **minimal permissions** - only S3 GetObject access to download agent packages.

### Task Role Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowS3GetObject",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject"
      ],
      "Resource": [
        "arn:aws:s3:::pixell-agent-packages/*"
      ]
    },
    {
      "Sid": "AllowS3ListBucket",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket"
      ],
      "Resource": [
        "arn:aws:s3:::pixell-agent-packages"
      ],
      "Condition": {
        "StringLike": {
          "s3:prefix": [
            "*"
          ]
        }
      }
    }
  ]
}
```

### Task Role Trust Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

### Creating the Task Role

```bash
# Create the role
aws iam create-role \
  --role-name pixell-agent-runtime-task-role \
  --assume-role-policy-document file://task-role-trust-policy.json

# Attach the policy
aws iam put-role-policy \
  --role-name pixell-agent-runtime-task-role \
  --policy-name S3PackageAccess \
  --policy-document file://task-role-policy.json
```

## Execution Role (ECS Infrastructure)

The Execution Role is used by ECS to manage the container lifecycle. It needs permissions to:
- Pull container images from ECR
- Write logs to CloudWatch
- Access EFS (if using wheelhouse cache)

### Execution Role Policy

Use the AWS managed policy `AmazonECSTaskExecutionRolePolicy` plus custom EFS permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowEFSMount",
      "Effect": "Allow",
      "Action": [
        "elasticfilesystem:ClientMount",
        "elasticfilesystem:ClientWrite"
      ],
      "Resource": "arn:aws:elasticfilesystem:us-east-2:ACCOUNT_ID:file-system/FILESYSTEM_ID",
      "Condition": {
        "StringEquals": {
          "elasticfilesystem:AccessPointArn": "arn:aws:elasticfilesystem:us-east-2:ACCOUNT_ID:access-point/ACCESS_POINT_ID"
        }
      }
    }
  ]
}
```

### Execution Role Trust Policy

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ecs-tasks.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

### Creating the Execution Role

```bash
# Create the role
aws iam create-role \
  --role-name pixell-agent-runtime-execution-role \
  --assume-role-policy-document file://execution-role-trust-policy.json

# Attach AWS managed policy
aws iam attach-role-policy \
  --role-name pixell-agent-runtime-execution-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy

# Attach custom EFS policy
aws iam put-role-policy \
  --role-name pixell-agent-runtime-execution-role \
  --policy-name EFSAccess \
  --policy-document file://execution-role-efs-policy.json
```

## Security Best Practices

### 1. Least Privilege
- Task Role has **only** S3 GetObject access
- No ECS, ELB, Service Discovery, or Database permissions
- No IAM permissions

### 2. Resource Restrictions
- Restrict S3 access to specific bucket (`pixell-agent-packages`)
- Optionally restrict to specific prefixes per agent

### 3. Network Isolation
- PAR should only make outbound calls to:
  - S3 (for package downloads)
  - No other AWS services
- Use VPC endpoints for S3 to keep traffic within AWS network
- Security group should block all inbound except from ALB/NLB

### 4. Forbidden Permissions
PAR Task Role should **NEVER** have:
- `ecs:*` - No ECS control plane access
- `elasticloadbalancing:*` - No ALB/NLB management
- `servicediscovery:*` - No Cloud Map registration
- `dynamodb:*` - No database access
- `rds:*` - No database access
- `iam:*` - No IAM management

## Verification

### Test S3 Access
```bash
# Should succeed
aws s3 cp s3://pixell-agent-packages/test.apkg /tmp/test.apkg --profile par-task-role

# Should fail (wrong bucket)
aws s3 cp s3://other-bucket/test.apkg /tmp/test.apkg --profile par-task-role
```

### Test Forbidden Actions
```bash
# Should all fail with AccessDenied
aws ecs list-tasks --profile par-task-role
aws elbv2 describe-load-balancers --profile par-task-role
aws servicediscovery list-services --profile par-task-role
aws dynamodb list-tables --profile par-task-role
```

## CloudWatch Logs

PAR writes structured JSON logs to stdout/stderr, which are captured by CloudWatch Logs.

### Log Group Configuration

```bash
# Create log group
aws logs create-log-group --log-group-name /ecs/pixell-agent-runtime

# Set retention (30 days recommended)
aws logs put-retention-policy \
  --log-group-name /ecs/pixell-agent-runtime \
  --retention-in-days 30
```

### Log Fields
All log entries include:
- `level`: Log level (info, warning, error)
- `timestamp`: ISO 8601 timestamp
- `agent_app_id`: Agent identifier
- `deployment_id`: Deployment identifier (optional)
- `event`: Event name
- Additional context fields

## Monitoring and Alarms

Recommended CloudWatch alarms:
1. **Boot failures**: Count of "Runtime failed" errors > 5 in 5 minutes
2. **Package download failures**: S3 403/404 errors
3. **Memory/CPU**: Container resource usage > 80%
4. **Health check failures**: ECS health check failures > 3

## Troubleshooting

### S3 Access Denied
1. Verify Task Role has S3 GetObject permission
2. Check S3 bucket policy allows the Task Role
3. Verify `PACKAGE_URL` matches allowed bucket
4. Check S3 VPC endpoint configuration

### ECS Task Won't Start
1. Verify Execution Role has ECR pull permissions
2. Check CloudWatch Logs creation permissions
3. Verify EFS mount permissions (if using wheelhouse)

### Container Exits Immediately
1. Check CloudWatch Logs for error messages
2. Verify `AGENT_APP_ID` environment variable is set
3. Verify `PACKAGE_URL` points to valid S3 object
4. Check S3 object exists and is accessible
