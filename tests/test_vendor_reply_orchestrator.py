"""End-to-end tests for workflow_process_vendor_replies' confidence gating
+ dedup + attachment paths.

We mock at the boundary of the orchestrator helpers (`_find_gmail_reply`,
`_apply_reply_update`, `_archive_reply_attachments_to_project`,
`_send_info_request_via_gmail`, `_compose_acknowledgement`,
`_parse_vendor_reply`, `llm.is_available`) — that gives us deterministic
control over the inputs without standing up a real Gmail/Drive/Sheets
mock stack.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import review_queue as rq
import vendor_followups as vf
from tools.project_invoices import ProcessVendorRepliesInput, ListReviewQueueInput


@pytest.fixture
def isolated_stores(tmp_path):
    """Isolate both the awaiting_info and review_queue stores per test."""
    vf_path = tmp_path / "awaiting_info.json"
    rq_path = tmp_path / "review_queue.json"
    vf._override_path_for_tests(vf_path)
    rq._override_path_for_tests(rq_path)
    yield vf, rq
    project_root = Path(__file__).resolve().parent.parent
    vf._override_path_for_tests(project_root / "awaiting_info.json")
    rq._override_path_for_tests(project_root / "review_queue.json")


def _seed_request(store):
    """Seed a single outstanding request and return its content_key."""
    store.register_request(
        content_key="ck1",
        channel="gmail",
        thread_id="thread-1",
        vendor_email="vendor@example.com",
        vendor_name="Acme",
        fields_requested=["invoice_number", "total"],
        sheet_id="sheet-1",
        row_number=2,
        project_code="ALPHA",
    )
    return "ck1"


def _run_orchestrator():
    """Resolve and call workflow_process_vendor_replies."""
    from server import mcp
    fn = mcp._tool_manager._tools["workflow_process_vendor_replies"].fn
    return json.loads(asyncio.run(fn(ProcessVendorRepliesInput())))


def _run_list_review_queue(**kwargs):
    from server import mcp
    fn = mcp._tool_manager._tools["workflow_list_review_queue"].fn
    return json.loads(asyncio.run(fn(ListReviewQueueInput(**kwargs))))


# --------------------------------------------------------------------------- #
# Confidence-branch tests
# --------------------------------------------------------------------------- #


def test_high_confidence_promotes_row(isolated_stores):
    vf_store, rq_store = isolated_stores
    _seed_request(vf_store)
    fake_reply = {
        "message": {"id": "msg-1", "payload": {}},
        "body": "Sure, INV-99 for $1234.56",
        "internal_ts_ms": 1714000000000,
        "internal_ts_iso": "2024-04-25T00:26:40+00:00",
    }
    with patch("llm.is_available", return_value=(True, "ok")), \
         patch("tools.project_invoices._find_gmail_reply",
               return_value=fake_reply), \
         patch("tools.project_invoices._parse_vendor_reply",
               return_value={"invoice_number": "INV-99", "total": 1234.56}), \
         patch("tools.project_invoices._apply_reply_update",
               return_value=True), \
         patch("tools.project_invoices._archive_reply_attachments_to_project",
               return_value=[]), \
         patch("tools.project_invoices._compose_acknowledgement",
               return_value={"subject": "ack", "plain": "thx", "html": "<p>thx</p>", "chat": "thx"}), \
         patch("tools.project_invoices._send_info_request_via_gmail",
               return_value=(True, None)):
        result = _run_orchestrator()

    assert result["status"] == "ok"
    assert result["results"]["replies_found"] == 1
    assert result["results"]["rows_promoted"] == 1
    assert result["results"]["rows_held_for_review"] == 0
    assert result["results"]["rows_low_confidence"] == 0
    # Awaiting_info entry should now be resolved (off the list_open)
    assert vf_store.get("ck1")["resolved_at"] is not None
    # Review queue is empty (high confidence doesn't queue)
    assert len(rq_store.list_open()) == 0
    # latest_reply_ts was advanced
    assert vf_store.get("ck1")["latest_reply_ts"] == "2024-04-25T00:26:40+00:00"


def test_medium_confidence_holds_for_review(isolated_stores):
    vf_store, rq_store = isolated_stores
    _seed_request(vf_store)
    fake_reply = {
        "message": {"id": "msg-1", "payload": {}},
        "body": "INV-99",
        "internal_ts_ms": 1714000000000,
        "internal_ts_iso": "2024-04-25T00:26:40+00:00",
    }
    # Only 1 of 2 fields parsed → medium confidence
    with patch("llm.is_available", return_value=(True, "ok")), \
         patch("tools.project_invoices._find_gmail_reply",
               return_value=fake_reply), \
         patch("tools.project_invoices._parse_vendor_reply",
               return_value={"invoice_number": "INV-99"}), \
         patch("tools.project_invoices._apply_reply_update",
               return_value=False), \
         patch("tools.project_invoices._archive_reply_attachments_to_project",
               return_value=[]):
        result = _run_orchestrator()

    assert result["results"]["replies_found"] == 1
    assert result["results"]["rows_held_for_review"] == 1
    assert result["results"]["rows_promoted"] == 0
    # Awaiting_info entry NOT resolved — still open
    assert vf_store.get("ck1")["resolved_at"] is None
    # Review queue has the entry
    queue = rq_store.list_open()
    assert len(queue) == 1
    assert queue[0]["content_key"] == "ck1"
    assert queue[0]["confidence"] == "medium"


def test_low_confidence_records_reminder_no_promotion(isolated_stores):
    vf_store, rq_store = isolated_stores
    _seed_request(vf_store)
    fake_reply = {
        "message": {"id": "msg-1", "payload": {}},
        "body": "Got it — I will send the invoice tomorrow morning.",
        "internal_ts_ms": 1714000000000,
        "internal_ts_iso": "2024-04-25T00:26:40+00:00",
    }
    # Even with parsed fields, deferral phrase forces low confidence
    with patch("llm.is_available", return_value=(True, "ok")), \
         patch("tools.project_invoices._find_gmail_reply",
               return_value=fake_reply), \
         patch("tools.project_invoices._parse_vendor_reply",
               return_value={"invoice_number": "INV-99", "total": 100}):
        result = _run_orchestrator()

    assert result["results"]["replies_found"] == 1
    assert result["results"]["rows_low_confidence"] == 1
    assert result["results"]["rows_promoted"] == 0
    assert result["results"]["rows_held_for_review"] == 0
    # No row update, no review queue entry
    assert vf_store.get("ck1")["resolved_at"] is None
    assert len(rq_store.list_open()) == 0
    # latest_reply_ts STILL advanced — we processed this message even if
    # we didn't act on its content. Stops next sweep from re-processing.
    assert vf_store.get("ck1")["latest_reply_ts"] == "2024-04-25T00:26:40+00:00"


# --------------------------------------------------------------------------- #
# Dedup test
# --------------------------------------------------------------------------- #


def test_dedup_skips_already_processed_message(isolated_stores):
    """If latest_reply_ts is set, _find_gmail_reply must be called with
    after_ts_iso — that's the dedup contract."""
    vf_store, rq_store = isolated_stores
    _seed_request(vf_store)
    vf_store.update_latest_reply_ts("ck1", "2024-04-25T00:26:40+00:00")

    captured = {}

    def fake_find(*, thread_id, sent_at_iso, after_ts_iso=None):
        captured["after_ts_iso"] = after_ts_iso
        captured["thread_id"] = thread_id
        return None  # "no new reply"

    with patch("llm.is_available", return_value=(True, "ok")), \
         patch("tools.project_invoices._find_gmail_reply", side_effect=fake_find):
        result = _run_orchestrator()

    assert captured["after_ts_iso"] == "2024-04-25T00:26:40+00:00"
    assert captured["thread_id"] == "thread-1"
    assert result["results"]["replies_found"] == 0
    assert result["results"]["rows_still_awaiting"] == 1


# --------------------------------------------------------------------------- #
# Attachment extraction tests
# --------------------------------------------------------------------------- #


def test_attachment_extraction_uploads_pdf_to_drive(isolated_stores):
    vf_store, rq_store = isolated_stores
    _seed_request(vf_store)
    fake_reply = {
        "message": {"id": "msg-1", "payload": {"parts": []}},
        "body": "Sure, INV-99 for $1234.56 — W-9 attached.",
        "internal_ts_ms": 1714000000000,
        "internal_ts_iso": "2024-04-25T00:26:40+00:00",
    }
    fake_attachments = [
        {"filename": "w9.pdf",
         "drive_link": "https://drive.google.com/file/d/abc",
         "mime_type": "application/pdf"}
    ]
    with patch("llm.is_available", return_value=(True, "ok")), \
         patch("tools.project_invoices._find_gmail_reply",
               return_value=fake_reply), \
         patch("tools.project_invoices._parse_vendor_reply",
               return_value={"invoice_number": "INV-99", "total": 1234.56}), \
         patch("tools.project_invoices._apply_reply_update",
               return_value=True), \
         patch("tools.project_invoices._archive_reply_attachments_to_project",
               return_value=fake_attachments) as mock_archive, \
         patch("tools.project_invoices._compose_acknowledgement",
               return_value={"subject": "ack", "plain": "thx", "html": "<p>thx</p>", "chat": "thx"}), \
         patch("tools.project_invoices._send_info_request_via_gmail",
               return_value=(True, None)):
        result = _run_orchestrator()

    # Archive helper was called with the right project + vendor
    mock_archive.assert_called_once()
    kwargs = mock_archive.call_args.kwargs
    assert kwargs["project_code"] == "ALPHA"
    assert kwargs["vendor_name"] == "Acme"
    # Result includes the saved attachment
    assert result["updates"][0]["attachments_saved"] == fake_attachments


def test_attachment_extraction_only_runs_on_high_or_medium(isolated_stores):
    """Low-confidence path should NOT call the archive helper — we don't
    want to clutter the project Drive with artifacts from deferral replies."""
    vf_store, rq_store = isolated_stores
    _seed_request(vf_store)
    fake_reply = {
        "message": {"id": "msg-1", "payload": {}},
        "body": "Got it — I will send tomorrow.",
        "internal_ts_ms": 1714000000000,
        "internal_ts_iso": "2024-04-25T00:26:40+00:00",
    }
    with patch("llm.is_available", return_value=(True, "ok")), \
         patch("tools.project_invoices._find_gmail_reply",
               return_value=fake_reply), \
         patch("tools.project_invoices._parse_vendor_reply",
               return_value={}), \
         patch("tools.project_invoices._archive_reply_attachments_to_project",
               return_value=[]) as mock_archive:
        _run_orchestrator()
    mock_archive.assert_not_called()


# --------------------------------------------------------------------------- #
# workflow_list_review_queue
# --------------------------------------------------------------------------- #


def test_list_review_queue_lists_entries(isolated_stores):
    vf_store, rq_store = isolated_stores
    rq_store.add_for_review(
        content_key="ck1", vendor_name="Acme", vendor_email=None,
        project_code="ALPHA", fields_requested=["a"],
        parsed_fields={"a": "x"}, confidence="medium",
        reply_excerpt="x",
    )
    result = _run_list_review_queue()
    assert result["total_open"] == 1
    assert result["entries"][0]["content_key"] == "ck1"


def test_list_review_queue_promote_clears_and_resolves(isolated_stores):
    vf_store, rq_store = isolated_stores
    _seed_request(vf_store)
    rq_store.add_for_review(
        content_key="ck1", vendor_name="Acme", vendor_email=None,
        project_code="ALPHA", fields_requested=["a"],
        parsed_fields={"a": "x"}, confidence="medium",
        reply_excerpt="x",
    )
    result = _run_list_review_queue(promote=["ck1"])
    assert "ck1" in result["acted"]["promoted"]
    assert result["total_open"] == 0
    # Underlying awaiting_info is now resolved
    assert vf_store.get("ck1")["resolved_at"] is not None


def test_list_review_queue_forget_clears_only(isolated_stores):
    vf_store, rq_store = isolated_stores
    _seed_request(vf_store)
    rq_store.add_for_review(
        content_key="ck1", vendor_name="Acme", vendor_email=None,
        project_code="ALPHA", fields_requested=["a"],
        parsed_fields={"a": "x"}, confidence="medium",
        reply_excerpt="x",
    )
    result = _run_list_review_queue(forget=["ck1"])
    assert "ck1" in result["acted"]["forgotten"]
    assert result["total_open"] == 0
    # Awaiting_info is NOT resolved by forget — reminders continue
    assert vf_store.get("ck1")["resolved_at"] is None


def test_list_review_queue_filters_by_project(isolated_stores):
    vf_store, rq_store = isolated_stores
    rq_store.add_for_review(
        content_key="k1", vendor_name="V", vendor_email=None,
        project_code="ALPHA", fields_requested=["a"],
        parsed_fields={"a": "x"}, confidence="medium",
        reply_excerpt="x",
    )
    rq_store.add_for_review(
        content_key="k2", vendor_name="V", vendor_email=None,
        project_code="BRAVO", fields_requested=["a"],
        parsed_fields={"a": "x"}, confidence="medium",
        reply_excerpt="x",
    )
    result = _run_list_review_queue(project_code="ALPHA")
    assert result["total_open"] == 1
    assert result["entries"][0]["content_key"] == "k1"
