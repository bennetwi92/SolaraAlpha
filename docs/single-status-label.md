# Single Status Label Enforcement

## Problem Fixed

**Before:** Issues accumulated multiple status labels
```
Issue #372 labels:
- status:ready
- status:awaiting-estimation
- status:in-progress
- status:in-review
❌ 4 status labels! Confusing and incorrect.
```

**After:** Issues maintain exactly ONE status label
```
Issue #372 labels:
- status:in-review
✅ Only 1 status label! Clear current state.
```

---

## How It Works

### New Functions Added

**1. `remove_status_labels(issue_num)`**
- Fetches current issue labels
- Finds all labels starting with `status:`
- Removes each one via GitHub API
- Returns success/failure

**2. `update_issue_status(issue_num, new_status)`**
- **Step 1:** Remove ALL existing status labels
- **Step 2:** Add the new status label
- Ensures atomic transition: old → new

### Updated Orchestrator Flow

**Old behavior (accumulation):**
```python
# Issue starts with: status:ready
update_issue_label(372, "status:awaiting-estimation")  # Now has 2 labels
update_issue_label(372, "status:in-progress")          # Now has 3 labels
update_issue_label(372, "status:in-review")            # Now has 4 labels!
```

**New behavior (replacement):**
```python
# Issue starts with: status:ready
update_issue_status(372, "status:awaiting-estimation")
  # 1. Remove: status:ready
  # 2. Add: status:awaiting-estimation
  # Result: ONLY status:awaiting-estimation

update_issue_status(372, "status:in-progress")
  # 1. Remove: status:awaiting-estimation
  # 2. Add: status:in-progress
  # Result: ONLY status:in-progress

update_issue_status(372, "status:in-review")
  # 1. Remove: status:in-progress
  # 2. Add: status:in-review
  # Result: ONLY status:in-review
```

---

## Status Transitions

### Orchestrator Processing

```
status:ready
    ↓ (remove ready, add awaiting-estimation)
status:awaiting-estimation
    ↓ (remove awaiting-estimation, add in-progress)
status:in-progress
    ↓ (remove in-progress, add in-review)
status:in-review
```

At each step, only ONE status label exists.

### Complete Lifecycle

```
[Issue Created]
    status:backlog (only label)
        ↓
[Scheduled for Sprint]
    status:ready (backlog removed, ready added)
        ↓
[Orchestrator Selects]
    status:awaiting-estimation (ready removed)
        ↓
[Work Begins]
    status:in-progress (awaiting-estimation removed)
        ↓
[PR Created]
    status:in-review (in-progress removed)
        ↓
[PR Approved]
    status:ready-to-merge (in-review removed)
        ↓
[Merged to Develop]
    status:in-develop (ready-to-merge removed)
        ↓
[Released to Main]
    (Issue closed, all labels removed)
```

---

## Output Changes

### Before Fix
```
[LABEL] Adding status:awaiting-estimation...
[LABEL] Adding status:in-progress...
[LABEL] Adding status:in-review...
```
*Result: 3 labels added, none removed*

### After Fix
```
[LABEL] Updating status → awaiting-estimation...
[LABEL] Updating status → in-progress...
[LABEL] Updating status → in-review...
```
*Result: Each update removes old, adds new*

---

## Benefits

### Clear State
✅ One label = one clear state
❌ Multiple labels = ambiguous state

### Query Simplicity
```bash
# Find all in-progress issues (clean results)
gh issue list --label "status:in-progress"

# Before fix: Also returned issues with multiple statuses
# After fix: Only issues currently in-progress
```

### Dashboard Accuracy
- Issue counts are accurate (no double-counting)
- Kanban boards work correctly
- Status filters are reliable

### Follows Best Practice
- Each issue has ONE status at a time
- Matches GitHub's recommended label usage
- Aligns with issue tracking conventions

---

## Testing

### Verify Single Status Label

```bash
# Check issue has only one status label
gh issue view 372 --json labels | jq '.labels[] | select(.name | startswith("status:"))'

# Expected: Only ONE status label returned
```

### Test Status Transitions

```python
from orchestrator import update_issue_status

# Start: status:ready
update_issue_status(372, "status:in-progress")
# Result: ONLY status:in-progress

update_issue_status(372, "status:in-review")
# Result: ONLY status:in-review
```

---

## API Calls Per Transition

### Old (Add Only)
- 1 POST request to add label
- **Total: 1 request**

### New (Remove + Add)
- 1 GET request to fetch current labels
- N DELETE requests to remove status labels (typically 1)
- 1 POST request to add new label
- **Total: ~3 requests**

**Impact:** Minimal - still well within GitHub's 5,000 req/hour limit

---

## Edge Cases Handled

### Issue With Multiple Status Labels (Manual Error)
```bash
# Issue manually has: status:ready, status:blocked
update_issue_status(372, "status:in-progress")
# Result: Both removed, only status:in-progress remains
```
✅ **Handles cleanup of manual errors**

### Issue With No Status Label (Invalid)
```bash
# Issue has no status label (shouldn't happen with templates)
update_issue_status(372, "status:in-progress")
# Result: Nothing removed, status:in-progress added
```
✅ **Handles missing status gracefully**

### Failed Status Update
```python
# If remove succeeds but add fails
# Issue will have NO status label temporarily
# Next operation will fix it
```
⚠️ **Edge case:** Brief moment with no status (rare)

---

## Human Label Management

### Manual Status Changes

Humans should also remove old status before adding new:

```bash
# ❌ Wrong way (accumulates labels)
gh issue edit 372 --add-label "status:in-review"

# ✅ Right way (replace status)
gh issue edit 372 --remove-label "status:in-progress"
gh issue edit 372 --add-label "status:in-review"

# Or use helper script
tools/update-issue-status.sh 372 in-review
```

### Helper Script (Optional)

Create `tools/update-issue-status.sh`:
```bash
#!/bin/bash
ISSUE=$1
NEW_STATUS="status:$2"

# Remove all status labels
for label in $(gh issue view $ISSUE --json labels -q '.labels[] | select(.name | startswith("status:")) | .name'); do
  gh issue edit $ISSUE --remove-label "$label"
done

# Add new status
gh issue edit $ISSUE --add-label "$NEW_STATUS"
```

Usage:
```bash
./tools/update-issue-status.sh 372 in-review
# Removes all status labels, adds status:in-review
```

---

## Validation

### Check for Multiple Status Labels

```bash
# Find issues with multiple status labels (should be none)
gh issue list --json number,labels --jq '.[] | select([.labels[] | select(.name | startswith("status:"))] | length > 1) | .number'

# Expected: Empty output (no issues with multiple statuses)
```

### Audit Script

```bash
#!/bin/bash
echo "Auditing status labels..."
issues=$(gh issue list --json number --jq '.[].number')

for issue in $issues; do
  count=$(gh issue view $issue --json labels --jq '[.labels[] | select(.name | startswith("status:"))] | length')
  if [ $count -gt 1 ]; then
    echo "❌ Issue #$issue has $count status labels"
  elif [ $count -eq 0 ]; then
    echo "⚠️  Issue #$issue has NO status label"
  fi
done

echo "✅ Audit complete"
```

---

## Migration Notes

### Cleanup Existing Issues

If you have issues with multiple status labels from before this fix:

```bash
# Script to clean up all issues
for issue in $(gh issue list --json number --jq '.[].number'); do
  echo "Cleaning issue #$issue..."

  # Get all status labels
  labels=$(gh issue view $issue --json labels --jq -r '.labels[] | select(.name | startswith("status:")) | .name')

  # Count them
  count=$(echo "$labels" | wc -l)

  if [ $count -gt 1 ]; then
    echo "  Found $count status labels, keeping only the last one..."

    # Remove all but last
    last_status=$(echo "$labels" | tail -1)
    for label in $labels; do
      if [ "$label" != "$last_status" ]; then
        gh issue edit $issue --remove-label "$label"
        echo "    Removed: $label"
      fi
    done

    echo "    Kept: $last_status"
  fi
done
```

---

## Related Updates

### Documentation Updated
- `gh_issues_standards.md` - Already specifies "one status label at a time"
- `MANDATORY_STATUS_LABELS.md` - Already requires single status
- Now enforced by code! ✅

### Orchestrator Behavior
- Respects single-status rule during processing
- Clean transitions between states
- No label accumulation

---

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Status Labels** | Accumulated (1-4+) | Always exactly 1 |
| **State Clarity** | Ambiguous | Crystal clear |
| **Query Results** | Inaccurate | Accurate |
| **Transitions** | Add only | Remove + Add |
| **API Calls** | 1 per update | 3 per update |
| **Compliance** | ❌ Violated policy | ✅ Enforces policy |

**Status:** ✅ Fixed and Tested
**Version:** 2.1
**Date:** 2025-10-22
