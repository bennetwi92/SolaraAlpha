# Create GitHub Issue

## When
User requests: "Create issue", "Add GitHub issue", "Track [work]"

## Decision Tree

**Priority** (choose ONE):
- Risk/trading safety → priority:high
- Profitability/core strategy → priority:medium or high
- Efficiency/nice-to-have → priority:medium or low

**Type** (choose ONE):
- Broken behavior → bug
- New capability → feature
- Improve existing → enhancement
- Code restructure → refactor
- Docs only → docs

**Domain** (phase-aligned):
- Phase 1 → domain:data
- Phase 2 → domain:backtest
- Phase 3 → domain:strategy
- Phase 4 → domain:risk
- Phase 5 → domain:execution

**NEVER add status:\* labels on creation** (orchestrator manages)

## Format

**Title**: `{Type}: {Component} - {Action}` (<80 chars)

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
  --title "Type: Component - Action" \
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
  --label "priority:LEVEL,TYPE,domain:DOMAIN"
```

## Validate

- Has priority:high OR medium
- NO status:\* labels
- Context ≥ 2 sentences
- Acceptance ≥ 1 testable condition
- Title specific, <80 chars

## Example

```bash
gh issue create \
  --title "Fix: RiskCalculator - Position limit ignores pre-market orders" \
  --label "priority:high,bug,domain:risk" \
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

- Multiple priority labels → Use exactly ONE
- Adding status:in-progress → Blocks orchestrator
- Vague title ("Fix bug") → Be specific
- No test requirements → Always specify for trading logic
