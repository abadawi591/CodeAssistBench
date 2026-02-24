# CAB Azure OpenAI Configuration via Strands - TODO

## Summary
Use native Strands framework with Azure OpenAI instead of custom Tool Agent.
Priority: Use Strands to avoid introducing errors.

---

## Phase 1: Investigate Strands Azure Support ✅ COMPLETE
- [x] Examine strands_tools model providers
- [x] Confirm Azure OpenAI is supported via OpenAIModel
- [x] Locate where CAB configures Strands models (`strands_agent.py`)
- [x] Check strands-agents package requirements
- [x] Document how to use OpenAIModel with Azure base_url

### Phase 1 Findings:
- **Strands supports Azure OpenAI** via the `OpenAIModel` class with `base_url` in `client_args`
- **Configuration example**:
```python
from strands.models.openai import OpenAIModel

model = OpenAIModel(
    client_args={
        "api_key": os.getenv("AZURE_OPENAI_API_KEY"),
        "base_url": "https://deepprompteastus2.openai.azure.com/openai/deployments/gpt-5.2/",
    },
    model_id="gpt-5.2",  # Azure deployment name
    params={
        "max_tokens": 4096,
        "temperature": 0.1,
    }
)
```
- **Available providers in tools/src/strands_tools/utils/models/model.py**:
  - bedrock, anthropic, litellm, llamaapi, ollama, openai, writer, cohere, github

## Phase 2: Configure Strands for Azure OpenAI ✅ COMPLETE
- [x] Configure OpenAI provider with Azure base_url
- [x] Set up environment variables for Azure
- [x] Key Vault integration for API key retrieval

## Phase 3: Update CAB StrandsAgent ✅ COMPLETE
- [x] Added `_is_azure_model()` method to detect Azure models
- [x] Added `_get_azure_openai_config()` method for Azure config
- [x] Modified `_build_strands_agent()` to use OpenAIModel for Azure
- [x] Preserved all existing Strands tools (fs_read, execute_bash, thinking, etc.)
- [x] Custom Tool Agent remains as final fallback if Strands fails

### Changes Made to `strands_agent.py`:
```python
# New methods added:
- _is_azure_model(model_name) -> bool
- _get_azure_openai_config(model_name) -> dict

# Modified _build_strands_agent():
if self._is_azure_model(self.model_name):
    # Use Strands OpenAIModel with Azure base_url
    from strands.models.openai import OpenAIModel
    model = OpenAIModel(
        client_args={
            "api_key": azure_config["api_key"],
            "base_url": f"{endpoint}/openai/deployments/{deployment}/",
            "default_headers": {"api-key": api_key},
            "default_query": {"api-version": api_version}
        },
        model_id=deployment_name,
        params={"max_completion_tokens": 4096, "temperature": 0.1}
    )
else:
    # Use Strands BedrockModel for AWS models
    model = BedrockModel(...)
```

## Phase 4: Environment Setup ✅ COMPLETE
- [x] Configure Key Vault integration (auto-retrieves from abadawikeys)
- [x] AZURE_OPENAI_ENDPOINT defaults to deepprompteastus2.openai.azure.com
- [x] AZURE_OPENAI_API_KEY from Key Vault or environment
- [x] Deployment name mapping (gpt-5.2, gpt52 → gpt-5.2)

## Phase 5: Testing ⏳ READY
- [ ] Test Strands Agent with Azure OpenAI
- [ ] Verify tools work (fs_read, execute_bash, thinking)
- [ ] Test single issue evaluation
- [ ] Compare results with custom Tool Agent

## Phase 6: Full Evaluation ⏳ READY
- [ ] Run on Recent dataset (Python subset)
- [ ] Monitor for errors
- [ ] Generate HTML viewer results

---

## Key Configuration

### Azure Endpoint Format for OpenAI SDK
```
https://{resource-name}.openai.azure.com/openai/deployments/{deployment-name}/
```

### Environment Variables
```bash
# Azure OpenAI
export AZURE_OPENAI_ENDPOINT="https://deepprompteastus2.openai.azure.com"
export AZURE_OPENAI_API_KEY="<from-key-vault>"
export AZURE_OPENAI_API_VERSION="2024-12-01-preview"

# Strands configuration (if needed)
export STRANDS_PROVIDER="openai"  # Use OpenAI provider for Azure
export STRANDS_MODEL_ID="gpt-5.2"
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/cab_evaluation/agents/strands_agent.py` | Use OpenAIModel for Azure |
| `tools/src/strands_tools/utils/models/model.py` | Add azure_openai provider |
| `src/cab_evaluation/core/config.py` | Add Strands-compatible Azure model |

---

## Priority Hierarchy (What Gets Used)

```
┌─────────────────────────────────────────────────────────────────┐
│                    MODEL SELECTION FLOW                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Is model Azure OpenAI (gpt-5.2)?                               │
│      │                                                          │
│      ├─ YES → Use Strands OpenAIModel with Azure base_url       │
│      │        ✓ All Strands tools available                     │
│      │        ✓ Native tool orchestration                       │
│      │        ✓ No custom code                                  │
│      │                                                          │
│      └─ NO → Is AWS Bedrock available?                          │
│              │                                                  │
│              ├─ YES → Use Strands BedrockModel                  │
│              │        ✓ All Strands tools available             │
│              │                                                  │
│              └─ NO → Use custom Tool Agent (fallback)           │
│                      ⚠ Limited functionality                    │
│                      ⚠ Custom implementation                    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Benefits of Using Native Strands

1. **Battle-tested tools** - fs_read, execute_bash, thinking all work natively
2. **Proper tool orchestration** - Multi-turn tool calls handled correctly
3. **Prompt caching** - Strands has built-in caching support
4. **No custom code risk** - Avoids introducing bugs in custom Tool Agent
5. **Future compatibility** - Updates to Strands will work automatically

---

## Test Command

```bash
cd ~/CodeAssistBench

# Clear cache
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
pip install -e . --force-reinstall --no-deps

# Login to Azure (for Key Vault)
az login

# Run test
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language python \
  --output results/gen_python_strands.jsonl \
  --concurrency 5 \
  --resume
```

Watch for this log message to confirm Strands Azure is working:
```
INFO - Created Strands OpenAIModel for Azure: gpt-5.2
```
