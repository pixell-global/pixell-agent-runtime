Param(
    [Parameter(Mandatory=$true)]
    [string]$InstanceId,

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
$env:PIP_BREAK_SYSTEM_PACKAGES = "1"   # 억지로라도 pip install 가능하게
# PowerShell 5에서는 try/catch를 한 줄로 작성하면 파싱 오류가 발생하므로 명시적으로 개행을 사용
try
{
    python -m pip install --quiet build | Out-Null
}
catch
{
    Write-Warn ("python -m pip install build failed: {0}" -f $_)
    Fail "Install python build module first"
}
if (Test-Path dist) { Remove-Item dist -Recurse -Force }
if (Test-Path build) { Remove-Item build -Recurse -Force }
Get-ChildItem *.egg-info | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Info "Running: python -m build --wheel"
$buildResult = python -m build --wheel
if ($LASTEXITCODE -ne 0) {
    Fail "Failed to build wheel package"
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
    "echo '[1/6] Downloading wheel from S3...'",
    "aws s3 cp '$s3Url' /tmp/'$($wheel.Name)' --region '$AwsRegion'",
    "echo '[2/6] Installing Python 3.11 if needed...'",
    "sudo yum install -y python3.11 python3.11-pip python3.11-devel 2>/dev/null || echo 'Already installed'",
    "echo '[3/6] Uninstalling old version...'",
    "sudo pip3.11 uninstall -y pixell-runtime 2>/dev/null || echo 'No old version'",
    "echo '[4/6] Installing new wheel...'",
    "sudo pip3.11 install /tmp/'$($wheel.Name)'",
    "echo '[5/6] Creating configuration and directories...'",
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
ExecStart=/usr/bin/python3.11 -m pixell_runtime.supervisor
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
ReadWritePaths=/var/lib/pixell
ReadWritePaths=/home
PrivateTmp=true
NoNewPrivileges=false
LimitNOFILE=65536
LimitNPROC=4096

[Install]
WantedBy=multi-user.target
EOFSVC
"@.Trim(),
    "echo '[6/6] Restarting supervisor service...'",
    "sudo systemctl daemon-reload",
    "sudo systemctl enable par-supervisor",
    "sudo systemctl restart par-supervisor",
    "sleep 3",
    "sudo systemctl is-active par-supervisor && echo 'Service active' || echo 'Service failed'",
    "pip3.11 show pixell-runtime | grep Version"
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
[System.IO.File]::WriteAllText($verifyTempFile, (@{ commands = @("pip3.11 show pixell-runtime | grep Version") } | ConvertTo-Json -Depth 3), (New-Object System.Text.UTF8Encoding($false)))
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