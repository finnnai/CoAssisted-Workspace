# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for P6 — Travel suite (3 workflows)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

import external_feeds as ef
import p6_workflows as p6


@pytest.fixture(autouse=True)
def _isolate_ef(tmp_path: Path):
    ef._override_cache_path_for_tests(tmp_path / "ef.json")
    ef.unfreeze()
    yield
    ef.unfreeze()
    ef._override_cache_path_for_tests(
        Path(__file__).resolve().parent.parent / "external_feeds_cache.json",
    )


# --------------------------------------------------------------------------- #
# Travel classification
# --------------------------------------------------------------------------- #


def test_classify_flight():
    out = p6.is_travel_confirmation(
        "Your flight confirmation: SFO → JFK",
        "Booking confirmation number AB123. Flight departing 8am.",
    )
    assert out["is_flight"] is True
    assert out["confidence"] > 0


def test_classify_hotel():
    out = p6.is_travel_confirmation(
        "Hotel confirmation",
        "Check-in May 15, check-out May 18. Confirmation: HXY",
    )
    assert out["is_hotel"] is True


def test_classify_neutral():
    out = p6.is_travel_confirmation("Lunch?", "Want to grab lunch tomorrow?")
    assert not out["is_flight"]
    assert not out["is_hotel"]


# --------------------------------------------------------------------------- #
# Travel package
# --------------------------------------------------------------------------- #


def _flight(**overrides):
    base = dict(
        origin_iata="SFO", origin_city="San Francisco", origin_state="CA",
        dest_iata="JFK", dest_city="New York", dest_state="NY",
        depart_iso="2026-05-15T08:00:00-07:00",
        arrive_iso="2026-05-15T16:30:00-04:00",
        flight_number="UA123",
    )
    base.update(overrides)
    return base


def test_package_outbound_flight_only():
    pkg = p6.build_travel_package(flight=_flight())
    # Should produce 1 calendar block (flight) + 1 drive-time
    assert len(pkg.calendar_blocks) == 1
    assert len(pkg.drive_time_blocks) == 1
    assert pkg.calendar_blocks[0]["summary"].startswith("Flight UA123")


def test_package_with_return_flight():
    pkg = p6.build_travel_package(
        flight=_flight(),
        return_flight=_flight(
            origin_iata="JFK", origin_city="New York", origin_state="NY",
            dest_iata="SFO", dest_city="San Francisco", dest_state="CA",
            depart_iso="2026-05-18T17:00:00-04:00",
            arrive_iso="2026-05-18T20:30:00-07:00",
            flight_number="UA124",
        ),
    )
    assert len(pkg.calendar_blocks) == 2
    assert len(pkg.drive_time_blocks) == 2


def test_package_with_hotel_computes_per_diem():
    pkg = p6.build_travel_package(
        flight=_flight(),
        hotel={
            "name": "Acme Hotel",
            "address": "123 Main St, NY",
            "check_in": "2026-05-15",
            "check_out": "2026-05-18",
        },
    )
    assert pkg.per_diem_estimate is not None
    assert pkg.per_diem_estimate["city"] == "New York"
    assert pkg.total_days == 4  # check_in to check_out inclusive
    assert pkg.per_diem_estimate["estimated_total"] > 0


def test_package_drive_time_precedes_flight():
    pkg = p6.build_travel_package(flight=_flight())
    flight_block = pkg.calendar_blocks[0]
    drive_block = pkg.drive_time_blocks[0]
    assert drive_block["end"]["dateTime"] == flight_block["start"]["dateTime"]


# --------------------------------------------------------------------------- #
# End-of-trip expense packager
# --------------------------------------------------------------------------- #


def test_packager_filters_to_window():
    receipts = [
        {"date": "2026-05-14", "merchant": "Cafe", "total": 12.50, "category": "Meals"},
        {"date": "2026-05-15", "merchant": "Hotel", "total": 200, "category": "Lodging"},
        {"date": "2026-05-16", "merchant": "Lyft", "total": 45, "category": "Transport"},
        {"date": "2026-05-20", "merchant": "Outside", "total": 30, "category": "Meals"},
    ]
    bundle = p6.package_trip_expenses(
        "2026-05-15", "2026-05-18", "New York", receipts,
        submitter_name="Finn", project_code="ALPHA",
    )
    assert len(bundle.receipts) == 2  # 5/15 and 5/16
    assert bundle.grand_total == 245.0


def test_packager_currency_conversion(monkeypatch):
    """Non-USD receipts should be converted via FX cache."""
    receipts = [
        {"date": "2026-05-15", "merchant": "Pub", "total": 50, "category": "Meals",
         "currency": "EUR"},
    ]
    bundle = p6.package_trip_expenses(
        "2026-05-15", "2026-05-18", "London", receipts,
    )
    # EUR→USD should multiply by ~1.07
    assert 50 < bundle.grand_total < 60


def test_packager_groups_by_category():
    receipts = [
        {"date": "2026-05-15", "merchant": "A", "total": 100, "category": "Meals"},
        {"date": "2026-05-15", "merchant": "B", "total": 200, "category": "Lodging"},
        {"date": "2026-05-16", "merchant": "C", "total": 50,  "category": "Meals"},
    ]
    bundle = p6.package_trip_expenses(
        "2026-05-15", "2026-05-18", "X", receipts,
    )
    assert bundle.by_category["Meals"] == 150.0
    assert bundle.by_category["Lodging"] == 200.0


def test_packager_subject_includes_project():
    bundle = p6.package_trip_expenses(
        "2026-05-15", "2026-05-18", "NYC", [],
        project_code="ALPHA",
    )
    assert "ALPHA" in bundle.submission_email_subject
    assert "NYC" in bundle.submission_email_subject


def test_packager_invalid_dates():
    with pytest.raises(ValueError):
        p6.package_trip_expenses("not-a-date", "2026-05-18", "NYC", [])


# --------------------------------------------------------------------------- #
# Receipt photo prompt
# --------------------------------------------------------------------------- #


def test_prompt_skipped_when_no_active_trip():
    now = _dt.datetime(2026, 5, 1, 18, 30,
                        tzinfo=_dt.timezone(_dt.timedelta(hours=-7)))
    decision = p6.should_prompt_receipts(
        trips=[{"start": "2026-04-01", "end": "2026-04-05",
                "destination": "NYC"}],
        now=now,
    )
    assert decision.should_send is False
    assert "trip window" in decision.reason


def test_prompt_fires_within_window():
    now = _dt.datetime(2026, 5, 16, 18, 45,  # 18:45, target 18:30 ± 90min
                        tzinfo=_dt.timezone(_dt.timedelta(hours=-7)))
    decision = p6.should_prompt_receipts(
        trips=[{"start": "2026-05-15", "end": "2026-05-18",
                "destination": "New York"}],
        now=now,
    )
    assert decision.should_send is True
    assert "New York" in decision.prompt_text
    assert "Day 2" in decision.prompt_text


def test_prompt_skipped_outside_window():
    now = _dt.datetime(2026, 5, 16, 8, 0,
                        tzinfo=_dt.timezone(_dt.timedelta(hours=-7)))
    decision = p6.should_prompt_receipts(
        trips=[{"start": "2026-05-15", "end": "2026-05-18",
                "destination": "NYC"}],
        now=now,
        window_minutes=90,
    )
    assert decision.should_send is False
    assert "outside" in decision.reason


def test_prompt_skipped_if_already_sent_today():
    now = _dt.datetime(2026, 5, 16, 18, 30,
                        tzinfo=_dt.timezone(_dt.timedelta(hours=-7)))
    decision = p6.should_prompt_receipts(
        trips=[{"start": "2026-05-15", "end": "2026-05-18",
                "destination": "NYC"}],
        now=now,
        last_prompt_iso=now.replace(hour=10).isoformat(),
    )
    assert decision.should_send is False
    assert "already prompted" in decision.reason
