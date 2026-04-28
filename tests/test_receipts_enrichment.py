# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
# See LICENSE file for terms.
"""3-tier enrichment ladder for low-confidence receipts.

Covers:
  - High-confidence receipts skip enrichment entirely (cheap path).
  - _enrich_with_maps: verify, propose, mismatch, no_location, no_match.
  - _enrich_with_web_search: applied / unavailable / merchant missing.
  - enrich_low_confidence_receipt: full ladder behavior.
  - needs_review flag prepended when both tiers fail to lift conf above 0.6.
  - notes annotation accumulates each tier's outcome.

Network and Google API are stubbed throughout; tests run offline.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

import receipts as r


def _new_rec(**overrides):
    """Build a low-conf ExtractedReceipt with sensible test defaults."""
    base = dict(
        merchant="Joe's Cafe",
        date="2026-04-26",
        total=12.50,
        currency="USD",
        confidence=0.3,
        location="123 Main St, Boston, MA 02101",
        category="Miscellaneous Expense",
        notes="LLM struggled here.",
    )
    base.update(overrides)
    return r.ExtractedReceipt(**base)


# --------------------------------------------------------------------------- #
# Skip path: high-confidence receipts must not pay enrichment cost
# --------------------------------------------------------------------------- #


def test_high_confidence_skips_enrichment():
    rec = _new_rec(confidence=0.9)
    # Make Maps and web search blow up if either is called.
    with patch.object(r, "_enrich_with_maps", side_effect=AssertionError("called!")) as m, \
         patch.object(r, "_enrich_with_web_search", side_effect=AssertionError("called!")) as w:
        out = r.enrich_low_confidence_receipt(rec)
    assert out.confidence == 0.9
    assert out.notes == "LLM struggled here."
    m.assert_not_called()
    w.assert_not_called()


# --------------------------------------------------------------------------- #
# _name_match — loose merchant comparison helper
# --------------------------------------------------------------------------- #


class TestNameMatch:
    def test_exact_match_case_insensitive(self):
        assert r._name_match("Joe's Cafe", "joe's cafe")

    def test_strips_inc_llc_pbc(self):
        assert r._name_match("Anthropic, PBC", "Anthropic")
        assert r._name_match("Anthropic", "Anthropic, PBC")
        assert r._name_match("Acme LLC", "Acme")

    def test_substring_match_either_direction(self):
        assert r._name_match(
            "The Mobile-First Company",
            "Mobile-First Company - Inc",
        )

    def test_empty_inputs_no_match(self):
        assert not r._name_match("", "Foo")
        assert not r._name_match("Foo", "")
        assert not r._name_match(None, "Foo")

    def test_short_substrings_dont_count(self):
        # 'a' and 'b' shouldn't fuzzy-match each other just because 'a' is short.
        assert not r._name_match("ab", "Hello World")


# --------------------------------------------------------------------------- #
# _enrich_with_maps
# --------------------------------------------------------------------------- #


class TestEnrichWithMaps:
    def _stub_client(self, places_results: list[dict]):
        client = MagicMock()
        client.places.return_value = {"results": places_results}
        return client

    def test_no_location_short_circuits(self):
        rec = _new_rec(location=None)
        out = r._enrich_with_maps(rec)
        assert out["applied"] is False
        assert out["reason"] == "no_location"

    def test_maps_unavailable_returns_reason(self):
        rec = _new_rec()
        with patch("gservices.maps", side_effect=RuntimeError("no key")):
            out = r._enrich_with_maps(rec)
        assert out["applied"] is False
        assert "maps_unavailable" in out["reason"]

    def test_verified_merchant_boosts_confidence(self):
        rec = _new_rec(merchant="Joe's Cafe")
        client = self._stub_client([{
            "name": "Joe's Cafe",
            "types": ["cafe", "food", "establishment"],
        }])
        with patch("gservices.maps", return_value=client):
            out = r._enrich_with_maps(rec)
        assert out["applied"] is True
        assert "verified" in out["reason"]
        assert out["confidence_delta"] == r._MAPS_BOOST
        # 'cafe' Place type collapses to 'Meals' since our
        # taxonomy doesn't split coffee shops out separately.
        assert out["category_proposal"] == "Meals"

    def test_strips_business_suffix_for_match(self):
        rec = _new_rec(merchant="Anthropic, PBC")
        client = self._stub_client([{
            "name": "Anthropic",
            "types": ["office", "establishment"],
        }])
        with patch("gservices.maps", return_value=client):
            out = r._enrich_with_maps(rec)
        assert out["applied"] is True

    def test_proposes_merchant_when_llm_had_none(self):
        rec = _new_rec(merchant=None)
        client = self._stub_client([{
            "name": "The Local Grill",
            "types": ["restaurant", "food"],
        }])
        with patch("gservices.maps", return_value=client):
            out = r._enrich_with_maps(rec)
        assert out["applied"] is True
        assert out["merchant_proposal"] == "The Local Grill"
        # Half-boost when proposing vs verifying
        assert out["confidence_delta"] == r._MAPS_BOOST / 2

    def test_mismatch_no_boost_but_records_note(self):
        rec = _new_rec(merchant="Joe's Cafe")
        client = self._stub_client([{
            "name": "Sam's Bakery",
            "types": ["bakery"],
        }])
        with patch("gservices.maps", return_value=client):
            out = r._enrich_with_maps(rec)
        assert out["applied"] is False
        assert "merchant_mismatch=Sam's Bakery" in out["reason"]

    def test_no_results_short_circuits(self):
        rec = _new_rec()
        client = self._stub_client([])
        with patch("gservices.maps", return_value=client):
            out = r._enrich_with_maps(rec)
        assert out["applied"] is False
        assert out["reason"] == "no_match"

    def test_maps_exception_is_caught(self):
        rec = _new_rec()
        client = MagicMock()
        client.places.side_effect = Exception("rate limited")
        with patch("gservices.maps", return_value=client):
            out = r._enrich_with_maps(rec)
        assert out["applied"] is False
        assert "maps_error" in out["reason"]


# --------------------------------------------------------------------------- #
# _enrich_with_web_search
# --------------------------------------------------------------------------- #


class TestEnrichWithWebSearch:
    def test_no_merchant_short_circuits(self):
        rec = _new_rec(merchant=None)
        out = r._enrich_with_web_search(rec)
        assert out["applied"] is False
        assert "no_merchant" in out["reason"]

    def test_llm_unavailable_short_circuits(self):
        rec = _new_rec()
        with patch("llm.is_available", return_value=(False, "no key")):
            out = r._enrich_with_web_search(rec)
        assert out["applied"] is False
        assert out["reason"] == "llm_unavailable"

    def test_successful_web_search_returns_category(self):
        rec = _new_rec(merchant="Snowflake Inc.")
        fake_response = {
            "text": (
                '{"business_type": "data warehouse", '
                '"expense_category": "Software Subscriptions", '
                '"confidence": 0.9, '
                '"summary": "Snowflake is a cloud data warehouse."}'
            ),
            "search_count": 2,
        }
        with patch("llm.is_available", return_value=(True, "ok")), \
             patch("llm.call_with_web_search", return_value=fake_response):
            out = r._enrich_with_web_search(rec)
        assert out["applied"] is True
        assert out["category_proposal"] == "Software Subscriptions"
        assert out["confidence_delta"] == r._WEBSEARCH_BOOST
        assert "data warehouse" in out["reason"]

    def test_unrecognized_category_dropped(self):
        """If the LLM proposes a category not in our list, we still apply
        the boost (we did get info) but don't update the category field."""
        rec = _new_rec(merchant="Some Merchant")
        fake_response = {
            "text": (
                '{"business_type": "thingy", '
                '"expense_category": "Some Made-Up Category", '
                '"confidence": 0.8, '
                '"summary": "..."}'
            ),
            "search_count": 1,
        }
        with patch("llm.is_available", return_value=(True, "ok")), \
             patch("llm.call_with_web_search", return_value=fake_response):
            out = r._enrich_with_web_search(rec)
        assert out["applied"] is True
        assert out["category_proposal"] is None  # unrecognized → don't trust

    def test_web_search_error_caught(self):
        rec = _new_rec(merchant="Foo")
        with patch("llm.is_available", return_value=(True, "ok")), \
             patch("llm.call_with_web_search", side_effect=Exception("API timeout")):
            out = r._enrich_with_web_search(rec)
        assert out["applied"] is False
        assert "web_search_error" in out["reason"]


# --------------------------------------------------------------------------- #
# Full ladder: enrich_low_confidence_receipt
# --------------------------------------------------------------------------- #


class TestFullLadder:
    def test_maps_alone_lifts_above_threshold_skips_websearch(self):
        rec = _new_rec(confidence=0.45)
        with patch.object(r, "_enrich_with_maps", return_value={
            "applied": True, "reason": "verified=Joe's Cafe",
            "confidence_delta": 0.2,
            "merchant_proposal": None,
            "category_proposal": "Meals",
        }) as m, patch.object(r, "_enrich_with_web_search") as w:
            out = r.enrich_low_confidence_receipt(rec)
        # 0.45 + 0.20 = 0.65 ≥ 0.6 → don't run websearch
        assert out.confidence >= r._ENRICHMENT_THRESHOLD
        assert "Maps:" in out.notes
        assert "[needs_review]" not in out.notes
        m.assert_called_once()
        w.assert_not_called()

    def test_maps_didnt_help_runs_websearch(self):
        rec = _new_rec(confidence=0.3)
        with patch.object(r, "_enrich_with_maps", return_value={
            "applied": False, "reason": "no_match",
        }), patch.object(r, "_enrich_with_web_search", return_value={
            "applied": True, "reason": "web_type=cafe",
            "confidence_delta": 0.15,
            "category_proposal": "Meals",
        }) as w:
            out = r.enrich_low_confidence_receipt(rec)
        # 0.3 + 0.15 = 0.45, still below 0.6 → flagged for review
        assert out.confidence == pytest.approx(0.45)
        assert out.notes.startswith("[needs_review]")
        assert "Web:" in out.notes
        w.assert_called_once()

    def test_both_tiers_lift_above_threshold(self):
        rec = _new_rec(confidence=0.3)
        with patch.object(r, "_enrich_with_maps", return_value={
            "applied": True, "reason": "proposed=Joe's Cafe",
            "confidence_delta": 0.10,
            "merchant_proposal": "Joe's Cafe",
            "category_proposal": "Meals",
        }), patch.object(r, "_enrich_with_web_search", return_value={
            "applied": True, "reason": "web_type=cafe",
            "confidence_delta": 0.20,  # one big boost just to test compounding
            "category_proposal": None,
        }):
            out = r.enrich_low_confidence_receipt(rec)
        # 0.3 + 0.10 + 0.20 = 0.6, exactly at threshold
        assert out.confidence >= r._ENRICHMENT_THRESHOLD
        assert "[needs_review]" not in out.notes

    def test_neither_tier_helps_flags_for_review(self):
        rec = _new_rec(confidence=0.3)
        with patch.object(r, "_enrich_with_maps", return_value={
            "applied": False, "reason": "no_location",
        }), patch.object(r, "_enrich_with_web_search", return_value={
            "applied": False, "reason": "llm_unavailable",
        }):
            out = r.enrich_low_confidence_receipt(rec)
        assert out.confidence == 0.3
        assert out.notes.startswith("[needs_review]")
        # Original notes preserved
        assert "LLM struggled here." in out.notes

    def test_maps_proposal_fills_missing_merchant(self):
        rec = _new_rec(merchant=None, confidence=0.3)
        with patch.object(r, "_enrich_with_maps", return_value={
            "applied": True, "reason": "proposed=The Local Grill",
            "confidence_delta": 0.1,
            "merchant_proposal": "The Local Grill",
            "category_proposal": "Meals",
        }), patch.object(r, "_enrich_with_web_search", return_value={
            "applied": False, "reason": "we don't get here in this test",
        }):
            out = r.enrich_low_confidence_receipt(rec)
        assert out.merchant == "The Local Grill"
        assert out.category == "Meals"

    def test_maps_does_not_override_set_category(self):
        """Maps proposing a category should NOT override an LLM-set one."""
        rec = _new_rec(category="Software Subscriptions", confidence=0.4)
        with patch.object(r, "_enrich_with_maps", return_value={
            "applied": True, "reason": "verified=Foo",
            "confidence_delta": 0.2,
            "merchant_proposal": None,
            "category_proposal": "Auto Expense",  # disagrees
        }), patch.object(r, "_enrich_with_web_search", return_value={
            "applied": False, "reason": "n/a",
        }):
            out = r.enrich_low_confidence_receipt(rec)
        # Confidence boosted, but original category preserved.
        assert out.category == "Software Subscriptions"

    def test_confidence_capped_at_0_95_via_maps(self):
        rec = _new_rec(confidence=0.55)
        with patch.object(r, "_enrich_with_maps", return_value={
            "applied": True, "reason": "verified",
            "confidence_delta": 0.99,  # absurd
            "merchant_proposal": None,
            "category_proposal": None,
        }):
            out = r.enrich_low_confidence_receipt(rec)
        assert out.confidence <= 0.95


# --------------------------------------------------------------------------- #
# Place type → category mapping
# --------------------------------------------------------------------------- #


class TestPlaceTypeMapping:
    def test_picks_first_known_type(self):
        # Given specific then general types, the specific should win.
        assert r._types_to_category(
            ["cafe", "establishment"],
        ) == "Meals"

    def test_returns_none_if_no_match(self):
        assert r._types_to_category(["unknown_type", "weird_thing"]) is None

    def test_handles_empty(self):
        assert r._types_to_category([]) is None
        assert r._types_to_category(None) is None

    def test_every_proposed_category_is_in_default_list(self):
        """Guard against silent bugs where a place_type maps to a category
        that doesn't exist in DEFAULT_CATEGORIES — production would happily
        write that into the cache, polluting downstream filters/exports.
        Caught a real bug where 'gas_station' → 'Auto Expense' before
        'Auto Expense' was added to the canonical list."""
        for place_type, category in r._PLACE_TYPE_TO_CATEGORY.items():
            assert category in r.DEFAULT_CATEGORIES, (
                f"Place type {place_type!r} maps to {category!r}, which is "
                f"NOT in DEFAULT_CATEGORIES. Either add the category or pick "
                f"the closest existing one."
            )

    def test_gas_station_maps_to_fuel(self):
        assert r._types_to_category(["gas_station"]) == "Auto Expense"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
