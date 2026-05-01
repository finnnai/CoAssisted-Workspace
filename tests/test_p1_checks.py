# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for p1_checks — the 7 P1 workflow check functions."""

from __future__ import annotations

import datetime as _dt

import p1_checks


# --------------------------------------------------------------------------- #
# #2 Inbox auto-snooze
# --------------------------------------------------------------------------- #


def test_classify_promo_from_pattern():
    assert p1_checks.classify_inbox_message(
        "Awesome Newsletter <promo@somenewsletter.com>", "Today's deals",
    ) == "promo"


def test_classify_unsubscribe_subject():
    assert p1_checks.classify_inbox_message(
        "Marketing <m@x.com>", "Sale ends Friday — unsubscribe to opt out",
    ) == "promo"


def test_classify_real_human_email_returns_none():
    assert p1_checks.classify_inbox_message(
        "Sarah Fields <sarah@x.com>",
        "Follow-up on our call",
    ) is None


def test_check_inbox_auto_snooze_emits_alerts():
    msgs = [
        {"id": "1", "from": "no-reply@x.com",
         "subject": "Your monthly digest", "threadId": "t1",
         "link": "https://m/1"},
        {"id": "2", "from": "Sarah <sarah@x.com>",
         "subject": "Quick question", "threadId": "t2",
         "link": "https://m/2"},
        {"id": "3", "from": "promo@something.com",
         "subject": "20% off this week", "threadId": "t3",
         "link": "https://m/3"},
    ]
    alerts = p1_checks.check_inbox_auto_snooze(msgs)
    titles = [a["title"] for a in alerts]
    assert "Your monthly digest" in titles
    assert "20% off this week" in titles
    assert "Quick question" not in titles


# --------------------------------------------------------------------------- #
# #7 Stale relationships
# --------------------------------------------------------------------------- #


def test_stale_threshold_60_days():
    contacts = [
        {"name": "A", "email": "a@x.com", "days_since_contact": 30},
        {"name": "B", "email": "b@x.com", "days_since_contact": 90},
    ]
    alerts = p1_checks.check_stale_relationships(contacts)
    assert len(alerts) == 1
    assert alerts[0]["title"] == "B"


def test_stale_custom_threshold():
    contacts = [{"name": "A", "email": "a@x.com", "days_since_contact": 45}]
    alerts = p1_checks.check_stale_relationships(contacts, threshold_days=30)
    assert len(alerts) == 1


# --------------------------------------------------------------------------- #
# #8 Reciprocity
# --------------------------------------------------------------------------- #


def test_reciprocity_flags_one_sided():
    contacts = [
        {"name": "Alex", "email": "a@x.com",
         "sent_last_60": 6, "received_last_60": 1},
    ]
    alerts = p1_checks.check_reciprocity(contacts)
    assert len(alerts) == 1
    assert "you sent 6, they sent 1" in alerts[0]["detail"]


def test_reciprocity_skips_balanced():
    contacts = [
        {"name": "Alex", "email": "a@x.com",
         "sent_last_60": 5, "received_last_60": 4},
    ]
    alerts = p1_checks.check_reciprocity(contacts)
    assert alerts == []


def test_reciprocity_skips_low_send_count():
    """Even one-sided, fewer than min_sent shouldn't flag."""
    contacts = [
        {"name": "Alex", "email": "a@x.com",
         "sent_last_60": 2, "received_last_60": 0},
    ]
    alerts = p1_checks.check_reciprocity(contacts, min_sent=4)
    assert alerts == []


def test_reciprocity_zero_received_uses_sent_as_ratio():
    contacts = [
        {"name": "A", "email": "a@x.com",
         "sent_last_60": 5, "received_last_60": 0},
    ]
    alerts = p1_checks.check_reciprocity(contacts)
    assert len(alerts) == 1
    assert alerts[0]["data"]["ratio"] == 5.0


# --------------------------------------------------------------------------- #
# #13 Send-later
# --------------------------------------------------------------------------- #


def test_send_later_due_emits_alert():
    now = _dt.datetime.now().astimezone()
    past = (now - _dt.timedelta(minutes=5)).isoformat()
    sched = [{
        "id": "s1", "kind": "initial", "subject": "Send to client",
        "due_at_iso": past, "link": "https://m/s1",
    }]
    alerts = p1_checks.check_send_later(sched, now=now)
    assert len(alerts) == 1
    assert alerts[0]["kind"] == "send_later_initial"


def test_send_later_future_skipped():
    now = _dt.datetime.now().astimezone()
    future = (now + _dt.timedelta(hours=2)).isoformat()
    sched = [{"id": "s1", "kind": "initial", "due_at_iso": future,
              "subject": "x"}]
    alerts = p1_checks.check_send_later(sched, now=now)
    assert alerts == []


def test_send_later_followup_kind_emitted():
    now = _dt.datetime.now().astimezone()
    past = (now - _dt.timedelta(minutes=1)).isoformat()
    sched = [{"id": "s1", "kind": "followup", "due_at_iso": past,
              "subject": "Follow-up to client"}]
    alerts = p1_checks.check_send_later(sched, now=now)
    assert alerts[0]["kind"] == "send_later_followup"


# --------------------------------------------------------------------------- #
# #22 Week-ahead
# --------------------------------------------------------------------------- #


def test_week_ahead_summary_alert_present():
    events = [
        {"summary": "Customer demo", "id": "e1"},
        {"summary": "Standup", "id": "e2"},
    ]
    deadlines = [{"title": "Filing due", "due_at": "Friday"}]
    commitments = []
    alerts = p1_checks.check_week_ahead(events, deadlines, commitments)
    assert alerts[0]["kind"] == "week_ahead_summary"
    assert alerts[0]["data"]["meeting_count"] == 2
    assert alerts[0]["data"]["high_stakes"] == 1
    # Plus one deadline alert.
    assert any(a["kind"] == "deadline" for a in alerts)


# --------------------------------------------------------------------------- #
# #37 Retention sweep
# --------------------------------------------------------------------------- #


def test_retention_finds_old_financial_threads():
    threads = [
        {"id": "t1", "subject": "Q3 invoice from Acme",
         "snippet": "Please pay $500", "age_days": 400},
        {"id": "t2", "subject": "Lunch?",
         "snippet": "wanna grab lunch", "age_days": 600},
        {"id": "t3", "subject": "Wire instructions",
         "snippet": "Routing 12345", "age_days": 200},  # too fresh
    ]
    alerts = p1_checks.check_retention_sweep(threads, cutoff_days=365)
    titles = [a["title"] for a in alerts]
    assert "Q3 invoice from Acme" in titles
    assert "Lunch?" not in titles  # no financial token
    assert "Wire instructions" not in titles  # too fresh


# --------------------------------------------------------------------------- #
# #38 End of day
# --------------------------------------------------------------------------- #


def test_end_of_day_summary_with_carryovers():
    tasks = [{"id": "t1", "title": "Finish memo"},
             {"id": "t2", "title": "Email Brian"}]
    threads = [{"id": "th1"}, {"id": "th2"}, {"id": "th3"}]
    alerts = p1_checks.check_end_of_day(tasks, threads)
    assert alerts[0]["kind"] == "end_of_day_summary"
    assert alerts[0]["data"]["task_count"] == 2
    assert alerts[0]["data"]["thread_count"] == 3
    assert sum(1 for a in alerts if a["kind"] == "task_carryover") == 2


def test_end_of_day_caps_carryovers_at_5():
    tasks = [{"id": str(i), "title": f"T{i}"} for i in range(20)]
    alerts = p1_checks.check_end_of_day(tasks, [])
    assert sum(1 for a in alerts if a["kind"] == "task_carryover") == 5


# --------------------------------------------------------------------------- #
# Registration with scanner
# --------------------------------------------------------------------------- #


def test_register_p1_checks_adds_seven_to_scanner():
    import scanner

    scanner._reset_registry_for_tests()
    p1_checks.register_p1_checks(
        inbox_fetcher=lambda: [],
        contacts_fetcher=lambda: [],
        reciprocity_fetcher=lambda: [],
        send_later_fetcher=lambda: [],
        week_ahead_fetcher=lambda: ([], [], []),
        retention_fetcher=lambda: [],
        end_of_day_fetcher=lambda: ([], []),
    )
    names = {c["name"] for c in scanner.list_checks()}
    assert {
        "p1_inbox_auto_snooze",
        "p1_stale_relationship_digest",
        "p1_reciprocity_flag",
        "p1_send_later_followup",
        "p1_sunday_week_ahead",
        "p1_retention_sweep",
        "p1_end_of_day_shutdown",
    } <= names
    scanner._reset_registry_for_tests()
