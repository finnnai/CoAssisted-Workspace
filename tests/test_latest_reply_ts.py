"""Tests for vendor_followups.update_latest_reply_ts — dedup helper."""
from __future__ import annotations

from pathlib import Path

import pytest

import vendor_followups as vf


@pytest.fixture
def isolated_store(tmp_path):
    fresh = tmp_path / "awaiting_info.json"
    vf._override_path_for_tests(fresh)
    yield vf
    vf._override_path_for_tests(
        Path(__file__).resolve().parent.parent / "awaiting_info.json"
    )


def test_register_request_initializes_latest_reply_ts_to_none(isolated_store):
    rec = isolated_store.register_request(
        content_key="k1", channel="gmail", thread_id="t1",
        vendor_email="v@x.com", vendor_name="V",
        fields_requested=["invoice_number"],
        sheet_id="s1", row_number=2,
    )
    assert rec.get("latest_reply_ts") is None


def test_update_latest_reply_ts_persists(isolated_store):
    isolated_store.register_request(
        content_key="k1", channel="gmail", thread_id="t1",
        vendor_email="v@x.com", vendor_name="V",
        fields_requested=["invoice_number"],
        sheet_id="s1", row_number=2,
    )
    assert isolated_store.update_latest_reply_ts(
        "k1", "2026-04-29T10:00:00+00:00",
    ) is True
    rec = isolated_store.get("k1")
    assert rec["latest_reply_ts"] == "2026-04-29T10:00:00+00:00"


def test_update_unknown_key_returns_false(isolated_store):
    assert isolated_store.update_latest_reply_ts("unknown", "2026-04-29") is False


def test_update_with_empty_args_returns_false(isolated_store):
    assert isolated_store.update_latest_reply_ts("", "2026-04-29") is False
    assert isolated_store.update_latest_reply_ts("k1", "") is False
