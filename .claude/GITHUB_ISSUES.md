# GitHub Issues Guide (SolaraAlpha)

Quick reference for creating issues for the AI proprietary trading firm.

## Label Taxonomy

| Category | Valid Labels | Orchestrator Rules |
|----------|-------------|-------------------|
| **Priority** (required) | `priority:high`, `priority:medium`, `priority:low` | Processes high→medium FIFO. `priority:low` skipped |
| **Type** (required) | `type:task`, `type:intervention` | Determines workflow and assignee |
| **Status** (orchestrator-managed) | `status:in-progress`, `status:blocked`, `status:ready` | **Never add on creation. Orchestrator manages these.** |

## Issue Types

### type:task (assignee: agent)
Atomic work item for orchestrator. Should be scoped to a single, well-defined objective. task-builder naturally decomposes complex requests into multiple atomic tasks.

### type:intervention (assignee: human)
Requires human review/decision. Created by agents when critical approval needed (risk limits, strategy validation, regulatory). Non-blocking - orchestrator continues with other work while human reviews.

## Issue Structure

**Title**: `{Component} - {Specific action}`

**Required Labels**: One priority, one type

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

**Body** (interventions):
```markdown
## Context
[What work was completed that needs review? Reference specific issue #]

## Review Required
- [Specific aspect needing validation]
- [Decision point or trade-off assessment]
- [Risk evaluation or regulatory concern]

## Related Work
- Completed in issue #[N]
- If changes needed: create new task with specific rework requirements

## Human Actions
1. Review completed work
2. Add comment with decision/feedback
3. Close this intervention when resolved (orchestrator continues automatically)
```

## Orchestrator Workflow

**Selection**: Issues with `priority:high|medium`, `type:task`, and NO `status:*` labels
- Skips `type:intervention` (human-only, non-blocking)
- Processes tasks in priority order (high→medium), FIFO within tier

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

Intervention (human-managed):
[No status] → (human reviews) → closed (human action, orchestrator unaware)
```

## Task Decomposition

**task-builder philosophy**: Use AI judgment to naturally decompose complex requests into atomic tasks.

**Guidelines**:
- Create multiple small tasks rather than one large task
- Each task should have a single, clear objective
- Tasks should be independently executable where possible
- Use descriptive titles to show relationship (e.g., "DataPipeline - Connect to source", "DataPipeline - Add validation")
- No need for parent/child hierarchy - just create logical sequence

**When to create interventions**:
- Risk model validation (affects capital)
- Trading strategy approval (regulatory/financial impact)
- Production deployment decisions
- Security-sensitive changes

## Validation

**Orchestrator-ready when**:
- Has `priority:high` OR `priority:medium`
- Has `type:task` (interventions are human-only)
- NO `status:*` labels present
- Context ≥ 2 sentences
- Acceptance criteria ≥ 1 testable condition
- Review Protocol specifies agent and focus areas

## Examples

### Example 1: Simple Atomic Task
```
Title: Config - Add risk limit constants
Labels: priority:medium, type:task

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
Labels: priority:high, type:task

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

**Task 2**: DataPipeline - Implement quote validation (priority:high, type:task)
**Task 3**: DataPipeline - Add storage layer (priority:high, type:task)

### Example 3: Intervention
```
Title: Intervention - Risk model validation review
Labels: priority:high, type:intervention
Assignee: @human

## Context
Completed risk model implementation in #42. Model calculates position limits based on volatility and account balance. Requires validation before enabling in production.

## Review Required
- Verify risk calculation logic matches trading strategy requirements
- Assess edge cases (zero volatility, max drawdown scenarios)
- Approve risk parameters (conservative enough?)

## Related Work
- Completed in issue #42
- If changes needed: human will create new task issue with specific rework requirements

## Human Actions
1. Review risk model code and test results
2. Add comment with decision/feedback
3. Close this intervention when resolved
```
