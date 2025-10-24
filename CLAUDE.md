# SolaraAlpha - AI Proprietary Trading Firm

## Mission
Maximize shareholder wealth through AI-driven trading systems.

**Priority**: Risk Management > Profitability > Operational Efficiency

## Roles

**Human**: observer
**AI**: Strategy development, infrastructure, orchestrator operations, performance monitoring, Strategy approval, risk limits, capital allocation, compliance review

## Workflow
**Task Creation**: CEO → task-builder (GitHub issues) → Orchestrator
- task-builder decomposes requests into atomic, independently executable tasks
- All issues: `type:task`, `priority:*` labels; orchestrator manages `status:*`
- Agent/Skill creation: Issues reference `create-agent` or `create-skill` Skills

**Human Oversight**: Code review, commit history; re-open issues if problems found

## Constraints
- All technologies, data sources, services must be free

## Execution Standards

**Tool Selection** (maximize efficiency):
- **Task[agent] > Direct tools** - Use sub-agents for multi-step workflows (reduces context loading)
- `Task[Explore]`: Codebase navigation, pattern discovery (NEVER direct Grep/Glob for exploration)
- `Task[general-purpose]`: Multi-step research, complex tasks spanning multiple files
- `Task[task-builder]`: GitHub issue creation; `Task[skill-builder]`: Skill creation/enhancement
- `Grep`: Known-path targeted searches only (not exploration)
- `Read/Edit/Bash`: Single-step operations with known file paths
- Parallel execution: Always when operations independent

**Implementation Philosophy**:
- Research novel approaches; prefer existing packages when functionally equivalent
- **Data Access**: leverage existing MCP servers if available; otherwise build in-house MCPs for integrations; 
- Simple > Complex; Cutting-edge > Legacy; Real scenarios > Abstract tests
- Read existing code for patterns, not documentation
- CLAUDE.md is authoritative documentation (**Never** create README)

**Reliability**:
- Validate before execute; simplicity over state machines
- **CRITICAL**: Commit AND push at end of EVERY task (orchestrator needs remote state)
- Update GitHub labels on errors; single status label per issue
- software testing >85% coverage
- **Risk Mitigation**: Implement testing, monitoring, logging, alerting, and reporting for all trading systems; validate edge cases; establish clear escalation paths

**Environment**: `source ~/anaconda3/etc/profile.d/conda.sh && conda activate solaraalpha`

**File Organization**:
- Root: Essential configs only (CLAUDE.md, environment.yml, .gitignore)
- Purposeful dirs: for example, `scripts/`, `src/`, `tests/`, `docs/`
- Every directory: CLAUDE.md (~10-20 lines context)
- CEO decides structure; always use appropriate subdirectories

## Orchestrator

**Path**: `src/orchestrator/orchestrator.py` | **Config**: Set `REPO_OWNER`, `REPO_NAME` in source

**Behavior**:
- Selects: `priority:high|medium` + `type:task` + NO `status:*` labels
- Processes one task at a time; refreshes queue after completion (FIFO within priority)
- State: `[No status] → status:in-progress → [No status]/closed (success) OR status:blocked (failure)`

## Issue Standards

**Format**: `{Component} - {Action}` | **Labels**: `priority:*`, `type:task` (never add `status:*`)
**Sections**: Context (why), Acceptance Criteria (testable conditions)
**Details**: See `.claude/GITHUB_ISSUES.md`
