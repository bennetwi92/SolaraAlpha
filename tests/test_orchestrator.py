#!/usr/bin/env python3
"""
Test suite for GitHub Issue Orchestrator.
Tests core functions with mocked GitHub API responses.
"""

import unittest
from unittest.mock import patch, MagicMock, call
import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import orchestrator functions (after setting GITHUB_TOKEN to avoid exit on import)
import os
os.environ['GITHUB_TOKEN'] = 'test_token_12345'

from orchestrator.orchestrator import (
    fetch_ready_issues,
    remove_status_labels,
    update_issue_status,
    post_issue_comment,
    close_issue,
    fetch_recent_commits,
    fetch_all_open_issues,
    fetch_blocked_issues,
    spawn_claude_worker,
    spawn_ceo_poke,
    main_loop,
    mark_issue_closed,
    clean_recently_closed_cache,
    recently_closed_issues,
    REPO_OWNER,
    REPO_NAME,
    WORKER_TIMEOUT,
    POST_CLOSE_DELAY,
    RECENTLY_CLOSED_TTL,
)


class TestOrchestratorIssueFetching(unittest.TestCase):
    """Test issue fetching and filtering logic."""

    @patch('orchestrator.orchestrator.requests.get')
    def test_fetch_ready_issues_filters_correctly(self, mock_get):
        """Test that fetch_ready_issues correctly filters by type and status."""
        # Mock API response with various issue types
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "number": 1,
                "title": "Task 1",
                "labels": [
                    {"name": "priority:high"},
                    {"name": "type:task"}
                ],
                "created_at": "2025-01-01T00:00:00Z"
            },
            {
                "number": 2,
                "title": "Task 2",
                "labels": [
                    {"name": "priority:medium"},
                    {"name": "type:task"}
                ],
                "created_at": "2025-01-02T00:00:00Z"
            },
            {
                "number": 3,
                "title": "Task with status label (should be filtered out)",
                "labels": [
                    {"name": "priority:high"},
                    {"name": "type:task"},
                    {"name": "status:in-progress"}
                ],
                "created_at": "2025-01-03T00:00:00Z"
            },
            {
                "number": 4,
                "title": "Low priority task",
                "labels": [
                    {"name": "priority:low"},
                    {"name": "type:task"}
                ],
                "created_at": "2025-01-04T00:00:00Z"
            },
            {
                "number": 5,
                "title": "Intervention issue (should be filtered out)",
                "labels": [
                    {"name": "priority:high"},
                    {"name": "type:intervention"}
                ],
                "created_at": "2025-01-05T00:00:00Z"
            },
            {
                "number": 6,
                "title": "PR (should be filtered out)",
                "labels": [
                    {"name": "priority:high"},
                    {"name": "type:task"}
                ],
                "pull_request": {},
                "created_at": "2025-01-06T00:00:00Z"
            },
        ]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        # Call function
        issues = fetch_ready_issues()

        # Verify issues 1, 2, and 4 are returned (in FIFO order)
        self.assertEqual(len(issues), 3)
        self.assertEqual(issues[0]["number"], 1)
        self.assertEqual(issues[1]["number"], 2)
        self.assertEqual(issues[2]["number"], 4)

    @patch('orchestrator.orchestrator.requests.get')
    def test_fetch_ready_issues_sorts_by_fifo(self, mock_get):
        """Test that issues are returned in FIFO order (oldest first)."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "number": 10,
                "title": "Older task",
                "labels": [
                    {"name": "priority:medium"},
                    {"name": "type:task"}
                ],
                "created_at": "2025-01-01T00:00:00Z"
            },
            {
                "number": 11,
                "title": "Newer task",
                "labels": [
                    {"name": "priority:high"},
                    {"name": "type:task"}
                ],
                "created_at": "2025-01-02T00:00:00Z"
            },
        ]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        issues = fetch_ready_issues()

        # Older issue should come first (FIFO)
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0]["number"], 10)  # Older
        self.assertEqual(issues[1]["number"], 11)  # Newer

    @patch('orchestrator.orchestrator.requests.get')
    def test_fetch_ready_issues_empty_result(self, mock_get):
        """Test handling of empty issue list."""
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        issues = fetch_ready_issues()

        self.assertEqual(len(issues), 0)

    @patch('orchestrator.orchestrator.requests.get')
    def test_fetch_ready_issues_api_error(self, mock_get):
        """Test handling of API errors."""
        import requests
        mock_get.side_effect = requests.exceptions.RequestException("API Error")

        issues = fetch_ready_issues()

        self.assertEqual(len(issues), 0)


class TestOrchestratorLabelManagement(unittest.TestCase):
    """Test label management functions."""

    @patch('orchestrator.orchestrator.requests.delete')
    @patch('orchestrator.orchestrator.requests.get')
    def test_remove_status_labels_success(self, mock_get, mock_delete):
        """Test successful removal of status labels."""
        # Mock get response with status labels
        mock_get_response = MagicMock()
        mock_get_response.json.return_value = {
            "number": 1,
            "labels": [
                {"name": "status:in-progress"},
                {"name": "status:blocked"},
                {"name": "priority:high"},
            ]
        }
        mock_get_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_get_response

        # Mock delete responses
        mock_delete_response = MagicMock()
        mock_delete_response.raise_for_status = MagicMock()
        mock_delete.return_value = mock_delete_response

        result = remove_status_labels(1)

        self.assertTrue(result)
        # Verify delete called twice (once for each status label)
        self.assertEqual(mock_delete.call_count, 2)

    @patch('orchestrator.orchestrator.requests.delete')
    @patch('orchestrator.orchestrator.requests.get')
    def test_remove_status_labels_no_status_labels(self, mock_get, mock_delete):
        """Test removal when no status labels exist."""
        mock_get_response = MagicMock()
        mock_get_response.json.return_value = {
            "number": 1,
            "labels": [
                {"name": "priority:high"},
                {"name": "type:task"},
            ]
        }
        mock_get_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_get_response

        result = remove_status_labels(1)

        self.assertTrue(result)
        # Verify delete never called (no status labels to remove)
        mock_delete.assert_not_called()

    @patch('orchestrator.orchestrator.requests.post')
    @patch('orchestrator.orchestrator.remove_status_labels')
    def test_update_issue_status_success(self, mock_remove, mock_post):
        """Test successful status update."""
        mock_remove.return_value = True

        mock_post_response = MagicMock()
        mock_post_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_post_response

        result = update_issue_status(1, "status:in-progress")

        self.assertTrue(result)
        mock_remove.assert_called_once_with(1)
        mock_post.assert_called_once()

    @patch('orchestrator.orchestrator.requests.post')
    def test_post_issue_comment_success(self, mock_post):
        """Test successful comment posting."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        result = post_issue_comment(1, "Test comment")

        self.assertTrue(result)
        mock_post.assert_called_once()

    @patch('orchestrator.orchestrator.requests.patch')
    def test_close_issue_success(self, mock_patch):
        """Test successful issue closing."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_patch.return_value = mock_response

        result = close_issue(1)

        self.assertTrue(result)
        # Verify PATCH called with correct state
        mock_patch.assert_called_once()
        call_args = mock_patch.call_args
        self.assertEqual(call_args[1]['json']['state'], 'closed')


class TestOrchestratorDataFetching(unittest.TestCase):
    """Test data fetching functions."""

    @patch('orchestrator.orchestrator.requests.get')
    def test_fetch_recent_commits_success(self, mock_get):
        """Test successful commit fetching."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "sha": "abc123def456",
                "commit": {
                    "message": "Fix bug in orchestrator\nDetailed description"
                }
            },
            {
                "sha": "789xyz456",
                "commit": {
                    "message": "Add new feature"
                }
            },
        ]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        commits = fetch_recent_commits(count=2)

        self.assertEqual(len(commits), 2)
        self.assertEqual(commits[0], "abc123d: Fix bug in orchestrator")
        self.assertEqual(commits[1], "789xyz4: Add new feature")

    @patch('orchestrator.orchestrator.requests.get')
    def test_fetch_all_open_issues_filters_prs(self, mock_get):
        """Test that fetch_all_open_issues filters out pull requests."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "number": 1,
                "title": "Regular issue",
            },
            {
                "number": 2,
                "title": "Pull request",
                "pull_request": {}
            },
        ]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        issues = fetch_all_open_issues()

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["number"], 1)

    @patch('orchestrator.orchestrator.requests.get')
    def test_fetch_blocked_issues_success(self, mock_get):
        """Test successful blocked issue fetching."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "number": 5,
                "title": "Blocked task",
                "labels": [{"name": "status:blocked"}]
            },
        ]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        issues = fetch_blocked_issues()

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["number"], 5)


class TestOrchestratorWorkerSpawning(unittest.TestCase):
    """Test worker spawning and execution logic."""

    @patch('orchestrator.orchestrator.time.sleep')
    @patch('orchestrator.orchestrator.subprocess.run')
    @patch('orchestrator.orchestrator.close_issue')
    @patch('orchestrator.orchestrator.post_issue_comment')
    @patch('orchestrator.orchestrator.remove_status_labels')
    @patch('orchestrator.orchestrator.update_issue_status')
    def test_spawn_claude_worker_success(self, mock_update_status, mock_remove_labels, mock_post_comment, mock_close_issue, mock_subprocess, mock_sleep):
        """Test successful worker execution."""
        # Mock subprocess success
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Worker output"
        mock_result.stderr = ""
        mock_subprocess.return_value = mock_result

        # Mock label operations
        mock_update_status.return_value = True
        mock_remove_labels.return_value = True
        mock_post_comment.return_value = True
        mock_close_issue.return_value = True

        # Create test issue
        issue = {
            "number": 42,
            "title": "Test issue",
            "body": "Test body",
            "html_url": "https://github.com/test/test/issues/42"
        }

        result, session_limit = spawn_claude_worker(issue)

        # Verify success
        self.assertTrue(result)
        self.assertIsNone(session_limit)
        # Verify subprocess called (at least once for Claude CLI, may include git log for context)
        self.assertTrue(mock_subprocess.called)
        # Verify status updated to in-progress
        mock_update_status.assert_called_once_with(42, "status:in-progress")
        # Verify status removed on success
        mock_remove_labels.assert_called_once_with(42)
        # Verify issue closed
        mock_close_issue.assert_called_once_with(42)
        # Verify comments posted (execution log + completion context)
        self.assertEqual(mock_post_comment.call_count, 2)

    @patch('orchestrator.orchestrator.subprocess.run')
    @patch('orchestrator.orchestrator.post_issue_comment')
    @patch('orchestrator.orchestrator.update_issue_status')
    def test_spawn_claude_worker_failure(self, mock_update_status, mock_post_comment, mock_subprocess):
        """Test worker execution failure."""
        # Mock subprocess failure
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "Error output"
        mock_result.stderr = "Error details"
        mock_subprocess.return_value = mock_result

        mock_update_status.return_value = True
        mock_post_comment.return_value = True

        issue = {
            "number": 43,
            "title": "Failing issue",
            "body": "Test body",
            "html_url": "https://github.com/test/test/issues/43"
        }

        result, session_limit = spawn_claude_worker(issue)

        # Verify failure
        self.assertFalse(result)
        # Verify status updated to blocked
        calls = mock_update_status.call_args_list
        self.assertEqual(calls[-1], call(43, "status:blocked"))

    @patch('orchestrator.orchestrator.subprocess.run')
    @patch('orchestrator.orchestrator.post_issue_comment')
    @patch('orchestrator.orchestrator.update_issue_status')
    def test_spawn_claude_worker_timeout(self, mock_update_status, mock_post_comment, mock_subprocess):
        """Test worker timeout handling."""
        import subprocess
        # Mock subprocess timeout
        mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=WORKER_TIMEOUT)

        mock_update_status.return_value = True
        mock_post_comment.return_value = True

        issue = {
            "number": 44,
            "title": "Timeout issue",
            "body": "Test body",
            "html_url": "https://github.com/test/test/issues/44"
        }

        result, session_limit = spawn_claude_worker(issue)

        # Verify timeout handled
        self.assertFalse(result)
        # Verify status updated to blocked
        calls = mock_update_status.call_args_list
        self.assertEqual(calls[-1], call(44, "status:blocked"))
        # Verify timeout comment posted
        self.assertTrue(mock_post_comment.called)

    @patch('orchestrator.orchestrator.subprocess.run')
    @patch('orchestrator.orchestrator.post_issue_comment')
    @patch('orchestrator.orchestrator.update_issue_status')
    def test_spawn_claude_worker_exception(self, mock_update_status, mock_post_comment, mock_subprocess):
        """Test worker exception handling."""
        # Mock subprocess exception
        mock_subprocess.side_effect = Exception("Unexpected error")

        mock_update_status.return_value = True
        mock_post_comment.return_value = True

        issue = {
            "number": 45,
            "title": "Exception issue",
            "body": "Test body",
            "html_url": "https://github.com/test/test/issues/45"
        }

        result, session_limit = spawn_claude_worker(issue)

        # Verify exception handled
        self.assertFalse(result)
        # Verify status updated to blocked
        calls = mock_update_status.call_args_list
        self.assertEqual(calls[-1], call(45, "status:blocked"))

    @patch('orchestrator.orchestrator.time.sleep')
    @patch('orchestrator.orchestrator.close_issue')
    @patch('orchestrator.orchestrator.remove_status_labels')
    @patch('orchestrator.orchestrator.subprocess.run')
    @patch('orchestrator.orchestrator.post_issue_comment')
    @patch('orchestrator.orchestrator.update_issue_status')
    def test_spawn_claude_worker_output_truncation(self, mock_update_status, mock_post_comment, mock_subprocess, mock_remove_labels, mock_close_issue, mock_sleep):
        """Test output truncation for large logs."""
        from orchestrator.orchestrator import MAX_COMMENT_LENGTH

        # Mock subprocess with very large output
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "x" * (MAX_COMMENT_LENGTH + 1000)
        mock_result.stderr = ""
        mock_subprocess.return_value = mock_result

        mock_update_status.return_value = True
        mock_post_comment.return_value = True
        mock_remove_labels.return_value = True
        mock_close_issue.return_value = True

        issue = {
            "number": 46,
            "title": "Large output issue",
            "body": "Test body",
            "html_url": "https://github.com/test/test/issues/46"
        }

        result, session_limit = spawn_claude_worker(issue)

        # Verify success
        self.assertTrue(result)
        # Verify comment was posted with truncated output (first comment is execution log)
        self.assertTrue(mock_post_comment.called)
        first_comment = mock_post_comment.call_args_list[0][0][1]
        self.assertLess(len(first_comment), MAX_COMMENT_LENGTH + 500)  # Some buffer for formatting
        self.assertIn("TRUNCATED", first_comment)

    @patch('orchestrator.orchestrator.subprocess.run')
    @patch('orchestrator.orchestrator.fetch_blocked_issues')
    @patch('orchestrator.orchestrator.fetch_all_open_issues')
    @patch('orchestrator.orchestrator.fetch_recent_commits')
    def test_spawn_ceo_poke_success(self, mock_commits, mock_open_issues, mock_blocked_issues, mock_subprocess):
        """Test successful CEO poke execution."""
        # Mock context data
        mock_commits.return_value = ["abc123: Recent commit"]
        mock_open_issues.return_value = [
            {"number": 1, "title": "Open issue", "labels": [{"name": "priority:high"}]}
        ]
        mock_blocked_issues.return_value = []

        # Mock subprocess success
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_subprocess.return_value = mock_result

        result, session_limit = spawn_ceo_poke()

        # Verify success
        self.assertTrue(result)
        self.assertIsNone(session_limit)
        # Verify subprocess called
        mock_subprocess.assert_called_once()
        # Verify context gathered
        mock_commits.assert_called_once()
        mock_open_issues.assert_called_once()
        mock_blocked_issues.assert_called_once()

    @patch('orchestrator.orchestrator.subprocess.run')
    @patch('orchestrator.orchestrator.fetch_blocked_issues')
    @patch('orchestrator.orchestrator.fetch_all_open_issues')
    @patch('orchestrator.orchestrator.fetch_recent_commits')
    def test_spawn_ceo_poke_failure(self, mock_commits, mock_open_issues, mock_blocked_issues, mock_subprocess):
        """Test CEO poke execution failure."""
        # Mock context data
        mock_commits.return_value = []
        mock_open_issues.return_value = []
        mock_blocked_issues.return_value = []

        # Mock subprocess failure
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_subprocess.return_value = mock_result

        result, session_limit = spawn_ceo_poke()

        # Verify failure
        self.assertFalse(result)

    @patch('orchestrator.orchestrator.subprocess.run')
    @patch('orchestrator.orchestrator.fetch_blocked_issues')
    @patch('orchestrator.orchestrator.fetch_all_open_issues')
    @patch('orchestrator.orchestrator.fetch_recent_commits')
    def test_spawn_ceo_poke_timeout(self, mock_commits, mock_open_issues, mock_blocked_issues, mock_subprocess):
        """Test CEO poke timeout handling."""
        import subprocess
        # Mock context data
        mock_commits.return_value = []
        mock_open_issues.return_value = []
        mock_blocked_issues.return_value = []

        # Mock subprocess timeout
        mock_subprocess.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=WORKER_TIMEOUT)

        result, session_limit = spawn_ceo_poke()

        # Verify timeout handled
        self.assertFalse(result)


class TestOrchestratorMainLoop(unittest.TestCase):
    """Test main orchestration loop logic."""

    @patch('orchestrator.orchestrator.time.sleep')
    @patch('orchestrator.orchestrator.spawn_claude_worker')
    @patch('orchestrator.orchestrator.fetch_ready_issues')
    def test_main_loop_processes_single_issue(self, mock_fetch, mock_spawn_worker, mock_sleep):
        """Test main loop processes a single issue then enters idle."""
        # Mock: First call returns 1 issue, second call returns empty (idle)
        test_issue = {
            "number": 100,
            "title": "Test issue",
            "body": "Test body",
            "html_url": "https://github.com/test/test/issues/100"
        }

        mock_fetch.side_effect = [
            [test_issue],  # First fetch: has work
            [],            # Second fetch: empty queue (triggers sleep)
            KeyboardInterrupt()  # Exit loop
        ]
        mock_spawn_worker.return_value = (True, None)

        # Run loop (will be interrupted by KeyboardInterrupt)
        try:
            main_loop(enable_ceo_poke=False)
        except SystemExit:
            pass  # Expected from KeyboardInterrupt handler

        # Verify worker spawned for the issue
        mock_spawn_worker.assert_called_once_with(test_issue)
        # Verify sleep called when queue empty
        mock_sleep.assert_called()

    @patch('orchestrator.orchestrator.time.sleep')
    @patch('orchestrator.orchestrator.spawn_ceo_poke')
    @patch('orchestrator.orchestrator.fetch_ready_issues')
    def test_main_loop_triggers_ceo_poke_when_enabled(self, mock_fetch, mock_spawn_ceo, mock_sleep):
        """Test main loop triggers CEO poke when queue empty and enabled."""
        # Mock: Empty queue, then interrupt
        mock_fetch.side_effect = [
            [],  # Empty queue on first fetch
            [],  # Still empty after CEO poke
            KeyboardInterrupt()
        ]
        mock_spawn_ceo.return_value = (True, None)

        try:
            main_loop(enable_ceo_poke=True)
        except SystemExit:
            pass

        # Verify CEO poke called exactly once
        mock_spawn_ceo.assert_called_once()

    @patch('orchestrator.orchestrator.time.sleep')
    @patch('orchestrator.orchestrator.spawn_ceo_poke')
    @patch('orchestrator.orchestrator.fetch_ready_issues')
    def test_main_loop_ceo_poke_not_called_when_disabled(self, mock_fetch, mock_spawn_ceo, mock_sleep):
        """Test CEO poke not called when disabled."""
        # Mock: Empty queue, then interrupt
        mock_fetch.side_effect = [
            [],  # Empty queue
            KeyboardInterrupt()
        ]

        try:
            main_loop(enable_ceo_poke=False)
        except SystemExit:
            pass

        # Verify CEO poke never called
        mock_spawn_ceo.assert_not_called()

    @patch('orchestrator.orchestrator.time.sleep')
    @patch('orchestrator.orchestrator.spawn_claude_worker')
    @patch('orchestrator.orchestrator.spawn_ceo_poke')
    @patch('orchestrator.orchestrator.fetch_ready_issues')
    def test_main_loop_ceo_poke_resets_on_new_work(self, mock_fetch, mock_spawn_ceo, mock_spawn_worker, mock_sleep):
        """Test CEO poke flag resets when new work appears after empty queue."""
        test_issue = {
            "number": 101,
            "title": "New work",
            "body": "After CEO poke",
            "html_url": "https://github.com/test/test/issues/101"
        }

        # Sequence: empty -> CEO poke -> new work -> empty again -> CEO poke again
        mock_fetch.side_effect = [
            [],  # Empty: triggers CEO poke
            [test_issue],  # CEO created work
            [],  # Processed work, now empty again
            [],  # Still empty: should trigger CEO poke again
            KeyboardInterrupt()
        ]
        mock_spawn_ceo.return_value = (True, None)
        mock_spawn_worker.return_value = (True, None)

        try:
            main_loop(enable_ceo_poke=True)
        except SystemExit:
            pass

        # Verify CEO poke called twice (once per empty queue cycle)
        self.assertEqual(mock_spawn_ceo.call_count, 2)
        # Verify worker called for the issue
        mock_spawn_worker.assert_called_once_with(test_issue)

    @patch('orchestrator.orchestrator.time.sleep')
    @patch('orchestrator.orchestrator.spawn_claude_worker')
    @patch('orchestrator.orchestrator.fetch_ready_issues')
    def test_main_loop_processes_issues_until_empty(self, mock_fetch, mock_spawn_worker, mock_sleep):
        """Test main loop processes multiple issues until queue empty."""
        issue1 = {"number": 1, "title": "Issue 1", "body": "Body 1", "html_url": "http://test/1"}
        issue2 = {"number": 2, "title": "Issue 2", "body": "Body 2", "html_url": "http://test/2"}
        issue3 = {"number": 3, "title": "Issue 3", "body": "Body 3", "html_url": "http://test/3"}

        # Sequence: 3 issues -> 2 issues -> 1 issue -> empty
        mock_fetch.side_effect = [
            [issue1, issue2, issue3],  # First fetch: 3 issues
            [issue2, issue3],           # After processing issue1
            [issue3],                   # After processing issue2
            [],                         # After processing issue3: empty
            KeyboardInterrupt()
        ]
        mock_spawn_worker.return_value = (True, None)

        try:
            main_loop(enable_ceo_poke=False)
        except SystemExit:
            pass

        # Verify all 3 issues processed (only first issue from each fetch)
        self.assertEqual(mock_spawn_worker.call_count, 3)
        mock_spawn_worker.assert_any_call(issue1)
        mock_spawn_worker.assert_any_call(issue2)
        mock_spawn_worker.assert_any_call(issue3)


class TestOrchestratorRaceConditionPrevention(unittest.TestCase):
    """Test race condition prevention for duplicate issue processing."""

    def setUp(self):
        """Clear recently_closed_issues cache before each test."""
        import orchestrator.orchestrator
        orchestrator.orchestrator.recently_closed_issues.clear()

    def test_mark_issue_closed_adds_to_cache(self):
        """Test that mark_issue_closed adds issue to cache with timestamp."""
        from datetime import datetime
        import orchestrator.orchestrator

        # Mark issue as closed
        mark_issue_closed(42)

        # Verify it's in the cache
        self.assertIn(42, orchestrator.orchestrator.recently_closed_issues)

        # Verify timestamp is recent (within last 5 seconds)
        timestamp = orchestrator.orchestrator.recently_closed_issues[42]
        age_seconds = (datetime.now() - timestamp).total_seconds()
        self.assertLess(age_seconds, 5)

    def test_clean_recently_closed_cache_removes_expired(self):
        """Test that clean_recently_closed_cache removes old entries."""
        from datetime import datetime, timedelta
        import orchestrator.orchestrator

        # Add a recent issue (should NOT be removed)
        mark_issue_closed(100)

        # Add an old issue manually (should be removed)
        old_timestamp = datetime.now() - timedelta(seconds=RECENTLY_CLOSED_TTL + 10)
        orchestrator.orchestrator.recently_closed_issues[200] = old_timestamp

        # Verify both are in cache
        self.assertIn(100, orchestrator.orchestrator.recently_closed_issues)
        self.assertIn(200, orchestrator.orchestrator.recently_closed_issues)

        # Clean cache
        clean_recently_closed_cache()

        # Verify only recent issue remains
        self.assertIn(100, orchestrator.orchestrator.recently_closed_issues)
        self.assertNotIn(200, orchestrator.orchestrator.recently_closed_issues)

    @patch('orchestrator.orchestrator.requests.get')
    def test_fetch_ready_issues_filters_recently_closed(self, mock_get):
        """Test that fetch_ready_issues skips recently closed issues."""
        import orchestrator.orchestrator

        # Mock API response with two valid tasks
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "number": 10,
                "title": "Task 10",
                "labels": [
                    {"name": "priority:high"},
                    {"name": "type:task"}
                ],
                "created_at": "2025-01-01T00:00:00Z"
            },
            {
                "number": 20,
                "title": "Task 20",
                "labels": [
                    {"name": "priority:high"},
                    {"name": "type:task"}
                ],
                "created_at": "2025-01-02T00:00:00Z"
            },
        ]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        # Mark issue 10 as recently closed
        mark_issue_closed(10)

        # Fetch issues
        issues = fetch_ready_issues()

        # Verify only issue 20 is returned (issue 10 was filtered out)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["number"], 20)

    @patch('orchestrator.orchestrator.requests.get')
    def test_fetch_ready_issues_shows_skip_count(self, mock_get):
        """Test that fetch shows count of skipped recently closed issues."""
        import orchestrator.orchestrator
        from io import StringIO
        import sys

        # Mock API response
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "number": 1,
                "title": "Task 1",
                "labels": [
                    {"name": "priority:high"},
                    {"name": "type:task"}
                ],
                "created_at": "2025-01-01T00:00:00Z"
            },
            {
                "number": 2,
                "title": "Task 2",
                "labels": [
                    {"name": "priority:high"},
                    {"name": "type:task"}
                ],
                "created_at": "2025-01-02T00:00:00Z"
            },
        ]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        # Mark both issues as recently closed
        mark_issue_closed(1)
        mark_issue_closed(2)

        # Capture stdout
        captured_output = StringIO()
        sys.stdout = captured_output

        try:
            fetch_ready_issues()
        finally:
            sys.stdout = sys.__stdout__

        # Verify skip count is shown in output
        output = captured_output.getvalue()
        self.assertIn("skipped 2 recently closed", output)

    def test_mark_issue_closed_cleanup_integration(self):
        """Test that mark_issue_closed automatically cleans up old entries."""
        from datetime import datetime, timedelta
        import orchestrator.orchestrator

        # Add an old entry manually
        old_timestamp = datetime.now() - timedelta(seconds=RECENTLY_CLOSED_TTL + 10)
        orchestrator.orchestrator.recently_closed_issues[999] = old_timestamp

        # Mark new issue as closed (should trigger cleanup)
        mark_issue_closed(111)

        # Verify old entry was removed and new entry exists
        self.assertNotIn(999, orchestrator.orchestrator.recently_closed_issues)
        self.assertIn(111, orchestrator.orchestrator.recently_closed_issues)


class TestOrchestratorConfiguration(unittest.TestCase):
    """Test configuration and constants."""

    def test_repo_configuration(self):
        """Verify repository configuration is set."""
        self.assertEqual(REPO_OWNER, "bennetwi92")
        self.assertEqual(REPO_NAME, "SolaraAlpha")


def run_tests():
    """Run all tests and return results."""
    print("=" * 70)
    print("Running Orchestrator Test Suite")
    print("=" * 70)
    print()

    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestOrchestratorIssueFetching))
    suite.addTests(loader.loadTestsFromTestCase(TestOrchestratorLabelManagement))
    suite.addTests(loader.loadTestsFromTestCase(TestOrchestratorDataFetching))
    suite.addTests(loader.loadTestsFromTestCase(TestOrchestratorWorkerSpawning))
    suite.addTests(loader.loadTestsFromTestCase(TestOrchestratorMainLoop))
    suite.addTests(loader.loadTestsFromTestCase(TestOrchestratorRaceConditionPrevention))
    suite.addTests(loader.loadTestsFromTestCase(TestOrchestratorConfiguration))

    # Run tests with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Print summary
    print()
    print("=" * 70)
    print("Test Summary")
    print("=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")

    if result.wasSuccessful():
        print("\n✅ All tests passed!")
        return True
    else:
        print("\n❌ Some tests failed")
        return False


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
