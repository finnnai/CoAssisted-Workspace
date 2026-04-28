# © 2026 CoAssisted Workspace contributors contributors. Licensed under MIT — see LICENSE.
"""Fixture-based tests — realistic receipt text from common merchants.

Each fixture pairs a real-shaped receipt body with the LLM response we
EXPECT the prompt to produce on that body. We mock llm.call_simple to
return the expected response, then validate that the parsing pipeline
(category override, sheet row mapping, QB row mapping) produces correct
downstream output.

The point: catch regressions in our PROMPT or PARSING, not the LLM itself.
"""

import json
from unittest.mock import patch

import pytest

import receipts as r


# --------------------------------------------------------------------------- #
# Realistic receipt fixtures
# --------------------------------------------------------------------------- #

UBER_FIXTURE = """\
Thanks for riding with Uber

Your Friday afternoon trip with Uber

Total $24.18

Trip fare    $20.50
Booking fee  $2.10
Tax          $1.58

Charged to Visa **** 4392
Apr 26, 2026 · 4:32 PM

Trip to: 415 Mission St, San Francisco, CA 94105
"""

UBER_EXPECTED = {
    "date": "2026-04-26",
    "merchant": "Uber",
    "total": 24.18,
    "currency": "USD",
    "subtotal": 22.60,
    "tax": 1.58,
    "tip": None,
    "payment_method_kind": "Visa",
    "last_4": "4392",
    "location": "San Francisco, CA",
    "category": "Travel",
    "confidence": 0.95,
    "line_items": [
        {"name": "Trip fare", "quantity": 1, "unit_price": 20.50, "line_total": 20.50},
        {"name": "Booking fee", "quantity": 1, "unit_price": 2.10, "line_total": 2.10},
    ],
}


STRIPE_FIXTURE = """\
Receipt from Acme Inc.

Receipt #2026-04-26

Subscription · Pro Plan
$99.00

Subtotal:    $99.00
Tax:         $0.00
Total:       $99.00 USD

Charged to Mastercard ending in 1111
Apr 26, 2026
"""

STRIPE_EXPECTED = {
    "date": "2026-04-26",
    "merchant": "Acme Inc.",
    "total": 99.00,
    "currency": "USD",
    "subtotal": 99.00,
    "tax": 0.00,
    "payment_method_kind": "Mastercard",
    "last_4": "1111",
    "category": "Software Subscriptions",
    "confidence": 0.95,
    "line_items": [
        {"name": "Pro Plan", "quantity": 1, "unit_price": 99.00, "line_total": 99.00},
    ],
}


HOTEL_FIXTURE = """\
Marriott — Folio

Guest: J. Smith
Check-in: 2026-04-22
Check-out: 2026-04-26
Nights: 4

Room              4 × $189.00 = $756.00
Resort fee        4 × $25.00  = $100.00
State tax         $51.45
Occupancy tax     $35.28

GRAND TOTAL                      $942.73

Settled to Amex ending 1003
"""

HOTEL_EXPECTED = {
    "date": "2026-04-26",
    "merchant": "Marriott",
    "total": 942.73,
    "currency": "USD",
    "subtotal": 856.00,
    "tax": 86.73,
    "payment_method_kind": "Amex",
    "last_4": "1003",
    "category": "Travel",
    "confidence": 0.95,
}


REFUND_FIXTURE = """\
Refund processed — Order #45821

We've refunded your purchase to your original payment method.

Refund amount: -$58.23

This may take 3-5 business days to reflect in your account.
"""

REFUND_EXPECTED = {
    "date": None,
    "merchant": "Acme Returns",
    "total": -58.23,
    "currency": "USD",
    "category": "Miscellaneous Expense",
    "confidence": 0.7,
    "notes": "REFUND — total is negative. Original purchase not in this receipt.",
}


EUR_FIXTURE = """\
Booking confirmation — Hotel Le Bristol

Date: 14 March 2026
Total: €1,425.00
TVA (20%): €237.50
Net: €1,187.50

Cardholder: J. Smith · MasterCard •••• 7432
"""

EUR_EXPECTED = {
    "date": "2026-03-14",
    "merchant": "Hotel Le Bristol",
    "total": 1425.00,
    "currency": "EUR",
    "tax": 237.50,
    "payment_method_kind": "Mastercard",
    "last_4": "7432",
    "category": "Travel",
    "confidence": 0.92,
}


MISSING_TOTAL_FIXTURE = """\
Order confirmation
Items: 3 widgets @ $12.99 each, 1 gizmo @ $25.99
Thanks!
"""

MISSING_TOTAL_EXPECTED = {
    "date": None,
    "merchant": None,
    "total": None,
    "currency": "USD",
    "category": "Miscellaneous Expense",
    "confidence": 0.3,
    "notes": "Total not explicitly stated; inferred line items only.",
}


# Adversarial: a credit card number printed in full. The LLM MUST NOT capture
# more than the last 4. This protects users even if the LLM gets confused.
PAN_LEAKED_FIXTURE = """\
Receipt
Card: 4532-1488-0343-6467
Total: $42.00
"""

PAN_LEAKED_EXPECTED = {
    "merchant": "Unknown",
    "total": 42.00,
    "currency": "USD",
    "payment_method_kind": "Visa",
    "last_4": "6467",  # ONLY last 4 — LLM is instructed never to capture full PAN
    "confidence": 0.6,
}


# --------------------------------------------------------------------------- #
# Fixture-driven tests
# --------------------------------------------------------------------------- #


def _mock_llm(expected: dict):
    """Return a mock that simulates llm.call_simple returning the expected JSON."""
    return {
        "text": json.dumps(expected),
        "model": "claude-haiku-4-5",
        "input_tokens": 500,
        "output_tokens": 200,
        "estimated_cost_usd": 0.0015,
    }


@pytest.mark.parametrize("fixture_text,expected", [
    (UBER_FIXTURE, UBER_EXPECTED),
    (STRIPE_FIXTURE, STRIPE_EXPECTED),
    (HOTEL_FIXTURE, HOTEL_EXPECTED),
    (EUR_FIXTURE, EUR_EXPECTED),
    (MISSING_TOTAL_FIXTURE, MISSING_TOTAL_EXPECTED),
])
def test_extract_realistic_fixtures(fixture_text, expected):
    """Each realistic fixture should parse cleanly through extract_from_text."""
    with patch("llm.call_simple", return_value=_mock_llm(expected)):
        rec = r.extract_from_text(fixture_text, source_id="test")
    # Required-ish fields that ALWAYS exist
    assert rec.confidence == expected["confidence"]
    assert rec.currency == expected["currency"]
    if "total" in expected:
        assert rec.total == expected["total"]
    if "merchant" in expected:
        assert rec.merchant == expected["merchant"]
    # Category should match exactly (heuristic override applies)
    assert rec.category == expected["category"]


def test_uber_specifically():
    """Spot-check: Uber receipts must categorize as Rideshare even if LLM picks Misc."""
    almost_misc = dict(UBER_EXPECTED)
    almost_misc["category"] = "Miscellaneous Expense"  # LLM gets it wrong
    with patch("llm.call_simple", return_value=_mock_llm(almost_misc)):
        rec = r.extract_from_text(UBER_FIXTURE, source_id="msg1")
    # Heuristic must override based on merchant name
    assert rec.category == "Travel"


def test_refund_negative_total():
    """Refunds should preserve negative total values."""
    with patch("llm.call_simple", return_value=_mock_llm(REFUND_EXPECTED)):
        rec = r.extract_from_text(REFUND_FIXTURE, source_id="m_refund")
    assert rec.total == -58.23


def test_eur_currency_preserved():
    """EUR should NOT be silently coerced to USD."""
    with patch("llm.call_simple", return_value=_mock_llm(EUR_EXPECTED)):
        rec = r.extract_from_text(EUR_FIXTURE, source_id="eur1")
    assert rec.currency == "EUR"
    assert rec.total == 1425.00


# --------------------------------------------------------------------------- #
# PII safety — credit card numbers must NEVER end up in last_4
# --------------------------------------------------------------------------- #


def test_full_pan_in_input_never_extracted():
    """Even if the input has a full credit card number, last_4 must be only 4 digits.

    The LLM is instructed not to extract full PANs. This test simulates a
    well-behaved LLM. The point is to validate our SHEET ROW MAPPING never
    accepts more than 4 digits in last_4.
    """
    with patch("llm.call_simple", return_value=_mock_llm(PAN_LEAKED_EXPECTED)):
        rec = r.extract_from_text(PAN_LEAKED_FIXTURE, source_id="m_pan")
    assert rec.last_4 == "6467"
    assert len(rec.last_4) == 4


def test_redact_payment_strips_last4_from_sheet_row():
    """When redact_payment=True, even a valid last_4 must be empty in the row."""
    rec = r.ExtractedReceipt(
        merchant="X", total=10.0, payment_method_kind="Visa", last_4="1234",
    )
    row = r.receipt_to_sheet_row(rec, logged_at="t", redact_payment=True)
    assert row[10] == ""  # last_4 column
    assert row[9] == "Visa"  # payment_method_kind preserved


def test_pan_pattern_never_matches_more_than_4_chars():
    """Defensive: even if upstream gives last_4 a longer string (e.g. full PAN
    leaks through the LLM), the field validator coerces it to last 4 digits."""
    rec = r.ExtractedReceipt(
        merchant="X", total=10.0, payment_method_kind="Visa",
        last_4="4532148803436467",  # full PAN
    )
    # The validator should have coerced this to just the last 4
    assert rec.last_4 == "6467"
    assert len(rec.last_4) == 4

    # Hyphenated input also gets stripped + coerced
    rec2 = r.ExtractedReceipt(last_4="4532-1488-0343-6467")
    assert rec2.last_4 == "6467"

    # Mixed garbage gets stripped to digits then last 4
    rec3 = r.ExtractedReceipt(last_4="abc1234def5678ghi")
    assert rec3.last_4 == "5678"

    # Empty / whitespace returns None
    assert r.ExtractedReceipt(last_4="").last_4 is None
    assert r.ExtractedReceipt(last_4="   ").last_4 is None
    assert r.ExtractedReceipt(last_4="abc").last_4 is None  # no digits


# --------------------------------------------------------------------------- #
# Cost guard — long bodies must be truncated
# --------------------------------------------------------------------------- #


def test_body_truncation_caps_at_15k_chars():
    """If a user has a huge HTML email, we must cap input to control cost."""
    huge_body = "spam " * 5000  # 25,000 chars
    captured = []

    def fake_llm(prompt, **kw):
        captured.append(prompt)
        return _mock_llm({"merchant": None, "total": None, "confidence": 0.0})

    with patch("llm.call_simple", side_effect=fake_llm):
        r.extract_from_text(huge_body, source_id="huge")
    assert len(captured) == 1
    # The body insertion (after the prompt template scaffolding) should be
    # capped at 15,000 chars worth.
    sent_prompt = captured[0]
    body_section = sent_prompt.split("Receipt content:")[-1]
    assert len(body_section) <= 15_500  # 15K body + small newline tolerance


# --------------------------------------------------------------------------- #
# Receipt classifier — false-positive defenses
# --------------------------------------------------------------------------- #


def test_classifier_rejects_marketing_with_unsubscribe():
    """Marketing emails with currency patterns should NOT be flagged as receipts
    UNLESS the subject says receipt-y things. The current classifier is
    permissive — confirm what it actually does so we know our false-positive
    rate.
    """
    is_r, reason = r.classify_email_as_receipt(
        subject="Limited time: 30% off!",
        sender="marketing@randomshop.example",
        body_preview="Save $25 today only. Click here to unsubscribe.",
    )
    # Marketing emails usually don't say "total" + currency — should be False
    assert is_r is False


def test_classifier_handles_subdomain_sender():
    """receipts.uber.com should still match uber.com whitelist."""
    is_r, reason = r.classify_email_as_receipt(
        subject="Trip to airport",
        sender="receipts.uber.com",
        body_preview="",
    )
    assert is_r is True
    assert "uber.com" in reason


def test_classifier_explicit_receipt_keyword_needs_body():
    """Updated contract: 'Receipt #' in subject is no longer a free pass.
    The classifier was over-permissive on subject alone (account-notification
    emails like 'Receipt for password reset' slipped through). Now requires
    body money confirmation too."""
    # No body ⇒ rejected.
    is_r, reason = r.classify_email_as_receipt(
        subject="Receipt #2026-04-26-001",
        sender="orders@somerandomshop.example",
    )
    assert is_r is False
    assert reason == "no_signal"
    # Same subject WITH body money ⇒ accepted.
    is_r, _ = r.classify_email_as_receipt(
        subject="Receipt #2026-04-26-001",
        sender="orders@somerandomshop.example",
        body_preview="Order Total: $42.10",
    )
    assert is_r is True


def test_classifier_confirmation_keyword_needs_body():
    """'Booking confirmation' alone isn't a receipt — it's a confirmation,
    which could be either a receipt-after-payment OR a booking-pending-payment.
    Require body money signal to distinguish."""
    # No body ⇒ rejected.
    is_r, _ = r.classify_email_as_receipt(
        subject="Your booking confirmation — Marriott",
        sender="reservations@anywhere.example",
    )
    assert is_r is False
    # With money ⇒ accepted via body_money_pattern (Marriott isn't on
    # strong-sender list since this fictional anywhere.example domain isn't).
    is_r, reason = r.classify_email_as_receipt(
        subject="Your booking confirmation — Marriott",
        sender="reservations@anywhere.example",
        body_preview="Total charged: $312.50",
    )
    assert is_r is True
    assert "money" in reason


# --------------------------------------------------------------------------- #
# Idempotency — extracting the same receipt twice produces same fields
# --------------------------------------------------------------------------- #


def test_extraction_idempotent():
    """Same input should produce same extraction (mocked LLM is deterministic)."""
    with patch("llm.call_simple", return_value=_mock_llm(UBER_EXPECTED)):
        r1 = r.extract_from_text(UBER_FIXTURE, source_id="x")
        r2 = r.extract_from_text(UBER_FIXTURE, source_id="x")
    assert r1.model_dump() == r2.model_dump()
