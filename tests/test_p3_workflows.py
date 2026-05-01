# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for P3 workflows."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

import external_feeds as ef
import p3_workflows as p3
import watched_sheets as ws


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    ef._override_cache_path_for_tests(tmp_path / "ef.json")
    ws._override_path_for_tests(tmp_path / "ws.json")
    ef.unfreeze()
    yield
    ef.unfreeze()
    ef._override_cache_path_for_tests(
        Path(__file__).resolve().parent.parent / "external_feeds_cache.json",
    )
    ws._override_path_for_tests(
        Path(__file__).resolve().parent.parent / "watched_sheets.json",
    )


# --------------------------------------------------------------------------- #
# #62 Per-diem
# --------------------------------------------------------------------------- #


def test_per_diem_single_day():
    """Day-trip is 75% M&IE, no lodging."""
    pd = p3.calculate_per_diem("San Francisco", "CA",
                               "2026-05-01", "2026-05-01", year=2026)
    assert pd.nights == 0
    assert pd.lodging_total == 0.0
    assert pd.meals_total > 0


def test_per_diem_multi_day():
    """3-night trip: 3 lodging × rate, meals = 0.75*rate*2 + rate*2 (full inner days)."""
    pd = p3.calculate_per_diem("San Francisco", "CA",
                               "2026-05-01", "2026-05-04", year=2026)
    assert pd.nights == 3
    assert pd.lodging_total > 0
    assert pd.travel_days == 2


def test_per_diem_unknown_city_uses_default():
    pd = p3.calculate_per_diem("Tinytown", "ZZ",
                               "2026-05-01", "2026-05-02", year=2026)
    assert pd.lodging_total > 0  # default CONUS rate applies


def test_per_diem_invalid_dates():
    with pytest.raises(ValueError):
        p3.calculate_per_diem("San Francisco", "CA",
                              "2026-05-04", "2026-05-01")


# --------------------------------------------------------------------------- #
# #61 Mileage
# --------------------------------------------------------------------------- #


def test_compute_mileage_with_drive_blocks():
    blocks = [
        {"date": "2026-04-28", "distance_miles": 12.5, "note": "to client"},
        {"date": "2026-04-28", "distance_miles": 10.0, "note": "back"},
        {"date": "2026-04-29", "distance_miles": 50.0},
    ]
    entries = p3.compute_mileage(blocks, year=2026)
    assert len(entries) == 3
    assert all(e.rate_per_mile == 0.72 for e in entries)
    # First entry: 12.5 * 0.72 = 9.0
    assert entries[0].deduction_usd == 9.0


def test_compute_mileage_skips_zero():
    blocks = [{"date": "2026-04-28", "distance_miles": 0}]
    entries = p3.compute_mileage(blocks, year=2026)
    assert entries == []


def test_aggregate_mileage_quarterly():
    blocks = [
        {"date": "2026-01-15", "distance_miles": 100},  # Q1
        {"date": "2026-04-01", "distance_miles": 200},  # Q2
        {"date": "2026-07-15", "distance_miles": 50},   # Q3
    ]
    entries = p3.compute_mileage(blocks, year=2026)
    agg = p3.aggregate_mileage(entries)
    assert agg["total_miles"] == 350
    assert agg["total_deduction_usd"] == 252.0  # 350 * 0.72
    assert "2026-Q1" in agg["by_quarter"]
    assert agg["by_quarter"]["2026-Q1"]["miles"] == 100


# --------------------------------------------------------------------------- #
# #36 License reminders
# --------------------------------------------------------------------------- #


def test_licenses_to_remind_assigns_threshold():
    today = _dt.date(2026, 1, 1)
    ws.register("license", "ny", fields={"expires_at": "2026-02-15", "name": "NY Armed"})
    ws.register("license", "fl", fields={"expires_at": "2026-01-25", "name": "FL Armed"})
    rows = p3.licenses_to_remind(today=today)
    by_slug = {r["slug"]: r for r in rows}
    # NY: 45d → bucket = 60
    assert by_slug["ny"]["crossed_threshold"] == 60
    # FL: 24d → bucket = 30
    assert by_slug["fl"]["crossed_threshold"] == 30


def test_licenses_to_remind_skips_far_future():
    today = _dt.date(2026, 1, 1)
    ws.register("license", "x", fields={"expires_at": "2027-12-31"})
    rows = p3.licenses_to_remind(today=today)
    assert rows == []


# --------------------------------------------------------------------------- #
# #47 DSR
# --------------------------------------------------------------------------- #


def test_dsr_collate_aggregates_all_sources():
    report = p3.collate_dsr_results(
        "alice@x.com",
        gmail_threads=[{"id": "t1", "subject": "Hi", "date": "2026-01-01"}],
        calendar_events=[{"id": "e1", "summary": "Sync",
                          "start": {"dateTime": "2026-01-15T10:00:00"}}],
        drive_files=[{"id": "f1", "name": "Notes.pdf",
                      "modifiedTime": "2026-01-20T00:00:00Z",
                      "webViewLink": "https://drv/f1"}],
        contacts=[{"email": "alice@x.com", "name": "Alice"}],
    )
    assert report["summary"]["total"] == 4
    assert report["summary"]["gmail"] == 1
    assert report["summary"]["calendar"] == 1
    assert report["summary"]["drive"] == 1
    assert report["summary"]["contacts"] == 1


def test_dsr_render_markdown_groups_by_source():
    report = p3.collate_dsr_results(
        "alice@x.com",
        gmail_threads=[{"id": "t1", "subject": "Hi", "date": "2026-01-01"}],
    )
    md = p3.render_dsr_markdown(report)
    assert "alice@x.com" in md
    assert "## Gmail" in md
    assert "Hi" in md


def test_dsr_with_no_results():
    report = p3.collate_dsr_results("nobody@x.com")
    assert report["summary"]["total"] == 0
    md = p3.render_dsr_markdown(report)
    assert "**Total items:** 0" in md
