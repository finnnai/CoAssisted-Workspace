# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for schedule defrag pure-logic core."""

from __future__ import annotations

import datetime as _dt

import schedule_defrag as core


# Use a fixed timezone-aware test reference to keep results deterministic.
TZ = _dt.timezone(_dt.timedelta(hours=-7))  # PDT-ish


def _evt(start_h: int, start_m: int, end_h: int, end_m: int,
         summary: str, ev_id: str = "x") -> dict:
    """Build a Calendar API event for a fixed test day (2026-04-28)."""
    base = _dt.date(2026, 4, 28)
    s = _dt.datetime(base.year, base.month, base.day, start_h, start_m, tzinfo=TZ)
    e = _dt.datetime(base.year, base.month, base.day, end_h, end_m, tzinfo=TZ)
    return {
        "id": ev_id,
        "summary": summary,
        "start": {"dateTime": s.isoformat()},
        "end": {"dateTime": e.isoformat()},
    }


def test_no_events_no_fragments():
    report = core.find_fragments([])
    assert report.fragments == []
    assert report.suggestions == []


def test_single_event_no_useful_fragments():
    """Event 9-10. Trailing block 10-6pm = 480min, useful, no fragments.
    Leading 8-9 = 60min, also useful."""
    report = core.find_fragments([_evt(9, 0, 10, 0, "Standup")])
    assert report.fragments == []


def test_back_to_back_creates_no_fragments():
    """Two adjacent meetings → no gap → no fragment."""
    report = core.find_fragments([
        _evt(9, 0, 10, 0, "A"),
        _evt(10, 0, 11, 0, "B"),
    ])
    assert report.fragments == []


def test_short_gap_is_a_fragment():
    """Gap of 30 min between two meetings → fragment."""
    report = core.find_fragments([
        _evt(9, 0, 10, 0, "A"),
        _evt(10, 30, 11, 30, "B"),
    ])
    assert len(report.fragments) == 1
    f = report.fragments[0]
    assert f.duration_min == 30
    assert f.before_event == "A"
    assert f.after_event == "B"


def test_useful_gap_above_threshold_not_a_fragment():
    """50-min gap (above 45 default) → not a fragment."""
    report = core.find_fragments([
        _evt(9, 0, 10, 0, "A"),
        _evt(10, 50, 11, 50, "B"),
    ])
    assert report.fragments == []


def test_defrag_suggestion_pairs_fragments_with_middle_meeting():
    """Two 30min fragments bracketing a 30min meeting → if moved, 90min block."""
    events = [
        _evt(9, 0, 10, 0, "Anchor 1"),
        _evt(10, 30, 11, 0, "Movable", "movable"),
        _evt(11, 30, 12, 30, "Anchor 2"),
    ]
    report = core.find_fragments(events)
    assert len(report.fragments) == 2
    assert len(report.suggestions) == 1
    s = report.suggestions[0]
    assert s.middle_event == "Movable"
    assert s.middle_event_id == "movable"
    # 30 + 30 + 30 = 90 min if movable shifts
    assert s.if_moved_block_min == 90


def test_no_suggestion_when_total_block_below_threshold():
    """If shifting still wouldn't yield a useful block, no suggestion."""
    events = [
        _evt(9, 0, 10, 0, "A"),
        _evt(10, 10, 10, 30, "Tiny middle"),
        _evt(10, 40, 11, 40, "B"),
    ]
    report = core.find_fragments(events, min_useful_block_min=60)
    # Two fragments (10min and 10min), but total block 40min = below 60min threshold.
    assert len(report.suggestions) == 0


def test_overlapping_events_dont_crash():
    events = [
        _evt(9, 0, 10, 30, "A"),
        _evt(10, 0, 11, 0, "B (overlap)"),
        _evt(11, 30, 12, 0, "C"),
    ]
    report = core.find_fragments(events)
    # Should not raise, should produce zero or more fragments
    assert isinstance(report.fragments, list)


def test_all_day_events_skipped():
    """All-day events have date but not dateTime → skipped."""
    all_day = {
        "id": "all_day",
        "summary": "Vacation",
        "start": {"date": "2026-04-28"},
        "end": {"date": "2026-04-29"},
    }
    report = core.find_fragments([all_day])
    assert report.fragments == []
    assert report.days_analyzed == []


def test_multiple_days_analyzed():
    base = _dt.date(2026, 4, 28)
    next_day = _dt.date(2026, 4, 29)

    def evt(d, sh, sm, eh, em, name):
        s = _dt.datetime(d.year, d.month, d.day, sh, sm, tzinfo=TZ)
        e = _dt.datetime(d.year, d.month, d.day, eh, em, tzinfo=TZ)
        return {"id": name, "summary": name,
                "start": {"dateTime": s.isoformat()},
                "end": {"dateTime": e.isoformat()}}

    events = [
        evt(base, 9, 0, 10, 0, "day1A"),
        evt(base, 10, 30, 11, 30, "day1B"),
        evt(next_day, 9, 0, 10, 0, "day2A"),
    ]
    report = core.find_fragments(events)
    assert "2026-04-28" in report.days_analyzed
    assert "2026-04-29" in report.days_analyzed


def test_to_dict_serializes():
    events = [
        _evt(9, 0, 10, 0, "A"),
        _evt(10, 30, 11, 30, "B"),
    ]
    report = core.find_fragments(events)
    d = report.to_dict()
    assert d["fragment_count"] == 1
    assert d["suggestion_count"] == 0
    assert "days_analyzed" in d
