#!/bin/bash
# Create the bootstrap issue for SolaraAlpha

set -e

gh issue create --repo bennetwi92/SolaraAlpha \
  --title "Organization - Initialize SolaraAlpha trading firm" \
  --label "priority:high,type:task" \
  --body "$(cat <<'EOF'
## Context

SolaraAlpha is an AI-driven proprietary trading firm. Mission: Maximize shareholder wealth through systematic, risk-managed trading.

**Priority**: Risk Management > Profitability > Operational Efficiency

You are the CEO. The repository exists but the trading firm does not. Your job is to build it.

## Objective

Build SolaraAlpha into a profitable, risk-managed trading operation.

## Authority & Resources

You have **full autonomy** to decide:
- What to build and in what order
- How to organize the firm
- What technology, strategies, and processes to use
- What agents, systems, and infrastructure are needed
- How to measure success

**Available resources:**
- Read `CLAUDE.md` for mission and principles
- Available agents: task-builder, team-builder, skill-builder, general-purpose
- Constraint: All technologies/services must be free

## Acceptance Criteria

- [ ] Strategic path forward determined
- [ ] Next tasks created via task-builder for orchestrator to execute
- [ ] Work committed AND pushed

## Execution

**Agent**: See `.claude/agents/ceo-orchestrator.md`

This is an AI-only organization optimizing for AI-to-AI workflows. Make decisions that maximize long-term shareholder value.
EOF
)"

echo "✅ Bootstrap issue created successfully"
