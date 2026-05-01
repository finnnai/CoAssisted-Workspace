# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Morning brief — pure-logic composition layer.

Takes already-fetched data (calendar events, unread inbox threads, AP
outstanding requests, CRM signals) and composes a structured "here are
the 5 things that need you today" brief.

The MCP tool wrapper (tools/morning_brief.py) is responsible for
fetching the source data; this module never makes API calls. Keeps
the logic deterministic + easy to unit-test.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Iterable


# Priority weights — drive the "top 5 today" ranking across all sections.
# Higher = more urgent. Tunable; smoke + tests assert on the resulting order.
_WEIGHTS = {
    # AP loop
    "ap_overdue_reminder": 80,           # vendor request blew past its cadence window
    "ap_outstanding": 40,                # AP request still open

    # Calendar
    "meeting_high_stakes": 70,           # 1:1, board, customer call, contract
    "meeting_today": 30,                 # generic meeting today
    "meeting_starting_soon": 60,         # within 90 minutes

    # Inbox
    "inbox_vip_unread": 75,              # VIP contact emailed, you haven't replied
    "inbox_thread_stale": 35,            # waiting-for-reply on a thread > 3 days
    "inbox_unread_count": 10,            # generic unread volume signal

    # Relationships
    "stale_relationship": 20,            # contact you haven't talked to in 60+ days
}


@dataclass
class BriefItem:
    """One actionable item in the brief."""
    section: str            # "calendar" | "inbox" | "ap" | "relationships"
    kind: str               # short stable code, see _WEIGHTS above
    title: str              # e.g. "Customer call: Anthropic at 10:00am"
    detail: str             # one-line elaboration
    priority: int           # weight from _WEIGHTS
    link: str | None = None # url to the source (event, thread, etc.)
    when: str | None = None # ISO timestamp if time-bound

    def to_dict(self) -> dict:
        return {
            "section": self.section,
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "priority": self.priority,
            "link": self.link,
            "when": self.when,
        }


@dataclass
class MorningBrief:
    """The full brief, structured by section + a top-5 cross-section ranking."""
    date: str
    user_email: str | None
    items: list[BriefItem] = field(default_factory=list)

    # Aggregated counts for the header line
    summary: dict = field(default_factory=dict)

    @property
    def top_items(self) -> list[BriefItem]:
        """The 5 highest-priority items across all sections."""
        return sorted(self.items, key=lambda i: -i.priority)[:5]

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "user_email": self.user_email,
            "summary": dict(self.summary),
            "top_5": [i.to_dict() for i in self.top_items],
            "by_section": {
                "calendar": [i.to_dict() for i in self.items if i.section == "calendar"],
                "inbox":    [i.to_dict() for i in self.items if i.section == "inbox"],
                "ap":       [i.to_dict() for i in self.items if i.section == "ap"],
                "relationships": [
                    i.to_dict() for i in self.items if i.section == "relationships"
                ],
            },
        }


# --------------------------------------------------------------------------- #
# Helpers — calendar event scoring
# --------------------------------------------------------------------------- #


# Patterns that indicate a "high-stakes" meeting.
_HIGH_STAKES_PATTERNS = [
    r"\bboard\b", r"\b1:1\b", r"\b1-1\b", r"\bone[- ]on[- ]one\b",
    r"\bquarterly\b", r"\breview\b", r"\binterview\b", r"\bperformance\b",
    r"\bcontract\b", r"\bnegotiation\b", r"\bclose\b",
    r"\bdemo\b", r"\bcustomer\b", r"\bclient\b", r"\bproposal\b",
    r"\bkickoff\b", r"\bstrategy\b", r"\boffsite\b",
]
_HIGH_STAKES_REGEX = [re.compile(p, re.IGNORECASE) for p in _HIGH_STAKES_PATTERNS]


def _is_high_stakes(summary: str | None, attendee_count: int) -> bool:
    if not summary:
        return False
    for rx in _HIGH_STAKES_REGEX:
        if rx.search(summary):
            return True
    # Small group meetings with 2-5 attendees are often higher-stakes than
    # all-hands but lower than 1:1 — heuristic only.
    if 2 <= attendee_count <= 5 and "all" not in summary.lower():
        return False  # not enough signal alone
    return False


def _parse_iso(ts: str | None) -> _dt.datetime | None:
    if not ts:
        return None
    try:
        # Calendar API returns strings like "2026-04-28T09:00:00-07:00"
        # or "2026-04-28" for all-day events.
        if len(ts) == 10:  # all-day
            return _dt.datetime.fromisoformat(ts).replace(tzinfo=_dt.timezone.utc)
        return _dt.datetime.fromisoformat(ts)
    except ValueError:
        return None


def _format_time(dt: _dt.datetime | None) -> str:
    if dt is None:
        return ""
    return dt.strftime("%-I:%M%p").lower()


# --------------------------------------------------------------------------- #
# Section composers
# --------------------------------------------------------------------------- #


def _calendar_items(events: Iterable[dict], now: _dt.datetime) -> list[BriefItem]:
    """Turn calendar events into BriefItems."""
    out: list[BriefItem] = []
    for e in events:
        summary = e.get("summary") or "(no title)"
        start = e.get("start") or {}
        start_ts = start.get("dateTime") or start.get("date")
        start_dt = _parse_iso(start_ts)
        attendees = e.get("attendees") or []

        # Time-to-meeting in minutes (None if all-day or missing).
        if start_dt and start_dt.tzinfo:
            now_local = now.astimezone(start_dt.tzinfo)
            delta_min = int((start_dt - now_local).total_seconds() / 60)
        else:
            delta_min = None

        # Pick the kind code based on stakes + proximity.
        if _is_high_stakes(summary, len(attendees)):
            kind = "meeting_high_stakes"
        elif delta_min is not None and 0 <= delta_min <= 90:
            kind = "meeting_starting_soon"
        else:
            kind = "meeting_today"

        priority = _WEIGHTS.get(kind, 0)
        # Bonus for happening soon (within 2 hours of now)
        if delta_min is not None and 0 <= delta_min <= 120:
            priority += max(0, 30 - (delta_min // 4))

        time_str = _format_time(start_dt)
        when_label = time_str or "today"
        att_count = len(attendees)
        att_str = f" with {att_count} attendees" if att_count >= 2 else ""

        out.append(BriefItem(
            section="calendar",
            kind=kind,
            title=f"{summary} — {when_label}",
            detail=f"{when_label}{att_str}",
            priority=priority,
            link=e.get("html_link") or e.get("htmlLink"),
            when=start_ts,
        ))
    return out


def _inbox_items(
    needs_reply: Iterable[dict],
    vip_emails: set[str] | None = None,
    unread_count: int | None = None,
) -> list[BriefItem]:
    """Turn 'needs reply' threads into BriefItems.

    needs_reply shape:
        {"id": str, "from": str, "subject": str, "snippet": str,
         "stale_days": int, "link": str|None}
    """
    vip_emails = {e.lower() for e in (vip_emails or set())}
    out: list[BriefItem] = []

    for t in needs_reply:
        sender = (t.get("from") or "").lower()
        sender_email = _extract_email(sender)
        is_vip = sender_email in vip_emails
        stale_days = int(t.get("stale_days") or 0)

        if is_vip:
            kind = "inbox_vip_unread"
        elif stale_days >= 3:
            kind = "inbox_thread_stale"
        else:
            continue  # too fresh / not VIP — skip

        priority = _WEIGHTS.get(kind, 0)
        if stale_days >= 7:
            priority += 15  # extra-stale bump

        out.append(BriefItem(
            section="inbox",
            kind=kind,
            title=t.get("subject") or "(no subject)",
            detail=f"from {sender_email or sender} — waiting {stale_days}d",
            priority=priority,
            link=t.get("link"),
        ))

    # Generic unread volume signal — only if it's notable.
    if unread_count is not None and unread_count >= 20:
        out.append(BriefItem(
            section="inbox",
            kind="inbox_unread_count",
            title=f"{unread_count} unread in inbox",
            detail="generic volume — clear when you can",
            priority=_WEIGHTS["inbox_unread_count"],
        ))

    return out


def _ap_items(
    outstanding: Iterable[dict],
    overdue: Iterable[dict],
) -> list[BriefItem]:
    """Turn AP-loop entries into BriefItems."""
    out: list[BriefItem] = []
    overdue_keys = {r.get("content_key") for r in overdue}

    for r in outstanding:
        ck = r.get("content_key")
        is_overdue = ck in overdue_keys
        kind = "ap_overdue_reminder" if is_overdue else "ap_outstanding"
        priority = _WEIGHTS[kind]

        vendor = r.get("vendor") or r.get("vendor_email") or "?"
        invoice = r.get("invoice_number") or r.get("source_id", "")
        missing = r.get("missing_fields") or []
        miss_str = ", ".join(missing[:3]) if missing else "info"

        out.append(BriefItem(
            section="ap",
            kind=kind,
            title=f"{vendor} — invoice {invoice}",
            detail=f"awaiting {miss_str}" + (" (overdue)" if is_overdue else ""),
            priority=priority,
            link=r.get("link"),
        ))
    return out


def _relationship_items(stale_contacts: Iterable[dict]) -> list[BriefItem]:
    """Turn stale-contact entries into BriefItems.

    stale_contacts shape:
        {"name": str, "email": str, "days_since_contact": int, "link": str|None}
    """
    out: list[BriefItem] = []
    for c in stale_contacts:
        days = int(c.get("days_since_contact") or 0)
        if days < 60:
            continue
        priority = _WEIGHTS["stale_relationship"] + min(20, (days - 60) // 10)
        out.append(BriefItem(
            section="relationships",
            kind="stale_relationship",
            title=f"{c.get('name') or c.get('email')}",
            detail=f"haven't talked in {days}d",
            priority=priority,
            link=c.get("link"),
        ))
    return out


def _extract_email(s: str) -> str:
    if not s:
        return ""
    m = re.search(r"<([^>]+)>", s)
    if m:
        return m.group(1).strip().lower()
    if "@" in s:
        return s.strip().lower()
    return ""


# --------------------------------------------------------------------------- #
# Top-level composer
# --------------------------------------------------------------------------- #


def compose_brief(
    *,
    date: str,
    user_email: str | None = None,
    calendar_events: Iterable[dict] = (),
    inbox_needs_reply: Iterable[dict] = (),
    inbox_unread_count: int | None = None,
    vip_emails: set[str] | None = None,
    ap_outstanding: Iterable[dict] = (),
    ap_overdue: Iterable[dict] = (),
    stale_contacts: Iterable[dict] = (),
    now: _dt.datetime | None = None,
) -> MorningBrief:
    """Assemble a MorningBrief from already-fetched data.

    Args:
        date: human-readable date for the header (e.g. "2026-04-28").
        user_email: the authed user (for sender exclusion).
        calendar_events: list of Calendar API event dicts (today's events).
        inbox_needs_reply: list of "needs reply" threads (see _inbox_items shape).
        inbox_unread_count: total unread inbox count.
        vip_emails: set of email addresses tagged as VIP.
        ap_outstanding: list of vendor_followups.list_open() results.
        ap_overdue: list of vendor_followups.due_for_reminder() results.
        stale_contacts: list of contacts past their stale threshold.
        now: override "now" for testing (defaults to datetime.now(tz=local)).

    Returns:
        MorningBrief with all items + the derived top-5 ranking.
    """
    now = now or _dt.datetime.now().astimezone()
    items: list[BriefItem] = []

    cal = list(calendar_events)
    items.extend(_calendar_items(cal, now))
    items.extend(_inbox_items(
        inbox_needs_reply, vip_emails=vip_emails, unread_count=inbox_unread_count,
    ))
    items.extend(_ap_items(ap_outstanding, ap_overdue))
    items.extend(_relationship_items(stale_contacts))

    summary = {
        "calendar_count": len(cal),
        "inbox_needs_reply": sum(
            1 for i in items
            if i.section == "inbox" and i.kind != "inbox_unread_count"
        ),
        "inbox_unread_total": inbox_unread_count or 0,
        "ap_outstanding": len(list(ap_outstanding)) if hasattr(ap_outstanding, "__len__") else None,
        "ap_overdue": sum(1 for i in items if i.kind == "ap_overdue_reminder"),
        "stale_relationships": sum(
            1 for i in items if i.kind == "stale_relationship"
        ),
    }

    return MorningBrief(
        date=date,
        user_email=user_email,
        items=items,
        summary=summary,
    )


# --------------------------------------------------------------------------- #
# Markdown rendering — for human-readable output
# --------------------------------------------------------------------------- #


def render_markdown(brief: MorningBrief) -> str:
    """Render a brief as a clean markdown summary."""
    lines: list[str] = []
    lines.append(f"# Morning brief — {brief.date}")
    if brief.user_email:
        lines.append(f"_for {brief.user_email}_")
    lines.append("")

    s = brief.summary
    bits = []
    if s.get("calendar_count"):
        bits.append(f"{s['calendar_count']} meetings")
    if s.get("inbox_needs_reply"):
        bits.append(f"{s['inbox_needs_reply']} threads need reply")
    if s.get("ap_overdue"):
        bits.append(f"{s['ap_overdue']} AP items overdue")
    if bits:
        lines.append("**Today:** " + ", ".join(bits))
        lines.append("")

    # Top-5 ranking
    top = brief.top_items
    if top:
        lines.append("## Top 5 today")
        for i, it in enumerate(top, 1):
            lines.append(f"{i}. **{it.title}** — {it.detail}")
        lines.append("")

    # By section
    sections = [
        ("calendar", "Calendar"),
        ("inbox", "Inbox"),
        ("ap", "AP loop"),
        ("relationships", "Relationships"),
    ]
    for sec_code, sec_label in sections:
        sec_items = [i for i in brief.items if i.section == sec_code]
        if not sec_items:
            continue
        lines.append(f"## {sec_label}")
        for it in sorted(sec_items, key=lambda x: -x.priority):
            lines.append(f"- {it.title} — {it.detail}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
