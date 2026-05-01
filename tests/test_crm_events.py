# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for crm_events store."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

import crm_events as ce


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path):
    ce._override_path_for_tests(tmp_path / "ev.json")
    yield
    ce._override_path_for_tests(
        Path(__file__).resolve().parent.parent / "crm_events.json",
    )


def test_append_returns_record_with_id():
    rec = ce.append("alice@x.com", "email_received", "Hi there")
    assert rec["id"]
    assert rec["kind"] == "email_received"
    assert rec["summary"] == "Hi there"


def test_email_normalized_lowercase():
    ce.append("Alice@X.com", "email_received", "x")
    assert "alice@x.com" in ce.all_emails()


def test_get_timeline_sorted_ascending():
    ce.append("a@x.com", "email_received", "older",
              ts="2026-01-01T00:00:00+00:00")
    ce.append("a@x.com", "email_received", "newer",
              ts="2026-04-01T00:00:00+00:00")
    timeline = ce.get_timeline("a@x.com")
    assert timeline[0]["summary"] == "older"
    assert timeline[1]["summary"] == "newer"


def test_get_recent_returns_newest_first():
    ce.append("a@x.com", "email_received", "1", ts="2026-01-01T00:00:00+00:00")
    ce.append("a@x.com", "email_received", "2", ts="2026-02-01T00:00:00+00:00")
    ce.append("a@x.com", "email_received", "3", ts="2026-03-01T00:00:00+00:00")
    recent = ce.get_recent("a@x.com", limit=2)
    assert [r["summary"] for r in recent] == ["3", "2"]


def test_last_event_filters_by_kind():
    ce.append("a@x.com", "email_received", "received")
    ce.append("a@x.com", "vip_alert", "alert")
    last_alert = ce.last_event("a@x.com", kind="vip_alert")
    assert last_alert["kind"] == "vip_alert"


def test_last_event_returns_none_for_unknown():
    assert ce.last_event("nobody@x.com") is None


def test_count_events_within_window():
    today = _dt.datetime(2026, 4, 28, tzinfo=_dt.timezone.utc)
    ce.append("a@x.com", "email_received", "old",
              ts=(today - _dt.timedelta(days=80)).isoformat())
    ce.append("a@x.com", "email_received", "recent",
              ts=(today - _dt.timedelta(days=20)).isoformat())
    n = ce.count_events("a@x.com", since_days=60, today=today)
    assert n == 1


def test_remove_event():
    rec = ce.append("a@x.com", "email_received", "x")
    assert ce.remove_event("a@x.com", rec["id"]) is True
    assert ce.remove_event("a@x.com", rec["id"]) is False


def test_clear_contact():
    ce.append("a@x.com", "x", "1")
    ce.append("a@x.com", "x", "2")
    n = ce.clear_contact("a@x.com")
    assert n == 2


def test_days_since_last_event():
    today = _dt.datetime(2026, 4, 28, tzinfo=_dt.timezone.utc)
    ce.append("a@x.com", "email_received", "x",
              ts=(today - _dt.timedelta(days=10)).isoformat())
    days = ce.days_since_last_event("a@x.com", today=today)
    assert days == 10


def test_find_intro_acceptance_when_a_to_b():
    today = _dt.datetime(2026, 4, 28, tzinfo=_dt.timezone.utc)
    ce.append("alice@x.com", "email_sent", "to bob",
              ts=(today - _dt.timedelta(days=5)).isoformat(),
              data={"with_email": "bob@x.com"})
    assert ce.find_intro_acceptance("alice@x.com", "bob@x.com",
                                     within_days=14, today=today) is True


def test_find_intro_acceptance_no_recent_contact():
    today = _dt.datetime(2026, 4, 28, tzinfo=_dt.timezone.utc)
    ce.append("alice@x.com", "email_sent", "to bob",
              ts=(today - _dt.timedelta(days=30)).isoformat(),
              data={"with_email": "bob@x.com"})
    assert ce.find_intro_acceptance("alice@x.com", "bob@x.com",
                                     within_days=14, today=today) is False


def test_atomic_write_no_leftover_tmp(tmp_path):
    ce.append("a@x.com", "x", "1")
    leftover = list(tmp_path.glob("*.tmp"))
    assert leftover == []
