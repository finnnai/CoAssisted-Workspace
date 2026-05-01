"""Tests for score_reply_confidence — pure function, no LLM/network."""
from __future__ import annotations

from project_invoices import score_reply_confidence


def test_high_when_all_fields_answered_no_deferral():
    parsed = {"invoice_number": "INV-99", "total": 1234.56}
    body = "Sure, INV-99 for $1234.56."
    assert score_reply_confidence(parsed, ["invoice_number", "total"], body) == "high"


def test_medium_when_half_answered():
    parsed = {"invoice_number": "INV-99"}
    body = "INV-99, will track down the total."
    # "will track" doesn't match the deferral list (we check for "will send"
    # specifically); 1/2 answered -> medium
    assert score_reply_confidence(parsed, ["invoice_number", "total"], body) == "medium"


def test_low_on_will_send_deferral():
    parsed = {"invoice_number": "INV-99", "total": 100}
    body = "Got it. I will send the proper invoice tomorrow morning."
    assert score_reply_confidence(parsed, ["invoice_number", "total"], body) == "low"


def test_low_on_let_me_check():
    parsed = {"invoice_number": "INV-99"}
    body = "Hmm, let me check on that and get back to you."
    assert score_reply_confidence(parsed, ["invoice_number"], body) == "low"


def test_low_on_out_of_office():
    parsed = {"invoice_number": "INV-99"}
    body = "I'm out of office until Monday — I'll send the invoice when I'm back."
    assert score_reply_confidence(parsed, ["invoice_number"], body) == "low"


def test_low_when_no_fields_answered():
    parsed = {}
    body = "Sure thing!"
    assert score_reply_confidence(parsed, ["invoice_number"], body) == "low"


def test_low_when_empty_body():
    parsed = {"invoice_number": "INV-99"}
    body = ""
    assert score_reply_confidence(parsed, ["invoice_number"], body) == "low"


def test_low_when_no_fields_requested():
    parsed = {"random": "value"}
    body = "Sure, here you go."
    assert score_reply_confidence(parsed, [], body) == "low"


def test_low_when_third_answered():
    """1 of 3 fields = 33% < 50% threshold = low."""
    parsed = {"invoice_number": "INV-99"}
    body = "INV-99 — sending the rest separately."
    # Note: "sending the rest" doesn't trip a deferral phrase, but 1/3 < 0.5
    assert score_reply_confidence(
        parsed, ["invoice_number", "total", "due_date"], body,
    ) == "low"


def test_high_priority_over_partial_match_phrase():
    """All fields answered, no deferral phrases → high even if body
    is brief."""
    parsed = {"invoice_number": "INV-99"}
    body = "INV-99"
    assert score_reply_confidence(parsed, ["invoice_number"], body) == "high"
