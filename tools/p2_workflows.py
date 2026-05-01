# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP wrappers for the 8 P2 workflows.

Each tool takes user-provided context, calls the corresponding p2_workflows
helper to build a DraftRequest, runs brand_voice.compose() on it, and queues
the result via draft_queue.

Tools:
  workflow_auto_draft_inbound        — #15
  workflow_rsvp_with_alternatives    — #24
  workflow_ghost_agenda              — #25
  workflow_birthday_check            — #26
  workflow_intro_followups           — #40
  workflow_cross_thread_context      — #43 (passive)
  workflow_meeting_poll              — #74
  workflow_translate_reply           — #77
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import brand_voice
import draft_queue
import p2_workflows as p2
from errors import format_error
from logging_util import log


# --------------------------------------------------------------------------- #
# Helper — common compose+enqueue chain
# --------------------------------------------------------------------------- #


def _compose_and_queue(
    req: brand_voice.DraftRequest,
    *,
    target,
    cc=None,
    kind: str,
    source_ref: Optional[str] = None,
    template_only: bool = False,
    enqueue: bool = True,
) -> dict:
    if template_only:
        d = brand_voice.compose_template_only(req)
    else:
        d = brand_voice.compose(req)
    payload = {"draft": d.to_dict()}
    if not enqueue:
        payload["queued"] = False
        return payload
    eid = draft_queue.enqueue(
        kind=kind,
        subject=d.subject,
        body_plain=d.plain,
        body_html=d.html,
        target=target,
        cc=cc,
        source_ref=source_ref,
        meta={"intent": req.intent, "audience": req.audience,
              "voice_used": d.voice_used,
              "estimated_cost_usd": d.estimated_cost_usd},
    )
    payload["entry_id"] = eid
    payload["queued"] = True
    return payload


# --------------------------------------------------------------------------- #
# #15 — Auto-draft inbound
# --------------------------------------------------------------------------- #


class AutoDraftInboundInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    threads: list[dict] = Field(
        ...,
        description=("List of thread dicts. Each must include 'id', 'subject', "
                     "'last_message': {from, from_name, body, snippet, from_self}, "
                     "'sender' (email), 'audience', and optionally 'is_vip', "
                     "'stale_days'."),
    )
    score_threshold: int = Field(default=60, ge=0, le=200)
    sender_name: Optional[str] = Field(default=None)
    template_only: bool = Field(default=False)
    enqueue: bool = Field(default=True)


# --------------------------------------------------------------------------- #
# #24 — RSVP alternatives
# --------------------------------------------------------------------------- #


class RsvpAltInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    invite_subject: str = Field(...)
    organizer_email: str = Field(...)
    organizer_name: Optional[str] = Field(default=None)
    requested_start_iso: str = Field(...)
    requested_end_iso: str = Field(...)
    busy_blocks: list[dict] = Field(
        default_factory=list,
        description=("List of {start_iso, end_iso} representing busy time. "
                     "Caller is responsible for fetching free/busy."),
    )
    candidates_per_day: int = Field(default=2, ge=1, le=5)
    days_ahead: int = Field(default=5, ge=1, le=14)
    sender_name: Optional[str] = Field(default=None)
    template_only: bool = Field(default=False)
    enqueue: bool = Field(default=True)


# --------------------------------------------------------------------------- #
# #25 — Ghost agenda
# --------------------------------------------------------------------------- #


class GhostAgendaInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    event: dict = Field(
        ...,
        description=("Calendar event dict. Must include 'id', 'summary', "
                     "'description', 'organizer.email', 'attendees'."),
    )
    user_email: str = Field(..., description="Authenticated user email.")
    recent_thread_summary: str = Field(
        default="", description="Pre-fetched summary of recent thread/chat with attendees.",
    )
    target: list[str] = Field(..., description="Recipients (attendee emails).")
    sender_name: Optional[str] = Field(default=None)
    template_only: bool = Field(default=False)
    enqueue: bool = Field(default=True)


# --------------------------------------------------------------------------- #
# #26 — Birthday check
# --------------------------------------------------------------------------- #


class BirthdayCheckInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    contacts: list[dict] = Field(
        ...,
        description=("Contacts with birthday metadata. "
                     "Each must include 'name', 'email', 'birthday_md' ('MM-DD')."),
    )
    sender_name: Optional[str] = Field(default=None)
    template_only: bool = Field(default=False)
    enqueue: bool = Field(default=True)


# --------------------------------------------------------------------------- #
# #40 — Intro follow-through
# --------------------------------------------------------------------------- #


class IntroFollowupsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    threads: list[dict] = Field(
        ...,
        description=("Sent-mail threads to scan. Each must include 'id', "
                     "'subject', 'body', 'from' (user), 'to' (list), "
                     "'created_at', 'has_followup_threads' (bool)."),
    )
    user_email: str = Field(...)
    days_threshold: int = Field(default=14, ge=1, le=90)
    sender_name: Optional[str] = Field(default=None)
    template_only: bool = Field(default=False)
    enqueue: bool = Field(default=True)


# --------------------------------------------------------------------------- #
# #43 — Cross-thread context
# --------------------------------------------------------------------------- #


class CrossThreadInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    target_email: str = Field(...)
    all_threads: list[dict] = Field(
        ...,
        description=("Pre-fetched threads with this person. Each: 'id', "
                     "'participants' (list), 'subject', 'last_activity', 'status'."),
    )
    exclude_thread_id: Optional[str] = Field(default=None)
    open_only: bool = Field(default=True)


# --------------------------------------------------------------------------- #
# #74 — Meeting poll
# --------------------------------------------------------------------------- #


class MeetingPollInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    invitees: list[str] = Field(...)
    busy_by_person: dict = Field(
        ...,
        description=("{email: [{start_iso, end_iso}, ...]} from free/busy per "
                     "invitee. Caller fetches via Calendar freeBusy."),
    )
    duration_min: int = Field(default=30, ge=15, le=240)
    days_ahead: int = Field(default=7, ge=1, le=21)
    candidates: int = Field(default=3, ge=1, le=10)
    meeting_topic: Optional[str] = Field(default=None)
    sender_name: Optional[str] = Field(default=None)
    template_only: bool = Field(default=False)
    enqueue: bool = Field(default=True)


# --------------------------------------------------------------------------- #
# #77 — Translate + reply
# --------------------------------------------------------------------------- #


class TranslateReplyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    inbound_body: str = Field(...)
    target_language: Optional[str] = Field(
        default=None,
        description=("ISO code (fr, es, de, pt, it). If omitted, detected "
                     "automatically from the inbound."),
    )
    target: str = Field(..., description="Recipient email.")
    recipient_name: Optional[str] = Field(default=None)
    sender_name: Optional[str] = Field(default=None)
    subject_hint: Optional[str] = Field(default=None)
    template_only: bool = Field(default=False)
    enqueue: bool = Field(default=True)


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def _parse_iso(s: str) -> _dt.datetime:
    return _dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_auto_draft_inbound",
        annotations={"title": "Auto-draft replies for needs-reply threads",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    async def workflow_auto_draft_inbound(params: AutoDraftInboundInput) -> str:
        """For each thread that scores above the threshold, compose a brand-voice
        reply and queue it for review. Returns list of queued entries."""
        try:
            candidates = p2.auto_draft_candidates(
                params.threads, score_threshold=params.score_threshold,
            )
            log.info("auto_draft_inbound: %d candidates", len(candidates))
            results = []
            for t in candidates:
                req = p2.build_inbound_reply_request(t, sender_name=params.sender_name)
                last = t.get("last_message") or {}
                payload = _compose_and_queue(
                    req,
                    target=t.get("sender") or last.get("from") or "",
                    kind="auto_reply_inbound",
                    source_ref=f"thread:{t.get('id')}",
                    template_only=params.template_only,
                    enqueue=params.enqueue,
                )
                payload["thread_id"] = t.get("id")
                payload["score"] = t.get("score")
                results.append(payload)
            return json.dumps({"count": len(results), "drafts": results},
                              indent=2, default=str)
        except Exception as e:
            return format_error("workflow_auto_draft_inbound", e)

    @mcp.tool(
        name="workflow_rsvp_with_alternatives",
        annotations={"title": "Decline + propose alternative meeting times",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    async def workflow_rsvp_with_alternatives(params: RsvpAltInput) -> str:
        """Decline a conflicting invite and propose alternates from your free/busy."""
        try:
            req_start = _parse_iso(params.requested_start_iso)
            req_end = _parse_iso(params.requested_end_iso)
            busy = [
                (_parse_iso(b["start_iso"]), _parse_iso(b["end_iso"]))
                for b in params.busy_blocks
            ]
            slots = p2.find_alternative_slots(
                req_start, req_end, busy,
                candidates_per_day=params.candidates_per_day,
                days_ahead=params.days_ahead,
            )
            if not slots:
                return json.dumps({"alternatives_found": 0,
                                   "note": "No open alternatives in the window."})
            req = p2.build_rsvp_alternative_request(
                invite_subject=params.invite_subject,
                organizer_name=params.organizer_name,
                sender_name=params.sender_name,
                alternatives=slots,
            )
            result = _compose_and_queue(
                req, target=params.organizer_email,
                kind="rsvp_alternative",
                source_ref=f"invite:{params.invite_subject}",
                template_only=params.template_only,
                enqueue=params.enqueue,
            )
            result["alternatives"] = [
                {"start": s.start.isoformat(), "end": s.end.isoformat()}
                for s in slots
            ]
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_rsvp_with_alternatives", e)

    @mcp.tool(
        name="workflow_ghost_agenda",
        annotations={"title": "Generate an agenda for an empty meeting",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    async def workflow_ghost_agenda(params: GhostAgendaInput) -> str:
        """If the event has no description and you organized it, draft a 3-bullet
        agenda from recent context with attendees."""
        try:
            if not p2.is_ghost_meeting(params.event, params.user_email):
                return json.dumps({"is_ghost": False,
                                   "note": "Event has a description or different organizer."})
            req = p2.build_ghost_agenda_request(
                params.event, params.recent_thread_summary,
                sender_name=params.sender_name,
            )
            result = _compose_and_queue(
                req, target=params.target,
                kind="ghost_agenda",
                source_ref=f"event:{params.event.get('id')}",
                template_only=params.template_only,
                enqueue=params.enqueue,
            )
            result["is_ghost"] = True
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_ghost_agenda", e)

    @mcp.tool(
        name="workflow_birthday_check",
        annotations={"title": "Check today's birthdays + draft notes",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    async def workflow_birthday_check(params: BirthdayCheckInput) -> str:
        """For every contact whose birthday is today, queue a brand-voice note."""
        try:
            today_birthdays = p2.find_today_birthdays(params.contacts)
            results = []
            for c in today_birthdays:
                req = p2.build_birthday_request(c, sender_name=params.sender_name)
                results.append(_compose_and_queue(
                    req, target=c.get("email"),
                    kind="birthday_note",
                    source_ref=f"contact:{c.get('email')}",
                    template_only=params.template_only,
                    enqueue=params.enqueue,
                ))
            return json.dumps({"birthdays_today": len(today_birthdays),
                               "drafts": results}, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_birthday_check", e)

    @mcp.tool(
        name="workflow_intro_followups",
        annotations={"title": "Find unfollowed intros + draft nudges",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    async def workflow_intro_followups(params: IntroFollowupsInput) -> str:
        """For each intro thread without follow-through in the threshold window,
        queue a gentle follow-up nudge."""
        try:
            unfollowed = p2.find_unfollowed_intros(
                params.threads, user_email=params.user_email,
                days_threshold=params.days_threshold,
            )
            results = []
            for t in unfollowed:
                req = p2.build_intro_followup_request(t, sender_name=params.sender_name)
                results.append(_compose_and_queue(
                    req, target=t.get("to") or [],
                    kind="intro_followup",
                    source_ref=f"intro:{t.get('id')}",
                    template_only=params.template_only,
                    enqueue=params.enqueue,
                ))
            return json.dumps({"unfollowed_intros": len(unfollowed),
                               "drafts": results}, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_intro_followups", e)

    @mcp.tool(
        name="workflow_cross_thread_context",
        annotations={"title": "Surface other open threads with the same person",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_cross_thread_context(params: CrossThreadInput) -> str:
        """When drafting a reply, surface other open threads with the same person.
        Returns thread metadata — no compose, just context."""
        try:
            related = p2.find_other_open_threads(
                params.target_email, params.all_threads,
                exclude_thread_id=params.exclude_thread_id,
                open_only=params.open_only,
            )
            return json.dumps({"target": params.target_email,
                               "open_threads": len(related),
                               "threads": related}, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_cross_thread_context", e)

    @mcp.tool(
        name="workflow_meeting_poll",
        annotations={"title": "Coordinate a meeting across recipients",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    async def workflow_meeting_poll(params: MeetingPollInput) -> str:
        """Find common free slots across invitees + draft a poll email."""
        try:
            busy_parsed: dict[str, list[tuple[_dt.datetime, _dt.datetime]]] = {}
            for email, ranges in params.busy_by_person.items():
                busy_parsed[email] = [
                    (_parse_iso(r["start_iso"]), _parse_iso(r["end_iso"]))
                    for r in ranges
                ]
            slots = p2.find_common_free_slots(
                busy_parsed,
                duration_min=params.duration_min,
                days_ahead=params.days_ahead,
                candidates=params.candidates,
            )
            if not slots:
                return json.dumps({"slots_found": 0,
                                   "note": "No common free time in the window."})
            req = p2.build_scheduling_poll_request(
                invitees=params.invitees,
                proposed_slots=slots,
                sender_name=params.sender_name,
                meeting_topic=params.meeting_topic,
            )
            result = _compose_and_queue(
                req, target=params.invitees,
                kind="meeting_poll",
                source_ref=f"poll:{params.meeting_topic or 'untitled'}",
                template_only=params.template_only,
                enqueue=params.enqueue,
            )
            result["slots"] = [{"start": s.start.isoformat(),
                                "end": s.end.isoformat()} for s in slots]
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_meeting_poll", e)

    @mcp.tool(
        name="workflow_translate_reply",
        annotations={"title": "Reply in the inbound message's language",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    async def workflow_translate_reply(params: TranslateReplyInput) -> str:
        """Detect inbound language (or use override) + draft reply in same language."""
        try:
            target_lang = params.target_language or p2.detect_language(params.inbound_body)
            if not target_lang:
                return json.dumps({"detected_language": None,
                                   "note": "Couldn't detect a known language; use target_language override."})
            req = p2.build_translate_reply_request(
                inbound_body=params.inbound_body,
                target_language=target_lang,
                recipient_name=params.recipient_name,
                sender_name=params.sender_name,
                subject_hint=params.subject_hint,
            )
            result = _compose_and_queue(
                req, target=params.target,
                kind="translate_reply",
                source_ref=f"lang:{target_lang}",
                template_only=params.template_only,
                enqueue=params.enqueue,
            )
            result["detected_language"] = target_lang
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_translate_reply", e)
