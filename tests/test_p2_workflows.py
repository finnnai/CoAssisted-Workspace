# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for the 8 P2 workflow logic functions."""

from __future__ import annotations

import datetime as _dt

import p2_workflows as p2


TZ = _dt.timezone(_dt.timedelta(hours=-7))


# --------------------------------------------------------------------------- #
# #15 — auto-draft inbound
# --------------------------------------------------------------------------- #


def test_needs_reply_score_question_from_other():
    score = p2.needs_reply_score({
        "last_message": {"from_self": False, "snippet": "Can we move?"},
    })
    assert score >= 80  # 50 (other sender) + 30 (?)


def test_needs_reply_score_self_sent_zero():
    score = p2.needs_reply_score({
        "last_message": {"from_self": True, "snippet": "Done."},
    })
    assert score == 0


def test_auto_draft_candidates_filters_below_threshold():
    threads = [
        {"id": "1", "last_message": {"from_self": False, "snippet": "ack"}},  # 50
        {"id": "2", "last_message": {"from_self": False, "snippet": "?"},
         "is_vip": True, "stale_days": 5},                                    # 100
    ]
    out = p2.auto_draft_candidates(threads, score_threshold=60)
    assert len(out) == 1
    assert out[0]["id"] == "2"


# --------------------------------------------------------------------------- #
# #24 — RSVP alternatives
# --------------------------------------------------------------------------- #


def test_find_alternative_slots_skips_busy():
    base = _dt.datetime(2026, 5, 1, 14, 0, tzinfo=TZ)  # Friday 2pm
    end = base + _dt.timedelta(hours=1)
    # Busy 9-12 next Mon (skipping Sat/Sun is on the caller; we still check)
    monday_morning_busy = (
        _dt.datetime(2026, 5, 4, 9, 0, tzinfo=TZ),
        _dt.datetime(2026, 5, 4, 12, 0, tzinfo=TZ),
    )
    slots = p2.find_alternative_slots(
        base, end, [monday_morning_busy],
        candidates_per_day=2, days_ahead=4,
    )
    assert len(slots) > 0
    # No slot should overlap the busy block
    for s in slots:
        if s.start.date() == monday_morning_busy[0].date():
            assert not (s.start < monday_morning_busy[1] and s.end > monday_morning_busy[0])


# --------------------------------------------------------------------------- #
# #25 — Ghost agenda
# --------------------------------------------------------------------------- #


def test_is_ghost_meeting_no_description():
    event = {"description": "", "organizer": {"email": "me@x.com"}}
    assert p2.is_ghost_meeting(event, "me@x.com")


def test_is_ghost_meeting_short_description_still_ghost():
    event = {"description": "tbd", "organizer": {"email": "me@x.com"}}
    assert p2.is_ghost_meeting(event, "me@x.com")


def test_not_ghost_when_other_organizer():
    event = {"description": "", "organizer": {"email": "other@x.com"}}
    assert not p2.is_ghost_meeting(event, "me@x.com")


def test_not_ghost_when_real_description():
    event = {"description": "Detailed agenda about Q3 sales pipeline with breakdown by region",
             "organizer": {"email": "me@x.com"}}
    assert not p2.is_ghost_meeting(event, "me@x.com")


# --------------------------------------------------------------------------- #
# #26 — Birthday
# --------------------------------------------------------------------------- #


def test_find_today_birthdays_match():
    today = _dt.date(2026, 9, 15)
    contacts = [
        {"name": "A", "email": "a@x.com", "birthday_md": "09-15"},
        {"name": "B", "email": "b@x.com", "birthday_md": "01-01"},
    ]
    out = p2.find_today_birthdays(contacts, today=today)
    assert len(out) == 1
    assert out[0]["name"] == "A"


def test_find_today_birthdays_empty():
    out = p2.find_today_birthdays([], today=_dt.date(2026, 9, 15))
    assert out == []


# --------------------------------------------------------------------------- #
# #40 — Intro follow-through
# --------------------------------------------------------------------------- #


def test_is_intro_thread_subject():
    assert p2.is_intro_thread("Intro: Sarah meet Brian", "")
    assert p2.is_intro_thread("Connecting you both", "")
    assert not p2.is_intro_thread("Lunch tomorrow?", "")


def test_find_unfollowed_intros():
    today = _dt.date(2026, 4, 28)
    threads = [
        # Intro from 20 days ago, no follow-through → flagged
        {"id": "t1", "subject": "Intro: A meet B",
         "body": "wanted to connect you both",
         "from": "me@x.com", "to": ["a@x.com", "b@x.com"],
         "created_at": "2026-04-08T10:00:00+00:00",
         "has_followup_threads": False},
        # Same intro but has follow-through → skipped
        {"id": "t2", "subject": "Introduction time",
         "body": "want you to meet",
         "from": "me@x.com", "to": ["c@x.com", "d@x.com"],
         "created_at": "2026-04-08T10:00:00+00:00",
         "has_followup_threads": True},
        # Recent intro (under threshold) → skipped
        {"id": "t3", "subject": "Intro",
         "body": "meet meet",
         "from": "me@x.com", "to": ["e@x.com", "f@x.com"],
         "created_at": "2026-04-25T10:00:00+00:00",
         "has_followup_threads": False},
    ]
    out = p2.find_unfollowed_intros(threads, user_email="me@x.com",
                                     days_threshold=14, today=today)
    assert len(out) == 1
    assert out[0]["id"] == "t1"


# --------------------------------------------------------------------------- #
# #43 — Cross-thread context
# --------------------------------------------------------------------------- #


def test_find_other_open_threads():
    threads = [
        {"id": "t1", "participants": ["sarah@x.com", "me@x.com"],
         "subject": "Renewal", "last_activity": "2026-04-28", "status": "open"},
        {"id": "t2", "participants": ["sarah@x.com", "me@x.com"],
         "subject": "Old thing", "last_activity": "2026-01-01", "status": "closed"},
        {"id": "t3", "participants": ["sarah@x.com", "me@x.com", "brian@x.com"],
         "subject": "Project ALPHA", "last_activity": "2026-04-20", "status": "open"},
        {"id": "t4", "participants": ["other@x.com", "me@x.com"],
         "subject": "Different person", "last_activity": "2026-04-22", "status": "open"},
    ]
    out = p2.find_other_open_threads("sarah@x.com", threads,
                                     exclude_thread_id="t1", open_only=True)
    ids = [t["id"] for t in out]
    assert "t1" not in ids
    assert "t2" not in ids  # closed
    assert "t3" in ids
    assert "t4" not in ids  # different person


# --------------------------------------------------------------------------- #
# #74 — Meeting coordinator
# --------------------------------------------------------------------------- #


def test_find_common_free_slots_basic():
    now = _dt.datetime(2026, 4, 28, 10, 0, tzinfo=TZ)
    # Person A busy all of next Tue 9-5, Person B busy next Wed 9-12
    busy = {
        "a@x.com": [(
            _dt.datetime(2026, 4, 29, 9, 0, tzinfo=TZ),
            _dt.datetime(2026, 4, 29, 17, 0, tzinfo=TZ),
        )],
        "b@x.com": [(
            _dt.datetime(2026, 4, 30, 9, 0, tzinfo=TZ),
            _dt.datetime(2026, 4, 30, 12, 0, tzinfo=TZ),
        )],
    }
    slots = p2.find_common_free_slots(
        busy, duration_min=30, candidates=3, now=now, days_ahead=5,
    )
    assert len(slots) >= 1
    # First candidate should not be on Apr 29 (A all-day busy)
    for s in slots:
        if s.start.date() == _dt.date(2026, 4, 29):
            # All slots conflict with A's busy block
            a_busy = busy["a@x.com"][0]
            assert s.end <= a_busy[0] or s.start >= a_busy[1]


# --------------------------------------------------------------------------- #
# #77 — Translate
# --------------------------------------------------------------------------- #


def test_detect_language_french():
    assert p2.detect_language(
        "Bonjour, merci pour votre message. Cordialement, Pierre"
    ) == "fr"


def test_detect_language_spanish():
    assert p2.detect_language(
        "Hola, gracias por su mensaje. Saludos cordialmente"
    ) == "es"


def test_detect_language_english_returns_none():
    assert p2.detect_language(
        "Hello, thanks for the message. Regards, Mark"
    ) is None


def test_detect_language_empty():
    assert p2.detect_language("") is None
    assert p2.detect_language(None) is None


def test_build_translate_reply_request_carries_target_lang():
    req = p2.build_translate_reply_request(
        "Bonjour", target_language="fr",
        recipient_name="Pierre", sender_name="Finn",
    )
    assert req.target_language == "fr"
    assert req.intent == "translate_reply"
