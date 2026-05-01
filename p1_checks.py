# © 2026 CoAssisted Workspace. Licensed under MIT.
"""P1 workflow checks — register the 7 P1 workflows with the scanner.

Each P1 workflow has a `check_<name>()` function that returns a list of
"alerts" — dicts describing things that need attention. The scanner runs
these on cadence and rolls up the results.

Registered checks:
  - p1_inbox_auto_snooze         (#2)  every 4 hours
  - p1_stale_relationship_digest (#7)  every 7 days (weekly)
  - p1_reciprocity_flag          (#8)  every 7 days (weekly)
  - p1_send_later_followup       (#13) every 1 hour
  - p1_sunday_week_ahead         (#22) every 168 hours (weekly)
  - p1_retention_sweep           (#37) every 24 hours
  - p1_end_of_day_shutdown       (#38) every 24 hours

These checks return alerts in a normalized shape:
    {
        "kind": str,
        "title": str,
        "detail": str,
        "link": str | None,
        "data": dict,
    }
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Iterable

import scanner


# --------------------------------------------------------------------------- #
# #2 Inbox auto-snooze candidates
# --------------------------------------------------------------------------- #


# Patterns that suggest a message can be safely deferred (newsletter / promo /
# transactional / no-action calendar updates).
_PROMO_FROM_PATTERNS = [
    r"@.*newsletter",
    r"^no[-]?reply@",
    r"^donot[-]?reply@",
    r"@.*marketing",
    r"^updates@",
    r"^notifications@",
]
_PROMO_FROM_REGEX = [re.compile(p, re.IGNORECASE) for p in _PROMO_FROM_PATTERNS]

_PROMO_SUBJECT_PATTERNS = [
    r"^\s*newsletter\b",
    r"\bunsubscribe\b",
    r"\bdigest\b",
    r"\bweekly\s+update\b",
    r"\bdaily\s+roundup\b",
    r"\bspecial\s+offer\b",
    r"% off\b",
    r"\bsale ends\b",
    r"^\s*\[?promo\]?\b",
]
_PROMO_SUBJECT_REGEX = [re.compile(p, re.IGNORECASE) for p in _PROMO_SUBJECT_PATTERNS]


def classify_inbox_message(from_hdr: str, subject: str) -> str | None:
    """Return 'promo' / 'transactional' / None for one message."""
    s = (from_hdr or "").lower()
    for rx in _PROMO_FROM_REGEX:
        if rx.search(s):
            return "promo"
    sub = subject or ""
    for rx in _PROMO_SUBJECT_REGEX:
        if rx.search(sub):
            return "promo"
    return None


def check_inbox_auto_snooze(messages: Iterable[dict]) -> list[dict]:
    """Identify auto-snoozable messages from a list of metadata dicts.

    Each message dict needs: id, from, subject, threadId, link.
    Returns alerts with kind="auto_snooze".
    """
    alerts: list[dict] = []
    for m in messages:
        verdict = classify_inbox_message(m.get("from", ""), m.get("subject", ""))
        if verdict:
            alerts.append({
                "kind": "auto_snooze",
                "title": m.get("subject") or "(no subject)",
                "detail": f"{verdict} — from {m.get('from', 'unknown')}",
                "link": m.get("link"),
                "data": {
                    "thread_id": m.get("threadId") or m.get("id"),
                    "classification": verdict,
                },
            })
    return alerts


# --------------------------------------------------------------------------- #
# #7 Stale relationship digest
# --------------------------------------------------------------------------- #


def check_stale_relationships(
    contacts: Iterable[dict],
    threshold_days: int = 60,
) -> list[dict]:
    """Return alerts for contacts whose last_interaction is older than threshold.

    Each contact dict needs: name, email, days_since_contact.
    """
    alerts: list[dict] = []
    for c in contacts:
        days = int(c.get("days_since_contact") or 0)
        if days < threshold_days:
            continue
        alerts.append({
            "kind": "stale_relationship",
            "title": f"{c.get('name') or c.get('email')}",
            "detail": f"haven't talked in {days}d",
            "link": c.get("link"),
            "data": {
                "email": c.get("email"),
                "days_since_contact": days,
            },
        })
    return alerts


# --------------------------------------------------------------------------- #
# #8 Reciprocity flagging
# --------------------------------------------------------------------------- #


def check_reciprocity(
    contacts: Iterable[dict],
    *,
    min_sent: int = 4,
    ratio_threshold: float = 4.0,
) -> list[dict]:
    """Flag contacts where you've sent N+ emails but received 0-1 back.

    Each contact dict needs: name, email, sent_last_60, received_last_60.
    """
    alerts: list[dict] = []
    for c in contacts:
        sent = int(c.get("sent_last_60") or 0)
        recv = int(c.get("received_last_60") or 0)
        if sent < min_sent:
            continue
        if recv == 0:
            ratio = float(sent)
        else:
            ratio = sent / recv
        if ratio < ratio_threshold:
            continue
        alerts.append({
            "kind": "reciprocity",
            "title": f"{c.get('name') or c.get('email')}",
            "detail": f"you sent {sent}, they sent {recv}",
            "link": c.get("link"),
            "data": {
                "sent_last_60": sent,
                "received_last_60": recv,
                "ratio": round(ratio, 2),
            },
        })
    return alerts


# --------------------------------------------------------------------------- #
# #13 Send-later + auto-follow-up
# --------------------------------------------------------------------------- #


def check_send_later(scheduled: Iterable[dict],
                     now: _dt.datetime | None = None) -> list[dict]:
    """Identify scheduled drafts that are due to send + queued followups
    that should fire because no reply has come in.

    Each entry: {id, kind, due_at_iso, ...}
       kind="initial" → due_at is when to send
       kind="followup" → due_at is when to fire the followup
    """
    now = now or _dt.datetime.now().astimezone()
    alerts: list[dict] = []
    for s in scheduled:
        due_iso = s.get("due_at_iso")
        if not due_iso:
            continue
        try:
            due_dt = _dt.datetime.fromisoformat(due_iso)
        except ValueError:
            continue
        if due_dt > now:
            continue  # not yet due
        kind = s.get("kind", "initial")
        title = s.get("subject") or "(no subject)"
        alerts.append({
            "kind": f"send_later_{kind}",
            "title": title,
            "detail": f"queued {kind} due now",
            "link": s.get("link"),
            "data": dict(s),
        })
    return alerts


# --------------------------------------------------------------------------- #
# #22 Sunday-night week-ahead plan
# --------------------------------------------------------------------------- #


def check_week_ahead(
    next_week_events: Iterable[dict],
    deadlines: Iterable[dict],
    open_commitments: Iterable[dict],
) -> list[dict]:
    """Compose a weekly plan brief.

    Returns one structured alert per finding category, plus a 'header'
    alert with summary counts.
    """
    events = list(next_week_events)
    deadlines = list(deadlines)
    commitments = list(open_commitments)
    high_stakes = sum(
        1 for e in events
        if any(t in (e.get("summary") or "").lower()
               for t in ["1:1", "customer", "demo", "board", "review"])
    )
    alerts: list[dict] = [{
        "kind": "week_ahead_summary",
        "title": "Week ahead",
        "detail": (
            f"{len(events)} meetings ({high_stakes} high-stakes), "
            f"{len(deadlines)} deadlines, {len(commitments)} open commitments"
        ),
        "link": None,
        "data": {
            "meeting_count": len(events),
            "high_stakes": high_stakes,
            "deadline_count": len(deadlines),
            "commitment_count": len(commitments),
        },
    }]
    for d in deadlines[:5]:
        alerts.append({
            "kind": "deadline",
            "title": d.get("title") or "(deadline)",
            "detail": f"due {d.get('due_at') or 'this week'}",
            "link": d.get("link"),
            "data": dict(d),
        })
    return alerts


# --------------------------------------------------------------------------- #
# #37 Communication retention sweep
# --------------------------------------------------------------------------- #


_RETENTION_FINANCIAL_TOKENS = [
    "invoice", "receipt", "wire", "bank", "tax", "1099", "w-9", "w9",
    "payroll", "expense", "bookkeeping", "audit", "accounting",
]


def check_retention_sweep(
    threads: Iterable[dict],
    *,
    cutoff_days: int = 365,
) -> list[dict]:
    """Find threads matching financial-retention tokens older than cutoff_days.

    Each thread: {id, subject, snippet, age_days, link}.
    """
    alerts: list[dict] = []
    for t in threads:
        age_days = int(t.get("age_days") or 0)
        if age_days < cutoff_days:
            continue
        haystack = ((t.get("subject") or "") + " " + (t.get("snippet") or "")).lower()
        if not any(tok in haystack for tok in _RETENTION_FINANCIAL_TOKENS):
            continue
        alerts.append({
            "kind": "retention_candidate",
            "title": t.get("subject") or "(no subject)",
            "detail": f"financial mail from {age_days}d ago",
            "link": t.get("link"),
            "data": {"thread_id": t.get("id"), "age_days": age_days},
        })
    return alerts


# --------------------------------------------------------------------------- #
# #38 End-of-day shutdown sequence
# --------------------------------------------------------------------------- #


def check_end_of_day(
    unfinished_tasks: Iterable[dict],
    unanswered_threads: Iterable[dict],
) -> list[dict]:
    """Produce a single end-of-day summary alert + per-item alerts."""
    tasks = list(unfinished_tasks)
    threads = list(unanswered_threads)
    alerts: list[dict] = [{
        "kind": "end_of_day_summary",
        "title": "End of day",
        "detail": (
            f"{len(tasks)} unfinished task(s), "
            f"{len(threads)} unanswered thread(s) — rolling to tomorrow"
        ),
        "link": None,
        "data": {
            "task_count": len(tasks),
            "thread_count": len(threads),
        },
    }]
    for t in tasks[:5]:
        alerts.append({
            "kind": "task_carryover",
            "title": t.get("title") or "(task)",
            "detail": "carry over to tomorrow",
            "link": t.get("link"),
            "data": dict(t),
        })
    return alerts


# --------------------------------------------------------------------------- #
# Registration with scanner — these get wired up by the wrapper at import time.
# --------------------------------------------------------------------------- #
# Rather than running live API calls at registry time, each check below uses a
# `lazy_fn` that fetches data when invoked. The wrapper module wires up live
# fetchers; tests can replace the fns with stubs.
# --------------------------------------------------------------------------- #


def register_p1_checks(
    *,
    inbox_fetcher,
    contacts_fetcher,
    reciprocity_fetcher,
    send_later_fetcher,
    week_ahead_fetcher,
    retention_fetcher,
    end_of_day_fetcher,
) -> None:
    """Register all 7 P1 checks with the scanner.

    Each fetcher is a no-arg callable returning the data shape the
    corresponding check_* function expects.
    """
    scanner.register_check(
        "p1_inbox_auto_snooze", cadence_hours=4,
        fn=lambda: check_inbox_auto_snooze(inbox_fetcher() or []),
        channel="json",
        description="Identify newsletters / promos / transactional mail to auto-snooze.",
    )
    scanner.register_check(
        "p1_stale_relationship_digest", cadence_hours=24 * 7,
        fn=lambda: check_stale_relationships(contacts_fetcher() or []),
        channel="chat",
        description="Weekly digest: contacts you haven't talked to in 60+ days.",
    )
    scanner.register_check(
        "p1_reciprocity_flag", cadence_hours=24 * 7,
        fn=lambda: check_reciprocity(reciprocity_fetcher() or []),
        channel="chat",
        description="Flag contacts where send/receive ratio is heavily one-sided.",
    )
    scanner.register_check(
        "p1_send_later_followup", cadence_hours=1,
        fn=lambda: check_send_later(send_later_fetcher() or []),
        channel="json",
        description="Fire scheduled drafts + queued auto-followups.",
    )
    scanner.register_check(
        "p1_sunday_week_ahead", cadence_hours=24 * 7,
        fn=lambda: _flatten_week_ahead(week_ahead_fetcher),
        channel="chat",
        description="Sunday-night plan for the week ahead.",
    )
    scanner.register_check(
        "p1_retention_sweep", cadence_hours=24,
        fn=lambda: check_retention_sweep(retention_fetcher() or []),
        channel="json",
        description="Daily sweep for financially-sensitive mail nearing retention age.",
    )
    scanner.register_check(
        "p1_end_of_day_shutdown", cadence_hours=24,
        fn=lambda: _flatten_end_of_day(end_of_day_fetcher),
        channel="chat",
        description="End-of-day shutdown brief: unfinished tasks + unanswered threads.",
    )


def _flatten_week_ahead(fetcher) -> list[dict]:
    """Fetcher returns a 3-tuple (events, deadlines, commitments)."""
    data = fetcher() or ([], [], [])
    events, deadlines, commitments = data
    return check_week_ahead(events, deadlines, commitments)


def _flatten_end_of_day(fetcher) -> list[dict]:
    data = fetcher() or ([], [])
    tasks, threads = data
    return check_end_of_day(tasks, threads)
