# © 2026 CoAssisted Workspace. Licensed under MIT.
"""P2 workflows — pure-logic functions that build DraftRequests + analyze data.

Each workflow takes already-fetched data and produces either:
  - DraftRequest(s) ready for brand_voice.compose() → draft_queue.enqueue()
  - Or, for #43 (cross-thread), passive context surfaced to the caller.

The MCP wrapper (tools/p2_workflows.py) handles fetching live data
from Gmail / Calendar / People APIs and feeds it through these.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

import brand_voice


# --------------------------------------------------------------------------- #
# #15 Brand-voice auto-draft for inbound
# --------------------------------------------------------------------------- #


def needs_reply_score(thread: dict) -> int:
    """Score a thread for 'needs my reply' from 0 (skip) to 100 (hot).

    Heuristics:
      - Last message is from someone other than the user → +50
      - Last message has a question mark → +30
      - Vendor/customer flagged in metadata → +10
      - Stale_days >= 3 → +10
    """
    score = 0
    last_msg = (thread.get("last_message") or {})
    if last_msg.get("from_self") is False:
        score += 50
    snippet = (last_msg.get("snippet") or "") + " " + (last_msg.get("body") or "")
    if "?" in snippet:
        score += 30
    if thread.get("is_vip"):
        score += 10
    if int(thread.get("stale_days") or 0) >= 3:
        score += 10
    return score


def build_inbound_reply_request(thread: dict, sender_name: Optional[str] = None) -> brand_voice.DraftRequest:
    """Build a DraftRequest for replying to one inbound thread."""
    last = thread.get("last_message") or {}
    sender = last.get("from") or thread.get("sender") or ""
    return brand_voice.DraftRequest(
        intent="reply",
        audience=thread.get("audience") or "customer",
        recipient_name=last.get("from_name") or thread.get("sender_name"),
        sender_name=sender_name,
        subject_hint=thread.get("subject") or last.get("subject"),
        context=last.get("body") or last.get("snippet") or "",
        seed_hint=thread.get("id"),
    )


def auto_draft_candidates(threads: Iterable[dict],
                          score_threshold: int = 60) -> list[dict]:
    """Filter threads to those worth auto-drafting. Returns annotated dicts."""
    out = []
    for t in threads:
        score = needs_reply_score(t)
        if score >= score_threshold:
            out.append({**t, "score": score})
    out.sort(key=lambda x: -x["score"])
    return out


# --------------------------------------------------------------------------- #
# #24 Conflict-aware auto-RSVP
# --------------------------------------------------------------------------- #


@dataclass
class TimeSlot:
    start: _dt.datetime
    end: _dt.datetime

    @property
    def duration_min(self) -> int:
        return int((self.end - self.start).total_seconds() / 60)


def find_alternative_slots(
    requested_start: _dt.datetime,
    requested_end: _dt.datetime,
    busy_blocks: list[tuple[_dt.datetime, _dt.datetime]],
    *,
    candidates_per_day: int = 2,
    days_ahead: int = 5,
    working_start_hour: int = 9,
    working_end_hour: int = 17,
) -> list[TimeSlot]:
    """Find up to candidates_per_day open slots over the next days_ahead days.

    Args:
        requested_start/end: the original conflicting invite time.
        busy_blocks: list of (start, end) tuples representing busy time.
        candidates_per_day: how many alternatives to propose per day.
        days_ahead: scan window after the requested day.

    Returns:
        list of TimeSlots that match the original duration and don't overlap
        any busy block.
    """
    duration = requested_end - requested_start
    tz = requested_start.tzinfo or _dt.timezone.utc
    slots: list[TimeSlot] = []
    busy_sorted = sorted(busy_blocks, key=lambda b: b[0])

    for d_offset in range(1, days_ahead + 1):
        day = (requested_start + _dt.timedelta(days=d_offset)).date()
        wd_start = _dt.datetime(day.year, day.month, day.day,
                                working_start_hour, tzinfo=tz)
        wd_end = _dt.datetime(day.year, day.month, day.day,
                              working_end_hour, tzinfo=tz)
        # Step through the day in 30-min increments.
        cursor = wd_start
        candidates_today = 0
        while cursor + duration <= wd_end and candidates_today < candidates_per_day:
            slot_end = cursor + duration
            conflict = any(
                not (slot_end <= b_start or cursor >= b_end)
                for b_start, b_end in busy_sorted
            )
            if not conflict:
                slots.append(TimeSlot(cursor, slot_end))
                candidates_today += 1
                cursor += _dt.timedelta(hours=1)  # space alternatives out
            else:
                cursor += _dt.timedelta(minutes=30)
    return slots


def build_rsvp_alternative_request(
    invite_subject: str,
    organizer_name: Optional[str],
    sender_name: Optional[str],
    alternatives: list[TimeSlot],
) -> brand_voice.DraftRequest:
    alt_lines = []
    for i, s in enumerate(alternatives, 1):
        alt_lines.append(
            f"  {i}. {s.start.strftime('%a %b %d, %-I:%M%p')} – "
            f"{s.end.strftime('%-I:%M%p')}"
        )
    context = (
        "I have a conflict with the proposed time. Here are some alternatives "
        "that work for me:\n\n" + "\n".join(alt_lines)
    )
    return brand_voice.DraftRequest(
        intent="rsvp_alternative",
        audience="internal_peer",
        recipient_name=organizer_name,
        sender_name=sender_name,
        subject_hint=invite_subject,
        context=context,
        seed_hint=invite_subject,
    )


# --------------------------------------------------------------------------- #
# #25 Ghost agenda generator
# --------------------------------------------------------------------------- #


def is_ghost_meeting(event: dict, user_email: Optional[str]) -> bool:
    """True if event has user as organizer + empty/short description."""
    desc = (event.get("description") or "").strip()
    if len(desc) >= 50:
        return False
    organizer = (event.get("organizer") or {}).get("email", "").lower()
    if not user_email:
        return True  # can't verify ownership; conservative pass
    return organizer == user_email.lower()


def build_ghost_agenda_request(
    event: dict,
    recent_thread_summary: str,
    sender_name: Optional[str] = None,
) -> brand_voice.DraftRequest:
    """Build agenda DraftRequest for a ghost meeting based on recent context."""
    attendees = [a.get("email") for a in event.get("attendees", []) or []]
    attendee_str = ", ".join(a for a in attendees if a)
    context = (
        f"Meeting: {event.get('summary', '(no title)')}\n"
        f"Attendees: {attendee_str}\n\n"
        f"Recent context with attendees:\n{recent_thread_summary}\n\n"
        "Build a 3-bullet agenda."
    )
    return brand_voice.DraftRequest(
        intent="agenda",
        audience="internal_peer",
        sender_name=sender_name,
        subject_hint=event.get("summary"),
        context=context,
        seed_hint=event.get("id"),
    )


# --------------------------------------------------------------------------- #
# #26 Birthday + anniversary watcher
# --------------------------------------------------------------------------- #


def find_today_birthdays(contacts: Iterable[dict],
                         today: _dt.date | None = None) -> list[dict]:
    """Filter contacts whose birthday is today.

    Each contact dict needs: name, email, birthday_md (string 'MM-DD').
    """
    today = today or _dt.date.today()
    today_md = today.strftime("%m-%d")
    out = []
    for c in contacts:
        if (c.get("birthday_md") or "") == today_md:
            out.append(c)
    return out


def build_birthday_request(contact: dict,
                           sender_name: Optional[str] = None) -> brand_voice.DraftRequest:
    return brand_voice.DraftRequest(
        intent="birthday",
        audience="personal",
        recipient_name=contact.get("name"),
        sender_name=sender_name,
        subject_hint="Happy birthday",
        context=f"Birthday note for {contact.get('name')}.",
        seed_hint=f"bday:{contact.get('email')}",
    )


# --------------------------------------------------------------------------- #
# #40 Introduction follow-through tracker
# --------------------------------------------------------------------------- #


# Patterns in subject/body that signal "this is an introduction"
_INTRO_PATTERNS = [
    r"\bintro(duction)?\b",
    r"\bmeet\b.*\bmeet\b",                  # "meet X, meet Y"
    r"\b(connecting|connect)ing you\b",
    r"^\s*re:\s*intro\b",
]
_INTRO_REGEX = [re.compile(p, re.IGNORECASE) for p in _INTRO_PATTERNS]


def is_intro_thread(subject: str, body: str) -> bool:
    haystack = (subject or "") + " " + (body or "")
    return any(rx.search(haystack) for rx in _INTRO_REGEX)


def find_unfollowed_intros(
    threads: Iterable[dict],
    *,
    user_email: str,
    days_threshold: int = 14,
    today: _dt.date | None = None,
) -> list[dict]:
    """For each thread where user introduced A and B (CC pattern), check
    whether A and B have direct activity in the threshold window.

    Each thread dict needs:
        subject, body, from (user), to: [a, b], created_at, has_followup_threads (bool)
    """
    today = today or _dt.date.today()
    out = []
    for t in threads:
        if not is_intro_thread(t.get("subject") or "", t.get("body") or ""):
            continue
        if (t.get("from") or "").lower() != user_email.lower():
            continue
        try:
            intro_dt = _dt.datetime.fromisoformat(
                (t.get("created_at") or "").replace("Z", "+00:00")
            ).date()
        except (ValueError, AttributeError):
            continue
        days = (today - intro_dt).days
        if days < days_threshold:
            continue
        if t.get("has_followup_threads"):
            continue
        out.append({**t, "days_since_intro": days})
    return out


def build_intro_followup_request(intro_thread: dict,
                                 sender_name: Optional[str] = None) -> brand_voice.DraftRequest:
    recipients = ", ".join(intro_thread.get("to") or [])
    return brand_voice.DraftRequest(
        intent="intro_followup",
        audience="customer",
        sender_name=sender_name,
        subject_hint=intro_thread.get("subject"),
        context=(
            f"Introduced {recipients} {intro_thread.get('days_since_intro', '?')} days "
            f"ago. No direct correspondence between them yet. Gentle nudge."
        ),
        seed_hint=intro_thread.get("id"),
    )


# --------------------------------------------------------------------------- #
# #43 Cross-thread context surfacer (passive — no compose)
# --------------------------------------------------------------------------- #


def find_other_open_threads(
    target_email: str,
    all_threads: Iterable[dict],
    *,
    exclude_thread_id: Optional[str] = None,
    open_only: bool = True,
) -> list[dict]:
    """Find other threads with the same recipient. Used to surface context
    while drafting a reply.

    Each thread dict needs: id, participants (list of emails), subject,
    last_activity, status (e.g. 'open', 'closed').
    """
    out = []
    target = (target_email or "").lower()
    for t in all_threads:
        if t.get("id") == exclude_thread_id:
            continue
        participants = [p.lower() for p in (t.get("participants") or [])]
        if target not in participants:
            continue
        if open_only and t.get("status") and t.get("status") != "open":
            continue
        out.append(t)
    out.sort(key=lambda x: x.get("last_activity") or "", reverse=True)
    return out


# --------------------------------------------------------------------------- #
# #74 Multi-recipient meeting coordinator
# --------------------------------------------------------------------------- #


def find_common_free_slots(
    busy_by_person: dict[str, list[tuple[_dt.datetime, _dt.datetime]]],
    *,
    duration_min: int = 30,
    days_ahead: int = 7,
    candidates: int = 3,
    working_start_hour: int = 9,
    working_end_hour: int = 17,
    now: _dt.datetime | None = None,
) -> list[TimeSlot]:
    """Find up to `candidates` slots where all listed people are free.

    busy_by_person: {email: [(start, end), ...]} per person.
    """
    now = now or _dt.datetime.now().astimezone()
    tz = now.tzinfo or _dt.timezone.utc
    duration = _dt.timedelta(minutes=duration_min)

    # Merge all busy intervals into one combined list.
    all_busy: list[tuple[_dt.datetime, _dt.datetime]] = []
    for ranges in busy_by_person.values():
        all_busy.extend(ranges)

    found: list[TimeSlot] = []
    for d_offset in range(1, days_ahead + 1):
        day = (now + _dt.timedelta(days=d_offset)).date()
        wd_start = _dt.datetime(day.year, day.month, day.day,
                                working_start_hour, tzinfo=tz)
        wd_end = _dt.datetime(day.year, day.month, day.day,
                              working_end_hour, tzinfo=tz)
        cursor = wd_start
        while cursor + duration <= wd_end and len(found) < candidates:
            slot_end = cursor + duration
            conflict = any(
                not (slot_end <= b_start or cursor >= b_end)
                for b_start, b_end in all_busy
            )
            if not conflict:
                found.append(TimeSlot(cursor, slot_end))
                cursor += _dt.timedelta(hours=2)  # space candidates out
            else:
                cursor += _dt.timedelta(minutes=30)
        if len(found) >= candidates:
            break
    return found


def build_scheduling_poll_request(
    invitees: list[str],
    proposed_slots: list[TimeSlot],
    sender_name: Optional[str] = None,
    meeting_topic: Optional[str] = None,
) -> brand_voice.DraftRequest:
    slot_lines = []
    for i, s in enumerate(proposed_slots, 1):
        slot_lines.append(
            f"  {i}. {s.start.strftime('%a %b %d, %-I:%M%p')} – "
            f"{s.end.strftime('%-I:%M%p')}"
        )
    context = (
        f"Coordinating a {meeting_topic or 'meeting'} between {len(invitees)} people. "
        f"Proposed slots:\n\n" + "\n".join(slot_lines)
    )
    return brand_voice.DraftRequest(
        intent="scheduling_poll",
        audience="internal_peer",
        sender_name=sender_name,
        subject_hint=meeting_topic or "Scheduling",
        context=context,
        seed_hint=",".join(sorted(invitees)),
    )


# --------------------------------------------------------------------------- #
# #77 Foreign-language translate + reply
# --------------------------------------------------------------------------- #


# Quick heuristic to identify the dominant language from common patterns.
# Real implementation could plug into a translation API; this gives us
# a no-LLM fallback that catches the most common cases.
_LANG_FINGERPRINTS = {
    "fr": [r"\b(bonjour|merci|cordialement|s'il vous pla[iî]t)\b"],
    "es": [r"\b(hola|gracias|saludos|por favor|cordialmente)\b"],
    "de": [r"\b(guten tag|danke|sehr geehrte|mit freundlichen gr[uü]ßen)\b"],
    "pt": [r"\b(ol[aá]|obrigad[ao]|atenciosamente)\b"],
    "it": [r"\b(buongiorno|grazie|cordiali saluti)\b"],
}
_LANG_REGEX = {
    code: [re.compile(p, re.IGNORECASE) for p in patterns]
    for code, patterns in _LANG_FINGERPRINTS.items()
}


def detect_language(text: str) -> Optional[str]:
    """Return ISO code if the text matches a known fingerprint, else None."""
    if not text:
        return None
    scores: dict[str, int] = {}
    for code, regexes in _LANG_REGEX.items():
        for rx in regexes:
            if rx.search(text):
                scores[code] = scores.get(code, 0) + 1
    if not scores:
        return None
    return max(scores.items(), key=lambda kv: kv[1])[0]


def build_translate_reply_request(
    inbound_body: str,
    target_language: str,
    recipient_name: Optional[str] = None,
    sender_name: Optional[str] = None,
    subject_hint: Optional[str] = None,
) -> brand_voice.DraftRequest:
    return brand_voice.DraftRequest(
        intent="translate_reply",
        audience="customer",
        recipient_name=recipient_name,
        sender_name=sender_name,
        subject_hint=subject_hint,
        context=inbound_body,
        target_language=target_language,
    )
