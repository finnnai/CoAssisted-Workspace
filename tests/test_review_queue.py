"""Tests for review_queue module — atomic store for medium-confidence replies."""
from __future__ import annotations

from pathlib import Path

import pytest

import review_queue as rq


@pytest.fixture
def isolated_queue(tmp_path):
    """Each test gets a fresh review_queue.json in tmp_path."""
    fresh = tmp_path / "review_queue.json"
    rq._override_path_for_tests(fresh)
    yield rq
    rq._override_path_for_tests(
        Path(__file__).resolve().parent.parent / "review_queue.json"
    )


def test_add_and_get(isolated_queue):
    isolated_queue.add_for_review(
        content_key="k1",
        vendor_name="Acme",
        vendor_email="a@example.com",
        project_code="ALPHA",
        fields_requested=["invoice_number", "total"],
        parsed_fields={"invoice_number": "INV-99"},
        confidence="medium",
        reply_excerpt="Got it INV-99",
    )
    rec = isolated_queue.get("k1")
    assert rec is not None
    assert rec["confidence"] == "medium"
    assert rec["parsed_fields"] == {"invoice_number": "INV-99"}
    assert rec["queued_at"]  # ISO timestamp


def test_add_re_queues_existing(isolated_queue):
    """Re-adding the same content_key updates in place — most recent wins."""
    isolated_queue.add_for_review(
        content_key="k1", vendor_name="A", vendor_email=None,
        project_code="X", fields_requested=["a"],
        parsed_fields={"a": "first"}, confidence="medium",
        reply_excerpt="first",
    )
    isolated_queue.add_for_review(
        content_key="k1", vendor_name="A", vendor_email=None,
        project_code="X", fields_requested=["a"],
        parsed_fields={"a": "second"}, confidence="medium",
        reply_excerpt="second",
    )
    rec = isolated_queue.get("k1")
    assert rec["parsed_fields"] == {"a": "second"}
    assert len(isolated_queue.list_open()) == 1


def test_list_open_filters_by_project(isolated_queue):
    for code, ck in [("ALPHA", "k1"), ("BRAVO", "k2"), ("ALPHA", "k3")]:
        isolated_queue.add_for_review(
            content_key=ck, vendor_name="V", vendor_email=None,
            project_code=code, fields_requested=["a"],
            parsed_fields={"a": "x"}, confidence="medium",
            reply_excerpt="x",
        )
    alphas = isolated_queue.list_open(project_code="ALPHA")
    assert len(alphas) == 2
    assert {e["content_key"] for e in alphas} == {"k1", "k3"}


def test_list_open_most_recent_first(isolated_queue):
    import time
    isolated_queue.add_for_review(
        content_key="old", vendor_name="V", vendor_email=None,
        project_code="X", fields_requested=["a"],
        parsed_fields={"a": "x"}, confidence="medium",
        reply_excerpt="x",
    )
    time.sleep(0.01)  # ensure ISO timestamps differ
    isolated_queue.add_for_review(
        content_key="new", vendor_name="V", vendor_email=None,
        project_code="X", fields_requested=["a"],
        parsed_fields={"a": "x"}, confidence="medium",
        reply_excerpt="x",
    )
    entries = isolated_queue.list_open()
    assert entries[0]["content_key"] == "new"
    assert entries[1]["content_key"] == "old"


def test_mark_promoted_removes_entry(isolated_queue):
    isolated_queue.add_for_review(
        content_key="k1", vendor_name="V", vendor_email=None,
        project_code="X", fields_requested=["a"],
        parsed_fields={}, confidence="medium",
        reply_excerpt="x",
    )
    assert isolated_queue.mark_promoted("k1") is True
    assert isolated_queue.get("k1") is None
    # Idempotent — second call returns False, doesn't crash
    assert isolated_queue.mark_promoted("k1") is False


def test_invalid_confidence_raises(isolated_queue):
    with pytest.raises(ValueError):
        isolated_queue.add_for_review(
            content_key="k1", vendor_name="V", vendor_email=None,
            project_code="X", fields_requested=["a"],
            parsed_fields={}, confidence="bogus",
            reply_excerpt="x",
        )


def test_clear_drops_everything(isolated_queue):
    for ck in ("k1", "k2", "k3"):
        isolated_queue.add_for_review(
            content_key=ck, vendor_name="V", vendor_email=None,
            project_code="X", fields_requested=["a"],
            parsed_fields={}, confidence="medium",
            reply_excerpt="x",
        )
    assert isolated_queue.clear() == 3
    assert len(isolated_queue.list_open()) == 0
