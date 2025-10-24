#!/usr/bin/env python3
"""
Context Manager - Efficient context perpetuation for orchestrator agents

Maintains a lightweight context cache that agents read at task start to understand
recent work, system state, and architectural decisions without loading full codebase.

Token budget: ~350 tokens (vs thousands for full context)
- Context cache: ~200 tokens
- Relevant CLAUDE.md files: ~50 tokens
- Issue body: ~100 tokens
"""

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class ContextManager:
    """Manages context cache for orchestrator task continuity."""

    def __init__(self, repo_root: str = "/Users/williambennett/Github/SolaraAlpha"):
        """
        Initialize context manager.

        Args:
            repo_root: Absolute path to repository root
        """
        self.repo_root = Path(repo_root)
        self.cache_path = self.repo_root / "src/orchestrator/.context_cache.json"

    def load_cache(self) -> Dict:
        """
        Load existing context cache.

        Returns:
            Context cache dictionary (empty if cache doesn't exist)
        """
        if self.cache_path.exists():
            try:
                with open(self.cache_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                # Corrupted cache - return empty
                return self._empty_cache()
        return self._empty_cache()

    def _empty_cache(self) -> Dict:
        """Return empty context cache structure."""
        return {
            "last_updated": None,
            "recent_completions": [],
            "active_decisions": {},
            "system_state": {
                "last_successful_test": None,
                "known_issues": [],
            },
        }

    def update_cache(
        self,
        issue_num: int,
        issue_title: str,
        outcome: str,
        key_learning: Optional[str] = None,
        blocker: Optional[str] = None,
        decisions: Optional[Dict] = None,
    ) -> bool:
        """
        Update context cache after task completion/failure.

        Args:
            issue_num: GitHub issue number
            issue_title: Issue title
            outcome: "success" or "blocked"
            key_learning: Key learning from successful completion (optional)
            blocker: Reason for blockage if outcome is "blocked" (optional)
            decisions: New architectural/system decisions to record (optional)

        Returns:
            True if cache updated successfully
        """
        try:
            cache = self.load_cache()

            # Update timestamp
            cache["last_updated"] = datetime.now().isoformat()

            # Add completion entry
            completion_entry = {
                "issue": f"#{issue_num}",
                "title": issue_title,
                "outcome": outcome,
                "timestamp": datetime.now().isoformat(),
            }
            if key_learning:
                completion_entry["key_learning"] = key_learning
            if blocker:
                completion_entry["blocker"] = blocker

            # Prepend new completion and keep last 5
            cache["recent_completions"] = (
                [completion_entry] + cache["recent_completions"]
            )[:5]

            # Update active decisions
            if decisions:
                cache["active_decisions"].update(decisions)

            # Save cache
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_path, "w") as f:
                json.dump(cache, f, indent=2)

            return True
        except Exception as e:
            print(f"[ERROR] Failed to update context cache: {e}")
            return False

    def get_context_summary(self) -> str:
        """
        Generate concise context summary for agents.

        Returns:
            Formatted context string (~200 tokens)
        """
        cache = self.load_cache()

        if not cache["last_updated"]:
            return "## Context\n\nNo previous task context available (first run)."

        summary = "## Recent Context\n\n"

        # Recent completions
        if cache["recent_completions"]:
            summary += "**Last 5 Tasks:**\n"
            for comp in cache["recent_completions"]:
                status_icon = "✅" if comp["outcome"] == "success" else "❌"
                summary += f"- {status_icon} {comp['issue']}: {comp['title']}\n"
                if "key_learning" in comp:
                    summary += f"  - Learning: {comp['key_learning']}\n"
                if "blocker" in comp:
                    summary += f"  - Blocker: {comp['blocker']}\n"
            summary += "\n"

        # Active decisions
        if cache["active_decisions"]:
            summary += "**System Decisions:**\n"
            for key, value in cache["active_decisions"].items():
                summary += f"- {key}: {value}\n"
            summary += "\n"

        # System state
        if cache["system_state"].get("known_issues"):
            summary += "**Known Issues:**\n"
            for issue in cache["system_state"]["known_issues"]:
                summary += f"- {issue}\n"
            summary += "\n"

        return summary.strip()

    def parse_git_log(self, count: int = 5) -> List[Dict[str, str]]:
        """
        Parse recent git commits for semantic context tags.

        Extracts [context:key=value] tags from commit messages.

        Args:
            count: Number of recent commits to parse

        Returns:
            List of commit dictionaries with extracted context
        """
        try:
            result = subprocess.run(
                ["git", "log", f"-n{count}", "--format=%H|%s"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return []

            commits = []
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                parts = line.split("|", 1)
                if len(parts) != 2:
                    continue

                sha, message = parts
                commit_data = {"sha": sha[:7], "message": message, "context": {}}

                # Extract [context:...] tags
                if "[context:" in message:
                    start = message.find("[context:")
                    end = message.find("]", start)
                    if end != -1:
                        context_str = message[start + 9 : end]  # Skip "[context:"
                        # Parse key=value pairs
                        for pair in context_str.split(","):
                            if "=" in pair:
                                key, value = pair.split("=", 1)
                                commit_data["context"][key.strip()] = value.strip()

                commits.append(commit_data)

            return commits

        except (subprocess.TimeoutExpired, Exception) as e:
            print(f"[WARNING] Failed to parse git log: {e}")
            return []

    def get_git_context_summary(self) -> str:
        """
        Generate git context summary from recent commits.

        Returns:
            Formatted git context string (~100 tokens)
        """
        commits = self.parse_git_log(count=5)

        if not commits:
            return ""

        summary = "**Recent Commits:**\n"
        for commit in commits:
            summary += f"- {commit['sha']}: {commit['message']}\n"
            if commit["context"]:
                for key, value in commit["context"].items():
                    summary += f"  - {key}: {value}\n"

        return summary

    def generate_full_context(self, issue_body: str) -> str:
        """
        Generate full context prompt for agent task start.

        Combines:
        - Context cache summary (~200 tokens)
        - Git context (~100 tokens)
        - Issue body (~100 tokens)

        Args:
            issue_body: GitHub issue body text

        Returns:
            Full context prompt for agent
        """
        context_parts = []

        # Context cache
        cache_summary = self.get_context_summary()
        if cache_summary:
            context_parts.append(cache_summary)

        # Git context
        git_summary = self.get_git_context_summary()
        if git_summary:
            context_parts.append(git_summary)

        # Separator before task
        context_parts.append("---")
        context_parts.append("## Current Task\n")
        context_parts.append(issue_body)

        return "\n\n".join(context_parts)

    def generate_completion_comment(
        self, outcome: str, key_learning: Optional[str] = None
    ) -> str:
        """
        Generate standardized completion comment for GitHub issues.

        Args:
            outcome: "success" or "blocked"
            key_learning: Key learning from task (optional)

        Returns:
            Formatted completion comment
        """
        if outcome == "success":
            comment = "## Task Completion Context\n\n"
            comment += "**Status:** ✅ Completed successfully\n\n"
            if key_learning:
                comment += f"**Key Learning:** {key_learning}\n\n"
            comment += "_This context will be available to future tasks via the orchestrator context cache._"
        else:
            comment = "## Task Context\n\n"
            comment += "**Status:** ❌ Blocked\n\n"
            comment += "_See execution log for blocker details. This context is preserved in the orchestrator cache._"

        return comment
