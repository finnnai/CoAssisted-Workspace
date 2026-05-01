# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for the 4 P5 AP analytics workflows."""

from __future__ import annotations

import datetime as _dt

import p5_workflows as p5


# --------------------------------------------------------------------------- #
# #9 Project spend dashboard
# --------------------------------------------------------------------------- #


def test_spend_dashboard_aggregates_per_project():
    today = _dt.date(2026, 4, 28)
    invoices = [
        {"project_code": "ALPHA", "vendor": "Acme",
         "total": 1000, "invoice_date": "2026-04-15"},  # last 30d
        {"project_code": "ALPHA", "vendor": "Acme",
         "total": 500, "invoice_date": "2026-04-01"},   # last 30d
        {"project_code": "ALPHA", "vendor": "Beta",
         "total": 800, "invoice_date": "2026-02-01"},   # before 60d
        {"project_code": "BETA", "vendor": "Acme",
         "total": 200, "invoice_date": "2026-04-20"},
    ]
    out = p5.build_project_spend_dashboard(invoices, today=today)
    by_project = {r["project_code"]: r for r in out}
    assert by_project["ALPHA"]["ytd_total"] == 2300
    assert by_project["ALPHA"]["last_30d_total"] == 1500
    assert by_project["ALPHA"]["invoice_count"] == 3
    assert by_project["ALPHA"]["top_vendors"][0]["vendor"] == "Acme"


def test_spend_dashboard_with_budgets():
    today = _dt.date(2026, 4, 28)
    invoices = [{"project_code": "ALPHA", "vendor": "X",
                 "total": 7500, "invoice_date": "2026-04-01"}]
    out = p5.build_project_spend_dashboard(
        invoices, today=today,
        budgets={"ALPHA": 10000},
    )
    assert out[0]["budget"] == 10000
    assert out[0]["percent_of_budget"] == 75.0


def test_spend_dashboard_unassigned_grouped():
    invoices = [{"project_code": None, "vendor": "X",
                 "total": 100, "invoice_date": "2026-04-01"}]
    out = p5.build_project_spend_dashboard(invoices)
    assert out[0]["project_code"] == "(unassigned)"


# --------------------------------------------------------------------------- #
# #29 P&L rollup
# --------------------------------------------------------------------------- #


def test_pnl_rollup_computes_margin():
    spend = [{"project_code": "A", "total": 1000}]
    revenue = [{"project_code": "A", "total": 3000}]
    out = p5.build_pnl_rollup(spend, revenue)
    assert out[0]["margin"] == 2000
    assert out[0]["margin_pct"] == 66.7


def test_pnl_rollup_negative_margin():
    spend = [{"project_code": "A", "total": 5000}]
    revenue = [{"project_code": "A", "total": 1000}]
    out = p5.build_pnl_rollup(spend, revenue)
    assert out[0]["margin"] == -4000


def test_pnl_rollup_no_revenue():
    spend = [{"project_code": "A", "total": 500}]
    out = p5.build_pnl_rollup(spend, [])
    assert out[0]["spend"] == 500
    assert out[0]["revenue"] == 0
    assert out[0]["margin_pct"] is None


def test_pnl_rollup_handles_currency_strings():
    """safe_float should normalize $1,234.56 → 1234.56."""
    spend = [{"project_code": "A", "total": "$1,000.00"}]
    revenue = [{"project_code": "A", "total": "$2,500.00"}]
    out = p5.build_pnl_rollup(spend, revenue)
    assert out[0]["spend"] == 1000.0
    assert out[0]["revenue"] == 2500.0


# --------------------------------------------------------------------------- #
# #55 Duplicate invoice detection
# --------------------------------------------------------------------------- #


def test_dup_detection_flags_close_dates():
    invoices = [
        {"id": 1, "vendor": "Acme", "total": 1000, "invoice_date": "2026-04-15"},
        {"id": 2, "vendor": "Acme", "total": 1003, "invoice_date": "2026-04-18"},
    ]
    pairs = p5.find_duplicate_invoices(invoices)
    assert len(pairs) == 1


def test_dup_detection_skips_distant_dates():
    invoices = [
        {"id": 1, "vendor": "Acme", "total": 1000, "invoice_date": "2026-01-15"},
        {"id": 2, "vendor": "Acme", "total": 1000, "invoice_date": "2026-04-18"},
    ]
    pairs = p5.find_duplicate_invoices(invoices, date_tolerance_days=7)
    assert pairs == []


def test_dup_detection_skips_distant_amounts():
    invoices = [
        {"id": 1, "vendor": "Acme", "total": 1000, "invoice_date": "2026-04-15"},
        {"id": 2, "vendor": "Acme", "total": 1500, "invoice_date": "2026-04-16"},
    ]
    pairs = p5.find_duplicate_invoices(invoices, amount_tolerance_pct=1.0)
    assert pairs == []


def test_dup_detection_different_vendors_skipped():
    invoices = [
        {"id": 1, "vendor": "Acme", "total": 500, "invoice_date": "2026-04-15"},
        {"id": 2, "vendor": "Beta", "total": 500, "invoice_date": "2026-04-15"},
    ]
    pairs = p5.find_duplicate_invoices(invoices)
    assert pairs == []


def test_dup_detection_marks_exact_invnum_match():
    invoices = [
        {"id": 1, "vendor": "Acme", "total": 1000, "invoice_date": "2026-04-15",
         "invoice_number": "INV-99"},
        {"id": 2, "vendor": "Acme", "total": 1000, "invoice_date": "2026-04-16",
         "invoice_number": "INV-99"},
    ]
    pairs = p5.find_duplicate_invoices(invoices)
    assert len(pairs) == 1
    assert pairs[0]["exact_invnum_match"] is True


# --------------------------------------------------------------------------- #
# #90 Anomaly detection
# --------------------------------------------------------------------------- #


def test_anomaly_detection_flags_outlier():
    """Vendor median ~$200, single $5000 invoice should flag."""
    invoices = [
        {"vendor": "Acme", "total": 200},
        {"vendor": "Acme", "total": 210},
        {"vendor": "Acme", "total": 195},
        {"vendor": "Acme", "total": 205},
        {"vendor": "Acme", "total": 5000},  # outlier
    ]
    out = p5.detect_ap_anomalies(invoices)
    assert len(out) == 1
    assert out[0]["invoice"]["total"] == 5000
    assert out[0]["severity"] in {"high", "medium"}


def test_anomaly_detection_skips_low_history():
    invoices = [{"vendor": "Solo", "total": 100}]  # only 1 invoice
    out = p5.detect_ap_anomalies(invoices, min_history=3)
    assert out == []


def test_anomaly_detection_no_outliers_returns_empty():
    invoices = [
        {"vendor": "Acme", "total": 200},
        {"vendor": "Acme", "total": 210},
        {"vendor": "Acme", "total": 195},
        {"vendor": "Acme", "total": 205},
    ]
    out = p5.detect_ap_anomalies(invoices)
    assert out == []
