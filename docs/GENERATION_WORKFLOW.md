# Generation Workflow Documentation

This document explains the complete generation workflow used to create benchmark conversations from GitHub issues.

## Two-Phase Architecture

CodeAssistBench uses a **two-phase evaluation system**:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    TWO-PHASE EVALUATION ARCHITECTURE                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ╔═══════════════════════════════════════════════════════════════════════╗  │
│  ║  PHASE 1: GENERATION                                                   ║  │
│  ║  Command: generation-dataset                                           ║  │
│  ╠═══════════════════════════════════════════════════════════════════════╣  │
│  ║                                                                        ║  │
│  ║  ┌─────────────┐         ┌─────────────┐                              ║  │
│  ║  │ MAINTAINER  │ ◄─────► │    USER     │                              ║  │
│  ║  │   Agent     │         │   Agent     │                              ║  │
│  ║  │             │         │             │                              ║  │
│  ║  │ • Has repo  │         │ • Asks Qs   │                              ║  │
│  ║  │ • Explores  │         │ • Rates     │                              ║  │
│  ║  │ • Answers   │         │   SATISFIED │                              ║  │
│  ║  └─────────────┘         └─────────────┘                              ║  │
│  ║                                │                                       ║  │
│  ║                                ▼                                       ║  │
│  ║  Output: Conversations + Satisfaction Status (SAT / NOT_SAT)          ║  │
│  ║                                                                        ║  │
│  ╚═══════════════════════════════════════════════════════════════════════╝  │
│                                  │                                           │
│                                  ▼                                           │
│  ╔═══════════════════════════════════════════════════════════════════════╗  │
│  ║  PHASE 2: EVALUATION                                                   ║  │
│  ║  Command: evaluate                                                     ║  │
│  ╠═══════════════════════════════════════════════════════════════════════╣  │
│  ║                                                                        ║  │
│  ║  ┌─────────────┐                                                      ║  │
│  ║  │   JUDGE     │  ← Independent evaluation                            ║  │
│  ║  │   Agent     │  ← Compares answer vs actual code                    ║  │
│  ║  │             │  ← Can explore repository                            ║  │
│  ║  │ • Verifies  │  ← Gives objective verdict                           ║  │
│  ║  │ • Scores    │                                                      ║  │
│  ║  └─────────────┘                                                      ║  │
│  ║         │                                                              ║  │
│  ║         ▼                                                              ║  │
│  ║  Output: Verdict (CORRECT / INCORRECT / PARTIAL) + Alignment Score    ║  │
│  ║                                                                        ║  │
│  ╚═══════════════════════════════════════════════════════════════════════╝  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Satisfaction vs Correctness (Important!)

**These are INDEPENDENT metrics:**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ SATISFACTION ≠ CORRECTNESS                                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ SATISFACTION (from User Agent)                                        │   │
│  │ ─────────────────────────────────────────────────────────────────────│   │
│  │ • Subjective: "Did the simulated user feel their question answered?" │   │
│  │ • Can be demanding/picky                                              │   │
│  │ • Stops at max turns even if answer is good                           │   │
│  │ • Values: SAT (satisfied) | NOT_SAT (not satisfied)                   │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │ CORRECTNESS (from Judge Agent)                                        │   │
│  │ ─────────────────────────────────────────────────────────────────────│   │
│  │ • Objective: "Is the maintainer's answer technically correct?"        │   │
│  │ • Compares against actual repository code                             │   │
│  │ • Can explore repo to verify claims                                   │   │
│  │ • Values: CORRECT | INCORRECT | PARTIAL                               │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  POSSIBLE COMBINATIONS:                                                      │
│  ┌────────────────┬─────────────┬───────────────────────────────────────┐   │
│  │ Satisfaction   │ Correctness │ Meaning                               │   │
│  ├────────────────┼─────────────┼───────────────────────────────────────┤   │
│  │ SAT            │ CORRECT     │ ✅ Happy user, correct answer          │   │
│  │ SAT            │ INCORRECT   │ ⚠️  User fooled by wrong answer        │   │
│  │ NOT_SAT        │ CORRECT     │ ✅ Demanding user, but answer is right │   │
│  │ NOT_SAT        │ INCORRECT   │ ❌ User right to be unsatisfied        │   │
│  │ NOT_SAT        │ PARTIAL     │ 🔶 Answer partially correct            │   │
│  └────────────────┴─────────────┴───────────────────────────────────────┘   │
│                                                                              │
│  KEY INSIGHT: A NOT_SAT result during generation can still be judged        │
│  as CORRECT during evaluation! The user agent may simply be demanding,      │
│  or the question may be inherently hard to fully satisfy.                   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Running Both Phases

```bash
# Phase 1: Generate conversations
python -m cab_evaluation.cli generation-dataset \
  dataset/cab_recent.jsonl \
  --agent-models '{"maintainer": "gpt-5.2", "user": "gpt-5.2"}' \
  --output results/gen_python.jsonl

# Phase 2: Evaluate correctness (separate step)
python -m cab_evaluation.cli evaluate \
  results/gen_python.jsonl \
  --agent-models '{"judge": "sonnet37"}' \
  --output results/eval_python.jsonl
```

---

## Overview

The generation workflow simulates a conversation between two AI agents:
- **Maintainer Agent**: Has access to the repository and can explore code
- **User Agent**: Asks follow-up questions based on the original issue

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        GENERATION WORKFLOW                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   GitHub Issue  ──►  Clone Repo  ──►  Explore  ──►  Conversation  ──►  Save │
│                                                                              │
│   ┌─────────┐      ┌─────────┐      ┌─────────┐      ┌─────────┐            │
│   │  Issue  │      │  Repo   │      │Maintainer│     │ Multi-  │            │
│   │  Data   │ ──►  │  Clone  │ ──►  │ Explores │ ──► │  Turn   │ ──► JSONL  │
│   │ (JSONL) │      │(@commit)│      │  Code    │     │  Chat   │            │
│   └─────────┘      └─────────┘      └─────────┘      └─────────┘            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Workflow Phases

Each issue goes through these phases:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ PHASE PROGRESSION                                                             │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                               │
│  ┌──────┐   ┌─────────┐   ┌───────────┐   ┌────────────────┐   ┌──────────┐  │
│  │ Init │ → │ Commit  │ → │  Cloning  │ → │   Exploring    │ → │  Turns   │  │
│  │  🔧  │   │   🔗    │   │    📦     │   │  🔍 (1-5 iter) │   │ 💬 (1-N) │  │
│  └──────┘   └─────────┘   └───────────┘   └────────────────┘   └──────────┘  │
│                                                                               │
│  ~1s         ~1s           ~10-60s         ~150-300s            ~60-180s      │
│                            (depends on     (5 iterations ×      (per turn)    │
│                             repo size)      30-60s each)                      │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Phase Details

| Phase | Icon | Description | Duration |
|-------|------|-------------|----------|
| Init | 🔧 | Initialize issue processing, load data | ~1s |
| Commit | 🔗 | Resolve commit hash for the issue | ~1s |
| Cloning | 📦 | Clone repository at specific commit | 10-60s |
| Exploring | 🔍 | Maintainer explores codebase (5 iterations) | 150-300s |
| Turn N | 💬 | Conversation turn between agents | 60-180s per turn |

---

## The Exploration Phase (Deep Dive)

The exploration phase is where the Maintainer agent learns about the codebase before answering. This uses an **iterative exploration loop**.

### Exploration Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      EXPLORATION ITERATIONS (5 cycles)                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ITERATION 1: Initial Discovery                                      │    │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐           │    │
│  │  │ Read Issue   │ → │ LLM Decides  │ → │ Execute Cmds │           │    │
│  │  │ Question     │    │ What to Look │    │ ls, find,    │           │    │
│  │  │              │    │ At First     │    │ tree, etc.   │           │    │
│  │  └──────────────┘    └──────────────┘    └──────────────┘           │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ITERATION 2: Targeted Search                                        │    │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐           │    │
│  │  │ Results from │ → │ LLM Searches │ → │ Execute Cmds │           │    │
│  │  │ Iteration 1  │    │ for Keywords │    │ grep, rg,    │           │    │
│  │  │              │    │ and Patterns │    │ find -name   │           │    │
│  │  └──────────────┘    └──────────────┘    └──────────────┘           │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ITERATION 3: Deep Reading                                           │    │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐           │    │
│  │  │ Found Files  │ → │ LLM Reads    │ → │ Execute Cmds │           │    │
│  │  │ from Search  │    │ Relevant     │    │ cat, head,   │           │    │
│  │  │              │    │ Source Files │    │ tail -n      │           │    │
│  │  └──────────────┘    └──────────────┘    └──────────────┘           │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ITERATION 4: Context Expansion                                      │    │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐           │    │
│  │  │ Understanding│ → │ LLM Checks   │ → │ Execute Cmds │           │    │
│  │  │ Core Code    │    │ Imports and  │    │ cat related  │           │    │
│  │  │              │    │ Dependencies │    │ files        │           │    │
│  │  └──────────────┘    └──────────────┘    └──────────────┘           │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  ITERATION 5: Final Answer                                           │    │
│  │  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐           │    │
│  │  │ Full Context │ → │ LLM Provides │ → │ ANSWER:      │           │    │
│  │  │ Gathered     │    │ Complete     │    │ (complete    │           │    │
│  │  │              │    │ Response     │    │  response)   │           │    │
│  │  └──────────────┘    └──────────────┘    └──────────────┘           │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Exploration Commands

The LLM outputs exploration commands using `EXPLORE:` prefix:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ EXPLORE COMMAND FORMAT                                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  LLM Response:                                                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ Let me first understand the repository structure.                    │    │
│  │                                                                       │    │
│  │ EXPLORE: ls -la                                                      │    │
│  │ EXPLORE: find . -name "*.py" -type f | head -20                      │    │
│  │ EXPLORE: cat README.md                                               │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  System executes each command and collects results:                          │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ Command: ls -la                                                      │    │
│  │ Result:                                                              │    │
│  │ total 48                                                             │    │
│  │ drwxr-xr-x  8 user user 4096 Jan 15 10:00 .                         │    │
│  │ -rw-r--r--  1 user user 1234 Jan 15 10:00 README.md                 │    │
│  │ drwxr-xr-x  4 user user 4096 Jan 15 10:00 src                       │    │
│  │ ...                                                                  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Early Exit

The LLM can exit exploration early by providing `ANSWER:` instead of more `EXPLORE:` commands:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ EARLY EXIT (when LLM has enough information)                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  After Iteration 3, LLM might respond:                                       │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ I now have enough information to answer the question.                │    │
│  │                                                                       │    │
│  │ ANSWER:                                                              │    │
│  │ The issue is caused by a race condition in the `process_data()`     │    │
│  │ function in `src/worker.py`. The fix involves adding a lock...      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  → Exploration stops, conversation phase begins                              │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Conversation Phase

After exploration, agents have a multi-turn conversation:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        CONVERSATION TURNS                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────┐         ┌─────────────────┐                            │
│  │   MAINTAINER    │◄───────►│      USER       │                            │
│  │   (has code)    │         │  (asks questions)│                           │
│  └─────────────────┘         └─────────────────┘                            │
│           │                           │                                      │
│           │    Turn 1                 │                                      │
│           │◄──────────────────────────│  "Can you explain more about        │
│           │                           │   the error handling?"              │
│           │──────────────────────────►│                                      │
│           │    (explores + responds)  │                                      │
│           │                           │                                      │
│           │    Turn 2                 │                                      │
│           │◄──────────────────────────│  "What about edge cases?"           │
│           │                           │                                      │
│           │──────────────────────────►│                                      │
│           │    (explores + responds)  │                                      │
│           │                           │                                      │
│           │         ...               │                                      │
│           │                           │                                      │
│           │    Turn N (satisfied)     │                                      │
│           │◄──────────────────────────│  "Thanks, that answers my           │
│           │                           │   question completely!"             │
│           │                           │                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Turn Workflow

Each conversation turn follows this flow:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ SINGLE TURN WORKFLOW                                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. User Agent generates follow-up question                                  │
│     ┌─────────────────────────────────────────────────────────────────┐     │
│     │ Based on the conversation so far, generate a follow-up question  │     │
│     │ that a developer might ask to better understand the issue.       │     │
│     └─────────────────────────────────────────────────────────────────┘     │
│                              │                                               │
│                              ▼                                               │
│  2. Maintainer Agent responds (may explore more code)                        │
│     ┌─────────────────────────────────────────────────────────────────┐     │
│     │ EXPLORE: cat src/utils.py | grep -A 10 "def validate"           │     │
│     │                                                                   │     │
│     │ The validation function checks for... [detailed response]        │     │
│     └─────────────────────────────────────────────────────────────────┘     │
│                              │                                               │
│                              ▼                                               │
│  3. User Agent evaluates satisfaction                                        │
│     ┌─────────────────────────────────────────────────────────────────┐     │
│     │ SATISFIED: yes/partial/no                                        │     │
│     │ REASON: The answer fully addresses my question about...          │     │
│     └─────────────────────────────────────────────────────────────────┘     │
│                              │                                               │
│                              ▼                                               │
│  4. Continue or End                                                          │
│     ┌─────────────────────────────────────────────────────────────────┐     │
│     │ If SATISFIED=yes  → End conversation                             │     │
│     │ If SATISFIED=no   → Continue to next turn                        │     │
│     │ If max_turns reached → End conversation                          │     │
│     └─────────────────────────────────────────────────────────────────┘     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## CLI Progress Display

The CLI shows real-time progress for all concurrent issues:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ CLI OUTPUT EXAMPLE                                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ℹ️  Starting generation with concurrency: 5                                 │
│  ℹ️  Output file: results/gen_python_nodock.jsonl                            │
│  ℹ️  Log file: results/generation_log_20260203_183530.log                    │
│                                                                              │
│  ⠦ Generation (×5) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  0% ✓0 ✗0 • 0:07:31       │
│                                                                              │
│  ┌────────┬─────────────────┬────────────────────────────────────┬───────┐  │
│  │ Issue  │ Phase           │ Title                              │ Time  │  │
│  ├────────┼─────────────────┼────────────────────────────────────┼───────┤  │
│  │ #294   │ 💬 Turn 2       │ about vae causality                │ 451s  │  │
│  │ #279   │ 💬 Turn 8       │ 微调wan2.1-t2v-1.3B时...           │ 451s  │  │
│  │ #127   │ 🔍 Exploring    │ 单卡4090用kj的workflow跑720P的问题 │ 451s  │  │
│  │ #109   │ 💬 Turn 1       │ --ulysses_size 和 --ring_size...   │ 451s  │  │
│  │ #107   │ 🔍 Exploring    │ 使用comfyui能不能多卡跑14b模型     │ 451s  │  │
│  └────────┴─────────────────┴────────────────────────────────────┴───────┘  │
│                                                                              │
│  Legend:                                                                     │
│  ─────────────────────────────────────────────────────────────────────────  │
│  🔧 Init       - Initializing issue processing                               │
│  🔗 Commit     - Resolving commit hash                                       │
│  📦 Cloning    - Cloning repository at commit                                │
│  🔍 Explore N/5- Exploration iteration N of 5                                │
│  💬 Turn N     - Conversation turn N                                         │
│  ✓ N          - Completed issues                                             │
│  ✗ N          - Failed issues                                                │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Timing Expectations

### With GPT-5.2 (Reasoning Model)

Reasoning models think deeply, resulting in longer response times:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TIMING BREAKDOWN (GPT-5.2 Reasoning)                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Phase              │ LLM Calls │ Time per Call │ Total Time                 │
│  ───────────────────┼───────────┼───────────────┼────────────                │
│  Init + Commit      │ 0         │ -             │ ~2s                        │
│  Cloning            │ 0         │ -             │ ~10-60s                    │
│  Exploration        │ 5         │ 30-60s        │ ~150-300s (2.5-5 min)      │
│  Per Turn           │ 2-3       │ 30-60s        │ ~60-180s per turn          │
│  ───────────────────┼───────────┼───────────────┼────────────                │
│  Total (5 turns)    │ 15-20     │ -             │ ~8-15 minutes per issue    │
│                                                                              │
│  With concurrency 5:                                                         │
│  50 issues ÷ 5 concurrent = 10 batches × 10 min = ~100 minutes total        │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### With Standard Models (GPT-4, Claude)

Non-reasoning models respond faster:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TIMING BREAKDOWN (Standard Models)                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Phase              │ LLM Calls │ Time per Call │ Total Time                 │
│  ───────────────────┼───────────┼───────────────┼────────────                │
│  Init + Commit      │ 0         │ -             │ ~2s                        │
│  Cloning            │ 0         │ -             │ ~10-60s                    │
│  Exploration        │ 5         │ 10-20s        │ ~50-100s (1-2 min)         │
│  Per Turn           │ 2-3       │ 10-20s        │ ~20-60s per turn           │
│  ───────────────────┼───────────┼───────────────┼────────────                │
│  Total (5 turns)    │ 15-20     │ -             │ ~3-6 minutes per issue     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Configuration Options

### Exploration Iterations

Control depth of code exploration:

```python
# In generation_workflow.py
max_iterations: int = 5  # Default: 5 iterations

# Faster (less thorough):
max_iterations: int = 2  # Quick scan

# More thorough:
max_iterations: int = 7  # Deep exploration
```

### Concurrency

Control parallel processing:

```bash
# CLI flag
--concurrency 5   # Process 5 issues in parallel (default)
--concurrency 10  # Process 10 issues in parallel (more API load)
--concurrency 1   # Sequential processing (debugging)
```

### Max Conversation Turns

Control conversation depth:

```python
# In config
max_turns: int = 10  # Maximum turns before stopping
```

---

## Output Format

Each completed issue produces a JSONL entry:

```json
{
  "id": "294",
  "repository": "owner/repo",
  "commit": "abc123...",
  "question": "Original issue question...",
  "exploration_log": "--- ITERATION 1 ---\nCommand: ls\nResult: ...",
  "conversation": [
    {"role": "maintainer", "content": "Initial answer..."},
    {"role": "user", "content": "Follow-up question..."},
    {"role": "maintainer", "content": "Detailed response..."},
    {"role": "user", "content": "SATISFIED: yes\nREASON: ..."}
  ],
  "final_answer": "Complete answer with code context...",
  "satisfaction": "satisfied",
  "total_turns": 3,
  "metadata": {
    "model": "gpt-5.2",
    "exploration_iterations": 5,
    "processing_time_seconds": 485
  }
}
```

---

## Troubleshooting

### Issues Stuck at "Exploring"

This is usually **normal** for reasoning models. Check:
1. Each exploration iteration takes 30-60s with GPT-5.2
2. 5 iterations × 45s = ~225s (~4 minutes) is expected
3. All issues at similar times = parallel processing working correctly

### No Progress After 10+ Minutes

Check the log file for errors:
```bash
cat results/generation_log_*.log | grep -i error
```

### API Rate Limits

If seeing 429 errors, reduce concurrency:
```bash
--concurrency 3  # Instead of 5
```

### Maintainer Responding with "USE_REFERENCE_COMMIT"

**Symptom**: Maintainer agent responds with `USE_REFERENCE_COMMIT` instead of actual answers.

**Cause**: This was a bug where the Strands agent context was polluted by the commit selection prompt.

**Fix**: The `choose_commit` function now uses direct LLM calls instead of the Strands agent to avoid context pollution.

**If you see this**: Update to the latest version of the codebase.

---

## See Also

- [COMMANDS.md](COMMANDS.md) - CLI command reference
- [DATA_PIPELINE.md](DATA_PIPELINE.md) - Data processing pipeline
- [AZURE_OPENAI_PIPELINE.md](AZURE_OPENAI_PIPELINE.md) - Azure OpenAI setup
