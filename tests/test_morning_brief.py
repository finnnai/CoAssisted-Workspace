# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Tests for the morning brief pure-logic core."""

from __future__ import annotations

import datetime as _dt

import morning_brief as core


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _event(summary: str, start_iso: str, attendees: list[str] | None = None) -> dict:
    return {
        "id": f"e_{summary}",
        "summary": summary,
        "start": {"dateTime": start_iso},
        "attendees": [{"email": a} for a in (attendees or [])],
        "html_link": f"https://cal.example/{summary}",
    }


def _today_at(hour: int, minute: int = 0, tz: _dt.tzinfo | None = None) -> str:
    tz = tz or _dt.datetime.now().astimezone().tzinfo
    now = _dt.datetime.now(tz=tz)
    dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return dt.isoformat()


# --------------------------------------------------------------------------- #
# Calendar items
# --------------------------------------------------------------------------- #


def test_high_stakes_meeting_recognized():
    events = [_event("Customer demo with Acme", _today_at(10), ["a@x.com"])]
    brief = core.compose_brief(date="2026-04-28", calendar_events=events)
    cal = [i for i in brief.items if i.section == "calendar"]
    assert cal[0].kind == "meeting_high_stakes"


def test_meeting_starting_soon_kind():
    """Event 30 min from now → starting_soon."""
    now = _dt.datetime.now().astimezone()
    soon = now + _dt.timedelta(minutes=30)
    events = [_event("Quick sync", soon.isoformat())]
    brief = core.compose_brief(date="x", calendar_events=events, now=now)
    cal = [i for i in brief.items if i.section == "calendar"]
    # Could be high_stakes OR starting_soon depending on title patterns;
    # 'sync' isn't high-stakes, so should be starting_soon.
    assert cal[0].kind == "meeting_starting_soon"


def test_generic_meeting_today():
    """Event later today, not high-stakes, not within 90 min."""
    now = _dt.datetime.now().astimezone()
    later = now + _dt.timedelta(hours=4)
    events = [_event("Team standup", later.isoformat())]
    brief = core.compose_brief(date="x", calendar_events=events, now=now)
    cal = [i for i in brief.items if i.section == "calendar"]
    assert cal[0].kind == "meeting_today"


def test_proximity_bumps_priority():
    """A meeting 10 min from now should outrank one 4 hours from now."""
    now = _dt.datetime.now().astimezone()
    near = now + _dt.timedelta(minutes=10)
    far = now + _dt.timedelta(hours=4)
    events = [
        _event("Far meeting", far.isoformat()),
        _event("Near meeting", near.isoformat()),
    ]
    brief = core.compose_brief(date="x", calendar_events=events, now=now)
    cal = sorted(
        [i for i in brief.items if i.section == "calendar"],
        key=lambda x: -x.priority,
    )
    assert "Near" in cal[0].title


# --------------------------------------------------------------------------- #
# Inbox items
# --------------------------------------------------------------------------- #


def test_vip_unread_is_high_priority():
    threads = [{
        "id": "t1", "from": "Sarah <sarah@bigcustomer.com>",
        "subject": "Renewal question", "snippet": "...",
        "stale_days": 1,
    }]
    brief = core.compose_brief(
        date="x",
        inbox_needs_reply=threads,
        vip_emails={"sarah@bigcustomer.com"},
    )
    inbox = [i for i in brief.items if i.section == "inbox"]
    assert inbox[0].kind == "inbox_vip_unread"
    assert inbox[0].priority >= core._WEIGHTS["inbox_vip_unread"]


def test_stale_thread_3_days():
    threads = [{
        "id": "t1", "from": "Random <r@x.com>",
        "subject": "Q", "stale_days": 4,
    }]
    brief = core.compose_brief(date="x", inbox_needs_reply=threads)
    inbox = [i for i in brief.items if i.section == "inbox"]
    assert inbox[0].kind == "inbox_thread_stale"


def test_fresh_non_vip_is_skipped():
    threads = [{
        "id": "t1", "from": "Random <r@x.com>",
        "subject": "Q", "stale_days": 1,
    }]
    brief = core.compose_brief(date="x", inbox_needs_reply=threads)
    inbox = [i for i in brief.items if i.section == "inbox"]
    assert inbox == []


def test_extra_stale_gets_priority_bump():
    threads = [{
        "id": "t1", "from": "r@x.com",
        "subject": "Q", "stale_days": 10,
    }]
    brief = core.compose_brief(date="x", inbox_needs_reply=threads)
    inbox = [i for i in brief.items if i.section == "inbox"]
    assert inbox[0].priority >= core._WEIGHTS["inbox_thread_stale"] + 15


def test_unread_volume_signal_only_above_threshold():
    brief = core.compose_brief(date="x", inbox_unread_count=10)
    assert not any(i.kind == "inbox_unread_count" for i in brief.items)
    brief = core.compose_brief(date="x", inbox_unread_count=42)
    assert any(i.kind == "inbox_unread_count" for i in brief.items)


# --------------------------------------------------------------------------- #
# AP items
# --------------------------------------------------------------------------- #


def test_overdue_ap_outranks_outstanding():
    outstanding = [
        {"content_key": "k1", "vendor": "Acme", "invoice_number": "INV-1",
         "missing_fields": ["invoice number"]},
        {"content_key": "k2", "vendor": "Beta", "invoice_number": "INV-2",
         "missing_fields": ["total"]},
    ]
    overdue = [outstanding[0]]
    brief = core.compose_brief(
        date="x", ap_outstanding=outstanding, ap_overdue=overdue,
    )
    ap = sorted([i for i in brief.items if i.section == "ap"], key=lambda x: -x.priority)
    assert ap[0].title.startswith("Acme")
    assert ap[0].kind == "ap_overdue_reminder"
    assert ap[1].kind == "ap_outstanding"


# --------------------------------------------------------------------------- #
# Stale relationships
# --------------------------------------------------------------------------- #


def test_stale_relationship_filtered_under_60_days():
    contacts = [
        {"name": "A", "email": "a@x.com", "days_since_contact": 30},
        {"name": "B", "email": "b@x.com", "days_since_contact": 90},
    ]
    brief = core.compose_brief(date="x", stale_contacts=contacts)
    rel = [i for i in brief.items if i.section == "relationships"]
    assert len(rel) == 1
    assert rel[0].title == "B"


def test_stale_priority_scales_with_days():
    contacts = [
        {"name": "A", "email": "a@x.com", "days_since_contact": 65},
        {"name": "B", "email": "b@x.com", "days_since_contact": 200},
    ]
    brief = core.compose_brief(date="x", stale_contacts=contacts)
    rel = sorted(
        [i for i in brief.items if i.section == "relationships"],
        key=lambda x: -x.priority,
    )
    assert rel[0].title == "B"
    assert rel[0].priority > rel[1].priority


# --------------------------------------------------------------------------- #
# Top-5 cross-section ranking
# --------------------------------------------------------------------------- #


def test_top_5_pulls_highest_across_sections():
    """Should pull AP overdue + VIP inbox above plain meetings + stale contacts."""
    now = _dt.datetime.now().astimezone()
    events = [_event("Daily standup", (now + _dt.timedelta(hours=3)).isoformat())]
    threads = [{
        "id": "t1", "from": "VIP <vip@bigcustomer.com>",
        "subject": "URGENT", "stale_days": 1,
    }]
    ap = [{"content_key": "k1", "vendor": "Acme", "invoice_number": "INV-1",
           "missing_fields": ["amount"]}]
    contacts = [{"name": "Old friend", "email": "x@y.com",
                 "days_since_contact": 120}]

    brief = core.compose_brief(
        date="x",
        calendar_events=events,
        inbox_needs_reply=threads,
        vip_emails={"vip@bigcustomer.com"},
        ap_outstanding=ap, ap_overdue=ap,
        stale_contacts=contacts,
        now=now,
    )
    top = brief.top_items
    assert len(top) <= 5
    # The first item must be one of the high-priority kinds.
    assert top[0].kind in ("ap_overdue_reminder", "inbox_vip_unread")


def test_top_5_caps_at_five():
    contacts = [
        {"name": f"C{i}", "email": f"c{i}@x.com", "days_since_contact": 100 + i}
        for i in range(10)
    ]
    brief = core.compose_brief(date="x", stale_contacts=contacts)
    assert len(brief.top_items) == 5


# --------------------------------------------------------------------------- #
# Markdown rendering
# --------------------------------------------------------------------------- #


def test_render_markdown_includes_top_5_header():
    contacts = [{"name": "A", "email": "a@x.com", "days_since_contact": 100}]
    brief = core.compose_brief(date="2026-04-28", stale_contacts=contacts)
    md = core.render_markdown(brief)
    assert "Morning brief — 2026-04-28" in md
    assert "Top 5 today" in md or "## Relationships" in md


def test_render_markdown_with_no_items():
    brief = core.compose_brief(date="2026-04-28")
    md = core.render_markdown(brief)
    assert "Morning brief — 2026-04-28" in md
    # Empty brief should still render the header without crashing.


# --------------------------------------------------------------------------- #
# Output shape
# --------------------------------------------------------------------------- #


def test_to_dict_has_all_sections_and_top_5():
    brief = core.compose_brief(date="x")
    d = brief.to_dict()
    assert d["date"] == "x"
    assert "summary" in d
    assert "top_5" in d
    assert "by_section" in d
    assert set(d["by_section"].keys()) == {"calendar", "inbox", "ap", "relationships"}
