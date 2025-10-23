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

## Core Principles

### Objective
Build profitable, risk-managed trading systems through AI-to-AI workflow automation.

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
- **CRITICAL: Commit work at end of EVERY task** - orchestrator requires git state for coordination

**Environment**:
- Conda: `source ~/anaconda3/etc/profile.d/conda.sh && conda activate solaraalpha`

## Orchestrator Tool

AI workers coordinate autonomously via orchestrator (`src/orchestrator/orchestrator.py`):
- Fetches open issues from GitHub
- Selects issues with `priority:high|medium` and NO `status:*` labels
- Spawns Claude Code worker subprocesses
- Tracks state via GitHub labels (simple 2-state workflow)
- Dynamic refresh after each completion (FIFO within priority tier)

**Configuration**: Set `REPO_OWNER` and `REPO_NAME` in orchestrator source.

**State Flow**:
```
[No status] → status:in-progress → [No status]     (success)
                                 → status:blocked   (failure/timeout)
```

Orchestrator processes one issue at a time, refreshing the queue after each completion to ensure highest-priority work is always selected next.

## Issue Standards

See `.claude/GITHUB_ISSUES.md` for complete issue creation standards.

**Quick summary**:
- Title format: `{Type}: {Component} - {Action}`
- Required labels: One priority, one type
- Required sections: Context (why needed), Acceptance Criteria (testable conditions)
- Never add `status:*` labels on creation (orchestrator manages these)

---
**Token Budget**: ~100 lines for AI execution efficiency
