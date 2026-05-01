"""Tests for P1-5 — snooze, bulk actions, escalation trail.

Covers:
  - vendor_followups.snooze / unsnooze and the due_for_reminder filter
  - vendor_followups.append_event / get_trail
  - 5 new tools: workflow_snooze_awaiting_info,
    workflow_unsnooze_awaiting_info, workflow_bulk_resolve_awaiting_info,
    workflow_bulk_promote_review_queue, workflow_get_escalation_trail
  - register_request now seeds an ASK event automatically
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import vendor_followups as vf
import review_queue as rq
from tools.project_invoices import (
    SnoozeAwaitingInfoInput, UnsnoozeAwaitingInfoInput,
    BulkResolveAwaitingInfoInput, BulkPromoteReviewQueueInput,
    GetEscalationTrailInput,
)


@pytest.fixture
def isolated_stores(tmp_path):
    vf._override_path_for_tests(tmp_path / "awaiting_info.json")
    rq._override_path_for_tests(tmp_path / "review_queue.json")
    yield vf, rq
    project_root = Path(__file__).resolve().parent.parent
    vf._override_path_for_tests(project_root / "awaiting_info.json")
    rq._override_path_for_tests(project_root / "review_queue.json")


def _seed(store, ck="ck1", channel="gmail"):
    store.register_request(
        content_key=ck, channel=channel, thread_id="t1",
        vendor_email="v@x.com", vendor_name="V",
        fields_requested=["invoice_number"],
        sheet_id="s1", row_number=2, project_code="ALPHA",
    )


def _resolve(name):
    from server import mcp
    return mcp._tool_manager._tools[name].fn


def _run(name, params):
    return asyncio.run(_resolve(name)(params))


# --------------------------------------------------------------------------- #
# vendor_followups.py module-level changes
# --------------------------------------------------------------------------- #


def test_register_request_seeds_ask_event(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store)
    rec = vf_store.get("ck1")
    assert rec["events"] is not None and len(rec["events"]) == 1
    assert rec["events"][0]["action"] == "ASK"
    assert rec["events"][0]["channel"] == "gmail"
    assert rec["events"][0]["fields"] == ["invoice_number"]
    assert rec["snoozed_until"] is None


def test_snooze_sets_snoozed_until_and_logs_event(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store)
    until = "2030-01-01T00:00:00+00:00"
    assert vf_store.snooze("ck1", until, reason="vendor on holiday") is True
    rec = vf_store.get("ck1")
    assert rec["snoozed_until"] == until
    assert any(ev["action"] == "SNOOZED" for ev in rec["events"])


def test_snooze_rejects_bad_iso(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store)
    with pytest.raises(ValueError):
        vf_store.snooze("ck1", "not-a-date")


def test_snooze_unknown_key_returns_false(isolated_stores):
    vf_store, _ = isolated_stores
    assert vf_store.snooze("missing", "2030-01-01") is False


def test_unsnooze_clears_field_and_logs_event(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store)
    vf_store.snooze("ck1", "2030-01-01T00:00:00+00:00")
    assert vf_store.unsnooze("ck1") is True
    rec = vf_store.get("ck1")
    assert rec["snoozed_until"] is None
    assert any(ev["action"] == "UNSNOOZED" for ev in rec["events"])


def test_unsnooze_returns_false_when_not_snoozed(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store)  # never snoozed
    assert vf_store.unsnooze("ck1") is False


def test_due_for_reminder_skips_snoozed(isolated_stores):
    """An entry snoozed into the future should not appear in
    due_for_reminder() output."""
    vf_store, _ = isolated_stores
    _seed(vf_store, ck="ck1")
    # Backdate request_sent_at far enough that it would otherwise be due.
    data = vf_store._load()
    data["ck1"]["request_sent_at"] = "2020-01-01T00:00:00+00:00"
    vf_store._save(data)
    # Without snooze, it's due
    assert any(r["content_key"] == "ck1" for r in vf_store.due_for_reminder())
    # Snooze 100 years out
    vf_store.snooze("ck1", "2125-01-01T00:00:00+00:00")
    assert not any(r["content_key"] == "ck1" for r in vf_store.due_for_reminder())


def test_due_for_reminder_includes_when_snooze_passes(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store, ck="ck1")
    data = vf_store._load()
    data["ck1"]["request_sent_at"] = "2020-01-01T00:00:00+00:00"
    vf_store._save(data)
    # Snooze in the past — should be ignored, entry surfaces
    vf_store.snooze("ck1", "2020-06-01T00:00:00+00:00")
    assert any(r["content_key"] == "ck1" for r in vf_store.due_for_reminder())


def test_record_reminder_appends_event(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store)
    vf_store.record_reminder("ck1")
    rec = vf_store.get("ck1")
    reminder_events = [ev for ev in rec["events"] if ev["action"] == "REMINDER"]
    assert len(reminder_events) == 1
    assert reminder_events[0]["tier"] == 1


def test_mark_resolved_appends_event(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store)
    vf_store.mark_resolved("ck1")
    rec = vf_store.get("ck1")
    assert any(ev["action"] == "RESOLVED" for ev in rec["events"])


def test_get_trail_oldest_first(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store)
    vf_store.record_reminder("ck1")
    vf_store.snooze("ck1", "2030-01-01T00:00:00+00:00")
    trail = vf_store.get_trail("ck1")
    actions = [ev["action"] for ev in trail]
    assert actions == ["ASK", "REMINDER", "SNOOZED"]


def test_get_trail_returns_empty_for_unknown(isolated_stores):
    vf_store, _ = isolated_stores
    assert vf_store.get_trail("missing") == []


def test_append_event_adds_arbitrary_event(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store)
    assert vf_store.append_event("ck1", {"action": "CUSTOM", "note": "hi"}) is True
    rec = vf_store.get("ck1")
    custom = [ev for ev in rec["events"] if ev["action"] == "CUSTOM"]
    assert len(custom) == 1
    assert custom[0]["note"] == "hi"
    assert "ts" in custom[0]


# --------------------------------------------------------------------------- #
# Tool input validation
# --------------------------------------------------------------------------- #


def test_snooze_input_requires_content_key_and_until():
    with pytest.raises(ValidationError):
        SnoozeAwaitingInfoInput()
    with pytest.raises(ValidationError):
        SnoozeAwaitingInfoInput(content_key="ck1")
    SnoozeAwaitingInfoInput(content_key="ck1", until_date="2030-01-01")


def test_unsnooze_input_requires_content_key():
    with pytest.raises(ValidationError):
        UnsnoozeAwaitingInfoInput()
    UnsnoozeAwaitingInfoInput(content_key="ck1")


def test_bulk_resolve_requires_non_empty_list():
    with pytest.raises(ValidationError):
        BulkResolveAwaitingInfoInput(content_keys=[])
    BulkResolveAwaitingInfoInput(content_keys=["ck1"])


def test_bulk_promote_requires_non_empty_list():
    with pytest.raises(ValidationError):
        BulkPromoteReviewQueueInput(content_keys=[])
    BulkPromoteReviewQueueInput(content_keys=["ck1"])


def test_get_trail_format_default_json():
    m = GetEscalationTrailInput(content_key="ck1")
    assert m.format == "json"


# --------------------------------------------------------------------------- #
# Tool happy paths
# --------------------------------------------------------------------------- #


def test_workflow_snooze_then_unsnooze(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store)
    out = _run("workflow_snooze_awaiting_info", SnoozeAwaitingInfoInput(
        content_key="ck1", until_date="2030-01-01T00:00:00+00:00",
        reason="vendor on holiday",
    ))
    payload = json.loads(out)
    assert payload["snoozed"] is True
    assert payload["snoozed_until"] == "2030-01-01T00:00:00+00:00"

    out2 = _run("workflow_unsnooze_awaiting_info",
                UnsnoozeAwaitingInfoInput(content_key="ck1"))
    payload2 = json.loads(out2)
    assert payload2["unsnoozed"] is True
    assert vf_store.get("ck1")["snoozed_until"] is None


def test_workflow_snooze_bad_iso_returns_error(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store)
    out = _run("workflow_snooze_awaiting_info", SnoozeAwaitingInfoInput(
        content_key="ck1", until_date="not-an-iso",
    ))
    assert "error" in out.lower() or "iso" in out.lower()


def test_workflow_bulk_resolve(isolated_stores):
    vf_store, _ = isolated_stores
    for ck in ("a", "b", "c"):
        _seed(vf_store, ck=ck)
    out = _run("workflow_bulk_resolve_awaiting_info",
               BulkResolveAwaitingInfoInput(
                   content_keys=["a", "b", "c", "missing"],
                   reason="annual cleanup",
               ))
    payload = json.loads(out)
    assert payload["total"] == 4
    assert payload["resolved"] == 3
    # Missing key returns resolved=False
    by_key = {r["content_key"]: r["resolved"] for r in payload["results"]}
    assert by_key == {"a": True, "b": True, "c": True, "missing": False}
    # Reason was logged on each that resolved
    for ck in ("a", "b", "c"):
        events = vf_store.get_trail(ck)
        actions = [ev["action"] for ev in events]
        assert "RESOLVED" in actions
        assert "RESOLVED_REASON" in actions


def test_workflow_bulk_promote_review_queue(isolated_stores):
    vf_store, rq_store = isolated_stores
    for ck in ("a", "b"):
        _seed(vf_store, ck=ck)
        rq_store.add_for_review(
            content_key=ck, vendor_name="V", vendor_email=None,
            project_code="ALPHA", fields_requested=["x"],
            parsed_fields={"x": "1"}, confidence="medium",
            reply_excerpt="x",
        )
    out = _run("workflow_bulk_promote_review_queue",
               BulkPromoteReviewQueueInput(content_keys=["a", "b"]))
    payload = json.loads(out)
    assert payload["promoted"] == 2
    # Both review-queue entries cleared
    assert len(rq_store.list_open()) == 0
    # Both awaiting_info entries resolved + got REVIEW_QUEUE_PROMOTED event
    for ck in ("a", "b"):
        rec = vf_store.get(ck)
        assert rec["resolved_at"] is not None
        actions = [ev["action"] for ev in rec["events"]]
        assert "REVIEW_QUEUE_PROMOTED" in actions


def test_workflow_get_escalation_trail_json(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store)
    vf_store.record_reminder("ck1")
    out = _run("workflow_get_escalation_trail",
               GetEscalationTrailInput(content_key="ck1"))
    payload = json.loads(out)
    assert payload["content_key"] == "ck1"
    assert len(payload["events"]) == 2  # ASK + REMINDER


def test_workflow_get_escalation_trail_text(isolated_stores):
    vf_store, _ = isolated_stores
    _seed(vf_store)
    vf_store.record_reminder("ck1")
    vf_store.snooze("ck1", "2030-05-15T00:00:00+00:00")
    out = _run("workflow_get_escalation_trail",
               GetEscalationTrailInput(content_key="ck1", format="text"))
    # Compact one-line view: " · " separated events
    assert "ASK" in out
    assert "R1" in out
    assert "SNOOZED→2030-05-15" in out
    assert " · " in out


def test_workflow_get_escalation_trail_unknown_text(isolated_stores):
    out = _run("workflow_get_escalation_trail",
               GetEscalationTrailInput(content_key="missing", format="text"))
    assert "no events" in out.lower() or "missing" in out


def test_all_p15_tools_registered():
    from server import mcp
    expected = {
        "workflow_snooze_awaiting_info",
        "workflow_unsnooze_awaiting_info",
        "workflow_bulk_resolve_awaiting_info",
        "workflow_bulk_promote_review_queue",
        "workflow_get_escalation_trail",
    }
    assert expected.issubset(set(mcp._tool_manager._tools))
