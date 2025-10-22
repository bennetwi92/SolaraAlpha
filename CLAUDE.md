# SolaraAlpha - Claude Code Guidance

This file provides guidance for Claude Code agents working with the SolaraAlpha orchestrator system.

## Project Overview

SolaraAlpha is an AI-driven GitHub issue orchestrator that automatically processes issues using Claude Code workers. It manages the complete lifecycle from issue selection to implementation and PR creation.

## Architecture

### Single-Process Design

```
Orchestrator Loop
    ↓
Fetch Issues (GitHub API)
    ↓
Filter & Prioritize
    ↓
Spawn Claude Code Worker (subprocess)
    ↓
Update GitHub Labels
    ↓
Dynamic Refresh → Loop
```

### Key Components

1. **Orchestrator** (`src/orchestrator/orchestrator.py`)
   - Main event loop
   - Issue fetching and filtering
   - Worker process management
   - State tracking via GitHub labels

2. **Worker** (Claude Code subprocess)
   - Receives issue context as prompt
   - Implements solution autonomously
   - Creates branch, commits, and PR
   - Reports success/failure back via exit code

3. **State Management** (GitHub labels)
   - `priority:high|medium|low` - Selection priority
   - `status:in-progress` - Work in progress
   - `status:blocked` - Blocked on error
   - `status:waiting-review` - PR submitted

## Core Principles

1. **Correctness Over Speed** - Validate before processing
2. **Single Status Label** - Enforce one status per issue
3. **Dynamic Refresh** - Re-fetch after each task
4. **Fail Safe** - Always update GitHub state on error
5. **Simplicity** - No complex state machines

## Essential Commands

### Running the Orchestrator

```bash
# Set GitHub token
export GITHUB_TOKEN=$(gh auth token)

# Run orchestrator
python src/orchestrator/orchestrator.py

# Stop gracefully (Ctrl+C)
```

### Testing

```bash
# Unit tests
python tests/test_orchestrator.py

# End-to-end tests
python tests/test_orchestrator_e2e.py

# Label tests
python tests/test_label_updates.py
```

### Setup

```bash
# Create GitHub labels
bash scripts/setup_labels.sh

# Clean up test issues
bash scripts/cleanup_test_issues.sh
```

## Code Style

- **PEP 8** compliance
- **Type hints** for all functions
- **Docstrings** for public functions
- **Descriptive variable names**
- **Max line length**: 100 characters

### Example

```python
def fetch_prioritized_issues() -> List[Dict]:
    """
    Fetch open issues from GitHub with priority labels.

    Returns:
        List of issue dictionaries sorted by priority then creation date.
    """
    # Implementation
```

## Development Workflow

### Adding Features

1. **Create issue** with proper labels (`priority:medium`, `feature`)
2. **Implement** following existing patterns
3. **Test** with test scripts
4. **Document** in relevant `.md` files
5. **Commit** with descriptive message

### Testing Changes

```bash
# 1. Create test issues on GitHub
gh issue create --title "Test: Feature X" --label "priority:high"

# 2. Run orchestrator in dry-run mode (future feature)
# python src/orchestrator/orchestrator.py --dry-run

# 3. Validate label transitions
python tests/test_label_updates.py
```

## Issue Standards

See `.claude/GITHUB_ISSUES.md` for complete issue creation standards.

### Quick Reference

**Title Format**: `{Type}: {Component} - {Specific action}`

**Required Labels**:
- One priority: `priority:high|medium|low`
- One type: `bug`, `feature`, `enhancement`, `refactor`, `docs`

**Required Sections**:
- Context (why needed)
- Acceptance Criteria (testable conditions)

**Orchestrator-Ready Checklist**:
- [ ] Has `priority:high` or `priority:medium`
- [ ] NO blocking status labels
- [ ] Context ≥ 2 sentences
- [ ] Acceptance criteria ≥ 1 condition

## Configuration

### Orchestrator Settings

Edit `src/orchestrator/orchestrator.py`:

```python
# Repository configuration
REPO_OWNER = "bennetwi92"          # GitHub username
REPO_NAME = "tradepilot"           # Target repository

# Worker configuration
WORKER_TIMEOUT = 3600              # Max seconds per task (1 hour)
IDLE_SLEEP_SECONDS = 300           # Sleep when no issues (5 minutes)
```

### Environment Variables

```bash
# Required
export GITHUB_TOKEN="ghp_..."      # GitHub Personal Access Token

# Optional (future)
export CLAUDE_API_KEY="..."        # Claude API key
export LOG_LEVEL="INFO"            # Logging level
```

## Data Flow

### Issue Processing Flow

```
1. Fetch open issues from GitHub API
   ↓
2. Filter: priority:high OR priority:medium
   ↓
3. Filter: NO status:* labels
   ↓
4. Sort: High priority first, then FIFO
   ↓
5. Select first issue
   ↓
6. Add status:in-progress label
   ↓
7. Spawn Claude Code worker subprocess
   ↓
8. Worker implements solution
   ↓
9. On success: Remove status label
   On failure: Replace with status:blocked
   ↓
10. Dynamic refresh (go to step 1)
```

### Label State Machine

```
Initial State (no status label)
    ↓
[Orchestrator selects issue]
    ↓
status:in-progress
    ↓
    ├─→ [Success] → (remove status label) → Ready for next issue
    └─→ [Failure] → status:blocked → Manual intervention needed
```

## Key Functions

### `fetch_prioritized_issues() -> List[Dict]`

Fetches and filters GitHub issues.

**Returns**: Sorted list of issues (high priority first, FIFO within tier)

### `update_issue_label(issue_number: int, add_labels: List[str], remove_labels: List[str]) -> bool`

Updates GitHub issue labels.

**Args**:
- `issue_number`: GitHub issue number
- `add_labels`: Labels to add
- `remove_labels`: Labels to remove

**Returns**: `True` on success, `False` on failure

### `spawn_worker(issue: Dict) -> int`

Spawns Claude Code worker for issue.

**Args**:
- `issue`: GitHub issue dictionary

**Returns**: Worker exit code (0 = success, non-zero = failure)

## Error Handling

### Common Errors

| Error | Cause | Solution |
|-------|-------|----------|
| `GITHUB_TOKEN not set` | Missing env var | Export token |
| `API rate limit` | Too many requests | Wait or increase limit |
| `Worker timeout` | Task took too long | Increase `WORKER_TIMEOUT` |
| `Label update failed` | GitHub API error | Check token permissions |

### Debugging

```bash
# Enable verbose logging (future)
export LOG_LEVEL=DEBUG
python src/orchestrator/orchestrator.py

# Check GitHub API rate limit
curl -H "Authorization: token $GITHUB_TOKEN" \
  https://api.github.com/rate_limit
```

## Testing Strategy

### Unit Tests (`tests/test_orchestrator.py`)

- Issue fetching logic
- Filtering by labels
- Sorting by priority and date
- Label update functions

### E2E Tests (`tests/test_orchestrator_e2e.py`)

- Complete workflow simulation
- Label state transitions
- Worker spawning (mocked)
- Error handling

### Integration Tests (Manual)

1. Create test issues on GitHub
2. Run orchestrator
3. Verify label transitions
4. Check PR creation
5. Clean up test issues

## Documentation Structure

```
docs/
├── usage.md              # User guide
├── test-plan.md          # Testing workflow
├── refresh-behavior.md   # Dynamic refresh implementation
├── single-status-label.md # Label management
└── state-flow-summary.md # State machine diagrams
```

## Common Patterns

### Adding a New Label Type

1. Update `fetch_prioritized_issues()` filter logic
2. Add label to `setup_labels.sh`
3. Document in `.claude/GITHUB_ISSUES.md`
4. Update tests

### Changing Priority Logic

1. Modify sorting in `fetch_prioritized_issues()`
2. Update documentation
3. Add tests for new behavior

### Adding Worker Configuration

1. Add to orchestrator configuration section
2. Pass to worker via environment or args
3. Document in CLAUDE.md and usage.md

## Related Documentation

- **[GitHub Issues Guide](.claude/GITHUB_ISSUES.md)** - Issue creation standards
- **[Usage Guide](docs/usage.md)** - Detailed usage instructions
- **[Test Plan](docs/test-plan.md)** - Testing workflow
- **[README.md](README.md)** - Project overview

## Integration with Other Systems

SolaraAlpha can orchestrate issues for any GitHub repository. Simply configure:

```python
REPO_OWNER = "your-username"
REPO_NAME = "your-repository"
```

No code dependencies required—works via GitHub API and Claude Code CLI.

## Best Practices

1. **Label Hygiene**: Always enforce single status label per issue
2. **Dynamic Refresh**: Re-fetch after each completion for real-time priorities
3. **Graceful Shutdown**: Handle Ctrl+C to avoid orphaned workers
4. **Error Comments**: Always add comment when blocking an issue
5. **Validation**: Check issue structure before processing

## Future Enhancements

- Configuration file support (YAML/JSON)
- Parallel worker execution
- Dry-run mode for testing
- Web dashboard for monitoring
- Slack/Discord notifications
- Custom label taxonomies
- Advanced filtering (by component, author, etc.)

---

**Version**: 1.0.0
**Last Updated**: 2025-10-22
