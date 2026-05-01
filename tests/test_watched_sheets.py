# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for the watched-sheet rules registry."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

import watched_sheets as ws


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    ws._override_path_for_tests(tmp_path / "watched.json")
    yield
    ws._override_path_for_tests(
        Path(__file__).resolve().parent.parent / "watched_sheets.json",
    )


def test_register_then_get():
    ws.register("license", "ny-armed", fields={"expires_at": "2026-12-31"})
    rec = ws.get("license", "ny-armed")
    assert rec is not None
    assert rec["family"] == "license"
    assert rec["fields"]["expires_at"] == "2026-12-31"


def test_register_idempotent_updates():
    ws.register("license", "ny-armed", fields={"expires_at": "2026-12-31"})
    ws.register("license", "ny-armed", fields={"expires_at": "2027-12-31"})
    rec = ws.get("license", "ny-armed")
    assert rec["fields"]["expires_at"] == "2027-12-31"


def test_register_requires_family_and_slug():
    with pytest.raises(ValueError):
        ws.register("", "x")
    with pytest.raises(ValueError):
        ws.register("license", "")


def test_list_family_filters():
    ws.register("license", "a", fields={})
    ws.register("license", "b", fields={})
    ws.register("retention", "c", fields={})
    assert len(ws.list_family("license")) == 2
    assert len(ws.list_family("retention")) == 1


def test_list_active_only():
    ws.register("license", "a", active=True)
    ws.register("license", "b", active=False)
    assert len(ws.list_family("license", active_only=True)) == 1
    assert len(ws.list_family("license", active_only=False)) == 2


def test_update_fields_merges():
    ws.register("license", "a", fields={"k1": 1, "k2": 2})
    ws.update_fields("license", "a", k2=99, k3=3)
    rec = ws.get("license", "a")
    assert rec["fields"] == {"k1": 1, "k2": 99, "k3": 3}


def test_update_unknown_returns_none():
    assert ws.update_fields("license", "nonexistent", x=1) is None


def test_remove_returns_true_when_present():
    ws.register("license", "a")
    assert ws.remove("license", "a") is True
    assert ws.remove("license", "a") is False


def test_deactivate_keeps_record():
    ws.register("license", "a", active=True)
    ws.deactivate("license", "a")
    rec = ws.get("license", "a")
    assert rec is not None
    assert rec["active"] is False


def test_clear_family_only():
    ws.register("license", "a")
    ws.register("retention", "b")
    n = ws.clear("license")
    assert n == 1
    assert ws.get("license", "a") is None
    assert ws.get("retention", "b") is not None


# --------------------------------------------------------------------------- #
# licenses_expiring helper
# --------------------------------------------------------------------------- #


def test_licenses_expiring_within_window():
    today = _dt.date(2026, 1, 1)
    ws.register("license", "a", fields={"expires_at": "2026-02-15"})  # 45d
    ws.register("license", "b", fields={"expires_at": "2026-06-01"})  # 151d (out)
    ws.register("license", "c", fields={"expires_at": "2025-12-15"})  # past
    out = ws.licenses_expiring(window_days=90, today=today)
    slugs = [r["slug"] for r in out]
    assert "a" in slugs
    assert "c" in slugs   # past licenses still flagged (negative days_until_expiry)
    assert "b" not in slugs


def test_licenses_expiring_skips_inactive():
    today = _dt.date(2026, 1, 1)
    ws.register("license", "a", fields={"expires_at": "2026-02-15"}, active=False)
    out = ws.licenses_expiring(window_days=90, today=today)
    assert out == []


def test_licenses_expiring_sorted_soonest_first():
    today = _dt.date(2026, 1, 1)
    ws.register("license", "later", fields={"expires_at": "2026-03-01"})
    ws.register("license", "sooner", fields={"expires_at": "2026-01-30"})
    out = ws.licenses_expiring(window_days=90, today=today)
    assert out[0]["slug"] == "sooner"
