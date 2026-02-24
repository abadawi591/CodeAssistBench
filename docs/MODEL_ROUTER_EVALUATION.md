# Model Router Evaluation with CodeAssistBench

This document describes how to use CodeAssistBench to evaluate a binary model router that decides between a strong model (e.g., GPT-5.2) and a weaker/cheaper model (e.g., GPT-5.2-mini).

## Table of Contents

1. [Overview](#overview)
2. [Evaluation Methodology](#evaluation-methodology)
3. [Issue Classification](#issue-classification)
4. [Router Evaluation Metrics](#router-evaluation-metrics)
5. [Implementation Guide](#implementation-guide)
6. [Expected Outcomes](#expected-outcomes)

---

## Overview

A model router is a binary classifier that decides which model to use for a given query:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MODEL ROUTER CONCEPT                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│                        ┌─────────────────┐                                  │
│                        │   User Query    │                                  │
│                        │  (GitHub Issue) │                                  │
│                        └────────┬────────┘                                  │
│                                 │                                           │
│                                 ▼                                           │
│                     ┌──────────────────────┐                                │
│                     │    MODEL ROUTER      │                                │
│                     │  (Binary Classifier) │                                │
│                     └──────────┬───────────┘                                │
│                                │                                            │
│              ┌─────────────────┴─────────────────┐                          │
│              │                                   │                          │
│              ▼                                   ▼                          │
│    ┌─────────────────────┐            ┌─────────────────────┐              │
│    │    STRONG MODEL     │            │    WEAK MODEL       │              │
│    │     (GPT-5.2)       │            │   (GPT-5.2-mini)    │              │
│    │                     │            │                     │              │
│    │  - Higher quality   │            │  - Lower cost       │              │
│    │  - More expensive   │            │  - Faster           │              │
│    │  - Slower           │            │  - Good enough?     │              │
│    └─────────────────────┘            └─────────────────────┘              │
│                                                                             │
│  GOAL: Route to weak model when possible, strong model when necessary       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Evaluation Methodology

### Phase 1: Baseline Runs

Run the entire dataset through BOTH models independently to establish ground truth:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PHASE 1: BASELINE EVALUATION                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Dataset: N issues (e.g., 159 Python issues)                                │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         RUN 1: STRONG MODEL                         │   │
│  │                                                                     │   │
│  │   Issues ────► GPT-5.2 ────► Results (gen_strong.jsonl)            │   │
│  │                                                                     │   │
│  │   Output: {issue_id, satisfaction_status, turns, cost, latency}    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         RUN 2: WEAK MODEL                           │   │
│  │                                                                     │   │
│  │   Issues ────► GPT-5.2-mini ────► Results (gen_weak.jsonl)         │   │
│  │                                                                     │   │
│  │   Output: {issue_id, satisfaction_status, turns, cost, latency}    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Commands for Baseline Runs

```bash
# Run 1: Strong model (GPT-5.2)
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language python \
  --no-docker \
  --output results/gen_strong_gpt52.jsonl \
  --concurrency 10

# Run 2: Weak model (GPT-5.2-mini)
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2-mini", "user": "gpt-5.2-mini"}' \
  --language python \
  --no-docker \
  --output results/gen_weak_gpt52mini.jsonl \
  --concurrency 10
```

---

## Issue Classification

After baseline runs, classify each issue into one of four categories:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    ISSUE CLASSIFICATION MATRIX                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│                              WEAK MODEL (GPT-5.2-mini)                      │
│                         ┌─────────────────┬─────────────────┐               │
│                         │    SATISFIED    │  NOT SATISFIED  │               │
│  ┌──────────────────────┼─────────────────┼─────────────────┤               │
│  │                      │                 │                 │               │
│  │      SATISFIED       │   BOTH SOLVE    │  STRONG ONLY    │               │
│  │                      │       (A)       │      (B)        │               │
│  │ STRONG ──────────────┼─────────────────┼─────────────────┤               │
│  │ MODEL                │                 │                 │               │
│  │ (GPT-5.2)            │   WEAK ONLY     │  NEITHER SOLVE  │               │
│  │    NOT SATISFIED     │       (C)       │      (D)        │               │
│  │                      │                 │                 │               │
│  └──────────────────────┴─────────────────┴─────────────────┘               │
│                                                                             │
│  Categories:                                                                │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │ (A) BOTH_SOLVE      │ Easy issues - route to WEAK model (save $)   │   │
│  ├─────────────────────┼───────────────────────────────────────────────┤   │
│  │ (B) STRONG_ONLY     │ Hard issues - MUST route to STRONG model     │   │
│  ├─────────────────────┼───────────────────────────────────────────────┤   │
│  │ (C) WEAK_ONLY       │ Anomaly - weak better? (investigate)         │   │
│  ├─────────────────────┼───────────────────────────────────────────────┤   │
│  │ (D) NEITHER_SOLVE   │ Very hard - neither model works              │   │
│  └─────────────────────┴───────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Expected Distribution (Hypothetical)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    EXPECTED DISTRIBUTION                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Total Issues: 159                                                          │
│                                                                             │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │                                                                    │    │
│  │  BOTH_SOLVE (A)    ████████████████████████████████  60% (95)     │    │
│  │                                                                    │    │
│  │  STRONG_ONLY (B)   ████████████████                  25% (40)     │    │
│  │                                                                    │    │
│  │  WEAK_ONLY (C)     ██                                 3% (5)      │    │
│  │                                                                    │    │
│  │  NEITHER (D)       ██████                            12% (19)     │    │
│  │                                                                    │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  Key Insight:                                                               │
│  - 60% of issues could use the cheaper model                               │
│  - 25% genuinely need the strong model                                     │
│  - Router's job: Identify the 25% that need strong model                   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Router Evaluation Metrics

### Phase 2: Router Evaluation

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PHASE 2: ROUTER EVALUATION                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         RUN 3: WITH ROUTER                          │   │
│  │                                                                     │   │
│  │   Issues ────► Router ────┬────► Strong Model ────► Results        │   │
│  │                           │                                         │   │
│  │                           └────► Weak Model ──────► Results        │   │
│  │                                                                     │   │
│  │   Output: {issue_id, routed_to, satisfaction_status, cost}         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Confusion Matrix for Router

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    ROUTER CONFUSION MATRIX                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  For issues in STRONG_ONLY category (B):                                    │
│  (These are the critical ones - router MUST route to strong)                │
│                                                                             │
│                              ROUTER DECISION                                │
│                    ┌─────────────────┬─────────────────┐                    │
│                    │  Route STRONG   │  Route WEAK     │                    │
│  ┌─────────────────┼─────────────────┼─────────────────┤                    │
│  │                 │                 │                 │                    │
│  │  NEEDS STRONG   │  TRUE POSITIVE  │ FALSE NEGATIVE  │                    │
│  │  (STRONG_ONLY)  │      (TP)       │      (FN)       │ ◄── CRITICAL!      │
│  │                 │   Correct!      │   FAILURE!      │     User unhappy   │
│  │ GROUND ─────────┼─────────────────┼─────────────────┤                    │
│  │ TRUTH           │                 │                 │                    │
│  │                 │ FALSE POSITIVE  │  TRUE NEGATIVE  │                    │
│  │  CAN USE WEAK   │      (FP)       │      (TN)       │                    │
│  │  (BOTH_SOLVE)   │   Wasted $      │   Correct!      │                    │
│  │                 │                 │   Saved $       │                    │
│  └─────────────────┴─────────────────┴─────────────────┘                    │
│                                                                             │
│  Key Metrics:                                                               │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                                                                     │   │
│  │  RECALL (for STRONG_ONLY) = TP / (TP + FN)                         │   │
│  │  └── "What % of hard issues did router correctly send to strong?"  │   │
│  │  └── TARGET: > 95% (minimize FN - user failures)                   │   │
│  │                                                                     │   │
│  │  PRECISION (for STRONG routing) = TP / (TP + FP)                   │   │
│  │  └── "Of issues sent to strong, what % actually needed it?"        │   │
│  │  └── Higher = more cost savings                                    │   │
│  │                                                                     │   │
│  │  COST SAVINGS = (TN × weak_cost) / (All × strong_cost)             │   │
│  │  └── "How much $ did we save by routing easy issues to weak?"      │   │
│  │                                                                     │   │
│  │  QUALITY PRESERVED = 1 - (FN / total_issues)                       │   │
│  │  └── "What % of issues got satisfactory responses?"                │   │
│  │                                                                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Trade-off Visualization

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    COST vs QUALITY TRADE-OFF                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Quality                                                                    │
│  (% Satisfied)                                                              │
│       │                                                                     │
│  100% ┼─────────────────────────────●───────────── All Strong              │
│       │                          ●                                          │
│       │                       ●                                             │
│   95% ┼─────────────────────●───────────────────── Optimal Router          │
│       │                   ●                                                 │
│       │                 ●                                                   │
│   90% ┼───────────────●─────────────────────────── Aggressive Router       │
│       │             ●                                                       │
│       │           ●                                                         │
│   85% ┼─────────●───────────────────────────────── Over-aggressive         │
│       │       ●                                                             │
│       │     ●                                                               │
│   60% ┼───●─────────────────────────────────────── All Weak                │
│       │                                                                     │
│       └─────┼─────────┼─────────┼─────────┼─────────┼──────► Cost          │
│            20%       40%       60%       80%      100%      (% of All-Strong)
│                                                                             │
│  Ideal Router: Maximize quality while minimizing cost                       │
│  Target: 95%+ quality at 40-50% cost                                        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Implementation Guide

### Step 1: Run Baseline Evaluations

```bash
# Create results directory
mkdir -p results/router_eval

# Run strong model
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language python \
  --no-docker \
  --output results/router_eval/baseline_strong.jsonl \
  --concurrency 10

# Run weak model  
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2-mini", "user": "gpt-5.2-mini"}' \
  --language python \
  --no-docker \
  --output results/router_eval/baseline_weak.jsonl \
  --concurrency 10
```

### Step 2: Analyze and Classify Issues

```python
# analyze_baselines.py
import json
from collections import defaultdict

def load_results(filepath):
    results = {}
    with open(filepath, 'r') as f:
        for line in f:
            data = json.loads(line)
            issue_id = data['issue_id']
            satisfied = data['satisfaction_status'] == 'SATISFIED'
            results[issue_id] = {
                'satisfied': satisfied,
                'status': data['satisfaction_status'],
                'turns': data.get('total_conversation_rounds', 0)
            }
    return results

# Load both baseline results
strong_results = load_results('results/router_eval/baseline_strong.jsonl')
weak_results = load_results('results/router_eval/baseline_weak.jsonl')

# Classify issues
classification = {
    'BOTH_SOLVE': [],      # A: Easy - use weak
    'STRONG_ONLY': [],     # B: Hard - need strong
    'WEAK_ONLY': [],       # C: Anomaly
    'NEITHER_SOLVE': []    # D: Very hard
}

for issue_id in strong_results:
    strong_ok = strong_results[issue_id]['satisfied']
    weak_ok = weak_results.get(issue_id, {}).get('satisfied', False)
    
    if strong_ok and weak_ok:
        classification['BOTH_SOLVE'].append(issue_id)
    elif strong_ok and not weak_ok:
        classification['STRONG_ONLY'].append(issue_id)
    elif not strong_ok and weak_ok:
        classification['WEAK_ONLY'].append(issue_id)
    else:
        classification['NEITHER_SOLVE'].append(issue_id)

# Print summary
print("Issue Classification:")
for category, issues in classification.items():
    pct = len(issues) / len(strong_results) * 100
    print(f"  {category}: {len(issues)} ({pct:.1f}%)")

# Save classification for router training
with open('results/router_eval/issue_classification.json', 'w') as f:
    json.dump(classification, f, indent=2)
```

### Step 3: Create Router Training Data

```python
# create_router_dataset.py
import json

# Load classification
with open('results/router_eval/issue_classification.json', 'r') as f:
    classification = json.load(f)

# Load original issues for features
issues_data = {}
with open('dataset/cab_recent.jsonl', 'r') as f:
    for line in f:
        data = json.loads(line)
        issues_data[str(data['number'])] = data

# Create training data
# Label: 1 = needs strong model, 0 = weak model is sufficient
training_data = []

for issue_id in classification['STRONG_ONLY']:
    if issue_id in issues_data:
        training_data.append({
            'issue_id': issue_id,
            'title': issues_data[issue_id]['title'],
            'body': issues_data[issue_id]['body'],
            'language': issues_data[issue_id].get('language', 'unknown'),
            'label': 1,  # Needs strong model
            'category': 'STRONG_ONLY'
        })

for issue_id in classification['BOTH_SOLVE']:
    if issue_id in issues_data:
        training_data.append({
            'issue_id': issue_id,
            'title': issues_data[issue_id]['title'],
            'body': issues_data[issue_id]['body'],
            'language': issues_data[issue_id].get('language', 'unknown'),
            'label': 0,  # Weak model sufficient
            'category': 'BOTH_SOLVE'
        })

# Save training data
with open('results/router_eval/router_training_data.jsonl', 'w') as f:
    for item in training_data:
        f.write(json.dumps(item) + '\n')

print(f"Created training data: {len(training_data)} samples")
print(f"  Label 1 (need strong): {sum(1 for x in training_data if x['label'] == 1)}")
print(f"  Label 0 (weak ok): {sum(1 for x in training_data if x['label'] == 0)}")
```

### Step 4: Evaluate Router

```python
# evaluate_router.py
import json

def evaluate_router(router_func, test_issues, ground_truth):
    """
    Evaluate a router function.
    
    Args:
        router_func: Function that takes issue dict, returns 'strong' or 'weak'
        test_issues: List of issue dicts
        ground_truth: Dict mapping issue_id to required model
    
    Returns:
        Evaluation metrics
    """
    results = {
        'TP': 0,  # Correctly routed to strong when needed
        'FN': 0,  # Incorrectly routed to weak when strong needed (BAD!)
        'FP': 0,  # Routed to strong when weak would work (wasted $)
        'TN': 0,  # Correctly routed to weak when weak works (saved $)
    }
    
    for issue in test_issues:
        issue_id = issue['issue_id']
        router_decision = router_func(issue)
        needs_strong = ground_truth.get(issue_id, 'weak') == 'strong'
        
        if needs_strong and router_decision == 'strong':
            results['TP'] += 1
        elif needs_strong and router_decision == 'weak':
            results['FN'] += 1
        elif not needs_strong and router_decision == 'strong':
            results['FP'] += 1
        else:
            results['TN'] += 1
    
    # Calculate metrics
    total = sum(results.values())
    precision = results['TP'] / (results['TP'] + results['FP']) if (results['TP'] + results['FP']) > 0 else 0
    recall = results['TP'] / (results['TP'] + results['FN']) if (results['TP'] + results['FN']) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    # Cost savings (assuming weak costs 20% of strong)
    weak_cost_ratio = 0.2
    all_strong_cost = total * 1.0
    router_cost = (results['TP'] + results['FP']) * 1.0 + (results['TN'] + results['FN']) * weak_cost_ratio
    cost_savings = (all_strong_cost - router_cost) / all_strong_cost
    
    # Quality preserved (issues that got satisfactory response)
    quality_preserved = (results['TP'] + results['TN']) / total
    
    return {
        'confusion_matrix': results,
        'precision': precision,
        'recall': recall,
        'f1_score': f1,
        'cost_savings': cost_savings,
        'quality_preserved': quality_preserved,
        'failure_rate': results['FN'] / total  # Critical metric!
    }

# Example usage
def simple_router(issue):
    """Example: Route based on issue length"""
    text_length = len(issue.get('title', '')) + len(issue.get('body', ''))
    return 'strong' if text_length > 500 else 'weak'

# Build ground truth from classification
ground_truth = {}
with open('results/router_eval/issue_classification.json', 'r') as f:
    classification = json.load(f)
    for issue_id in classification['STRONG_ONLY']:
        ground_truth[issue_id] = 'strong'
    for issue_id in classification['BOTH_SOLVE']:
        ground_truth[issue_id] = 'weak'

# Load test issues
test_issues = []
with open('results/router_eval/router_training_data.jsonl', 'r') as f:
    for line in f:
        test_issues.append(json.loads(line))

# Evaluate
metrics = evaluate_router(simple_router, test_issues, ground_truth)
print("Router Evaluation Results:")
print(f"  Precision: {metrics['precision']:.2%}")
print(f"  Recall: {metrics['recall']:.2%}")
print(f"  F1 Score: {metrics['f1_score']:.2%}")
print(f"  Cost Savings: {metrics['cost_savings']:.2%}")
print(f"  Quality Preserved: {metrics['quality_preserved']:.2%}")
print(f"  Failure Rate: {metrics['failure_rate']:.2%} (target: < 5%)")
```

---

## Expected Outcomes

### Summary Metrics Dashboard

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    ROUTER EVALUATION DASHBOARD                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  BASELINE RESULTS                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  Strong Model (GPT-5.2):                                            │   │
│  │    Satisfaction Rate: 75%  |  Avg Cost: $0.15/issue                 │   │
│  │                                                                     │   │
│  │  Weak Model (GPT-5.2-mini):                                         │   │
│  │    Satisfaction Rate: 50%  |  Avg Cost: $0.03/issue                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ISSUE CLASSIFICATION                                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  ████████████████████████████████████████  BOTH_SOLVE:    60%      │   │
│  │  ████████████████                          STRONG_ONLY:   25%      │   │
│  │  ██                                        WEAK_ONLY:      3%      │   │
│  │  ████████                                  NEITHER:       12%      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ROUTER PERFORMANCE                                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                                                                     │   │
│  │  Metric              Target        Achieved       Status            │   │
│  │  ─────────────────────────────────────────────────────────────      │   │
│  │  Recall              > 95%         97.5%          ✓ PASS            │   │
│  │  Precision           > 50%         62.3%          ✓ PASS            │   │
│  │  Cost Savings        > 30%         48.2%          ✓ PASS            │   │
│  │  Quality Preserved   > 90%         94.1%          ✓ PASS            │   │
│  │  Failure Rate        < 5%          2.5%           ✓ PASS            │   │
│  │                                                                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  FINANCIAL IMPACT                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                                                                     │   │
│  │  All-Strong Cost:     $23.85 (159 issues × $0.15)                  │   │
│  │  With Router Cost:    $12.35                                        │   │
│  │  ───────────────────────────────────────────                       │   │
│  │  Savings:             $11.50 (48.2%)                                │   │
│  │                                                                     │   │
│  │  Quality Impact:                                                    │   │
│  │    Without Router:    75% satisfied (119/159)                       │   │
│  │    With Router:       73% satisfied (116/159)                       │   │
│  │    Quality Loss:      -2% (acceptable trade-off)                    │   │
│  │                                                                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Decision Framework

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    ROUTER DECISION FRAMEWORK                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  When to ship router to production:                                         │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                                                                     │   │
│  │  ✓ Recall > 95%                                                    │   │
│  │    └── Critical: Don't miss hard issues                            │   │
│  │                                                                     │   │
│  │  ✓ Failure Rate < 5%                                               │   │
│  │    └── Users experience degraded quality < 5% of time              │   │
│  │                                                                     │   │
│  │  ✓ Cost Savings > 30%                                              │   │
│  │    └── Meaningful financial benefit                                │   │
│  │                                                                     │   │
│  │  ✓ Quality Preserved > 90%                                         │   │
│  │    └── Overall satisfaction remains high                           │   │
│  │                                                                     │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  If metrics not met:                                                        │
│                                                                             │
│  1. Collect more training data (especially STRONG_ONLY cases)               │
│  2. Engineer better features (code complexity, language, etc.)              │
│  3. Try ensemble methods                                                    │
│  4. Adjust classification threshold                                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Files Structure

```
results/router_eval/
├── baseline_strong.jsonl      # GPT-5.2 results
├── baseline_weak.jsonl        # GPT-5.2-mini results
├── issue_classification.json  # A/B/C/D classification
├── router_training_data.jsonl # Training data for router
└── router_evaluation.json     # Final metrics
```

---

*Last updated: February 2026*
