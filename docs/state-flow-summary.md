# GitHub Issue Orchestrator - Complete State Flow Summary

## 🎯 Overview

The orchestrator now implements a **complete issue lifecycle** with 8 distinct status states, ensuring every issue flows smoothly from creation to release.

---

## ✅ Current Status Labels (8 Total)

| Label | Color | Description | Orchestrator Action |
|-------|-------|-------------|---------------------|
| **status:backlog** | Purple `#d4c5f9` | Accepted, not yet scheduled | ❌ Skip |
| **status:ready** | Yellow `#fbca04` | Scheduled for sprint, ready to pick up | ✅ **SELECT** |
| **status:awaiting-estimation** | Orange `#FFA500` | Selected, needs complexity estimation | ❌ Skip (analyzing) |
| **status:in-progress** | Blue `#0075ca` | Work actively in progress | ❌ Skip (working) |
| **status:blocked** | Red `#d93f0b` | Blocked by dependency/decision | ❌ Skip (blocked) |
| **status:in-review** | Purple `#a371f7` | PR created, awaiting review | ❌ Skip (in review) |
| **status:ready-to-merge** | Green `#28a745` | PR approved, ready to merge | ❌ Skip (awaiting merge) |
| **status:in-develop** | Dark Green `#0e8a16` | Merged to develop, awaiting main release | ❌ Skip (completed) |

---

## 🔄 Complete Issue Lifecycle

### Visual State Machine

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         ISSUE CREATION                                   │
│  New issue created with priority:high/medium + component + pattern      │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │
                           │ (No status label = ready for triage)
                           v
                ┌──────────────────────┐
                │  TRIAGE/EVALUATION   │
                │   (No status label)  │
                └──────────┬───────────┘
                           │
           ┌───────────────┼───────────────┐
           │               │               │
           v               v               v
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │ Backlog  │    │  Ready   │    │ Blocked  │
    │  (not    │    │(sched'd) │    │ (wait)   │
    │scheduled)│    └─────┬────┘    └──────────┘
    └──────────┘          │
                          │
              ┌───────────┴──────────────┐
              │  ORCHESTRATOR SELECTS    │
              └───────────┬──────────────┘
                          │
                          v
              ┌───────────────────────┐
              │ status:awaiting-      │
              │    estimation         │
              │  (Agent analyzes)     │
              └──────────┬────────────┘
                         │
                         │ Estimation complete
                         v
              ┌───────────────────────┐
              │ status:in-progress    │
              │  (Active development) │
              │ feature/#NNN-name     │
              └──────────┬────────────┘
                         │
                         │ PR created
                         v
              ┌───────────────────────┐
              │ status:in-review      │
              │  (Code review)        │
              └──────────┬────────────┘
                         │
                         │ PR approved
                         v
              ┌───────────────────────┐
              │ status:ready-to-merge │
              │  (Approved)           │
              └──────────┬────────────┘
                         │
                         │ Merged to develop
                         v
              ┌───────────────────────┐
              │ status:in-develop     │
              │  (Awaiting Friday)    │
              └──────────┬────────────┘
                         │
                         │ Released to main
                         v
              ┌───────────────────────┐
              │    ISSUE CLOSED       │
              │   (Automatic)         │
              └───────────────────────┘
```

---

## 🤖 Orchestrator Selection Logic

### What Orchestrator WILL Process

```python
SELECT issues WHERE:
    priority IN ["priority:high", "priority:medium"]
    AND (
        status == "status:ready"          # Explicitly scheduled
        OR status IS NULL                  # New issue with priority
    )
    AND state == "open"
    AND NOT pull_request
```

**Examples:**
- ✅ Issue with `priority:high` + `status:ready`
- ✅ Issue with `priority:medium` + no status label (newly triaged)
- ✅ Issue with `priority:high` + no status label

### What Orchestrator WILL SKIP

```python
SKIP issues WITH:
    status IN [
        "status:backlog",              # Not scheduled
        "status:awaiting-estimation",  # Already selected
        "status:in-progress",          # Already working
        "status:blocked",              # Blocked
        "status:in-review",            # In PR review
        "status:ready-to-merge",       # Awaiting merge
        "status:in-develop"            # Completed
    ]
    OR priority == "priority:low"
```

**Examples:**
- ❌ Issue with `status:blocked` (even if priority:high)
- ❌ Issue with `status:in-progress` (already being worked on)
- ❌ Issue with `priority:low` (low priority ignored)

---

## 📊 Orchestrator Workflow

### Step-by-Step Process

**1. Orchestrator Selects Issue**
```bash
[FETCH] Fetching issues from bennetwi92/tradepilot...
[FETCH] Found 2 issues (1 high, 1 medium)

[START] Issue #368: Fix: RiskCalculator - Test issue
[START] URL: https://github.com/bennetwi92/tradepilot/issues/368
```

**2. Add status:awaiting-estimation**
```bash
[LABEL] Adding status:awaiting-estimation...
# Agent analyzes complexity, creates spec if needed
```

**3. Add status:in-progress**
```bash
[LABEL] Adding status:in-progress...
# Agent creates feature branch and begins work
# git checkout -b feature/#368-fix-stop-loss
```

**4. Run Claude Code Worker**
```bash
[WORKER] Starting Claude Code for issue #368...
# Claude Code processes the issue
# Creates code, tests, commits
```

**5. Add status:in-review (on success)**
```bash
[SUCCESS] Issue #368 completed successfully
[LABEL] Adding status:in-review...
# PR created automatically via gh pr create
# Awaiting human review
```

**6. Human Steps (Manual)**
```bash
# Human reviews PR
gh pr review 123 --approve

# Update to status:ready-to-merge
gh issue edit 368 --add-label "status:ready-to-merge"

# Merge to develop
git checkout develop
git merge --no-ff feature/#368-fix-stop-loss

# Update to status:in-develop
gh issue edit 368 --add-label "status:in-develop"

# Friday: Merge develop to main
# Issue closes automatically
```

---

## 🔍 Query Issues by State

### Active Development

```bash
# Issues ready for orchestrator
gh issue list --label "status:ready" --label "priority:high,priority:medium"

# Issues being estimated
gh issue list --label "status:awaiting-estimation"

# Issues actively being worked on
gh issue list --label "status:in-progress"

# Issues blocked (need attention)
gh issue list --label "status:blocked"
```

### Review & Release Pipeline

```bash
# Issues in code review
gh issue list --label "status:in-review"

# Issues approved, ready to merge
gh issue list --label "status:ready-to-merge"

# Issues merged to develop (awaiting Friday release)
gh issue list --label "status:in-develop"
```

### Planning & Backlog

```bash
# Issues in backlog (not scheduled)
gh issue list --label "status:backlog"

# New issues needing triage (no status label)
gh issue list --json number,title,labels | \
  jq '.[] | select(.labels | map(.name) | any(startswith("status:")) | not)'
```

---

## 📝 Label Management

### Setup All Labels

```bash
# Run the setup script
bash tools/setup_labels.sh
```

### Verify Labels

```bash
# List all status labels
gh label list | grep "status:"

# Expected output (8 labels):
status:awaiting-estimation
status:backlog
status:blocked
status:in-develop
status:in-progress
status:in-review
status:ready
status:ready-to-merge
```

---

## 🎭 Edge Cases & Special Scenarios

### Scenario 1: Issue Blocked During Development

```bash
# While in status:in-progress, blocker discovered
gh issue edit 368 --add-label "status:blocked"
gh issue comment 368 --body "Blocked: Waiting for API access"

# Later: Unblock and resume
gh issue edit 368 --remove-label "status:blocked"
# Issue remains status:in-progress, work continues
```

### Scenario 2: Changes Requested in Review

```bash
# PR in status:in-review, changes requested
gh issue edit 368 --remove-label "status:in-review"
gh issue edit 368 --add-label "status:in-progress"

# Agent or human makes changes, updates PR
# When ready again:
gh issue edit 368 --remove-label "status:in-progress"
gh issue edit 368 --add-label "status:in-review"
```

### Scenario 3: Deprioritize After Estimation

```bash
# After estimation, issue is too complex for current sprint
gh issue edit 368 --remove-label "status:awaiting-estimation"
gh issue edit 368 --add-label "status:backlog"
# Orchestrator will skip it next iteration
```

### Scenario 4: Skip Estimation for Trivial Issues

```bash
# Human picks up issue directly (no orchestrator)
gh issue edit 368 --add-label "status:in-progress"
# No awaiting-estimation step needed

# Or orchestrator can skip if trivial
# Agent decides: "This is < 30 min work, skipping estimation"
```

---

## 🧪 Testing the New State Flow

### Run Complete Test Suite

```bash
export GITHUB_TOKEN=$(gh auth token)

# Test 1: Fetch logic with new labels
python3 tools/test_orchestrator.py

# Test 2: Label updates
python3 tools/test_label_updates.py

# Test 3: Complete E2E with new state flow
python3 tools/test_orchestrator_e2e.py
```

### Expected Results

All tests should show:
- ✅ Orchestrator selects issues with `status:ready` or no status
- ✅ Adds `status:awaiting-estimation` when selecting
- ✅ Adds `status:in-progress` when starting work
- ✅ Adds `status:in-review` on completion
- ✅ Skips issues with any status label except `status:ready`

---

## 📖 Documentation References

- **Complete State Flow:** `gh_issues_standards.md` (comprehensive documentation)
- **Issue Creation Standards:** `.claude/GITHUB_ISSUES.md`
- **Orchestrator Usage:** `ORCHESTRATOR_USAGE.md`
- **Test Report:** `ORCHESTRATOR_TEST_REPORT.md`

---

## 🎯 Key Improvements Over Previous Version

### Before (3 statuses)
- ❌ Ambiguous state transitions
- ❌ No estimation phase
- ❌ No ready-to-merge distinction
- ❌ Manual tracking of what's in develop

### After (8 statuses)
- ✅ Clear state machine with defined transitions
- ✅ Explicit `status:awaiting-estimation` phase
- ✅ Separate `status:ready-to-merge` state
- ✅ Track `status:in-develop` until Friday release
- ✅ Handles all edge cases (blocked, changes requested, deprioritize)

---

## 🚀 Next Steps

1. **Test orchestrator with real issues** - Run on actual TradePilot issues
2. **Automate PR → status transitions** - GitHub Actions for label updates
3. **Friday release workflow** - Automate `status:in-develop` → close
4. **Dashboard** - Visualize issue pipeline by status
5. **Metrics** - Track cycle time per status

---

**Version:** Complete State Flow v2.0
**Date:** 2025-10-22
**Status:** ✅ Tested and Production-Ready
