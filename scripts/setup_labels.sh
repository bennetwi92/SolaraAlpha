#!/bin/bash
# Setup GitHub labels for SolaraAlpha orchestrator

set -e

REPO="${GITHUB_REPO:-bennetwi92/SolaraAlpha}"

echo "Setting up labels for $REPO..."

# Priority labels (required - orchestrator selection)
gh label create "priority:high" --color "d73a4a" --description "High priority - process first" --repo "$REPO" --force
gh label create "priority:medium" --color "fbca04" --description "Medium priority - process after high" --repo "$REPO" --force
gh label create "priority:low" --color "0e8a16" --description "Low priority - orchestrator skips" --repo "$REPO" --force

# Status labels (orchestrator-managed - never add manually)
gh label create "status:in-progress" --color "0075ca" --description "Work actively in progress" --repo "$REPO" --force
gh label create "status:blocked" --color "d93f0b" --description "Failed or blocked, needs manual review" --repo "$REPO" --force

echo "✅ Labels created successfully for $REPO"
echo ""
echo "Verify with: gh label list --repo $REPO"
