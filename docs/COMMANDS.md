# CAB Evaluation Commands Reference

Quick reference for running CodeAssistBench evaluations with Azure OpenAI GPT-5.2.

---

## Setup

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

### Run on Full Dataset

```bash
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --output results/gen_recent.jsonl \
  --concurrency 6 \
  --resume
```

### Run on Python Issues Only

```bash
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language python \
  --output results/gen_python.jsonl \
  --concurrency 6 \
  --resume
```

### Run on Other Languages

```bash
# TypeScript
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language typescript \
  --output results/gen_typescript.jsonl \
  --concurrency 6 \
  --resume

# C/C++
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language c \
  --output results/gen_c.jsonl \
  --concurrency 6 \
  --resume
```

### Run on Non-Docker Issues Only (Rate-Limit Safe)

For S0 tier Azure OpenAI with limited quota, use lower concurrency and exclude Docker issues:

```bash
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language python \
  --output results/gen_python_strands.jsonl \
  --concurrency 2 \
  --limit 100 \
  --no-docker
```

### Run on Docker Issues Only

```bash
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language python \
  --output results/gen_python_docker.jsonl \
  --concurrency 4 \
  --has-dockerfile \
  --resume
```

---

## Evaluation Commands

### Run Judge Evaluation on Generation Results

```bash
python -m cab_evaluation.cli evaluation-dataset \
  results/gen_python.jsonl \
  --agent-models '{"judge": "gpt-5.2"}' \
  --output results/eval_python.jsonl \
  --resume
```

---

## Combined Commands (Generation + Evaluation)

```bash
python -m cab_evaluation.cli dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2", "judge": "gpt-5.2"}' \
  --output results/cab_recent \
  --resume
```

---

## Command Options Reference

### Generation Dataset Options

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| `--output` | `-o` | Output JSONL file path | `generation_results_<timestamp>.jsonl` |
| `--language` | `-l` | Filter by programming language | All languages |
| `--agent-models` | | JSON mapping of agents to models | Required |
| `--concurrency` | `-c` | Number of parallel issues | `1` |
| `--limit` | | Max number of issues to process | All issues |
| `--resume` | | Skip already processed issues | `false` |
| `--max-conversation-rounds` | | Max turns per conversation | `10` |
| `--no-docker` | | Only process issues without Dockerfile | `false` |
| `--has-dockerfile` | | Only process issues with Dockerfile | `false` |
| `--disable-ast-tools` | | Disable AST tools for OpenHands | `false` |

### Agent Models JSON Format

```json
{
  "maintainer": "gpt-5.2",
  "user": "gpt-5.2",
  "judge": "gpt-5.2"
}
```

---

## Concurrency Guidelines

| Concurrency | Use Case | Estimated Time (194 issues) |
|-------------|----------|----------------------------|
| `1` | Debugging, low resources | ~8-10 hours |
| `4` | Conservative | ~2-2.5 hours |
| `6` | Recommended | ~1.5-2 hours |
| `8` | Aggressive | ~1-1.5 hours |

With 4 Azure OpenAI endpoints (30,500 RPM total), `--concurrency 6` is optimal.

---

## Resume Flag Behavior

| Scenario | Without `--resume` | With `--resume` |
|----------|-------------------|-----------------|
| Output file exists | Overwrites | Appends |
| Previous progress | Lost | Preserved |
| Interrupted run | Starts over | Continues |

**Recommendation:** Always use `--resume` for long runs.

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

## Viewing Results

### Generate HTML Viewer

```bash
python -m tools.results_viewer results/gen_python.jsonl --open
```

### View Sample HTML

```bash
explorer.exe results/cab_viewer_clean.html
```

---

## Parallel Processing (Multiple Terminals)

For maximum throughput, run multiple processes by language:

```bash
# Terminal 1
python -m cab_evaluation.cli generation-dataset dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language python --output results/gen_python.jsonl --concurrency 4 --resume

# Terminal 2
python -m cab_evaluation.cli generation-dataset dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language typescript --output results/gen_typescript.jsonl --concurrency 4 --resume

# Terminal 3
python -m cab_evaluation.cli generation-dataset dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language c --output results/gen_c.jsonl --concurrency 4 --resume
```

---

## Troubleshooting

### Authentication Error
```bash
az login
```

### Missing Dependencies
```bash
pip install -r requirements.txt
```

### Rate Limit Errors (429)

If you see `RateLimitReached` or `429` errors:

1. **Reduce concurrency** (most effective):
```bash
--concurrency 2
```

2. **Use lower concurrency with non-Docker issues** (S0 tier safe):
```bash
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language python \
  --output results/gen_python_strands.jsonl \
  --concurrency 2 \
  --limit 100 \
  --no-docker
```

3. **Request quota increase**: https://aka.ms/oai/quotaincrease

| Azure Tier | Recommended Concurrency |
|------------|------------------------|
| S0 (default) | 1-2 |
| S1+ / Higher quota | 4-6 |
| Multiple endpoints | 6-8 |

### Resume Not Working
Check that `--output` path matches the existing file exactly.
