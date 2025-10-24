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
sys.path.insert(0, str(Path(__file__).parent / "src"))

# Import orchestrator functions (after setting GITHUB_TOKEN to avoid exit on import)
import os
os.environ['GITHUB_TOKEN'] = 'test_token_12345'

from orchestrator.orchestrator import (
    fetch_prioritized_issues,
    remove_status_labels,
    update_issue_status,
    post_issue_comment,
    fetch_recent_commits,
    fetch_all_open_issues,
    fetch_blocked_issues,
    REPO_OWNER,
    REPO_NAME,
)


class TestOrchestratorIssueFetching(unittest.TestCase):
    """Test issue fetching and filtering logic."""

    @patch('orchestrator.orchestrator.requests.get')
    def test_fetch_prioritized_issues_filters_correctly(self, mock_get):
        """Test that fetch_prioritized_issues correctly filters by priority, type, and status."""
        # Mock API response with various issue types
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "number": 1,
                "title": "High priority task",
                "labels": [
                    {"name": "priority:high"},
                    {"name": "type:task"}
                ],
                "created_at": "2025-01-01T00:00:00Z"
            },
            {
                "number": 2,
                "title": "Medium priority task",
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
                "title": "Low priority task (should be filtered out)",
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
        issues = fetch_prioritized_issues()

        # Verify only issues 1 and 2 are returned
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0]["number"], 1)  # High priority first
        self.assertEqual(issues[1]["number"], 2)  # Medium priority second

    @patch('orchestrator.orchestrator.requests.get')
    def test_fetch_prioritized_issues_sorts_by_priority(self, mock_get):
        """Test that high priority issues come before medium priority."""
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {
                "number": 10,
                "title": "Medium priority task (older)",
                "labels": [
                    {"name": "priority:medium"},
                    {"name": "type:task"}
                ],
                "created_at": "2025-01-01T00:00:00Z"
            },
            {
                "number": 11,
                "title": "High priority task (newer)",
                "labels": [
                    {"name": "priority:high"},
                    {"name": "type:task"}
                ],
                "created_at": "2025-01-02T00:00:00Z"
            },
        ]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        issues = fetch_prioritized_issues()

        # High priority should come first despite being newer
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0]["number"], 11)  # High priority
        self.assertEqual(issues[1]["number"], 10)  # Medium priority

    @patch('orchestrator.orchestrator.requests.get')
    def test_fetch_prioritized_issues_empty_result(self, mock_get):
        """Test handling of empty issue list."""
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        issues = fetch_prioritized_issues()

        self.assertEqual(len(issues), 0)

    @patch('orchestrator.orchestrator.requests.get')
    def test_fetch_prioritized_issues_api_error(self, mock_get):
        """Test handling of API errors."""
        import requests
        mock_get.side_effect = requests.exceptions.RequestException("API Error")

        issues = fetch_prioritized_issues()

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
