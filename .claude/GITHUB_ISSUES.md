# GitHub Issues Guide (SolaraAlpha)

Quick reference for creating issues for the AI proprietary trading firm.

## Label Taxonomy

| Category | Valid Labels | Orchestrator Rules |
|----------|-------------|-------------------|
| **Type** (required) | `type:task` | All issues are tasks |
| **Status** (orchestrator-managed) | `status:in-progress`, `status:blocked` | **Never add on creation. Orchestrator manages these.** |

## Issue Types

### type:task
Atomic work item for orchestrator. Should be scoped to a single, well-defined objective. task-builder naturally decomposes complex requests into multiple atomic tasks.

**Human oversight**: Via normal code review, commit history, and re-opening closed issues if problems found.

## Issue Structure

**Title**: `{Component} - {Specific action}`

**Required Labels**: `type:task`

**Body** (tasks):
```markdown
## Context
[Why needed? 2-4 sentences explaining business impact or necessity]

## Requirements (features) OR Current/Expected Behavior (bugs)
- [Specific, testable items]

## Acceptance Criteria
- [ ] [Testable completion condition]
- [ ] [Test coverage if code changes]
- [ ] [Peer review completed via Task tool before commit]
- [ ] [Commit AND push work (required for orchestrator)]

## Review Protocol
**Agent**: [Specify agent type: general-purpose, code-reviewer, etc. - must be existing agent]
**Focus Areas**:
- [What to review: e.g., "Risk calculations and edge cases"]
- [What to review: e.g., "Integration with existing data pipeline"]
- [What to review: e.g., "Error handling and validation logic"]

## Implementation Notes (optional)
[Technical approach, constraints, dependencies]
```

## Orchestrator Workflow

**Selection**: Issues with `type:task` and NO `status:*` labels
- Processes tasks in pure FIFO order (oldest first)

**Execution**:
1. Adds `status:in-progress`
2. Spawns worker (Claude Code subprocess)
3. On completion:
   - Success: Removes status, closes issue
   - Failure: Changes to `status:blocked`

**State Flows**:
```
Task:
[No status] → status:in-progress → [No status] → closed     (success)
                                 → status:blocked            (failure/timeout)
```

**Human oversight**: Review via commits, re-open closed issues if problems found, create new tasks for fixes.

## Task Decomposition

**task-builder philosophy**: Use AI judgment to naturally decompose complex requests into atomic tasks.

**Guidelines**:
- Create multiple small tasks rather than one large task
- Each task should have a single, clear objective
- Tasks should be independently executable where possible
- Use descriptive titles to show relationship (e.g., "DataPipeline - Connect to source", "DataPipeline - Add validation")
- No need for parent/child hierarchy - just create logical sequence


## Validation

**Orchestrator-ready when**:
- Has `type:task`
- NO `status:*` labels present
- Context ≥ 2 sentences
- Acceptance criteria ≥ 1 testable condition
- Review Protocol specifies agent and focus areas

## Examples

### Example 1: Simple Atomic Task
```
Title: Config - Add risk limit constants
Labels: type:task

## Context
Trading system needs configurable risk limits for position sizing and drawdown protection.

## Requirements
- Add MAX_POSITION_SIZE, MAX_DRAWDOWN constants
- Document units and rationale

## Acceptance Criteria
- [ ] Constants defined in config module
- [ ] Values validated on startup
- [ ] Commit AND push work

## Review Protocol
**Agent**: general-purpose
**Focus Areas**:
- Constant naming and organization
- Value reasonableness for trading context
```

### Example 2: Complex Request Decomposed by task-builder

**User request**: "Build real-time market data pipeline"

**task-builder creates 3 atomic tasks**:

**Task 1**:
```
Title: DataPipeline - Connect to data source
Labels: type:task

## Context
Establish connection to free market data API for quote ingestion.

## Requirements
- Connect to data source with retry logic
- Handle connection errors gracefully
- Log connection status

## Acceptance Criteria
- [ ] Connection established successfully
- [ ] Retry logic handles transient failures
- [ ] Tests cover connection scenarios
- [ ] Peer review completed via Task tool before commit
- [ ] Commit AND push work

## Review Protocol
**Agent**: general-purpose
**Focus Areas**:
- Connection reliability and error handling
- Retry logic edge cases
```

**Task 2**: DataPipeline - Implement quote validation (type:task)
**Task 3**: DataPipeline - Add storage layer (type:task)
