# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Unit tests for gl_classifier.py — full four-tier ladder.

Tier 0 (operator merchant map) is exercised via gl_merchant_map fixtures.
Tier 1 (MCC table) is exercised directly.
Tier 2 (JE-trained memo matcher) is exercised via a stub that simulates
the memo classifier's behavior — keeps these tests deterministic
regardless of whether the user has actually run the trainer.
Tier 3 (LLM fallback) is the catch-all sentinel.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

import gl_classifier
import gl_memo_classifier
import gl_merchant_map
from gl_classifier import (
    Confidence,
    Tier,
    classify_transaction,
    MCC_TO_GL,
    _lookup_mcc,
)


@pytest.fixture
def isolated_classifier_state(tmp_path, monkeypatch):
    """Each test gets a fresh merchant map + a stubbed memo classifier.

    The stub returns [] by default (simulates "tier 2 has no opinion"),
    so tests that don't need tier 2 fall straight through to tier 3.
    Tests that DO need tier 2 can monkeypatch
    `gl_memo_classifier.lookup_by_memo` themselves.

    Per Finnn 2026-05-01 Part G2: also redirects `_INDEX_PATH` to a
    nonexistent tmp path AND resets the in-process `_INDEX` cache, so
    even code paths that bypass `lookup_by_memo` (which the
    monkeypatch covers) can't pick up a real disk-resident index that
    `scripts/train_gl_memo_classifier.py` may have produced on the
    operator's machine. Belt-and-suspenders isolation.
    """
    monkeypatch.setattr(
        gl_merchant_map, "_MAP_PATH", tmp_path / "gl_merchant_map.json"
    )
    monkeypatch.setattr(
        gl_memo_classifier, "lookup_by_memo", lambda memo, top_k=3: []
    )
    monkeypatch.setattr(
        gl_memo_classifier, "_INDEX_PATH", tmp_path / "gl_memo_index.json"
    )
    gl_memo_classifier._reset_for_test()
    yield tmp_path
    gl_memo_classifier._reset_for_test()


# -----------------------------------------------------------------------------
# MCC range lookup — tier 1 internals
# -----------------------------------------------------------------------------

def test_lookup_mcc_inside_range():
    """An MCC inside one of the bands resolves to the band's GL account."""
    # 4511 is in the (4511, 4511) band → Travel - COS
    assert _lookup_mcc(4511) == "53000:Travel - COS"
    # 7011 → lodging → Travel - COS
    assert _lookup_mcc(7011) == "53000:Travel - COS"
    # 5541 → fuel dispenser → Vehicles - COS
    assert _lookup_mcc(5541) == "52100:Vehicles - COS"


def test_lookup_mcc_at_boundaries():
    """MCC at exact band edges still resolves (inclusive ranges)."""
    # 3000 and 3299 are the inclusive bounds of the airline carrier band
    assert _lookup_mcc(3000) == "53000:Travel - COS"
    assert _lookup_mcc(3299) == "53000:Travel - COS"


def test_lookup_mcc_outside_any_range():
    """An MCC not in any band returns None — caller falls through to tier 2."""
    # 9999 is not assigned to any band in MCC_TO_GL
    assert _lookup_mcc(9999) is None


def test_mcc_table_has_no_overlapping_bands():
    """No MCC code should match two different GL accounts.

    Overlapping bands are a config bug — the lookup is order-dependent
    and would silently shadow whichever band came second.
    """
    bands = list(MCC_TO_GL.keys())
    for i, (lo_a, hi_a) in enumerate(bands):
        for (lo_b, hi_b) in bands[i + 1 :]:
            overlaps = lo_a <= hi_b and lo_b <= hi_a
            if overlaps:
                # Same GL is fine (we may split a contiguous range into
                # two entries for clarity); only flag cross-GL overlaps.
                if MCC_TO_GL[(lo_a, hi_a)] != MCC_TO_GL[(lo_b, hi_b)]:
                    raise AssertionError(
                        f"MCC bands ({lo_a}-{hi_a}) and ({lo_b}-{hi_b}) "
                        f"overlap with different GL accounts: "
                        f"{MCC_TO_GL[(lo_a, hi_a)]!r} vs "
                        f"{MCC_TO_GL[(lo_b, hi_b)]!r}"
                    )


# -----------------------------------------------------------------------------
# classify_transaction — public entry point
# -----------------------------------------------------------------------------

def test_classify_amex_airline_high_confidence(isolated_classifier_state):
    """An AMEX airline transaction routes via MCC table at HIGH confidence."""
    result = classify_transaction(
        merchant_name="UNITED AIRLINES",
        mcc_code=4511,
        amount=487.20,
    )
    assert result.gl_account == "53000:Travel - COS"
    assert result.confidence == Confidence.HIGH
    assert result.tier_used == Tier.MCC_TABLE


def test_classify_tier_0_operator_override_beats_mcc(isolated_classifier_state):
    """An operator-confirmed mapping wins over the MCC table."""
    gl_merchant_map.learn(
        "UNITED AIRLINES", "63000:Travel", source="operator"
    )
    result = classify_transaction(
        merchant_name="UNITED AIRLINES",
        mcc_code=4511,  # would normally route to 53000:Travel - COS
    )
    assert result.gl_account == "63000:Travel"
    assert result.tier_used == Tier.MERCHANT_MAP
    assert result.confidence == Confidence.HIGH
    assert result.merchant_map_hit is True


def test_classify_tier_0_per_cardholder_override(isolated_classifier_state):
    """Cardholder-specific override applies only for that cardholder."""
    gl_merchant_map.learn(
        "Amazon",
        "52200:Supplies & Equipment - COS",
        source="operator",
        cardholder_email="ops@surefox.com",
    )
    # Cardholder match wins
    r1 = classify_transaction(
        merchant_name="Amazon",
        mcc_code=None,
        cardholder_email="ops@surefox.com",
    )
    assert r1.gl_account == "52200:Supplies & Equipment - COS"
    assert r1.tier_used == Tier.MERCHANT_MAP
    # Different cardholder, no global override → falls through
    r2 = classify_transaction(
        merchant_name="Amazon",
        mcc_code=None,
        cardholder_email="admin@surefox.com",
    )
    assert r2.tier_used != Tier.MERCHANT_MAP


def test_classify_tier_2_je_trained_routing(monkeypatch, isolated_classifier_state):
    """When MCC misses but the JE matcher has a clear winner, tier 2 routes."""
    # Stub the memo classifier to return a clear winner.
    monkeypatch.setattr(
        gl_memo_classifier,
        "lookup_by_memo",
        lambda memo, top_k=3: [
            ("62300:IT Expenses", 1.0),
            ("62200:Supplies & Equipment", 0.20),  # gap=0.80 → MEDIUM
        ],
    )
    monkeypatch.setattr(
        gl_memo_classifier, "confidence_from_top_two", lambda r, **kw: "medium"
    )
    result = classify_transaction(
        merchant_name="Knack",
        mcc_code=None,
        memo="AMEX Transactions - KNACK.COM",
    )
    assert result.gl_account == "62300:IT Expenses"
    assert result.tier_used == Tier.JE_TRAINED
    assert result.confidence == Confidence.MEDIUM
    assert "JE-trained" in result.reason


def test_classify_tier_2_low_confidence_close_call(monkeypatch, isolated_classifier_state):
    """Small gap between tier-2 candidates returns LOW (caller bumps to tier 3 / review)."""
    monkeypatch.setattr(
        gl_memo_classifier,
        "lookup_by_memo",
        lambda memo, top_k=3: [
            ("53000:Travel - COS", 1.0),
            ("63000:Travel", 0.95),
        ],
    )
    monkeypatch.setattr(
        gl_memo_classifier, "confidence_from_top_two", lambda r, **kw: "low"
    )
    result = classify_transaction(
        merchant_name="UNITED",
        mcc_code=None,
        memo="United Airlines flight",
    )
    assert result.tier_used == Tier.JE_TRAINED
    assert result.confidence == Confidence.LOW


def test_classify_amex_courier_high_confidence(isolated_classifier_state):
    """Couriers (4214/4215) currently fall outside the table — fallback path.

    Today MCC 4215 is NOT in the table (test pins the current behavior).
    When we add courier coverage, this test flips to HIGH/MCC_TABLE.
    """
    result = classify_transaction(
        merchant_name="PIRATE SHIP",
        mcc_code=4215,
    )
    # No MCC match + tier 2 stubbed empty → tier 3 fallback.
    assert result.tier_used == Tier.LLM_FALLBACK


def test_classify_wex_no_mcc_falls_through_when_je_index_empty(
    isolated_classifier_state,
):
    """WEX has no MCC; with empty memo index, falls to tier 3."""
    result = classify_transaction(
        merchant_name="CHEVRON 0090562",
        mcc_code=None,
        memo="ISOC Gas Usage",
        department_hint="ISOC",
    )
    # JE index is stubbed to return [] in this fixture.
    assert result.confidence == Confidence.LOW
    assert result.tier_used == Tier.LLM_FALLBACK
    assert result.gl_account == "22040:Credit Card Clearing"


def test_classify_unknown_mcc_falls_through(isolated_classifier_state):
    """An MCC not in any band falls through to lower tiers."""
    result = classify_transaction(
        merchant_name="MYSTERY VENDOR",
        mcc_code=9999,
    )
    assert result.confidence == Confidence.LOW
    assert result.tier_used == Tier.LLM_FALLBACK


def test_classification_result_carries_reason(isolated_classifier_state):
    """Every result must include a non-empty reason string for audit logs."""
    result = classify_transaction(
        merchant_name="UNITED AIRLINES",
        mcc_code=4511,
    )
    assert result.reason  # truthy — non-empty string
    assert "4511" in result.reason or "MCC" in result.reason


# -----------------------------------------------------------------------------
# Coverage spot-check against the AMEX April sample
# -----------------------------------------------------------------------------

def test_april_sample_top_mccs_route_high():
    """The eight most-common MCCs in samples/Amex Transactions - April.csv
    should each route via the deterministic table at HIGH confidence.

    Top MCCs from that month (per `csv` analysis):
        4511 — Airlines (33 txns)
        4215 — Couriers (18 txns) — NOT in table yet, see TODO
        7512 — Car Rental (11 txns)
        5969 — Direct Marketing (5 txns)
        9399 — Government Services NEC (5 txns) — NOT in table yet
        7399 — Business Services NEC (5 txns) — NOT in table yet
        4900 — Utilities (4 txns)
        7311 — Advertising (2 txns)

    Test pins the ones we DO cover; others trigger the TODO band.
    """
    high_confidence_cases = [
        (4511, "53000:Travel - COS"),    # Airlines
        (7512, "53000:Travel - COS"),    # Car Rental
        (5969, "63300:Marketing & Advertising"),  # Direct Marketing
        (4900, "62000:Facilities"),      # Utilities
        (7311, "63300:Marketing & Advertising"),  # Advertising
    ]
    for mcc, expected_gl in high_confidence_cases:
        result = classify_transaction(
            merchant_name=f"TEST_MCC_{mcc}",
            mcc_code=mcc,
        )
        assert result.confidence == Confidence.HIGH, (
            f"MCC {mcc} should route HIGH; got {result.confidence}"
        )
        assert result.gl_account == expected_gl, (
            f"MCC {mcc} should map to {expected_gl!r}; got {result.gl_account!r}"
        )
        assert result.tier_used == Tier.MCC_TABLE
