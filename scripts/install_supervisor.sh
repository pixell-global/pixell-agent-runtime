#!/bin/bash
# Installation script for Pixell Agent Runtime Supervisor
#
# This script installs and configures the supervisor on an EC2 instance
# for managing multiple agent deployments with Linux user isolation.
#
# Usage:
#   sudo ./scripts/install_supervisor.sh

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   log_error "This script must be run as root (use sudo)"
   exit 1
fi

log_info "Starting Pixell Agent Runtime Supervisor installation..."

# Detect project root (script is in scripts/ subdirectory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

log_info "Project root: $PROJECT_ROOT"

# Configuration
INSTALL_DIR="/opt/pixell-agent-runtime"
SYSTEMD_SERVICE="/etc/systemd/system/pixell-supervisor.service"
PACKAGES_DIR="/var/lib/pixell/packages"
EXTRACTED_DIR="/var/lib/pixell/extracted"
LOGS_DIR="/var/lib/pixell/logs"

# Step 1: Install Python dependencies
log_info "Installing Python dependencies..."
if ! command -v python3 &> /dev/null; then
    log_error "Python 3 is not installed. Please install Python 3.11+"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | awk '{print $2}')
log_info "Found Python version: $PYTHON_VERSION"

# Step 2: Create directories
log_info "Creating required directories..."
mkdir -p "$PACKAGES_DIR"
mkdir -p "$EXTRACTED_DIR"
mkdir -p "$LOGS_DIR"

chmod 755 "$PACKAGES_DIR"
chmod 755 "$EXTRACTED_DIR"
chmod 755 "$LOGS_DIR"

log_info "Directories created:"
log_info "  - $PACKAGES_DIR (package cache)"
log_info "  - $EXTRACTED_DIR (extracted packages)"
log_info "  - $LOGS_DIR (supervisor logs)"

# Step 3: Copy project to install directory
log_info "Installing PAR to $INSTALL_DIR..."

if [ -d "$INSTALL_DIR" ]; then
    log_warn "Install directory already exists, backing up..."
    mv "$INSTALL_DIR" "$INSTALL_DIR.backup.$(date +%Y%m%d_%H%M%S)"
fi

mkdir -p "$INSTALL_DIR"
cp -r "$PROJECT_ROOT"/* "$INSTALL_DIR/"

log_info "PAR installed to $INSTALL_DIR"

# Step 4: Install Python package
log_info "Installing PAR Python package..."
cd "$INSTALL_DIR"
python3 -m pip install -e . --quiet || {
    log_error "Failed to install PAR package"
    exit 1
}

log_info "PAR package installed successfully"

# Step 5: Install systemd service
log_info "Installing systemd service..."

if [ ! -f "$PROJECT_ROOT/systemd/pixell-supervisor.service" ]; then
    log_error "Systemd service file not found: $PROJECT_ROOT/systemd/pixell-supervisor.service"
    exit 1
fi

cp "$PROJECT_ROOT/systemd/pixell-supervisor.service" "$SYSTEMD_SERVICE"

# Update ExecStart path in service file
sed -i "s|WorkingDirectory=.*|WorkingDirectory=$INSTALL_DIR|g" "$SYSTEMD_SERVICE"

log_info "Systemd service installed: $SYSTEMD_SERVICE"

# Step 6: Reload systemd
log_info "Reloading systemd daemon..."
systemctl daemon-reload

# Step 7: Enable service
log_info "Enabling pixell-supervisor service..."
systemctl enable pixell-supervisor

log_info "Service enabled (will start on boot)"

# Step 8: Start service
log_info "Starting pixell-supervisor service..."
systemctl start pixell-supervisor

# Wait a moment for service to start
sleep 2

# Step 9: Check service status
if systemctl is-active --quiet pixell-supervisor; then
    log_info "✅ Supervisor is running!"

    # Show status
    systemctl status pixell-supervisor --no-pager | head -15

    # Test health endpoint
    log_info "Testing health endpoint..."
    sleep 3

    if curl -sf http://localhost:9000/health > /dev/null 2>&1; then
        log_info "✅ Health endpoint responding"
        curl -s http://localhost:9000/health | python3 -m json.tool || true
    else
        log_warn "Health endpoint not responding yet (may still be starting up)"
    fi
else
    log_error "❌ Supervisor failed to start"
    log_error "Check logs with: sudo journalctl -u pixell-supervisor -n 50"
    exit 1
fi

# Step 10: Display useful information
echo ""
log_info "=========================================="
log_info "Supervisor Installation Complete!"
log_info "=========================================="
echo ""
log_info "Service status:"
log_info "  sudo systemctl status pixell-supervisor"
echo ""
log_info "View logs:"
log_info "  sudo journalctl -u pixell-supervisor -f"
echo ""
log_info "Health check:"
log_info "  curl http://localhost:9000/health"
echo ""
log_info "List agents:"
log_info "  curl http://localhost:9000/agents"
echo ""
log_info "Directories:"
log_info "  Install: $INSTALL_DIR"
log_info "  Packages: $PACKAGES_DIR"
log_info "  Extracted: $EXTRACTED_DIR"
log_info "  Logs: $LOGS_DIR"
echo ""
log_info "=========================================="
