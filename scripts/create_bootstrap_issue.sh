#!/bin/bash
# Create the bootstrap issue for SolaraAlpha

set -e

gh issue create --repo bennetwi92/SolaraAlpha \
  --title "Feature: Organization - Initialize SolaraAlpha trading firm" \
  --label "priority:high,feature" \
  --body "$(cat <<'EOF'
## Context

SolaraAlpha is an AI-driven proprietary trading firm with the objective to maximize shareholder wealth through systematic, risk-managed trading. The CEO agent (ceo-orchestrator) needs to initialize organizational structure, assess current state, and determine the strategic path forward to build a profitable trading operation.

This is the bootstrap issue - the CEO's first task is to establish the foundation for autonomous operation.

## Requirements

The CEO should:
- Assess the current state (empty repository, agents available, orchestrator ready)
- Determine what organizational infrastructure is needed (e.g., `/org/` state files for vision, strategy, metrics, structure, decisions)
- Decide on strategic approach to build trading capability
- Define success criteria for the trading firm
- Prioritize initial work using RICE or similar framework
- Create subsequent issues via task-builder for identified priorities

The CEO has full autonomy to decide:
- What phases or milestones to define (if any)
- What technology stack to use
- What agents to create
- What processes to establish
- How to measure progress
- How to achieve the wealth maximization objective

## Acceptance Criteria

- [ ] CEO has assessed organizational needs and documented approach
- [ ] Organizational state files created (if CEO deems necessary)
- [ ] Strategic priorities identified and documented with rationale
- [ ] Next set of issues created via task-builder for immediate work
- [ ] Clear path established from current state to wealth-generating trading systems

## Implementation Notes

- CEO agent should read CLAUDE.md for mission and principles
- CEO has access to: task-builder, team-builder, skill-builder, general-purpose agents
- CEO should delegate issue creation to task-builder (never create issues directly)
- Focus on autonomous decision-making aligned with "Risk > Profit > Efficiency" priority
- Remember: This is an AI-only organization optimizing for AI-to-AI workflows
EOF
)"

echo "✅ Bootstrap issue created successfully"
