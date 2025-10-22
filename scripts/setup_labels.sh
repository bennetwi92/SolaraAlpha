#!/bin/bash
# Setup GitHub labels for complete issue state flow

echo "================================================================================"
echo "Setting up GitHub Issue Status Labels"
echo "================================================================================"
echo ""

# Create new labels
echo "[1] Creating new labels..."

gh label create "status:awaiting-estimation" \
  --description "Selected by orchestrator, needs complexity estimation" \
  --color "FFA500" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "    ✅ Created: status:awaiting-estimation"
else
    echo "    ⚠️  status:awaiting-estimation already exists"
fi

gh label create "status:ready-to-merge" \
  --description "PR approved, ready to merge to develop" \
  --color "28a745" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "    ✅ Created: status:ready-to-merge"
else
    echo "    ⚠️  status:ready-to-merge already exists"
fi

echo ""
echo "[2] Updating existing label descriptions..."

gh label edit "status:ready" \
  --description "Scheduled for current sprint, ready to be picked up" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "    ✅ Updated: status:ready"
else
    echo "    ⚠️  Could not update status:ready"
fi

gh label edit "status:in-progress" \
  --description "Work actively in progress, feature branch created" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "    ✅ Updated: status:in-progress"
else
    echo "    ⚠️  Could not update status:in-progress"
fi

gh label edit "status:in-review" \
  --description "PR created, awaiting code review and approval" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "    ✅ Updated: status:in-review"
else
    echo "    ⚠️  Could not update status:in-review"
fi

gh label edit "status:in-develop" \
  --description "Merged to develop, awaiting Friday release to main" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "    ✅ Updated: status:in-develop"
else
    echo "    ⚠️  Could not update status:in-develop"
fi

gh label edit "status:blocked" \
  --description "Blocked by dependency, decision, or external factor" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "    ✅ Updated: status:blocked"
else
    echo "    ⚠️  Could not update status:blocked"
fi

gh label edit "status:backlog" \
  --description "Accepted issue, not yet scheduled for current sprint" 2>/dev/null
if [ $? -eq 0 ]; then
    echo "    ✅ Updated: status:backlog"
else
    echo "    ⚠️  Could not update status:backlog"
fi

echo ""
echo "[3] Removing deprecated labels..."

gh label delete "status:waiting-for-review" --yes 2>/dev/null
if [ $? -eq 0 ]; then
    echo "    ✅ Deleted: status:waiting-for-review (use status:in-review instead)"
else
    echo "    ⚠️  status:waiting-for-review already removed or doesn't exist"
fi

echo ""
echo "================================================================================"
echo "Label setup complete!"
echo "================================================================================"
echo ""
echo "View all status labels:"
echo "  gh label list | grep status:"
echo ""
echo "See gh_issues_standards.md for complete state flow documentation"
