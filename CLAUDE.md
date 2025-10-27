# SolaraAlpha - AI Proprietary Trading Firm

## Mission
Maximize shareholder wealth through AI-driven trading systems.

**Priority**: Risk Management > Profitability > Operational Efficiency

## Trading Philosophy
**Multi-Strategy**: Diversified portfolio of uncorrelated trading strategies (emphasize relative value)
**Multi-Asset**: Coverage across equities, etfs, crypto, forex, commodities, fixed income
**Multi-Timeframe**: Strategies spanning high-frequency to long-term horizons

**Strategy Focus**: Prioritize relative value strategies (pairs trading, statistical arbitrage, spread trading) that exploit pricing inefficiencies between related instruments while maintaining market-neutral positions

## Roles

**Human**: Monitors the progress of the CEO and the ongoing tasks.
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
- Use sub-agents for specialised and/or multi-step workflows (reduces context loading)
    - example: "use the code-aligner to review the implementation"
    - see .claude/AGENT_GUIDE.md for what agents are available to use.
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

**Philosophy**: Trust the system (CEO + task-builder) to only create important work. All tasks treated equally.

**Behavior**:
- Selects: `type:task` + NO `status:*` labels
- Processes in pure FIFO order (oldest first); refreshes queue after each completion
- State: `[No status] → status:in-progress → [No status]/closed (success) OR status:blocked (failure)`

## Issue Standards

**Format**: `{Component} - {Action}` | **Labels**: `type:task` (never add `status:*`)
**Sections**: Context (why), Acceptance Criteria (testable conditions)
**Details**: See `.claude/GITHUB_ISSUES.md`
