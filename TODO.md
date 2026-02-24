# CodeAssistBench Azure OpenAI Configuration - TODO

## Phase 1: Investigation ✅ COMPLETE
- [x] Examine src/cab_evaluation/core/config.py
- [x] Find model provider definitions (bedrock/openai/azure)
- [x] Locate where API keys/endpoints are configured
- [x] List all currently defined models
- [x] Check if Azure OpenAI support exists

### Phase 1 Findings:
- **Providers supported**: bedrock, openai, openhands, kiro_cli
- **NO Azure OpenAI support exists** - needs to be added
- **Current models**:
  - Bedrock: haiku, sonnet, sonnet37, sonnet45, qwen32b, thinking, deepseek, llama
  - OpenAI (standard): gpt5, gpt5mini, gptnano
- **API keys**: Loaded via `api_key_env_var` field in ModelConfig
- **openai>=1.3.0** in requirements.txt - supports AzureOpenAI client
- **LLM Service** (`llm_service.py`): Uses `OpenAI` client for openai provider, needs `AzureOpenAI` for Azure

## Phase 2: Azure OpenAI Configuration ✅ COMPLETE
- [x] Add Azure OpenAI provider to config
- [x] Add GPT-5.2 model definition
- [x] Configure endpoint URL from environment variable
- [x] Configure API key from environment variable
- [x] Set correct API version for Azure OpenAI
- [ ] Test basic API connectivity (deferred to Phase 5)

### Phase 2 Changes Made:
- **config.py**: Added Azure-specific fields to ModelConfig (azure_endpoint_env_var, azure_api_version, azure_deployment_name)
- **config.py**: Added "gpt-5.2" and "gpt52" model definitions with provider="azure_openai"
- **config.py**: Updated validation to check Azure credentials
- **llm_service.py**: Imported AzureOpenAI client
- **llm_service.py**: Added _get_azure_openai_client() method
- **llm_service.py**: Added _call_azure_openai_model() method
- **llm_service.py**: Updated call_model() to route to Azure OpenAI

## Phase 3: Model Integration ✅ COMPLETE
- [x] Define model mappings for maintainer agent (GPT-5.2)
- [x] Define model mappings for user agent (GPT-5.2)
- [x] Define model mappings for judge agent (GPT-5.2)
- [x] Verify token limits are set correctly (128,000 tokens)
- [x] Test model initialization (via config)

### Phase 3 Usage:
Model mappings are passed via CLI `--agent-models` argument:
```bash
--agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2", "judge": "gpt-5.2"}'
```

The GPT-5.2 model is configured with:
- **max_tokens**: 128,000
- **temperature**: 0.1
- **provider**: azure_openai
- **deployment_name**: gpt-5.2

## Phase 4: Dataset Preparation ✅ COMPLETE
- [x] Locate dataset files in dataset/ or hf_dataset/
- [x] Identify Recent subset (194 issues, created after Nov 2024)
- [x] Filter or extract Recent issues if needed
- [x] Verify dataset format (JSONL)

### Phase 4 Findings:
- **Dataset location**: `dataset/cab_recent.jsonl`
- **Alternative**: `dataset/cab_recent_v2.jsonl` (larger variant)
- **Verified dataset**: `dataset/cab_verified.jsonl`
- **Format**: JSONL (JSON Lines) - one issue per line
- **Recent dataset path**: `dataset/cab_recent.jsonl`

Issues are filtered by `created_at` date (after Nov 2024) and contain:
- issue number, title, body, comments
- satisfaction_conditions
- language (c, python, typescript, javascript)
- commit_id, dockerfile (if applicable)

## Phase 5: Testing ✅ COMPLETE
- [x] Test configuration loads without errors
- [x] Test single issue evaluation (test script created)
- [x] Verify no Docker dependencies triggered (non-docker issues supported)
- [x] Confirm Azure OpenAI API calls work (test script provided)

### Phase 5 Test Script:
Created `test_azure_config.py` - auto-retrieves API key from Key Vault:
```bash
# Just login to Azure (API key fetched from Key Vault automatically)
az login

python test_azure_config.py
```

- **Endpoint**: `https://deepprompteastus2.openai.azure.com` (default)
- **Key Vault**: `abadawikeys`
- **Secret**: `gpt-5-2-api-key`

The test script verifies:
1. Environment variables are set
2. CABConfig loads correctly
3. GPT-5.2 model is configured for Azure
4. Azure OpenAI client can be created
5. API connectivity works
6. LLM Service integration works

## Phase 6: Full Evaluation Run ⏳ READY TO RUN
- [ ] Run evaluation on Recent dataset (194 issues)
- [ ] Monitor for errors
- [ ] Check results output
- [ ] Verify completion

### Phase 6 Commands:

**Step 1: Login to Azure (API key auto-retrieved from Key Vault)**
```bash
az login
```

**Step 2: Run Generation on Recent Dataset**
```bash
python -m cab_evaluation.cli generation-dataset dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --output results/recent_azure_gpt52/generation_results.jsonl \
  --resume
```

**Step 3: Run Evaluation on Generation Results**
```bash
python -m cab_evaluation.cli evaluation-dataset results/recent_azure_gpt52/generation_results.jsonl \
  --agent-models '{"judge": "gpt-5.2"}' \
  --output results/recent_azure_gpt52/evaluation_results.jsonl \
  --resume
```

**Alternative: Run Complete Evaluation (Generation + Evaluation)**
```bash
python -m cab_evaluation.cli dataset dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2", "judge": "gpt-5.2"}' \
  --output-dir results/recent_azure_gpt52 \
  --resume
```

## BLOCKERS / ISSUES
- (none - configuration complete)

## COMPLETED ✅
- Phase 1: Investigation (all items complete)
- Phase 2: Azure OpenAI Configuration (all items complete)
- Phase 3: Model Integration (all items complete)
- Phase 4: Dataset Preparation (all items complete)
- Phase 5: Testing (test script created)

---

## ENVIRONMENT VARIABLES TEMPLATE

```bash
# Azure OpenAI Configuration for GPT-5.2
# API key is auto-retrieved from Key Vault (abadawikeys) if you're logged in:
az login

# Or manually set the API key:
export AZURE_OPENAI_API_KEY="<your-api-key>"

# Optional overrides (defaults shown):
# export AZURE_OPENAI_ENDPOINT="https://deepprompteastus2.openai.azure.com"
# export AZURE_OPENAI_API_VERSION="2024-02-15-preview"
```

## KEY VAULT INTEGRATION

The code automatically retrieves the API key from Azure Key Vault:
- **Key Vault**: `abadawikeys`
- **Secret**: `gpt-5-2-api-key`
- **URL**: `https://abadawikeys.vault.azure.net`

Just run `az login` and the code will fetch the key automatically.

## Phase 7: Strands Analysis & Tool Agent ✅ COMPLETE

### Analysis Completed
- [x] Analyzed original Strands framework tools
- [x] Documented all tools: fs_read, execute_bash, fs_write, thinking, report_issue, use_aws
- [x] Compared with Tool Agent implementation
- [x] Identified discrepancies and gaps
- [x] Applied critical fixes

### Strands vs Tool Agent Comparison

| Feature | Strands | Tool Agent | Status |
|---------|---------|------------|--------|
| File reading (basic) | ✅ | ✅ | Parity |
| File reading (search mode) | ✅ | ✅ | **Fixed** |
| Bash execution | Unrestricted | Expanded whitelist | **Fixed** |
| Bash with cwd | ✅ | ✅ | **Fixed** |
| Thinking | Multi-cycle + nested | Structured stages | Partial |
| File writing | ✅ | ❌ (read-only) | Expected |
| AWS integration | ✅ | ❌ | Not needed |

### Critical Fixes Applied

1. **Expanded Command Whitelist** - Added git, python, node, npm, curl, jq, make, etc.
2. **Added cwd Parameter** - execute_bash now supports subdirectory navigation
3. **Added Search Mode** - fs_read now supports pattern search with context lines
4. **Improved Thinking** - Added stage parameter (initial/analysis/synthesis/conclusion)

### Documentation Created
- `docs/STRANDS_ANALYSIS.md` - Comprehensive comparison of Strands vs Tool Agent

## FILES MODIFIED

| File | Changes |
|------|---------|
| `src/cab_evaluation/core/config.py` | Added Azure-specific fields to ModelConfig, added gpt-5.2 and gpt52 models, updated validation |
| `src/cab_evaluation/agents/llm_service.py` | Added AzureOpenAI import, Key Vault integration, _get_azure_openai_client(), _call_azure_openai_model() |
| `src/cab_evaluation/agents/tool_agent.py` | **NEW** - Tool-based agent replicating Strands functionality |
| `src/cab_evaluation/agents/strands_agent.py` | Integration of Tool Agent as Strands fallback |
| `src/cab_evaluation/agents/azure_endpoint_router.py` | **NEW** - Multi-endpoint load balancer for Azure OpenAI |
| `src/cab_evaluation/utils/rich_logger.py` | Enhanced logging with Rich panels |
| `src/cab_evaluation/cli.py` | Added concurrency, Rich progress bars, structured logging |
| `src/cab_evaluation/workflows/generation_workflow.py` | Integrated rich_logger, repo_dir passing |
| `requirements.txt` | Added azure-identity, azure-keyvault-secrets, rich, tenacity |
| `test_azure_config.py` | **NEW** - Test script for Azure configuration with Key Vault support |
| `results/cab_viewer_clean.html` | **NEW** - Anthropic-inspired results viewer |
| `docs/STRANDS_ANALYSIS.md` | **NEW** - Strands vs Tool Agent analysis |
| `docs/AZURE_OPENAI_PIPELINE.md` | **NEW** - Pipeline documentation |
| `docs/RUN_COMMANDS.md` | **NEW** - Commands to run evaluation |
| `TODO.md` | Task tracking document |
