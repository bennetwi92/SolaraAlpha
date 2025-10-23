#!/usr/bin/env python3
"""
GitHub Issue Orchestrator - Lightweight POC

Automatically processes GitHub issues by:
1. Fetching open issues with priority:high or priority:medium labels
2. Sorting FIFO (oldest first) within priority tier
3. Spawning Claude Code workers to process issues
4. Updating GitHub labels to track state
5. Poking CEO for strategic assessment when queue is empty

Usage:
    export GITHUB_TOKEN="ghp_..."
    python tools/orchestrator.py
"""
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Dict, List
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("[ERROR] requests library not found. Install with: pip install requests")
    sys.exit(1)

# Configuration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_OWNER = "bennetwi92"
REPO_NAME = "SolaraAlpha"
BASE_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# Worker configuration
WORKER_TIMEOUT = 1200  # 20 minutes
IDLE_SLEEP_SECONDS = 300  # 5 minutes


def fetch_prioritized_issues() -> List[Dict]:
    """
    Fetch open issues from GitHub with priority:high or priority:medium labels.
    Excludes issues with any status:* labels (already being processed).

    Returns:
        List of issue dictionaries sorted by priority (high first) then creation date (FIFO)
    """
    if not GITHUB_TOKEN:
        print("[ERROR] GITHUB_TOKEN environment variable not set")
        sys.exit(1)

    print(f"[FETCH] Fetching issues from {REPO_OWNER}/{REPO_NAME}...")

    try:
        # Fetch all open issues (GitHub API doesn't support OR in label filters)
        params = {
            "state": "open",
            "per_page": 100,
            "sort": "created",
            "direction": "asc",
        }

        response = requests.get(f"{BASE_URL}/issues", headers=HEADERS, params=params)
        response.raise_for_status()
        all_issues = response.json()

        # Filter for priority labels
        priority_labels = {"priority:high", "priority:medium"}

        filtered_issues = []
        for issue in all_issues:
            issue_labels = {label["name"] for label in issue.get("labels", [])}

            # Check if has priority label
            has_priority = bool(issue_labels & priority_labels)
            # Skip if ANY status:* label exists (already being processed)
            has_status = any(label.startswith("status:") for label in issue_labels)
            # Skip pull requests (they have pull_request field)
            is_pr = "pull_request" in issue

            # Include ONLY if: has priority AND no status labels AND not a PR
            if has_priority and not has_status and not is_pr:
                filtered_issues.append(issue)

        # Sort: priority:high first, then by created_at (already sorted by API)
        high_priority = [
            i
            for i in filtered_issues
            if "priority:high" in {l["name"] for l in i["labels"]}
        ]
        medium_priority = [
            i
            for i in filtered_issues
            if "priority:medium" in {l["name"] for l in i["labels"]}
        ]

        sorted_issues = high_priority + medium_priority

        print(
            f"[FETCH] Found {len(sorted_issues)} issues ({len(high_priority)} high, {len(medium_priority)} medium)"
        )
        return sorted_issues

    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to fetch issues: {e}")
        return []


def remove_status_labels(issue_num: int) -> bool:
    """
    Remove all status: labels from a GitHub issue.

    Args:
        issue_num: Issue number

    Returns:
        True if successful, False otherwise
    """
    try:
        # Get current labels
        url = f"{BASE_URL}/issues/{issue_num}"
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        issue = response.json()

        # Find and remove all status: labels
        status_labels = [
            label["name"]
            for label in issue.get("labels", [])
            if label["name"].startswith("status:")
        ]

        for label in status_labels:
            # URL-encode label name (colon in "status:in-progress" needs encoding)
            encoded_label = quote(label, safe='')
            delete_url = f"{BASE_URL}/issues/{issue_num}/labels/{encoded_label}"
            response = requests.delete(delete_url, headers=HEADERS)
            response.raise_for_status()

        return True
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to remove status labels from issue #{issue_num}: {e}")
        return False


def update_issue_status(issue_num: int, new_status: str) -> bool:
    """
    Update issue status label, removing old status labels first.
    Ensures only ONE status label exists at a time.

    Args:
        issue_num: Issue number
        new_status: New status label to add (e.g., "status:in-progress")

    Returns:
        True if successful, False otherwise
    """
    try:
        # Step 1: Remove all existing status: labels
        remove_status_labels(issue_num)

        # Step 2: Add new status label
        url = f"{BASE_URL}/issues/{issue_num}/labels"
        response = requests.post(url, headers=HEADERS, json={"labels": [new_status]})
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(
            f"[ERROR] Failed to update status label '{new_status}' on issue #{issue_num}: {e}"
        )
        return False


def fetch_recent_commits(count: int = 10) -> List[str]:
    """
    Fetch recent commits from the repository.

    Args:
        count: Number of recent commits to fetch

    Returns:
        List of commit strings in format "hash: message"
    """
    try:
        url = f"{BASE_URL}/commits"
        params = {"per_page": count}
        response = requests.get(url, headers=HEADERS, params=params)
        response.raise_for_status()
        commits = response.json()

        commit_list = []
        for commit in commits:
            sha = commit["sha"][:7]
            message = commit["commit"]["message"].split("\n")[0]  # First line only
            commit_list.append(f"{sha}: {message}")

        return commit_list
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to fetch commits: {e}")
        return []


def fetch_all_open_issues() -> List[Dict]:
    """
    Fetch ALL open issues from GitHub (regardless of priority/status).

    Returns:
        List of all open issue dictionaries
    """
    try:
        params = {
            "state": "open",
            "per_page": 100,
            "sort": "created",
            "direction": "desc",
        }
        response = requests.get(f"{BASE_URL}/issues", headers=HEADERS, params=params)
        response.raise_for_status()
        all_issues = response.json()

        # Filter out pull requests
        issues = [i for i in all_issues if "pull_request" not in i]
        return issues
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to fetch all open issues: {e}")
        return []


def fetch_blocked_issues() -> List[Dict]:
    """
    Fetch issues with status:blocked label.

    Returns:
        List of blocked issue dictionaries
    """
    try:
        params = {
            "state": "open",
            "labels": "status:blocked",
            "per_page": 100,
        }
        response = requests.get(f"{BASE_URL}/issues", headers=HEADERS, params=params)
        response.raise_for_status()
        issues = response.json()

        # Filter out pull requests
        issues = [i for i in issues if "pull_request" not in i]
        return issues
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to fetch blocked issues: {e}")
        return []


def spawn_ceo_poke() -> bool:
    """
    Poke CEO to assess organizational state when queue is empty.

    CEO receives context:
    - Recent commits (what work was completed)
    - All open issues (current backlog)
    - Blocked issues (red flags)

    CEO decides whether to create new issues (via task-builder) or report no action needed.

    Returns:
        True if CEO completed successfully, False otherwise
    """
    print("\n[CEO POKE] Queue empty - spawning CEO assessment...")

    # Gather context
    recent_commits = fetch_recent_commits(count=10)
    open_issues = fetch_all_open_issues()
    blocked_issues = fetch_blocked_issues()

    # Format context for CEO
    commits_text = "\n".join(f"  - {c}" for c in recent_commits) if recent_commits else "  (No recent commits)"

    open_issues_text = ""
    if open_issues:
        for issue in open_issues[:20]:  # Limit to 20 to avoid prompt overflow
            labels = ", ".join(l["name"] for l in issue["labels"])
            open_issues_text += f"  - #{issue['number']}: {issue['title']} [{labels}]\n"
    else:
        open_issues_text = "  (No open issues)\n"

    blocked_text = ""
    if blocked_issues:
        for issue in blocked_issues:
            blocked_text += f"  - #{issue['number']}: {issue['title']}\n"
    else:
        blocked_text = "  (No blocked issues)\n"

    # Build CEO prompt
    prompt = f"""Review organizational state and assess if new work is needed.

Recent completions (last 10 commits):
{commits_text}

Current open issues:
{open_issues_text}

Blocked issues (require attention):
{blocked_text}

Evaluate:
- Are blocked issues preventing progress?
- Is critical infrastructure missing?
- Are we working on highest-value opportunities?
- Should low-priority work be escalated?

If valuable work identified: Use task-builder agent to create issues
If backlog is healthy: Report "MONITOR: No action required"

Only create issues if genuinely valuable. "No action" is a valid outcome.
"""

    try:
        print("[CEO] Starting CEO assessment...")
        start_time = time.time()

        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions", prompt],
            timeout=WORKER_TIMEOUT,
            capture_output=False,
            text=True,
        )

        elapsed = time.time() - start_time
        print(f"[CEO] Completed in {elapsed:.1f}s with exit code {result.returncode}")

        if result.returncode == 0:
            print("[CEO] Assessment completed successfully")
            return True
        else:
            print(f"[CEO] Assessment failed with exit code {result.returncode}")
            return False

    except subprocess.TimeoutExpired:
        print(f"[CEO] Assessment exceeded {WORKER_TIMEOUT}s timeout")
        return False
    except Exception as e:
        print(f"[CEO] Assessment failed with error: {e}")
        return False


def spawn_claude_worker(issue: Dict) -> bool:
    """
    Spawn Claude Code CLI to process an issue.

    Args:
        issue: GitHub issue dictionary

    Returns:
        True if worker completed successfully, False otherwise
    """
    issue_num = issue["number"]
    issue_title = issue["title"]
    issue_body = issue.get("body", "")

    print(f"\n[START] Issue #{issue_num}: {issue_title}")
    print(f"[START] URL: {issue['html_url']}")

    # Update status to in-progress (beginning work)
    print("[LABEL] Updating status → in-progress...")
    if not update_issue_status(issue_num, "status:in-progress"):
        print("[WARNING] Could not update status to in-progress, continuing anyway...")

    # Build prompt for Claude Code
    prompt = f"{issue_body}"

    try:
        # Spawn Claude Code worker
        print(f"[WORKER] Starting Claude Code for issue #{issue_num}...")
        start_time = time.time()

        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions", prompt],
            timeout=WORKER_TIMEOUT,
            capture_output=False,  # Let output go to console
            text=True,
        )

        elapsed = time.time() - start_time
        print(
            f"[WORKER] Completed in {elapsed:.1f}s with exit code {result.returncode}"
        )

        if result.returncode == 0:
            print(f"[SUCCESS] Issue #{issue_num} completed successfully")
            # Remove status label (issue complete, returns to pool)
            print("[LABEL] Removing status labels...")
            remove_status_labels(issue_num)
            return True
        else:
            print(
                f"[FAILED] Issue #{issue_num} failed with exit code {result.returncode}"
            )
            # Update status to blocked
            print("[LABEL] Updating status → blocked...")
            update_issue_status(issue_num, "status:blocked")
            return False

    except subprocess.TimeoutExpired:
        print(f"[TIMEOUT] Issue #{issue_num} exceeded {WORKER_TIMEOUT}s timeout")
        # Update status to blocked
        print("[LABEL] Updating status → blocked...")
        update_issue_status(issue_num, "status:blocked")
        return False
    except FileNotFoundError:
        print("[ERROR] Claude Code CLI not found. Please install Claude Code first.")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Issue #{issue_num} failed with error: {e}")
        # Update status to blocked
        print("[LABEL] Updating status → blocked...")
        update_issue_status(issue_num, "status:blocked")
        return False


def main_loop():
    """
    Main orchestration loop.

    Continuously fetches and processes issues until manually stopped (Ctrl+C).
    When queue is empty, pokes CEO once to assess organizational state.
    """
    print("=" * 80)
    print("GitHub Issue Orchestrator - Lightweight POC")
    print(f"Repository: {REPO_OWNER}/{REPO_NAME}")
    print(f"Worker timeout: {WORKER_TIMEOUT}s")
    print(f"Idle sleep: {IDLE_SLEEP_SECONDS}s")
    print("=" * 80)
    print("\nPress Ctrl+C to stop\n")

    if not GITHUB_TOKEN:
        print("[ERROR] GITHUB_TOKEN environment variable not set")
        print("Export your GitHub token: export GITHUB_TOKEN='ghp_...'")
        sys.exit(1)

    iteration = 0
    ceo_poked_this_cycle = False  # Track whether CEO has been poked during empty queue

    try:
        while True:
            iteration += 1
            print(f"\n{'=' * 80}")
            print(
                f"[ITERATION {iteration}] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            print(f"{'=' * 80}")

            # Process issues one at a time, refreshing after each completion
            total_success = 0
            total_failed = 0

            while True:
                # Fetch prioritized issues (refresh on each iteration)
                print("\n[REFRESH] Fetching latest issues...")
                issues = fetch_prioritized_issues()

                if not issues:
                    # Queue is empty
                    if not ceo_poked_this_cycle:
                        # First time queue is empty - poke CEO
                        print("[EMPTY QUEUE] No eligible issues found - poking CEO...")
                        spawn_ceo_poke()
                        ceo_poked_this_cycle = True
                        # Refresh immediately to check if CEO created new issues
                        continue
                    else:
                        # CEO already poked, enter idle monitoring
                        print(
                            f"[IDLE] No issues to process. Sleeping {IDLE_SLEEP_SECONDS}s..."
                        )
                        time.sleep(IDLE_SLEEP_SECONDS)
                        break  # Exit inner loop, will refresh in outer loop
                else:
                    # Queue has work - reset CEO poke flag
                    if ceo_poked_this_cycle:
                        print("[QUEUE ACTIVE] Work detected - resetting CEO poke flag")
                        ceo_poked_this_cycle = False

                    # Process only the FIRST issue (highest priority)
                    issue = issues[0]
                    print(f"\n[PROCESSING] Next issue: #{issue['number']}")
                    success = spawn_claude_worker(issue)

                    if success:
                        total_success += 1
                    else:
                        total_failed += 1

                    # After processing one issue, loop back to refresh and get next highest priority

            # Print iteration summary
            if total_success > 0 or total_failed > 0:
                print(f"\n{'=' * 80}")
                print(
                    f"[ITERATION COMPLETE] Processed {total_success + total_failed} issue(s)"
                )
                print(f"  ✅ Success: {total_success}")
                print(f"  ❌ Failed:  {total_failed}")
                print(f"{'=' * 80}")

            # No sleep between iterations - immediately fetch next batch

    except KeyboardInterrupt:
        print("\n\n[SHUTDOWN] Received Ctrl+C, shutting down gracefully...")
        print("[SHUTDOWN] Orchestrator stopped")
        sys.exit(0)


if __name__ == "__main__":
    main_loop()
