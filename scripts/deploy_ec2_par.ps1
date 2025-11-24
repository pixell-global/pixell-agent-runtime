Param(
    [Parameter(Mandatory=$false)]
    [string]$InstanceId = "i-09dcb7f387166efd0",

    [string]$AwsRegion = "us-east-2"
)

# region helper functions
function Write-Step($index, $msg) { Write-Host "[STEP $index] $msg" -ForegroundColor Cyan }
function Write-Info($msg) { Write-Host "[INFO]  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[WARN]  $msg" -ForegroundColor Yellow }
function Write-Err($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red }
function Fail($msg) { Write-Err $msg; exit 1 }
# endregion

# region pre-checks
if (!(Test-Path "pyproject.toml") -or !(Test-Path "src/pixell_runtime")) {
    Fail "Must run from repo root (pixell-agent-runtime)"
}

if (-not $InstanceId -or $InstanceId -notmatch '^i-[0-9a-f]+$') {
    Fail "Instance ID required (format i-xxxxxxxxxxxxxxxxx)"
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Fail "python not found in PATH"
}
if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    Fail "aws CLI not found in PATH"
}

Write-Info "=========================================="
Write-Info "PAR Supervisor Deployment (via SSM)"
Write-Info "=========================================="
Write-Info "Target instance: $InstanceId"
Write-Info "AWS region     : $AwsRegion"
Write-Host ""

# Step 1
Write-Step "1/7" "Checking prerequisites..."
Write-Info "[OK] Prerequisites OK"

# Step 2: build wheel
Write-Step "2/7" "Building PAR wheel package..."

# Check if pip is available
python -m pip --version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warn "pip not available, attempting to install..."
    # Try to ensure pip is available
    python -m ensurepip --upgrade 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail "pip is not available and cannot be installed. Please install pip first."
    }
}

# Install build package (try user install if system install fails)
Write-Info "Installing build package..."
$buildInstall = python -m pip install --quiet --upgrade build 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Warn "System install failed, trying user install..."
    python -m pip install --user --upgrade build 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Fail "Failed to install build package. Error: $buildInstall"
    }
}

# Clean previous builds
if (Test-Path dist) { Remove-Item dist -Recurse -Force }
if (Test-Path build) { Remove-Item build -Recurse -Force }
Get-ChildItem *.egg-info -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# Build wheel
Write-Info "Running: python -m build --wheel"
$buildOutput = python -m build --wheel 2>&1
$buildExitCode = $LASTEXITCODE
if ($buildExitCode -ne 0) {
    Write-Err "Build output: $buildOutput"
    Fail "Failed to build wheel package (exit code: $buildExitCode)"
}

$wheel = Get-ChildItem dist/pixell_runtime-*.whl | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $wheel) { Fail "Wheel file not found in dist/" }
Write-Info "[OK] Built: $($wheel.Name)"

$expectedVersion = (Select-String '^version = ' pyproject.toml).Line.Split('"')[1]
if ($wheel.Name -notmatch $expectedVersion) {
    Fail "Version mismatch: pyproject=$expectedVersion wheel=$($wheel.Name)"
}

# Step 3: SSM connectivity
Write-Step "3/7" "Verifying SSM connectivity..."
$ssmStatus = aws ssm describe-instance-information `
    --region $AwsRegion `
    --filters "Key=InstanceIds,Values=$InstanceId" `
    --query "InstanceInformationList[0].PingStatus" `
    --output text 2>$null
if ($ssmStatus -ne "Online") {
    Fail "Instance not reachable via SSM (status: $ssmStatus)"
}
Write-Info "[OK] SSM connectivity OK"

# fetch instance info
$instanceJson = aws ec2 describe-instances `
    --region $AwsRegion `
    --instance-ids $InstanceId `
    --query "Reservations[0].Instances[0]" `
    --output json
$instance = $instanceJson | ConvertFrom-Json
$privateIp = $instance.PrivateIpAddress
$publicIp = if ($instance.PublicIpAddress) { $instance.PublicIpAddress } else { "N/A" }
Write-Info "Private IP: $privateIp"
Write-Info "Public  IP: $publicIp"

# Step 4: upload wheel
Write-Step "4/7" "Uploading wheel to S3..."
$s3Bucket = "pixell-agent-packages"
$s3Key = "deployments/par-supervisor/$($wheel.Name)"
$s3Url = "s3://$s3Bucket/$s3Key"
aws s3 cp $wheel.FullName $s3Url --region $AwsRegion | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Failed to upload wheel to $s3Url" }
Write-Info "[OK] Wheel uploaded to $s3Url"

# Step 5: Install via SSM
Write-Step "5/7" "Installing PAR on EC2..."
$commands = @(
    "set -e",
    "echo '[1/8] Downloading wheel from S3...'",
    "aws s3 cp '$s3Url' /tmp/'$($wheel.Name)' --region '$AwsRegion'",
    "echo '[2/8] Installing Python 3.11 if needed...'",
    "sudo yum install -y python3.11 python3.11-pip python3.11-devel 2>/dev/null || echo 'Already installed'",
    "echo '[3/8] Creating virtual environment...'",
    "VENV_DIR=/opt/pixell-agent-runtime/venv",
    "sudo mkdir -p /opt/pixell-agent-runtime",
    "if [ -d `"`$VENV_DIR`" ]; then",
    "  echo 'Removing existing virtual environment...'",
    "  sudo rm -rf `"`$VENV_DIR`"",
    "fi",
    "echo 'Creating new virtual environment...'",
    "sudo /usr/bin/python3.11 -m venv `"`$VENV_DIR`"",
    "echo '[4/8] Upgrading pip in virtual environment...'",
    "sudo `"`$VENV_DIR/bin/pip`" install --upgrade pip setuptools wheel",
    "echo '[5/8] Installing pixell-runtime wheel in virtual environment...'",
    "sudo `"`$VENV_DIR/bin/pip`" install /tmp/'$($wheel.Name)'",
    "echo '[6/8] Verifying installation...'",
    "`"`$VENV_DIR/bin/python`" -m pixell_runtime.supervisor --help > /dev/null 2>&1 && echo 'Installation verified' || echo 'Installation verification failed'",
    "echo '[7/8] Creating configuration and directories...'",
    "sudo mkdir -p /etc/pixell",
    "sudo mkdir -p /var/lib/pixell/{packages,extracted,logs,agents}",
    "sudo chmod 755 /var/lib/pixell /var/lib/pixell/packages /var/lib/pixell/extracted /var/lib/pixell/logs /var/lib/pixell/agents",
    @"
cat <<'EOFCONF' | sudo tee /etc/par-supervisor.conf > /dev/null
PORT=9000
LOG_LEVEL=info
MAX_AGENTS=20
PACKAGE_DIR=/var/lib/pixell/packages
EXTRACT_DIR=/var/lib/pixell/extracted
LOG_DIR=/var/lib/pixell/logs
AGENT_BASE_DIR=/var/lib/pixell/agents
REST_PORT_RANGE=8081-8100
A2A_PORT_RANGE=50052-50071
UI_PORT_RANGE=3001-3020
AWS_REGION=$AwsRegion
EOFCONF
"@.Trim(),
    @"
cat <<'EOFSVC' | sudo tee /etc/systemd/system/par-supervisor.service > /dev/null
[Unit]
Description=Pixell Agent Runtime Supervisor
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/var/lib/pixell
EnvironmentFile=/etc/par-supervisor.conf
# Use virtual environment Python
ExecStart=/opt/pixell-agent-runtime/venv/bin/python -m pixell_runtime.supervisor
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
ReadWritePaths=/var/lib/pixell
ReadWritePaths=/home
ReadWritePaths=/opt/pixell-agent-runtime
PrivateTmp=true
NoNewPrivileges=false
LimitNOFILE=65536
LimitNPROC=4096

[Install]
WantedBy=multi-user.target
EOFSVC
"@.Trim(),
    "echo '[8/8] Restarting supervisor service...'",
    "sudo systemctl daemon-reload",
    "sudo systemctl enable par-supervisor",
    "sudo systemctl restart par-supervisor",
    "sleep 3",
    "sudo systemctl is-active par-supervisor && echo 'Service active' || echo 'Service failed'",
    "/opt/pixell-agent-runtime/venv/bin/pip show pixell-runtime | grep Version"
)
$commandJson = @{ commands = $commands } | ConvertTo-Json -Depth 3
$tempParamFile = New-TemporaryFile
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($tempParamFile, $commandJson, $utf8NoBom)
try {
    $commandId = aws ssm send-command `
        --region $AwsRegion `
        --instance-ids $InstanceId `
        --document-name "AWS-RunShellScript" `
        --parameters file://$tempParamFile `
        --cli-binary-format raw-in-base64-out `
        --comment "Install PAR $expectedVersion" `
        --query "Command.CommandId" `
        --output text
    Write-Info "SSM Command ID: $commandId"

    aws ssm wait command-executed `
        --region $AwsRegion `
        --command-id $commandId `
        --instance-id $InstanceId | Out-Null

    $commandStatus = aws ssm get-command-invocation `
        --region $AwsRegion `
        --command-id $commandId `
        --instance-id $InstanceId `
        --query "Status" `
        --output text
    if ($commandStatus -ne "Success") {
        Write-Err "Installation failed (status $commandStatus)"
        aws ssm get-command-invocation --region $AwsRegion --command-id $commandId --instance-id $InstanceId --query 'StandardErrorContent' --output text
        exit 1
    }
}
finally {
    Remove-Item $tempParamFile -ErrorAction SilentlyContinue
}
Write-Info "[OK] PAR supervisor installed on EC2"

# Step 6: verify version
Write-Step "6/7" "Verifying installed version..."
$verifyTempFile = New-TemporaryFile
[System.IO.File]::WriteAllText($verifyTempFile, (@{ commands = @("/opt/pixell-agent-runtime/venv/bin/pip show pixell-runtime | grep Version") } | ConvertTo-Json -Depth 3), (New-Object System.Text.UTF8Encoding($false)))
$verifyId = aws ssm send-command `
    --region $AwsRegion `
    --instance-ids $InstanceId `
    --document-name "AWS-RunShellScript" `
    --parameters file://$verifyTempFile `
    --cli-binary-format raw-in-base64-out `
    --output text --query "Command.CommandId"
Start-Sleep -Seconds 3
$installedVersion = aws ssm get-command-invocation `
    --region $AwsRegion `
    --command-id $verifyId `
    --instance-id $InstanceId `
    --query "StandardOutputContent" `
    --output text | Select-String "Version:" | ForEach-Object { $_.ToString().Split()[1] }
Remove-Item $verifyTempFile -ErrorAction SilentlyContinue

if ($installedVersion -ne $expectedVersion) {
    Fail "Installed version mismatch (expected $expectedVersion got $installedVersion)"
}
Write-Info "[OK] Installed version verified: $installedVersion"

# Step 7: health check
Write-Step "7/7" "Verifying external health check..."
$healthIp = if ($publicIp -ne "N/A") { $publicIp } else { $privateIp }
$healthUrl = "http://$healthIp:9000/health"
try {
    $resp = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5
    Write-Info "[OK] Health check OK ($healthUrl)"
    $resp.Content
} catch {
    Write-Warn "Health check failed (may be expected if no public IP)"
}

Write-Host ""
Write-Info "=========================================="
Write-Info "[OK] PAR SUPERVISOR DEPLOYED SUCCESSFULLY"
Write-Info "=========================================="
Write-Info "Instance ID : $InstanceId"
Write-Info "Private IP  : $privateIp"
Write-Info "Public IP   : $publicIp"
Write-Info "Region      : $AwsRegion"
Write-Info "Supervisor  : http://$privateIp:9000/health"