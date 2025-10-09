# Marginal COGS Quick Reference

## Cost Variable Hierarchy

```
Total Cost (TC)
├── Fixed Costs (F) = $120.46/month
│   ├── ALB = $27.88
│   ├── NLB = $20.58
│   ├── NAT Gateways (2×) = $67.05
│   ├── Cloud Map namespace = $1.00
│   ├── ECR repository = $0.20
│   ├── S3 infrastructure = $0.50
│   └── CloudWatch logs (infra) = $1.00
│
├── Semi-Variable Costs (S)
│   ├── Database = $14.71/month
│   └── Runtime costs (depends on architecture)
│       ├── Multi-Agent: Fargate runtime = $72.08 + Logs = $1.80
│       └── Per-Agent: $0 (costs moved to per-agent variable)
│
└── Variable Costs (V) = Per-agent costs
    └── Per-Agent Architecture only:
        ├── Fargate task = $9.01/agent
        ├── CloudWatch logs = $0.50/agent
        └── S3 APKG = $0.01/agent
```

## Core Formulas

### Multi-Agent Runtime
```
TC(n) = F + S_multi + (V_multi × n)
      = $120.46 + $88.59 + ($0 × n)
      = $209.05  (constant for n ≤ 20)

MC(n) = $0/agent  (until capacity limit)
AC(n) = $209.05 / n
```

### Per-Agent Tasks
```
TC(n) = F + S_single + (V_single × n)
      = $120.46 + $14.71 + ($9.52 × n)
      = $135.17 + $9.52n

MC(n) = $9.52/agent  (constant)
AC(n) = $135.17/n + $9.52
```

## Key Metrics

| Metric | Multi-Agent (n=20) | Per-Agent (n=20) |
|--------|-------------------|------------------|
| **Total Cost** | $209.05 | $325.57 |
| **Average Cost** | $10.45/agent | $16.28/agent |
| **Marginal Cost** | $0/agent | $9.52/agent |
| **Break-even** | n ≥ 5 agents | n < 5 agents |

## Decision Tree

```
Start: How many agents?
│
├─ n < 5 agents
│  └─ Use: Per-Agent Tasks
│     Cost: ~$20-140/agent
│
├─ 5 ≤ n ≤ 20 agents
│  └─ Use: Multi-Agent Runtime
│     Cost: $10-40/agent
│     Marginal: $0/agent
│
├─ 21 ≤ n ≤ 40 agents
│  └─ Use: 2× Multi-Agent Runtimes
│     Cost: ~$7-15/agent
│     Marginal: $0/agent (within capacity)
│
└─ n > 40 agents
   └─ Consider: Per-Agent Tasks
      Cost: ~$11-13/agent
      Marginal: $9.52/agent (constant)
```

## Optimization Levers

| Variable | Current | Optimized | Savings | Impact on MC |
|----------|---------|-----------|---------|--------------|
| **NAT Gateways** | 2 | 1 | $32.85/mo | -$1.64/agent (n=20) |
| **S3 VPC Endpoint** | No | Yes | ~$15/mo | -$0.75/agent (n=20) |
| **Database** | t3.micro | Right-sized | Variable | Scales with n |
| **Fargate Savings Plan** | No | Yes (1yr) | 17% | -$1.23/agent (per-agent) |

## Python Cost Calculator

```python
# Quick calculator
def marginal_cost(architecture, n_agents):
    """Calculate marginal cost for nth agent"""
    if architecture == "multi":
        # Check if new runtime needed
        if n_agents % 20 == 1:
            return 88.59  # New runtime
        return 0  # Within capacity
    else:  # per-agent
        return 9.52  # Constant

def average_cost(architecture, n_agents):
    """Calculate average cost per agent"""
    if architecture == "multi":
        n_runtimes = math.ceil(n_agents / 20)
        tc = 120.46 + (88.59 * n_runtimes)
        return tc / n_agents
    else:  # per-agent
        tc = 135.17 + (9.52 * n_agents)
        return tc / n_agents

# Example usage
print(f"Multi-agent (20): ${average_cost('multi', 20):.2f}/agent")
print(f"Per-agent (20): ${average_cost('per-agent', 20):.2f}/agent")
print(f"Marginal cost (multi, 15th agent): ${marginal_cost('multi', 15):.2f}")
print(f"Marginal cost (multi, 21st agent): ${marginal_cost('multi', 21):.2f}")
```

## Cost Sensitivity Matrix

| If this increases by 10% | Impact on AC (n=20) |
|---------------------------|---------------------|
| **Fargate runtime price** | Multi: +$0.36/agent<br>Per-agent: +$0.45/agent |
| **Database price** | Both: +$0.07/agent |
| **NAT Gateway price** | Both: +$0.34/agent |
| **Load balancer price** | Both: +$0.24/agent |

## Real-World Scenarios

### Scenario 1: Startup (1-5 agents)
```
Optimal: Per-Agent Tasks
Monthly: $145-183
Per agent: $145-37/agent
Reason: Fixed costs amortized over few agents
```

### Scenario 2: Growth (5-20 agents)
```
Optimal: Multi-Agent Runtime
Monthly: $209 (fixed)
Per agent: $42-10/agent
Reason: Zero marginal cost!
```

### Scenario 3: Scale (50+ agents)
```
Optimal: Multi-Agent (3 runtimes) or Per-Agent Tasks
Multi-Agent: $386 = $7.72/agent
Per-Agent: $611 = $12.22/agent
Reason: Multi-agent still cheaper, but per-agent simpler operations
```

## Monthly Cost Table

| Agents | Multi-Agent | Per-Agent | Savings | Optimal |
|--------|-------------|-----------|---------|---------|
| 1 | $209.05 | $144.69 | -$64.36 | Per-Agent |
| 5 | $209.05 | $182.77 | $26.28 | Multi |
| 10 | $209.05 | $230.37 | $21.32 | Multi |
| 20 | $209.05 | $325.57 | $116.52 | **Multi** |
| 40 | $297.64 | $515.97 | $218.33 | **Multi** |
| 50 | $386.23 | $611.17 | $224.94 | **Multi** |
| 100 | $563.41 | $1,087.17 | $523.76 | **Multi** |

**Insight**: Multi-agent runtime optimal for nearly all production scenarios (n ≥ 5)

---

**TL;DR:**
- **Marginal Cost (Multi-Agent)**: $0 per agent (until capacity)
- **Marginal Cost (Per-Agent)**: $9.52 per agent (constant)
- **Optimal for most**: Multi-Agent Runtime
- **Break-even**: 5 agents
