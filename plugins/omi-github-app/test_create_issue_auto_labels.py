"""Hermetic regression tests for issue #14280.

The backend serializes omitted optional tool arguments as JSON ``null``.
``create_issue`` documents ``auto_labels`` as default-on, so both an omitted
flag and an explicit JSON null must run AI label selection while an explicit
false must opt out.  The suite reuses the standard-library-only plugin test
harness and never contacts GitHub or an AI provider.
"""

import unittest
from unittest.mock import AsyncMock, patch

from test_main import USER, authed, call_tool, main


def create_payload(**overrides):
    payload = {"uid": "u1", "title": "Null-safe labels"}
    payload.update(overrides)
    return payload


class AutoLabelSelectionTests(unittest.TestCase):
    def run_creation(self, **payload_overrides):
        with authed(USER), patch.object(
            main.github_client,
            "get_repo_labels",
            return_value=["bug", "feature"],
        ) as get_labels, patch.object(
            main,
            "ai_select_labels",
            new=AsyncMock(return_value=["bug"]),
        ) as select_labels, patch.object(
            main.github_client,
            "create_issue",
            return_value={
                "success": True,
                "issue_number": 14280,
                "issue_url": "https://github.com/owner/repo/issues/14280",
            },
        ) as create_issue:
            response = call_tool(
                "/tools/create_issue",
                create_payload(**payload_overrides),
            )
        self.assertIsNone(response.error)
        self.assertEqual(create_issue.call_args.kwargs["labels"], ["bug"])
        return get_labels, select_labels, create_issue

    def test_omitted_flag_uses_documented_default(self):
        get_labels, select_labels, _ = self.run_creation()
        get_labels.assert_called_once_with("tok", "owner/repo")
        select_labels.assert_awaited_once_with(
            "Null-safe labels", "", ["bug", "feature"]
        )

    def test_null_flag_uses_documented_default(self):
        get_labels, select_labels, _ = self.run_creation(auto_labels=None)
        get_labels.assert_called_once_with("tok", "owner/repo")
        select_labels.assert_awaited_once()

    def test_explicit_false_skips_selection(self):
        with authed(USER), patch.object(
            main.github_client, "get_repo_labels"
        ) as get_labels, patch.object(
            main, "ai_select_labels", new=AsyncMock()
        ) as select_labels, patch.object(
            main.github_client,
            "create_issue",
            return_value={
                "success": True,
                "issue_number": 14280,
                "issue_url": "https://github.com/owner/repo/issues/14280",
            },
        ) as create_issue:
            response = call_tool(
                "/tools/create_issue",
                create_payload(auto_labels=False),
            )

        self.assertIsNone(response.error)
        get_labels.assert_not_called()
        select_labels.assert_not_awaited()
        self.assertEqual(create_issue.call_args.kwargs["labels"], [])

    def test_explicit_true_runs_selection(self):
        get_labels, select_labels, _ = self.run_creation(auto_labels=True)
        get_labels.assert_called_once()
        select_labels.assert_awaited_once()

    def test_supplied_labels_short_circuit_auto_selection(self):
        with authed(USER), patch.object(
            main.github_client, "get_repo_labels"
        ) as get_labels, patch.object(
            main, "ai_select_labels", new=AsyncMock()
        ) as select_labels, patch.object(
            main.github_client,
            "create_issue",
            return_value={
                "success": True,
                "issue_number": 14280,
                "issue_url": "https://github.com/owner/repo/issues/14280",
            },
        ) as create_issue:
            response = call_tool(
                "/tools/create_issue",
                create_payload(labels=["documentation"]),
            )

        self.assertIsNone(response.error)
        get_labels.assert_not_called()
        select_labels.assert_not_awaited()
        self.assertEqual(
            create_issue.call_args.kwargs["labels"], ["documentation"]
        )

    def test_nullish_label_payloads_still_fall_back_to_auto_selection(self):
        for raw_labels in (None, [], "", "  ", [None, None]):
            with self.subTest(raw_labels=raw_labels):
                _, select_labels, _ = self.run_creation(labels=raw_labels)
                select_labels.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
