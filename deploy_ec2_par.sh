#!/bin/bash
# deploy_ec2_par.sh - Deploy PAR Supervisor to Existing EC2 Instance via SSM
#
# This script deploys the Pixell Agent Runtime (PAR) supervisor to an existing
# EC2 instance that was provisioned by PAC's create-ec2-runtime-instance.sh.
# Uses AWS Systems Manager (SSM) - no SSH keys required.
#
# Usage:
#   ./scripts/deploy_ec2_par.sh <instance-id>
#
# Example:
#   ./scripts/deploy_ec2_par.sh i-09dcb7f387166efd0
#
# Requirements:
#   - Run from PAR repository root
#   - EC2 instance already exists (Amazon Linux 2023)
#   - AWS CLI installed and configured
#   - Instance has SSM agent running (pre-installed on Amazon Linux 2023)
#   - Instance IAM role has AmazonSSMManagedInstanceCore policy

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP $1]${NC} $2"
}

# Usage function
usage() {
    cat << EOF
Usage: $0 <instance-id>

Deploy PAR supervisor to an existing EC2 instance using AWS Systems Manager (no SSH key needed).

Arguments:
  instance-id    EC2 instance ID (e.g., i-09dcb7f387166efd0)

Examples:
  $0 i-09dcb7f387166efd0

Notes:
  - Must run from PAR repository root
  - EC2 instance must already exist (created by PAC team)
  - AWS CLI must be configured with valid credentials
  - Instance must have SSM agent running (automatic on Amazon Linux 2023)
  - Instance IAM role must have AmazonSSMManagedInstanceCore policy
EOF
    exit 1
}

# Check if running from PAR repository root
check_repo_root() {
    if [ ! -f "pyproject.toml" ] || [ ! -d "src/pixell_runtime" ]; then
        log_error "Must run from PAR repository root"
        log_error "Current directory: $(pwd)"
        log_error "Expected: pixell-agent-runtime/"
        exit 1
    fi
}

# Validate arguments
if [ $# -ne 1 ]; then
    log_error "Invalid number of arguments"
    usage
fi

INSTANCE_ID="$1"
AWS_REGION="${AWS_REGION:-us-east-2}"

# Validate instance ID format
if ! [[ "$INSTANCE_ID" =~ ^i-[0-9a-f]+$ ]]; then
    log_error "Invalid instance ID format: $INSTANCE_ID"
    log_error "Expected format: i-xxxxxxxxxxxxxxxxx"
    exit 1
fi

log_info "=========================================="
log_info "PAR Supervisor Deployment (via SSM)"
log_info "=========================================="
log_info "Target instance: $INSTANCE_ID"
log_info "AWS region: $AWS_REGION"
echo ""

# Check repository root
check_repo_root

# Step 1: Check prerequisites
log_step "1/7" "Checking prerequisites..."

if ! command -v python3 &> /dev/null; then
    log_error "python3 is not installed"
    exit 1
fi

if ! command -v aws &> /dev/null; then
    log_error "AWS CLI is not installed"
    log_error "Install with: pip install awscli"
    exit 1
fi

if ! command -v jq &> /dev/null; then
    log_error "jq is not installed (required for JSON parsing)"
    log_error "Install with: brew install jq (macOS) or apt install jq (Ubuntu)"
    exit 1
fi

log_info "✅ Prerequisites OK"

# Step 2: Build PAR wheel package
log_step "2/7" "Building PAR wheel package..."

# Install build package if not present
python3 -m pip install --quiet build 2>/dev/null || {
    log_info "Installing build package..."
    python3 -m pip install --user build
}

# Clean previous builds
rm -rf dist/ build/ *.egg-info 2>/dev/null || true

# Build wheel
log_info "Running: python3 -m build --wheel"
python3 -m build --wheel || {
    log_error "Failed to build wheel package"
    exit 1
}

# Find the built wheel
WHEEL_FILE=$(ls -t dist/pixell_runtime-*.whl 2>/dev/null | head -1)
if [ ! -f "$WHEEL_FILE" ]; then
    log_error "Wheel file not found in dist/"
    log_error "Expected: dist/pixell_runtime-*.whl"
    exit 1
fi

WHEEL_FILENAME=$(basename "$WHEEL_FILE")
log_info "✅ Built: $WHEEL_FILENAME"

# Verify wheel version matches pyproject.toml
EXPECTED_VERSION=$(grep '^version = ' pyproject.toml | cut -d'"' -f2)
if [[ ! "$WHEEL_FILENAME" =~ $EXPECTED_VERSION ]]; then
    log_error "Version mismatch detected!"
    log_error "  pyproject.toml version: $EXPECTED_VERSION"
    log_error "  Built wheel: $WHEEL_FILENAME"
    log_error "This indicates the build system is not reading pyproject.toml correctly"
    exit 1
fi
log_info "✅ Version verified: $EXPECTED_VERSION"

# Step 3: Verify SSM connectivity
log_step "3/7" "Verifying SSM connectivity..."

SSM_STATUS=$(aws ssm describe-instance-information \
    --region "$AWS_REGION" \
    --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --query 'InstanceInformationList[0].PingStatus' \
    --output text 2>/dev/null || echo "NotFound")

if [ "$SSM_STATUS" != "Online" ]; then
    log_error "Instance is not reachable via SSM"
    log_error "Status: $SSM_STATUS"
    log_error "Check:"
    log_error "  1. Instance is running"
    log_error "  2. SSM agent is running (should be automatic on Amazon Linux 2023)"
    log_error "  3. Instance IAM role has AmazonSSMManagedInstanceCore policy"
    log_error "  4. Wait a few minutes for SSM agent to register after policy attachment"
    exit 1
fi

log_info "✅ SSM connectivity OK (Status: $SSM_STATUS)"

# Get instance details
log_info "Fetching instance details..."
INSTANCE_INFO=$(aws ec2 describe-instances \
    --region "$AWS_REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0]' \
    --output json)

INSTANCE_IP=$(echo "$INSTANCE_INFO" | jq -r '.PrivateIpAddress')
PUBLIC_IP=$(echo "$INSTANCE_INFO" | jq -r '.PublicIpAddress // "N/A"')

log_info "Private IP: $INSTANCE_IP"
log_info "Public IP: $PUBLIC_IP"

# Step 4: Upload wheel to S3
log_step "4/7" "Uploading wheel to S3..."

S3_BUCKET="pixell-agent-packages"
S3_KEY="deployments/par-supervisor/$WHEEL_FILENAME"
S3_URL="s3://$S3_BUCKET/$S3_KEY"

aws s3 cp "$WHEEL_FILE" "$S3_URL" --region "$AWS_REGION" || {
    log_error "Failed to upload wheel to S3"
    log_error "Make sure bucket $S3_BUCKET exists and you have write access"
    exit 1
}

log_info "✅ Wheel uploaded to $S3_URL"

# Step 5: Install PAR on EC2 via SSM
log_step "5/7" "Installing PAR on EC2..."

# Execute installation directly via SSM (no separate script file)
# NOTE: We install to BOTH supervisor venv AND system site-packages because:
#   - Supervisor runs from venv: /opt/pixell-agent-runtime/venv/bin/python
#   - Agents run from system python: /usr/bin/python3.11 -m pixell_runtime
COMMAND_ID=$(aws ssm send-command \
    --region "$AWS_REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --parameters "{\"commands\":[
\"set -e\",
\"echo '[1/7] Downloading wheel from S3...'\",
\"aws s3 cp '$S3_URL' /tmp/'$WHEEL_FILENAME' --region '$AWS_REGION'\",
\"echo '[2/7] Installing Python 3.11 if needed...'\",
\"sudo yum install -y python3.11 python3.11-pip python3.11-devel 2>/dev/null || echo 'Already installed'\",
\"echo '[3/7] Uninstalling old version from venv...'\",
\"sudo /opt/pixell-agent-runtime/venv/bin/pip uninstall -y pixell-runtime 2>/dev/null || echo 'No old version'\",
\"echo '[4/7] Installing new wheel into supervisor venv...'\",
\"sudo /opt/pixell-agent-runtime/venv/bin/pip install /tmp/'$WHEEL_FILENAME'\",
\"echo '[5/7] Installing new wheel into system site-packages (for agents)...'\",
\"sudo /usr/bin/pip3.11 install --upgrade /tmp/'$WHEEL_FILENAME'\",
\"echo '[6/7] Updating supervisor configuration...'\",
\"sudo sed -i 's/^PORT=.*/SUPERVISOR_PORT=9000/' /etc/par-supervisor.conf\",
\"echo 'Updated SUPERVISOR_PORT to 9000'\",
\"cat /etc/par-supervisor.conf | grep SUPERVISOR_PORT\",
\"echo '[7/7] Restarting supervisor service...'\",
\"sudo systemctl daemon-reload\",
\"sudo systemctl restart par-supervisor\",
\"sleep 3\",
\"sudo systemctl is-active par-supervisor && echo 'Service active' || echo 'Service failed'\",
\"echo 'Supervisor venv:' && /opt/pixell-agent-runtime/venv/bin/pip show pixell-runtime | grep Version\",
\"echo 'System site-packages:' && /usr/bin/pip3.11 show pixell-runtime | grep Version\"
]}" \
    --comment "Install PAR $EXPECTED_VERSION" \
    --query 'Command.CommandId' \
    --output text)

log_info "SSM Command ID: $COMMAND_ID"
log_info "Waiting for installation to complete..."

# Wait for command to complete
aws ssm wait command-executed \
    --region "$AWS_REGION" \
    --command-id "$COMMAND_ID" \
    --instance-id "$INSTANCE_ID" || {
    log_error "Installation command timed out or failed"
    log_error "Check output: aws ssm get-command-invocation --command-id $COMMAND_ID --instance-id $INSTANCE_ID --region $AWS_REGION"
    exit 1
}

# Get command output
log_info "Installation command output:"
aws ssm get-command-invocation \
    --region "$AWS_REGION" \
    --command-id "$COMMAND_ID" \
    --instance-id "$INSTANCE_ID" \
    --query 'StandardOutputContent' \
    --output text

# Check command status
COMMAND_STATUS=$(aws ssm get-command-invocation \
    --region "$AWS_REGION" \
    --command-id "$COMMAND_ID" \
    --instance-id "$INSTANCE_ID" \
    --query 'Status' \
    --output text)

if [ "$COMMAND_STATUS" != "Success" ]; then
    log_error "Installation failed with status: $COMMAND_STATUS"
    log_error "Error output:"
    aws ssm get-command-invocation \
        --region "$AWS_REGION" \
        --command-id "$COMMAND_ID" \
        --instance-id "$INSTANCE_ID" \
        --query 'StandardErrorContent' \
        --output text
    exit 1
fi

log_info "✅ PAR supervisor installed on EC2"

# Step 6: Verify installed version matches expected version
log_step "6/7" "Verifying installed version..."
VERIFY_CMD=$(aws ssm send-command \
    --region "$AWS_REGION" \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --parameters '{"commands":["/opt/pixell-agent-runtime/venv/bin/pip show pixell-runtime | grep Version"]}' \
    --comment "Verify PAR version" \
    --query 'Command.CommandId' \
    --output text)

sleep 3

INSTALLED_VERSION=$(aws ssm get-command-invocation \
    --region "$AWS_REGION" \
    --command-id "$VERIFY_CMD" \
    --instance-id "$INSTANCE_ID" \
    --query 'StandardOutputContent' \
    --output text | grep 'Version:' | awk '{print $2}' || echo "unknown")

if [ "$INSTALLED_VERSION" != "$EXPECTED_VERSION" ]; then
    log_error "Installed version mismatch!"
    log_error "  Expected: $EXPECTED_VERSION"
    log_error "  Installed: $INSTALLED_VERSION"
    log_error "Deployment succeeded but EC2 has wrong version"
    exit 1
fi
log_info "✅ Installed version verified: $INSTALLED_VERSION"

# Step 7: Verify external health check
log_step "7/7" "Verifying external health check..."

sleep 3

HEALTH_IP="$PUBLIC_IP"
if [ "$HEALTH_IP" = "N/A" ]; then
    HEALTH_IP="$INSTANCE_IP"
    log_warn "No public IP, using private IP: $HEALTH_IP"
fi

if curl -sf "http://$HEALTH_IP:9000/health" > /dev/null 2>&1; then
    log_info "✅ External health check passed"
    log_info "Health response:"
    curl -s "http://$HEALTH_IP:9000/health" | python3 -m json.tool || true
else
    log_warn "❌ Cannot reach supervisor from outside (this is normal if instance has no public IP)"
    log_info "Supervisor should be accessible from within the VPC at: http://$INSTANCE_IP:9000/health"
fi

echo ""
log_info "=========================================="
log_info "✅ PAR SUPERVISOR DEPLOYED SUCCESSFULLY"
log_info "=========================================="
echo ""
log_info "Instance Details:"
log_info "  Instance ID: $INSTANCE_ID"
log_info "  Private IP: $INSTANCE_IP"
log_info "  Public IP: $PUBLIC_IP"
log_info "  Region: $AWS_REGION"
log_info "  Supervisor Port: 9000"
echo ""
log_warn "⚠️  NEXT STEP: Register instance in PAC database"
log_info "Run this command from PAC repository:"
echo ""
echo "  node scripts/register-ec2-instance.js \\"
echo "    --instance-id $INSTANCE_ID \\"
echo "    --private-ip $INSTANCE_IP \\"
echo "    --region $AWS_REGION \\"
echo "    --capacity 20"
echo ""
log_info "=========================================="
log_info "Useful Commands"
log_info "=========================================="
echo ""
log_info "Check service status (via SSM):"
echo "  aws ssm start-session --target $INSTANCE_ID --region $AWS_REGION"
echo "  sudo systemctl status par-supervisor"
echo ""
log_info "View logs (via SSM):"
echo "  aws ssm start-session --target $INSTANCE_ID --region $AWS_REGION"
echo "  sudo journalctl -u par-supervisor -f"
echo ""
log_info "Test health endpoint (from VPC):"
echo "  curl http://$INSTANCE_IP:9000/health"
echo ""
log_info "List agents:"
echo "  curl http://$INSTANCE_IP:9000/agents"
echo ""
log_info "Get supervisor status:"
echo "  curl http://$INSTANCE_IP:9000/status"
echo ""
log_info "=========================================="
log_info "Test Agent Deployment"
log_info "=========================================="
echo ""
log_info "Deploy a test agent:"
cat << 'EOF'
  curl -X POST http://$INSTANCE_IP:9000/agents \
    -H "Content-Type: application/json" \
    -d '{
      "agent_app_id": "test-agent-001",
      "deployment_id": "deploy-001",
      "package_url": "s3://pixell-agent-packages/path/to/agent.apkg",
      "version": "1.0.0",
      "org_id": "org-123"
    }'
EOF
echo ""
log_info "=========================================="
log_info "Configuration Files on EC2"
log_info "=========================================="
echo ""
log_info "  Config: /etc/par-supervisor.conf"
log_info "  Service: /etc/systemd/system/par-supervisor.service"
log_info "  Packages: /var/lib/pixell/packages"
log_info "  Extracted: /var/lib/pixell/extracted"
log_info "  Logs: /var/lib/pixell/logs"
log_info "  Agents: /var/lib/pixell/agents"
echo ""
log_info "=========================================="
log_info "🚀 Deployment Complete!"
log_info "=========================================="
