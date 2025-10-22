# GitHub Issue Orchestrator - Claude Code Guidance

This directory contains documentation for the TradePilot GitHub Issue Orchestrator.

## Overview

The orchestrator automatically processes GitHub issues by:
1. Fetching issues with `status:ready` and `priority:high/medium`
2. Spawning Claude Code workers to implement solutions
3. Managing issue state transitions via GitHub labels
4. Creating PRs and updating issue status

## Quick Start

```bash
export GITHUB_TOKEN=$(gh auth token)
python tools/orchestrator.py
```

Press `Ctrl+C` to stop.

## Documentation

### User Guides
- **[Usage Guide](usage.md)** - How to run and configure the orchestrator
- **[Test Plan](test-plan.md)** - Complete testing workflow with sample issues

### Technical Documentation
- **[Refresh Behavior](refresh-behavior.md)** - Dynamic issue refresh after each task
- **[Single Status Label](single-status-label.md)** - How status label enforcement works
- **[State Flow Summary](state-flow-summary.md)** - Visual state machine diagrams

### Policy Documents
- **[Mandatory Status Labels](mandatory-status-labels.md)** - Status label policy and enforcement
- **[Test Report](test-report.md)** - Test results and validation

### Related Documentation
- **[Issue Standards](../../gh_issues_standards.md)** - Complete issue lifecycle and state flow
- **[GitHub Issues Guide](../../.claude/GITHUB_ISSUES.md)** - Issue creation standards

## Core Concepts

### Status Label Flow
```
status:ready → status:awaiting-estimation → status:in-progress → status:in-review
```

### Priority Processing
- High priority issues processed first
- Medium priority issues processed second  
- Low priority issues skipped
- Issues refreshed after each completion

### Single Status Label Rule
Each issue has exactly ONE status label at all times. Old status labels are removed before adding new ones.

## Key Features

- ✅ Dynamic issue refresh after each task
- ✅ Real-time priority updates
- ✅ Single status label enforcement
- ✅ Automatic label state transitions
- ✅ Continuous processing (no artificial delays)

## Files

- **`usage.md`** - User guide and configuration
- **`test-plan.md`** - Testing workflow
- **`refresh-behavior.md`** - Dynamic refresh implementation
- **`single-status-label.md`** - Label management details
- **`state-flow-summary.md`** - Complete state machine
- **`mandatory-status-labels.md`** - Status label policy
- **`test-report.md`** - Test validation results

---

**Orchestrator Version:** 2.1 (Dynamic Refresh + Single Status)
**Last Updated:** 2025-10-22
