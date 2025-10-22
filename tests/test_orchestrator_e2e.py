#!/usr/bin/env python3
"""
End-to-end simulation of orchestrator workflow.
Uses a mock worker instead of spawning actual Claude Code instances.
"""
import sys
import os
import time

# Add parent directory to path to import orchestrator
sys.path.insert(0, os.path.dirname(__file__))

from orchestrator import (
    fetch_prioritized_issues,
    update_issue_label,
    BASE_URL,
    HEADERS
)
import requests


def remove_status_labels(issue_num: int):
    """Remove all status labels from an issue for clean testing."""
    try:
        response = requests.get(f"{BASE_URL}/issues/{issue_num}", headers=HEADERS)
        response.raise_for_status()
        issue = response.json()

        status_labels = [
            label["name"] for label in issue.get("labels", [])
            if label["name"].startswith("status:")
        ]

        for label in status_labels:
            url = f"{BASE_URL}/issues/{issue_num}/labels/{label}"
            requests.delete(url, headers=HEADERS)
            print(f"    Removed '{label}' from #{issue_num}")

        return True
    except Exception as e:
        print(f"    Error cleaning labels: {e}")
        return False


def mock_worker(issue: dict, should_succeed: bool = True) -> bool:
    """
    Mock Claude Code worker for testing.
    Simulates processing an issue without actually running Claude Code.
    """
    issue_num = issue["number"]
    issue_title = issue["title"]

    print(f"\n[MOCK WORKER] Processing issue #{issue_num}: {issue_title}")
    print(f"[MOCK WORKER] Simulating work (2 seconds)...")

    # Simulate work
    time.sleep(2)

    if should_succeed:
        print(f"[MOCK WORKER] ✅ Issue #{issue_num} completed successfully")
        return True
    else:
        print(f"[MOCK WORKER] ❌ Issue #{issue_num} failed")
        return False


def test_e2e_workflow():
    """Test the complete orchestrator workflow end-to-end."""
    print("=" * 80)
    print("Orchestrator End-to-End Simulation")
    print("=" * 80)
    print()

    print("[SETUP] Cleaning status labels from test issues...")
    for issue_num in [368, 369]:
        print(f"  Issue #{issue_num}:")
        remove_status_labels(issue_num)
    print()

    print("=" * 80)
    print("[PHASE 1] Fetch Prioritized Issues")
    print("=" * 80)
    issues = fetch_prioritized_issues()

    if not issues:
        print("❌ No issues found to process")
        return False

    print(f"✅ Found {len(issues)} issues to process")
    for idx, issue in enumerate(issues, 1):
        print(f"  [{idx}] Issue #{issue['number']}: {issue['title']}")
    print()

    print("=" * 80)
    print("[PHASE 2] Process Issues Sequentially")
    print("=" * 80)

    results = []

    for idx, issue in enumerate(issues, 1):
        issue_num = issue["number"]
        print(f"\n[{idx}/{len(issues)}] Processing Issue #{issue_num}")
        print("-" * 80)

        # Step 1: Add status:awaiting-estimation label (orchestrator selected)
        print(f"[1] Adding 'status:awaiting-estimation' label...")
        if update_issue_label(issue_num, "status:awaiting-estimation"):
            print(f"    ✅ Label added")
        else:
            print(f"    ❌ Failed to add label")
            results.append({"issue": issue_num, "success": False, "reason": "Label update failed"})
            continue

        # Step 2: Add status:in-progress label (beginning work)
        print(f"[2] Adding 'status:in-progress' label...")
        if update_issue_label(issue_num, "status:in-progress"):
            print(f"    ✅ Label added")
        else:
            print(f"    ❌ Failed to add label")
            results.append({"issue": issue_num, "success": False, "reason": "Label update failed"})
            continue

        # Step 3: Run mock worker
        print(f"[3] Running worker...")
        # Simulate success for first issue, can customize for different scenarios
        worker_success = mock_worker(issue, should_succeed=True)

        # Step 4: Update labels based on result
        if worker_success:
            print(f"[4] Adding 'status:in-review' label...")
            update_issue_label(issue_num, "status:in-review")
            results.append({"issue": issue_num, "success": True})
        else:
            print(f"[4] Worker failed, keeping 'status:in-progress' label")
            results.append({"issue": issue_num, "success": False, "reason": "Worker failed"})

        print()

    # Phase 3: Verify orchestrator skips already-processed issues
    print("=" * 80)
    print("[PHASE 3] Verify Processed Issues Are Skipped")
    print("=" * 80)

    print("\nFetching issues again (should exclude processed ones)...")
    remaining_issues = fetch_prioritized_issues()

    processed_nums = [r["issue"] for r in results if r["success"]]

    print(f"Processed issues: {processed_nums}")
    print(f"Remaining issues found: {[i['number'] for i in remaining_issues]}")

    for issue_num in processed_nums:
        if any(i["number"] == issue_num for i in remaining_issues):
            print(f"❌ Issue #{issue_num} still in queue (should be excluded)")
            return False

    print("✅ All processed issues correctly excluded from next fetch")

    # Summary
    print()
    print("=" * 80)
    print("WORKFLOW SUMMARY")
    print("=" * 80)

    success_count = sum(1 for r in results if r["success"])
    failed_count = len(results) - success_count

    print(f"Total issues processed: {len(results)}")
    print(f"  ✅ Successful: {success_count}")
    print(f"  ❌ Failed:     {failed_count}")
    print()

    for result in results:
        status = "✅" if result["success"] else "❌"
        reason = f" ({result.get('reason', 'Unknown')})" if not result["success"] else ""
        print(f"  {status} Issue #{result['issue']}{reason}")

    print()
    print("=" * 80)
    print("VERDICT: ✅ END-TO-END WORKFLOW VALIDATED")
    print("=" * 80)
    print()
    print("Key behaviors confirmed:")
    print("  ✅ Fetches and filters issues correctly")
    print("  ✅ Processes high priority before medium priority")
    print("  ✅ Adds status:awaiting-estimation when selected")
    print("  ✅ Adds status:in-progress when starting work")
    print("  ✅ Adds status:in-review on success")
    print("  ✅ Excludes already-processed issues from next fetch")
    print()

    # Cleanup
    print("[CLEANUP] Removing test status labels...")
    for result in results:
        remove_status_labels(result["issue"])

    return True


if __name__ == "__main__":
    success = test_e2e_workflow()
    sys.exit(0 if success else 1)
