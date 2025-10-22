#!/usr/bin/env python3
"""
GitHub Issue Orchestrator - Lightweight POC

Automatically processes GitHub issues by:
1. Fetching open issues with priority:high or priority:medium labels
2. Sorting FIFO (oldest first) within priority tier
3. Spawning Claude Code workers to process issues
4. Updating GitHub labels to track state

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

try:
    import requests
except ImportError:
    print("[ERROR] requests library not found. Install with: pip install requests")
    sys.exit(1)

# Configuration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_OWNER = "bennetwi92"
REPO_NAME = "tradepilot"
BASE_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# Worker configuration
WORKER_TIMEOUT = 3600  # 1 hour
IDLE_SLEEP_SECONDS = 300  # 5 minutes


def fetch_prioritized_issues() -> List[Dict]:
    """
    Fetch open issues from GitHub with priority:high or priority:medium labels.
    Excludes issues with blocking status labels.

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
        priority_labels = {"priority:high", "priority:medium", "priority:low"}
        # Labels that indicate issue is already being processed or completed
        blocked_labels = {
            "status:backlog",  # Not scheduled yet
            "status:awaiting-estimation",  # Already selected, being analyzed
            "status:in-progress",  # Already being worked on
            "status:blocked",  # Blocked by dependency
            "status:in-review",  # PR in review
            "status:ready-to-merge",  # Approved, awaiting merge
            "status:in-develop",  # Already merged to develop
        }

        filtered_issues = []
        for issue in all_issues:
            issue_labels = {label["name"] for label in issue.get("labels", [])}

            # Check if has priority label
            has_priority = bool(issue_labels & priority_labels)
            # ONLY process issues with status:ready (explicitly scheduled)
            is_ready = "status:ready" in issue_labels
            # Skip pull requests (they have pull_request field)
            is_pr = "pull_request" in issue

            # Include ONLY if: has priority AND status:ready AND not a PR
            if has_priority and is_ready and not is_pr:
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
        low_priority = [
            i
            for i in filtered_issues
            if "priority:low" in {l["name"] for l in i["labels"]}
        ]

        sorted_issues = high_priority + medium_priority + low_priority

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
            delete_url = f"{BASE_URL}/issues/{issue_num}/labels/{label}"
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

    # Step 1: Update status to awaiting-estimation (orchestrator selected issue)
    print("[LABEL] Updating status → awaiting-estimation...")
    if not update_issue_status(issue_num, "status:awaiting-estimation"):
        print(
            "[WARNING] Could not update status to awaiting-estimation, continuing anyway..."
        )

    # Step 2: Update status to in-progress (beginning work)
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
            # Step 3: Update status to in-review (PR created, awaiting review)
            print("[LABEL] Updating status → in-review...")
            update_issue_status(issue_num, "status:in-review")
            return True
        else:
            print(
                f"[FAILED] Issue #{issue_num} failed with exit code {result.returncode}"
            )
            # Leave status:in-progress for manual review
            return False

    except subprocess.TimeoutExpired:
        print(f"[TIMEOUT] Issue #{issue_num} exceeded {WORKER_TIMEOUT}s timeout")
        return False
    except FileNotFoundError:
        print("[ERROR] Claude Code CLI not found. Please install Claude Code first.")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Issue #{issue_num} failed with error: {e}")
        return False


def main_loop():
    """
    Main orchestration loop.

    Continuously fetches and processes issues until manually stopped (Ctrl+C).
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
                    print(
                        f"[IDLE] No issues to process. Sleeping {IDLE_SLEEP_SECONDS}s..."
                    )
                    time.sleep(IDLE_SLEEP_SECONDS)
                    break  # Exit inner loop, will refresh in outer loop

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
