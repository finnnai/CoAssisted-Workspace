# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for P7 — Knowledge layer (2 workflows)."""

from __future__ import annotations

import p7_workflows as p7


# --------------------------------------------------------------------------- #
# Tokenizer
# --------------------------------------------------------------------------- #


def test_tokenize_drops_stopwords():
    out = p7.tokenize("The quick brown fox jumps over the lazy dog")
    # 'the', 'over' are stopwords
    assert "the" not in out
    assert "quick" in out
    assert "brown" in out
    assert "fox" in out


def test_tokenize_lowercases():
    out = p7.tokenize("Quarterly Strategy Review")
    assert all(t == t.lower() for t in out)


def test_tokenize_skips_short_tokens():
    out = p7.tokenize("a I we Q3 quarterly")
    assert "a" not in out
    assert "i" not in out
    # Q3 has 2 chars, just makes the cut
    assert "q3" in out
    assert "quarterly" in out


# --------------------------------------------------------------------------- #
# Wiki index
# --------------------------------------------------------------------------- #


def _thread(tid: str, subject: str, body: str, **kw) -> dict:
    return {"id": tid, "subject": subject, "body": body, **kw}


def test_index_records_postings():
    threads = [
        _thread("t1", "Q3 Strategy", "We need to think about pricing and product roadmap for Q3."),
        _thread("t2", "Lunch?", "Want to grab lunch tomorrow?"),
    ]
    idx = p7.build_wiki_index(threads)
    assert idx.total_threads == 2
    assert "strategy" in idx.postings
    assert "lunch" in idx.postings


def test_search_finds_relevant_thread():
    threads = [
        _thread("t1", "Q3 Strategy",
                "Pricing and product roadmap discussion for Q3 strategy review."),
        _thread("t2", "Lunch?", "Want to grab lunch tomorrow?"),
        _thread("t3", "Renewal terms",
                "Locking in renewal terms with the customer this week."),
    ]
    idx = p7.build_wiki_index(threads)
    results = p7.search_wiki(idx, "Q3 strategy")
    assert results
    assert results[0].thread_id == "t1"
    assert "strategy" in results[0].matched_terms or "q3" in results[0].matched_terms


def test_search_returns_empty_for_no_match():
    threads = [_thread("t1", "Lunch", "Want to grab lunch?")]
    idx = p7.build_wiki_index(threads)
    results = p7.search_wiki(idx, "kubernetes deployment")
    assert results == []


def test_search_ranks_by_relevance():
    threads = [
        _thread("t1", "Strategy review", "Quick mention of strategy."),
        _thread("t2", "Strategy strategy strategy",
                "Strategy strategy strategy strategy strategy"),
    ]
    idx = p7.build_wiki_index(threads)
    results = p7.search_wiki(idx, "strategy")
    assert results[0].thread_id == "t2"


def test_search_subject_weighted_higher():
    """Term in subject should rank higher than term only in body."""
    threads = [
        _thread("t_subject", "Pricing review", "Brief talk about timing."),
        _thread("t_body_only", "Generic update",
                "This thread mentions pricing once buried in a long body."),
    ]
    idx = p7.build_wiki_index(threads)
    results = p7.search_wiki(idx, "pricing")
    # Subject hit should rank first thanks to 3x weighting
    assert results[0].thread_id == "t_subject"


def test_search_returns_snippet_around_match():
    body = (
        "This is a long thread body. " * 5
        + "The Q3 STRATEGY mention is here in the middle. "
        + "More text after. " * 5
    )
    threads = [_thread("t1", "Random subject", body)]
    idx = p7.build_wiki_index(threads)
    results = p7.search_wiki(idx, "strategy", snippet_chars=100)
    assert results
    assert "STRATEGY" in results[0].snippet or "strategy" in results[0].snippet.lower()


def test_search_carries_link_and_timestamp():
    threads = [
        _thread("t1", "X", "Y lots of words here",
                timestamp="2026-04-28", link="https://m/t1"),
    ]
    idx = p7.build_wiki_index(threads)
    results = p7.search_wiki(idx, "words")
    assert results[0].link == "https://m/t1"
    assert results[0].timestamp == "2026-04-28"


# --------------------------------------------------------------------------- #
# Doc diff
# --------------------------------------------------------------------------- #


def test_diff_no_changes_minor():
    d = p7.diff_doc_text("hello\nworld", "hello\nworld")
    assert d.severity == "minor"
    assert d.summary_bullets == ["No substantive changes."]


def test_diff_added_lines():
    d = p7.diff_doc_text(
        "line one",
        "line one\nline two\nline three",
    )
    assert len(d.lines_added) >= 2
    assert d.severity == "minor"


def test_diff_removed_lines():
    d = p7.diff_doc_text("a\nb\nc", "a")
    assert len(d.lines_removed) == 2


def test_diff_modified_lines():
    d = p7.diff_doc_text("Hello world", "Hello universe")
    assert len(d.lines_modified) == 1
    b, a = d.lines_modified[0]
    assert "world" in b and "universe" in a


def test_diff_severity_major_for_long_lines():
    long_before = "x" * 150
    long_after = "y" * 150
    d = p7.diff_doc_text(long_before, long_after)
    assert d.severity == "major"


def test_diff_severity_moderate_for_5_to_25_changes():
    before = "\n".join(f"line {i}" for i in range(20))
    after = "\n".join(f"changed {i}" for i in range(20))
    d = p7.diff_doc_text(before, after)
    assert d.severity in {"moderate", "major"}


def test_diff_summary_includes_examples():
    d = p7.diff_doc_text(
        "old version of section A",
        "new version of section A",
    )
    assert any("Changed" in s for s in d.summary_bullets)
