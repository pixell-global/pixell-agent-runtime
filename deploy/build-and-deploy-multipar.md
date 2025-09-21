# Build and Deploy Multi-PAR to AWS

## Overview

This guide explains how to build the Multi-PAR Docker image and deploy it to replace the current single PAR deployment.

## Current vs New Architecture

**Current (Single PAR)**:
```
Fargate Task
└── PAR (single process)
    └── All agents in one process
```

**New (Multi-PAR)**:
```
Fargate Task
└── Supervisor (port 80)
    ├── PAR Worker 1 (port 8001) → Agent A
    ├── PAR Worker 2 (port 8002) → Agent B
    └── PAR Worker 3 (port 8003) → Agent C
```

## Build Steps

### 1. Create Multi-PAR Dockerfile

Create `deploy/docker/Dockerfile.multipar`:

```dockerfile
FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY setup.py .
COPY pyproject.toml .

# Install the package
RUN pip install -e .

# Create directories for packages and runtime
RUN mkdir -p /tmp/pixell-runtime/packages

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PAR_MODE=supervisor

# Expose supervisor port (PAC will override this)
EXPOSE 80

# Start supervisor (not individual PAR)
CMD ["python", "-m", "src.run_supervisor"]
```

### 2. Build Docker Image (Linux AMD64)

```bash
# Build for Linux AMD64 architecture (required for Fargate)
docker buildx build \
  --platform linux/amd64 \
  -t pixell-multipar:latest \
  -f deploy/docker/Dockerfile.multipar \
  .
```

### 3. Tag and Push to ECR

```bash
# Get ECR login token
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin 636212886452.dkr.ecr.us-east-2.amazonaws.com

# Tag the image
docker tag pixell-multipar:latest 636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-runtime:multipar

# Push to ECR
docker push 636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-runtime:multipar
```

## Deploy to ECS

### Option 1: Update Existing Service (Recommended)

Update the ECS task definition to use the new image:

```bash
# 1. Register new task definition with multipar image
aws ecs register-task-definition \
  --family pixell-runtime \
  --network-mode awsvpc \
  --requires-compatibilities FARGATE \
  --cpu "512" \
  --memory "1024" \
  --container-definitions '[
    {
      "name": "pixell-runtime",
      "image": "636212886452.dkr.ecr.us-east-2.amazonaws.com/pixell-runtime:multipar",
      "portMappings": [
        {
          "containerPort": 80,
          "protocol": "tcp"
        }
      ],
      "environment": [
        {
          "name": "SUPERVISOR_PORT",
          "value": "80"
        },
        {
          "name": "PAR_MODE",
          "value": "supervisor"
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
  ]' \
  --region us-east-2

# 2. Update service to use new task definition
aws ecs update-service \
  --cluster pixell-runtime-cluster \
  --service pixell-runtime \
  --task-definition pixell-runtime \
  --region us-east-2
```

### Option 2: Update via Terraform

Update `deploy/terraform/main.tf`:

```hcl
# In the container_definitions
container_definitions = jsonencode([
  {
    name  = "pixell-runtime"
    image = "${aws_ecr_repository.pixell_runtime.repository_url}:multipar"  # Changed tag
    
    portMappings = [
      {
        containerPort = 80  # Supervisor listens on 80
        protocol      = "tcp"
      }
    ]
    
    environment = [
      {
        name  = "SUPERVISOR_PORT"
        value = "80"
      },
      {
        name  = "PAR_MODE"
        value = "supervisor"
      }
    ]
    # ... rest of configuration
  }
])
```

Then apply:
```bash
cd deploy/terraform
terraform apply
```

## What PAC Needs to Know

Based on the architecture:

1. **PAC doesn't need to change image names** - It just needs to know:
   - Which ECR image to deploy: `pixell-runtime:multipar`
   - Which port to expose: 80 (supervisor port)
   - How much CPU/RAM to allocate to the Fargate task

2. **The Multi-PAR supervisor handles**:
   - Spawning individual PAR workers
   - Assigning ports to each worker (8001, 8002, etc.)
   - Routing requests to the correct worker

3. **PAC's only responsibility**:
   - Deploy the Fargate task with the Multi-PAR image
   - Route external traffic to port 80
   - Scale Fargate tasks if needed

## Verification Steps

After deployment:

```bash
# 1. Check health
curl http://pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com/supervisor/status

# 2. View running PAR processes
curl http://pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com/supervisor/processes

# 3. Check logs
aws logs tail /ecs/pixell-runtime --follow --region us-east-2
```

## Important Notes

1. **Port 80 vs 8000**: The supervisor can listen on any port. PAC tells it which port via environment variable or container port mapping.

2. **No Agent Routing in PAC**: PAC doesn't need to know about individual agents or their ports. The supervisor handles all internal routing.

3. **Resource Allocation**: The Fargate task resources (CPU/RAM) are shared among all PAR workers. The supervisor manages this distribution.