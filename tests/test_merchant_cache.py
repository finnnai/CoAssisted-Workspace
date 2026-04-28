# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
# See LICENSE file for terms.
"""Persistent merchant cache (Tier 0 of the enrichment ladder).

Covers:
  - Cache lookup misses, hits, expiry.
  - Update upsert behavior: new entries, manual override authority, source-
    based field-fill rules.
  - apply_correction (manual) and forget operations.
  - Cache hit boost varies by source.
  - Atomic write resilience: corrupt JSON returns empty cache cleanly.
  - Integration with enrich_low_confidence_receipt: cache hit short-circuits
    paid tiers; Tier 2/3 successes write back.

Each test uses a tempdir-scoped cache file via _override_path_for_tests.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import merchant_cache as mc
import receipts as r


@pytest.fixture
def isolated_cache():
    """Expose the cache path the conftest fixture set up, for tests that need
    to plant data directly via file write (e.g. expired-entry tests)."""
    yield mc._CACHE_PATH


# --------------------------------------------------------------------------- #
# Lookup
# --------------------------------------------------------------------------- #


def test_lookup_empty_cache_returns_none():
    assert mc.lookup("Anthropic") is None


def test_lookup_after_update_finds_entry():
    mc.update(
        "Anthropic",
        display_name="Anthropic",
        category="Software Subscriptions",
        source="web_search",
        confidence=0.85,
    )
    found = mc.lookup("Anthropic")
    assert found is not None
    assert found["category"] == "Software Subscriptions"
    assert found["source"] == "web_search"


def test_lookup_normalizes_keys():
    """'Anthropic, PBC', 'anthropic', 'Anthropic' all collapse to one key."""
    mc.update("Anthropic, PBC", category="X", source="maps")
    assert mc.lookup("Anthropic") is not None
    assert mc.lookup("anthropic") is not None
    assert mc.lookup("ANTHROPIC, PBC") is not None


def test_lookup_returns_defensive_copy():
    """Mutating the returned dict must not corrupt the cache."""
    mc.update("Foo", category="Bar", source="maps")
    found = mc.lookup("Foo")
    found["category"] = "POISONED"
    again = mc.lookup("Foo")
    assert again["category"] == "Bar"


def test_lookup_expired_entries_filtered(isolated_cache):
    """Manually plant an entry with an old last_seen so we can test TTL."""
    old_iso = (
        _dt.datetime.now().astimezone() - _dt.timedelta(days=400)
    ).isoformat(timespec="seconds")
    isolated_cache.write_text(json.dumps({
        "stale_merchant": {
            "display_name": "Stale Inc",
            "category": "X",
            "source": "maps",
            "first_seen": old_iso,
            "last_seen": old_iso,
            "hit_count": 5,
        }
    }))
    assert mc.lookup("Stale Inc") is None  # expired ⇒ filtered


def test_lookup_empty_name_returns_none():
    mc.update("Foo", category="X", source="maps")
    assert mc.lookup("") is None
    assert mc.lookup(None) is None


# --------------------------------------------------------------------------- #
# Update — source authority rules
# --------------------------------------------------------------------------- #


def test_first_update_creates_record():
    rec = mc.update("Joe's Cafe", category="Meals", source="maps")
    assert rec["category"] == "Meals"
    assert rec["source"] == "maps"
    assert rec["first_seen"] == rec["last_seen"]
    assert rec["hit_count"] == 0


def test_non_manual_update_does_not_overwrite_existing_fields():
    mc.update("Foo", category="Original", source="web_search")
    mc.update("Foo", category="LATER GUESS", source="maps")
    found = mc.lookup("Foo")
    # Maps should NOT overwrite an existing category that web_search set.
    assert found["category"] == "Original"


def test_manual_correction_overwrites_existing_fields():
    mc.update("Foo", category="Wrong Category", source="web_search")
    mc.apply_correction("Foo", category="Software Subscriptions")
    found = mc.lookup("Foo")
    assert found["category"] == "Software Subscriptions"
    assert found["source"] == "manual_correction"
    assert found["confidence"] == 0.95


def test_history_keeps_last_5_events():
    for i in range(8):
        mc.update("Foo", category="X", source="maps")
    found = mc.lookup("Foo")
    assert len(found["history"]) == 5


# --------------------------------------------------------------------------- #
# record_hit
# --------------------------------------------------------------------------- #


def test_record_hit_increments_count_and_last_seen():
    mc.update("Foo", category="X", source="maps")
    first = mc.lookup("Foo")
    assert first["hit_count"] == 0
    mc.record_hit("Foo")
    mc.record_hit("Foo")
    after = mc.lookup("Foo")
    assert after["hit_count"] == 2
    # last_seen should be at-or-after first_seen
    assert after["last_seen"] >= first["last_seen"]


def test_record_hit_on_unknown_merchant_silent_noop():
    """Should not raise, and should not create a phantom entry."""
    mc.record_hit("DoesNotExist")
    assert mc.lookup("DoesNotExist") is None


# --------------------------------------------------------------------------- #
# boost_for — different sources grant different boosts
# --------------------------------------------------------------------------- #


class TestBoostFor:
    def test_manual_correction_strongest(self):
        assert mc.boost_for({"source": "manual_correction"}) == mc._CACHE_HIT_BOOST_MANUAL

    def test_web_search_boost(self):
        assert mc.boost_for({"source": "web_search"}) == mc._CACHE_HIT_BOOST_WEBSEARCH

    def test_maps_default_floor(self):
        assert mc.boost_for({"source": "maps"}) == mc._CACHE_HIT_BOOST_MAPS

    def test_unknown_source_falls_to_floor(self):
        assert mc.boost_for({"source": "weird"}) == mc._CACHE_HIT_BOOST_MAPS
        assert mc.boost_for({}) == mc._CACHE_HIT_BOOST_MAPS


# --------------------------------------------------------------------------- #
# forget
# --------------------------------------------------------------------------- #


def test_forget_removes_entry():
    mc.update("Foo", category="X", source="maps")
    assert mc.forget("Foo") is True
    assert mc.lookup("Foo") is None


def test_forget_returns_false_for_unknown():
    assert mc.forget("NeverExisted") is False


def test_forget_uses_normalized_key():
    mc.update("Anthropic, PBC", category="X", source="maps")
    assert mc.forget("anthropic") is True
    assert mc.lookup("Anthropic, PBC") is None


# --------------------------------------------------------------------------- #
# list_all + stats
# --------------------------------------------------------------------------- #


def test_list_all_sorts_by_hit_count_desc():
    mc.update("A", category="X", source="maps")
    mc.update("B", category="X", source="maps")
    mc.update("C", category="X", source="maps")
    for _ in range(5): mc.record_hit("B")
    for _ in range(2): mc.record_hit("C")
    out = mc.list_all(sort_by="hit_count")
    names = [r["display_name"] for r in out]
    assert names == ["B", "C", "A"]


def test_list_all_excludes_expired_by_default(isolated_cache):
    fresh_iso = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    old_iso = (
        _dt.datetime.now().astimezone() - _dt.timedelta(days=400)
    ).isoformat(timespec="seconds")
    isolated_cache.write_text(json.dumps({
        "fresh": {"display_name": "Fresh", "category": "X", "source": "maps",
                  "first_seen": fresh_iso, "last_seen": fresh_iso, "hit_count": 1},
        "stale": {"display_name": "Stale", "category": "X", "source": "maps",
                  "first_seen": old_iso, "last_seen": old_iso, "hit_count": 5},
    }))
    out = mc.list_all()
    names = [r["display_name"] for r in out]
    assert names == ["Fresh"]
    out_with_expired = mc.list_all(include_expired=True)
    assert len(out_with_expired) == 2


def test_stats_summarizes_cache():
    mc.update("A", category="X", source="maps")
    mc.update("B", category="X", source="web_search")
    mc.update("C", category="X", source="manual_correction")
    s = mc.stats()
    assert s["total_merchants"] == 3
    assert s["by_source"] == {
        "maps": 1, "web_search": 1, "manual_correction": 1,
    }


# --------------------------------------------------------------------------- #
# Atomic write resilience
# --------------------------------------------------------------------------- #


def test_corrupt_cache_file_returns_empty(isolated_cache):
    """A bad JSON file should not crash the orchestrator; treat as empty
    cache and let it heal on the next write."""
    isolated_cache.write_text("{ this is not valid json")
    assert mc.lookup("Anything") is None
    # Subsequent write should overwrite the corrupt file cleanly.
    mc.update("Foo", category="X", source="maps")
    assert mc.lookup("Foo") is not None


def test_clear_drops_everything():
    mc.update("A", category="X", source="maps")
    mc.update("B", category="X", source="maps")
    n = mc.clear()
    assert n == 2
    assert mc.list_all() == []


# --------------------------------------------------------------------------- #
# Integration: enrich_low_confidence_receipt + cache
# --------------------------------------------------------------------------- #


def _new_low_conf_rec(**overrides):
    base = dict(
        merchant="Snowflake",
        date="2026-04-26",
        total=180.0,
        currency="USD",
        confidence=0.3,
        location="San Mateo, CA",
        category="Miscellaneous Expense",
    )
    base.update(overrides)
    return r.ExtractedReceipt(**base)


def test_cache_hit_alone_lifts_above_threshold_skips_paid():
    """When the cache boost is enough to cross 0.6, paid tiers don't run."""
    mc.update(
        "Snowflake", category="Software Subscriptions",
        source="manual_correction",  # +0.25 boost
        confidence=0.95,
    )
    rec = _new_low_conf_rec(confidence=0.45)  # 0.45 + 0.25 = 0.70 ≥ 0.6
    with patch.object(r, "_enrich_with_maps") as m, \
         patch.object(r, "_enrich_with_web_search") as w:
        out = r.enrich_low_confidence_receipt(rec)
    assert out.confidence >= r._ENRICHMENT_THRESHOLD
    assert out.category == "Software Subscriptions"
    assert "Cache:" in out.notes
    m.assert_not_called()
    w.assert_not_called()


def test_cache_hit_below_threshold_still_runs_paid_tiers():
    """Cache hit with small boost — paid tiers still run if needed."""
    mc.update("Snowflake", category="Software Subscriptions", source="maps")
    rec = _new_low_conf_rec(confidence=0.30)  # 0.30 + 0.20 = 0.50 still <0.6
    with patch.object(r, "_enrich_with_maps", return_value={
        "applied": False, "reason": "no_match",
    }) as m, patch.object(r, "_enrich_with_web_search", return_value={
        "applied": True, "reason": "web_type=SaaS",
        "confidence_delta": 0.15, "category_proposal": None,
    }) as w:
        out = r.enrich_low_confidence_receipt(rec)
    m.assert_called_once()
    w.assert_called_once()


def test_maps_success_writes_cache():
    """After a real Maps verification, the merchant is cached for next time."""
    rec = _new_low_conf_rec(merchant="Joe's Cafe")
    with patch.object(r, "_enrich_with_maps", return_value={
        "applied": True, "reason": "verified=Joe's Cafe",
        "confidence_delta": 0.20,
        "merchant_proposal": None,
        "category_proposal": "Meals",
        "maps_name": "Joe's Cafe",
    }):
        r.enrich_low_confidence_receipt(rec)
    cached = mc.lookup("Joe's Cafe")
    assert cached is not None
    assert cached["source"] == "maps"
    assert cached["category"] == "Meals"


def test_web_search_success_writes_cache():
    rec = _new_low_conf_rec(merchant="Snowflake")
    with patch.object(r, "_enrich_with_maps", return_value={
        "applied": False, "reason": "no_match",
    }), patch.object(r, "_enrich_with_web_search", return_value={
        "applied": True, "reason": "web_type=data warehouse; summary=...",
        "confidence_delta": 0.15,
        "category_proposal": "Software Subscriptions",
    }):
        r.enrich_low_confidence_receipt(rec)
    cached = mc.lookup("Snowflake")
    assert cached is not None
    assert cached["source"] == "web_search"
    assert cached["business_type"] == "data warehouse"
    assert cached["category"] == "Software Subscriptions"


def test_record_hit_called_on_cache_match():
    mc.update("Foo", category="X", source="maps")
    rec = _new_low_conf_rec(merchant="Foo")
    with patch.object(r, "_enrich_with_maps", return_value={
        "applied": False, "reason": "no_match",
    }), patch.object(r, "_enrich_with_web_search", return_value={
        "applied": False, "reason": "n/a",
    }):
        r.enrich_low_confidence_receipt(rec)
    assert mc.lookup("Foo")["hit_count"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
