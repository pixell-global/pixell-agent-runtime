# Cost Analysis at Scale: Hosting Agent Apps

**Generated**: October 5, 2025  
**Based on**: Marginal COGS Mathematical Model  
**Region**: us-east-2 (Ohio)

---

## Cost Breakdown by Agent Count

### Summary Table

| Agents (n) | Multi-Agent Total | Multi-Agent per Agent | Per-Agent Total | Per-Agent per Agent | Savings | Winner |
|-----------|-------------------|----------------------|-----------------|---------------------|---------|---------|
| **1** | $209.05 | $209.05 | $144.69 | $144.69 | -$64.36 | Per-Agent |
| **10** | $209.05 | $20.91 | $230.37 | $23.04 | $21.32 | **Multi-Agent** |
| **100** | $563.41 | $5.63 | $1,087.17 | $10.87 | $523.76 | **Multi-Agent** |
| **1,000** | $4,549.96 | $4.55 | $9,655.17 | $9.66 | $5,105.21 | **Multi-Agent** |
| **5,000** | $22,267.96 | $4.45 | $47,735.17 | $9.55 | $25,467.21 | **Multi-Agent** |
| **10,000** | $44,415.46 | $4.44 | $95,335.17 | $9.53 | $50,919.71 | **Multi-Agent** |

---

## Detailed Cost Analysis

### n = 1 Agent

**Multi-Agent Runtime:**
```
Fixed Infrastructure:     $120.46
1 Runtime (20 capacity):  $88.59
────────────────────────────────
Total Monthly Cost:       $209.05
Cost per Agent:           $209.05/agent
```

**Per-Agent Tasks:**
```
Fixed Infrastructure:     $135.17
1 Agent Task:            $9.52
────────────────────────────────
Total Monthly Cost:       $144.69
Cost per Agent:           $144.69/agent
```

**Winner:** Per-Agent Tasks (saves $64.36/month)

**Insight:** For a single agent, per-agent task is more efficient because you don't pay for unused multi-agent runtime capacity.

---

### n = 10 Agents

**Multi-Agent Runtime:**
```
Fixed Infrastructure:     $120.46
1 Runtime (holds 20):     $88.59
────────────────────────────────
Total Monthly Cost:       $209.05
Cost per Agent:           $20.91/agent

Marginal Cost: $0/agent (all 10 fit in one runtime)
```

**Per-Agent Tasks:**
```
Fixed Infrastructure:     $135.17
10 Agent Tasks:          $95.20  (10 × $9.52)
────────────────────────────────
Total Monthly Cost:       $230.37
Cost per Agent:           $23.04/agent

Marginal Cost: $9.52/agent (constant)
```

**Winner:** Multi-Agent Runtime (saves $21.32/month)

**Insight:** Break-even point is around 5 agents. Multi-agent becomes more cost-effective after that.

---

### n = 100 Agents

**Multi-Agent Runtime:**
```
Fixed Infrastructure:     $120.46
5 Runtimes:              $442.95  (⌈100/20⌉ = 5 runtimes)
────────────────────────────────
Total Monthly Cost:       $563.41
Cost per Agent:           $5.63/agent

Runtimes Needed: 5
Agents per Runtime: 20 (fully utilized)
Marginal Cost: $0/agent (within capacity)
```

**Per-Agent Tasks:**
```
Fixed Infrastructure:     $135.17
100 Agent Tasks:         $952.00  (100 × $9.52)
────────────────────────────────
Total Monthly Cost:       $1,087.17
Cost per Agent:           $10.87/agent

Marginal Cost: $9.52/agent
```

**Winner:** Multi-Agent Runtime (saves $523.76/month or 48%)

**Insight:** At 100 agents, multi-agent is nearly 2× more cost-efficient. Cost per agent has dropped to $5.63.

---

### n = 1,000 Agents

**Multi-Agent Runtime:**
```
Fixed Infrastructure:     $120.46
50 Runtimes:             $4,429.50  (⌈1000/20⌉ = 50 runtimes)
────────────────────────────────
Total Monthly Cost:       $4,549.96
Cost per Agent:           $4.55/agent

Runtimes Needed: 50
Total Fargate vCPUs: 100 vCPUs (50 runtimes × 2 vCPU)
Total Memory: 200 GB (50 runtimes × 4 GB)
Marginal Cost: $0/agent (within capacity)
```

**Per-Agent Tasks:**
```
Fixed Infrastructure:     $135.17
1,000 Agent Tasks:       $9,520.00  (1,000 × $9.52)
────────────────────────────────
Total Monthly Cost:       $9,655.17
Cost per Agent:           $9.66/agent

Total Fargate vCPUs: 250 vCPUs (1,000 × 0.25 vCPU)
Total Memory: 500 GB (1,000 × 0.5 GB)
Marginal Cost: $9.52/agent
```

**Winner:** Multi-Agent Runtime (saves $5,105.21/month or 53%)

**Insights:** 
- Multi-agent is 2.1× more cost-efficient
- Uses fewer total vCPUs (100 vs 250) - more resource efficient
- Annual savings: **$61,262.52** with multi-agent

---

### n = 5,000 Agents

**Multi-Agent Runtime:**
```
Fixed Infrastructure:     $120.46
250 Runtimes:            $22,147.50  (⌈5000/20⌉ = 250 runtimes)
────────────────────────────────
Total Monthly Cost:       $22,267.96
Cost per Agent:           $4.45/agent

Runtimes Needed: 250
Total Fargate vCPUs: 500 vCPUs (250 runtimes × 2 vCPU)
Total Memory: 1,000 GB (250 runtimes × 4 GB)
Capacity Utilization: 100% (5000/5000)
Marginal Cost: $0/agent
```

**Per-Agent Tasks:**
```
Fixed Infrastructure:     $135.17
5,000 Agent Tasks:       $47,600.00  (5,000 × $9.52)
────────────────────────────────
Total Monthly Cost:       $47,735.17
Cost per Agent:           $9.55/agent

Total Fargate vCPUs: 1,250 vCPUs (5,000 × 0.25 vCPU)
Total Memory: 2,500 GB (5,000 × 0.5 GB)
Marginal Cost: $9.52/agent
```

**Winner:** Multi-Agent Runtime (saves $25,467.21/month or 53%)

**Insights:**
- Multi-agent is 2.1× more cost-efficient
- Uses 60% fewer vCPUs (500 vs 1,250)
- Annual savings: **$305,606.52** with multi-agent
- Cost per agent approaches asymptotic limit of ~$4.45

---

### n = 10,000 Agents

**Multi-Agent Runtime:**
```
Fixed Infrastructure:     $120.46
500 Runtimes:            $44,295.00  (⌈10000/20⌉ = 500 runtimes)
────────────────────────────────
Total Monthly Cost:       $44,415.46
Cost per Agent:           $4.44/agent

Runtimes Needed: 500
Total Fargate vCPUs: 1,000 vCPUs (500 runtimes × 2 vCPU)
Total Memory: 2,000 GB (500 runtimes × 4 GB)
Capacity Utilization: 100%
Marginal Cost: $0/agent
```

**Per-Agent Tasks:**
```
Fixed Infrastructure:     $135.17
10,000 Agent Tasks:      $95,200.00  (10,000 × $9.52)
────────────────────────────────
Total Monthly Cost:       $95,335.17
Cost per Agent:           $9.53/agent

Total Fargate vCPUs: 2,500 vCPUs (10,000 × 0.25 vCPU)
Total Memory: 5,000 GB (10,000 × 0.5 GB)
Marginal Cost: $9.52/agent
```

**Winner:** Multi-Agent Runtime (saves $50,919.71/month or 53%)

**Insights:**
- Multi-agent is 2.15× more cost-efficient
- Uses 60% fewer vCPUs (1,000 vs 2,500)
- Uses 60% less memory (2,000 GB vs 5,000 GB)
- Annual savings: **$611,036.52** with multi-agent
- Cost per agent stabilizes at ~$4.44 (approaching asymptotic limit)

---

## Comparative Visualization

### Monthly Cost Comparison

```
n = 1:
Multi:  ████████████████████ $209
Per:    ██████████████ $145 ← WINNER

n = 10:
Multi:  ████████████████████ $209 ← WINNER
Per:    ██████████████████████ $230

n = 100:
Multi:  ████████████████████████████ $563 ← WINNER
Per:    ████████████████████████████████████████████████████ $1,087

n = 1,000:
Multi:  ████████████████████████████████████████████ $4,550 ← WINNER
Per:    ███████████████████████████████████████████████████████████████████████████████████████ $9,655

n = 5,000:
Multi:  ████████████████████████████████████████████ $22,268 ← WINNER
Per:    ███████████████████████████████████████████████████████████████████████████████████████████████████ $47,735

n = 10,000:
Multi:  ████████████████████████████████████████████ $44,415 ← WINNER
Per:    ███████████████████████████████████████████████████████████████████████████████████████████████████████████████ $95,335
```

### Cost per Agent Comparison

```
n = 1:     Multi: $209/agent | Per: $145/agent ← WINNER
n = 10:    Multi: $21/agent  | Per: $23/agent
n = 100:   Multi: $6/agent   | Per: $11/agent
n = 1,000: Multi: $5/agent   | Per: $10/agent
n = 5,000: Multi: $4/agent   | Per: $10/agent
n = 10,000: Multi: $4/agent  | Per: $10/agent
           └──────┬──────┘    └──────┬──────┘
           Decreasing         Constant
           (Economies of      (Linear
            Scale)             Scaling)
```

---

## Key Metrics Summary

### Cost Efficiency Comparison

| Scale | Multi-Agent Cost/Agent | Per-Agent Cost/Agent | Multi-Agent Advantage |
|-------|----------------------|---------------------|---------------------|
| **Small (1)** | $209.05 | $144.69 | -30% (worse) |
| **Small (10)** | $20.91 | $23.04 | +9% (better) |
| **Medium (100)** | $5.63 | $10.87 | +48% (better) |
| **Large (1K)** | $4.55 | $9.66 | +53% (better) |
| **XL (5K)** | $4.45 | $9.55 | +53% (better) |
| **XXL (10K)** | $4.44 | $9.53 | +53% (better) |

### Annual Cost Comparison

| Agents | Multi-Agent Annual | Per-Agent Annual | Annual Savings |
|--------|-------------------|------------------|----------------|
| 1 | $2,508.60 | $1,736.28 | -$772.32 |
| 10 | $2,508.60 | $2,764.44 | $255.84 |
| 100 | $6,760.92 | $13,046.04 | **$6,285.12** |
| 1,000 | $54,599.52 | $115,862.04 | **$61,262.52** |
| 5,000 | $267,215.52 | $572,822.04 | **$305,606.52** |
| 10,000 | $532,985.52 | $1,144,022.04 | **$611,036.52** |

### Resource Utilization Efficiency

| Agents | Multi-Agent vCPUs | Per-Agent vCPUs | vCPU Efficiency |
|--------|------------------|----------------|----------------|
| 100 | 10 vCPUs | 25 vCPUs | 2.5× more efficient |
| 1,000 | 100 vCPUs | 250 vCPUs | 2.5× more efficient |
| 5,000 | 500 vCPUs | 1,250 vCPUs | 2.5× more efficient |
| 10,000 | 1,000 vCPUs | 2,500 vCPUs | 2.5× more efficient |

**Insight:** Multi-agent runtime uses 60% fewer vCPUs and memory resources at scale.

---

## Cost Per Agent Trends

### Multi-Agent Runtime (Economies of Scale)

```
Cost per Agent = $120.46/n + $88.59/n × ⌈n/20⌉

As n → ∞:
Cost per Agent → $88.59/20 = $4.43/agent (asymptotic limit)
```

| Agents | Cost/Agent | % of Limit |
|--------|-----------|------------|
| 20 | $10.45 | 236% |
| 100 | $5.63 | 127% |
| 1,000 | $4.55 | 103% |
| 5,000 | $4.45 | 100% |
| 10,000 | $4.44 | 100% |
| ∞ | $4.43 | 100% |

**Conclusion:** Cost per agent decreases rapidly and approaches $4.43/agent at scale.

### Per-Agent Tasks (Linear Scaling)

```
Cost per Agent = $135.17/n + $9.52

As n → ∞:
Cost per Agent → $9.52/agent (asymptotic limit)
```

| Agents | Cost/Agent | % of Limit |
|--------|-----------|------------|
| 20 | $16.28 | 171% |
| 100 | $10.87 | 114% |
| 1,000 | $9.66 | 101% |
| 5,000 | $9.55 | 100% |
| 10,000 | $9.53 | 100% |
| ∞ | $9.52 | 100% |

**Conclusion:** Cost per agent approaches $9.52/agent and remains constant at scale.

---

## Break-Even Analysis

### When does Multi-Agent become cheaper?

**Solving:** $209.05/n = $135.17/n + $9.52

```
$209.05/n - $135.17/n = $9.52
$73.88/n = $9.52
n = 7.76 agents
```

**Break-even point:** ~8 agents

**Practical recommendation:** Use multi-agent for n ≥ 5 agents

---

## Real-World Financial Implications

### If charging customers $20/agent/month:

| Agents | Multi-Agent Profit | Per-Agent Profit | Extra Profit w/ Multi |
|--------|-------------------|-----------------|---------------------|
| 10 | $190.95 (91%) | $169.63 (74%) | $21.32 (+13%) |
| 100 | $1,436.59 (72%) | $912.83 (46%) | $523.76 (+57%) |
| 1,000 | $15,450.04 (77%) | $10,344.83 (52%) | $5,105.21 (+49%) |
| 5,000 | $77,732.04 (78%) | $52,264.83 (52%) | $25,467.21 (+49%) |
| 10,000 | $155,584.54 (78%) | $104,664.83 (52%) | $50,919.71 (+49%) |

**Insight:** At scale, multi-agent runtime increases profit margins by ~50% compared to per-agent tasks.

### If charging customers $15/agent/month:

| Agents | Multi-Agent Profit | Per-Agent Profit | Verdict |
|--------|-------------------|-----------------|---------|
| 100 | $936.59 (62%) | $412.83 (27%) | Multi: +127% profit |
| 1,000 | $10,450.04 (70%) | $5,344.83 (36%) | Multi: +96% profit |
| 10,000 | $105,584.54 (70%) | $54,664.83 (37%) | Multi: +93% profit |

---

## Operational Considerations at Scale

### n = 10,000 Agents

**Multi-Agent Runtime:**
- **Containers to manage:** 500 ECS tasks
- **CloudWatch log groups:** 500
- **Health checks:** 500 endpoints
- **Deployment complexity:** Moderate
- **Rollout time:** ~10-15 minutes (500 tasks)
- **Operational overhead:** Medium

**Per-Agent Tasks:**
- **Containers to manage:** 10,000 ECS tasks
- **CloudWatch log groups:** 10,000
- **Health checks:** 10,000 endpoints
- **Deployment complexity:** High
- **Rollout time:** 30-60 minutes (10,000 tasks)
- **Operational overhead:** Very High

**Winner:** Multi-Agent (20× fewer resources to manage)

---

## Recommendations by Scale

### Small Scale (1-10 agents)
**Use:** Per-Agent Tasks for n=1, Multi-Agent for n≥5
**Reason:** More cost-effective at n=1, but multi-agent wins by n=5

### Medium Scale (10-1,000 agents)
**Use:** Multi-Agent Runtime
**Reason:** 
- 2× more cost-efficient
- Simpler operations
- Optimal capacity utilization

### Large Scale (1,000-10,000 agents)
**Use:** Multi-Agent Runtime
**Reason:**
- 2.1× more cost-efficient
- 60% less infrastructure
- $300K-600K annual savings
- Approaching asymptotic efficiency

### Massive Scale (10,000+ agents)
**Consider:** Hybrid approach
- **Premium agents** → Per-Agent Tasks (SLA, isolation)
- **Standard agents** → Multi-Agent Runtime (efficiency)
- **Result:** Balance cost vs isolation

---

## Final Cost Summary Table

| Agents | Multi-Agent ($/month) | Per-Agent ($/month) | Winner | Monthly Savings |
|--------|--------------------|-------------------|--------|----------------|
| **1** | $209.05 | $144.69 | Per-Agent | -$64.36 |
| **10** | $209.05 | $230.37 | **Multi-Agent** | $21.32 |
| **100** | $563.41 | $1,087.17 | **Multi-Agent** | $523.76 |
| **1,000** | $4,549.96 | $9,655.17 | **Multi-Agent** | $5,105.21 |
| **5,000** | $22,267.96 | $47,735.17 | **Multi-Agent** | $25,467.21 |
| **10,000** | $44,415.46 | $95,335.17 | **Multi-Agent** | $50,919.71 |

---

**Key Takeaway:** Multi-Agent Runtime is the clear winner for hosting many agent apps. At 10,000 agents, it costs **$4.44/agent** vs **$9.53/agent** for per-agent tasks, saving over **$50,000/month** or **$600,000/year**.


