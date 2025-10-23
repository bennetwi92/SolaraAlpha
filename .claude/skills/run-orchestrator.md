# Run Orchestrator

## When
Start autonomous issue processing, monitor execution, troubleshoot failures

## Pre-Flight

```bash
# Set token
export GITHUB_TOKEN=$(gh auth token)

# Verify Claude CLI
which claude

# Check labels exist
gh label list | grep "priority:high"
# If missing: bash scripts/setup_labels.sh

# Check ready issues
gh issue list --label "priority:high" --label "priority:medium" --state open
```

## Start

```bash
cd /Users/williambennett/Github/SolaraAlpha
python src/orchestrator/orchestrator.py
```

**Flow**: Fetch priority:high|medium + NO status → Sort → Spawn worker → Update labels → Refresh

## Stop

`Ctrl+C` (current worker completes, then stops)

## Monitor

**Normal**:
```
[FETCH] Found 3 issues (2 high, 1 medium)
[START] Issue #42: Feature...
[SUCCESS] Issue #42 completed
```

**Errors**:
```
[ERROR] GITHUB_TOKEN not set
[TIMEOUT] Issue #42 exceeded timeout
[FAILED] Issue #42 exit code 1
```

## Troubleshoot

**No issues processed**:
```bash
# Check labels
gh issue list --label "priority:high" --state open
# If missing NO status:* labels, add them

# Add priority if needed
gh issue edit <number> --add-label "priority:high"
```

**Worker timeout**:
- Increase `WORKER_TIMEOUT` in `src/orchestrator/orchestrator.py`
- Or decompose issue into smaller tasks

**API rate limit**:
```bash
# Check limit
gh api rate_limit
# Wait or increase IDLE_SLEEP_SECONDS
```

**Worker fails**:
- Review issue for ambiguity
- Check worker output
- Mark blocked: `gh issue edit <number> --add-label "status:blocked"`

## Gotchas

- Orchestrator ONLY selects issues with NO status:\* labels
- NEVER run multiple orchestrators on same repo
- Issues need Context + Acceptance Criteria
- Single status label enforced automatically
- Sequential processing (one at a time)
