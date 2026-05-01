# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Unit tests for gl_classifier_llm.py — Tier 3 LLM fallback.

We don't actually call the LLM here; tests stub `llm.call_simple` and
`llm.is_available` so they're deterministic and free. End-to-end live
calls live in tests/test_gl_classifier_e2e.py (network marker, skipped
in test-fast).
"""

from __future__ import annotations

import sys
import types

import pytest

import gl_classifier_llm


@pytest.fixture
def stub_llm(monkeypatch):
    """Replace the lazy `import llm` in classify_via_llm with a stub.

    Yields a callable: pass `set_response(text)` to control what the next
    call_simple returns. Pass `set_unavailable()` to simulate no API key.
    """
    state: dict = {"available": True, "text": "62300:IT Expenses"}

    fake_llm = types.SimpleNamespace(
        is_available=lambda: (state["available"], "stub" if state["available"] else "no key"),
        call_simple=lambda prompt, system=None, max_tokens=64, temperature=0.0: {
            "text": state["text"],
            "model": "claude-haiku-4-5",
            "input_tokens": 200,
            "output_tokens": 5,
            "estimated_cost_usd": 0.0002,
        },
    )
    # Inject as a top-level module so the lazy `import llm` inside
    # classify_via_llm picks it up.
    monkeypatch.setitem(sys.modules, "llm", fake_llm)

    class Controller:
        def set_response(self, text: str) -> None:
            state["text"] = text

        def set_unavailable(self) -> None:
            state["available"] = False

    yield Controller()


# -----------------------------------------------------------------------------
# Happy path — LLM returns a valid candidate
# -----------------------------------------------------------------------------

def test_returns_valid_account_string(stub_llm):
    stub_llm.set_response("62300:IT Expenses")
    result = gl_classifier_llm.classify_via_llm(merchant_name="Knack")
    assert result == "62300:IT Expenses"


def test_returns_none_when_llm_says_none(stub_llm):
    stub_llm.set_response("NONE")
    result = gl_classifier_llm.classify_via_llm(merchant_name="MysteryBiz")
    assert result is None


def test_returns_none_when_llm_unavailable(stub_llm):
    stub_llm.set_unavailable()
    result = gl_classifier_llm.classify_via_llm(merchant_name="Knack")
    assert result is None


# -----------------------------------------------------------------------------
# Defensive parsing — never trust free-form LLM output
# -----------------------------------------------------------------------------

def test_rejects_invalid_account_string(stub_llm):
    """LLM hallucinates a fake GL → we return None, not the bad string."""
    stub_llm.set_response("99999:Made Up Account")
    result = gl_classifier_llm.classify_via_llm(merchant_name="Knack")
    assert result is None


def test_strips_quote_wrapping(stub_llm):
    """LLM wraps the answer in quotes → we still recognize it."""
    stub_llm.set_response('"62300:IT Expenses"')
    assert (
        gl_classifier_llm.classify_via_llm(merchant_name="Knack")
        == "62300:IT Expenses"
    )


def test_strips_backtick_wrapping(stub_llm):
    """LLM wraps in code fence → still recognized."""
    stub_llm.set_response("`62300:IT Expenses`")
    assert (
        gl_classifier_llm.classify_via_llm(merchant_name="Knack")
        == "62300:IT Expenses"
    )


def test_extracts_from_prefix_match(stub_llm):
    """LLM appends commentary; we recover the leading account number."""
    stub_llm.set_response("62300:IT Expenses (admin office software)")
    assert (
        gl_classifier_llm.classify_via_llm(merchant_name="Knack")
        == "62300:IT Expenses"
    )


def test_returns_none_on_empty_response(stub_llm):
    stub_llm.set_response("")
    assert gl_classifier_llm.classify_via_llm(merchant_name="Knack") is None


def test_returns_none_when_merchant_blank():
    """No merchant name → don't even bother calling the LLM."""
    assert gl_classifier_llm.classify_via_llm(merchant_name="") is None


# -----------------------------------------------------------------------------
# Error resilience
# -----------------------------------------------------------------------------

def test_returns_none_when_call_simple_raises(monkeypatch):
    """Any exception inside the LLM call → graceful None."""
    fake_llm = types.SimpleNamespace(
        is_available=lambda: (True, "stub"),
        call_simple=lambda **_: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setitem(sys.modules, "llm", fake_llm)
    assert (
        gl_classifier_llm.classify_via_llm(merchant_name="Knack")
        is None
    )


# -----------------------------------------------------------------------------
# Prompt construction
# -----------------------------------------------------------------------------

def test_prompt_includes_all_provided_fields():
    """The user prompt should carry every signal the caller passed in."""
    prompt = gl_classifier_llm._build_user_prompt(
        merchant="PIRATE SHIP",
        mcc=4215,
        mcc_description="Couriers",
        memo="AMEX Transactions - PIRATE SHIP",
        amount=42.99,
        cardholder_email="ops@surefox.com",
        department_hint="OXBLOOD",
    )
    assert "PIRATE SHIP" in prompt
    assert "4215" in prompt
    assert "Couriers" in prompt
    assert "AMEX Transactions" in prompt
    assert "42.99" in prompt
    assert "ops@surefox.com" in prompt
    assert "OXBLOOD" in prompt
    # Candidate list must be present.
    assert "62300:IT Expenses" in prompt


def test_prompt_omits_blank_fields():
    """Optional fields don't appear in the prompt when None."""
    prompt = gl_classifier_llm._build_user_prompt(
        merchant="Acme",
        mcc=None,
        mcc_description=None,
        memo=None,
        amount=None,
        cardholder_email=None,
        department_hint=None,
    )
    assert "Acme" in prompt
    assert "MCC:" not in prompt
    assert "Memo:" not in prompt
    assert "Amount:" not in prompt
    assert "Cardholder" not in prompt
    assert "Department" not in prompt
