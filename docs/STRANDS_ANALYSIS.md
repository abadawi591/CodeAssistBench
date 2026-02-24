# Strands vs. Tool Agent Analysis

## Executive Summary

This document provides a thorough analysis of the original AWS Strands framework tools and compares them to our Azure OpenAI-based Tool Agent implementation. The goal is to identify discrepancies, missing features, and potential issues.

---

## 1. Original Strands Framework Overview

### What is Strands?

Strands is an AWS-proprietary agentic framework that provides:
- Model integration via AWS Bedrock (Claude, DeepSeek, Llama, etc.)
- Tool orchestration with automatic function calling
- Prompt caching for efficiency
- Hooks system for extensibility

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         STRANDS FRAMEWORK                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────┐     ┌──────────────────────────────────────────┐   │
│  │   Strands Agent     │     │        Bedrock Model                      │   │
│  │  ─────────────────  │     │   ───────────────────────                 │   │
│  │  • System Prompt    │◄───►│  • Claude/DeepSeek/Llama                  │   │
│  │  • Tool Registry    │     │  • Prompt Caching                         │   │
│  │  • Hooks (Cache)    │     │  • Temperature=0 (deterministic)          │   │
│  │  • Message History  │     │  • max_retries=1000                       │   │
│  └─────────────────────┘     └──────────────────────────────────────────┘   │
│              │                                                               │
│              ▼                                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                       TOOLS (strands_tools)                            │  │
│  ├───────────────────────────────────────────────────────────────────────┤  │
│  │                                                                         │  │
│  │  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  ┌───────────┐  │  │
│  │  │  fs_read    │  │ execute_bash │  │   fs_write     │  │  thinking │  │  │
│  │  │  ─────────  │  │  ──────────  │  │  ──────────    │  │  ───────  │  │  │
│  │  │  • Line     │  │  • Any cmd   │  │  • Create/edit │  │  • Nested │  │  │
│  │  │  • Directory│  │  • Any cwd   │  │  • Confirm     │  │    Agent  │  │  │
│  │  │  • Search   │  │  • Unrestrict│  │                │  │  • Cycles │  │  │
│  │  │  • Image    │  │              │  │                │  │  • Models │  │  │
│  │  └─────────────┘  └──────────────┘  └────────────────┘  └───────────┘  │  │
│  │                                                                         │  │
│  │  ┌─────────────┐  ┌──────────────┐                                     │  │
│  │  │ report_issue│  │   use_aws    │                                     │  │
│  │  │ ──────────  │  │  ──────────  │                                     │  │
│  │  │ • GitHub    │  │  • boto3     │                                     │  │
│  │  │ • Browser   │  │  • Any svc   │                                     │  │
│  │  └─────────────┘  └──────────────┘                                     │  │
│  │                                                                         │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Detailed Tool Comparison

### 2.1 File Reading (fs_read)

#### Original Strands `fs_read`

```python
# TOOL_SPEC from strands_tools/fs_read.py
{
    "name": "fs_read",
    "description": "Tool for reading files, directories and images...",
    "inputSchema": {
        "properties": {
            "path": {"type": "string"},
            "mode": {"enum": ["Line", "Directory", "Search", "Image"]},
            "start_line": {"type": "integer", "default": 1},
            "end_line": {"type": "integer", "default": -1},
            "pattern": {"type": "string"},  # For Search mode
            "context_lines": {"type": "integer", "default": 2},
            "depth": {"type": "integer", "default": 0}  # For Directory mode
        },
        "required": ["mode"]
    }
}
```

**Features:**
| Feature | Strands | Tool Agent | Notes |
|---------|---------|------------|-------|
| Line mode (read file) | ✅ | ✅ | Both support line ranges |
| Directory mode (ls -la) | ✅ | ✅ (separate tool) | Strands has depth recursion |
| Search mode (grep) | ✅ | ✅ (via execute_bash) | Strands: built-in with context |
| Image mode (base64) | ✅ | ❌ | **MISSING** - read images |
| Negative line indices | ✅ | ❌ | **MISSING** - count from EOF |
| 64KB size guard | ✅ | ✅ | Both truncate large files |
| Symlink handling | ✅ | ❌ | **MISSING** - explicit handling |

#### Our Tool Agent `fs_read`

```python
{
    "name": "fs_read",
    "parameters": {
        "properties": {
            "file_path": {"type": "string"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"}
        },
        "required": ["file_path"]
    }
}
```

**Discrepancies:**
1. **No Search mode** - We rely on `execute_bash` for grep
2. **No Image mode** - Cannot read binary/image files
3. **No negative line indices** - Cannot specify "last 50 lines"
4. **No depth parameter** - Directory listing is separate tool
5. **Different max size** - 100KB vs 64KB

---

### 2.2 Bash Execution (execute_bash/executeBash)

#### Original Strands `executeBash`

```python
{
    "name": "executeBash",
    "inputSchema": {
        "properties": {
            "explanation": {"type": "string"},  # Why running this
            "command": {"type": "string"},
            "cwd": {"type": "string"}  # Working directory
        },
        "required": ["command", "cwd"]
    }
}
```

**Features:**
- **Unrestricted execution** - Any command allowed
- **Working directory** - Explicit `cwd` parameter
- **Explanation tracking** - Documents reasoning
- **No whitelist** - Full shell access

#### Our Tool Agent `execute_bash`

```python
{
    "name": "execute_bash",
    "parameters": {
        "properties": {
            "command": {"type": "string"}
        },
        "required": ["command"]
    }
}
```

**Discrepancies:**
| Feature | Strands | Tool Agent | Impact |
|---------|---------|------------|--------|
| Command whitelist | ❌ (unrestricted) | ✅ (whitelist) | **HIGH** - Limits exploration |
| cwd parameter | ✅ | ❌ (uses repo_dir) | **MEDIUM** - Less flexible |
| explanation field | ✅ | ❌ | LOW - Logging only |
| Any bash command | ✅ | ❌ | **HIGH** - Cannot run arbitrary |
| Write operations | ✅ | ❌ (read-only) | Expected in read-only mode |

**Whitelist in Tool Agent:**
```python
SAFE_COMMANDS = [
    'grep', 'find', 'ls', 'cat', 'head', 'tail', 'wc', 'sort', 'uniq',
    'tree', 'file', 'stat', 'du', 'pwd', 'echo', 'awk', 'sed', 'cut',
    'diff', 'comm', 'tr', 'xargs', 'basename', 'dirname', 'realpath'
]
```

**Commands NOT in whitelist that might be useful:**
- `git` - For version control operations
- `python` - For running scripts
- `pip` - For checking dependencies  
- `npm`/`yarn` - For JS projects
- `make` - For build systems

---

### 2.3 Thinking Tool

#### Original Strands `think`

```python
@tool
def think(
    thought: str,
    cycle_count: int,           # Number of thinking cycles (1-10)
    system_prompt: str,         # Custom system prompt
    tools: Optional[List[str]], # Tools available to thinking agent
    model_provider: Optional[str],  # "bedrock", "anthropic", etc.
    model_settings: Optional[Dict],
    thinking_system_prompt: Optional[str],  # HOW to think
    agent: Optional[Any]
) -> Dict[str, Any]:
```

**Features:**
- **Multi-cycle thinking** - Iterative refinement (up to 10 cycles)
- **Nested agent** - Creates new agent for thinking
- **Model switching** - Can use different models for thinking
- **Tool inheritance** - Thinking agent can use parent's tools
- **Recursion prevention** - Excludes `think` from nested agent

#### Our Tool Agent `thinking`

```python
{
    "name": "thinking",
    "parameters": {
        "properties": {
            "thought": {"type": "string"}
        },
        "required": ["thought"]
    }
}
```

**Discrepancies:**
| Feature | Strands | Tool Agent | Impact |
|---------|---------|------------|--------|
| Multi-cycle | ✅ (1-10 cycles) | ❌ (single record) | **HIGH** - Less deep reasoning |
| Nested agent | ✅ | ❌ | **HIGH** - No recursive analysis |
| Model switching | ✅ | ❌ | MEDIUM - Same model throughout |
| Tool access | ✅ | ❌ | **HIGH** - Cannot explore during think |

**Our implementation is essentially a no-op:**
```python
def thinking(self, thought: str) -> ToolResult:
    """Record a thinking step."""
    return ToolResult(True, f"[Thinking recorded]: {thought}", "thinking")
```

---

### 2.4 Additional Strands Tools (Not Implemented)

#### `fs_write` (File Write)
- Create/edit files
- User confirmation required
- Syntax highlighting preview
- Directory creation

**Our Implementation:** ❌ Not implemented (read-only mode)

#### `report_issue` (GitHub Issue)
- Opens browser with pre-filled issue
- Includes conversation transcript
- Context information

**Our Implementation:** ❌ Not implemented (not needed for CAB)

#### `use_aws` (AWS Services)
- Access any boto3 service
- Parameter validation
- Mutative operation confirmation

**Our Implementation:** ❌ Not implemented (not needed for CAB)

---

## 3. Integration Analysis

### How Strands is Used in CAB

```python
# In strands_agent.py
def _build_strands_agent(self, system_prompt: str):
    from strands import Agent
    from strands.models import BedrockModel
    from strands.hooks import HookProvider, MessageAddedEvent
    from tools.src.strands_tools import (
        execute_bash, fs_read, fs_write, report_issue, use_aws, thinking
    )
    
    # Read-only mode tools
    if self.read_only:
        tools = [execute_bash, fs_read, thinking]  # Limited
    else:
        tools = [execute_bash, fs_read, fs_write, report_issue, use_aws, thinking]
    
    strands_agent = Agent(
        model=BedrockModel(...),
        system_prompt=system_prompt,
        tools=tools,
        hooks=[cache_hook]
    )
```

### Our Fallback Integration

```python
# In strands_agent.py - when Strands unavailable
async def call_llm(self, user_prompt, system_prompt, issue_id, **kwargs):
    repo_dir = kwargs.get('repo_dir', self._current_repo_dir)
    
    if repo_dir and not self._strands_available:
        return await self._run_tool_agent(user_prompt, system_prompt, repo_dir, issue_id)
    
    return await super().call_llm(user_prompt, system_prompt, issue_id, **kwargs)
```

---

## 4. Critical Discrepancies

### HIGH Priority Issues

#### 1. Command Whitelist Too Restrictive

**Problem:** Strands allows ANY command, our whitelist blocks many useful ones.

**Impact:** Model cannot:
- Run `git log` to see commit history
- Run `python -c "..."` to test code
- Run `curl` to test endpoints
- Run `jq` to parse JSON

**Fix:** Expand whitelist or add `--unrestricted` flag:
```python
SAFE_COMMANDS = [
    # Current list...
    'git', 'python', 'python3', 'pip', 'npm', 'yarn',
    'curl', 'wget', 'jq', 'make', 'which', 'type',
    'env', 'printenv', 'test', 'expr', 'true', 'false'
]
```

#### 2. Thinking Tool is Non-functional

**Problem:** Our `thinking` just logs - doesn't enable deep reasoning.

**Impact:** Model cannot:
- Perform multi-step analysis
- Use tools during reasoning
- Iteratively refine understanding

**Fix:** Implement proper thinking cycles:
```python
async def thinking(self, thought: str, cycles: int = 3) -> ToolResult:
    """Perform multi-cycle thinking."""
    current = thought
    for i in range(cycles):
        # Let model reason about previous thought
        response = await self.client.chat.completions.create(
            model=self.deployment_name,
            messages=[
                {"role": "system", "content": "Analyze this thought deeply:"},
                {"role": "user", "content": current}
            ]
        )
        current = response.choices[0].message.content
    return ToolResult(True, current, "thinking")
```

#### 3. Missing Search Mode in fs_read

**Problem:** Strands has built-in search with context lines.

**Impact:** Using `grep` via bash is slower and less integrated.

**Fix:** Add search mode:
```python
def fs_read_search(self, file_path: str, pattern: str, context_lines: int = 2):
    """Search file for pattern with context."""
    # Implementation similar to Strands
```

### MEDIUM Priority Issues

#### 4. No Image Reading

**Problem:** Cannot analyze screenshots, diagrams, or binary files.

**Impact:** Limited for issues involving visual assets.

#### 5. No cwd Parameter in execute_bash

**Problem:** Always runs from repo root.

**Impact:** Cannot navigate to subdirectories easily.

#### 6. No Negative Line Indices

**Problem:** Cannot say "last 100 lines" easily.

---

## 5. Functional Comparison Matrix

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    STRANDS vs TOOL AGENT COMPARISON                          │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Feature                          Strands    Tool Agent    Gap Severity      │
│  ─────────────────────────────────────────────────────────────────────────   │
│  File Reading (basic)               ✅          ✅          None              │
│  File Reading (line ranges)         ✅          ✅          None              │
│  File Reading (search mode)         ✅          ⚠️           Medium           │
│  File Reading (directory mode)      ✅          ✅          None              │
│  File Reading (image mode)          ✅          ❌          Medium            │
│  File Reading (negative indices)    ✅          ❌          Low               │
│                                                                              │
│  Bash (unrestricted)                ✅          ❌          HIGH              │
│  Bash (with cwd)                    ✅          ❌          Medium            │
│  Bash (timeout)                     ❌          ✅          Better            │
│  Bash (output truncation)           ❌          ✅          Better            │
│                                                                              │
│  Thinking (basic)                   ✅          ⚠️           HIGH              │
│  Thinking (multi-cycle)             ✅          ❌          HIGH              │
│  Thinking (nested agent)            ✅          ❌          HIGH              │
│  Thinking (model switching)         ✅          ❌          Medium            │
│                                                                              │
│  File Writing                       ✅          ❌          Expected          │
│  AWS Integration                    ✅          ❌          Not needed        │
│  GitHub Integration                 ✅          ❌          Not needed        │
│                                                                              │
│  Azure OpenAI Support               ❌          ✅          Advantage         │
│  Async Operations                   ⚠️           ✅          Advantage         │
│  Multi-endpoint Load Balancing      ❌          ✅          Advantage         │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Recommendations

### Immediate Fixes (Before Running Evaluation)

1. **Expand command whitelist** - Add `git`, `python`, `jq`
2. **Add basic thinking cycles** - At least 2-3 iterations
3. **Add cwd parameter** - Allow subdirectory navigation

### Future Improvements

1. **Implement proper nested thinking** - With tool access
2. **Add search mode to fs_read** - Built-in grep with context
3. **Add image support** - For visual issues

### Keep As-Is (Advantages)

1. **Azure OpenAI support** - Works where Strands doesn't
2. **Async operations** - Better performance
3. **Multi-endpoint routing** - Scalability
4. **Sandboxing** - Security for untrusted repos

---

## 7. Test Recommendations

Run evaluation and monitor:

```bash
# Watch for these in logs:
grep -E "(not allowed|whitelist|Unknown tool)" results/*.log

# Check tool usage patterns:
grep -E "execute_bash|fs_read|thinking" results/gen_python.jsonl | head -20
```

---

## 8. Conclusion

Our Tool Agent provides ~70% of Strands functionality for CAB's use case:

| Category | Coverage | Notes |
|----------|----------|-------|
| File reading | 90% | Missing image mode, search mode |
| Bash execution | 60% | Whitelist too restrictive |
| Thinking | 20% | **Critical gap** - needs improvement |
| Overall | 70% | Functional but limited |

The main gaps are:
1. **Command restrictions** - Will limit code exploration
2. **Thinking depth** - Won't match Strands' analysis quality
3. **Search integration** - Less convenient than built-in

For CAB evaluation, the agent should still produce reasonable results, but may underperform compared to true Strands due to the thinking tool limitations.
