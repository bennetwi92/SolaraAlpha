# Create GitHub Issue

## When
User requests: "Create issue", "Add GitHub issue", "Track [work]"

## Decision Tree

**Type** (required):
- All work items → type:task

**NEVER add status:\* labels on creation** (orchestrator manages)
**Note**: Orchestrator processes all type:task issues in pure FIFO order (oldest first)

## Format

**Title**: `{Component} - {Action}` (<80 chars)

**Body**:
```markdown
## Context
[Why needed? 2-4 sentences. Risk/profit/efficiency impact.]

## Requirements (features) OR Current/Expected Behavior (bugs)
- [Specific, testable items]

## Acceptance Criteria
- [ ] [Testable condition]
- [ ] [Test coverage if code change]
- [ ] [Paper trading if trading logic]
```

## Command

```bash
gh issue create \
  --repo REPO_OWNER/REPO_NAME \
  --title "Component - Action" \
  --body "$(cat <<'EOF'
## Context
[2-4 sentences]

## Requirements
- [Item 1]
- [Item 2]

## Acceptance Criteria
- [ ] [Condition 1]
- [ ] [Test requirement]
EOF
)" \
  --label "type:task"
```

## Validate

- Has type:task label
- NO status:\* labels
- Context ≥ 2 sentences
- Acceptance ≥ 1 testable condition
- Title specific, <80 chars

## Example

```bash
gh issue create \
  --title "RiskCalculator - Position limit ignores pre-market orders" \
  --label "type:task" \
  --body "$(cat <<'EOF'
## Context
Pre-market orders bypass position limit validation, exposing capital to oversized positions. Violates firm risk policy.

## Current Behavior
RiskCalculator.check_position_limits() only validates market hours orders.

## Expected Behavior
All orders (pre-market, regular, after-hours) validate against position limits.

## Acceptance Criteria
- [ ] RiskCalculator validates all session types
- [ ] Unit test: Pre-market order exceeding limit rejected
- [ ] Paper trading validation
EOF
)"
```

## Gotchas

- Adding status:* labels → Blocks orchestrator (it manages these)
- Vague title ("Fix bug") → Be specific
- No test requirements → Always specify for critical logic
- Issues processed FIFO → Creation order matters
