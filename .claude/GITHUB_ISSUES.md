# GitHub Issues Guide (SolaraAlpha)

Quick reference for creating issues for the AI proprietary trading firm.

## Label Taxonomy

| Category | Valid Labels | Orchestrator Rules |
|----------|-------------|-------------------|
| **Priority** (required) | `priority:high`, `priority:medium`, `priority:low` | Processes high→medium FIFO. `priority:low` skipped |
| **Type** (required) | `type:task`, `type:sub-task`, `type:intervention` | Determines workflow and assignee |
| **Size** (required) | `size:1`, `size:2`, `size:3`, `size:5`, `size:8`, `size:13`, `size:21` | Story points. ≥8 requires decomposition |
| **Status** (orchestrator-managed) | `status:in-progress`, `status:blocked`, `status:ready` | **Never add on creation. Orchestrator manages these.** |
| **Assignee** | `agent` (tasks/sub-tasks), `human` (interventions) | Determines who processes the issue |

## Story Point Guidelines

| Points | Time Estimate | Complexity | When to Use |
|--------|--------------|------------|-------------|
| 1-2 | ~5-10 min | Simple, isolated | Config changes, minor fixes, single-file edits |
| 3 | ~10-15 min | Standard | Clear feature, well-defined scope |
| 5 | ~15-20 min | Moderate | Multiple components, some complexity |
| **8+** | >20 min | **MUST DECOMPOSE** | Split into sub-tasks ≤5 points |
| 13-21 | N/A | Epic/Parent | Tracking only, not worked directly |

**Rule**: Issues ≥8 points MUST be decomposed by task-builder into sub-tasks.

## Issue Types

### type:task (assignee: agent)
Standard work item. Can be standalone or parent to sub-tasks.

### type:sub-task (assignee: agent)
Child of parent task. Must reference parent in title or body. Size ≤5 points.

### type:intervention (assignee: human)
Requires human review/input. Blocks dependent work until resolved.

## Issue Structure

**Title**: `{Component} - {Specific action}`

**Required Labels**: One priority, one type, one size

**Body** (tasks/sub-tasks):
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
- [ ] [Create intervention issue if human review needed]

## Review Protocol
**Agent**: [Specify agent type: general-purpose, code-reviewer, etc. - must be existing agent]
**Focus Areas**:
- [What to review: e.g., "Risk calculations and edge cases"]
- [What to review: e.g., "Integration with existing data pipeline"]
- [What to review: e.g., "Error handling and validation logic"]

## Implementation Notes (optional)
[Technical approach, constraints, dependencies]
[Parent issue: #N (if sub-task)]
```

**Body** (interventions):
```markdown
## Context
Completed work on #[parent-issue] requires human review before proceeding.

## Review Required
- [Specific aspect needing validation]
- [Decision point or trade-off assessment]
- [Risk evaluation]

## Blocking Issues
- Issue #[parent-issue] (status:blocked until review)
- Issue #[dependent-issue] (cannot start until resolved)

## Human Actions
1. Review completed work
2. Add comment with feedback/decision
3. Change label to `status:ready` when resolved
```

## Orchestrator Workflow

**Selection**: Issues with `priority:high|medium`, `type:task|sub-task` (not intervention), and NO `status:*` labels

**Execution**:
1. Adds `status:in-progress`
2. Spawns worker (Claude Code subprocess)
3. On completion:
   - Success: Removes status (returns to pool)
   - Failure: Changes to `status:blocked`

**State Flows**:
```
Task/Sub-task:
[No status] → status:in-progress → [No status]     (success)
                                 → status:blocked   (failure/timeout)

Intervention:
[No status] → (human reviews) → status:ready → (unblocks dependent tasks)
```

**Task Decomposition**:
When task-builder estimates ≥8 points:
1. Create parent issue (type:task, size:X, priority:Y)
2. Create sub-tasks (type:sub-task, size:≤5, priority:Y)
3. Parent tracks progress, sub-tasks are worked by orchestrator

## Validation

**Orchestrator-ready when**:
- Has `priority:high` OR `priority:medium`
- Has `type:task` OR `type:sub-task` (interventions skipped by orchestrator)
- Has `size:*` label (1/2/3/5 for workable issues, 8/13/21 for parent tracking)
- NO `status:*` labels present
- Context ≥ 2 sentences
- Acceptance criteria ≥ 1 testable condition
- Review Protocol specifies agent and focus areas

**task-builder validation**:
- Estimates story points on ALL issues
- Decomposes issues ≥8 points into sub-tasks ≤5 points
- Sets appropriate type label (task/sub-task/intervention)
- Links sub-tasks to parent issue

## Examples

### Example 1: Simple Task (3 points)
```
Title: Config - Add risk limit constants
Labels: priority:medium, type:task, size:3

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

### Example 2: Decomposed Task (Parent = 13 points)
```
Title: [PARENT] DataPipeline - Real-time market data system
Labels: priority:high, type:task, size:13

## Context
Trading firm needs complete market data infrastructure for strategy development. Requires ingestion, validation, and storage.

## Sub-tasks
- #[N+1]: DataPipeline - Connect to data source (size:3)
- #[N+2]: DataPipeline - Implement quote validation (size:5)
- #[N+3]: DataPipeline - Add storage layer (size:5)

## Completion Criteria
All sub-tasks completed and integrated.
```

**Sub-task Example** (from above parent):
```
Title: DataPipeline - Connect to data source
Labels: priority:high, type:sub-task, size:3
Parent: #[N]

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

### Example 3: Intervention
```
Title: Intervention - Risk model validation review
Labels: priority:high, type:intervention
Assignee: @human

## Context
Completed risk model implementation in #[N]. Model calculates position limits based on volatility and account balance. Requires validation before enabling in production.

## Review Required
- Verify risk calculation logic matches trading strategy requirements
- Assess edge cases (zero volatility, max drawdown scenarios)
- Approve risk parameters (conservative enough?)

## Blocking Issues
- Issue #[N] (status:blocked - awaiting risk approval)
- Issue #[N+1] (cannot start live trading until model validated)

## Human Actions
1. Review risk model code and test results
2. Add comment with approval/adjustments needed
3. Change label to `status:ready` when validated
```
