# © 2026 CoAssisted Workspace. Licensed under MIT.
"""AR collections kill-switch — Finnn 2026-05-01 Part F.

Operator wants AR collections handled by humans, not the bot. The
3-mode gate (send / draft / disabled) lives in config.ar.collections_mode
+ collections_mode_per_tier.

Tests cover:
  - resolve_collections_mode reads config + per-tier override
  - send_collection_reminder routes by mode
  - Default config: every tier 'draft' except escalation_to_legal 'disabled'
  - mode_override param wins over config
  - draft_queue post-approval hook advances state on actual send
"""

from __future__ import annotations

import datetime as _dt
import sys
import types

import pytest

import ar_invoicing
import ar_send
import draft_queue
import labor_ingest
import project_registry


@pytest.fixture
def fresh_stores(tmp_path, monkeypatch):
    """Fresh ar_invoices, project registry, draft queue, and config block."""
    monkeypatch.setattr(ar_invoicing, "_INVOICES_PATH", tmp_path / "ar.json")
    monkeypatch.setattr(project_registry, "_REGISTRY_PATH", tmp_path / "p.json")
    monkeypatch.setattr(draft_queue, "_QUEUE_PATH", tmp_path / "dq.json")
    yield tmp_path


def _stub_config(monkeypatch, *, ar_block):
    """Inject a fake config module with a controllable `ar` block."""
    fake_cfg = types.SimpleNamespace(
        get=lambda k, d=None: ar_block if k == "ar" else d,
    )
    monkeypatch.setitem(sys.modules, "config", fake_cfg)


def _seed_invoice() -> str:
    """Drop an invoice in the store + return its id."""
    project_registry.register(
        "GE1",
        name="Google - Golden Eagle 1",
        client="Google, LLC",
        billing_terms="Net-15",
        customer_email="ap@google.com",
    )
    rows = [labor_ingest.LaborRow(
        job_number="", job_description="",
        work_date=_dt.date(2026, 4, 5),
        employee_name="", employee_number="", post_description="Day",
        shift_start="", shift_end="",
        hours=8, overtime_hours=0, doubletime_hours=0,
        dollars=200, holiday_dollars=0, overtime_dollars=0,
        doubletime_dollars=0,
        billable_hours=8, billable_dollars=400,
    )]
    inv = ar_invoicing.generate_invoice_from_labor(
        "GE1",
        period_start=_dt.date(2026, 4, 1),
        period_end=_dt.date(2026, 4, 30),
        labor_rows=rows,
        invoice_date=_dt.date(2026, 4, 1),
    )
    ar_invoicing.persist(inv)
    ar_invoicing.mark_sent(inv.invoice_id, sent_date=_dt.date(2026, 4, 2))
    return inv.invoice_id


# -----------------------------------------------------------------------------
# resolve_collections_mode
# -----------------------------------------------------------------------------

def test_resolve_falls_back_to_draft_when_config_missing(monkeypatch):
    """Missing config block → safe default of 'draft'."""
    fake_cfg = types.SimpleNamespace(get=lambda k, d=None: d)
    monkeypatch.setitem(sys.modules, "config", fake_cfg)
    assert ar_send.resolve_collections_mode("courtesy_reminder") == "draft"


def test_resolve_uses_base_mode(monkeypatch):
    _stub_config(monkeypatch, ar_block={"collections_mode": "send"})
    assert ar_send.resolve_collections_mode("first_followup") == "send"


def test_resolve_per_tier_override_wins(monkeypatch):
    """per-tier value overrides the base mode for that tier only."""
    _stub_config(monkeypatch, ar_block={
        "collections_mode": "send",
        "collections_mode_per_tier": {"escalation_to_legal": "disabled"},
    })
    assert ar_send.resolve_collections_mode("escalation_to_legal") == "disabled"
    # Other tiers fall back to the base.
    assert ar_send.resolve_collections_mode("courtesy_reminder") == "send"


def test_resolve_unknown_mode_falls_back_to_draft(monkeypatch):
    """Garbage mode strings fail safe."""
    _stub_config(monkeypatch, ar_block={"collections_mode": "rocket-ship"})
    assert ar_send.resolve_collections_mode("first_followup") == "draft"


# -----------------------------------------------------------------------------
# send_collection_reminder by mode
# -----------------------------------------------------------------------------

def test_disabled_mode_returns_skipped(fresh_stores, monkeypatch):
    """mode=disabled: no send, no draft, no state change."""
    _stub_config(monkeypatch, ar_block={"collections_mode": "disabled"})
    iid = _seed_invoice()
    result = ar_send.send_collection_reminder(
        iid, tier="courtesy_reminder", as_of=_dt.date(2026, 4, 22),
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "collections_disabled"
    assert result["sent"] is False
    assert result.get("drafted") is False
    # Invoice has no collection events.
    inv = ar_invoicing.get(iid)
    assert inv.collection_events == []


def test_draft_mode_enqueues_no_send(fresh_stores, monkeypatch):
    """mode=draft: queue in draft_queue, no Gmail send fired."""
    _stub_config(monkeypatch, ar_block={"collections_mode": "draft"})
    sent_calls = []
    monkeypatch.setattr(
        ar_send, "_send_email_with_attachment",
        lambda **kw: sent_calls.append(kw) or {"sent": True, "message_id": "x"},
    )
    iid = _seed_invoice()
    result = ar_send.send_collection_reminder(
        iid, tier="first_followup", as_of=_dt.date(2026, 5, 1),
    )
    assert result["status"] == "drafted"
    assert result["drafted"] is True
    assert result["sent"] is False
    assert result["draft_id"]
    assert sent_calls == []  # No Gmail send.
    # Draft is in the queue.
    rec = draft_queue.get(result["draft_id"])
    assert rec is not None
    assert rec["kind"] == "ar_collection"
    assert rec["meta"]["invoice_id"] == iid
    assert rec["meta"]["tier"] == "first_followup"
    # No collection event yet — fires on approval+send.
    inv = ar_invoicing.get(iid)
    assert inv.collection_events == []


def test_send_mode_immediate(fresh_stores, monkeypatch):
    """mode=send: immediate Gmail send, collection event recorded inline."""
    _stub_config(monkeypatch, ar_block={"collections_mode": "send"})
    monkeypatch.setattr(
        ar_send, "_send_email_with_attachment",
        lambda **kw: {"sent": True, "message_id": "msg-123"},
    )
    iid = _seed_invoice()
    result = ar_send.send_collection_reminder(
        iid, tier="courtesy_reminder", as_of=_dt.date(2026, 4, 22),
    )
    assert result["status"] == "sent"
    assert result["sent"] is True
    assert result["mode"] == "send"
    inv = ar_invoicing.get(iid)
    assert len(inv.collection_events) == 1
    assert inv.collection_events[0]["type"] == "courtesy_reminder"


def test_per_tier_disabled_overrides_base_send(fresh_stores, monkeypatch):
    """Default config: every tier 'draft' except escalation_to_legal 'disabled'.

    Verifies the operator-safety property — even when the base is send,
    per-tier override of 'disabled' on escalation_to_legal stops it.
    """
    _stub_config(monkeypatch, ar_block={
        "collections_mode": "send",
        "collections_mode_per_tier": {"escalation_to_legal": "disabled"},
    })
    iid = _seed_invoice()
    result = ar_send.send_collection_reminder(
        iid, tier="escalation_to_legal", as_of=_dt.date(2026, 4, 22),
    )
    assert result["status"] == "skipped"
    assert result["reason"] == "collections_disabled"


def test_mode_override_param_wins(fresh_stores, monkeypatch):
    """mode_override on the call beats config."""
    _stub_config(monkeypatch, ar_block={"collections_mode": "send"})
    iid = _seed_invoice()
    result = ar_send.send_collection_reminder(
        iid, tier="courtesy_reminder",
        as_of=_dt.date(2026, 4, 22),
        mode_override="disabled",
    )
    assert result["status"] == "skipped"


def test_invalid_mode_override_falls_back_to_config(fresh_stores, monkeypatch):
    """Unknown mode_override values are ignored, config wins."""
    _stub_config(monkeypatch, ar_block={"collections_mode": "disabled"})
    iid = _seed_invoice()
    result = ar_send.send_collection_reminder(
        iid, tier="courtesy_reminder",
        as_of=_dt.date(2026, 4, 22),
        mode_override="nonsense",
    )
    # Config says disabled; bad override doesn't promote it.
    assert result["status"] == "skipped"


# -----------------------------------------------------------------------------
# Post-approval hook
# -----------------------------------------------------------------------------

def test_post_approval_hook_fires_add_collection_event(fresh_stores, monkeypatch):
    """Approving an ar_collection draft fires add_collection_event."""
    iid = _seed_invoice()
    # Build a fake draft record matching what ar_send would have enqueued.
    fake_rec = {
        "id": "draft-abc",
        "kind": "ar_collection",
        "meta": {
            "invoice_id": iid,
            "tier": "first_followup",
            "invoice_number": "GE1-2026-04",
            "customer_name": "Google, LLC",
        },
    }
    # Hook is registered at ar_send module import; fire it directly.
    draft_queue.fire_post_approval_hooks(fake_rec)
    # State advanced.
    inv = ar_invoicing.get(iid)
    assert len(inv.collection_events) == 1
    assert inv.collection_events[0]["type"] == "first_followup"


def test_post_approval_hook_ignores_non_ar_drafts(fresh_stores):
    """Drafts of other kinds don't fire the AR hook."""
    iid = _seed_invoice()
    fake_rec = {
        "id": "draft-xyz",
        "kind": "auto_reply_inbound",
        "meta": {"invoice_id": iid, "tier": "first_followup"},
    }
    draft_queue.fire_post_approval_hooks(fake_rec)
    inv = ar_invoicing.get(iid)
    assert inv.collection_events == []


def test_post_approval_hook_handles_missing_meta(fresh_stores):
    """A draft without invoice_id/tier in meta is a no-op (no crash)."""
    fake_rec = {
        "id": "draft-broken",
        "kind": "ar_collection",
        "meta": {},
    }
    # Should not raise.
    draft_queue.fire_post_approval_hooks(fake_rec)


def test_register_post_approval_hook_dedups():
    """Re-registering the same callback is idempotent."""
    calls = []

    def cb(rec):
        calls.append(rec)

    draft_queue.register_post_approval_hook("test_kind_dedup", cb)
    draft_queue.register_post_approval_hook("test_kind_dedup", cb)
    draft_queue.register_post_approval_hook("test_kind_dedup", cb)
    draft_queue.fire_post_approval_hooks({"kind": "test_kind_dedup"})
    assert len(calls) == 1


def test_register_post_approval_hook_rejects_bad_input():
    with pytest.raises(ValueError):
        draft_queue.register_post_approval_hook("", lambda r: None)
    with pytest.raises(TypeError):
        draft_queue.register_post_approval_hook("k", "not callable")
