# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
"""Tests for vendor_followups — outstanding-request store + reminder cadence."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

import vendor_followups as vf


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path: Path):
    vf._override_path_for_tests(tmp_path / "awaiting_info.json")
    yield
    vf._override_path_for_tests(
        Path(__file__).resolve().parent.parent / "awaiting_info.json",
    )


# --------------------------------------------------------------------------- #
# Register / get / list / forget
# --------------------------------------------------------------------------- #


def test_register_request_stores_chat_thread_name():
    """When a chat thread name is supplied, it's persisted on the record
    so reminders can thread back to the same conversation."""
    rec = vf.register_request(
        content_key="chat|acme|inv-2",
        thread_id="spaces/AAQA-space",
        channel="chat",
        vendor_email=None,
        vendor_name="Acme",
        fields_requested=["total"],
        sheet_id="sheet-1",
        row_number=3,
        chat_thread_name="spaces/AAQA-space/threads/T-12345",
    )
    assert rec["chat_thread_name"] == "spaces/AAQA-space/threads/T-12345"
    # Survives a re-load.
    assert vf.get("chat|acme|inv-2")["chat_thread_name"] == (
        "spaces/AAQA-space/threads/T-12345"
    )


def test_register_request_chat_thread_defaults_to_none():
    """Backwards-compat: callers that don't pass chat_thread_name still
    get a record (legacy field is stored as null)."""
    rec = vf.register_request(
        content_key="legacy|x",
        thread_id="t", channel="gmail",
        vendor_email="x@y.com", vendor_name="X",
        fields_requested=[], sheet_id="s", row_number=2,
    )
    assert rec["chat_thread_name"] is None


def test_register_request_creates_record():
    rec = vf.register_request(
        content_key="acme|inv-1|10000",
        thread_id="thread-abc",
        channel="gmail",
        vendor_email="billing@acme.io",
        vendor_name="Acme Inc",
        fields_requested=["invoice_number", "total"],
        sheet_id="sheet-1",
        row_number=2,
        project_code="ALPHA",
    )
    assert rec["content_key"] == "acme|inv-1|10000"
    assert rec["channel"] == "gmail"
    assert rec["reminder_count"] == 0
    assert rec["resolved_at"] is None
    assert rec["request_sent_at"]


def test_register_rejects_invalid_channel():
    with pytest.raises(ValueError):
        vf.register_request(
            content_key="x", thread_id="t", channel="sms",
            vendor_email=None, vendor_name=None, fields_requested=[],
            sheet_id="s", row_number=2,
        )


def test_register_rejects_empty_content_key():
    with pytest.raises(ValueError):
        vf.register_request(
            content_key="", thread_id="t", channel="gmail",
            vendor_email=None, vendor_name=None, fields_requested=[],
            sheet_id="s", row_number=2,
        )


def test_get_returns_none_for_unknown():
    assert vf.get("nope") is None
    assert vf.get("") is None


def test_list_open_excludes_resolved():
    vf.register_request(
        content_key="a", thread_id="t1", channel="gmail",
        vendor_email="x@y.com", vendor_name="A",
        fields_requested=["total"], sheet_id="s", row_number=2,
    )
    vf.register_request(
        content_key="b", thread_id="t2", channel="chat",
        vendor_email=None, vendor_name="B",
        fields_requested=["invoice_number"], sheet_id="s", row_number=3,
    )
    vf.mark_resolved("a")
    open_ = vf.list_open()
    assert {r["content_key"] for r in open_} == {"b"}


def test_list_open_filter_by_channel():
    vf.register_request(
        content_key="g", thread_id="tg", channel="gmail",
        vendor_email="x@y.com", vendor_name="G",
        fields_requested=["total"], sheet_id="s", row_number=2,
    )
    vf.register_request(
        content_key="c", thread_id="tc", channel="chat",
        vendor_email=None, vendor_name="C",
        fields_requested=["total"], sheet_id="s", row_number=3,
    )
    assert {r["content_key"] for r in vf.list_open(channel="gmail")} == {"g"}
    assert {r["content_key"] for r in vf.list_open(channel="chat")} == {"c"}


def test_forget_removes_entry():
    vf.register_request(
        content_key="x", thread_id="t", channel="gmail",
        vendor_email="x@y.com", vendor_name="X",
        fields_requested=[], sheet_id="s", row_number=2,
    )
    assert vf.forget("x") is True
    assert vf.get("x") is None
    assert vf.forget("x") is False


def test_clear_drops_all():
    vf.register_request(
        content_key="a", thread_id="t", channel="gmail",
        vendor_email="x@y.com", vendor_name="A",
        fields_requested=[], sheet_id="s", row_number=2,
    )
    vf.register_request(
        content_key="b", thread_id="t", channel="chat",
        vendor_email=None, vendor_name="B",
        fields_requested=[], sheet_id="s", row_number=3,
    )
    n = vf.clear()
    assert n == 2
    assert vf.list_open() == []


# --------------------------------------------------------------------------- #
# Reminder cadence — chat is immediate, email is 24h
# --------------------------------------------------------------------------- #


def test_due_for_reminder_chat_immediate(monkeypatch):
    vf.register_request(
        content_key="c", thread_id="spaces/AAQA", channel="chat",
        vendor_email=None, vendor_name="C",
        fields_requested=["total"], sheet_id="s", row_number=2,
    )
    # Chat: 0h wait, so it's due immediately.
    due = vf.due_for_reminder()
    assert any(r["content_key"] == "c" for r in due)


def test_due_for_reminder_email_waits_24h():
    """A fresh email request should NOT be due within the 24h window."""
    vf.register_request(
        content_key="g", thread_id="t", channel="gmail",
        vendor_email="x@y.com", vendor_name="G",
        fields_requested=["total"], sheet_id="s", row_number=2,
    )
    due = vf.due_for_reminder()
    assert not any(r["content_key"] == "g" for r in due)


def test_due_for_reminder_email_after_24h(tmp_path):
    """Backdate request_sent_at by 25 hours and confirm it shows up as due."""
    vf.register_request(
        content_key="g", thread_id="t", channel="gmail",
        vendor_email="x@y.com", vendor_name="G",
        fields_requested=["total"], sheet_id="s", row_number=2,
    )
    # Manually backdate the on-disk record.
    import json as _json
    import os as _os
    p = vf._STORE_PATH
    with open(p, "r") as f:
        data = _json.load(f)
    past = (
        _dt.datetime.now().astimezone() - _dt.timedelta(hours=25)
    ).isoformat(timespec="seconds")
    data["g"]["request_sent_at"] = past
    with open(p, "w") as f:
        _json.dump(data, f)
    due = vf.due_for_reminder()
    assert any(r["content_key"] == "g" for r in due)


def test_email_ladder_wait_hours_per_stage():
    """Verify the per-stage email reminder ladder: 24 / 48 / 48."""
    assert vf.EMAIL_REMINDER_HOURS_LADDER == (24, 48, 48)
    assert vf._email_wait_hours(0) == 24  # before 1st reminder
    assert vf._email_wait_hours(1) == 48  # before 2nd reminder
    assert vf._email_wait_hours(2) == 48  # before 3rd reminder
    # Beyond cap: returns a sentinel so we never fire.
    assert vf._email_wait_hours(3) > 1_000_000
    assert vf._email_wait_hours(99) > 1_000_000


def test_email_second_reminder_needs_48h_not_24h(tmp_path):
    """After the 1st reminder fires, the 2nd should NOT be due 25h later —
    the 2nd reminder needs 48h, not 24h."""
    import json as _json
    vf.register_request(
        content_key="g", thread_id="t", channel="gmail",
        vendor_email="x@y.com", vendor_name="G",
        fields_requested=["total"], sheet_id="s", row_number=2,
    )
    # Simulate the 1st reminder having gone out 25h ago.
    p = vf._STORE_PATH
    with open(p, "r") as f:
        data = _json.load(f)
    past = (
        _dt.datetime.now().astimezone() - _dt.timedelta(hours=25)
    ).isoformat(timespec="seconds")
    data["g"]["reminder_count"] = 1
    data["g"]["last_reminder_at"] = past
    with open(p, "w") as f:
        _json.dump(data, f)
    # 25h after 1st reminder is < 48h cadence — should NOT be due yet.
    due = vf.due_for_reminder()
    assert not any(r["content_key"] == "g" for r in due)


def test_email_second_reminder_due_after_48h(tmp_path):
    """48h after the 1st reminder, the 2nd should fire."""
    import json as _json
    vf.register_request(
        content_key="g", thread_id="t", channel="gmail",
        vendor_email="x@y.com", vendor_name="G",
        fields_requested=["total"], sheet_id="s", row_number=2,
    )
    p = vf._STORE_PATH
    with open(p, "r") as f:
        data = _json.load(f)
    past = (
        _dt.datetime.now().astimezone() - _dt.timedelta(hours=49)
    ).isoformat(timespec="seconds")
    data["g"]["reminder_count"] = 1
    data["g"]["last_reminder_at"] = past
    with open(p, "w") as f:
        _json.dump(data, f)
    due = vf.due_for_reminder()
    assert any(r["content_key"] == "g" for r in due)


def test_email_third_reminder_also_needs_48h(tmp_path):
    """3rd reminder also waits 48h — same cadence as the 2nd."""
    import json as _json
    vf.register_request(
        content_key="g", thread_id="t", channel="gmail",
        vendor_email="x@y.com", vendor_name="G",
        fields_requested=["total"], sheet_id="s", row_number=2,
    )
    p = vf._STORE_PATH
    with open(p, "r") as f:
        data = _json.load(f)
    # 2 reminders already sent, the 2nd was 47h ago — not yet due.
    just_short = (
        _dt.datetime.now().astimezone() - _dt.timedelta(hours=47)
    ).isoformat(timespec="seconds")
    data["g"]["reminder_count"] = 2
    data["g"]["last_reminder_at"] = just_short
    with open(p, "w") as f:
        _json.dump(data, f)
    assert not any(r["content_key"] == "g" for r in vf.due_for_reminder())

    # Now backdate to 49h — should be due.
    with open(p, "r") as f:
        data = _json.load(f)
    long_enough = (
        _dt.datetime.now().astimezone() - _dt.timedelta(hours=49)
    ).isoformat(timespec="seconds")
    data["g"]["last_reminder_at"] = long_enough
    with open(p, "w") as f:
        _json.dump(data, f)
    assert any(r["content_key"] == "g" for r in vf.due_for_reminder())


def test_record_reminder_increments_counter():
    vf.register_request(
        content_key="c", thread_id="t", channel="chat",
        vendor_email=None, vendor_name="C",
        fields_requested=[], sheet_id="s", row_number=2,
    )
    rec1 = vf.record_reminder("c")
    assert rec1["reminder_count"] == 1
    rec2 = vf.record_reminder("c")
    assert rec2["reminder_count"] == 2


def test_record_reminder_caps_at_max():
    vf.register_request(
        content_key="c", thread_id="t", channel="chat",
        vendor_email=None, vendor_name="C",
        fields_requested=[], sheet_id="s", row_number=2,
    )
    # Three reminders are allowed (MAX_REMINDERS = 3).
    assert vf.record_reminder("c")["reminder_count"] == 1
    assert vf.record_reminder("c")["reminder_count"] == 2
    assert vf.record_reminder("c")["reminder_count"] == 3
    # Fourth is refused.
    assert vf.record_reminder("c") is None


def test_due_for_reminder_excludes_capped():
    vf.register_request(
        content_key="c", thread_id="t", channel="chat",
        vendor_email=None, vendor_name="C",
        fields_requested=[], sheet_id="s", row_number=2,
    )
    # Hit the cap by recording MAX_REMINDERS reminders.
    for _ in range(vf.MAX_REMINDERS):
        vf.record_reminder("c")
    assert int(vf.get("c")["reminder_count"]) == vf.MAX_REMINDERS
    due = vf.due_for_reminder()
    assert not any(r["content_key"] == "c" for r in due)


def test_due_for_reminder_excludes_resolved():
    vf.register_request(
        content_key="c", thread_id="t", channel="chat",
        vendor_email=None, vendor_name="C",
        fields_requested=[], sheet_id="s", row_number=2,
    )
    vf.mark_resolved("c")
    assert vf.due_for_reminder() == []


def test_mark_resolved_idempotent():
    vf.register_request(
        content_key="c", thread_id="t", channel="chat",
        vendor_email=None, vendor_name="C",
        fields_requested=[], sheet_id="s", row_number=2,
    )
    assert vf.mark_resolved("c") is True
    # Second call is a no-op.
    assert vf.mark_resolved("c") is False


def test_register_resets_existing_record():
    """Re-registering same content_key resets the reminder counter."""
    vf.register_request(
        content_key="c", thread_id="t", channel="chat",
        vendor_email=None, vendor_name="C",
        fields_requested=[], sheet_id="s", row_number=2,
    )
    vf.record_reminder("c")
    vf.record_reminder("c")
    assert vf.get("c")["reminder_count"] == 2
    # Re-register
    vf.register_request(
        content_key="c", thread_id="t2", channel="gmail",
        vendor_email="x@y.com", vendor_name="C2",
        fields_requested=["total"], sheet_id="s", row_number=2,
    )
    assert vf.get("c")["reminder_count"] == 0
    assert vf.get("c")["channel"] == "gmail"


# --------------------------------------------------------------------------- #
# Status integration — the new AWAITING_INFO state
# --------------------------------------------------------------------------- #


def test_invoice_statuses_include_awaiting_info():
    import project_invoices as pi
    assert "AWAITING_INFO" in pi.INVOICE_STATUSES
    # Lifecycle order — AWAITING_INFO between OPEN and APPROVED.
    statuses = pi.INVOICE_STATUSES
    assert statuses.index("OPEN") < statuses.index("AWAITING_INFO")
    assert statuses.index("AWAITING_INFO") < statuses.index("APPROVED")


def test_invoice_status_validator_accepts_awaiting_info():
    import project_invoices as pi
    inv = pi.ExtractedInvoice.model_validate({"status": "awaiting_info"})
    assert inv.status == "AWAITING_INFO"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
