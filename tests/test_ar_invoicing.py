# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Unit tests for ar_invoicing.py — AR-9 invoicing + aging + collections."""

from __future__ import annotations

import datetime as _dt

import pytest

import ar_invoicing
import labor_ingest
import project_registry


@pytest.fixture
def fresh_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(ar_invoicing, "_INVOICES_PATH", tmp_path / "inv.json")
    monkeypatch.setattr(project_registry, "_REGISTRY_PATH", tmp_path / "p.json")
    yield tmp_path


def _make_row(post: str, hours: float, billable: float, when: _dt.date):
    return labor_ingest.LaborRow(
        job_number="", job_description="", work_date=when,
        employee_name="", employee_number="", post_description=post,
        shift_start="", shift_end="",
        hours=hours, overtime_hours=0, doubletime_hours=0,
        dollars=hours * 25, holiday_dollars=0, overtime_dollars=0,
        doubletime_dollars=0,
        billable_hours=hours, billable_dollars=billable,
    )


# -----------------------------------------------------------------------------
# terms parsing
# -----------------------------------------------------------------------------

def test_terms_to_days_net15():
    assert ar_invoicing._terms_to_days("Net-15") == 15
    assert ar_invoicing._terms_to_days("net 15") == 15
    assert ar_invoicing._terms_to_days("NET-15") == 15


def test_terms_to_days_net30():
    assert ar_invoicing._terms_to_days("Net-30") == 30


def test_terms_to_days_due_on_receipt():
    assert ar_invoicing._terms_to_days("Due on Receipt") == 0


def test_terms_to_days_unknown_falls_back_to_15():
    assert ar_invoicing._terms_to_days("whatever") == 15
    assert ar_invoicing._terms_to_days("") == 15


# -----------------------------------------------------------------------------
# Invoice generation
# -----------------------------------------------------------------------------

def test_generate_invoice_groups_by_post(fresh_stores):
    project_registry.register(
        "GE1", name="Google - Golden Eagle 1",
        client="Google, LLC", billing_terms="Net-30",
        customer_email="ap@google.com",
    )
    rows = [
        _make_row("Day", 8, 400, _dt.date(2026, 4, 1)),
        _make_row("Day", 8, 400, _dt.date(2026, 4, 2)),
        _make_row("Night", 8, 480, _dt.date(2026, 4, 1)),
    ]
    inv = ar_invoicing.generate_invoice_from_labor(
        "GE1",
        period_start=_dt.date(2026, 4, 1),
        period_end=_dt.date(2026, 4, 30),
        labor_rows=rows,
    )
    assert len(inv.lines) == 2
    by_desc = {ln.description: ln for ln in inv.lines}
    assert by_desc["Day"].quantity == 16
    assert by_desc["Day"].amount == 800
    assert by_desc["Night"].quantity == 8
    assert by_desc["Night"].amount == 480


def test_generate_invoice_total_equals_subtotal(fresh_stores):
    project_registry.register("GE1", name="Google - Golden Eagle 1")
    rows = [_make_row("Day", 8, 400, _dt.date(2026, 4, 5))]
    inv = ar_invoicing.generate_invoice_from_labor(
        "GE1",
        period_start=_dt.date(2026, 4, 1),
        period_end=_dt.date(2026, 4, 30),
        labor_rows=rows,
    )
    assert inv.subtotal == 400
    assert inv.total == 400  # No tax handling


def test_generate_invoice_filters_to_period(fresh_stores):
    project_registry.register("GE1", name="Google - Golden Eagle 1")
    rows = [
        _make_row("Day", 8, 400, _dt.date(2026, 4, 5)),
        _make_row("Day", 8, 400, _dt.date(2026, 5, 1)),  # outside period
    ]
    inv = ar_invoicing.generate_invoice_from_labor(
        "GE1",
        period_start=_dt.date(2026, 4, 1),
        period_end=_dt.date(2026, 4, 30),
        labor_rows=rows,
    )
    # Only April shifts count
    assert inv.subtotal == 400


def test_generate_invoice_sets_due_date_from_terms(fresh_stores):
    project_registry.register(
        "GE1", name="Google - Golden Eagle 1", billing_terms="Net-15",
    )
    rows = [_make_row("Day", 8, 400, _dt.date(2026, 4, 5))]
    inv = ar_invoicing.generate_invoice_from_labor(
        "GE1",
        period_start=_dt.date(2026, 4, 1),
        period_end=_dt.date(2026, 4, 30),
        labor_rows=rows,
        invoice_date=_dt.date(2026, 5, 1),
    )
    assert inv.due_date == _dt.date(2026, 5, 16)  # 15 days after May 1


def test_generate_invoice_unknown_project_raises(fresh_stores):
    rows = [_make_row("Day", 8, 400, _dt.date(2026, 4, 5))]
    with pytest.raises(ValueError, match="Unknown project"):
        ar_invoicing.generate_invoice_from_labor(
            "GHOST",
            period_start=_dt.date(2026, 4, 1),
            period_end=_dt.date(2026, 4, 30),
            labor_rows=rows,
        )


def test_generate_invoice_weekly_cadence_uses_date_in_number(fresh_stores):
    project_registry.register(
        "NYP", name="New York Project",
        billing_origin_state="NY", billing_cadence="weekly",
    )
    rows = [_make_row("Day", 8, 400, _dt.date(2026, 4, 5))]
    inv = ar_invoicing.generate_invoice_from_labor(
        "NYP",
        period_start=_dt.date(2026, 4, 1),
        period_end=_dt.date(2026, 4, 7),
        labor_rows=rows,
    )
    # Weekly invoices use full ISO date in the number, not just YYYY-MM
    assert inv.invoice_number == "NYP-2026-04-01"


# -----------------------------------------------------------------------------
# Persistence + status transitions
# -----------------------------------------------------------------------------

def test_persist_and_get_round_trip(fresh_stores):
    project_registry.register("GE1", name="Google - Golden Eagle 1")
    rows = [_make_row("Day", 8, 400, _dt.date(2026, 4, 5))]
    inv = ar_invoicing.generate_invoice_from_labor(
        "GE1",
        period_start=_dt.date(2026, 4, 1),
        period_end=_dt.date(2026, 4, 30),
        labor_rows=rows,
    )
    ar_invoicing.persist(inv)
    fetched = ar_invoicing.get(inv.invoice_id)
    assert fetched is not None
    assert fetched.invoice_number == inv.invoice_number
    assert fetched.total == inv.total
    assert len(fetched.lines) == 1


def test_mark_sent_updates_status_and_date(fresh_stores):
    project_registry.register("GE1", name="GE1")
    rows = [_make_row("Day", 8, 400, _dt.date(2026, 4, 5))]
    inv = ar_invoicing.generate_invoice_from_labor(
        "GE1",
        period_start=_dt.date(2026, 4, 1),
        period_end=_dt.date(2026, 4, 30),
        labor_rows=rows,
    )
    ar_invoicing.persist(inv)
    assert ar_invoicing.mark_sent(inv.invoice_id, sent_date=_dt.date(2026, 5, 2))
    fetched = ar_invoicing.get(inv.invoice_id)
    assert fetched.status == "sent"
    assert fetched.sent_date == _dt.date(2026, 5, 2)


def test_apply_full_payment_marks_paid(fresh_stores):
    project_registry.register("GE1", name="GE1")
    rows = [_make_row("Day", 8, 400, _dt.date(2026, 4, 5))]
    inv = ar_invoicing.generate_invoice_from_labor(
        "GE1",
        period_start=_dt.date(2026, 4, 1),
        period_end=_dt.date(2026, 4, 30),
        labor_rows=rows,
    )
    ar_invoicing.persist(inv)
    ar_invoicing.mark_sent(inv.invoice_id)
    assert ar_invoicing.apply_payment(inv.invoice_id, 400, paid_date=_dt.date(2026, 5, 20))
    fetched = ar_invoicing.get(inv.invoice_id)
    assert fetched.status == "paid"
    assert fetched.paid_amount == 400.0


def test_apply_partial_payment_marks_partial(fresh_stores):
    project_registry.register("GE1", name="GE1")
    rows = [_make_row("Day", 8, 1000, _dt.date(2026, 4, 5))]
    inv = ar_invoicing.generate_invoice_from_labor(
        "GE1",
        period_start=_dt.date(2026, 4, 1),
        period_end=_dt.date(2026, 4, 30),
        labor_rows=rows,
    )
    ar_invoicing.persist(inv)
    ar_invoicing.mark_sent(inv.invoice_id)
    ar_invoicing.apply_payment(inv.invoice_id, 400)
    fetched = ar_invoicing.get(inv.invoice_id)
    assert fetched.status == "partial"
    assert fetched.paid_amount == 400.0


# -----------------------------------------------------------------------------
# Aging
# -----------------------------------------------------------------------------

def _setup_overdue_invoice(due_date: _dt.date) -> str:
    project_registry.register("GE1", name="GE1", billing_terms="Net-15")
    rows = [_make_row("Day", 8, 400, _dt.date(2026, 4, 5))]
    inv = ar_invoicing.generate_invoice_from_labor(
        "GE1",
        period_start=_dt.date(2026, 4, 1),
        period_end=_dt.date(2026, 4, 30),
        labor_rows=rows,
        invoice_date=due_date - _dt.timedelta(days=15),
    )
    ar_invoicing.persist(inv)
    ar_invoicing.mark_sent(inv.invoice_id)
    return inv.invoice_id


def test_compute_aging_buckets_correctly(fresh_stores):
    """An invoice 20 days past due → bucket 16-30."""
    _setup_overdue_invoice(due_date=_dt.date(2026, 5, 1))
    aging = ar_invoicing.compute_aging(as_of=_dt.date(2026, 5, 21))
    assert len(aging) == 1
    assert aging[0].days_past_due == 20
    assert aging[0].bucket == "16-30"
    assert aging[0].is_overdue is True


def test_compute_aging_skips_paid_invoices(fresh_stores):
    iid = _setup_overdue_invoice(due_date=_dt.date(2026, 5, 1))
    ar_invoicing.apply_payment(iid, 400)
    aging = ar_invoicing.compute_aging(as_of=_dt.date(2026, 5, 21))
    assert aging == []


def test_aging_summary_groups_by_bucket(fresh_stores):
    """Multiple invoices in different buckets → totals per bucket."""
    project_registry.register("GE1", name="GE1", billing_terms="Net-15")
    # Invoice 1: 5 days past due → 1-15
    rows1 = [_make_row("Day", 8, 100, _dt.date(2026, 4, 5))]
    inv1 = ar_invoicing.generate_invoice_from_labor(
        "GE1",
        period_start=_dt.date(2026, 4, 1),
        period_end=_dt.date(2026, 4, 30),
        labor_rows=rows1,
        invoice_date=_dt.date(2026, 4, 30),
    )
    ar_invoicing.persist(inv1)
    ar_invoicing.mark_sent(inv1.invoice_id)
    # Invoice 2: 50 days past due → 31-60
    rows2 = [_make_row("Night", 8, 200, _dt.date(2026, 3, 1))]
    inv2 = ar_invoicing.generate_invoice_from_labor(
        "GE1",
        period_start=_dt.date(2026, 3, 1),
        period_end=_dt.date(2026, 3, 31),
        labor_rows=rows2,
        invoice_date=_dt.date(2026, 3, 1),
    )
    ar_invoicing.persist(inv2)
    ar_invoicing.mark_sent(inv2.invoice_id)
    summary = ar_invoicing.aging_summary(as_of=_dt.date(2026, 5, 20))
    # 5 days = 1-15 bucket; 65 days = 61-90 bucket
    assert summary["1-15"] == 100.0
    # Inv2 due 2026-03-16 (Mar 1 + Net-15); as_of=May 20 → 65 days past due
    assert summary["61-90"] == 200.0


def test_bucket_for_days_boundaries():
    assert ar_invoicing._bucket_for_days(0) == "current"
    assert ar_invoicing._bucket_for_days(1) == "1-15"
    assert ar_invoicing._bucket_for_days(15) == "1-15"
    assert ar_invoicing._bucket_for_days(16) == "16-30"
    assert ar_invoicing._bucket_for_days(31) == "31-60"
    assert ar_invoicing._bucket_for_days(91) == "90+"


# -----------------------------------------------------------------------------
# Collections cadence
# -----------------------------------------------------------------------------

def test_collections_due_today_first_tier(fresh_stores):
    """5 days past due → first courtesy reminder."""
    iid = _setup_overdue_invoice(due_date=_dt.date(2026, 5, 1))
    candidates = ar_invoicing.collections_due_today(as_of=_dt.date(2026, 5, 6))
    assert len(candidates) == 1
    assert candidates[0].reminder_type == "courtesy_reminder"


def test_collections_skips_already_sent_tier(fresh_stores):
    """If we already sent the courtesy reminder, don't fire again."""
    iid = _setup_overdue_invoice(due_date=_dt.date(2026, 5, 1))
    ar_invoicing.add_collection_event(iid, "courtesy_reminder", note="sent")
    # Still 5 days past due — we shouldn't double-send.
    candidates = ar_invoicing.collections_due_today(as_of=_dt.date(2026, 5, 6))
    assert candidates == []


def test_collections_advances_to_next_tier(fresh_stores):
    """Once enough time passes, collections climbs the cadence ladder."""
    iid = _setup_overdue_invoice(due_date=_dt.date(2026, 5, 1))
    ar_invoicing.add_collection_event(iid, "courtesy_reminder", note="sent")
    # 16 days past due (May 17): first_followup is the new tier
    candidates = ar_invoicing.collections_due_today(as_of=_dt.date(2026, 5, 17))
    assert len(candidates) == 1
    assert candidates[0].reminder_type == "first_followup"


def test_collections_skips_paid_invoices(fresh_stores):
    iid = _setup_overdue_invoice(due_date=_dt.date(2026, 5, 1))
    ar_invoicing.apply_payment(iid, 400)  # full payment
    candidates = ar_invoicing.collections_due_today(as_of=_dt.date(2026, 5, 30))
    assert candidates == []


def test_collections_skips_drafts(fresh_stores):
    """Drafts (not yet sent) don't show up in collections."""
    project_registry.register("GE1", name="GE1", billing_terms="Net-15")
    rows = [_make_row("Day", 8, 400, _dt.date(2026, 4, 5))]
    inv = ar_invoicing.generate_invoice_from_labor(
        "GE1",
        period_start=_dt.date(2026, 4, 1),
        period_end=_dt.date(2026, 4, 30),
        labor_rows=rows,
        invoice_date=_dt.date(2026, 4, 1),
    )
    ar_invoicing.persist(inv)
    # Don't mark_sent — leave as draft.
    candidates = ar_invoicing.collections_due_today(as_of=_dt.date(2026, 6, 1))
    assert candidates == []
