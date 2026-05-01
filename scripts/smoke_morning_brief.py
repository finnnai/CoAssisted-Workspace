# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Smoke test for morning brief — realistic full-day composition.

Run from project root:  python3 scripts/smoke_morning_brief.py
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import morning_brief as core


def main() -> int:
    now = _dt.datetime.now().astimezone()

    # ----- Calendar: 4 events spanning the day --------------------------- #
    events = [
        {
            "id": "e1", "summary": "1:1 with Alex",
            "start": {"dateTime": (now + _dt.timedelta(minutes=20)).isoformat()},
            "attendees": [{"email": "alex@surefox.com"}],
            "html_link": "https://cal.example/e1",
        },
        {
            "id": "e2", "summary": "Customer demo with Anthropic",
            "start": {"dateTime": (now + _dt.timedelta(hours=3)).isoformat()},
            "attendees": [{"email": "vendor@anthropic.com"},
                          {"email": "finn@surefox.com"}],
            "html_link": "https://cal.example/e2",
        },
        {
            "id": "e3", "summary": "Team standup",
            "start": {"dateTime": (now + _dt.timedelta(hours=5)).isoformat()},
            "attendees": [{"email": f"team{i}@surefox.com"} for i in range(5)],
            "html_link": "https://cal.example/e3",
        },
        {
            "id": "e4", "summary": "Quarterly review prep",
            "start": {"dateTime": (now + _dt.timedelta(hours=7)).isoformat()},
            "attendees": [{"email": "ceo@surefox.com"}],
            "html_link": "https://cal.example/e4",
        },
    ]

    # ----- Inbox: VIP unread + stale threads + fresh non-VIP -------------- #
    inbox_threads = [
        {"id": "t1", "from": "Sarah Fields <sarah@bigcustomer.com>",
         "subject": "Renewal terms — quick check",
         "snippet": "Hey, wanted to circle back on the renewal — ",
         "stale_days": 1,
         "link": "https://mail.google.com/mail/u/0/#inbox/t1"},
        {"id": "t2", "from": "Brian <brian@xenture.com>",
         "subject": "Q3 platform update",
         "snippet": "Here is the update I promised you ",
         "stale_days": 6,
         "link": "https://mail.google.com/mail/u/0/#inbox/t2"},
        {"id": "t3", "from": "Random <random@example.com>",
         "subject": "Following up",
         "snippet": "Just checking in",
         "stale_days": 1},  # too fresh + not VIP → skipped
        {"id": "t4", "from": "Allan <allan@anothercustomer.com>",
         "subject": "Contract redlines",
         "snippet": "Attached are our redlines",
         "stale_days": 9,
         "link": "https://mail.google.com/mail/u/0/#inbox/t4"},
    ]
    vips = {"sarah@bigcustomer.com", "allan@anothercustomer.com"}

    # ----- AP: 3 outstanding, 1 overdue ----------------------------------- #
    ap_outstanding = [
        {"content_key": "k1", "vendor": "Acme Roofing",
         "invoice_number": "INV-2026-04-A",
         "missing_fields": ["invoice number"],
         "request_sent_at": (now - _dt.timedelta(days=3)).isoformat()},
        {"content_key": "k2", "vendor": "Bay Plumbing",
         "invoice_number": "INV-101",
         "missing_fields": ["amount", "date"],
         "request_sent_at": (now - _dt.timedelta(days=1)).isoformat()},
        {"content_key": "k3", "vendor": "Capital Electric",
         "invoice_number": "INV-77",
         "missing_fields": ["project_code"],
         "request_sent_at": (now - _dt.timedelta(hours=12)).isoformat()},
    ]
    ap_overdue = [ap_outstanding[0]]  # Acme is past its reminder cadence

    # ----- Stale relationships ------------------------------------------- #
    stale_contacts = [
        {"name": "Mark Adams", "email": "mark@oldfriend.com",
         "days_since_contact": 120},
        {"name": "Linda Cho", "email": "linda@former.com",
         "days_since_contact": 180},
        {"name": "Todd Rivera", "email": "todd@partner.com",
         "days_since_contact": 65},
    ]

    brief = core.compose_brief(
        date=now.date().isoformat(),
        user_email="finn@surefox.com",
        calendar_events=events,
        inbox_needs_reply=inbox_threads,
        inbox_unread_count=47,
        vip_emails=vips,
        ap_outstanding=ap_outstanding,
        ap_overdue=ap_overdue,
        stale_contacts=stale_contacts,
        now=now,
    )

    print("=" * 100)
    print("SMOKE TEST: morning brief — realistic full-day scenario")
    print("=" * 100)
    print()
    print(core.render_markdown(brief))
    print("=" * 100)

    # ----- Assertions on the resulting brief ----------------------------- #
    fails = []

    summary = brief.summary
    if summary["calendar_count"] != 4:
        fails.append(f"calendar_count = {summary['calendar_count']}, expected 4")
    if summary["inbox_needs_reply"] != 3:  # VIP + 2 stale; the fresh-non-VIP is dropped
        fails.append(f"inbox_needs_reply = {summary['inbox_needs_reply']}, expected 3")
    if summary["ap_overdue"] != 1:
        fails.append(f"ap_overdue = {summary['ap_overdue']}, expected 1")
    if summary["stale_relationships"] != 3:
        fails.append(f"stale_relationships = {summary['stale_relationships']}, expected 3")

    top = brief.top_items
    if len(top) != 5:
        fails.append(f"top_items count = {len(top)}, expected 5")

    # The #1 item should be either the AP overdue or a VIP inbox thread
    # (both score >70). The 1:1 scheduled in 20 min is also strong (high_stakes
    # + proximity bonus). Whichever wins, "team standup" should NOT be #1.
    if top and "Team standup" in top[0].title:
        fails.append("Team standup ranked #1 — proximity/stakes ranking broken")

    # Acme Roofing (overdue) should appear before Bay Plumbing in AP section.
    ap_items = [i for i in brief.items if i.section == "ap"]
    ap_titles = [i.title for i in sorted(ap_items, key=lambda x: -x.priority)]
    if not ap_titles or "Acme Roofing" not in ap_titles[0]:
        fails.append(f"Acme (overdue) didn't rank first in AP section: {ap_titles}")

    print()
    print("Top-5 ranking:")
    for i, it in enumerate(top, 1):
        print(f"  {i}. [{it.priority:>3}] {it.kind:<25} — {it.title}")
    print()

    if fails:
        print(f"FAIL — {len(fails)} assertion(s):")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("PASS — full-day scenario produces a sensible brief")
    return 0


if __name__ == "__main__":
    sys.exit(main())
