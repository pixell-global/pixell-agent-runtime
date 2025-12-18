# EC2 Infrastructure State - December 2025

**Document Version:** 1.0
**Date:** December 2, 2025
**Purpose:** Document current EC2 instance configuration and PAR deployment state

---

## EC2 INSTANCE DETAILS

| Property | Value |
|----------|-------|
| **Instance ID** | `i-0df57d61c09d02b00` |
| **Name** | `pixell-agent-runtime` |
| **VPC** | `vpc-0dc5816f0b041abad` (px-vpc - NEW) |
| **Private IP** | `172.31.13.141` |
| **Public IP** | `18.116.13.50` |
| **OS** | Amazon Linux 2023 |
| **Package Manager** | `yum` |

---

## PAR SUPERVISOR CONFIGURATION

### Installation Location
```
/opt/pixell-agent-runtime/
└── venv/                    # Python virtual environment
    └── bin/python           # Supervisor Python
```

### Systemd Service
**File:** `/etc/systemd/system/par-supervisor.service`

```ini
[Unit]
Description=Pixell Agent Runtime Supervisor
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/var/lib/pixell
EnvironmentFile=/etc/par-supervisor.conf
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
```

### Environment Configuration
**File:** `/etc/par-supervisor.conf`

```bash
PORT=9000
LOG_LEVEL=info
MAX_AGENTS=20
PACKAGE_DIR=/var/lib/pixell/packages
EXTRACT_DIR=/var/lib/pixell/extracted
LOG_DIR=/var/lib/pixell/logs
AGENT_BASE_DIR=/var/lib/pixell/agents
REST_PORT_RANGE=8081-8100      # Fallback (PAC provides ports)
A2A_PORT_RANGE=50052-50071     # Fallback (PAC provides ports)
UI_PORT_RANGE=3001-3020        # Fallback (PAC provides ports)
AWS_REGION=us-east-2
```

---

## DIRECTORY STRUCTURE

### System Directories
```
/var/lib/pixell/
├── agents/              # Agent data directory (mostly empty)
├── extracted/           # Extracted .apkg packages
│   ├── {hash1}/         # Package contents by SHA hash
│   ├── {hash2}/
│   └── ...
├── logs/                # Agent and supervisor logs
│   ├── supervisor.log
│   ├── agent_{agent_app_id}.log
│   └── ...
├── packages/            # Downloaded .apkg files
│   └── {filename}.apkg
└── pip-cache/           # Pip cache directory
```

### Agent Home Directories
```
/home/
├── agent_{org_short_id}_{agent_short_id}/
│   ├── venvs/           # Agent virtual environments
│   │   └── {agent_app_id}_{version_hash}/
│   │       └── bin/python
│   ├── packages/        # Agent-specific packages
│   └── ...
├── ec2-user/
├── ssm-user/
└── ...
```

---

## RUNNING SERVICES

### PAR Supervisor
| Property | Value |
|----------|-------|
| **Port** | 9000 (HTTP API) |
| **gRPC Gateway** | 50051 |
| **PID** | Dynamic |
| **User** | root |
| **Command** | `/opt/pixell-agent-runtime/venv/bin/python -m pixell_runtime.supervisor` |

### Current Listening Ports
```
Port    Service         Protocol
----    -------         --------
22      SSH             TCP
6379    Redis           TCP
9000    PAR Supervisor  HTTP
50051   gRPC Gateway    HTTP2
60000   Agent A2A       gRPC
63000   Agent REST      HTTP
65000   Agent UI        HTTP (if enabled)
```

---

## AGENT USER NAMING CONVENTION

### Pattern
```
agent_{org_short_id}_{agent_short_id}
```

### Examples
| Agent App ID | Org Short ID | Agent Short ID | Linux User |
|--------------|--------------|----------------|------------|
| `ed8784f3-b602-481c-8701-3b6406c8fd98` | - | `ed8784f3`, `b602` | `agent_ed8784f3_b602` |
| `3d0e7e50-fd36-4664-ba9d-42b1ce602c50` | - | `3d0e7e50`, `fd36` | `agent_3d0e7e50_fd36` |
| `4906eeb7-9959-414e-84c6-f2445822ebe4` | `8c82966883524dad` | `4906eeb7` | `agent_8c82966883524dad_4906eeb7` |

### Character Limits
- Max username length: 31 characters (Linux limit)
- Org short ID: 16 characters
- Agent short ID: 8 characters
- Prefix `agent_`: 6 characters
- Underscores: 2 characters
- Total: 6 + 16 + 1 + 8 = 31 characters (exact limit)

---

## AGENT ENVIRONMENT VARIABLES

When PAR spawns an agent, these environment variables are set:

### Core Variables
| Variable | Example | Description |
|----------|---------|-------------|
| `AGENT_APP_ID` | `ed8784f3-b602-481c-8701-3b6406c8fd98` | Full agent UUID |
| `AGENT_PACKAGE_PATH` | `/var/lib/pixell/extracted/{hash}` | Extracted package location |
| `BASE_PATH` | `/agents/ed8784f3-b602-481c-8701-3b6406c8fd98` | URL path prefix |
| `MULTIPLEXED` | `true` | UI multiplexed with REST |

### Port Variables (Current - Port Mode)
| Variable | Example | Description |
|----------|---------|-------------|
| `REST_PORT` | `63000` | REST API port |
| `A2A_PORT` | `60000` | gRPC A2A port |
| `UI_PORT` | `65000` | UI server port |

### Socket Variables (Future - Socket Mode)
| Variable | Example | Description |
|----------|---------|-------------|
| `SOCKET_MODE` | `true` | Enable socket binding |
| `REST_SOCKET` | `/var/run/pixell-agents/agent_ed8784f3/rest.sock` | REST socket path |
| `A2A_SOCKET` | `/var/run/pixell-agents/agent_ed8784f3/a2a.sock` | gRPC socket path |
| `UI_SOCKET` | `/var/run/pixell-agents/agent_ed8784f3/ui.sock` | UI socket path |

### Python Path
| Variable | Example |
|----------|---------|
| `PYTHONPATH` | `/var/lib/pixell/extracted/{hash}:/opt/pixell-agent-runtime/venv/lib/python3.11/site-packages` |
| `AGENT_VENV_PATH` | `/home/agent_xxx/venvs/{agent_app_id}_{hash}` |
| `PATH` | `/home/agent_xxx/.local/bin:/home/agent_xxx/bin:/usr/local/bin:/usr/bin:...` |

---

## SECURITY GROUPS

### EC2 Instance Security Group
**ID:** `sg-02a98c7cec76b53fa` (pixell-agent-runtime-sg)

**Current Inbound Rules:**
| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 22 | TCP | VPC CIDR | SSH |
| 3001-3020 | TCP | ALB SG | Web UI ports |
| 6379 | TCP | VPC CIDR | Redis |
| 8081-8100 | TCP | ALB SG | Health/API |
| 9000 | TCP | VPC CIDR | PAR Supervisor |
| 50051-50071 | TCP | ALB SG | gRPC Gateway |
| 60000-60199 | TCP | ALB SG | A2A gRPC ports |
| 63000-63199 | TCP | ALB SG | REST API ports |
| 65000-65199 | TCP | ALB SG | UI Server ports |

**Required for Socket Mode (Add):**
| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 8080 | TCP | ALB SG | Nginx REST proxy |
| 50051 | TCP | ALB SG | Nginx gRPC proxy |
| 3000 | TCP | ALB SG | Nginx UI proxy |

---

## SOFTWARE VERSIONS

| Software | Version | Location |
|----------|---------|----------|
| Python | 3.11 | `/usr/bin/python3.11` |
| pixell-runtime | 0.2.1 | `/opt/pixell-agent-runtime/venv/` |
| Redis | Latest | System package |
| Nginx | Not installed | (Required for socket mode) |

---

## WHAT NEEDS TO BE CREATED FOR SOCKET MODE

### Directories
```bash
sudo mkdir -p /var/run/pixell-agents
sudo chmod 755 /var/run/pixell-agents
```

### Software
```bash
sudo yum install -y nginx
sudo systemctl enable nginx
```

### Nginx User/Group
```bash
# Ensure nginx group exists (created by yum install)
# Agent users need to be in nginx group for socket access
sudo usermod -aG nginx agent_xxx
```

### Socket Directory Structure
```
/var/run/pixell-agents/
├── agent_{short_id}/              # 750 agent:nginx
│   ├── rest.sock                  # 660 agent:nginx
│   ├── a2a.sock                   # 660 agent:nginx
│   └── ui.sock                    # 660 agent:nginx
└── ...
```

---

## IMPORTANT NOTES

1. **VPC Migration Complete**: EC2 instance is already in `px-vpc` (new VPC)
2. **DNS Still Points to Old ALB**: Route53 needs update
3. **No Nginx Installed**: Must install before socket mode
4. **Port Allocation**: PAC provides ports, PAR uses them (not config file ranges)
5. **Agent Isolation**: Each agent runs as dedicated Linux user
6. **Root Supervisor**: PAR supervisor runs as root to manage users and processes
