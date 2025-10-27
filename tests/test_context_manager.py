#!/usr/bin/env python3
"""
Test script for context manager functionality
"""

import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from orchestrator.context_manager import ContextManager


def test_context_manager():
    """Test basic context manager functionality."""
    print("Testing Context Manager")
    print("=" * 80)

    # Initialize manager
    repo_root = Path(__file__).parent.parent
    cm = ContextManager(repo_root=str(repo_root))

    # Clean up cache file if it exists (for clean test state)
    cache_file = Path(repo_root) / "src/orchestrator/.context_cache.json"
    if cache_file.exists():
        cache_file.unlink()
        print(f"[SETUP] Removed existing cache file: {cache_file}")

    # Recreate the ContextManager instance to ensure clean state
    cm = ContextManager(repo_root=str(repo_root))

    # Test 1: Empty cache
    print("\n[TEST 1] Loading empty cache...")
    cache = cm.load_cache()
    print(f"Cache loaded: {cache}")
    assert cache["last_updated"] is None, "Empty cache should have no update timestamp"
    print("✅ Empty cache test passed")

    # Test 2: Update cache with success
    print("\n[TEST 2] Updating cache with successful task...")
    cm.update_cache(
        issue_num=123,
        issue_title="Test Task - Add feature",
        outcome="success",
        key_learning="Feature implemented using new library",
        decisions={"testing_framework": "pytest"}
    )
    cache = cm.load_cache()
    print(f"Cache after update: {cache}")
    assert len(cache["recent_completions"]) == 1, "Should have 1 completion"
    assert cache["recent_completions"][0]["issue"] == "#123"
    assert cache["active_decisions"]["testing_framework"] == "pytest"
    print("✅ Success update test passed")

    # Test 3: Update cache with blocked task
    print("\n[TEST 3] Updating cache with blocked task...")
    cm.update_cache(
        issue_num=124,
        issue_title="Test Task - Fix bug",
        outcome="blocked",
        blocker="Missing API credentials"
    )
    cache = cm.load_cache()
    print(f"Cache after second update: {cache}")
    assert len(cache["recent_completions"]) == 2, "Should have 2 completions"
    assert cache["recent_completions"][0]["issue"] == "#124", "Most recent should be first"
    assert cache["recent_completions"][0]["blocker"] == "Missing API credentials"
    print("✅ Blocked update test passed")

    # Test 4: Generate context summary
    print("\n[TEST 4] Generating context summary...")
    summary = cm.get_context_summary()
    print(f"Context summary:\n{summary}")
    assert "#123" in summary, "Summary should contain issue #123"
    assert "#124" in summary, "Summary should contain issue #124"
    assert "pytest" in summary, "Summary should contain decision about pytest"
    print("✅ Context summary test passed")

    # Test 5: Git log parsing
    print("\n[TEST 5] Testing git log parsing...")
    commits = cm.parse_git_log(count=3)
    print(f"Found {len(commits)} commits:")
    for commit in commits:
        print(f"  - {commit['sha']}: {commit['message']}")
        if commit['context']:
            print(f"    Context: {commit['context']}")
    print("✅ Git log parsing test passed")

    # Test 6: Full context generation
    print("\n[TEST 6] Generating full context for task...")
    issue_body = "Implement new authentication module using JWT tokens"
    full_context = cm.generate_full_context(issue_body)
    print(f"Full context ({len(full_context)} chars):")
    print("-" * 80)
    print(full_context)
    print("-" * 80)
    assert "Recent Context" in full_context or "first run" in full_context
    assert issue_body in full_context
    print("✅ Full context generation test passed")

    # Test 7: Completion comments
    print("\n[TEST 7] Testing completion comment generation...")
    success_comment = cm.generate_completion_comment(
        outcome="success",
        key_learning="Successfully integrated feature"
    )
    print(f"Success comment:\n{success_comment}\n")
    assert "✅" in success_comment
    assert "Successfully integrated feature" in success_comment

    blocked_comment = cm.generate_completion_comment(outcome="blocked")
    print(f"Blocked comment:\n{blocked_comment}\n")
    assert "❌" in blocked_comment
    print("✅ Completion comment test passed")

    print("\n" + "=" * 80)
    print("All tests passed! ✅")
    print("=" * 80)


if __name__ == "__main__":
    test_context_manager()
