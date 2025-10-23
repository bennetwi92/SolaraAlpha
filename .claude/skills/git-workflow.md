# Git Workflow

## When
Processing issues, creating branches, committing code, creating PRs

## Branch

```bash
# Format: {type}/#{number}-{description}
git checkout main && git pull
git checkout -b feature/#45-risk-limits
```

Types: `feature|fix|refactor|docs|enhancement`

## Commit

```bash
git add .
git commit -m "$(cat <<'EOF'
fix: RiskCalculator - Stop-loss HWM updates

Fixes #67

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>
EOF
)"
```

Format: `{type}: {component} - {description}`
Always include `Fixes #NNN`

## Push

```bash
git push -u origin feature/#45-risk-limits
```

## PR

```bash
gh pr create --title "Fix: RiskCalculator - HWM updates" --body "$(cat <<'EOF'
## Summary
- Track HWM from real-time data
- Fix stale HWM without fills

## Test Plan
- [ ] Unit tests pass
- [ ] Manual verification

## Acceptance Criteria
- [x] HWM updates real-time
- [x] Tests added

Fixes #67

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

## AI Constraints

- NO `git rebase -i` (interactive)
- NO `git add -p` (interactive)
- NO `git commit --amend` (unless safe)
- Linear history only
- Branch from main, PR to main

## Gotchas

- Use HEREDOC for multi-line commits (prevents escaping issues)
- Always include `#` in branch name (GitHub linking)
- PR body must have acceptance criteria from issue
- One logical change per commit
