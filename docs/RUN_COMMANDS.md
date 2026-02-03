# CAB Evaluation Run Commands

Quick reference for running CodeAssistBench evaluations with Azure OpenAI GPT-5.2.

---

## Prerequisites

```bash
# Navigate to project
cd ~/CodeAssistBench

# Activate virtual environment
source .venv/bin/activate

# Authenticate with Azure (for Key Vault access)
az login
```

---

## Generation Commands

### Run All Issues (Full Dataset)

```bash
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --output results/gen_recent.jsonl \
  --concurrency 6 \
  --resume
```

### Run by Language

**Python only:**
```bash
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language python \
  --output results/gen_python.jsonl \
  --concurrency 6 \
  --resume
```

**TypeScript only:**
```bash
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language typescript \
  --output results/gen_typescript.jsonl \
  --concurrency 6 \
  --resume
```

**C/C++ only:**
```bash
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language c \
  --output results/gen_c.jsonl \
  --concurrency 6 \
  --resume
```

---

## Evaluation Commands

After generation completes, run evaluation (judge):

```bash
python -m cab_evaluation.cli evaluation-dataset \
  results/gen_python.jsonl \
  --agent-models '{"judge": "gpt-5.2"}' \
  --output results/eval_python.jsonl \
  --resume
```

---

## CLI Options Reference

| Option | Description | Default |
|--------|-------------|---------|
| `--agent-models` | JSON mapping of agent roles to models | Required |
| `--output`, `-o` | Output JSONL file path | Auto-generated |
| `--language`, `-l` | Filter by programming language | All |
| `--concurrency`, `-c` | Number of parallel issues | 1 |
| `--resume` | Skip already-processed issues | False |
| `--config` | Path to config file | None |

---

## Concurrency Guidelines

| Concurrency | Parallel Issues | Estimated Time (194 issues) |
|-------------|-----------------|----------------------------|
| `--concurrency 1` | Sequential | ~8-10 hours |
| `--concurrency 4` | 4 at once | ~2-2.5 hours |
| `--concurrency 6` | 6 at once | ~1.5-2 hours |
| `--concurrency 8` | 8 at once | ~1-1.5 hours |

**Recommended:** `--concurrency 6` for good balance of speed and stability.

---

## Multi-Endpoint Router

The system automatically load-balances across 4 Azure OpenAI endpoints:

| Region | Endpoint | RPM | Priority |
|--------|----------|-----|----------|
| East US 2 | deepprompteastus2.openai.azure.com | 10,000 | 1 |
| South Central US | deeppromptsouthcentralus.openai.azure.com | 10,000 | 2 |
| Sweden Central | deeppromptswedencentral.openai.azure.com | 8,500 | 3 |
| East US 2 (#4) | deepprompteastus2.openai.azure.com | 2,000 | 4 |
| **Total** | | **30,500** | |

Features:
- Automatic failover on errors
- Weighted load balancing by capacity
- Rate limit awareness with backoff

---

## View Results

### Generate HTML Viewer

```bash
python -m tools.results_viewer results/gen_python.jsonl --open
```

### Or manually open the demo:

```bash
explorer.exe results/cab_viewer_clean.html
```

---

## Testing Commands

### Test Azure Configuration

```bash
python test_azure_config.py
```

### Test Multi-Endpoint Router

```bash
python test_azure_router.py
```

---

## Example Full Workflow

```bash
# 1. Setup
cd ~/CodeAssistBench
source .venv/bin/activate
az login

# 2. Test configuration
python test_azure_config.py

# 3. Run generation on Python issues
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language python \
  --output results/gen_python.jsonl \
  --concurrency 6 \
  --resume

# 4. Run evaluation (judge)
python -m cab_evaluation.cli evaluation-dataset \
  results/gen_python.jsonl \
  --agent-models '{"judge": "gpt-5.2"}' \
  --output results/eval_python.jsonl \
  --resume

# 5. View results
python -m tools.results_viewer results/eval_python.jsonl --open
```

---

## Troubleshooting

### "az login" required
```bash
az login
```

### Rate limit errors
- Reduce `--concurrency` to 4 or lower
- The router will automatically failover to other endpoints

### Resume not working
- Ensure output file path matches exactly
- Check that issue IDs in output match input dataset

### View router stats
```python
from cab_evaluation.agents.azure_endpoint_router import get_router
router = get_router()
print(router.get_stats())
```
