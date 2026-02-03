# CodeAssistBench Azure OpenAI Pipeline Guide

This guide documents the end-to-end pipeline for running CodeAssistBench evaluations using **Azure OpenAI GPT-5.2**.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        CodeAssistBench Pipeline                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                │
│   │   Dataset    │     │  Generation  │     │  Evaluation  │                │
│   │   (JSONL)    │────▶│   Workflow   │────▶│   Workflow   │                │
│   │              │     │              │     │              │                │
│   │ cab_recent.  │     │ Maintainer + │     │    Judge     │                │
│   │   jsonl      │     │    User      │     │    Agent     │                │
│   └──────────────┘     └──────────────┘     └──────────────┘                │
│                               │                    │                         │
│                               ▼                    ▼                         │
│                  ┌───────────────────────────────────────────┐              │
│                  │       Azure OpenAI Endpoint Router        │              │
│                  │  ┌─────────┬─────────┬─────────┬────────┐ │              │
│                  │  │East US 2│South    │Sweden   │East US │ │              │
│                  │  │(Primary)│Central  │Central  │2 (#4)  │ │              │
│                  │  │10K RPM  │10K RPM  │8.5K RPM │2K RPM  │ │              │
│                  │  └─────────┴─────────┴─────────┴────────┘ │              │
│                  │         Total: 30,500 RPM                 │              │
│                  └───────────────────────────────────────────┘              │
│                                       │                                      │
│                                       ▼                                      │
│                        ┌─────────────────────────────────┐                  │
│                        │      Azure Key Vault            │                  │
│                        │       (abadawikeys)             │                  │
│                        │    4 API Key Secrets            │                  │
│                        └─────────────────────────────────┘                  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Multi-Endpoint Router

The pipeline uses an intelligent endpoint router that distributes requests across 4 Azure OpenAI deployments for maximum throughput and reliability.

### Endpoint Configuration

| Region           | Endpoint                                  | Deployment  | RPM    | TPM       | Priority |
|------------------|-------------------------------------------|-------------|--------|-----------|----------|
| East US 2        | deepprompteastus2.openai.azure.com        | gpt-5.2     | 10,000 | 1,000,000 | 1        |
| South Central US | deeppromptsouthcentralus.openai.azure.com | gpt-5.2_2   | 10,000 | 1,000,000 | 2        |
| Sweden Central   | deeppromptswedencentral.openai.azure.com  | gpt-5.2     | 8,500  | 850,000   | 3        |
| East US 2 (#4)   | deepprompteastus2.openai.azure.com        | gpt-5.2-4   | 2,000  | 200,000   | 4        |
| **TOTAL**        |                                           |             | **30,500** | **3,050,000** |    |

### Router Features

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Endpoint Router Architecture                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────────────┐                                                       │
│   │  API Request    │                                                       │
│   └────────┬────────┘                                                       │
│            │                                                                 │
│            ▼                                                                 │
│   ┌─────────────────────────────────────────────────────────────┐           │
│   │              WEIGHTED LOAD BALANCER                          │           │
│   │  ┌─────────────────────────────────────────────────────┐    │           │
│   │  │ • Priority-based routing (lower = higher priority)  │    │           │
│   │  │ • Capacity-weighted selection (RPM-based)           │    │           │
│   │  │ • Health-aware (avoid unhealthy endpoints)          │    │           │
│   │  └─────────────────────────────────────────────────────┘    │           │
│   └─────────────────────┬───────────────────────────────────────┘           │
│                         │                                                    │
│         ┌───────────────┼───────────────┬───────────────┐                   │
│         ▼               ▼               ▼               ▼                   │
│   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐                │
│   │ Endpoint │   │ Endpoint │   │ Endpoint │   │ Endpoint │                │
│   │    1     │   │    2     │   │    3     │   │    4     │                │
│   │ (33%)    │   │ (33%)    │   │ (28%)    │   │ (6%)     │                │
│   └──────────┘   └──────────┘   └──────────┘   └──────────┘                │
│         │               │               │               │                   │
│         └───────────────┴───────┬───────┴───────────────┘                   │
│                                 │                                            │
│                                 ▼                                            │
│   ┌─────────────────────────────────────────────────────────────┐           │
│   │                    FAILOVER LOGIC                            │           │
│   │  ┌─────────────────────────────────────────────────────┐    │           │
│   │  │ If endpoint fails:                                   │    │           │
│   │  │   1. Mark endpoint as degraded/unhealthy            │    │           │
│   │  │   2. Retry on next best endpoint                    │    │           │
│   │  │   3. Up to 4 attempts (one per endpoint)            │    │           │
│   │  │   4. Tenacity retry with exponential backoff        │    │           │
│   │  └─────────────────────────────────────────────────────┘    │           │
│   └─────────────────────────────────────────────────────────────┘           │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Testing the Router

```bash
# Test router initialization and API connectivity
python test_azure_router.py
```

---

## Pipeline Stages

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│  STAGE 1: SETUP              STAGE 2: GENERATION         STAGE 3: EVALUATION│
│  ─────────────────           ───────────────────         ───────────────────│
│                                                                              │
│  ┌─────────────┐             ┌─────────────┐             ┌─────────────┐    │
│  │ az login    │             │ Maintainer  │             │   Judge     │    │
│  │             │             │   Agent     │             │   Agent     │    │
│  │ venv setup  │────────────▶│     ↕       │────────────▶│             │    │
│  │             │             │ User Agent  │             │  Scores &   │    │
│  │ pip install │             │             │             │  Verdicts   │    │
│  └─────────────┘             └─────────────┘             └─────────────┘    │
│        │                           │                           │            │
│        ▼                           ▼                           ▼            │
│  ┌─────────────┐             ┌─────────────┐             ┌─────────────┐    │
│  │ test_azure_ │             │ generation_ │             │ evaluation_ │    │
│  │ config.py   │             │ results.    │             │ results.    │    │
│  │   (verify)  │             │   jsonl     │             │   jsonl     │    │
│  └─────────────┘             └─────────────┘             └─────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Detailed Agent Interaction Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Generation Workflow Detail                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│    GitHub Issue                                                              │
│    ┌─────────────────────────────────────────────────┐                      │
│    │ Issue #1234: "How to configure port settings?"  │                      │
│    │ Body: "I want to run multiple agents..."        │                      │
│    │ Satisfaction Conditions: [...]                  │                      │
│    └─────────────────────────────────────────────────┘                      │
│                           │                                                  │
│                           ▼                                                  │
│    ┌─────────────────────────────────────────────────┐                      │
│    │              MAINTAINER AGENT (GPT-5.2)         │                      │
│    │  "You can set SERVER_PORT in .env file..."      │                      │
│    └─────────────────────────────────────────────────┘                      │
│                           │                                                  │
│                           ▼                                                  │
│    ┌─────────────────────────────────────────────────┐                      │
│    │                USER AGENT (GPT-5.2)             │                      │
│    │  Simulates user follow-up questions             │                      │
│    │  "Does this work with Docker?"                  │                      │
│    └─────────────────────────────────────────────────┘                      │
│                           │                                                  │
│                           ▼                                                  │
│    ┌───────────────────────────────────────────────────────────────────┐    │
│    │                    CONVERSATION LOOP                               │    │
│    │  ┌──────────┐      ┌──────────┐      ┌──────────┐                 │    │
│    │  │ Round 1  │─────▶│ Round 2  │─────▶│ Round N  │                 │    │
│    │  │ M ↔ U    │      │ M ↔ U    │      │ M ↔ U    │                 │    │
│    │  └──────────┘      └──────────┘      └──────────┘                 │    │
│    │                (max_conversation_rounds)                           │    │
│    └───────────────────────────────────────────────────────────────────┘    │
│                           │                                                  │
│                           ▼                                                  │
│    ┌─────────────────────────────────────────────────┐                      │
│    │              GENERATION RESULT                  │                      │
│    │  - final_answer                                 │                      │
│    │  - conversation_history                         │                      │
│    │  - user_satisfied (True/False)                  │                      │
│    └─────────────────────────────────────────────────┘                      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘


┌─────────────────────────────────────────────────────────────────────────────┐
│                        Evaluation Workflow Detail                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│    Generation Result                                                         │
│    ┌─────────────────────────────────────────────────┐                      │
│    │ - Issue context                                 │                      │
│    │ - Maintainer's answer                           │                      │
│    │ - Conversation history                          │                      │
│    │ - Satisfaction conditions                       │                      │
│    └─────────────────────────────────────────────────┘                      │
│                           │                                                  │
│                           ▼                                                  │
│    ┌─────────────────────────────────────────────────┐                      │
│    │               JUDGE AGENT (GPT-5.2)             │                      │
│    │                                                  │                      │
│    │  Evaluates against satisfaction conditions:     │                      │
│    │  ┌─────────────────────────────────────────┐   │                      │
│    │  │ Condition 1: ✅ Satisfied                │   │                      │
│    │  │ Condition 2: ✅ Satisfied                │   │                      │
│    │  │ Condition 3: ❌ Not Satisfied            │   │                      │
│    │  └─────────────────────────────────────────┘   │                      │
│    └─────────────────────────────────────────────────┘                      │
│                           │                                                  │
│                           ▼                                                  │
│    ┌─────────────────────────────────────────────────┐                      │
│    │              EVALUATION RESULT                  │                      │
│    │                                                  │                      │
│    │  verdict: CORRECT | PARTIALLY_CORRECT |         │                      │
│    │           INCORRECT | ERROR                     │                      │
│    │                                                  │                      │
│    │  alignment_score: 2/3 (66.7%)                   │                      │
│    │  judgment: "The response addressed..."          │                      │
│    │  key_issues: [...]                              │                      │
│    └─────────────────────────────────────────────────┘                      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Azure OpenAI Integration

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Azure OpenAI Authentication Flow                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌──────────────┐                                                          │
│   │   az login   │                                                          │
│   │              │                                                          │
│   └──────┬───────┘                                                          │
│          │                                                                   │
│          ▼                                                                   │
│   ┌──────────────────────────────────────────────────────────────────┐      │
│   │                    DefaultAzureCredential                         │      │
│   │                                                                   │      │
│   │  Tries in order:                                                  │      │
│   │  1. Environment variables                                         │      │
│   │  2. Managed Identity                                              │      │
│   │  3. Azure CLI credentials  ◄── (used when you run `az login`)    │      │
│   │  4. Azure PowerShell                                              │      │
│   │  5. Interactive browser                                           │      │
│   └──────────────────────────────────────────────────────────────────┘      │
│          │                                                                   │
│          ▼                                                                   │
│   ┌──────────────────────────────────────────────────────────────────┐      │
│   │                      Azure Key Vault                              │      │
│   │                                                                   │      │
│   │  URL: https://abadawikeys.vault.azure.net                        │      │
│   │                                                                   │      │
│   │  Secrets:                                                         │      │
│   │  ┌────────────────────────────┬──────────┐                       │      │
│   │  │ gpt-5-2-api-key           │ Enabled  │ ◄── Primary            │      │
│   │  │ gpt-5-2-number-2-api-key  │ Enabled  │                        │      │
│   │  │ gpt-5-2-number-3-api-key  │ Enabled  │                        │      │
│   │  │ gpt-5-2-number-4-api-key  │ Enabled  │                        │      │
│   │  │ text-embedding-3-large-*  │ Enabled  │                        │      │
│   │  └────────────────────────────┴──────────┘                       │      │
│   └──────────────────────────────────────────────────────────────────┘      │
│          │                                                                   │
│          ▼                                                                   │
│   ┌──────────────────────────────────────────────────────────────────┐      │
│   │                    Azure OpenAI Service                           │      │
│   │                                                                   │      │
│   │  Endpoint: https://deepprompteastus2.openai.azure.com            │      │
│   │  Region:   East US 2                                              │      │
│   │  Model:    gpt-5.2                                                │      │
│   │  API Ver:  2024-02-15-preview                                     │      │
│   │                                                                   │      │
│   │  Limits:                                                          │      │
│   │  ├─ TPM: 1,000,000 tokens/minute                                  │      │
│   │  └─ RPM: 10,000 requests/minute                                   │      │
│   └──────────────────────────────────────────────────────────────────┘      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Step-by-Step Setup

### Step 1: Clone and Setup Environment

```bash
# Clone the repository
git clone https://github.com/abadawi591/CodeAssistBench.git
cd CodeAssistBench

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install -e .
```

### Step 2: Azure Authentication

```bash
# Login to Azure (for Key Vault access)
az login

# Verify access (optional)
az keyvault secret show --vault-name abadawikeys --name gpt-5-2-api-key --query value -o tsv
```

### Step 3: Verify Configuration

```bash
# Run the test script to verify everything works
python test_azure_config.py
```

Expected output:
```
============================================================
  TEST SUMMARY
============================================================
  ✅ PASS: Environment Variables
  ✅ PASS: Config Loading
  ✅ PASS: GPT-5.2 Model
  ✅ PASS: Azure Client Creation
  ✅ PASS: API Connectivity
  ✅ PASS: LLM Service Integration

  Total: 6 passed, 0 failed

🎉 All tests passed! Azure OpenAI is configured correctly.
```

### Step 4: Run Generation Workflow

```bash
# Generate maintainer responses for the Recent dataset
python -m cab_evaluation.cli generation-dataset dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --output results/generation_gpt52.jsonl \
  --resume
```

### Step 5: Run Evaluation Workflow

```bash
# Evaluate the generated responses
python -m cab_evaluation.cli evaluation-dataset results/generation_gpt52.jsonl \
  --agent-models '{"judge": "gpt-5.2"}' \
  --output results/evaluation_gpt52.jsonl \
  --resume
```

### Step 6: Analyze Results

```python
import json
from collections import Counter

# Load evaluation results
with open('results/evaluation_gpt52.jsonl', 'r') as f:
    results = [json.loads(line) for line in f]

# Count verdicts
verdicts = Counter(r['verdict'] for r in results)
print(f"Total: {len(results)}")
print(f"CORRECT: {verdicts['CORRECT']} ({verdicts['CORRECT']/len(results)*100:.1f}%)")
print(f"PARTIALLY_CORRECT: {verdicts['PARTIALLY_CORRECT']}")
print(f"INCORRECT: {verdicts['INCORRECT']}")
print(f"ERROR: {verdicts.get('ERROR', 0)}")
```

---

## File Structure

```
CodeAssistBench/
│
├── dataset/
│   ├── cab_recent.jsonl          # 308 recent issues (June 2025 - Jan 2026)
│   ├── cab_recent_v2.jsonl       # 771 issues (extended dataset)
│   └── cab_verified.jsonl        # 149 verified issues with Dockerfiles
│
├── src/cab_evaluation/
│   ├── agents/
│   │   ├── llm_service.py        # Azure OpenAI + Key Vault integration
│   │   ├── maintainer_agent.py   # Maintainer agent implementation
│   │   ├── user_agent.py         # User agent implementation
│   │   └── judge_agent.py        # Judge agent implementation
│   │
│   ├── core/
│   │   ├── config.py             # Model configurations (including gpt-5.2)
│   │   └── models.py             # Data models
│   │
│   ├── workflows/
│   │   ├── generation_workflow.py
│   │   └── evaluation_workflow.py
│   │
│   └── cli.py                    # Command-line interface
│
├── results/                       # Output directory for results
│   ├── generation_gpt52.jsonl
│   └── evaluation_gpt52.jsonl
│
├── test_azure_config.py          # Azure configuration test script
├── requirements.txt              # Dependencies (including azure-*)
└── TODO.md                       # Task tracking
```

---

## Model Configuration

| Model | Provider | Endpoint | Max Tokens | Temperature |
|-------|----------|----------|------------|-------------|
| gpt-5.2 | azure_openai | deepprompteastus2.openai.azure.com | 128,000 | 0.1 |
| gpt52 | azure_openai | (alias for gpt-5.2) | 128,000 | 0.1 |
| haiku | bedrock | AWS Bedrock | 120,000 | 0.0 |
| sonnet37 | bedrock | AWS Bedrock | 120,000 | 0.0 |

---

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| `Missing environment variable: AZURE_OPENAI_API_KEY` | Run `az login` to authenticate with Azure |
| `Failed to retrieve secret from Key Vault` | Ensure you have access to the `abadawikeys` Key Vault |
| `max_tokens is not supported` | Fixed: Code uses `max_completion_tokens` for GPT-5.2 |
| `Creating minimal config for OpenHands model` | Fixed: Config now checks models dict first |

### Checking Logs

```bash
# Enable debug logging
python -m cab_evaluation.cli generation-dataset dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --log-level DEBUG \
  --log-file debug.log
```

---

## Quick Reference Commands

```bash
# Full pipeline in one go
az login && \
python -m cab_evaluation.cli generation-dataset dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --output results/gen.jsonl --resume && \
python -m cab_evaluation.cli evaluation-dataset results/gen.jsonl \
  --agent-models '{"judge": "gpt-5.2"}' \
  --output results/eval.jsonl --resume
```

---

## Contact & Resources

- **Repository**: https://github.com/abadawi591/CodeAssistBench
- **Upstream**: https://github.com/amazon-science/CodeAssistBench
- **Azure Portal**: https://portal.azure.com
- **Key Vault**: https://abadawikeys.vault.azure.net
