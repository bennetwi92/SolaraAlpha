# GitHub Issues Guide (SolaraAlpha)

Quick reference for creating issues for the AI proprietary trading firm.

## Label Taxonomy

| Category | Valid Labels | Orchestrator Rules |
|----------|-------------|-------------------|
| **Priority** (required) | `priority:high`, `priority:medium`, `priority:low` | Processes high→medium FIFO. `priority:low` skipped |
| **Status** (orchestrator-managed) | `status:in-progress`, `status:blocked` | **Never add on creation. Orchestrator manages these.** |

## Issue Structure

**Title**: `{Component} - {Specific action}`

**Body**:
```markdown
## Context
[Why needed? 2-4 sentences explaining business impact or necessity]

## Requirements (features) OR Current/Expected Behavior (bugs)
- [Specific, testable items]

## Acceptance Criteria
- [ ] [Testable completion condition]
- [ ] [Test coverage if code changes]
- [ ] [Peer review completed via Task tool before commit]
- [ ] [Any safety considerations]

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

**Selection**: Issues with `priority:high|medium` and NO `status:*` labels
**Execution**: Adds `status:in-progress` → spawns worker → removes status (success) OR changes to `status:blocked` (failure)
**State Flow**:
```
[No status] → status:in-progress → [No status]     (success)
                                 → status:blocked   (failure/timeout)
```

## Validation

**Orchestrator-ready when**:
- Has `priority:high` OR `priority:medium`
- NO `status:*` labels present
- Context ≥ 2 sentences
- Acceptance criteria ≥ 1 testable condition
- Review Protocol specifies agent and focus areas

## Example

```
Title: DataPipeline - Real-time quote ingestion
Labels: priority:high

## Context
Trading firm needs market data to develop strategies. Must ingest real-time quotes with validation.

## Requirements
- Connect to data source
- Store quotes with <1s latency
- Validate data quality (detect gaps, bad values)

## Acceptance Criteria
- [ ] Real-time quotes stored successfully
- [ ] Data quality validation detects edge cases
- [ ] Unit and E2E tests included
- [ ] Peer review completed via Task tool before commit

## Review Protocol
**Agent**: general-purpose
**Focus Areas**:
- Data validation logic and edge case handling
- Latency requirements (<1s) verification
- Error handling for connection failures
- Test coverage completeness
```
