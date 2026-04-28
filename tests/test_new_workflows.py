# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
"""Tests for the 4 receipt + brand-voice composed workflows.

These tests exercise the input-model validation surface and confirm the
tools register cleanly. Live exercise (Gmail / Chat / Sheets / LLM) is
out-of-scope here; the underlying components are already tested
independently in their own files.
"""

from __future__ import annotations

import pytest

from tools import workflows as wf


# --------------------------------------------------------------------------- #
# Pydantic input validation
# --------------------------------------------------------------------------- #


class TestReceiptChatDigestInput:
    def test_minimum_valid(self):
        m = wf.ReceiptChatDigestInput(chat_space_id="spaces/AAQA")
        assert m.days == 30  # default

    def test_rejects_short_space_id(self):
        with pytest.raises(Exception):
            wf.ReceiptChatDigestInput(chat_space_id="x")

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            wf.ReceiptChatDigestInput(
                chat_space_id="spaces/AAQA", surprise="boom",
            )

    def test_days_bounds(self):
        with pytest.raises(Exception):
            wf.ReceiptChatDigestInput(chat_space_id="spaces/X", days=0)
        with pytest.raises(Exception):
            wf.ReceiptChatDigestInput(chat_space_id="spaces/X", days=400)


class TestMonthlyExpenseReportInput:
    def test_minimum_valid(self):
        m = wf.MonthlyExpenseReportInput(
            month="2026-04", recipient_email="x@y.com",
        )
        assert m.month == "2026-04"

    def test_rejects_missing_month(self):
        with pytest.raises(Exception):
            wf.MonthlyExpenseReportInput(recipient_email="x@y.com")

    def test_rejects_missing_recipient(self):
        with pytest.raises(Exception):
            wf.MonthlyExpenseReportInput(month="2026-04")


class TestSuggestResponseTemplateInput:
    def test_accepts_message_id(self):
        m = wf.SuggestResponseTemplateInput(message_id="abc123")
        assert m.message_id == "abc123"

    def test_accepts_text_only(self):
        m = wf.SuggestResponseTemplateInput(text="Some inbound text")
        assert m.text == "Some inbound text"

    def test_both_optional_neither_required_at_validation(self):
        """Either message_id or text is required, but the model itself
        accepts both as None — runtime check enforces 'at least one'."""
        m = wf.SuggestResponseTemplateInput()
        assert m.message_id is None
        assert m.text is None

    def test_rejects_extra_fields(self):
        with pytest.raises(Exception):
            wf.SuggestResponseTemplateInput(
                message_id="x", surprise="boom",
            )


class TestSmartFollowupFinderInput:
    def test_defaults(self):
        m = wf.SmartFollowupFinderInput()
        assert m.days_stale == 7
        assert m.max_threads == 20
        assert m.only_external is True
        assert m.create_drafts is False  # safe default

    def test_days_stale_bounds(self):
        with pytest.raises(Exception):
            wf.SmartFollowupFinderInput(days_stale=0)
        with pytest.raises(Exception):
            wf.SmartFollowupFinderInput(days_stale=200)

    def test_max_threads_bounds(self):
        with pytest.raises(Exception):
            wf.SmartFollowupFinderInput(max_threads=0)
        with pytest.raises(Exception):
            wf.SmartFollowupFinderInput(max_threads=500)


# --------------------------------------------------------------------------- #
# Registration smoke — the 4 new tools ship in workflows.py
# --------------------------------------------------------------------------- #


def test_all_four_register():
    """Confirm the 4 new workflows are wired into the register() function."""
    class Fake:
        def __init__(self):
            self.names = []
        def tool(self, name=None, **kw):
            def deco(fn):
                self.names.append(name or fn.__name__)
                return fn
            return deco
    fake = Fake()
    wf.register(fake)
    expected = {
        "workflow_receipt_chat_digest",
        "workflow_monthly_expense_report",
        "workflow_suggest_response_template",
        "workflow_smart_followup_finder",
    }
    missing = expected - set(fake.names)
    assert not missing, f"Workflows not registered: {missing}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
