# Orchestrator Refresh Behavior

## 🔄 Dynamic Issue Refresh

The orchestrator now **refreshes the issue list after each task completion** to ensure it always processes the highest priority issue available.

---

## Why This Matters

### Before (Batch Processing)
```
1. Fetch all issues at start: [#372 high, #373 medium, #374 medium]
2. Process #372 (takes 10 minutes)
3. Process #373 (but #375 critical was added during #372!)
4. Process #374
5. Finally process #375 (critical waited 20+ minutes!)
```

❌ **Problem:** New high-priority issues wait until the entire batch completes

### After (Dynamic Refresh)
```
1. Fetch issues: [#372 high, #373 medium]
2. Process #372 (takes 10 minutes)
3. REFRESH issues: [#375 critical, #373 medium] (new issue added!)
4. Process #375 immediately (critical handled right away!)
5. REFRESH issues: [#373 medium]
6. Process #373
```

✅ **Benefit:** New high-priority issues are processed immediately

---

## How It Works

### Processing Flow

```
[START ITERATION]
    ↓
[INNER LOOP START]
    ↓
[REFRESH] Fetch latest issues from GitHub
    ↓
    Issues found?
    ├─ YES → Process FIRST issue (highest priority)
    │         ↓
    │     [COMPLETE] Issue processed
    │         ↓
    │     [LOOP BACK] Refresh issues again
    │
    └─ NO → [IDLE] Sleep 5 minutes, exit inner loop
         ↓
    [OUTER LOOP] Start next iteration
```

### Key Differences

| Aspect | Old Behavior | New Behavior |
|--------|--------------|--------------|
| **Fetch Timing** | Once per iteration | After each task |
| **Priority Updates** | Batch start only | Real-time |
| **New Issues** | Wait for next batch | Processed immediately |
| **Label Changes** | Not detected | Detected instantly |

---

## Example Scenarios

### Scenario 1: New High-Priority Issue Added Mid-Processing

**Timeline:**
```
10:00 - Orchestrator starts
10:00 - Fetch: [#372 high, #373 medium]
10:01 - Start processing #372
10:05 - USER ADDS #375 (priority:critical, status:ready)
10:10 - #372 completes
10:10 - REFRESH: [#375 critical, #373 medium]
10:10 - Start processing #375 (critical handled immediately!)
10:15 - #375 completes
10:15 - REFRESH: [#373 medium]
10:15 - Start processing #373
```

✅ **Result:** Critical issue processed within 5 minutes of being added

### Scenario 2: Issue Moved from Backlog to Ready

**Timeline:**
```
10:00 - Fetch: [#372 high]
10:01 - Start processing #372
10:05 - USER MOVES #374 from backlog → ready (priority:high)
10:10 - #372 completes
10:10 - REFRESH: [#374 high] (now visible!)
10:10 - Start processing #374
```

✅ **Result:** Newly scheduled issue picked up immediately

### Scenario 3: All Issues Completed

**Timeline:**
```
10:00 - Fetch: [#372 high]
10:01 - Start processing #372
10:10 - #372 completes (now has status:in-review)
10:10 - REFRESH: [] (no issues ready)
10:10 - [IDLE] No issues to process. Sleeping 300s...
10:15 - [ITERATION 2] Start
10:15 - REFRESH: [] (still no issues)
10:15 - [IDLE] No issues to process. Sleeping 300s...
```

✅ **Result:** Orchestrator enters idle state, checks every 5 minutes

---

## Output Format

### Old Output (Batch)
```
[FETCH] Found 3 issues (1 high, 2 medium)

[PROCESSING 1/3]
[START] Issue #372...
[SUCCESS] Issue #372 completed successfully

[PROCESSING 2/3]
[START] Issue #373...
[SUCCESS] Issue #373 completed successfully

[PROCESSING 3/3]
[START] Issue #374...
[SUCCESS] Issue #374 completed successfully

[BATCH COMPLETE] Processed 3 issues
  ✅ Success: 3
  ❌ Failed:  0

[IDLE] Sleeping 300s before next batch...
```

### New Output (Dynamic Refresh)
```
[REFRESH] Fetching latest issues...
[FETCH] Found 2 issues (1 high, 1 medium)

[PROCESSING] Next issue: #372
[START] Issue #372...
[SUCCESS] Issue #372 completed successfully

[REFRESH] Fetching latest issues...
[FETCH] Found 1 issues (0 high, 1 medium)

[PROCESSING] Next issue: #373
[START] Issue #373...
[SUCCESS] Issue #373 completed successfully

[REFRESH] Fetching latest issues...
[FETCH] Found 0 issues (0 high, 0 medium)

[IDLE] No issues to process. Sleeping 300s...

[ITERATION COMPLETE] Processed 2 issue(s)
  ✅ Success: 2
  ❌ Failed:  0
```

---

## Behavior Details

### Refresh Frequency
- **After each task:** Issues refreshed after every single task completion
- **On idle:** If no issues found, sleep 5 minutes then refresh
- **Continuous:** No artificial delays between tasks

### Priority Re-evaluation
Every refresh re-evaluates:
1. **New issues** added with `status:ready`
2. **Priority changes** (high → critical, medium → high)
3. **Status changes** (backlog → ready, blocked → ready)
4. **Label updates** (any status label changes)

### Always Processes Highest Priority
- Fetches latest issues
- Sorts: `priority:high` → `priority:medium` (FIFO within tier)
- Processes **only the first issue** (highest priority)
- Repeats

---

## Performance Considerations

### API Rate Limiting
GitHub API limits: **5,000 requests/hour**

**Refresh cost per task:**
- 1 fetch request (list issues)
- 3 label update requests (awaiting-estimation, in-progress, in-review)
- **Total: ~4 requests per task**

**Capacity:** ~1,250 tasks/hour (far more than needed)

### Network Overhead
- Fetch request: ~200-500ms (negligible compared to task time)
- Task time: Typically 2-15 minutes
- **Impact:** < 0.1% overhead

---

## Configuration

### Idle Sleep Duration
```python
IDLE_SLEEP_SECONDS = 300  # 5 minutes
```

**When it sleeps:**
- No issues with `status:ready` found
- Waits 5 minutes before checking again

**To change:**
Edit `IDLE_SLEEP_SECONDS` in `tools/orchestrator.py`

### Worker Timeout
```python
WORKER_TIMEOUT = 3600  # 1 hour
```

**Purpose:** Maximum time per task before timeout

---

## Testing Refresh Behavior

### Test Scenario: Add Issue Mid-Processing

**Setup:**
```bash
# Start orchestrator with one issue
export GITHUB_TOKEN=$(gh auth token)
python tools/orchestrator.py
```

**During processing:**
```bash
# In another terminal, add new high-priority issue
gh issue create \
  --title "Feature: Urgent task" \
  --label "priority:high,status:ready,pattern:develop-feature" \
  --body "Urgent task added during processing"
```

**Expected behavior:**
1. Current issue completes
2. Orchestrator refreshes
3. New high-priority issue processed immediately

### Test Scenario: Change Priority Mid-Processing

**Setup:**
```bash
# Start with medium priority issue processing
```

**During processing:**
```bash
# In another terminal, add critical priority issue
gh issue create \
  --title "Fix: Critical bug" \
  --label "priority:high,status:ready,pattern:fix-bug" \
  --body "Critical bug discovered"
```

**Expected behavior:**
1. Current medium priority issue completes
2. Orchestrator refreshes
3. Critical issue processed next (before other medium issues)

---

## Migration Notes

### Backward Compatibility
✅ **Fully compatible** - No breaking changes to:
- Issue selection logic
- Label management
- Worker spawning
- Error handling

### Differences
- Fetches issues more frequently (after each task vs. per batch)
- No batch summary (shows iteration summary instead)
- No delay between tasks (immediate refresh)

---

## Benefits Summary

| Benefit | Impact |
|---------|--------|
| **Real-time priority** | High-priority issues processed immediately |
| **Dynamic scheduling** | Label changes detected instantly |
| **Responsive to changes** | New issues visible within task completion time |
| **No stale state** | Always working with latest GitHub data |
| **Better UX** | Urgent issues don't wait for batch to complete |

---

## Related Documentation

- **Orchestrator Usage:** `ORCHESTRATOR_USAGE.md`
- **State Flow:** `gh_issues_standards.md`
- **Test Plan:** `ORCHESTRATOR_TEST_PLAN.md`

---

**Version:** 2.0 (Dynamic Refresh)
**Updated:** 2025-10-22
