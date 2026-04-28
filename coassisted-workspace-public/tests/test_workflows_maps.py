# © 2026 CoAssisted Workspace contributors contributors. Licensed under MIT — see LICENSE.
"""Tests for Maps × CRM × Calendar workflow helpers.

These exercise the pure-Python helpers (no Google API calls) — TSP routing
logic, haversine math, address extraction, geocode cache.
"""

from unittest.mock import MagicMock, patch

import pytest

import tools.workflows as wf


def test_haversine_known_distance():
    """SFO → SJC is ~48 km."""
    sfo = (37.6213, -122.3790)
    sjc = (37.3639, -121.9289)
    d = wf._haversine_km(sfo, sjc)
    # Tolerance: hand-computed range
    assert 45 < d < 55


def test_haversine_zero_distance():
    p = (37.0, -122.0)
    assert wf._haversine_km(p, p) == pytest.approx(0.0, abs=0.01)


def test_extract_address_block_prefers_work():
    person = {
        "addresses": [
            {"type": "home", "formattedValue": "Home St"},
            {"type": "work", "formattedValue": "Work Ave",
             "city": "Cupertino", "region": "CA"},
        ]
    }
    block = wf._extract_address_block(person)
    assert block["formatted"] == "Work Ave"
    assert block["city"] == "Cupertino"
    assert block["region"] == "CA"
    assert block["type"] == "work"


def test_extract_address_block_falls_back_to_first():
    person = {
        "addresses": [
            {"formattedValue": "First", "city": "Foo"},
        ]
    }
    block = wf._extract_address_block(person)
    assert block["formatted"] == "First"


def test_extract_address_block_no_addresses():
    assert wf._extract_address_block({}) == {}
    assert wf._extract_address_block({"addresses": []}) == {}


def test_contact_lat_lng_reads_custom_fields():
    person = {
        "userDefined": [
            {"key": "lat", "value": "37.4419"},
            {"key": "lng", "value": "-122.1430"},
            {"key": "other", "value": "junk"},
        ]
    }
    assert wf._contact_lat_lng(person) == (37.4419, -122.1430)


def test_contact_lat_lng_missing_returns_none():
    assert wf._contact_lat_lng({}) is None
    assert wf._contact_lat_lng({"userDefined": []}) is None
    assert wf._contact_lat_lng({"userDefined": [{"key": "lat", "value": "abc"}]}) is None


def test_geocode_cache_hit_skips_api(monkeypatch, tmp_path):
    """Cache hit should NOT call the live Google API."""
    # Point cache at a tempfile
    cache_file = tmp_path / "geocode_cache.json"
    monkeypatch.setattr(wf, "_geocode_cache_path", lambda: cache_file)
    monkeypatch.setattr(wf, "_GEOCODE_CACHE", None)

    # Pre-populate cache
    cache_file.write_text('{"foo street": {"lat": 1.0, "lng": 2.0, '
                          '"formatted_address": "Foo St", "place_id": "p1"}}')

    # Mock gservices.maps to fail loudly if called
    fail_marker = MagicMock(side_effect=AssertionError("API should not be called on cache hit"))
    with patch.object(wf.gservices, "maps", fail_marker):
        result = wf._geocode_cached("Foo Street")  # different case to confirm normalization

    assert result is not None
    assert result["lat"] == 1.0
    assert result["lng"] == 2.0
    assert result["source"] == "cache"


def test_geocode_cache_miss_calls_api(monkeypatch, tmp_path):
    """Cache miss should call the API once and persist the result."""
    cache_file = tmp_path / "geocode_cache.json"
    monkeypatch.setattr(wf, "_geocode_cache_path", lambda: cache_file)
    monkeypatch.setattr(wf, "_GEOCODE_CACHE", None)

    fake_gmaps = MagicMock()
    fake_gmaps.geocode.return_value = [{
        "geometry": {"location": {"lat": 10.0, "lng": 20.0}},
        "formatted_address": "Bar Street, Foo City",
        "place_id": "p2",
    }]
    with patch.object(wf.gservices, "maps", return_value=fake_gmaps):
        result = wf._geocode_cached("Bar Street")

    assert result["lat"] == 10.0
    assert result["source"] == "api"
    fake_gmaps.geocode.assert_called_once_with("Bar Street")

    # Cache should now contain it.
    import json
    persisted = json.loads(cache_file.read_text())
    assert "bar street" in persisted
    assert persisted["bar street"]["lat"] == 10.0


def test_geocode_cached_handles_empty_address():
    assert wf._geocode_cached("") is None
    assert wf._geocode_cached("   ") is None


def test_resolve_current_location_modes():
    """mode=off and mode=home short-circuit to None."""
    assert wf._resolve_current_location(mode="off") is None
    assert wf._resolve_current_location(mode="home") is None
