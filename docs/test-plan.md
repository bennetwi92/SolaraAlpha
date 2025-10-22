# Orchestrator Test Plan - Complete Flow Validation

## Test Overview

This test plan validates the complete orchestrator workflow using 5 test issues with simple, actionable tasks.

---

## Test Issues Created

### ✅ Issues That SHOULD Be Processed

| Issue # | Title | Priority | Status | Expected Behavior |
|---------|-------|----------|--------|-------------------|
| #372 | Feature: Create hello.txt file | **priority:high** | **status:ready** | **PROCESS FIRST** - Simple file creation |
| #373 | Feature: Convert hello.txt to markdown | **priority:medium** | **status:ready** | **PROCESS SECOND** - File conversion |

### ❌ Issues That SHOULD Be Skipped

| Issue # | Title | Priority | Status | Expected Behavior | Reason |
|---------|-------|----------|--------|-------------------|--------|
| #374 | Feature: Add documentation | priority:medium | **status:backlog** | **SKIP** | Not scheduled yet |
| #375 | Fix: Critical security issue | priority:high | **status:blocked** | **SKIP** | Blocked |
| #376 | Enhancement: Low priority test | **priority:low** | status:ready | **SKIP** | Low priority ignored |

---

## Pre-Flight Checklist

Before running the orchestrator, verify the setup:

```bash
# 1. Check all issues exist with correct labels
gh issue list --json number,title,labels --limit 10

# 2. Verify GitHub token is set
echo $GITHUB_TOKEN || export GITHUB_TOKEN=$(gh auth token)

# 3. Verify orchestrator can fetch issues
export GITHUB_TOKEN=$(gh auth token) && python3 tools/test_orchestrator.py
```

**Expected pre-flight output:**
```
[FETCH] Found 2 issues (1 high, 1 medium)
  [1] Issue #372 (high priority, status:ready)
  [2] Issue #373 (medium priority, status:ready)
```

---

## Running the Orchestrator

### Option 1: Dry Run (Recommended First)

Test the orchestrator selection logic without spawning workers:

```bash
export GITHUB_TOKEN=$(gh auth token)
python3 tools/test_orchestrator.py
```

**Expected output:**
- ✅ Issue #372 included (high priority + status:ready)
- ✅ Issue #373 included (medium priority + status:ready)
- ✅ Issue #374 excluded (status:backlog)
- ✅ Issue #375 excluded (status:blocked)
- ✅ Issue #376 excluded (priority:low)

### Option 2: Full Orchestrator Run

**⚠️ WARNING:** This will spawn Claude Code workers and process issues for real.

```bash
export GITHUB_TOKEN=$(gh auth token)
python tools/orchestrator.py
```

**To stop:** Press `Ctrl+C`

---

## Expected Orchestrator Behavior

### Issue #372 Processing (First - High Priority)

```
[FETCH] Found 2 issues (1 high, 1 medium)

[START] Issue #372: Feature: Create hello.txt file with greeting
[START] URL: https://github.com/bennetwi92/tradepilot/issues/372

[LABEL] Adding status:awaiting-estimation...
[LABEL] Adding status:in-progress...

[WORKER] Starting Claude Code for issue #372...
# Claude Code creates hello.txt file
# Commits to feature/#372-create-hello-txt
# Creates PR

[SUCCESS] Issue #372 completed successfully
[LABEL] Adding status:in-review...
```

**Artifacts Created:**
- File: `hello.txt` (in project root)
- Branch: `feature/#372-create-hello-txt`
- PR: Linked to issue #372
- Labels: `status:awaiting-estimation`, `status:in-progress`, `status:in-review`

### Issue #373 Processing (Second - Medium Priority)

```
[START] Issue #373: Feature: Convert hello.txt to hello.md markdown format
[START] URL: https://github.com/bennetwi92/tradepilot/issues/373

[LABEL] Adding status:awaiting-estimation...
[LABEL] Adding status:in-progress...

[WORKER] Starting Claude Code for issue #373...
# Claude Code converts hello.txt to hello.md
# Commits to feature/#373-convert-hello-md
# Creates PR

[SUCCESS] Issue #373 completed successfully
[LABEL] Adding status:in-review...
```

**Artifacts Created:**
- File: `hello.md` (in project root)
- Branch: `feature/#373-convert-hello-md`
- PR: Linked to issue #373
- Labels: `status:awaiting-estimation`, `status:in-progress`, `status:in-review`

### Batch Complete

```
================================================================================
[BATCH COMPLETE] Processed 2 issues
  ✅ Success: 2
  ❌ Failed:  0
================================================================================

[IDLE] Sleeping 300s before next batch...
```

### Next Fetch Cycle

After completing #372 and #373, the orchestrator should find 0 issues:

```
[FETCH] Fetching issues from bennetwi92/tradepilot...
[FETCH] Found 0 issues (0 high, 0 medium)

[IDLE] No issues to process. Sleeping 300s...
```

**Why 0 issues?**
- #372: Has `status:in-review` (no longer `status:ready`)
- #373: Has `status:in-review` (no longer `status:ready`)
- #374: Still `status:backlog` (not ready)
- #375: Still `status:blocked` (blocked)
- #376: Still `priority:low` (ignored)

---

## Validation Steps

### After Orchestrator Run

**1. Check Issue Labels**

```bash
# Issue #372 should have status:in-review
gh issue view 372 --json labels | jq '.labels[].name' | grep status:

# Issue #373 should have status:in-review
gh issue view 373 --json labels | jq '.labels[].name' | grep status:

# Issue #374 should still have status:backlog (unchanged)
gh issue view 374 --json labels | jq '.labels[].name' | grep status:
```

**Expected:**
- #372: `status:awaiting-estimation`, `status:in-progress`, `status:in-review`
- #373: `status:awaiting-estimation`, `status:in-progress`, `status:in-review`
- #374: `status:backlog` (unchanged)
- #375: `status:blocked` (unchanged)
- #376: `status:ready` (unchanged - just never selected)

**2. Check Created Files**

```bash
# Verify hello.txt exists
test -f hello.txt && echo "✅ hello.txt exists" || echo "❌ hello.txt missing"

# Verify hello.md exists
test -f hello.md && echo "✅ hello.md exists" || echo "❌ hello.md missing"

# View contents
cat hello.txt
cat hello.md
```

**3. Check Branches**

```bash
# List feature branches
git branch -a | grep "feature/#37"

# Expected output:
# feature/#372-create-hello-txt
# feature/#373-convert-hello-md
```

**4. Check PRs**

```bash
# List open PRs
gh pr list --state open

# Should show 2 PRs linked to issues #372 and #373
```

---

## Human Review Workflow

After orchestrator completes, perform human review:

### Review Issue #372

```bash
# View PR
gh pr view <PR_NUMBER>

# Review code changes
gh pr diff <PR_NUMBER>

# Approve PR
gh pr review <PR_NUMBER> --approve

# Update issue status
gh issue edit 372 --add-label "status:ready-to-merge"

# Merge to develop
gh pr merge <PR_NUMBER> --squash
git checkout develop
git pull

# Update issue status
gh issue edit 372 --add-label "status:in-develop"
```

### Review Issue #373

```bash
# Same process as #372
gh pr view <PR_NUMBER>
gh pr review <PR_NUMBER> --approve
gh issue edit 373 --add-label "status:ready-to-merge"
gh pr merge <PR_NUMBER> --squash
gh issue edit 373 --add-label "status:in-develop"
```

---

## Cleanup After Testing

### Option 1: Keep Test Files (Recommended)

Keep `hello.txt` and `hello.md` as proof of orchestrator functionality:

```bash
# Just close the test issues
gh issue close 372 373 374 375 376 --comment "Orchestrator test complete"
```

### Option 2: Full Cleanup

Remove all test artifacts:

```bash
# Close issues
gh issue close 372 373 374 375 376 --comment "Orchestrator test complete - cleaning up"

# Delete test files
rm -f hello.txt hello.md

# Delete feature branches (if not merged)
git branch -D feature/#372-create-hello-txt 2>/dev/null
git branch -D feature/#373-convert-hello-md 2>/dev/null

# Commit cleanup
git add -A
git commit -m "chore: remove orchestrator test files"
```

---

## Troubleshooting

### Orchestrator Finds 0 Issues

**Problem:** `[FETCH] Found 0 issues`

**Check:**
```bash
gh issue list --label "status:ready" --label "priority:high,priority:medium"
```

**Fix:** Ensure issues #372 and #373 have `status:ready`:
```bash
gh issue edit 372 --add-label "status:ready"
gh issue edit 373 --add-label "status:ready"
```

### Orchestrator Processes Wrong Issues

**Problem:** Orchestrator picks up #374, #375, or #376

**Check:** Verify these issues have blocking labels:
```bash
gh issue view 374 --json labels
gh issue view 375 --json labels
gh issue view 376 --json labels
```

**Fix:**
- #374 should have `status:backlog`
- #375 should have `status:blocked`
- #376 should have `priority:low`

### Claude Code Worker Fails

**Problem:** `[FAILED] Issue #372 failed with exit code 1`

**Check:**
- Is Claude Code CLI installed? `claude --version`
- Is the prompt clear and actionable?
- Check worker output for errors

**Fix:** Review issue #372 details and re-run manually:
```bash
claude chat "Create file hello.txt in project root with Hello World content"
```

---

## Success Criteria

The test is successful if:

### ✅ Selection
- Orchestrator fetches exactly 2 issues (#372, #373)
- Issues processed in order: high → medium priority
- Other issues (#374, #375, #376) correctly skipped

### ✅ Processing
- Both issues complete successfully
- Labels updated: `awaiting-estimation` → `in-progress` → `in-review`
- Feature branches created
- PRs created and linked

### ✅ Artifacts
- `hello.txt` file exists with correct content
- `hello.md` file exists with markdown formatting
- Commits pushed to feature branches

### ✅ Second Fetch
- Next fetch finds 0 issues (all processed issues have `status:in-review`)
- Orchestrator enters idle state

---

## Test Plan Summary

| Test Case | Validates |
|-----------|-----------|
| Issue #372 (high + ready) | High priority processing, file creation |
| Issue #373 (medium + ready) | Medium priority processing, file conversion |
| Issue #374 (backlog) | Backlog exclusion logic |
| Issue #375 (blocked) | Blocked issue exclusion |
| Issue #376 (low priority) | Low priority exclusion |

**Total Validations:** 5 issue states, 2 successful workflows, label transitions, artifact creation

---

**Test Plan Version:** 1.0
**Created:** 2025-10-22
**Status:** ✅ Ready to Execute
