# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for the 3 P4 workflow logic functions."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

import crm_events as ce
import p4_workflows as p4


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    ce._override_path_for_tests(tmp_path / "ev.json")
    yield
    ce._override_path_for_tests(
        Path(__file__).resolve().parent.parent / "crm_events.json",
    )


# --------------------------------------------------------------------------- #
# #3 VIP escalation
# --------------------------------------------------------------------------- #


def test_vip_escalation_fires_for_vip():
    msgs = [
        {"from_email": "vip@bigcustomer.com", "subject": "Renewal",
         "snippet": "Hi", "thread_id": "t1"},
        {"from_email": "random@x.com", "subject": "Sale", "snippet": "x",
         "thread_id": "t2"},
    ]
    alerts = p4.find_vip_escalations(msgs, {"vip@bigcustomer.com"})
    assert len(alerts) == 1
    assert alerts[0]["email"] == "vip@bigcustomer.com"


def test_vip_escalation_dedupes_within_window():
    """Second call within window should produce no new alerts."""
    msgs = [{"from_email": "vip@x.com", "subject": "Hi", "thread_id": "t1"}]
    a1 = p4.find_vip_escalations(msgs, {"vip@x.com"})
    a2 = p4.find_vip_escalations(msgs, {"vip@x.com"})
    assert len(a1) == 1
    assert len(a2) == 0  # deduped


def test_vip_escalation_records_event_in_crm():
    msgs = [{"from_email": "vip@x.com", "subject": "Hi", "thread_id": "t1"}]
    p4.find_vip_escalations(msgs, {"vip@x.com"})
    last = ce.last_event("vip@x.com", kind="vip_alert")
    assert last is not None


# --------------------------------------------------------------------------- #
# #27 Calibrator
# --------------------------------------------------------------------------- #


def test_substantive_with_long_body():
    body = ("Following up on our discussion last week — I think there are three "
            "things we should align on before Friday.")
    assert p4.is_substantive_message(body)


def test_not_substantive_with_thanks():
    assert not p4.is_substantive_message("thanks!")
    assert not p4.is_substantive_message("Got it")


def test_substantive_short_with_question_mark():
    assert p4.is_substantive_message("Friday or Monday?")


def test_record_message_event_marks_substantive():
    rec = p4.record_message_event(
        "alice@x.com",
        "Following up on the renewal with three concrete asks below. "
        "Can we lock in by Friday?",
    )
    assert rec["data"]["substantive"] is True
    assert rec["kind"] == "email_substantive"


def test_record_message_event_marks_thin():
    rec = p4.record_message_event("alice@x.com", "thanks")
    assert rec["data"]["substantive"] is False
    assert rec["kind"] == "email_received"


def test_calibrated_staleness_distinguishes_substantive_from_acks():
    today = _dt.datetime(2026, 4, 28, tzinfo=_dt.timezone.utc)
    # Substantive 90 days ago, thin ack 5 days ago
    ce.append("alice@x.com", "email_substantive", "real talk",
              ts=(today - _dt.timedelta(days=90)).isoformat())
    ce.append("alice@x.com", "email_received", "thanks",
              ts=(today - _dt.timedelta(days=5)).isoformat())

    out = p4.calibrated_staleness("alice@x.com", today=today)
    assert out["days_since_substantive"] == 90
    assert out["days_since_any"] == 5  # thin ack is recent
    # Calibration insight: contact "feels recent" but no real conversation in 90 days


# --------------------------------------------------------------------------- #
# #41 Vendor onboarding
# --------------------------------------------------------------------------- #


def test_is_new_vendor_true_when_no_history():
    assert p4.is_new_vendor("brand_new@vendor.com") is True


def test_is_new_vendor_false_after_invoice_event():
    ce.append("known@vendor.com", "vendor_invoice", "INV-1")
    assert p4.is_new_vendor("known@vendor.com") is False


def test_build_onboarding_plan_default_checklist():
    today = _dt.date(2026, 4, 28)
    plan = p4.build_onboarding_plan(
        "vendor@x.com", "Vendor Co.",
        invoice_id="INV-1", today=today,
    )
    assert len(plan.checklist) == 5
    titles = [c["title"] for c in plan.checklist]
    assert any("W-9" in t for t in titles)
    assert any("COI" in t for t in titles)
    assert any("NDA" in t for t in titles)


def test_build_onboarding_plan_due_dates_stagger():
    today = _dt.date(2026, 4, 28)
    plan = p4.build_onboarding_plan("v@x.com", "V", today=today, base_due_days=7)
    dues = [c["due_at"] for c in plan.checklist]
    # First item due in 7 days, last in 5*7 = 35 days
    assert dues[0] == "2026-05-05"
    assert dues[-1] == "2026-06-02"


def test_record_onboarding_kicked_off_appends_event():
    plan = p4.build_onboarding_plan("vendor@x.com", "Vendor Co.")
    p4.record_onboarding_kicked_off(plan)
    last = ce.last_event("vendor@x.com", kind="vendor_onboarded")
    assert last is not None
