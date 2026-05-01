# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Unit tests for gl_memo_classifier.py.

Covers tokenization, scoring math, confidence-band logic, and the
graceful "no index trained" path. End-to-end accuracy checks against
the real 17k-row JE corpus live in tests/test_gl_classifier_e2e.py
(network-style integration test, not run in test-fast).
"""

from __future__ import annotations

import json

import pytest

import gl_memo_classifier as gmc


@pytest.fixture
def fresh_index(tmp_path, monkeypatch):
    """Redirect the module's _INDEX_PATH to a clean temp file per test."""
    fake = tmp_path / "gl_memo_index.json"
    monkeypatch.setattr(gmc, "_INDEX_PATH", fake)
    gmc._reset_for_test()
    yield fake
    gmc._reset_for_test()


def _write_minimal_index(path, gl_priors, tokens_by_gl):
    """Persist a synthetic index for unit-test scoring."""
    gl_token_totals = {
        gl: sum(toks.values()) for gl, toks in tokens_by_gl.items()
    }
    vocab: set[str] = set()
    for toks in tokens_by_gl.values():
        vocab.update(toks.keys())
    n_documents = sum(gl_priors.values())
    index = {
        "trained_on": "2026-05-01T22:00:00-07:00",
        "n_documents": n_documents,
        "vocab_size": len(vocab),
        "gl_priors": gl_priors,
        "gl_token_totals": gl_token_totals,
        "tokens_by_gl": tokens_by_gl,
    }
    path.write_text(json.dumps(index))


# -----------------------------------------------------------------------------
# Tokenization
# -----------------------------------------------------------------------------

def test_tokenize_strips_stopwords():
    """Common English stopwords drop out before scoring."""
    toks = gmc.tokenize("To record the rent payment for office")
    assert "to" not in toks
    assert "the" not in toks
    assert "for" not in toks
    assert "rent" in toks
    assert "payment" in toks
    assert "office" in toks


def test_tokenize_dedupes():
    """Repeated tokens count once — bag-of-words representation."""
    toks = gmc.tokenize("travel travel travel")
    assert toks == ["travel"]


def test_tokenize_drops_short_tokens():
    """Single-letter tokens carry no signal."""
    toks = gmc.tokenize("a b c long enough")
    assert "a" not in toks
    assert "b" not in toks
    assert "long" in toks
    assert "enough" in toks


def test_tokenize_lowercases():
    assert gmc.tokenize("RENT") == ["rent"]
    assert gmc.tokenize("Marriott") == ["marriott"]


def test_tokenize_handles_empty():
    assert gmc.tokenize("") == []
    assert gmc.tokenize(None) == []  # type: ignore[arg-type]


def test_tokenize_strips_punctuation():
    """Periods, dashes, slashes don't end up as tokens."""
    toks = gmc.tokenize("AMEX - Vendor.com / Office")
    assert "amex" in toks
    assert "vendor" in toks  # "vendor.com" splits on '.'
    assert "com" in toks
    assert "office" in toks


# -----------------------------------------------------------------------------
# No-index graceful path
# -----------------------------------------------------------------------------

def test_lookup_returns_empty_when_untrained(fresh_index):
    """No gl_memo_index.json on disk → return empty list, don't crash."""
    assert gmc.lookup_by_memo("anything goes here") == []


def test_index_status_reports_untrained(fresh_index):
    status = gmc.index_status()
    assert status["status"] == "untrained"
    assert "scripts/train_gl_memo_classifier.py" in status["message"]


def test_lookup_with_empty_memo_returns_empty(fresh_index):
    """Empty memo + trained index = no results (no tokens to score)."""
    _write_minimal_index(
        fresh_index,
        gl_priors={"62300:IT Expenses": 10},
        tokens_by_gl={"62300:IT Expenses": {"office": 10}},
    )
    assert gmc.lookup_by_memo("") == []


# -----------------------------------------------------------------------------
# Scoring + ranking
# -----------------------------------------------------------------------------

def test_lookup_ranks_obvious_winner_first(fresh_index):
    """When one GL clearly dominates the memo's tokens, it ranks first."""
    _write_minimal_index(
        fresh_index,
        gl_priors={
            "53000:Travel - COS": 100,
            "62300:IT Expenses": 100,
        },
        tokens_by_gl={
            "53000:Travel - COS": {"hotel": 50, "flight": 40, "marriott": 30},
            "62300:IT Expenses": {"laptop": 20, "monitor": 15, "saas": 10},
        },
    )
    results = gmc.lookup_by_memo("Marriott hotel for travel")
    assert results
    assert results[0][0] == "53000:Travel - COS"


def test_lookup_returns_normalized_scores(fresh_index):
    """Top score is 1.0; runner-ups in [0, 1]."""
    _write_minimal_index(
        fresh_index,
        gl_priors={"A:foo": 50, "B:bar": 50},
        tokens_by_gl={
            "A:foo": {"alpha": 20, "beta": 5},
            "B:bar": {"gamma": 10, "delta": 8},
        },
    )
    results = gmc.lookup_by_memo("alpha beta gamma")
    assert results[0][1] == pytest.approx(1.0)
    for _gl, score in results:
        assert 0.0 <= score <= 1.0


def test_lookup_respects_top_k(fresh_index):
    """top_k caps the result length."""
    _write_minimal_index(
        fresh_index,
        gl_priors={f"{n}:gl": 10 for n in (50000, 51000, 52000, 53000, 54000)},
        tokens_by_gl={
            f"{n}:gl": {"common": 10, f"unique{n}": 5}
            for n in (50000, 51000, 52000, 53000, 54000)
        },
    )
    results = gmc.lookup_by_memo("common", top_k=2)
    assert len(results) == 2


# -----------------------------------------------------------------------------
# Confidence band
# -----------------------------------------------------------------------------

def test_confidence_medium_on_clear_winner():
    """Big gap between top-1 and top-2 → MEDIUM confidence."""
    results = [("A:foo", 1.0), ("B:bar", 0.5), ("C:baz", 0.1)]
    assert gmc.confidence_from_top_two(results, gap_threshold=0.30) == "medium"


def test_confidence_low_on_close_call():
    """Small gap → LOW; caller should consult tier 3."""
    results = [("A:foo", 1.0), ("B:bar", 0.95), ("C:baz", 0.1)]
    assert gmc.confidence_from_top_two(results, gap_threshold=0.30) == "low"


def test_confidence_low_with_single_candidate():
    """One result by itself can't be 'a clear winner over what?'"""
    assert gmc.confidence_from_top_two([("A:foo", 1.0)]) == "low"


def test_confidence_low_with_empty():
    assert gmc.confidence_from_top_two([]) == "low"


# -----------------------------------------------------------------------------
# Resilience
# -----------------------------------------------------------------------------

def test_corrupt_index_treated_as_untrained(fresh_index):
    """Malformed JSON in gl_memo_index.json doesn't crash the classifier."""
    fresh_index.write_text("{ this is not valid json")
    gmc._reset_for_test()
    assert gmc.lookup_by_memo("anything") == []
    assert gmc.index_status()["status"] == "untrained"
