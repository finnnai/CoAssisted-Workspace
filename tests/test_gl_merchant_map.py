# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Unit tests for gl_merchant_map.py.

Each test gets a fresh JSON store via the temp-dir fixture so we don't
collide with the user's actual gl_merchant_map.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import gl_merchant_map


@pytest.fixture
def fresh_store(tmp_path, monkeypatch):
    """Redirect the module's _MAP_PATH to a clean temp file per test."""
    fake = tmp_path / "gl_merchant_map.json"
    monkeypatch.setattr(gl_merchant_map, "_MAP_PATH", fake)
    yield fake


# -----------------------------------------------------------------------------
# Normalization
# -----------------------------------------------------------------------------

def test_normalize_lowercases_and_collapses_whitespace():
    """Casing and double-spacing collapse to one canonical form."""
    assert gl_merchant_map._normalize("AMAZON  MARKEPLACE") == "amazon markeplace"
    assert gl_merchant_map._normalize("Amazon Markeplace") == "amazon markeplace"


def test_normalize_strips_corporate_suffixes():
    """Inc/LLC/PBC/etc. don't differentiate the same merchant."""
    assert gl_merchant_map._normalize("Anthropic, Inc.") == "anthropic"
    assert gl_merchant_map._normalize("Acme LLC") == "acme"
    assert gl_merchant_map._normalize("Stripe PBC") == "stripe"


def test_normalize_drops_store_id_digits():
    """Same fuel-station chain at different sites → same key."""
    assert gl_merchant_map._normalize("CHEVRON 0090562") == "chevron"
    assert gl_merchant_map._normalize("CHEVRON 0093591") == "chevron"


def test_normalize_handles_empty_input():
    assert gl_merchant_map._normalize("") == ""
    assert gl_merchant_map._normalize("   ") == ""


# -----------------------------------------------------------------------------
# Lookup + learn round-trip
# -----------------------------------------------------------------------------

def test_lookup_miss_returns_none(fresh_store):
    """Empty store + any lookup = None."""
    assert gl_merchant_map.lookup("Anthropic") is None


def test_learn_then_lookup_global(fresh_store):
    """A learned mapping with no cardholder hits a lookup with no cardholder."""
    gl_merchant_map.learn("Anthropic", "62300:IT Expenses", source="operator")
    assert gl_merchant_map.lookup("Anthropic") == "62300:IT Expenses"


def test_learn_then_lookup_per_cardholder(fresh_store):
    """A per-cardholder mapping is the most specific match."""
    gl_merchant_map.learn(
        "Amazon",
        "52200:Supplies & Equipment - COS",
        source="operator",
        cardholder_email="ops@surefox.com",
    )
    # Cardholder match wins.
    assert (
        gl_merchant_map.lookup("Amazon", cardholder_email="ops@surefox.com")
        == "52200:Supplies & Equipment - COS"
    )
    # Different cardholder, no global → miss.
    assert (
        gl_merchant_map.lookup("Amazon", cardholder_email="admin@surefox.com")
        is None
    )


def test_lookup_falls_back_to_global_when_per_cardholder_missing(fresh_store):
    """If only the global entry exists, any cardholder gets it."""
    gl_merchant_map.learn(
        "Amazon", "62200:Supplies & Equipment", source="operator"
    )
    assert (
        gl_merchant_map.lookup("Amazon", cardholder_email="anyone@x.com")
        == "62200:Supplies & Equipment"
    )


def test_per_cardholder_overrides_global(fresh_store):
    """When both exist, the per-cardholder entry wins for that cardholder."""
    gl_merchant_map.learn("Amazon", "62200:Supplies & Equipment", source="operator")
    gl_merchant_map.learn(
        "Amazon",
        "52200:Supplies & Equipment - COS",
        source="operator",
        cardholder_email="ops@surefox.com",
    )
    # Ops cardholder gets the COS bucket.
    assert (
        gl_merchant_map.lookup("Amazon", cardholder_email="ops@surefox.com")
        == "52200:Supplies & Equipment - COS"
    )
    # Other cardholder still gets the global SG&A bucket.
    assert (
        gl_merchant_map.lookup("Amazon", cardholder_email="other@surefox.com")
        == "62200:Supplies & Equipment"
    )


def test_normalization_makes_lookup_case_insensitive(fresh_store):
    """Casing and trailing punctuation don't affect lookup."""
    gl_merchant_map.learn("ANTHROPIC", "62300:IT Expenses", source="operator")
    assert gl_merchant_map.lookup("anthropic") == "62300:IT Expenses"
    assert gl_merchant_map.lookup("Anthropic, Inc.") == "62300:IT Expenses"


# -----------------------------------------------------------------------------
# Source precedence
# -----------------------------------------------------------------------------

def test_operator_overrides_training(fresh_store):
    """Operator override beats a prior training-derived entry."""
    gl_merchant_map.learn("Acme", "62200:Supplies & Equipment", source="training")
    gl_merchant_map.learn("Acme", "62300:IT Expenses", source="operator")
    assert gl_merchant_map.lookup("Acme") == "62300:IT Expenses"


def test_training_does_not_overwrite_operator(fresh_store):
    """A training pass after operator confirmation must NOT clobber it."""
    gl_merchant_map.learn("Acme", "62300:IT Expenses", source="operator")
    gl_merchant_map.learn(
        "Acme",
        "62200:Supplies & Equipment",
        source="training",
        je_id="JE-9999",
    )
    # Operator value still wins.
    assert gl_merchant_map.lookup("Acme") == "62300:IT Expenses"


def test_operator_can_change_their_mind(fresh_store):
    """Same-precedence updates ARE allowed (operator corrects themselves)."""
    gl_merchant_map.learn("Acme", "62200:Supplies & Equipment", source="operator")
    gl_merchant_map.learn("Acme", "62300:IT Expenses", source="operator")
    assert gl_merchant_map.lookup("Acme") == "62300:IT Expenses"


def test_unknown_source_raises(fresh_store):
    """Typos in source string fail loud rather than silently miscategorizing."""
    with pytest.raises(ValueError):
        gl_merchant_map.learn("Acme", "62300:IT Expenses", source="hunch")


# -----------------------------------------------------------------------------
# History + hit counting
# -----------------------------------------------------------------------------

def test_history_appends_each_event(fresh_store):
    """Every learn() call appends an event with source + iso."""
    gl_merchant_map.learn("Acme", "62300:IT Expenses", source="operator")
    gl_merchant_map.learn("Acme", "62300:IT Expenses", source="operator")
    gl_merchant_map.learn("Acme", "62300:IT Expenses", source="operator")
    raw = json.loads(fresh_store.read_text())
    record = next(iter(raw.values()))
    assert len(record["history"]) == 3
    for ev in record["history"]:
        assert ev["source"] == "operator"
        assert "iso" in ev


def test_history_capped_at_5(fresh_store):
    """Older events are dropped once we exceed 5 — bounded growth."""
    for _ in range(10):
        gl_merchant_map.learn("Acme", "62300:IT Expenses", source="operator")
    raw = json.loads(fresh_store.read_text())
    record = next(iter(raw.values()))
    assert len(record["history"]) == 5


def test_hit_count_increments_on_lookup(fresh_store):
    """Every successful lookup advances hit_count + last_seen."""
    gl_merchant_map.learn("Acme", "62300:IT Expenses", source="operator")
    gl_merchant_map.lookup("Acme")
    gl_merchant_map.lookup("Acme")
    gl_merchant_map.lookup("Acme")
    raw = json.loads(fresh_store.read_text())
    record = next(iter(raw.values()))
    assert record["hit_count"] == 3


def test_hit_count_does_not_advance_on_miss(fresh_store):
    """Missed lookups don't touch the store."""
    gl_merchant_map.lookup("NonExistent")
    # Store should still be empty (or non-existent)
    if fresh_store.exists():
        raw = json.loads(fresh_store.read_text())
        assert raw == {}


# -----------------------------------------------------------------------------
# forget / list_all / stats / clear
# -----------------------------------------------------------------------------

def test_forget_removes_entry(fresh_store):
    gl_merchant_map.learn("Acme", "62300:IT Expenses", source="operator")
    assert gl_merchant_map.forget("Acme") is True
    assert gl_merchant_map.lookup("Acme") is None


def test_forget_returns_false_when_not_found(fresh_store):
    assert gl_merchant_map.forget("NeverWas") is False


def test_list_all_filters_by_source(fresh_store):
    gl_merchant_map.learn("Operator-Confirmed", "62300:IT Expenses", source="operator")
    gl_merchant_map.learn("Training-Derived", "62200:Supplies", source="training")
    operators = gl_merchant_map.list_all(source="operator")
    trainings = gl_merchant_map.list_all(source="training")
    assert len(operators) == 1
    assert len(trainings) == 1
    assert operators[0]["merchant_display_name"] == "Operator-Confirmed"


def test_stats_summarizes_store(fresh_store):
    gl_merchant_map.learn("A", "62300:IT", source="operator")
    gl_merchant_map.learn("B", "62200:Supplies", source="training")
    gl_merchant_map.learn("C", "62200:Supplies", source="training")
    s = gl_merchant_map.stats()
    assert s["total_entries"] == 3
    assert s["by_source"]["operator"] == 1
    assert s["by_source"]["training"] == 2


def test_clear_returns_count_removed(fresh_store):
    gl_merchant_map.learn("A", "62300:IT", source="operator")
    gl_merchant_map.learn("B", "62200:Supplies", source="training")
    assert gl_merchant_map.clear() == 2
    assert gl_merchant_map.lookup("A") is None
    assert gl_merchant_map.lookup("B") is None


# -----------------------------------------------------------------------------
# Atomic write resilience
# -----------------------------------------------------------------------------

def test_corrupt_store_treated_as_empty(fresh_store):
    """If gl_merchant_map.json is malformed, we don't crash — we re-learn."""
    fresh_store.write_text("{ this is not valid json")
    assert gl_merchant_map.lookup("Anything") is None
    # We should still be able to learn a new entry.
    gl_merchant_map.learn("Acme", "62300:IT", source="operator")
    assert gl_merchant_map.lookup("Acme") == "62300:IT"
