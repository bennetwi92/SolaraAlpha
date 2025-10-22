# Mandatory Status Labels - Policy & Enforcement

## 🚨 Critical Rule

**Every GitHub issue MUST have a status label at all times. No exceptions.**

---

## Why This Matters

**Without mandatory status labels:**
- ❌ Issues get "lost" in an unclear state
- ❌ Orchestrator doesn't know what to process
- ❌ Team can't see pipeline at a glance
- ❌ No clear ownership or next steps

**With mandatory status labels:**
- ✅ Every issue has clear state and ownership
- ✅ Orchestrator knows exactly what to process (`status:ready`)
- ✅ Team can query by state (backlog, in-progress, in-review, etc.)
- ✅ No ambiguity about what happens next

---

## Enforcement Mechanisms

### 1. GitHub Issue Templates (Automatic)

**All issue templates automatically add `status:backlog` label:**

```yaml
# .github/ISSUE_TEMPLATE/bug_report.yml
labels: ["status:backlog", "pattern:fix-bug"]

# .github/ISSUE_TEMPLATE/feature_request.yml
labels: ["status:backlog", "pattern:develop-feature"]
```

**Result:** New issues created via templates start in `status:backlog`

### 2. Manual Issue Creation (Human Responsibility)

If creating issues manually (not recommended), you MUST add `status:backlog`:

```bash
# Correct - includes status:backlog
gh issue create \
  --title "Fix: Bug description" \
  --label "priority:high,status:backlog,pattern:fix-bug,component:core" \
  --body "..."

# WRONG - missing status label (invalid!)
gh issue create \
  --title "Fix: Bug description" \
  --label "priority:high,pattern:fix-bug" \
  --body "..."
```

### 3. Orchestrator Validation (Automatic)

The orchestrator **ONLY** processes issues with `status:ready`:

```python
# Orchestrator selection logic
if "status:ready" in issue_labels and has_priority:
    process_issue()  # ✅ Process
else:
    skip_issue()     # ❌ Skip
```

**Result:** Issues without explicit `status:ready` are never processed

---

## Status Label Lifecycle

### Default State: status:backlog

**All new issues must start here:**

```
[NEW ISSUE CREATED]
    |
    | Issue template automatically adds: status:backlog
    v
[status:backlog]
    - Needs triage
    - Needs priority assignment
    - Awaiting sprint planning
```

### Progression Through States

```
status:backlog → status:ready → status:awaiting-estimation → status:in-progress
    → status:in-review → status:ready-to-merge → status:in-develop → CLOSED
```

**Key Point:** Issue ALWAYS has exactly one status label (never zero, never multiple status labels)

---

## What Each Status Means

| Status | Meaning | Who/What Processes It |
|--------|---------|----------------------|
| `status:backlog` | New issue, not scheduled | Human (triage) |
| `status:ready` | Scheduled for sprint | **Orchestrator** |
| `status:awaiting-estimation` | Being analyzed | Orchestrator/Agent |
| `status:in-progress` | Active development | Agent/Human |
| `status:blocked` | Can't proceed | Human (resolve blocker) |
| `status:in-review` | PR under review | Human (reviewer) |
| `status:ready-to-merge` | Approved | Human (merge) |
| `status:in-develop` | Awaiting release | Automated (Friday) |

---

## Orchestrator Behavior

### ✅ Orchestrator WILL Process

```python
status == "status:ready"
AND priority in ["priority:high", "priority:medium"]
AND state == "open"
AND NOT pull_request
```

**Example:** Issue #42 with `priority:high` + `status:ready`

### ❌ Orchestrator WILL SKIP

**Any issue WITHOUT `status:ready`:**
- `status:backlog` - Not scheduled yet (needs triage)
- `status:awaiting-estimation` - Already being processed
- `status:in-progress` - Already being worked on
- `status:blocked` - Blocked (needs human intervention)
- `status:in-review` - In PR review
- `status:ready-to-merge` - Approved, awaiting merge
- `status:in-develop` - Already completed
- **No status label** - ❌ INVALID (should never happen)

---

## Common Workflows

### Workflow 1: New Bug Report

```bash
# 1. User creates issue via template
# Template automatically adds: status:backlog

# 2. Triage (human)
gh issue edit 42 --add-label "priority:high"
gh issue comment 42 --body "Confirmed - scheduling for sprint"

# 3. Schedule for sprint (human)
gh issue edit 42 --remove-label "status:backlog"
gh issue edit 42 --add-label "status:ready"

# 4. Orchestrator picks up (automatic)
# Adds: status:awaiting-estimation
# Adds: status:in-progress
# Adds: status:in-review (when PR created)

# 5. Human review and merge
gh pr review 123 --approve
gh issue edit 42 --add-label "status:ready-to-merge"
# Merge to develop
gh issue edit 42 --add-label "status:in-develop"

# 6. Friday release
# Issue auto-closes when merged to main
```

### Workflow 2: Blocking an Issue

```bash
# Issue in-progress, blocker discovered
gh issue edit 42 --add-label "status:blocked"
gh issue comment 42 --body "Blocked: Waiting for API access"

# Later: Unblock
gh issue edit 42 --remove-label "status:blocked"
# Returns to status:in-progress, work continues
```

### Workflow 3: Deprioritizing After Estimation

```bash
# Orchestrator selects issue, adds: status:awaiting-estimation
# Agent analyzes: "Too complex for this sprint"

# Return to backlog
gh issue edit 42 --remove-label "status:awaiting-estimation"
gh issue edit 42 --add-label "status:backlog"

# Orchestrator will skip it next iteration
```

---

## Validation & Auditing

### Find Issues Without Status (Should be ZERO)

```bash
# Query all open issues
gh issue list --json number,title,labels --state open | \
  jq '.[] | select(.labels | map(.name) | any(startswith("status:")) | not)'
```

**Expected output:** Empty (no issues without status)

**If found:** Manually add `status:backlog` immediately

### Count Issues by Status

```bash
# Backlog
gh issue list --label "status:backlog" | wc -l

# Ready for orchestrator
gh issue list --label "status:ready" | wc -l

# In progress
gh issue list --label "status:in-progress" | wc -l

# In review
gh issue list --label "status:in-review" | wc -l

# All statuses
for status in backlog ready awaiting-estimation in-progress blocked in-review ready-to-merge in-develop; do
  count=$(gh issue list --label "status:$status" --state open | wc -l)
  echo "status:$status: $count"
done
```

---

## Troubleshooting

### Issue Created Without Status Label

**Problem:** Issue #42 has no status label

**Fix:**
```bash
# Add status:backlog immediately
gh issue edit 42 --add-label "status:backlog"
gh issue comment 42 --body "Added missing status:backlog label (all issues must have status)"
```

### Issue Has Multiple Status Labels

**Problem:** Issue #42 has both `status:ready` and `status:in-progress`

**Fix:**
```bash
# Determine current state, remove incorrect label
gh issue edit 42 --remove-label "status:ready"
# Keep only the correct current status
```

### Orchestrator Not Processing Issue

**Symptoms:** Issue has `priority:high` but orchestrator skips it

**Checklist:**
1. ✅ Does it have `status:ready` label? (Not backlog, not in-progress, etc.)
2. ✅ Does it have `priority:high` or `priority:medium` label?
3. ✅ Is it open (not closed)?
4. ✅ Is it an issue (not a PR)?

**Fix:**
```bash
# If missing status:ready
gh issue edit 42 --add-label "status:ready"

# If has wrong status (e.g., backlog)
gh issue edit 42 --remove-label "status:backlog"
gh issue edit 42 --add-label "status:ready"
```

---

## Policy Summary

### ✅ DO

- Use issue templates (automatically adds `status:backlog`)
- Triage new issues promptly (move from backlog → ready)
- Update status as work progresses
- Remove old status before adding new status (keep exactly ONE status label)

### ❌ DON'T

- Create issues without status labels
- Leave issues in backlog indefinitely (triage regularly)
- Have multiple status labels on same issue
- Remove status label without adding a new one (exception: closing issue)

---

## Quick Reference

### Issue Creation
```bash
# Via template (recommended) - status:backlog added automatically
gh issue create --web

# Manual (must include status:backlog)
gh issue create --label "status:backlog,priority:high,pattern:fix-bug"
```

### Moving to Sprint
```bash
gh issue edit 42 --remove-label "status:backlog"
gh issue edit 42 --add-label "status:ready"
```

### Query Ready Issues
```bash
gh issue list --label "status:ready" --label "priority:high,priority:medium"
```

### Run Orchestrator
```bash
export GITHUB_TOKEN=$(gh auth token)
python tools/orchestrator.py
# Processes ONLY issues with status:ready
```

---

## See Also

- **Complete State Flow:** `gh_issues_standards.md`
- **Orchestrator Usage:** `ORCHESTRATOR_USAGE.md`
- **State Flow Summary:** `ORCHESTRATOR_STATE_FLOW_SUMMARY.md`
- **Issue Standards:** `.claude/GITHUB_ISSUES.md`

---

**Policy Version:** 2.0
**Effective Date:** 2025-10-22
**Status:** ✅ Enforced via templates and orchestrator
