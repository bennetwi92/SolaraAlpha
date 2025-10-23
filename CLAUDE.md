# SolaraAlpha - AI Proprietary Trading Firm

## Mission
Maximize shareholder wealth through AI-driven trading systems.

**Priority**: Risk Management > Profitability > Operational Efficiency

## Organizational Structure

### Human (Shareholder)
- Approves trading strategies
- Sets risk limits and constraints
- Authorizes capital deployment
- Reviews regulatory compliance

### AI (Organization)
- Develops trading strategies (backtest, validate, optimize)
- Builds infrastructure (data pipelines, execution systems, risk monitors)
- Operates orchestrator for autonomous task coordination
- Monitors performance and reports to shareholder

### Workflow Protocol
**Task Creation**: CEO identifies need → **task-builder** creates GitHub issue → Orchestrator processes
**Never**: CEO or other agents create issues directly (task-builder optimizes prompts)
**Agent/Skill Creation**: Issues must reference appropriate Skill (`create-agent`, `create-skill`)

**Issue Sizing**:
- All issues estimated in story points (1/2/3/5/8/13/21)
- Target: ≤5 points (~10-15 min work)
- Issues ≥8 points MUST be decomposed into sub-tasks by task-builder
- Types: `task` (standard), `sub-task` (child), `intervention` (human review)

**Human Intervention**:
- Agents create `type:intervention` issues when requiring human approval
- Human reviews → adds comment → sets `status:ready`
- Blocked tasks unblock when intervention resolved

## Core Principles

### Objective
Build profitable, risk-managed trading systems through AI-to-AI workflow automation.

**Constraints**:
- All technologies, data sources, and services must be free (no paid APIs, subscriptions, or commercial data)

Human involvement only for:
- Trading strategy approval
- Risk limit authorization
- Regulatory compliance
- Capital allocation

### Execution Standards

**Token Efficiency**:
- Grep > Read (targeted search vs full file read)
- Direct tools > Agents (single-step vs multi-step workflows)
- Parallel calls when operations independent
- Read existing code for patterns, not documentation

**Lightweight Implementation**:
- Simple > Complex architectures
- Existing patterns > New dependencies
- No human-aesthetic documentation
- **NEVER create README files** - CLAUDE.md is authoritative, only doc for AI
- Real trading scenarios > Abstract unit tests

**Tool Selection**:
- `Read/Edit/Grep/Bash`: Single-step operations
- `Task` (agents): Multi-step workflows only
- `Task[Explore]`: Codebase navigation (never direct Grep/Glob for exploration)
- Parallel execution: Always when operations independent

**Reliability**:
- Validate before execute
- Single status label per issue
- Dynamic refresh after task completion
- Always update GitHub state on error
- Simplicity over complex state machines
- **CRITICAL: Commit AND push work at end of EVERY task** - orchestrator requires remote git state for coordination

**Environment**:
- Conda: `source ~/anaconda3/etc/profile.d/conda.sh && conda activate solaraalpha`

## Orchestrator Tool

AI workers coordinate autonomously via orchestrator (`src/orchestrator/orchestrator.py`):
- Fetches open issues from GitHub
- Selects issues with `priority:high|medium`, `type:task|sub-task`, NO `status:*` labels
- Spawns Claude Code worker subprocesses
- Tracks state via GitHub labels (simple workflow)
- Dynamic refresh after each completion (FIFO within priority tier)
- Skips `type:intervention` (human-only)

**Configuration**: Set `REPO_OWNER` and `REPO_NAME` in orchestrator source.

**State Flows**:
```
Task/Sub-task:
[No status] → status:in-progress → [No status]     (success)
                                 → status:blocked   (failure/timeout)

Intervention (human-managed):
[No status] → (human reviews) → status:ready → (unblocks dependent work)
```

Orchestrator processes one issue at a time, refreshing the queue after each completion to ensure highest-priority work is always selected next.

## Issue Standards

See `.claude/GITHUB_ISSUES.md` for complete issue creation standards.

**Quick summary**:
- Title format: `{Component} - {Action}`
- Required labels: One priority, one type, one size
- Types: `type:task` (agent), `type:sub-task` (agent), `type:intervention` (human)
- Size: Story points (1/2/3/5 workable, 8/13/21 parent tracking)
- Required sections: Context (why needed), Acceptance Criteria (testable conditions)
- Never add `status:*` labels on creation (orchestrator manages these)

---
**Token Budget**: ~100 lines for AI execution efficiency
