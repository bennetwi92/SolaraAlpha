# GitHub Issues Guide (AI Agent Reference)

Quick reference for creating/validating issues that orchestrator scripts can process autonomously.

## Label Taxonomy

| Category | Valid Labels | Orchestrator Rules |
|----------|-------------|-------------------|
| **Priority** (required) | `priority:high`, `priority:medium`, `priority:low` | Processes high→medium FIFO. `priority:low` skipped |
| **Type** (required) | `bug`, `feature`, `enhancement`, `refactor`, `docs` | Must match title prefix |
| **Status** (blocking) | `status:blocked`, `status:in-progress`, `status:waiting-review`, `status:backlog` | **Orchestrator skips if any present** |
| **Component** (optional) | `risk-calculator`, `broker-service`, `risk-one` | Improves context, not required |

### Labels to AVOID
- Generic: `good-first-issue`, `help-wanted` (orchestrator ignores)
- Redundant: Multiple type labels, duplicate status markers
- Custom without pattern: Don't create new label categories

## Issue Structure Template

**Title**: `{Type}: {Component} - {Specific action}`
```
✓ Fix: RiskOne - Trailing stop HWM not updating with position high
✓ Feature: BrokerService - Add bulk order cancellation endpoint
✗ Fix bug (too generic)
✗ Update UI (no component or specifics)
```

**Body** (sections as orchestrator prompt variables):
```markdown
## Context
[Why needed? Business impact? User pain point?]

## Current Behavior (bugs only)
[What's broken? Observable symptoms?]

## Expected Behavior (bugs only)
[What should happen instead?]

## Reproduction (bugs only)
1. [Specific steps to reproduce]

## Requirements (features/enhancements)
- [Specific, testable requirement]
- [Clear scope boundaries]

## Acceptance Criteria (required for all)
- [ ] Testable completion condition
- [ ] Test coverage requirement (if code change)
- [ ] Safety consideration (paper vs live if trading logic)

## Implementation Notes (optional)
[Technical approach, constraints, dependencies]

## Links (when applicable)
- Related: #NNN
- Spec: /docs/features/NNN-name/
- ADR: /docs/architecture/adr/NNN-name.md
```

## Validation Rules

**Orchestrator-ready when**:
- [ ] Has `priority:high` OR `priority:medium` label
- [ ] Has exactly one type label matching title prefix
- [ ] NO blocking status labels present
- [ ] Context ≥ 2 sentences (explains "why")
- [ ] Acceptance criteria ≥ 1 testable condition
- [ ] Title ≤ 80 chars and specific

**Orchestrator skips if**:
- Missing required labels (priority or type)
- Has ANY `status:*` label (blocked, in-progress, etc.)
- Locked or closed issue
- Already has linked PR
- No acceptance criteria (low confidence)

## Orchestrator Workflow

**Selection Algorithm**:
1. Filter: `priority:high OR priority:medium`
2. Exclude: Any `status:*` labels
3. Sort: FIFO by `created_at` within priority tier
4. Process: Sequential execution (one at a time)

**State Tracking**:
- Adds `status:in-progress` when starting work
- Removes status label on success
- Adds `status:blocked` on failure with error comment

**Issue Body → Prompt Variables**:
```
Context → ${ISSUE_CONTEXT}
Requirements → ${ISSUE_REQUIREMENTS}
Acceptance Criteria → ${ISSUE_ACCEPTANCE}
Full Body → ${ISSUE_BODY}
```

## TradePilot-Specific Requirements

**Architecture Awareness**:
- Specify component: RiskCalculator (pure functions), BrokerService (IBKR API), RiskOne (MVVM UI)
- Reference CLAUDE.md principles: simple > clever, correctness paramount

**Trading Safety**:
- Note paper vs live mode implications
- Specify test coverage for financial logic
- Flag broker-dependent changes (need user testing)

**Testing Requirements**:
- RiskCalculator changes: Unit tests required
- BrokerService changes: E2E tests with mock TWS
- RiskOne changes: ViewModel tests + UI verification

**Documentation Links**:
- Feature specs: `/docs/features/NNN-name/specification.md`
- ADRs: `/docs/architecture/adr/NNN-name.md`
- Component guides: `src/{component}/CLAUDE.md`

## Quality Tiers

| Tier | Criteria | Action |
|------|----------|--------|
| **Orchestrator-ready** | All validation rules pass, sufficient context | Auto-process |
| **Human-ready** | Minor details missing, needs judgment | Manual review |
| **Not ready** | Unclear intent, missing context, conflicting requirements | Ask questions, request clarification |

## Common Pitfalls

- ❌ Generic titles: "Fix bug", "Add feature"
- ❌ Missing context: No explanation of business impact
- ❌ Vague acceptance criteria: "Make it work"
- ❌ No component reference: Unclear scope
- ❌ Missing links: No specs, ADRs, or related issues
- ❌ Wrong status labels: `status:in-progress` blocks orchestrator
- ❌ No testing strategy: Especially critical for financial logic

## Examples

**Good Bug Issue**:
```
Title: Fix: RiskOne - Trailing stop HWM not updating with position high
Labels: priority:high, bug, risk-one

## Context
Users lose money when trailing stops lag behind actual peak price. Current
implementation only tracks fills, missing position highs from market data.

## Current Behavior
HWM updates only on order fills. Price rises to $15 with no sells, HWM stays at $10.

## Expected Behavior
HWM should track highest price seen since entry, regardless of fills.

## Acceptance Criteria
- [ ] HWM updates in real-time from market data feed
- [ ] New test: HWM increases when price rises without fill
- [ ] UI displays HWM and position high correctly

## Links
- ADR: /docs/architecture/adr/005-position-state.md
```

**Good Feature Issue**:
```
Title: Feature: BrokerService - Add bulk order cancellation endpoint
Labels: priority:medium, feature, broker-service

## Context
Day traders need quick exits in volatile markets. Current one-by-one cancellation
causes dangerous delays.

## Requirements
- POST /orders/cancel-all endpoint accepts symbol filter
- Cancels all open orders (or filtered by symbol)
- Returns cancelled order IDs, handles IBKR errors gracefully
- Updates order status via WebSocket to UI

## Acceptance Criteria
- [ ] Endpoint cancels all/filtered orders successfully
- [ ] Returns 200 with cancelled IDs
- [ ] WebSocket broadcasts events
- [ ] Tests cover success + partial failure scenarios

## Links
- Component guide: /src/broker_service/CLAUDE.md
```
