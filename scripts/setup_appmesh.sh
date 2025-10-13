#!/usr/bin/env bash
set -euo pipefail

# Setup AWS App Mesh for Pixell Agent Runtime
# This script creates the App Mesh infrastructure for Envoy-based A2A routing

AWS_REGION="${AWS_REGION:-us-east-2}"
MESH_NAME="pixell-runtime-mesh"
VIRTUAL_NODE_NAME="par-node"
VIRTUAL_SERVICE_NAME="par.pixell-runtime.local"
NAMESPACE="pixell-runtime.local"

echo "==================================================="
echo " Pixell Runtime - App Mesh Setup"
echo "==================================================="
echo "Region: $AWS_REGION"
echo "Mesh: $MESH_NAME"
echo ""

# Step 1: Create App Mesh
echo "Step 1: Creating App Mesh..."
if aws appmesh describe-mesh --region "$AWS_REGION" --mesh-name "$MESH_NAME" 2>/dev/null; then
  echo "✓ Mesh already exists: $MESH_NAME"
else
  aws appmesh create-mesh \
    --region "$AWS_REGION" \
    --mesh-name "$MESH_NAME" \
    --spec "{}" \
    >/dev/null
  echo "✓ Created mesh: $MESH_NAME"
fi

# Step 2: Get Cloud Map namespace ID
echo ""
echo "Step 2: Looking up Cloud Map namespace..."
NAMESPACE_ID=$(aws servicediscovery list-namespaces \
  --region "$AWS_REGION" \
  --query "Namespaces[?Name=='$NAMESPACE'].Id" \
  --output text)

if [[ -z "$NAMESPACE_ID" ]]; then
  echo "❌ Cloud Map namespace not found: $NAMESPACE"
  echo "Run scripts/setup_service_discovery.sh first"
  exit 1
fi
echo "✓ Found namespace: $NAMESPACE (ID: $NAMESPACE_ID)"

# Step 3: Get Cloud Map service ARN for PAR
echo ""
echo "Step 3: Looking up Cloud Map service..."
SERVICE_ARN=$(aws servicediscovery list-services \
  --region "$AWS_REGION" \
  --filters "Name=NAMESPACE_ID,Values=$NAMESPACE_ID,Condition=EQ" \
  --query "Services[?Name=='par'].Arn" \
  --output text)

if [[ -z "$SERVICE_ARN" ]]; then
  echo "❌ Cloud Map service 'par' not found in namespace $NAMESPACE"
  echo "Creating service..."

  SERVICE_ID=$(aws servicediscovery create-service \
    --region "$AWS_REGION" \
    --name "par" \
    --namespace-id "$NAMESPACE_ID" \
    --dns-config "NamespaceId=$NAMESPACE_ID,DnsRecords=[{Type=A,TTL=10}]" \
    --health-check-custom-config "FailureThreshold=1" \
    --query 'Service.Arn' \
    --output text)

  SERVICE_ARN="$SERVICE_ID"
  echo "✓ Created service: par (ARN: $SERVICE_ARN)"
else
  echo "✓ Found service: par (ARN: $SERVICE_ARN)"
fi

# Step 4: Create Virtual Node
echo ""
echo "Step 4: Creating Virtual Node..."

VIRTUAL_NODE_SPEC=$(cat <<EOF
{
  "listeners": [
    {
      "portMapping": {
        "port": 50051,
        "protocol": "grpc"
      },
      "healthCheck": {
        "protocol": "grpc",
        "port": 50051,
        "path": "/agent.AgentService/Health",
        "intervalMillis": 30000,
        "timeoutMillis": 5000,
        "unhealthyThreshold": 2,
        "healthyThreshold": 2
      }
    }
  ],
  "serviceDiscovery": {
    "awsCloudMap": {
      "namespaceName": "$NAMESPACE",
      "serviceName": "par"
    }
  },
  "logging": {
    "accessLog": {
      "file": {
        "path": "/dev/stdout"
      }
    }
  }
}
EOF
)

if aws appmesh describe-virtual-node \
  --region "$AWS_REGION" \
  --mesh-name "$MESH_NAME" \
  --virtual-node-name "$VIRTUAL_NODE_NAME" 2>/dev/null; then

  echo "⚠️  Virtual node already exists, updating..."
  aws appmesh update-virtual-node \
    --region "$AWS_REGION" \
    --mesh-name "$MESH_NAME" \
    --virtual-node-name "$VIRTUAL_NODE_NAME" \
    --spec "$VIRTUAL_NODE_SPEC" \
    >/dev/null
  echo "✓ Updated virtual node: $VIRTUAL_NODE_NAME"
else
  aws appmesh create-virtual-node \
    --region "$AWS_REGION" \
    --mesh-name "$MESH_NAME" \
    --virtual-node-name "$VIRTUAL_NODE_NAME" \
    --spec "$VIRTUAL_NODE_SPEC" \
    >/dev/null
  echo "✓ Created virtual node: $VIRTUAL_NODE_NAME"
fi

# Step 5: Create Virtual Service
echo ""
echo "Step 5: Creating Virtual Service..."

VIRTUAL_SERVICE_SPEC=$(cat <<EOF
{
  "provider": {
    "virtualNode": {
      "virtualNodeName": "$VIRTUAL_NODE_NAME"
    }
  }
}
EOF
)

if aws appmesh describe-virtual-service \
  --region "$AWS_REGION" \
  --mesh-name "$MESH_NAME" \
  --virtual-service-name "$VIRTUAL_SERVICE_NAME" 2>/dev/null; then

  echo "⚠️  Virtual service already exists, updating..."
  aws appmesh update-virtual-service \
    --region "$AWS_REGION" \
    --mesh-name "$MESH_NAME" \
    --virtual-service-name "$VIRTUAL_SERVICE_NAME" \
    --spec "$VIRTUAL_SERVICE_SPEC" \
    >/dev/null
  echo "✓ Updated virtual service: $VIRTUAL_SERVICE_NAME"
else
  aws appmesh create-virtual-service \
    --region "$AWS_REGION" \
    --mesh-name "$MESH_NAME" \
    --virtual-service-name "$VIRTUAL_SERVICE_NAME" \
    --spec "$VIRTUAL_SERVICE_SPEC" \
    >/dev/null
  echo "✓ Created virtual service: $VIRTUAL_SERVICE_NAME"
fi

# Step 6: Get Virtual Node ARN for task definition
echo ""
echo "Step 6: Getting Virtual Node ARN..."
VIRTUAL_NODE_ARN=$(aws appmesh describe-virtual-node \
  --region "$AWS_REGION" \
  --mesh-name "$MESH_NAME" \
  --virtual-node-name "$VIRTUAL_NODE_NAME" \
  --query 'virtualNode.metadata.arn' \
  --output text)

echo "✓ Virtual Node ARN: $VIRTUAL_NODE_ARN"

# Summary
echo ""
echo "==================================================="
echo " App Mesh Setup Complete!"
echo "==================================================="
echo ""
echo "Next steps:"
echo "1. Update task definition with APPMESH_RESOURCE_ARN:"
echo "   $VIRTUAL_NODE_ARN"
echo ""
echo "2. Deploy with Envoy sidecar:"
echo "   ./scripts/deploy_par.sh --use-envoy"
echo ""
echo "3. Update ECS service to use new task definition"
echo ""
echo "Virtual Service DNS: $VIRTUAL_SERVICE_NAME"
echo "Agents will be accessible via NLB with x-deployment-id header"
echo "===================================================="