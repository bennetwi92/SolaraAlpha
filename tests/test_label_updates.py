#!/usr/bin/env python3
"""
Test script for orchestrator label updates - validates that labels are added correctly.
"""
import sys
import os

# Add parent directory to path to import orchestrator
sys.path.insert(0, os.path.dirname(__file__))

from orchestrator import update_issue_label, BASE_URL, HEADERS
import requests

def get_issue_labels(issue_num: int):
    """Fetch current labels for an issue."""
    try:
        response = requests.get(f"{BASE_URL}/issues/{issue_num}", headers=HEADERS)
        response.raise_for_status()
        issue = response.json()
        return {label["name"] for label in issue.get("labels", [])}
    except Exception as e:
        print(f"Error fetching labels: {e}")
        return set()

def remove_issue_label(issue_num: int, label: str):
    """Remove a label from an issue."""
    try:
        url = f"{BASE_URL}/issues/{issue_num}/labels/{label}"
        response = requests.delete(url, headers=HEADERS)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Error removing label: {e}")
        return False

def test_label_updates():
    """Test that orchestrator correctly updates issue labels."""
    print("=" * 80)
    print("Testing Orchestrator Label Update Functionality")
    print("=" * 80)
    print()

    test_issue = 368  # Using the high-priority test issue
    test_label = "status:in-progress"

    print(f"Test Issue: #{test_issue}")
    print(f"Test Label: {test_label}")
    print()

    # Get initial labels
    print("[1] Fetching initial labels...")
    initial_labels = get_issue_labels(test_issue)
    print(f"    Initial labels: {initial_labels}")

    # Remove test label if it exists (clean slate)
    if test_label in initial_labels:
        print(f"[2] Removing existing '{test_label}' label for clean test...")
        if remove_issue_label(test_issue, test_label):
            print(f"    ✅ Label removed")
        else:
            print(f"    ❌ Failed to remove label")
            return False

    # Test adding the label
    print(f"[3] Adding '{test_label}' label using orchestrator function...")
    success = update_issue_label(test_issue, test_label)

    if success:
        print(f"    ✅ Function returned success")
    else:
        print(f"    ❌ Function returned failure")
        return False

    # Verify label was actually added
    print(f"[4] Verifying label was added...")
    updated_labels = get_issue_labels(test_issue)
    print(f"    Updated labels: {updated_labels}")

    if test_label in updated_labels:
        print(f"    ✅ Label '{test_label}' successfully added")
    else:
        print(f"    ❌ Label '{test_label}' not found after update")
        return False

    # Clean up - remove the test label
    print(f"[5] Cleaning up - removing test label...")
    if remove_issue_label(test_issue, test_label):
        print(f"    ✅ Test label removed")
    else:
        print(f"    ⚠️  Could not remove test label (manual cleanup needed)")

    print()
    print("=" * 80)
    print("VERDICT: ✅ LABEL UPDATE TESTS PASSED")
    print("=" * 80)

    return True


if __name__ == "__main__":
    success = test_label_updates()
    sys.exit(0 if success else 1)
