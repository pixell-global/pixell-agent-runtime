# ALB Migration Guide: pixell-runtime-alb → px-pixell-runtime-alb

**Document Version:** 1.0
**Date:** December 2, 2025
**Purpose:** Complete migration guide for agents from old VPC to new VPC
**Domain:** par.pixell.global/agents/{agent_app_id}

---

## EXECUTIVE SUMMARY

After migrating AWS resources to a new VPC (`px-vpc`) to eliminate costly private NAT gateway charges, the agents hosted on `par.pixell.global/agents/{agent_app_id}` are experiencing errors because:

1. **DNS still points to old ALB** - Route53 record for `par.pixell.global` points to `pixell-runtime-alb` (old VPC)
2. **Certificate missing on new ALB** - `px-pixell-runtime-alb` only has `app.pixell.global` certificate
3. **No agent routing rules on new ALB** - Path-based routing rules for each agent are missing
4. **Target groups in wrong VPC** - Per-agent target groups exist only in old VPC

---

## INFRASTRUCTURE COMPARISON

### VPCs

| Property | Old VPC | New VPC |
|----------|---------|---------|
| **VPC ID** | `vpc-0039e5988107ae565` | `vpc-0dc5816f0b041abad` |
| **Name** | pixell-runtime-vpc | px-vpc |
| **CIDR** | 10.0.0.0/16 | 172.31.0.0/16 |
| **NAT Type** | Private NAT (expensive) | Public NAT / IGW |
| **Subnets** | 2 (us-east-2a, us-east-2b) | 3 (us-east-2a, us-east-2b, us-east-2c) |

### Application Load Balancers

| Property | Old ALB | New ALB |
|----------|---------|---------|
| **Name** | `pixell-runtime-alb` | `px-pixell-runtime-alb` |
| **DNS** | `pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com` | `px-pixell-runtime-alb-133550711.us-east-2.elb.amazonaws.com` |
| **VPC** | `vpc-0039e5988107ae565` (old) | `vpc-0dc5816f0b041abad` (new) |
| **Security Groups** | `sg-0f5b28ee64419e95d` | `sg-0869c371d1826a660`, `sg-0c0f983d66125ec24` |
| **Listeners** | HTTP:80 (redirect), HTTPS:443 | HTTP:80 (redirect), HTTPS:443 |
| **Certificate** | `par.pixell.global` | `app.pixell.global` ⚠️ **WRONG** |
| **Agent Rules** | 12+ path-based rules | **NONE** ⚠️ |

### Current DNS Records (Route53)

```
par.pixell.global     → pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com  (OLD)
agents.pixell.global  → pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com  (OLD)
app.pixell.global     → px-pixell-runtime-alb-133550711.us-east-2.elb.amazonaws.com (NEW)
```

---

## CURRENT STATE ANALYSIS

### Old ALB (pixell-runtime-alb) - HTTPS:443 Listener Rules

```
Priority | Condition                                           | Target Group
---------|-----------------------------------------------------|----------------------------------
10       | Host: app.pixell.global                             | pixell-web-tg
10066    | Path: /agents/4906eeb7-*/api/*                      | pac-agent-4906eeb7-rest
10067    | Path: /agents/4906eeb7-*/a2a/*                      | pac-agent-4906eeb7-grpc
10068    | Path: /agents/4906eeb7-*/*                          | pac-agent-4906eeb7-rest
26020    | Path: /agents/c489095f-*/api/*                      | pac-agent-c489095f-rest
26021    | Path: /agents/c489095f-*/a2a/*                      | pac-agent-c489095f-grpc
26022    | Path: /agents/c489095f-*/*                          | pac-agent-c489095f-rest
39556    | Path: /agents/ed8784f3-*/api/*                      | pac-agent-ed8784f3-rest
39557    | Path: /agents/ed8784f3-*/a2a/*                      | pac-agent-ed8784f3-grpc
39558    | Path: /agents/ed8784f3-*/*                          | pac-agent-ed8784f3-rest
default  | (no condition)                                       | par-multi-agent-tg
```

### New ALB (px-pixell-runtime-alb) - HTTPS:443 Listener Rules

```
Priority | Condition                                           | Target Group
---------|-----------------------------------------------------|----------------------------------
10       | Host: app.pixell.global                             | px-pixell-web-tg
default  | (no condition)                                       | px-par-multi-agent-tg
```

**⚠️ MISSING: All per-agent path-based routing rules!**

### Per-Agent Target Groups (All in OLD VPC!)

| Target Group Name | Port | Protocol | VPC | Health Check Path |
|-------------------|------|----------|-----|-------------------|
| pac-agent-4906eeb7-rest | 63000 | HTTP | OLD | /agents/4906eeb7-.../health |
| pac-agent-4906eeb7-grpc | 60000 | HTTP | OLD | /agents/4906eeb7-.../health |
| pac-agent-c489095f-rest | 63002 | HTTP | OLD | /agents/c489095f-.../health |
| pac-agent-c489095f-grpc | 60002 | HTTP | OLD | /agents/c489095f-.../health |
| pac-agent-ed8784f3-rest | 63001 | HTTP | OLD | /agents/ed8784f3-.../health |
| pac-agent-ed8784f3-grpc | 60001 | HTTP | OLD | /agents/ed8784f3-.../health |

### New VPC Target Groups

| Target Group Name | Port | Protocol | VPC | Target Health |
|-------------------|------|----------|-----|---------------|
| px-par-multi-agent-tg | 8080 | HTTP | NEW | 1 healthy (i-0df57d61c09d02b00:8081) |
| px-pixell-runtime-a2a-tg | 50051 | TCP | NEW | - |

### Runtime Instance Location

```
Instance: i-0df57d61c09d02b00 (pixell-agent-runtime)
  VPC: vpc-0dc5816f0b041abad (NEW - px-vpc) ✓
  Private IP: 172.31.13.141
  Public IP: 18.116.13.50
  Security Group: sg-02a98c7cec76b53fa (pixell-agent-runtime-sg)

  Open Ports:
    - 22    (SSH)
    - 3001-3020 (Web UI ports)
    - 6379  (Redis)
    - 8081-8100 (Health/API)
    - 9000  (PAR Supervisor gRPC)
    - 50051-50071 (gRPC Gateway)
    - 60000-60199 (A2A gRPC ports)
    - 63000-63199 (REST API ports)
    - 65000-65199 (UI Server ports)
```

---

## IDENTIFIED ISSUES

### Issue 1: DNS Points to Old ALB
**Severity:** CRITICAL
**Impact:** All traffic to par.pixell.global goes to old ALB which cannot route to new VPC

```
Current:  par.pixell.global → pixell-runtime-alb (OLD VPC)
Required: par.pixell.global → px-pixell-runtime-alb (NEW VPC)
```

### Issue 2: Missing SSL Certificate on New ALB
**Severity:** CRITICAL
**Impact:** HTTPS connections to par.pixell.global will fail with certificate error

```
Current Certificate on px-pixell-runtime-alb: app.pixell.global
Required Certificate: par.pixell.global (arn:aws:acm:us-east-2:636212886452:certificate/27009de7-9e7f-40af-b0f9-2222638f78a5)
```

### Issue 3: No Agent Routing Rules on New ALB
**Severity:** CRITICAL
**Impact:** Requests to /agents/{id}/* will hit default target group instead of agent-specific targets

```
Missing rules for:
  - /agents/{agent_app_id}/api/*  → REST target group
  - /agents/{agent_app_id}/a2a/*  → gRPC target group
  - /agents/{agent_app_id}/*      → REST target group (fallback)
```

### Issue 4: Target Groups in Wrong VPC
**Severity:** CRITICAL
**Impact:** Target groups in old VPC cannot reach instances in new VPC

```
All pac-agent-* target groups are in vpc-0039e5988107ae565 (OLD)
EC2 instance i-0df57d61c09d02b00 is in vpc-0dc5816f0b041abad (NEW)
```

### Issue 5: Old ALB Target Group Empty
**Severity:** HIGH
**Impact:** Even if DNS pointed to old ALB, no targets registered

```
par-multi-agent-tg (default target group): 0 registered targets
```

---

## MIGRATION STEPS

### Step 1: Add SSL Certificate to New ALB

Add the `par.pixell.global` certificate to the HTTPS listener:

```bash
# Get the listener ARN for HTTPS:443 on new ALB
LISTENER_ARN="arn:aws:elasticloadbalancing:us-east-2:636212886452:listener/app/px-pixell-runtime-alb/bc04340265e7343e/dd8644d338a2a781"

# Certificate ARN for par.pixell.global
CERT_ARN="arn:aws:acm:us-east-2:636212886452:certificate/27009de7-9e7f-40af-b0f9-2222638f78a5"

# Add certificate to listener (supports multiple certificates via SNI)
aws elbv2 add-listener-certificates \
  --listener-arn "$LISTENER_ARN" \
  --certificates CertificateArn="$CERT_ARN"
```

**Verification:**
```bash
aws elbv2 describe-listener-certificates --listener-arn "$LISTENER_ARN"
```

### Step 2: Create Target Groups in New VPC

For each deployed agent, create target groups in the new VPC:

```bash
# Variables
NEW_VPC_ID="vpc-0dc5816f0b041abad"
INSTANCE_ID="i-0df57d61c09d02b00"

# Agent: 4906eeb7-9959-414e-84c6-f2445822ebe4
AGENT_SHORT_ID="4906eeb7"
AGENT_FULL_ID="4906eeb7-9959-414e-84c6-f2445822ebe4"
REST_PORT=63000
GRPC_PORT=60000

# Create REST target group
aws elbv2 create-target-group \
  --name "px-agent-${AGENT_SHORT_ID}-rest" \
  --protocol HTTP \
  --port ${REST_PORT} \
  --vpc-id ${NEW_VPC_ID} \
  --target-type instance \
  --health-check-path "/agents/${AGENT_FULL_ID}/health" \
  --health-check-interval-seconds 30 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3

# Create gRPC target group (MUST use HTTP2 protocol version!)
aws elbv2 create-target-group \
  --name "px-agent-${AGENT_SHORT_ID}-grpc" \
  --protocol HTTP \
  --protocol-version HTTP2 \
  --port ${GRPC_PORT} \
  --vpc-id ${NEW_VPC_ID} \
  --target-type instance \
  --health-check-path "/agents/${AGENT_FULL_ID}/health" \
  --health-check-protocol HTTP \
  --health-check-interval-seconds 30 \
  --healthy-threshold-count 2 \
  --unhealthy-threshold-count 3
```

**Repeat for other agents:**
- `ed8784f3-b602-481c-8701-3b6406c8fd98` (REST: 63001, gRPC: 60001)
- `c489095f-7431-4a60-8f5c-cb11d742d983` (REST: 63002, gRPC: 60002)

### Step 3: Register EC2 Instance to Target Groups

```bash
# Get target group ARNs (after creation)
REST_TG_ARN=$(aws elbv2 describe-target-groups --names "px-agent-4906eeb7-rest" --query 'TargetGroups[0].TargetGroupArn' --output text)
GRPC_TG_ARN=$(aws elbv2 describe-target-groups --names "px-agent-4906eeb7-grpc" --query 'TargetGroups[0].TargetGroupArn' --output text)

# Register instance with REST target group
aws elbv2 register-targets \
  --target-group-arn "$REST_TG_ARN" \
  --targets Id=${INSTANCE_ID},Port=63000

# Register instance with gRPC target group
aws elbv2 register-targets \
  --target-group-arn "$GRPC_TG_ARN" \
  --targets Id=${INSTANCE_ID},Port=60000
```

### Step 4: Create ALB Listener Rules

```bash
LISTENER_ARN="arn:aws:elasticloadbalancing:us-east-2:636212886452:listener/app/px-pixell-runtime-alb/bc04340265e7343e/dd8644d338a2a781"
AGENT_FULL_ID="4906eeb7-9959-414e-84c6-f2445822ebe4"

# Rule for REST API (/agents/{id}/api/*)
aws elbv2 create-rule \
  --listener-arn "$LISTENER_ARN" \
  --priority 10066 \
  --conditions '[{"Field":"path-pattern","PathPatternConfig":{"Values":["/agents/'${AGENT_FULL_ID}'/api/*"]}}]' \
  --actions '[{"Type":"forward","TargetGroupArn":"'${REST_TG_ARN}'"}]'

# Rule for gRPC/A2A (/agents/{id}/a2a/*)
aws elbv2 create-rule \
  --listener-arn "$LISTENER_ARN" \
  --priority 10067 \
  --conditions '[{"Field":"path-pattern","PathPatternConfig":{"Values":["/agents/'${AGENT_FULL_ID}'/a2a/*"]}}]' \
  --actions '[{"Type":"forward","TargetGroupArn":"'${GRPC_TG_ARN}'"}]'

# Rule for fallback (/agents/{id}/*)
aws elbv2 create-rule \
  --listener-arn "$LISTENER_ARN" \
  --priority 10068 \
  --conditions '[{"Field":"path-pattern","PathPatternConfig":{"Values":["/agents/'${AGENT_FULL_ID}'/*"]}}]' \
  --actions '[{"Type":"forward","TargetGroupArn":"'${REST_TG_ARN}'"}]'
```

### Step 5: Update Route53 DNS

**CAUTION:** This will redirect all production traffic. Consider a blue-green approach.

```bash
# Get hosted zone ID
HOSTED_ZONE_ID="Z0366260153B1X4I8MP66"

# New ALB DNS name and hosted zone
NEW_ALB_DNS="px-pixell-runtime-alb-133550711.us-east-2.elb.amazonaws.com"
NEW_ALB_HOSTED_ZONE="Z3AADJGX6KTTL2"  # us-east-2 ALB hosted zone

# Update par.pixell.global A record
aws route53 change-resource-record-sets \
  --hosted-zone-id ${HOSTED_ZONE_ID} \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "par.pixell.global",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "'${NEW_ALB_HOSTED_ZONE}'",
          "DNSName": "dualstack.'${NEW_ALB_DNS}'",
          "EvaluateTargetHealth": true
        }
      }
    }]
  }'
```

### Step 6: Verify Security Groups

Ensure the ALB security group allows traffic to the EC2 instance:

```bash
# Check ALB security group allows outbound to instance ports
aws ec2 describe-security-groups --group-ids sg-0c0f983d66125ec24

# Check EC2 security group allows inbound from ALB
aws ec2 describe-security-groups --group-ids sg-02a98c7cec76b53fa
```

**Required Rules:**
- ALB SG (`sg-0c0f983d66125ec24`): Inbound 80, 443 from 0.0.0.0/0
- EC2 SG (`sg-02a98c7cec76b53fa`): Inbound 60000-60199, 63000-63199, 65000-65199 from ALB SG

---

## COMPLETE MIGRATION SCRIPT

```bash
#!/bin/bash
set -e

# Configuration
NEW_VPC_ID="vpc-0dc5816f0b041abad"
INSTANCE_ID="i-0df57d61c09d02b00"
LISTENER_ARN="arn:aws:elasticloadbalancing:us-east-2:636212886452:listener/app/px-pixell-runtime-alb/bc04340265e7343e/dd8644d338a2a781"
PAR_CERT_ARN="arn:aws:acm:us-east-2:636212886452:certificate/27009de7-9e7f-40af-b0f9-2222638f78a5"
HOSTED_ZONE_ID="Z0366260153B1X4I8MP66"
NEW_ALB_DNS="px-pixell-runtime-alb-133550711.us-east-2.elb.amazonaws.com"
ALB_HOSTED_ZONE="Z3AADJGX6KTTL2"

# Agents to migrate (short_id:full_id:rest_port:grpc_port)
AGENTS=(
  "4906eeb7:4906eeb7-9959-414e-84c6-f2445822ebe4:63000:60000"
  "ed8784f3:ed8784f3-b602-481c-8701-3b6406c8fd98:63001:60001"
  "c489095f:c489095f-7431-4a60-8f5c-cb11d742d983:63002:60002"
)

echo "=== Step 1: Add SSL Certificate ==="
aws elbv2 add-listener-certificates \
  --listener-arn "$LISTENER_ARN" \
  --certificates CertificateArn="$PAR_CERT_ARN"
echo "Certificate added"

echo "=== Step 2 & 3: Create Target Groups and Register Targets ==="
PRIORITY=10066

for agent in "${AGENTS[@]}"; do
  IFS=':' read -r SHORT_ID FULL_ID REST_PORT GRPC_PORT <<< "$agent"
  echo "Processing agent: $FULL_ID"

  # Create REST target group
  REST_TG_ARN=$(aws elbv2 create-target-group \
    --name "px-agent-${SHORT_ID}-rest" \
    --protocol HTTP \
    --port ${REST_PORT} \
    --vpc-id ${NEW_VPC_ID} \
    --target-type instance \
    --health-check-path "/agents/${FULL_ID}/health" \
    --health-check-interval-seconds 30 \
    --query 'TargetGroups[0].TargetGroupArn' \
    --output text 2>/dev/null || \
    aws elbv2 describe-target-groups --names "px-agent-${SHORT_ID}-rest" \
    --query 'TargetGroups[0].TargetGroupArn' --output text)

  # Create gRPC target group
  GRPC_TG_ARN=$(aws elbv2 create-target-group \
    --name "px-agent-${SHORT_ID}-grpc" \
    --protocol HTTP \
    --protocol-version HTTP2 \
    --port ${GRPC_PORT} \
    --vpc-id ${NEW_VPC_ID} \
    --target-type instance \
    --health-check-path "/agents/${FULL_ID}/health" \
    --health-check-protocol HTTP \
    --health-check-interval-seconds 30 \
    --query 'TargetGroups[0].TargetGroupArn' \
    --output text 2>/dev/null || \
    aws elbv2 describe-target-groups --names "px-agent-${SHORT_ID}-grpc" \
    --query 'TargetGroups[0].TargetGroupArn' --output text)

  # Register targets
  aws elbv2 register-targets --target-group-arn "$REST_TG_ARN" \
    --targets Id=${INSTANCE_ID},Port=${REST_PORT} 2>/dev/null || true
  aws elbv2 register-targets --target-group-arn "$GRPC_TG_ARN" \
    --targets Id=${INSTANCE_ID},Port=${GRPC_PORT} 2>/dev/null || true

  echo "  Target groups created and targets registered"

  # Create listener rules
  aws elbv2 create-rule --listener-arn "$LISTENER_ARN" --priority $PRIORITY \
    --conditions "[{\"Field\":\"path-pattern\",\"PathPatternConfig\":{\"Values\":[\"/agents/${FULL_ID}/api/*\"]}}]" \
    --actions "[{\"Type\":\"forward\",\"TargetGroupArn\":\"${REST_TG_ARN}\"}]" 2>/dev/null || true

  aws elbv2 create-rule --listener-arn "$LISTENER_ARN" --priority $((PRIORITY+1)) \
    --conditions "[{\"Field\":\"path-pattern\",\"PathPatternConfig\":{\"Values\":[\"/agents/${FULL_ID}/a2a/*\"]}}]" \
    --actions "[{\"Type\":\"forward\",\"TargetGroupArn\":\"${GRPC_TG_ARN}\"}]" 2>/dev/null || true

  aws elbv2 create-rule --listener-arn "$LISTENER_ARN" --priority $((PRIORITY+2)) \
    --conditions "[{\"Field\":\"path-pattern\",\"PathPatternConfig\":{\"Values\":[\"/agents/${FULL_ID}/*\"]}}]" \
    --actions "[{\"Type\":\"forward\",\"TargetGroupArn\":\"${REST_TG_ARN}\"}]" 2>/dev/null || true

  echo "  Listener rules created"

  PRIORITY=$((PRIORITY + 10000))
done

echo "=== Step 4: Update DNS (MANUAL - REVIEW FIRST) ==="
echo "Run the following command to update DNS:"
echo ""
echo "aws route53 change-resource-record-sets \\"
echo "  --hosted-zone-id ${HOSTED_ZONE_ID} \\"
echo "  --change-batch '{\"Changes\":[{\"Action\":\"UPSERT\",\"ResourceRecordSet\":{\"Name\":\"par.pixell.global\",\"Type\":\"A\",\"AliasTarget\":{\"HostedZoneId\":\"${ALB_HOSTED_ZONE}\",\"DNSName\":\"dualstack.${NEW_ALB_DNS}\",\"EvaluateTargetHealth\":true}}}]}'"

echo ""
echo "=== Migration Complete (DNS update pending) ==="
```

---

## VERIFICATION CHECKLIST

After migration, verify each component:

### 1. Certificate Verification
```bash
aws elbv2 describe-listener-certificates \
  --listener-arn "arn:aws:elasticloadbalancing:us-east-2:636212886452:listener/app/px-pixell-runtime-alb/bc04340265e7343e/dd8644d338a2a781"
# Should show both app.pixell.global and par.pixell.global certificates
```

### 2. Target Group Health
```bash
# Check each agent target group
aws elbv2 describe-target-health --target-group-arn "arn:aws:elasticloadbalancing:us-east-2:636212886452:targetgroup/px-agent-4906eeb7-rest/xxx"
# Should show healthy status
```

### 3. Listener Rules
```bash
aws elbv2 describe-rules \
  --listener-arn "arn:aws:elasticloadbalancing:us-east-2:636212886452:listener/app/px-pixell-runtime-alb/bc04340265e7343e/dd8644d338a2a781" \
  | jq '.Rules[] | {Priority: .Priority, Conditions: .Conditions[0].Values}'
# Should show all agent path patterns
```

### 4. DNS Propagation
```bash
dig par.pixell.global +short
# Should return: px-pixell-runtime-alb-133550711.us-east-2.elb.amazonaws.com
```

### 5. End-to-End Test
```bash
# Test agent health endpoint
curl -v https://par.pixell.global/agents/4906eeb7-9959-414e-84c6-f2445822ebe4/health

# Test REST API
curl -v https://par.pixell.global/agents/4906eeb7-9959-414e-84c6-f2445822ebe4/api/status
```

---

## ROLLBACK PLAN

If issues occur, rollback DNS to old ALB:

```bash
aws route53 change-resource-record-sets \
  --hosted-zone-id Z0366260153B1X4I8MP66 \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "par.pixell.global",
        "Type": "A",
        "AliasTarget": {
          "HostedZoneId": "Z3AADJGX6KTTL2",
          "DNSName": "dualstack.pixell-runtime-alb-420577088.us-east-2.elb.amazonaws.com",
          "EvaluateTargetHealth": true
        }
      }
    }]
  }'
```

**Note:** Rollback requires old ALB and target groups to be functional, which they currently are NOT (no targets registered in old VPC target groups).

---

## ARCHITECTURE DIAGRAM

```
                                    Route53
                                       │
                    ┌──────────────────┴──────────────────┐
                    │                                      │
                    ▼                                      ▼
        par.pixell.global                      app.pixell.global
        agents.pixell.global                           │
                    │                                      │
        ┌───────────┴───────────┐                          │
        │  CURRENTLY WRONG!     │                          │
        │  Points to OLD ALB    │                          │
        └───────────┬───────────┘                          │
                    │                                      │
    ┌───────────────┼──────────────────────────────────────┼───────────────────┐
    │               ▼                                      ▼                   │
    │   ┌─────────────────────────┐         ┌─────────────────────────┐       │
    │   │  pixell-runtime-alb     │         │  px-pixell-runtime-alb  │       │
    │   │  (OLD - vpc-0039...)    │         │  (NEW - vpc-0dc5...)    │       │
    │   │                         │         │                         │       │
    │   │  Cert: par.pixell.global│         │  Cert: app.pixell.global│       │
    │   │  Rules: 12+ agent rules │         │  Rules: 2 (default only)│       │
    │   │  Targets: EMPTY ⚠️       │         │  Targets: 1 healthy     │       │
    │   └────────────┬────────────┘         └────────────┬────────────┘       │
    │                │                                    │                    │
    │                ▼                                    ▼                    │
    │   ┌─────────────────────────┐         ┌─────────────────────────┐       │
    │   │  Target Groups (OLD)    │         │  Target Groups (NEW)    │       │
    │   │  - pac-agent-*-rest     │         │  - px-par-multi-agent-tg│       │
    │   │  - pac-agent-*-grpc     │         │  - (agents TG MISSING!) │       │
    │   │  VPC: OLD               │         │  VPC: NEW               │       │
    │   └────────────┬────────────┘         └────────────┬────────────┘       │
    │                │                                    │                    │
    │                ✗ NO TARGETS                         │                    │
    │                                                     ▼                    │
    │                                    ┌───────────────────────────────┐     │
    │                                    │  EC2: i-0df57d61c09d02b00    │     │
    │                                    │  (pixell-agent-runtime)      │     │
    │                                    │  VPC: NEW (vpc-0dc5...)      │     │
    │                                    │  IP: 172.31.13.141           │     │
    │                                    │                               │     │
    │                                    │  Running Agents:              │     │
    │                                    │  - 4906eeb7 (63000/60000)    │     │
    │                                    │  - ed8784f3 (63001/60001)    │     │
    │                                    │  - c489095f (63002/60002)    │     │
    │                                    └───────────────────────────────┘     │
    │                                                                          │
    │                              px-vpc (vpc-0dc5816f0b041abad)              │
    └──────────────────────────────────────────────────────────────────────────┘
```

---

## FUTURE RECOMMENDATIONS

1. **Automate Target Group Creation**: Update PAC deployment worker to create target groups in new VPC
2. **Update ec2-multi-agent.ts**: Change VPC ID constant to new VPC
3. **Clean Up Old Resources**: After verification, delete unused old VPC resources
4. **Add Monitoring**: CloudWatch alarms for target group health
5. **Document in Terraform**: Consider IaC for ALB rules management

---

**Document Status:** Complete
**Last Updated:** December 2, 2025
**Author:** Claude Code (Infrastructure Analysis)
