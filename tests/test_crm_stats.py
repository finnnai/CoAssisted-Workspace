"""Tests for crm_stats.py — pure-function helpers, no Gmail/People calls."""

import config
import crm_stats


def _set_window(days: int):
    """Force a window config value for a single test."""
    config._cache = {**config._DEFAULTS, "crm_window_days": days}


def teardown_function(_):
    config._cache = None  # reset so other tests see defaults


def test_current_managed_keys_default():
    _set_window(60)
    last, sent, received = crm_stats.current_managed_keys()
    assert last == "Last Interaction"
    assert sent == "Sent, last 60"
    assert received == "Received, last 60"


def test_current_managed_keys_custom_window():
    _set_window(30)
    _, sent, received = crm_stats.current_managed_keys()
    assert sent == "Sent, last 30"
    assert received == "Received, last 30"


def test_is_managed_key_matches_current():
    _set_window(30)
    assert crm_stats.is_managed_key("Sent, last 30")
    assert crm_stats.is_managed_key("Received, last 30")


def test_is_managed_key_matches_stale_window():
    """When window is 30, a stale 'Sent, last 60' should still count as managed."""
    _set_window(30)
    assert crm_stats.is_managed_key("Sent, last 60")
    assert crm_stats.is_managed_key("Received, last 180")


def test_is_managed_key_rejects_unrelated():
    assert not crm_stats.is_managed_key("stage")
    assert not crm_stats.is_managed_key("tier")
    assert not crm_stats.is_managed_key("Last")  # substring, not match


def test_managed_keys_shim_contains():
    """The MANAGED_KEYS tuple is a shim that delegates `in` to is_managed_key()."""
    assert "Sent, last 30" in crm_stats.MANAGED_KEYS
    assert "stage" not in crm_stats.MANAGED_KEYS


def test_merge_preserves_non_managed():
    _set_window(60)
    existing = [
        {"key": "stage", "value": "prospect"},
        {"key": "Sent, last 60", "value": "stale"},
    ]
    stats = {
        "Last Interaction": "Sent - 2026-04-23 - 14:32",
        "Sent, last 60": "+5",
        "Received, last 60": "+3",
    }
    merged = crm_stats.merge_managed_into_userdefined(existing, stats)
    keys = [e["key"] for e in merged]
    assert "stage" in keys
    # Only one "Sent, last 60" entry (the fresh one).
    assert keys.count("Sent, last 60") == 1
    assert next(e["value"] for e in merged if e["key"] == "Sent, last 60") == "+5"


def test_merge_wipes_stale_window_keys():
    """Switching from 60 to 30 should remove stale 'Sent, last 60' even with new keys absent from stats."""
    _set_window(30)
    existing = [
        {"key": "Sent, last 60", "value": "+99"},
        {"key": "Received, last 60", "value": "+99"},
    ]
    stats = {
        "Last Interaction": "Sent - 2026-04-23 - 14:32",
        "Sent, last 30": "+5",
        "Received, last 30": "+3",
    }
    merged = crm_stats.merge_managed_into_userdefined(existing, stats)
    keys = {e["key"] for e in merged}
    assert "Sent, last 60" not in keys
    assert "Received, last 60" not in keys
    assert "Sent, last 30" in keys
    assert "Received, last 30" in keys


def test_strip_managed_removes_all_window_variants():
    stripped = crm_stats.strip_managed_from_userdefined(
        [
            {"key": "stage", "value": "prospect"},
            {"key": "Sent, last 60", "value": "+5"},
            {"key": "Received, last 90", "value": "+12"},
            {"key": "Last Interaction", "value": "..."},
        ]
    )
    assert stripped == [{"key": "stage", "value": "prospect"}]


def test_window_days_clamps_too_small():
    _set_window(0)
    assert crm_stats._window_days() == 1


def test_window_days_clamps_too_large():
    _set_window(9999)
    assert crm_stats._window_days() == 3650


def test_window_days_bad_input_falls_back():
    config._cache = {**config._DEFAULTS, "crm_window_days": "not a number"}
    assert crm_stats._window_days() == 60
