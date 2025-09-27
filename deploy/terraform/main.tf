terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  required_version = ">= 1.0"
}

provider "aws" {
  region = var.aws_region
}

# Variables
variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-2"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

variable "internal_registry_bucket_name" {
  description = "S3 bucket name for internal developer package registry"
  type        = string
}

variable "external_registry_bucket_name" {
  description = "S3 bucket name for external developer package registry"
  type        = string
}

# VPC Configuration
resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "pixell-runtime-vpc"
    Environment = var.environment
  }
}

# Internet Gateway
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "pixell-runtime-igw"
  }
}

# Public Subnets for ALB
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.${count.index + 1}.0/24"
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = {
    Name = "pixell-runtime-public-${count.index + 1}"
    Type = "public"
  }
}

# Private Subnets for Fargate
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 10}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = {
    Name = "pixell-runtime-private-${count.index + 1}"
    Type = "private"
  }
}

# Route Tables
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "pixell-runtime-public-rt"
  }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# NAT Gateway for private subnets
resource "aws_eip" "nat" {
  count  = 2
  domain = "vpc"

  tags = {
    Name = "pixell-runtime-nat-${count.index + 1}"
  }
}

resource "aws_nat_gateway" "main" {
  count         = 2
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = {
    Name = "pixell-runtime-nat-${count.index + 1}"
  }
}

# Route tables for private subnets
resource "aws_route_table" "private" {
  count  = 2
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }

  tags = {
    Name = "pixell-runtime-private-rt-${count.index + 1}"
  }
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# Security Groups
resource "aws_security_group" "alb" {
  name_prefix = "pixell-runtime-alb-"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "pixell-runtime-alb-sg"
  }
}

resource "aws_security_group" "ecs_tasks" {
  name_prefix = "pixell-runtime-ecs-"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    from_port       = 9090
    to_port         = 9090
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "pixell-runtime-ecs-sg"
  }
}

# S3 Bucket for Internal Registry
resource "aws_s3_bucket" "internal_registry" {
  bucket = var.internal_registry_bucket_name

  tags = {
    Name        = "pixell-internal-registry"
    Environment = var.environment
    Type        = "internal"
  }
}

resource "aws_s3_bucket_versioning" "internal_registry" {
  bucket = aws_s3_bucket.internal_registry.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "internal_registry" {
  bucket = aws_s3_bucket.internal_registry.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "internal_registry" {
  bucket = aws_s3_bucket.internal_registry.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# S3 Bucket for External Registry
resource "aws_s3_bucket" "external_registry" {
  bucket = var.external_registry_bucket_name

  tags = {
    Name        = "pixell-external-registry"
    Environment = var.environment
    Type        = "external"
  }
}

resource "aws_s3_bucket_versioning" "external_registry" {
  bucket = aws_s3_bucket.external_registry.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "external_registry" {
  bucket = aws_s3_bucket.external_registry.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "external_registry" {
  bucket = aws_s3_bucket.external_registry.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ECR Repository
resource "aws_ecr_repository" "pixell_runtime" {
  name                 = "pixell-runtime"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name = "pixell-runtime"
  }
}

# ECS Cluster
resource "aws_ecs_cluster" "main" {
  name = "pixell-runtime-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }

  tags = {
    Name        = "pixell-runtime-cluster"
    Environment = var.environment
  }
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/pixell-runtime"
  retention_in_days = 7

  tags = {
    Name = "pixell-runtime-logs"
  }
}

# IAM Roles
resource "aws_iam_role" "ecs_task_execution" {
  name = "pixell-runtime-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "ecs_task" {
  name = "pixell-runtime-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "ecs_task_s3" {
  name = "pixell-runtime-s3-access"
  role = aws_iam_role.ecs_task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.internal_registry.arn,
          "${aws_s3_bucket.internal_registry.arn}/*",
          aws_s3_bucket.external_registry.arn,
          "${aws_s3_bucket.external_registry.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "cloudwatch:PutMetricData"
        ]
        Resource = "*"
      }
    ]
  })
}

# ALB
resource "aws_lb" "main" {
  name               = "pixell-runtime-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  enable_deletion_protection = false
  enable_http2              = true

  tags = {
    Name        = "pixell-runtime-alb"
    Environment = var.environment
  }
}

resource "aws_lb_target_group" "main" {
  name        = "pixell-runtime-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    enabled             = true
    healthy_threshold   = 3
    interval            = 45
    matcher             = "200"
    path                = "/runtime/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    timeout             = 10
    unhealthy_threshold = 3
  }

  tags = {
    Name = "pixell-runtime-tg"
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.main.arn
  }
}

# Note: For HTTPS, you'll need to add an SSL certificate
# Uncomment and configure the following when you have an ACM certificate:
# resource "aws_lb_listener" "https" {
#   load_balancer_arn = aws_lb.main.arn
#   port              = "443"
#   protocol          = "HTTPS"
#   ssl_policy        = "ELBSecurityPolicy-TLS-1-2-2017-01"
#   certificate_arn   = "arn:aws:acm:region:account-id:certificate/certificate-id"
#
#   default_action {
#     type             = "forward"
#     target_group_arn = aws_lb_target_group.main.arn
#   }
# }

# ECS Task Definition
resource "aws_ecs_task_definition" "pixell_runtime" {
  family                   = "pixell-runtime"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn           = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name  = "pixell-runtime"
      image = "${aws_ecr_repository.pixell_runtime.repository_url}:latest"
      
      environment = [
        {
          name  = "PAR_HOST"
          value = "0.0.0.0"
        },
        {
          name  = "PAR_PORT"
          value = "8000"
        },
        {
          name  = "PAR_INTERNAL_REGISTRY_BUCKET"
          value = aws_s3_bucket.internal_registry.id
        },
        {
          name  = "PAR_EXTERNAL_REGISTRY_BUCKET"
          value = aws_s3_bucket.external_registry.id
        },
        {
          name  = "PAR_LOG_LEVEL"
          value = "INFO"
        }
      ]
      
      portMappings = [
        {
          containerPort = 8000
          protocol      = "tcp"
        },
        {
          containerPort = 9090
          protocol      = "tcp"
        }
      ]
      
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }

      healthCheck = {
        command     = [
          "CMD-SHELL",
          "python -c \"import json,sys,urllib.request; resp=urllib.request.urlopen('http://127.0.0.1:8000/runtime/health', timeout=9); sys.exit(0 if resp.getcode()==200 else 1)\" || exit 1"
        ]
        interval    = 30
        timeout     = 10
        retries     = 5
        startPeriod = 120
      }
    }
  ])
}

# ECS Service
resource "aws_ecs_service" "pixell_runtime" {
  name            = "pixell-runtime"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.pixell_runtime.arn
  desired_count   = 2
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.main.arn
    container_name   = "pixell-runtime"
    container_port   = 8000
  }

  depends_on = [
    aws_lb_listener.http,
    aws_lb_target_group.main
  ]
}

# Data sources
data "aws_availability_zones" "available" {
  state = "available"
}

# Outputs
output "alb_dns_name" {
  description = "DNS name of the load balancer"
  value       = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  description = "URL of the ECR repository"
  value       = aws_ecr_repository.pixell_runtime.repository_url
}

output "internal_s3_bucket_name" {
  description = "Name of the internal S3 registry bucket"
  value       = aws_s3_bucket.internal_registry.id
}

output "external_s3_bucket_name" {
  description = "Name of the external S3 registry bucket"
  value       = aws_s3_bucket.external_registry.id
}

output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.main.id
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = aws_ecs_cluster.main.name
}