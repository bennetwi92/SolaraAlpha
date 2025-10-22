#!/usr/bin/env python3
"""
Test script for orchestrator - validates fetch and filter logic without spawning workers.
"""
import sys
import os

# Add parent directory to path to import orchestrator
sys.path.insert(0, os.path.dirname(__file__))

from orchestrator import fetch_prioritized_issues

def test_fetch_logic():
    """Test that orchestrator correctly fetches and filters issues."""
    print("=" * 80)
    print("Testing Orchestrator Fetch Logic")
    print("=" * 80)
    print()

    # Fetch issues using orchestrator logic
    issues = fetch_prioritized_issues()

    print(f"\n{'=' * 80}")
    print("FETCH RESULTS")
    print("=" * 80)
    print(f"Total issues found: {len(issues)}")
    print()

    # Expected results
    expected_issues = {
        368: {"priority": "high", "should_process": True, "reason": "High priority, no blocking labels"},
        369: {"priority": "medium", "should_process": True, "reason": "Medium priority, no blocking labels"},
        370: {"priority": "high", "should_process": False, "reason": "Has status:blocked label"},
        371: {"priority": "low", "should_process": False, "reason": "Low priority"}
    }

    print("EXPECTED BEHAVIOR:")
    print("-" * 80)
    for num, info in expected_issues.items():
        status = "✅ PROCESS" if info["should_process"] else "❌ SKIP"
        print(f"  #{num} [{info['priority']}]: {status} - {info['reason']}")

    print()
    print("ACTUAL RESULTS:")
    print("-" * 80)

    if not issues:
        print("  ⚠️  No issues returned by orchestrator")
        print()
        print("VERDICT: ❌ FAILED - Expected to find issues #368 and #369")
        return False

    found_numbers = []
    for idx, issue in enumerate(issues, 1):
        number = issue["number"]
        found_numbers.append(number)
        labels = {label["name"] for label in issue.get("labels", [])}

        priority_label = None
        for label in labels:
            if label.startswith("priority:"):
                priority_label = label
                break

        print(f"  [{idx}] Issue #{number}: {issue['title']}")
        print(f"      Priority: {priority_label}")
        print(f"      Labels: {', '.join(labels)}")
        print()

    # Validation
    print("=" * 80)
    print("VALIDATION")
    print("=" * 80)

    all_passed = True

    # Check that #368 is included (high priority, not blocked)
    if 368 in found_numbers:
        print("✅ Issue #368 correctly included (high priority)")
    else:
        print("❌ Issue #368 missing (should be included - high priority, not blocked)")
        all_passed = False

    # Check that #369 is included (medium priority, not blocked)
    if 369 in found_numbers:
        print("✅ Issue #369 correctly included (medium priority)")
    else:
        print("❌ Issue #369 missing (should be included - medium priority, not blocked)")
        all_passed = False

    # Check that #370 is excluded (blocked)
    if 370 not in found_numbers:
        print("✅ Issue #370 correctly excluded (status:blocked)")
    else:
        print("❌ Issue #370 incorrectly included (should be excluded - has status:blocked)")
        all_passed = False

    # Check that #371 is excluded (low priority)
    if 371 not in found_numbers:
        print("✅ Issue #371 correctly excluded (priority:low)")
    else:
        print("❌ Issue #371 incorrectly included (should be excluded - low priority)")
        all_passed = False

    # Check ordering (high priority first)
    if len(found_numbers) >= 2:
        if found_numbers[0] == 368:
            print("✅ Issue ordering correct (high priority #368 before medium #369)")
        else:
            print(f"❌ Issue ordering incorrect (expected #368 first, got #{found_numbers[0]})")
            all_passed = False

    print()
    print("=" * 80)
    if all_passed:
        print("VERDICT: ✅ ALL TESTS PASSED")
    else:
        print("VERDICT: ❌ SOME TESTS FAILED")
    print("=" * 80)

    return all_passed


if __name__ == "__main__":
    success = test_fetch_logic()
    sys.exit(0 if success else 1)
