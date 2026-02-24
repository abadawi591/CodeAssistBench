# Strands Framework Optimizations for Azure OpenAI

This document details the optimizations made to the Strands framework integration for improved performance, true concurrency, and better observability when running with Azure OpenAI.

## Table of Contents

1. [Overview](#overview)
2. [Problem Analysis](#problem-analysis)
3. [Optimizations](#optimizations)
   - [Azure Config Caching](#1-azure-config-caching)
   - [Async Execution with Thread Pool](#2-async-execution-with-thread-pool)
   - [Non-Blocking Sleep](#3-non-blocking-sleep)
   - [Warmup Phase](#4-warmup-phase)
   - [Progress Tracking](#5-progress-tracking)
4. [Architecture Diagrams](#architecture-diagrams)
5. [Performance Impact](#performance-impact)
6. [Configuration](#configuration)

---

## Overview

The CodeAssistBench evaluation pipeline uses the Strands framework with Azure OpenAI (GPT-5.2) to process GitHub issues concurrently. Several optimizations were implemented to achieve true parallelism and better resource utilization.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    OPTIMIZED STRANDS PIPELINE                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐              │
│  │ Issue 1  │    │ Issue 2  │    │ Issue 3  │    │ Issue N  │              │
│  └────┬─────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘              │
│       │               │               │               │                     │
│       ▼               ▼               ▼               ▼                     │
│  ┌────────────────────────────────────────────────────────────────┐        │
│  │              ASYNCIO EVENT LOOP (Non-Blocking)                 │        │
│  │  ┌─────────────────────────────────────────────────────────┐   │        │
│  │  │           THREAD POOL EXECUTOR                          │   │        │
│  │  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐       │   │        │
│  │  │  │Strands 1│ │Strands 2│ │Strands 3│ │Strands N│       │   │        │
│  │  │  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘       │   │        │
│  │  └───────┼───────────┼───────────┼───────────┼────────────┘   │        │
│  └──────────┼───────────┼───────────┼───────────┼────────────────┘        │
│             │           │           │           │                          │
│             ▼           ▼           ▼           ▼                          │
│  ┌──────────────────────────────────────────────────────────────┐         │
│  │         AZURE OPENAI MULTI-ENDPOINT ROUTER                   │         │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐ │         │
│  │  │ East US 2  │ │ S.Central  │ │  Sweden    │ │ East US 2  │ │         │
│  │  │ (Primary)  │ │   US       │ │  Central   │ │    #4      │ │         │
│  │  │ 10K RPM    │ │ 10K RPM    │ │  8.5K RPM  │ │  2K RPM    │ │         │
│  │  └────────────┘ └────────────┘ └────────────┘ └────────────┘ │         │
│  └──────────────────────────────────────────────────────────────┘         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Problem Analysis

### Original Issues Identified

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    BEFORE: SEQUENTIAL EXECUTION                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Time ──────────────────────────────────────────────────────────────────►   │
│                                                                             │
│  Issue 1: [===KEY VAULT===][=====STRANDS CALL=====][==RESPONSE==]           │
│  Issue 2:                                          [===KEY VAULT===]...     │
│  Issue 3:                                                          ...      │
│  Issue 4:                                                          ...      │
│  Issue 5:                                                          ...      │
│           ▲                 ▲                                               │
│           │                 │                                               │
│           │                 └── Blocking: time.sleep() & sync call          │
│           │                                                                 │
│           └── Repeated Key Vault lookups (network latency)                  │
│                                                                             │
│  RESULT: 5 issues processed SEQUENTIALLY despite async/await                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Root Causes

1. **Blocking Strands Call**: `self._strands_agent(prompt)` is synchronous
2. **Blocking Sleep**: `time.sleep(19)` for throttle retries
3. **Repeated Key Vault Lookups**: Each agent fetched API key independently
4. **No Warmup**: Cold start penalty for first concurrent requests

---

## Optimizations

### 1. Azure Config Caching

**Problem**: Each `StrandsAgent` instance called Azure Key Vault to get the API key.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    BEFORE: REPEATED KEY VAULT CALLS                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Agent 1 ──► Key Vault ──► Get Secret ──► 500ms                            │
│  Agent 2 ──► Key Vault ──► Get Secret ──► 500ms                            │
│  Agent 3 ──► Key Vault ──► Get Secret ──► 500ms                            │
│  Agent 4 ──► Key Vault ──► Get Secret ──► 500ms                            │
│  Agent 5 ──► Key Vault ──► Get Secret ──► 500ms                            │
│                                                                             │
│  Total: 2500ms + potential rate limiting                                    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                    AFTER: CACHED CONFIG                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Warmup ──► Key Vault ──► Get Secret ──► Cache ──► 500ms (once)            │
│                                             │                               │
│  Agent 1 ──► Cache Hit ◄─────────────────────┤ ──► <1ms                    │
│  Agent 2 ──► Cache Hit ◄─────────────────────┤ ──► <1ms                    │
│  Agent 3 ──► Cache Hit ◄─────────────────────┤ ──► <1ms                    │
│  Agent 4 ──► Cache Hit ◄─────────────────────┤ ──► <1ms                    │
│  Agent 5 ──► Cache Hit ◄─────────────────────┘ ──► <1ms                    │
│                                                                             │
│  Total: ~500ms (one-time) + negligible cache hits                          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Implementation** (`strands_agent.py`):

```python
# Global cache for Azure OpenAI configuration
_azure_config_cache = {}

def _get_azure_openai_config(self, model_name: str) -> dict:
    global _azure_config_cache
    
    # Check cache first
    if model_name in _azure_config_cache:
        self.logger.debug(f"Using cached Azure config for {model_name}")
        return _azure_config_cache[model_name]
    
    # Fetch from Key Vault (only once)
    api_key = self._fetch_from_key_vault()
    
    config = {
        "api_key": api_key,
        "endpoint": endpoint,
        "deployment_name": deployment_name,
        "api_version": api_version
    }
    
    # Cache for subsequent calls
    _azure_config_cache[model_name] = config
    return config
```

---

### 2. Async Execution with Thread Pool

**Problem**: Strands `__call__` is synchronous, blocking the event loop.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    BEFORE: BLOCKING EVENT LOOP                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  async def generate_response(...):                                          │
│      response = self._strands_agent(prompt)  # ◄── BLOCKS HERE!            │
│      #          ▲                                                           │
│      #          │                                                           │
│      #          └── Synchronous call, blocks entire event loop              │
│      #               Other coroutines CANNOT run during this time           │
│                                                                             │
│  Event Loop:                                                                │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ Task 1: [████████████ BLOCKED ████████████████████████████]        │    │
│  │ Task 2: [░░░░░░░░░░░░ WAITING ░░░░░░░░░░░░░░░░░░░░░░░░░░░░]        │    │
│  │ Task 3: [░░░░░░░░░░░░ WAITING ░░░░░░░░░░░░░░░░░░░░░░░░░░░░]        │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                    AFTER: THREAD POOL EXECUTOR                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  async def generate_response(...):                                          │
│      loop = asyncio.get_event_loop()                                        │
│      response = await loop.run_in_executor(                                 │
│          None,  # Default thread pool                                       │
│          self._strands_agent,  # Sync function                              │
│          enhanced_prompt       # Arguments                                  │
│      )                                                                      │
│                                                                             │
│  Event Loop + Thread Pool:                                                  │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ Event Loop (coordinates, non-blocking):                            │    │
│  │   Task 1: [await]──────────────────────────────────[done]          │    │
│  │   Task 2: [await]──────────────────────────────────[done]          │    │
│  │   Task 3: [await]──────────────────────────────────[done]          │    │
│  ├────────────────────────────────────────────────────────────────────┤    │
│  │ Thread Pool (actual work):                                         │    │
│  │   Thread 1: [████████ Strands Call 1 ████████]                     │    │
│  │   Thread 2: [████████ Strands Call 2 ████████]                     │    │
│  │   Thread 3: [████████ Strands Call 3 ████████]                     │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  TRUE PARALLELISM ACHIEVED!                                                 │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Implementation**:

```python
# OLD (BLOCKING)
response = self._strands_agent(enhanced_prompt)

# NEW (NON-BLOCKING)
loop = asyncio.get_event_loop()
response = await loop.run_in_executor(
    None,  # Use default ThreadPoolExecutor
    self._strands_agent,
    enhanced_prompt
)
```

---

### 3. Non-Blocking Sleep

**Problem**: `time.sleep()` blocks the entire event loop during throttle retries.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    BEFORE: BLOCKING SLEEP                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  if throttled:                                                              │
│      time.sleep(19)  # ◄── BLOCKS EVERYTHING FOR 19 SECONDS!               │
│                                                                             │
│  Event Loop during sleep:                                                   │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ ALL TASKS: [░░░░░░░░░░ FROZEN FOR 19s ░░░░░░░░░░░░░░░░░░░]         │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                    AFTER: ASYNC SLEEP                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  if throttled:                                                              │
│      await asyncio.sleep(19)  # ◄── Only THIS task waits                   │
│                                                                             │
│  Event Loop during sleep:                                                   │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ Task 1 (throttled): [░░░░░░░░░░ sleeping ░░░░░░░░░░░]              │    │
│  │ Task 2: [████████ RUNNING ████████████████████████████]            │    │
│  │ Task 3: [████████ RUNNING ████████████████████████████]            │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  OTHER TASKS CONTINUE RUNNING!                                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Implementation**:

```python
# OLD
time.sleep(19)

# NEW
await asyncio.sleep(19)
```

---

### 4. Warmup Phase

**Problem**: First concurrent requests all hit Key Vault simultaneously.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    BEFORE: COLD START STAMPEDE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Start ──► Issue 1 ──► Key Vault ──┐                                       │
│        ──► Issue 2 ──► Key Vault ──┤                                       │
│        ──► Issue 3 ──► Key Vault ──┼──► RATE LIMITED / CONTENTION          │
│        ──► Issue 4 ──► Key Vault ──┤                                       │
│        ──► Issue 5 ──► Key Vault ──┘                                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                    AFTER: WARMUP PHASE                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Start ──► Warmup Agent ──► Key Vault ──► Cache                            │
│                                             │                               │
│        ──► Issue 1 ──► Cache Hit ◄──────────┤                              │
│        ──► Issue 2 ──► Cache Hit ◄──────────┤  (No Key Vault calls)        │
│        ──► Issue 3 ──► Cache Hit ◄──────────┤                              │
│        ──► Issue 4 ──► Cache Hit ◄──────────┤                              │
│        ──► Issue 5 ──► Cache Hit ◄──────────┘                              │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Implementation** (`cli.py`):

```python
# Warm up Azure config cache before parallel processing
if agent_model_mapping:
    maintainer_model = agent_model_mapping.get("maintainer", "gpt-5.2")
    if "gpt-5" in maintainer_model.lower():
        logger.info("Pre-caching Azure OpenAI configuration...")
        warmup_agent = StrandsAgent(model_name=maintainer_model, config=config)
        warmup_agent._get_azure_openai_config(maintainer_model)
        logger.info("Azure config cached. Ready for parallel processing.")
```

---

### 5. Progress Tracking

Enhanced visibility into issue processing phases:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PROGRESS DISPLAY                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ⠹ Generation (×5) ━━━━━━━━━━━━━━━━━━━━━  15% ✓3 ✗0 • 0:05:32 • 0:25:00    │
│                                                                             │
│   #294    ✓ SATISFIED T5     about vae causality                    320s   │
│   #279    ✗ NOT_SAT T10      微调wan2.1-t2v-1.3B时...               627s   │
│   #131    ◐ PARTIAL T8       WSL2 Ubuntu: cache_video...            290s   │
│           ─────────────                                                     │
│   #127    🤖 Turn 4          单卡4090用kj的workflow...              180s   │
│   #109    👤 Turn 3          --ulysses_size 和 --ring_size...       175s   │
│   #107    🔍 Exploring       使用comfyui能不能多卡跑14b...          120s   │
│   #66     📦 Cloning...      No such file or directory...            45s   │
│   #144    🔧 Init...         Prevent Timeout                         12s   │
│                                                                             │
│  Legend:                                                                    │
│    🔧 Init      = Agent initialization                                     │
│    📦 Cloning   = Repository cloning                                       │
│    🔍 Exploring = Initial codebase exploration                             │
│    👤 Turn N    = User agent turn                                          │
│    🤖 Turn N    = Maintainer agent turn                                    │
│    ✓ SATISFIED  = User satisfied                                           │
│    ◐ PARTIAL    = Partially satisfied                                      │
│    ✗ NOT_SAT    = Not satisfied                                            │
│    ✗ ERROR      = Processing error                                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Architecture Diagrams

### Complete Request Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         COMPLETE REQUEST FLOW                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  CLI (cli.py)                                                               │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  1. Load issues from JSONL                                           │  │
│  │  2. Apply filters (--language, --no-docker, --category)              │  │
│  │  3. Warmup Azure config cache                                        │  │
│  │  4. Create asyncio.Semaphore(concurrency)                            │  │
│  │  5. Launch concurrent tasks with asyncio.gather()                    │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                              │
│                              ▼                                              │
│  GenerationWorkflow (generation_workflow.py)                                │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  For each issue (concurrent):                                        │  │
│  │  ┌────────────────────────────────────────────────────────────────┐  │  │
│  │  │  1. progress_callback("init")                                  │  │  │
│  │  │  2. Create agents (uses cached config)                         │  │  │
│  │  │  3. progress_callback("commit")                                │  │  │
│  │  │  4. choose_commit() ──► LLM call                               │  │  │
│  │  │  5. progress_callback("cloning")                               │  │  │
│  │  │  6. Clone repository                                           │  │  │
│  │  │  7. progress_callback("exploring")                             │  │  │
│  │  │  8. Initial exploration                                        │  │  │
│  │  │  9. Conversation loop (up to 10 turns):                        │  │  │
│  │  │     ├── progress_callback("user", turn_num)                    │  │  │
│  │  │     ├── User agent generates question                          │  │  │
│  │  │     ├── progress_callback("maintainer", turn_num)              │  │  │
│  │  │     └── Maintainer agent generates response                    │  │  │
│  │  │  10. Return result with satisfaction status                    │  │  │
│  │  └────────────────────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                              │
│                              ▼                                              │
│  StrandsAgent (strands_agent.py)                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  generate_response():                                                │  │
│  │  ┌────────────────────────────────────────────────────────────────┐  │  │
│  │  │  1. Get Azure config (from cache)                              │  │  │
│  │  │  2. Build Strands agent (if not cached)                        │  │  │
│  │  │  3. Prepare enhanced prompt with repo context                  │  │  │
│  │  │  4. Execute in thread pool:                                    │  │  │
│  │  │     response = await loop.run_in_executor(                     │  │  │
│  │  │         None, self._strands_agent, prompt                      │  │  │
│  │  │     )                                                          │  │  │
│  │  │  5. Handle throttling with await asyncio.sleep(19)             │  │  │
│  │  │  6. Return response                                            │  │  │
│  │  └────────────────────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                              │
│                              ▼                                              │
│  Azure Endpoint Router (azure_endpoint_router.py)                           │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  - Load balances across 4 endpoints                                  │  │
│  │  - Automatic failover with tenacity                                  │  │
│  │  - Health tracking per endpoint                                      │  │
│  │  - 30,500 RPM total capacity                                         │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Concurrency Model

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         CONCURRENCY MODEL                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│                    ┌─────────────────────────────────┐                      │
│                    │      ASYNCIO EVENT LOOP         │                      │
│                    │   (Single-threaded coordinator) │                      │
│                    └───────────────┬─────────────────┘                      │
│                                    │                                        │
│           ┌────────────────────────┼────────────────────────┐               │
│           │                        │                        │               │
│           ▼                        ▼                        ▼               │
│    ┌─────────────┐          ┌─────────────┐          ┌─────────────┐        │
│    │  Task 1     │          │  Task 2     │          │  Task N     │        │
│    │  (Issue)    │          │  (Issue)    │          │  (Issue)    │        │
│    └──────┬──────┘          └──────┬──────┘          └──────┬──────┘        │
│           │                        │                        │               │
│           │ await                  │ await                  │ await         │
│           │ run_in_executor()      │ run_in_executor()      │ ...           │
│           │                        │                        │               │
│           ▼                        ▼                        ▼               │
│    ┌──────────────────────────────────────────────────────────────┐        │
│    │              THREAD POOL EXECUTOR                            │        │
│    │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │        │
│    │  │ Thread 1 │  │ Thread 2 │  │ Thread 3 │  │ Thread N │     │        │
│    │  │ Strands  │  │ Strands  │  │ Strands  │  │ Strands  │     │        │
│    │  │  Call    │  │  Call    │  │  Call    │  │  Call    │     │        │
│    │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘     │        │
│    └───────┼─────────────┼─────────────┼─────────────┼────────────┘        │
│            │             │             │             │                      │
│            ▼             ▼             ▼             ▼                      │
│    ┌──────────────────────────────────────────────────────────────┐        │
│    │                  AZURE OPENAI ENDPOINTS                      │        │
│    │   East US 2 ◄──► South Central US ◄──► Sweden ◄──► East #4   │        │
│    └──────────────────────────────────────────────────────────────┘        │
│                                                                             │
│  Key:                                                                       │
│    Event Loop: Coordinates tasks, handles I/O, never blocks                 │
│    Tasks: Python coroutines, yield control at await points                  │
│    Thread Pool: Runs blocking Strands calls in parallel                     │
│    Endpoints: Distributed Azure OpenAI for throughput                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Performance Impact

### Before vs After Comparison

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    PERFORMANCE COMPARISON                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  BEFORE OPTIMIZATIONS (Sequential):                                         │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │  Issue 1: [████████████████████] 5 min                             │    │
│  │  Issue 2:                       [████████████████████] 5 min       │    │
│  │  Issue 3:                                            [████...      │    │
│  │                                                                    │    │
│  │  5 issues × 5 min = 25 minutes (sequential)                        │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  AFTER OPTIMIZATIONS (Parallel):                                            │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │  Issue 1: [████████████████████]                                   │    │
│  │  Issue 2: [████████████████████]                                   │    │
│  │  Issue 3: [████████████████████]  } 5-6 min total                  │    │
│  │  Issue 4: [████████████████████]                                   │    │
│  │  Issue 5: [████████████████████]                                   │    │
│  │                                                                    │    │
│  │  5 issues in ~6 minutes (parallel, ~4x speedup)                    │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  METRICS:                                                                   │
│  ┌────────────────────────────────────┬───────────────────────────────┐    │
│  │ Metric                             │ Before      │ After           │    │
│  ├────────────────────────────────────┼─────────────┼─────────────────┤    │
│  │ Key Vault calls per run            │ N × 2       │ 1 (cached)      │    │
│  │ Event loop blocking                │ YES         │ NO              │    │
│  │ True parallelism                   │ NO          │ YES             │    │
│  │ Time for 5 issues (5min each)      │ ~25 min     │ ~6 min          │    │
│  │ Throughput (issues/hour)           │ 12          │ 50+             │    │
│  └────────────────────────────────────┴─────────────┴─────────────────┘    │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Configuration

### Recommended Settings

```yaml
# Concurrency based on endpoint capacity
concurrency: 5-10  # Start with 5, increase if stable

# Azure Endpoints (30,500 RPM total)
# - East US 2:       10,000 RPM (primary)
# - South Central:   10,000 RPM
# - Sweden Central:   8,500 RPM  
# - East US 2 #4:     2,000 RPM

# Filters for simpler issues
--no-docker          # Skip issues requiring Docker
--language python    # Filter by language
--limit 50           # Process subset for testing
```

### Example Command

```bash
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --language python \
  --no-docker \
  --output results/gen_python_optimized.jsonl \
  --concurrency 10 \
  --limit 100
```

---

## Files Modified

| File | Changes |
|------|---------|
| `src/cab_evaluation/agents/strands_agent.py` | Added `_azure_config_cache`, `run_in_executor()`, `asyncio.sleep()` |
| `src/cab_evaluation/cli.py` | Added warmup phase, progress tracking, filtering options |
| `src/cab_evaluation/workflows/generation_workflow.py` | Added progress callbacks for phases |

---

## Troubleshooting

### Issues Still Sequential?

1. Clear `__pycache__`: `find . -type d -name __pycache__ -exec rm -rf {} +`
2. Reinstall: `pip install -e .`
3. Check concurrency flag: `--concurrency 5`

### Throttling Errors?

- Reduce concurrency
- Check Azure endpoint quotas
- The system will auto-retry with exponential backoff

### Progress Not Updating?

- Ensure progress callbacks are being called
- Check that `rich` library is installed
- Verify terminal supports ANSI colors

---

*Last updated: February 2026*
