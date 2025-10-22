# GitHub Issue Orchestrator - Usage Guide

## Quick Start

### Prerequisites
1. GitHub CLI installed and authenticated: `gh auth status`
2. Python 3.7+ with `requests` library: `pip install requests`
3. Repository: `bennetwi92/tradepilot`

### Running the Orchestrator

```bash
# Export GitHub token (required)
export GITHUB_TOKEN=$(gh auth token)

# Run orchestrator in continuous mode
python tools/orchestrator.py
```

The orchestrator will:
1. Fetch open issues with `priority:high` or `priority:medium` labels
2. Skip issues with blocking status labels (`status:blocked`, `status:in-progress`, `status:waiting-for-review`)
3. Process issues sequentially (high priority first, then medium in FIFO order)
4. Add `status:in-progress` when starting work
5. Add `status:waiting-for-review` on successful completion
6. Sleep for 5 minutes between batches

### Stopping the Orchestrator

Press `Ctrl+C` to gracefully shut down the orchestrator.

---

## What Happens When Orchestrator Runs

### Example Output

```
================================================================================
GitHub Issue Orchestrator - Lightweight POC
Repository: bennetwi92/tradepilot
Worker timeout: 3600s
Idle sleep: 300s
================================================================================

Press Ctrl+C to stop

================================================================================
[ITERATION 1] 2025-10-22 14:30:00
================================================================================

[FETCH] Fetching issues from bennetwi92/tradepilot...
[FETCH] Found 2 issues (1 high, 1 medium)

[PROCESSING 1/2]

[START] Issue #368: Fix: RiskCalculator - Test issue for orchestrator
[START] URL: https://github.com/bennetwi92/tradepilot/issues/368
[WORKER] Starting Claude Code for issue #368...

# Claude Code will process the issue here
# (you'll see Claude's output in real-time)

[WORKER] Completed in 245.3s with exit code 0
[SUCCESS] Issue #368 completed successfully

[PROCESSING 2/2]

[START] Issue #369: Feature: BrokerService - Test medium priority feature
[START] URL: https://github.com/bennetwi92/tradepilot/issues/369
[WORKER] Starting Claude Code for issue #369...

# Claude Code processes second issue

================================================================================
[BATCH COMPLETE] Processed 2 issues
  ✅ Success: 2
  ❌ Failed:  0
================================================================================

[IDLE] Sleeping 300s before next batch...
```

---

## Label-Based Workflow

### Issue Selection Logic

**Included:**
- ✅ Has `priority:high` OR `priority:medium` label
- ✅ Status: `open`
- ✅ No blocking labels present

**Excluded:**
- ❌ Has `priority:low` label
- ❌ Has any `status:*` label (blocked, in-progress, waiting-for-review)
- ❌ Is a pull request
- ❌ Is closed or locked

### Label State Transitions

```
[Open Issue]
    |
    | (priority:high or priority:medium, no status labels)
    v
[Orchestrator Selects]
    |
    | Add: status:in-progress
    v
[Claude Code Processing]
    |
    +-- Success --> Add: status:waiting-for-review
    |
    +-- Failure --> Keep: status:in-progress (for manual review)
```

### Example Issue Labels

**Before Orchestrator:**
```
priority:high
pattern:fix-bug
component:core
```

**During Processing:**
```
priority:high
pattern:fix-bug
component:core
status:in-progress      ← Added by orchestrator
```

**After Success:**
```
priority:high
pattern:fix-bug
component:core
status:in-progress
status:waiting-for-review    ← Added by orchestrator
```

---

## Testing the Orchestrator

### Run Test Suite

```bash
# Test 1: Fetch and filter logic
export GITHUB_TOKEN=$(gh auth token)
python3 tools/test_orchestrator.py

# Test 2: Label updates
python3 tools/test_label_updates.py

# Test 3: End-to-end workflow (with mock worker)
python3 tools/test_orchestrator_e2e.py
```

### Expected Test Results
All tests should pass with ✅ verdicts:
- Fetch test: Validates issue filtering and priority ordering
- Label test: Confirms GitHub API label updates work
- E2E test: Simulates complete workflow with mock worker

---

## Configuration

### Environment Variables

```bash
# Required
export GITHUB_TOKEN="ghp_your_token_here"

# Optional (modify in orchestrator.py)
WORKER_TIMEOUT=3600        # Max time per issue (1 hour)
IDLE_SLEEP_SECONDS=300     # Sleep between batches (5 minutes)
```

### Repository Configuration

Edit `tools/orchestrator.py` to change repository:

```python
REPO_OWNER = "bennetwi92"
REPO_NAME = "tradepilot"
```

---

## Troubleshooting

### "GITHUB_TOKEN environment variable not set"
**Solution:** Export token from gh CLI:
```bash
export GITHUB_TOKEN=$(gh auth token)
```

### "requests library not found"
**Solution:** Install requests:
```bash
pip install requests
```

### "Claude Code CLI not found"
**Solution:** Install Claude Code:
```bash
# Follow instructions at https://claude.com/code
```

### Issues not being processed
**Check:**
1. Issues have `priority:high` or `priority:medium` label
2. Issues don't have `status:blocked`, `status:in-progress`, or `status:waiting-for-review` labels
3. Issues are open (not closed)
4. GitHub API is accessible

**Debug:**
```bash
# List issues with labels
gh issue list --json number,title,labels,state

# Check what orchestrator sees
python3 tools/test_orchestrator.py
```

---

## Integration with TradePilot Workflow

### Creating Orchestrator-Ready Issues

**Required:**
- Title: `{Type}: {Component} - {Specific action}`
- Labels: `priority:high` or `priority:medium`
- Body: Context, requirements, acceptance criteria

**Example:**
```bash
gh issue create \
  --title "Fix: RiskCalculator - Stop loss calculation includes commission twice" \
  --body "## Context
Stop loss calculation incorrectly includes IBKR commission twice...

## Acceptance Criteria
- [ ] Commission only applied once
- [ ] Tests verify correct calculation
- [ ] UI displays correct stop loss price" \
  --label "priority:high,pattern:fix-bug,component:core"
```

See `.claude/GITHUB_ISSUES.md` for complete issue creation standards.

---

## Monitoring & Logs

### Real-Time Monitoring

The orchestrator outputs to stdout. Redirect to file for logging:

```bash
python tools/orchestrator.py 2>&1 | tee orchestrator.log
```

### Key Log Events

```
[FETCH] - Issue fetching and filtering
[START] - Worker starting on issue
[WORKER] - Worker progress updates
[SUCCESS] - Issue completed successfully
[FAILED] - Issue processing failed
[TIMEOUT] - Worker exceeded timeout
[IDLE] - No issues to process, sleeping
```

---

## Production Deployment

### Running as Background Service

```bash
# Using nohup
nohup python tools/orchestrator.py > orchestrator.log 2>&1 &

# Or using screen
screen -dmS orchestrator python tools/orchestrator.py

# Or using systemd (create service file)
sudo systemctl start orchestrator
```

### Recommended Setup

1. **Dedicated Server/VM** - Don't run on development machine
2. **Monitoring** - Set up alerts for failures
3. **Log Rotation** - Prevent disk space issues
4. **Rate Limiting** - Respect GitHub API limits (5000 requests/hour)
5. **Secrets Management** - Store `GITHUB_TOKEN` securely

---

## Cleanup

### Remove Test Issues

```bash
bash tools/cleanup_test_issues.sh
```

Or manually:
```bash
gh issue close 368 369 370 371
```

### Remove Test Scripts (Optional)

```bash
rm tools/test_orchestrator.py
rm tools/test_label_updates.py
rm tools/test_orchestrator_e2e.py
rm tools/cleanup_test_issues.sh
```

---

## Support

**Documentation:**
- `.claude/GITHUB_ISSUES.md` - Issue creation standards
- `CLAUDE.md` - Project conventions
- `project/STATUS.md` - Current work status

**Debugging:**
- Run test scripts to validate configuration
- Check GitHub API rate limits: `gh api rate_limit`
- Verify Claude Code CLI: `claude --version`

---

**Version:** Lightweight POC (feature/#003-github-issue-orchestrator)
**Last Updated:** 2025-10-22
