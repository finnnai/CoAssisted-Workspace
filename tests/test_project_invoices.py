# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE use only.
"""Tests for project_invoices — model, classifier, sheet projection, QB row."""

from __future__ import annotations

import pytest

import project_invoices as pi
from project_invoices import (
    ExtractedInvoice,
    InvoiceLineItem,
    INVOICE_SHEET_COLUMNS,
    PROJECT_SHEET_COLUMNS,
    QB_INVOICE_CSV_COLUMNS,
)


# --------------------------------------------------------------------------- #
# ExtractedInvoice — Pydantic shape + coercions
# --------------------------------------------------------------------------- #


def test_minimum_invoice_validates():
    inv = ExtractedInvoice()
    assert inv.currency == "USD"
    assert inv.status == "OPEN"
    assert inv.billable is True
    assert inv.markup_pct == 0.0
    assert inv.line_items == []


def test_currency_none_coerces_to_usd():
    inv = ExtractedInvoice.model_validate({"currency": None})
    assert inv.currency == "USD"


def test_category_none_coerces_to_misc():
    inv = ExtractedInvoice.model_validate({"category": None})
    assert inv.category == "Miscellaneous Expense"


def test_category_legacy_maps_to_qbo():
    inv = ExtractedInvoice.model_validate({"category": "Travel — Airfare"})
    assert inv.category == "Travel"


def test_status_invalid_coerces_to_open():
    inv = ExtractedInvoice.model_validate({"status": "weird"})
    assert inv.status == "OPEN"


def test_status_uppercases():
    inv = ExtractedInvoice.model_validate({"status": "approved"})
    assert inv.status == "APPROVED"


def test_payment_terms_normalize():
    inv = ExtractedInvoice.model_validate({"payment_terms": "net 30"})
    assert inv.payment_terms == "Net 30"


def test_payment_terms_due_on_receipt_variants():
    a = ExtractedInvoice.model_validate({"payment_terms": "Due upon receipt"})
    b = ExtractedInvoice.model_validate({"payment_terms": "due on receipt"})
    assert a.payment_terms == "Due on receipt"
    assert b.payment_terms == "Due on receipt"


def test_terms_to_days():
    assert pi.terms_to_days("Net 30") == 30
    assert pi.terms_to_days("Due on receipt") == 0
    assert pi.terms_to_days("Net 999") is None
    assert pi.terms_to_days(None) is None


def test_extra_fields_ignored():
    """Strict validation should not break when LLM emits surprise keys."""
    inv = ExtractedInvoice.model_validate({
        "vendor": "Acme",
        "surprise": "bonus_field",
        "line_items": [],
    })
    assert inv.vendor == "Acme"


def test_markup_negative_rejected():
    with pytest.raises(Exception):
        ExtractedInvoice(markup_pct=-5)


# --------------------------------------------------------------------------- #
# Billable + markup math
# --------------------------------------------------------------------------- #


def test_compute_invoiceable_amount_with_markup():
    inv = ExtractedInvoice(total=1000.0, billable=True, markup_pct=15.0)
    out = inv.compute_invoiceable_amount()
    assert out == 1150.0
    assert inv.invoiceable_amount == 1150.0


def test_compute_invoiceable_amount_no_markup():
    inv = ExtractedInvoice(total=500.0, billable=True, markup_pct=0.0)
    assert inv.compute_invoiceable_amount() == 500.0


def test_compute_invoiceable_amount_not_billable():
    inv = ExtractedInvoice(total=999.0, billable=False, markup_pct=20.0)
    assert inv.compute_invoiceable_amount() is None


def test_compute_invoiceable_amount_no_total():
    inv = ExtractedInvoice(billable=True, markup_pct=10.0)
    assert inv.compute_invoiceable_amount() is None


# --------------------------------------------------------------------------- #
# Document classifier
# --------------------------------------------------------------------------- #


def test_classify_invoice_strong_signals():
    text = (
        "INVOICE NUMBER: INV-2026-0042\n"
        "Bill To: Acme HQ\n"
        "Due Date: 2026-05-15\n"
        "Net 30\n"
        "Total: $1,250.00\n"
    )
    kind, conf, _ = pi.classify_document(text)
    assert kind == "invoice"
    assert conf >= 0.8


def test_classify_unum_style_no_invoice_number_rejected():
    """Regression for the Unum benefits-statement false positive (Apr 2026):
    weak invoice language without a printed invoice/PO number must NOT
    classify as a strong invoice. The body has 'amount due', 'due date',
    'remit to' — but no actual invoice #."""
    text = (
        "Your benefits statement\n"
        "Amount due: $66,499.37\n"
        "Due date: May 1, 2026\n"
        "Remit to: Unum Group, P.O. Box 12345, Chattanooga TN\n"
        "Bill to: Acme Corp Benefits\n"
    )
    kind, conf, reason = pi.classify_document(text)
    # New rule: weak-only signals score below the 0.6 default threshold so
    # the orchestrator's classify_threshold rejects this as not-an-invoice.
    assert conf < 0.6, f"expected low confidence, got {conf} ({reason})"


def test_classify_strong_plus_weak_high_confidence():
    text = (
        "Invoice #INV-2026-0042\n"
        "Net 30\n"
        "Amount due: $500\n"
    )
    kind, conf, _ = pi.classify_document(text)
    assert kind == "invoice"
    assert conf >= 0.8  # strong + weak combo


def test_classify_strong_only_medium_confidence():
    text = "Invoice no: ABC-001"
    kind, conf, _ = pi.classify_document(text)
    assert kind == "invoice"
    assert conf >= 0.65  # just one strong signal, no weak corroboration


def test_classify_receipt_strong_signals():
    text = (
        "Thank you for your purchase!\n"
        "Visa **** 4242 — paid\n"
        "Authorization code: 7H3X9\n"
        "Subtotal: 12.00  Tax: 1.20  Total: 13.20\n"
    )
    kind, conf, _ = pi.classify_document(text)
    assert kind == "receipt"
    assert conf >= 0.7


def test_classify_empty_defaults_invoice():
    kind, conf, reason = pi.classify_document("")
    assert kind == "invoice"
    assert "default" in reason


def test_classify_no_signal_defaults_invoice():
    kind, _, reason = pi.classify_document("Random unrelated body text.")
    assert kind == "invoice"
    assert "no_signal" in reason or "default" in reason


def test_classify_ambiguous_returns_lower_confidence():
    text = "Net 30 — paid in full last month"  # one of each side
    kind, conf, _ = pi.classify_document(text)
    assert kind in ("invoice", "receipt")
    assert conf < 0.85


# --------------------------------------------------------------------------- #
# Content key dedup
# --------------------------------------------------------------------------- #


def test_content_key_basic():
    k = pi.invoice_content_key("Acme Inc.", "INV-001", 100.00)
    assert k is not None
    # Vendor lowercased + 'Inc' suffix stripped, total in cents
    assert "10000" in k
    assert "inv-001" in k


def test_content_key_missing_vendor_returns_none():
    assert pi.invoice_content_key(None, "INV-1", 10.0) is None


def test_content_key_missing_invoice_number_returns_none():
    assert pi.invoice_content_key("Acme", None, 10.0) is None


def test_content_key_handles_no_total():
    # When total is None we still return a key (with 0 cents marker).
    k = pi.invoice_content_key("Acme", "INV-1", None)
    assert k is not None
    assert k.endswith("|0")


def test_content_key_collapses_vendor_variants():
    a = pi.invoice_content_key("Anthropic", "INV-1", 100.0)
    b = pi.invoice_content_key("Anthropic, PBC", "INV-1", 100.0)
    assert a == b


# --------------------------------------------------------------------------- #
# Sheet row projection
# --------------------------------------------------------------------------- #


def test_sheet_row_length_matches_columns():
    inv = ExtractedInvoice(
        vendor="Acme", invoice_number="INV-1",
        invoice_date="2026-04-01", total=100.0, billable=True, markup_pct=10,
    )
    row = pi.invoice_to_sheet_row(
        inv, logged_at="2026-04-26T10:00:00-07:00", asof_iso="2026-04-26",
    )
    assert len(row) == len(PROJECT_SHEET_COLUMNS)


def test_invoice_sheet_columns_alias_matches_project():
    """Backwards-compat: INVOICE_SHEET_COLUMNS must point at the same list."""
    assert INVOICE_SHEET_COLUMNS is PROJECT_SHEET_COLUMNS


def test_sheet_row_starts_with_doc_type_invoice():
    """The unified schema puts doc_type at index 1 — invoices stamp 'invoice'."""
    inv = ExtractedInvoice(
        vendor="Acme", invoice_number="INV-1",
        invoice_date="2026-04-01", total=100.0,
    )
    row = pi.invoice_to_sheet_row(inv, logged_at="2026-04-26")
    dt_idx = PROJECT_SHEET_COLUMNS.index("doc_type")
    assert row[dt_idx] == "invoice"


def test_sheet_row_computes_days_outstanding():
    inv = ExtractedInvoice(
        vendor="Acme", invoice_number="INV-2",
        invoice_date="2026-04-01", total=200.0,
    )
    row = pi.invoice_to_sheet_row(
        inv, logged_at="2026-04-26", asof_iso="2026-04-26",
    )
    do_idx = INVOICE_SHEET_COLUMNS.index("days_outstanding")
    assert row[do_idx] == 25


def test_sheet_row_includes_invoiceable_amount():
    inv = ExtractedInvoice(
        vendor="Acme", invoice_number="INV-3",
        invoice_date="2026-04-01", total=1000.0,
        billable=True, markup_pct=20.0,
    )
    row = pi.invoice_to_sheet_row(inv, logged_at="2026-04-26")
    ia_idx = INVOICE_SHEET_COLUMNS.index("invoiceable_amount")
    assert row[ia_idx] == 1200.0


def test_sheet_row_billable_false_renders_blank_amount():
    inv = ExtractedInvoice(
        vendor="Acme", invoice_number="INV-4",
        invoice_date="2026-04-01", total=500.0,
        billable=False, markup_pct=10.0,
    )
    row = pi.invoice_to_sheet_row(inv, logged_at="2026-04-26")
    ia_idx = INVOICE_SHEET_COLUMNS.index("invoiceable_amount")
    b_idx = INVOICE_SHEET_COLUMNS.index("billable")
    assert row[ia_idx] == ""
    assert row[b_idx] == "FALSE"


def test_sheet_row_includes_content_key():
    inv = ExtractedInvoice(
        vendor="Acme", invoice_number="INV-5",
        invoice_date="2026-04-01", total=42.0,
    )
    row = pi.invoice_to_sheet_row(inv, logged_at="2026-04-26")
    ck_idx = INVOICE_SHEET_COLUMNS.index("content_key")
    assert "inv-5" in row[ck_idx]


# --------------------------------------------------------------------------- #
# QuickBooks row projection
# --------------------------------------------------------------------------- #


def test_qb_row_basic_shape():
    inv = ExtractedInvoice(
        vendor="Acme", invoice_number="INV-9",
        invoice_date="2026-04-01", due_date="2026-05-01",
        total=750.0, currency="USD", project_code="ALPHA",
        category="Contract Labor", po_number="PO-7",
    )
    row = pi.invoice_to_qb_row(inv)
    assert len(row) == len(QB_INVOICE_CSV_COLUMNS)
    assert row[QB_INVOICE_CSV_COLUMNS.index("BillNo")] == "INV-9"
    assert row[QB_INVOICE_CSV_COLUMNS.index("Vendor")] == "Acme"
    assert row[QB_INVOICE_CSV_COLUMNS.index("Account")] == "Contract Labor"
    memo = row[QB_INVOICE_CSV_COLUMNS.index("Memo")]
    assert "Project: ALPHA" in memo
    assert "PO: PO-7" in memo


def test_qb_row_no_project_no_po():
    inv = ExtractedInvoice(
        vendor="Acme", invoice_number="INV-10", total=100.0,
    )
    row = pi.invoice_to_qb_row(inv)
    memo = row[QB_INVOICE_CSV_COLUMNS.index("Memo")]
    # No "Project:" or "PO:" prefixes when those fields are missing.
    assert "Project:" not in memo
    assert "PO:" not in memo


# --------------------------------------------------------------------------- #
# days_outstanding helper
# --------------------------------------------------------------------------- #


def test_days_outstanding_basic():
    assert pi.days_outstanding("2026-04-01", "2026-04-10") == 9


def test_days_outstanding_negative_clamped_to_zero():
    # Future invoice date — clamp to 0.
    assert pi.days_outstanding("2030-01-01", "2026-04-01") == 0


def test_days_outstanding_bad_input():
    assert pi.days_outstanding(None, "2026-04-01") is None
    assert pi.days_outstanding("not-a-date", "2026-04-01") is None


# --------------------------------------------------------------------------- #
# Status enumeration
# --------------------------------------------------------------------------- #


def test_invoice_statuses_all_valid():
    assert "OPEN" in pi.INVOICE_STATUSES
    assert "APPROVED" in pi.INVOICE_STATUSES
    assert "PAID" in pi.INVOICE_STATUSES
    assert "DISPUTED" in pi.INVOICE_STATUSES
    assert "VOID" in pi.INVOICE_STATUSES


def test_line_item_extra_ignored():
    li = InvoiceLineItem.model_validate({
        "description": "Labor",
        "quantity": 4.0,
        "unit_price": 75.0,
        "line_total": 300.0,
        "junk": "ignored",
    })
    assert li.description == "Labor"
    assert li.line_total == 300.0


# --------------------------------------------------------------------------- #
# receipt_to_project_sheet_row — receipts in the unified project sheet
# --------------------------------------------------------------------------- #


def _make_fake_receipt(**overrides):
    """Build a minimal stand-in for ExtractedReceipt without importing the
    real receipts module (which has heavier deps the tests don't need)."""
    from receipts import ExtractedReceipt
    defaults = dict(
        date="2026-04-15",
        merchant="Chevron",
        total=42.50,
        currency="USD",
        category="Auto Expense",
        confidence=0.9,
        source_kind="email_image",
        source_id="gmail:abc123",
    )
    defaults.update(overrides)
    return ExtractedReceipt(**defaults)


def test_receipt_row_length_matches_columns():
    rec = _make_fake_receipt()
    row = pi.receipt_to_project_sheet_row(
        rec, project_code="ALPHA",
        billable=True, markup_pct=10.0, logged_at="2026-04-26",
    )
    assert len(row) == len(PROJECT_SHEET_COLUMNS)


def test_receipt_row_doc_type_is_receipt():
    rec = _make_fake_receipt()
    row = pi.receipt_to_project_sheet_row(
        rec, project_code="ALPHA",
        billable=True, markup_pct=0.0, logged_at="2026-04-26",
    )
    dt_idx = PROJECT_SHEET_COLUMNS.index("doc_type")
    assert row[dt_idx] == "receipt"


def test_receipt_row_status_is_paid():
    """Receipts are by definition already paid."""
    rec = _make_fake_receipt()
    row = pi.receipt_to_project_sheet_row(
        rec, project_code="ALPHA",
        billable=True, markup_pct=0.0, logged_at="2026-04-26",
    )
    s_idx = PROJECT_SHEET_COLUMNS.index("status")
    do_idx = PROJECT_SHEET_COLUMNS.index("days_outstanding")
    assert row[s_idx] == "PAID"
    assert row[do_idx] == 0


def test_receipt_row_invoice_specific_fields_blank():
    """Invoice-specific fields stay blank for receipts."""
    rec = _make_fake_receipt()
    row = pi.receipt_to_project_sheet_row(
        rec, project_code="ALPHA",
        billable=True, markup_pct=0.0, logged_at="2026-04-26",
    )
    for blank_col in ("due_date", "invoice_number", "po_number",
                      "payment_terms", "bill_to", "remit_to"):
        idx = PROJECT_SHEET_COLUMNS.index(blank_col)
        assert row[idx] == "", f"expected {blank_col} blank, got {row[idx]!r}"


def test_receipt_row_merchant_lands_in_vendor_column():
    rec = _make_fake_receipt(merchant="Anthropic, PBC")
    row = pi.receipt_to_project_sheet_row(
        rec, project_code="ALPHA",
        billable=True, markup_pct=0.0, logged_at="2026-04-26",
    )
    v_idx = PROJECT_SHEET_COLUMNS.index("vendor")
    assert row[v_idx] == "Anthropic, PBC"


def test_receipt_row_invoiceable_amount_with_markup():
    rec = _make_fake_receipt(total=100.0)
    row = pi.receipt_to_project_sheet_row(
        rec, project_code="ALPHA",
        billable=True, markup_pct=15.0, logged_at="2026-04-26",
    )
    ia_idx = PROJECT_SHEET_COLUMNS.index("invoiceable_amount")
    assert row[ia_idx] == 115.0


def test_receipt_row_billable_false_blanks_invoiceable():
    rec = _make_fake_receipt(total=100.0)
    row = pi.receipt_to_project_sheet_row(
        rec, project_code="ALPHA",
        billable=False, markup_pct=20.0, logged_at="2026-04-26",
    )
    ia_idx = PROJECT_SHEET_COLUMNS.index("invoiceable_amount")
    b_idx = PROJECT_SHEET_COLUMNS.index("billable")
    assert row[ia_idx] == ""
    assert row[b_idx] == "FALSE"


def test_receipt_row_unresolved_project_keeps_code_blank():
    """When called with project_code=None (parking-lot path)."""
    rec = _make_fake_receipt()
    row = pi.receipt_to_project_sheet_row(
        rec, project_code=None,
        billable=True, markup_pct=0.0, logged_at="2026-04-26",
    )
    pc_idx = PROJECT_SHEET_COLUMNS.index("project_code")
    assert row[pc_idx] == ""


def test_receipt_row_includes_content_key():
    rec = _make_fake_receipt()
    row = pi.receipt_to_project_sheet_row(
        rec, project_code="ALPHA",
        billable=True, markup_pct=0.0, logged_at="2026-04-26",
    )
    ck_idx = PROJECT_SHEET_COLUMNS.index("content_key")
    # Receipt content_key uses receipts.content_key — merchant|date|cents|last4
    assert row[ck_idx] != ""
    assert "chevron" in row[ck_idx].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
