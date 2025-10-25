#!/usr/bin/env python3
"""
GitHub Issue Orchestrator - Lightweight POC

Automatically processes GitHub issues by:
1. Fetching open issues with priority:high or priority:medium labels
2. Sorting FIFO (oldest first) within priority tier
3. Spawning Claude Code workers to process issues
4. Updating GitHub labels to track state
5. Optionally pokes CEO for strategic assessment when queue is empty

Usage:
    export GITHUB_TOKEN="ghp_..."
    python tools/orchestrator.py [--enable-ceo-poke]

Options:
    --enable-ceo-poke    Enable CEO assessment when queue is empty (default: disabled)
"""
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("[ERROR] requests library not found. Install with: pip install requests")
    sys.exit(1)

from .context_manager import ContextManager

# Configuration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_OWNER = "bennetwi92"
REPO_NAME = "SolaraAlpha"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
HEADERS = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

# Claude CLI configuration
CLAUDE_CLI_PATH = os.path.expanduser("~/.claude/local/claude")

# Worker configuration
WORKER_TIMEOUT = 1200  # 20 minutes
IDLE_SLEEP_SECONDS = 300  # 5 minutes
MAX_COMMENT_LENGTH = 65000  # GitHub limit is 65,536; leave buffer for formatting

# Initialize context manager
context_mgr = ContextManager(repo_root=REPO_ROOT)


def detect_session_limit(output: str) -> Optional[str]:
    """
    Detect if Claude CLI output indicates a session limit error.

    Args:
        output: Combined stdout/stderr from Claude CLI

    Returns:
        Session limit message if detected, None otherwise
    """
    # Pattern to match session limit messages like "Session limit reached ∙ resets 2pm"
    patterns = [
        r"Session limit reached.*resets\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))",
        r"session limit.*resets?\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))",
        r"rate limit.*resets?\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))",
    ]

    for pattern in patterns:
        match = re.search(pattern, output, re.IGNORECASE)
        if match:
            # Return the full matched line for context
            return match.group(0)

    return None


def parse_reset_time(message: str) -> Optional[datetime]:
    """
    Parse reset time from session limit message.

    Args:
        message: Session limit message containing reset time

    Returns:
        datetime of when session limit resets, or None if unable to parse
    """
    # Extract time string like "2pm", "2:30pm", "14:00"
    time_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", message, re.IGNORECASE)
    if not time_match:
        return None

    hour = int(time_match.group(1))
    minute = int(time_match.group(2)) if time_match.group(2) else 0
    period = time_match.group(3).lower()

    # Convert to 24-hour format
    if period == 'pm' and hour != 12:
        hour += 12
    elif period == 'am' and hour == 12:
        hour = 0

    # Get current time and construct reset time for today
    now = datetime.now()
    reset_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # If reset time is in the past, assume it's for tomorrow
    if reset_time <= now:
        reset_time += timedelta(days=1)

    return reset_time


def wait_for_session_reset(reset_message: str) -> bool:
    """
    Wait for session limit to reset.

    Args:
        reset_message: Session limit message containing reset time

    Returns:
        True if successfully waited, False if unable to parse reset time
    """
    reset_time = parse_reset_time(reset_message)

    if not reset_time:
        # Unable to parse reset time, wait a default period (1 hour)
        print("[SESSION LIMIT] Unable to parse reset time, waiting 1 hour...")
        time.sleep(3600)
        return True

    now = datetime.now()
    wait_seconds = (reset_time - now).total_seconds()

    if wait_seconds <= 0:
        # Reset time already passed
        print("[SESSION LIMIT] Reset time already passed, resuming immediately")
        return True

    # Add small buffer (5 minutes) to ensure session has reset
    wait_seconds += 300

    print(f"[SESSION LIMIT] Session limit reached. Pausing until {reset_time.strftime('%I:%M %p')}")
    print(f"[SESSION LIMIT] Wait time: {wait_seconds/60:.1f} minutes ({wait_seconds/3600:.1f} hours)")

    # Sleep until reset time
    time.sleep(wait_seconds)

    print(f"[SESSION LIMIT] Session limit should now be reset. Resuming operations...")
    return True


def fetch_prioritized_issues() -> List[Dict]:
    """
    Fetch open issues from GitHub with priority:high or priority:medium labels.
    Only processes type:task issues. Excludes:
    - Issues with any status:* labels (already being processed)
    - type:intervention issues (human-only)

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

        # Filter for priority and type labels
        priority_labels = {"priority:high", "priority:medium"}

        filtered_issues = []
        for issue in all_issues:
            issue_labels = {label["name"] for label in issue.get("labels", [])}

            # Check if has priority label
            has_priority = bool(issue_labels & priority_labels)
            # Must have type:task (skip interventions)
            has_task_type = "type:task" in issue_labels
            # Skip if ANY status:* label exists (already being processed)
            has_status = any(label.startswith("status:") for label in issue_labels)
            # Skip pull requests (they have pull_request field)
            is_pr = "pull_request" in issue

            # Include ONLY if: has priority AND type:task AND no status labels AND not a PR
            if has_priority and has_task_type and not has_status and not is_pr:
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


def post_issue_comment(issue_num: int, comment: str) -> bool:
    """
    Post a comment to a GitHub issue.

    Args:
        issue_num: Issue number
        comment: Comment text to post

    Returns:
        True if successful, False otherwise
    """
    try:
        url = f"{BASE_URL}/issues/{issue_num}/comments"
        response = requests.post(url, headers=HEADERS, json={"body": comment})
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to post comment to issue #{issue_num}: {e}")
        return False


def close_issue(issue_num: int) -> bool:
    """
    Close a GitHub issue.

    Args:
        issue_num: Issue number

    Returns:
        True if successful, False otherwise
    """
    try:
        url = f"{BASE_URL}/issues/{issue_num}"
        response = requests.patch(url, headers=HEADERS, json={"state": "closed"})
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Failed to close issue #{issue_num}: {e}")
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


def spawn_ceo_poke() -> Tuple[bool, Optional[str]]:
    """
    Poke CEO to assess organizational state when queue is empty.

    CEO receives context:
    - Recent commits (what work was completed)
    - All open issues (current backlog)
    - Blocked issues (red flags)

    CEO decides whether to create new issues (via task-builder) or report no action needed.

    Returns:
        Tuple of (success, session_limit_message)
        - success: True if CEO completed successfully, False otherwise
        - session_limit_message: Session limit message if detected, None otherwise
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
            [CLAUDE_CLI_PATH, "-p", "--dangerously-skip-permissions", prompt],
            timeout=WORKER_TIMEOUT,
            capture_output=True,  # Changed to True to check for session limit
            text=True,
        )

        elapsed = time.time() - start_time
        print(f"[CEO] Completed in {elapsed:.1f}s with exit code {result.returncode}")

        # Check for session limit in output
        output_log = (result.stdout or "") + (result.stderr or "")
        session_limit_msg = detect_session_limit(output_log)

        if session_limit_msg:
            print(f"[CEO] Session limit detected: {session_limit_msg}")
            return False, session_limit_msg

        if result.returncode == 0:
            print("[CEO] Assessment completed successfully")
            return True, None
        else:
            print(f"[CEO] Assessment failed with exit code {result.returncode}")
            return False, None

    except subprocess.TimeoutExpired:
        print(f"[CEO] Assessment exceeded {WORKER_TIMEOUT}s timeout")
        return False, None
    except Exception as e:
        print(f"[CEO] Assessment failed with error: {e}")
        return False, None


def spawn_claude_worker(issue: Dict) -> Tuple[bool, Optional[str]]:
    """
    Spawn Claude Code CLI to process an issue.

    Args:
        issue: GitHub issue dictionary

    Returns:
        Tuple of (success, session_limit_message)
        - success: True if worker completed successfully, False otherwise
        - session_limit_message: Session limit message if detected, None otherwise
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

    # Build prompt with context for Claude Code
    print("[CONTEXT] Generating context for worker...")
    prompt = context_mgr.generate_full_context(issue_body)

    try:
        # Spawn Claude Code worker
        print(f"[WORKER] Starting Claude Code for issue #{issue_num}...")
        start_time = time.time()

        result = subprocess.run(
            [CLAUDE_CLI_PATH, "-p", "--dangerously-skip-permissions", prompt],
            timeout=WORKER_TIMEOUT,
            capture_output=True,  # Capture output to post as comment
            text=True,
        )

        elapsed = time.time() - start_time
        print(
            f"[WORKER] Completed in {elapsed:.1f}s with exit code {result.returncode}"
        )

        # Combine stdout and stderr for complete log
        output_log = ""
        if result.stdout:
            output_log += result.stdout
        if result.stderr:
            if output_log:
                output_log += "\n\n--- STDERR ---\n\n"
            output_log += result.stderr

        # Truncate if exceeds GitHub comment limit
        if len(output_log) > MAX_COMMENT_LENGTH:
            output_log = output_log[:MAX_COMMENT_LENGTH] + "\n\n[OUTPUT TRUNCATED - exceeded GitHub comment size limit]"

        # Check for session limit BEFORE processing results
        session_limit_msg = detect_session_limit(output_log)

        if session_limit_msg:
            print(f"[SESSION LIMIT] Detected session limit: {session_limit_msg}")

            # Post session limit comment to issue
            session_comment = f"""## Session Limit Reached

**Duration**: {elapsed:.1f}s
**Status**: ⏸️ Paused (Session Limit)

The Claude CLI session limit has been reached. The orchestrator will pause and retry this issue after the session resets.

**Session Limit Message**: {session_limit_msg}

This issue will be retried automatically - no action required.
"""
            print(f"[COMMENT] Posting session limit notice to issue #{issue_num}...")
            post_issue_comment(issue_num, session_comment)

            # Remove status label so issue can be retried after session reset
            print("[LABEL] Removing status labels (will retry after session reset)...")
            remove_status_labels(issue_num)

            return False, session_limit_msg

        # Post output as comment to issue
        if output_log.strip():
            comment_body = f"""## Orchestrator Execution Log

**Duration**: {elapsed:.1f}s
**Exit Code**: {result.returncode}
**Status**: {'✅ Success' if result.returncode == 0 else '❌ Failed'}

<details>
<summary>View full execution log</summary>

```
{output_log}
```

</details>
"""
            print(f"[COMMENT] Posting execution log to issue #{issue_num}...")
            post_issue_comment(issue_num, comment_body)

        if result.returncode == 0:
            print(f"[SUCCESS] Issue #{issue_num} completed successfully")

            # Update context cache with success
            print("[CONTEXT] Updating context cache with completion...")
            context_mgr.update_cache(
                issue_num=issue_num,
                issue_title=issue_title,
                outcome="success",
                key_learning=f"Completed: {issue_title}"
            )

            # Post completion comment with context
            completion_comment = context_mgr.generate_completion_comment(
                outcome="success",
                key_learning=f"Successfully completed task"
            )
            post_issue_comment(issue_num, completion_comment)

            # Remove status label and close issue
            print("[LABEL] Removing status labels...")
            remove_status_labels(issue_num)
            print("[CLOSE] Closing issue...")
            if not close_issue(issue_num):
                print("[WARNING] Could not close issue, but task completed successfully")
            return True, None
        else:
            print(
                f"[FAILED] Issue #{issue_num} failed with exit code {result.returncode}"
            )

            # Update context cache with failure
            print("[CONTEXT] Updating context cache with blockage...")
            context_mgr.update_cache(
                issue_num=issue_num,
                issue_title=issue_title,
                outcome="blocked",
                blocker=f"Worker failed with exit code {result.returncode}"
            )

            # Post completion comment with context
            completion_comment = context_mgr.generate_completion_comment(outcome="blocked")
            post_issue_comment(issue_num, completion_comment)

            # Update status to blocked
            print("[LABEL] Updating status → blocked...")
            update_issue_status(issue_num, "status:blocked")
            return False, None

    except subprocess.TimeoutExpired:
        print(f"[TIMEOUT] Issue #{issue_num} exceeded {WORKER_TIMEOUT}s timeout")

        # Update context cache with timeout
        context_mgr.update_cache(
            issue_num=issue_num,
            issue_title=issue_title,
            outcome="blocked",
            blocker=f"Worker timeout after {WORKER_TIMEOUT}s"
        )

        # Post timeout comment
        timeout_comment = f"""## Orchestrator Execution Log

**Duration**: {WORKER_TIMEOUT}s (timeout)
**Status**: ⏱️ Timeout

Worker exceeded maximum execution time of {WORKER_TIMEOUT}s and was terminated.
"""
        post_issue_comment(issue_num, timeout_comment)

        # Post completion comment with context
        completion_comment = context_mgr.generate_completion_comment(outcome="blocked")
        post_issue_comment(issue_num, completion_comment)

        # Update status to blocked
        print("[LABEL] Updating status → blocked...")
        update_issue_status(issue_num, "status:blocked")
        return False, None
    except FileNotFoundError:
        print("[ERROR] Claude Code CLI not found. Please install Claude Code first.")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Issue #{issue_num} failed with error: {e}")

        # Update context cache with error
        context_mgr.update_cache(
            issue_num=issue_num,
            issue_title=issue_title,
            outcome="blocked",
            blocker=f"Worker exception: {str(e)}"
        )

        # Post error comment
        error_comment = f"""## Orchestrator Execution Log

**Status**: ❌ Error

Worker failed with exception:
```
{str(e)}
```
"""
        post_issue_comment(issue_num, error_comment)

        # Post completion comment with context
        completion_comment = context_mgr.generate_completion_comment(outcome="blocked")
        post_issue_comment(issue_num, completion_comment)

        # Update status to blocked
        print("[LABEL] Updating status → blocked...")
        update_issue_status(issue_num, "status:blocked")
        return False, None


def main_loop(enable_ceo_poke: bool = False):
    """
    Main orchestration loop.

    Continuously fetches and processes issues until manually stopped (Ctrl+C).
    When queue is empty, optionally pokes CEO once to assess organizational state.

    Args:
        enable_ceo_poke: If True, poke CEO when queue is empty (default: False)
    """
    print("=" * 80)
    print("GitHub Issue Orchestrator - Lightweight POC")
    print(f"Repository: {REPO_OWNER}/{REPO_NAME}")
    print(f"Worker timeout: {WORKER_TIMEOUT}s")
    print(f"Idle sleep: {IDLE_SLEEP_SECONDS}s")
    print(f"CEO poke enabled: {enable_ceo_poke}")
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
                    if enable_ceo_poke and not ceo_poked_this_cycle:
                        # First time queue is empty and CEO poke enabled - poke CEO
                        print("[EMPTY QUEUE] No eligible issues found - poking CEO...")
                        ceo_success, ceo_session_limit_msg = spawn_ceo_poke()

                        if ceo_session_limit_msg:
                            # Session limit hit during CEO poke - wait for reset
                            print("[SESSION LIMIT] Session limit hit during CEO poke")
                            print("[SESSION LIMIT] Pausing orchestrator until session resets...")
                            wait_for_session_reset(ceo_session_limit_msg)
                            # After waiting, continue to retry
                            continue

                        ceo_poked_this_cycle = True
                        # Refresh immediately to check if CEO created new issues
                        continue
                    else:
                        # CEO already poked or CEO poke disabled, enter idle monitoring
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
                    success, session_limit_msg = spawn_claude_worker(issue)

                    if session_limit_msg:
                        # Session limit reached - wait for reset and retry
                        print(f"[SESSION LIMIT] Session limit hit while processing issue #{issue['number']}")
                        print("[SESSION LIMIT] Pausing orchestrator until session resets...")
                        wait_for_session_reset(session_limit_msg)
                        # After waiting, continue to retry same issue (it should have no status label now)
                        continue

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
    # Parse command-line arguments
    enable_ceo_poke = "--enable-ceo-poke" in sys.argv
    main_loop(enable_ceo_poke=enable_ceo_poke)
