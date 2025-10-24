# SolaraAlpha

**AI-driven GitHub issue orchestrator and automation system**

SolaraAlpha is an intelligent orchestration system that automatically processes GitHub issues using AI agents powered by Claude Code. It manages the complete lifecycle of issue resolution—from selection and prioritization to implementation and pull request creation.

## Features

- **Intelligent Issue Selection**: Automatically fetches and prioritizes GitHub issues based on labels
- **Dynamic Workflow**: Refreshes issue queue after each task completion for real-time priority updates
- **State Management**: Single status label enforcement with automatic state transitions
- **AI-Powered Implementation**: Spawns Claude Code workers to autonomously implement solutions
- **Continuous Processing**: Runs continuously with configurable idle periods
- **Safety First**: Comprehensive validation and error handling

## Quick Start

### Prerequisites

- Python 3.9+
- GitHub Personal Access Token with repo permissions
- Claude Code CLI installed

### Installation

1. Clone the repository:
```bash
git clone https://github.com/bennetwi92/SolaraAlpha.git
cd SolaraAlpha
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up GitHub token:
```bash
export GITHUB_TOKEN=$(gh auth token)
```

### Usage

Run the orchestrator:
```bash
python src/orchestrator/orchestrator.py --enable-ceo-poke
```

Press `Ctrl+C` to stop gracefully.

## Configuration

Edit `src/orchestrator/orchestrator.py` to configure:

- `REPO_OWNER`: GitHub repository owner (default: `bennetwi92`)
- `REPO_NAME`: Repository name (default: `tradepilot`)
- `WORKER_TIMEOUT`: Maximum time for each task (default: 3600 seconds)
- `IDLE_SLEEP_SECONDS`: Sleep duration when no issues available (default: 300 seconds)

## How It Works

### Issue Selection Algorithm

1. **Fetch** open issues with `priority:high` or `priority:medium` labels
2. **Filter** out issues with blocking status labels (`status:blocked`, `status:in-progress`, `status:waiting-review`)
3. **Sort** by priority tier (high first), then FIFO by creation date
4. **Process** sequentially, one issue at a time

### Status Label Flow

```
[No status] → status:in-progress → [No status] (success)
                                 ↓
                          status:blocked (failure)
```

### State Transitions

- **Start Work**: Adds `status:in-progress` label
- **Success**: Removes all status labels
- **Failure**: Replaces with `status:blocked` and adds error comment
- **Dynamic Refresh**: Re-fetches issues after each completion

## Label Taxonomy

| Category | Valid Labels | Orchestrator Rules |
|----------|-------------|-------------------|
| **Priority** (required) | `priority:high`, `priority:medium`, `priority:low` | Processes high→medium FIFO. Skips `priority:low` |
| **Status** (blocking) | `status:blocked`, `status:in-progress`, `status:waiting-review` | Skips if any present |
| **Type** (optional) | `bug`, `feature`, `enhancement`, `refactor`, `docs` | For context only |

## Project Structure

```
SolaraAlpha/
├── src/orchestrator/       # Main orchestrator code
│   └── orchestrator.py    # Core orchestration logic
├── tests/                 # Test suite
│   ├── test_orchestrator.py
│   ├── test_orchestrator_e2e.py
│   └── test_label_updates.py
├── scripts/               # Utility scripts
│   ├── setup_labels.sh   # GitHub label setup
│   └── cleanup_test_issues.sh
├── docs/                  # Documentation
│   ├── usage.md          # Usage guide
│   ├── test-plan.md      # Testing workflow
│   └── *.md              # Additional docs
└── .claude/              # AI agent definitions
    ├── GITHUB_ISSUES.md  # Issue creation standards
    └── agents/           # Agent specifications
```

## Documentation

- **[Usage Guide](docs/usage.md)** - Detailed usage and configuration
- **[Test Plan](docs/test-plan.md)** - Testing workflow and validation
- **[CLAUDE.md](CLAUDE.md)** - Development guide for AI agents
- **[GitHub Issues Guide](.claude/GITHUB_ISSUES.md)** - Issue creation standards

## Testing

Run unit tests:
```bash
export GITHUB_TOKEN=$(gh auth token)
python tests/test_orchestrator.py
```

Run end-to-end tests:
```bash
python tests/test_orchestrator_e2e.py
```

Test label updates:
```bash
python tests/test_label_updates.py
```

## Setup Scripts

### Create GitHub Labels

```bash
export GITHUB_TOKEN=$(gh auth token)
bash scripts/setup_labels.sh
```

### Clean Up Test Issues

```bash
bash scripts/cleanup_test_issues.sh
```

## Requirements

- `requests` - GitHub API communication
- `subprocess` - Worker process management
- Python 3.9+ standard library

## Architecture

SolaraAlpha uses a lightweight, single-process architecture:

1. **Orchestrator Loop**: Continuously fetches and processes issues
2. **Worker Spawning**: Launches Claude Code as subprocess for each issue
3. **State Tracking**: Updates GitHub labels to reflect progress
4. **Dynamic Refresh**: Re-evaluates priority after each task

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## License

Private repository - All rights reserved

## Acknowledgments

- Built for autonomous issue processing with Claude Code
- Inspired by AI-driven development workflows
- Designed for continuous integration and delivery

---

**Version**: 1.0.0
**Last Updated**: 2025-10-22
