# Actual Hosting Costs for Pixell Agent Runtime

**Generated**: October 5, 2025  
**Region**: us-east-2 (Ohio)  
**Based on**: Live AWS resource inspection

---

## Executive Summary

### Cost per Agent App (After Aurora Deletion)

**Current Multi-Agent Architecture** (agents share one container):
- **Fixed infrastructure cost**: $222/month (regardless of agent count)
- **Marginal cost per additional agent**: **$0/month** (up to 20 agents max)
- **Average cost per agent** (with 10 agents): **$22.20/month**
- **Average cost per agent** (with 20 agents): **$11.10/month**

**Per-Agent Task Architecture** (one container per agent):
- **Fixed infrastructure cost**: $145/month (shared components)
- **Direct cost per agent**: **$9.52/month** (compute + logs)
- **Total cost per agent** (at 10 agents): **$24.02/month**
- **Total cost per agent** (at 50 agents): **$12.42/month**

### Key Insight
With your multi-agent runtime, **adding agents costs nothing** until you hit the 20-agent limit. The entire infrastructure runs for $222/month whether you host 1 agent or 20 agents.

---

## Actual Deployed Resources

### 1. ECS Fargate - Runtime Task
**Service**: `pixell-runtime-multi-agent`  
**Task Definition**: `pixell-runtime-multi-agent:70`

**Configuration**:
- **CPU**: 2048 units (2 vCPU)
- **Memory**: 4096 MB (4 GB)
- **Desired Count**: 1 task
- **Launch Type**: FARGATE

**Monthly Cost**:
```
vCPU:   2 × $0.04048 × 730 hours = $59.10/month
Memory: 4 GB × $0.004445 × 730 hours = $12.98/month
Total Fargate: $72.08/month
```

**Current Usage**: Hosting multiple agents in one task (MAX_AGENTS=20)

---

### 2. RDS Database Instance

#### MySQL Instance (db.t3.micro)
**Instance**: `database-3` (MySQL)

**Configuration**:
- **Type**: db.t3.micro (2 vCPU, 1 GB RAM)
- **Engine**: MySQL 8.0
- **Storage**: 20 GB GP2

**Monthly Cost**:
```
Instance hours: $0.017 × 730 hours = $12.41/month
Storage: 20GB × $0.115 = $2.30/month
Total MySQL: $14.71/month
```

**Note**: Aurora PostgreSQL cluster deleted to save $427/month (66% cost reduction).

---

### 3. Load Balancers

#### Application Load Balancer (ALB)
**Name**: `pixell-runtime-alb`  
**Purpose**: Routes REST/UI traffic to agents

**Monthly Cost**:
```
Fixed: $16.20/month
LCU hours: Varies by traffic
  - Estimate 2 LCUs average × $0.008 × 730 = $11.68/month
Total ALB: $27.88/month
```

#### Network Load Balancer (NLB)
**Name**: `pixell-runtime-nlb`  
**Purpose**: Routes gRPC A2A traffic

**Monthly Cost**:
```
Fixed: $16.20/month
NLCU hours: Varies by traffic
  - Estimate 1 NLCU average × $0.006 × 730 = $4.38/month
Total NLB: $20.58/month
```

**Total Load Balancer Cost**: **$48.46/month**

---

### 4. NAT Gateways (High Availability)

**Count**: 2 NAT Gateways (for multi-AZ redundancy)

**Monthly Cost**:
```
Fixed: 2 × $32.40 = $64.80/month
Data processing: $0.045/GB
  - Estimate 50 GB/month = $2.25/month
Total NAT: $67.05/month
```

---

### 5. S3 Storage

**Buckets**:
- `pixell-agent-packages` (APKG files)
- `pixell-internal-registry-636212886452` (internal packages)
- `pixell-external-registry-636212886452` (external packages)
- `pixell-runtime-alb-logs-636212886452` (ALB logs)

**Estimated Monthly Cost**:
```
Storage: ~20 GB × $0.023/GB = $0.46/month
Requests: ~10,000 requests × $0.0004/1000 = $0.004/month
Data transfer: First 100GB free
Total S3: ~$0.50/month
```

---

### 6. AWS Cloud Map (Service Discovery)

**Namespace**: `pixell-runtime.local` (ns-ipmcpi2q5twajhzm)  
**Service**: `agents`  
**Type**: DNS_PRIVATE

**Monthly Cost**:
```
Hosted zone: $1.00/month
Queries: $0.0000001 per query (negligible)
Total Cloud Map: $1.00/month
```

---

### 7. CloudWatch Logs

**Log Groups**:
- `/ecs/pixell-runtime-multi-agent`
- `/ecs/pixell-agent-runtime`
- ALB access logs

**Estimated Monthly Cost**:
```
Ingestion: 5 GB × $0.50 = $2.50/month
Storage: 10 GB × $0.03 = $0.30/month (7-day retention)
Total CloudWatch: $2.80/month
```

---

### 8. ECR (Container Registry)

**Repository**: `pixell-runtime`

**Estimated Monthly Cost**:
```
Storage: 2 GB × $0.10 = $0.20/month
Data transfer: Included in Fargate pricing
Total ECR: $0.20/month
```

---

### 9. VPC Resources

**Resources**:
- VPC, Subnets, Route Tables, Internet Gateway
- Security Groups
- Elastic IPs (for NAT Gateways)

**Monthly Cost**:
```
VPC/Subnets/Route Tables: Free
Security Groups: Free
Elastic IPs (attached to NAT): Free
Total VPC: $0.00/month
```

---

## Total Monthly Cost Breakdown

### Current Multi-Agent Architecture (After Aurora Deletion)

| Component | Monthly Cost |
|-----------|-------------|
| **ECS Fargate (1 task, 2 vCPU/4GB)** | $72.08 |
| **RDS MySQL (db.t3.micro)** | $14.71 |
| **Application Load Balancer** | $27.88 |
| **Network Load Balancer** | $20.58 |
| **NAT Gateways (2×)** | $67.05 |
| **S3 Storage** | $0.50 |
| **Cloud Map** | $1.00 |
| **CloudWatch Logs** | $2.80 |
| **ECR** | $0.20 |
| **VPC Resources** | $0.00 |
| **TOTAL** | **$206.80/month** |

### Unit Economics: Cost per Agent

#### Understanding the Cost Model

Your multi-agent runtime has **zero marginal cost** for additional agents:

```
Fixed Infrastructure Cost: $206.80/month
─────────────────────────────────────────
Number of Agents    Cost per Agent
─────────────────────────────────────────
1 agent             $206.80/agent
5 agents            $41.36/agent
10 agents           $20.68/agent
15 agents           $13.79/agent
20 agents (max)     $10.34/agent
```

**Key Takeaway**: The same infrastructure that costs $206.80/month can host 1 agent or 20 agents. Adding agents is FREE until you hit the container limit (MAX_AGENTS=20).

---

## Per-Agent ECS Task Architecture (Proposed)

If you migrate to **one ECS task per agent** (as designed in the codebase):

### Per-Agent Direct Costs

**Task Configuration** (from `ecs-task-definition-template.json`):
- **CPU**: 256 units (0.25 vCPU)
- **Memory**: 512 MB

**Monthly Cost per Agent**:
```
vCPU:   0.25 × $0.04048 × 730 = $7.39/month
Memory: 0.5 GB × $0.004445 × 730 = $1.62/month
CloudWatch Logs: ~$0.50/month (1 GB/month)
S3 (APKG): ~$0.01/month
Cloud Map instance: ~$0.001/month
Total Direct Cost per Agent: $9.52/month
```

### Shared Infrastructure (Amortized)

| Component | Monthly Cost | Cost/Agent (10 agents) | Cost/Agent (50 agents) |
|-----------|-------------|----------------------|----------------------|
| **ALB** | $27.88 | $2.79 | $0.56 |
| **NLB** | $20.58 | $2.06 | $0.41 |
| **NAT Gateways** | $67.05 | $6.71 | $1.34 |
| **RDS MySQL** | $14.71 | $1.47 | $0.29 |
| **Cloud Map Namespace** | $1.00 | $0.10 | $0.02 |
| **ECR** | $0.20 | $0.02 | $0.004 |
| **Subtotal Shared** | $131.42 | $13.14 | $2.63 |

### Total Cost per Agent at Scale

| Number of Agents | Direct Cost | Shared Cost | **Total/Agent** |
|-----------------|-------------|-------------|----------------|
| **1** | $9.52 | $131.42 | **$140.94** |
| **10** | $9.52 | $13.14 | **$22.66** |
| **20** | $9.52 | $6.57 | **$16.09** |
| **50** | $9.52 | $2.63 | **$12.15** |
| **100** | $9.52 | $1.31 | **$10.83** |

---

## Key Insights

### 1. Multi-Agent Runtime = Zero Marginal Cost
**Critical insight**: Adding agents to your current runtime costs **$0/month** (up to 20 agents).

The entire $206.80/month infrastructure is **fixed cost** regardless of agent count:
- 1 agent = $206.80/agent
- 10 agents = $20.68/agent  
- 20 agents = $10.34/agent

**Implication**: Fill your runtime capacity before considering per-agent tasks.

### 2. Current Multi-Agent Runtime is Cost-Efficient
Your current architecture ($72/month for Fargate) efficiently shares one container across multiple agents.

**When to keep multi-agent runtime**:
- Low traffic per agent
- Agents don't have resource spikes
- Acceptable shared fate (if runtime crashes, all agents down)

**When to migrate to per-agent tasks**:
- Need isolation between agents
- Different resource requirements per agent
- Compliance/security requires isolation
- Serving high-traffic agents

### 3. NAT Gateway is Expensive ($67/month)
Consider **VPC Endpoints** for AWS services:
- S3 VPC Endpoint (free, eliminates NAT for S3 traffic)
- ECR VPC Endpoint ($7.20/month, but saves NAT costs)

### 4. Load Balancers Scale Well
At $48/month combined, the cost is reasonable and doesn't increase per agent.

---

## Cost Optimization Recommendations

### Immediate Savings (High Impact)

1. **✅ COMPLETED: Aurora Cluster Deleted**
   - **Saved: $427/month (66% reduction)**
   - Previous cost: $649/month → New cost: $207/month

2. **Add S3 VPC Endpoint** (free)
   - Reduces NAT Gateway data processing
   - **Potential savings: ~$10-20/month in NAT costs**

3. **Reduce NAT Gateway redundancy** (for non-production)
   - Current: 2 NAT Gateways = $65/month
   - Use 1 NAT Gateway = $32/month
   - **Savings: $32/month** (trade-off: no AZ redundancy)

4. **Fill current runtime capacity**
   - You can host up to 20 agents at $207/month
   - Each additional agent = **$0 marginal cost**
   - **Best ROI optimization available**

### Long-term Optimizations

1. **Fargate Savings Plans**
   - 1-year commitment: 17% discount
   - 3-year commitment: 32% discount
   - **Savings: $12-23/month on Fargate**

2. **CloudWatch Log Retention**
   - Current: 7 days (good!)
   - Consider exporting to S3 for long-term storage
   - **Savings: Minimal but best practice**

3. **Reserved NAT Gateway** (not available, but alternatives):
   - Use VPC endpoints where possible
   - Consider single NAT for non-prod
   - **Savings: $32/month for single NAT**

---

## Scenarios and Recommendations

### Scenario 1: Hosting 1-20 Agents (Current Capacity)

**Cost**: $206.80/month (fixed, regardless of agent count)

```
Agent Count    Total Cost    Cost per Agent
───────────────────────────────────────────
1 agent        $207/month    $207.00/agent
5 agents       $207/month    $41.36/agent
10 agents      $207/month    $20.68/agent
20 agents      $207/month    $10.34/agent
```

**Recommendation**: **Keep multi-agent runtime**. Zero marginal cost per agent.

**Action**: Deploy as many agents as possible (up to 20) to maximize cost efficiency.

### Scenario 2: Scaling Beyond 20 Agents

When you exceed 20 agents, you need to choose:

**Option A: Add another multi-agent runtime**
- Cost: 2× $207 = $414/month for 40 agents
- Per agent: $10.35/agent
- Pros: Simple, proven architecture
- Cons: Still limited to 20 agents per runtime

**Option B: Migrate to per-agent tasks**
- Cost at 50 agents: $607/month = $12.15/agent
- Per agent cost: $9.52 (direct) + $2.63 (shared)
- Pros: Unlimited scale, per-agent isolation
- Cons: More complex operations

**Recommendation**: 
- **21-40 agents**: Add 2nd multi-agent runtime ($10.35/agent)
- **50+ agents**: Migrate to per-agent task architecture ($12.15/agent)

### Scenario 3: Further Cost Optimization

If you need even lower costs, consider:

**Single NAT Gateway** (acceptable risk for many workloads)
- Saves: $32/month
- New total: $175/month for 20 agents = **$8.75/agent**

**With S3 VPC Endpoint** (free)
- Saves: ~$15/month in NAT data processing
- New total: $160/month for 20 agents = **$8.00/agent**

**Fully Optimized Cost**:
- 20 agents at $160/month = **$8.00 per agent per month**

---

## Summary Table

| Scenario | Agents | Architecture | Monthly Cost | Cost/Agent | Recommended |
|----------|--------|--------------|--------------|-----------|------------|
| **Current (After Aurora Deletion)** | 1-20 | Multi-agent | $207 | $10-207 | ✅ Best for ≤20 agents |
| **Optimized (S3 VPC + 1 NAT)** | 20 | Multi-agent | $160 | $8 | ✅ Maximum efficiency |
| **Dual Runtime** | 40 | Multi-agent × 2 | $414 | $10.35 | ✅ Good for 21-40 agents |
| **Per-Agent Tasks** | 50 | Per-agent | $607 | $12.15 | ✅ Best for 50+ agents |
| **Per-Agent Tasks** | 100 | Per-agent | $1,083 | $10.83 | ✅ Best for 100+ agents |

**Key Insight**: Your current multi-agent runtime is **incredibly cost-efficient** at $8-10/agent when running at capacity.

---

## Action Items

### Priority 1: Maximize Current Runtime Efficiency ✅
- [x] Delete Aurora cluster → **Saved $427/month**
- [ ] Deploy more agents to current runtime (0-20 agents supported)
- [ ] Monitor agent performance and resource usage
- [ ] **Goal**: Achieve $10/agent or better by running at capacity

### Priority 2: Network Optimization (Potential $40-50/month savings)
- [ ] Create S3 VPC Endpoint (free, saves ~$15/month NAT costs)
- [ ] Analyze NAT Gateway usage patterns
- [ ] Consider single NAT Gateway for non-prod ($32/month savings)
- [ ] Consider ECR VPC Endpoint if high image pull frequency

### Priority 3: Cost Monitoring & Governance
- [ ] Set up AWS Cost Explorer with agent count tracking
- [ ] Create budget alert at $250/month (20% buffer)
- [ ] Track cost per agent metric in dashboard
- [ ] Monthly review of actual vs expected costs

### Priority 4: Scale Planning
- [ ] Document when to add 2nd multi-agent runtime (21-40 agents)
- [ ] Plan per-agent task migration for 50+ agents scale
- [ ] Test per-agent task architecture in staging
- [ ] Document resource requirements per agent type

---

## Appendix: AWS Pricing References

**Fargate (us-east-2)**:
- vCPU: $0.04048/hour
- Memory: $0.004445/GB-hour

**RDS Aurora PostgreSQL (us-east-2)**:
- db.r6g.large: $0.288/hour
- db.r6g.medium: $0.144/hour
- Storage: $0.10/GB-month
- I/O: $0.20/1M requests

**RDS MySQL (us-east-2)**:
- db.t3.micro: $0.017/hour
- Storage: $0.115/GB-month

**Load Balancers (us-east-2)**:
- ALB: $16.20/month + $0.008/LCU-hour
- NLB: $16.20/month + $0.006/NLCU-hour

**NAT Gateway (us-east-2)**:
- Fixed: $0.045/hour ($32.40/month)
- Data: $0.045/GB

**S3 Standard (us-east-2)**:
- Storage: $0.023/GB-month
- Requests: $0.0004/1000 GET

**CloudWatch Logs (us-east-2)**:
- Ingestion: $0.50/GB
- Storage: $0.03/GB-month

---

## Quick Reference: Unit Cost Calculator

**Formula for Multi-Agent Runtime:**
```
Cost per agent = $206.80 / number_of_agents

Examples:
- 1 agent:  $207/agent
- 5 agents: $41/agent
- 10 agents: $21/agent
- 20 agents: $10/agent (maximum capacity)
```

**Formula for Per-Agent Tasks:**
```
Cost per agent = $9.52 + ($131.42 / number_of_agents)

Examples:
- 10 agents: $23/agent
- 50 agents: $12/agent
- 100 agents: $11/agent
```

**Break-even point**: ~23 agents (multi-agent runtime becomes more cost-effective than per-agent tasks)

---

**Report Generated**: October 5, 2025  
**Updated**: After Aurora PostgreSQL deletion ($427/month savings)  
**Current Total Cost**: $206.80/month (68% reduction from original $649/month)  
**Next Review**: After deploying additional agents to measure actual unit economics
