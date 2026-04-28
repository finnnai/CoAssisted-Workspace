"""Calendar tools: list/create/update/delete events, find free time."""

from __future__ import annotations

import json
import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import config
import gservices
from dryrun import dry_run_preview, is_dry_run
from errors import format_error
from logging_util import log


def _service():
    return gservices.calendar()


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class ListEventsInput(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True, extra="forbid", populate_by_name=True
    )

    calendar_id: Optional[str] = Field(
        default=None, description="Calendar ID. Defaults to config.default_calendar_id or 'primary'."
    )
    time_min: Optional[str] = Field(
        default=None, description="ISO 8601 start of window (default: now)."
    )
    time_max: Optional[str] = Field(
        default=None, description="ISO 8601 end of window."
    )
    query: Optional[str] = Field(default=None, description="Full-text search filter.")
    limit: int = Field(
        default=25, ge=1, le=250,
        alias="max_results",
        description="Max events to return. Alias `max_results` is also accepted.",
    )


class CreateEventInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    calendar_id: Optional[str] = Field(
        default=None,
        description="Calendar ID. Defaults to config.default_calendar_id or 'primary'.",
    )
    summary: str = Field(..., description="Event title.")
    description: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None)
    start: str = Field(
        ...,
        description="ISO 8601 start (e.g. '2026-04-25T14:00:00-07:00'). For all-day, use date only: '2026-04-25'.",
    )
    end: str = Field(..., description="ISO 8601 end. Same format rules as start.")
    timezone: Optional[str] = Field(
        default=None,
        description="IANA timezone (e.g. 'America/Los_Angeles'). Falls back to config.default_timezone.",
    )
    attendees: Optional[list[str]] = Field(
        default=None, description="Attendee email addresses."
    )
    send_updates: str = Field(
        default="none",
        description="Whether to email attendees: 'all', 'externalOnly', or 'none'.",
    )
    add_meet: bool = Field(
        default=False,
        description="If True, auto-generate a Google Meet link attached to this event.",
    )
    # Recurrence — three friendly knobs OR a raw RRULE override.
    recurrence_pattern: Optional[str] = Field(
        default=None,
        description=(
            "Friendly recurrence: 'daily', 'weekdays', 'weekly', 'biweekly', "
            "'monthly', 'yearly'. Translated to RFC 5545 RRULE under the hood. "
            "Mutually exclusive with `recurrence_rrule`."
        ),
    )
    recurrence_count: Optional[int] = Field(
        default=None, ge=1, le=5000,
        description="Stop after N occurrences (combine with recurrence_pattern).",
    )
    recurrence_until: Optional[str] = Field(
        default=None,
        description=(
            "Stop on this date — ISO date 'YYYY-MM-DD' or full ISO datetime. "
            "Combine with recurrence_pattern."
        ),
    )
    recurrence_rrule: Optional[str] = Field(
        default=None,
        description=(
            "Raw RFC 5545 RRULE for power users (e.g. "
            "'RRULE:FREQ=WEEKLY;BYDAY=MO,WE;UNTIL=20261231T000000Z'). "
            "If set, overrides the friendly pattern fields."
        ),
    )
    dry_run: Optional[bool] = Field(default=None)


class QuickAddInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    text: str = Field(
        ...,
        description=(
            "Natural-language description of the event. "
            "Examples: 'Dinner with Josh Friday 7pm', 'Dentist next Tuesday 2-3pm'."
        ),
    )
    calendar_id: Optional[str] = Field(default=None)
    send_updates: str = Field(default="none")
    dry_run: Optional[bool] = Field(default=None)


class RespondToEventInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    calendar_id: Optional[str] = Field(default=None)
    event_id: str = Field(...)
    response: str = Field(
        ..., description="'accepted', 'declined', or 'tentative'."
    )


class ListCalendarsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpdateEventInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    calendar_id: Optional[str] = Field(default=None)
    event_id: str = Field(...)
    summary: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None)
    start: Optional[str] = Field(default=None)
    end: Optional[str] = Field(default=None)
    timezone: Optional[str] = Field(default=None)
    send_updates: str = Field(default="none")
    dry_run: Optional[bool] = Field(default=None)


class DeleteEventInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    calendar_id: Optional[str] = Field(default=None)
    event_id: str = Field(...)
    send_updates: str = Field(default="none")
    dry_run: Optional[bool] = Field(default=None)


class FreeBusyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    time_min: str = Field(..., description="ISO 8601 start of window.")
    time_max: str = Field(..., description="ISO 8601 end of window.")
    calendar_ids: list[str] = Field(
        default_factory=lambda: ["primary"],
        description="Calendars to check. Default: just 'primary'.",
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _time_field(value: str, timezone: str | None) -> dict:
    """Return a Calendar API time dict (date for all-day, dateTime otherwise)."""
    if "T" in value:
        out = {"dateTime": value}
        if timezone:
            out["timeZone"] = timezone
        return out
    return {"date": value}


def _cal_id(explicit: str | None) -> str:
    """Pick the effective calendar ID: explicit arg → config default → 'primary'."""
    return explicit or config.get("default_calendar_id", "primary") or "primary"


def _tz(explicit: str | None) -> str | None:
    return explicit or config.get("default_timezone")


_FRIENDLY_PATTERNS = {
    "daily": "FREQ=DAILY",
    "weekdays": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "weekly": "FREQ=WEEKLY",
    "biweekly": "FREQ=WEEKLY;INTERVAL=2",
    "monthly": "FREQ=MONTHLY",
    "yearly": "FREQ=YEARLY",
    "annually": "FREQ=YEARLY",
}


def _build_recurrence(
    *,
    pattern: str | None,
    count: int | None,
    until: str | None,
    rrule: str | None,
) -> list[str] | None:
    """Build the Google Calendar `recurrence` array from friendly args.

    Priority:
      1. raw `rrule` wins if provided (must include or omit 'RRULE:' prefix)
      2. otherwise, friendly `pattern` is mapped via _FRIENDLY_PATTERNS, with
         optional COUNT and UNTIL constraints layered in.

    Returns None if no recurrence is requested.
    """
    if rrule:
        # Normalize prefix.
        return [rrule if rrule.upper().startswith("RRULE:") else f"RRULE:{rrule}"]

    if not pattern:
        return None

    base = _FRIENDLY_PATTERNS.get(pattern.lower().strip())
    if not base:
        raise ValueError(
            f"Unknown recurrence_pattern '{pattern}'. "
            f"Choose from: {sorted(_FRIENDLY_PATTERNS)}"
        )
    parts = [base]
    if count:
        parts.append(f"COUNT={int(count)}")
    if until:
        # Accept "YYYY-MM-DD" or full ISO; convert to RFC 5545 UTC format
        # (YYYYMMDDTHHMMSSZ or YYYYMMDD for date-only RRULEs).
        u = until.strip()
        if "T" not in u:
            # Date-only — strip dashes.
            parts.append(f"UNTIL={u.replace('-', '')}")
        else:
            # Datetime — convert to YYYYMMDDTHHMMSSZ form.
            cleaned = (
                u.replace("-", "").replace(":", "")
                .split("+")[0]  # strip tz offset
                .split(".")[0]   # strip milliseconds
            )
            if not cleaned.endswith("Z"):
                cleaned += "Z"
            parts.append(f"UNTIL={cleaned}")
    return [f"RRULE:{';'.join(parts)}"]


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="calendar_list_events",
        annotations={
            "title": "List calendar events",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def calendar_list_events(params: ListEventsInput) -> str:
        """List events from a calendar with optional time window and text search."""
        try:
            kwargs = {
                "calendarId": _cal_id(params.calendar_id),
                "maxResults": params.limit,
                "singleEvents": True,
                "orderBy": "startTime",
            }
            if params.time_min:
                kwargs["timeMin"] = params.time_min
            if params.time_max:
                kwargs["timeMax"] = params.time_max
            if params.query:
                kwargs["q"] = params.query
            events = _service().events().list(**kwargs).execute().get("items", [])
            out = [
                {
                    "id": e["id"],
                    "summary": e.get("summary"),
                    "start": e.get("start"),
                    "end": e.get("end"),
                    "location": e.get("location"),
                    "attendees": [a.get("email") for a in e.get("attendees", [])],
                    "html_link": e.get("htmlLink"),
                }
                for e in events
            ]
            return json.dumps({"count": len(out), "events": out}, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="calendar_create_event",
        annotations={
            "title": "Create a calendar event",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def calendar_create_event(params: CreateEventInput) -> str:
        """Create an event. Supports timed, all-day, invitations with attendees, and Google Meet auto-link."""
        try:
            tz = _tz(params.timezone)
            body: dict = {
                "summary": params.summary,
                "description": params.description,
                "location": params.location,
                "start": _time_field(params.start, tz),
                "end": _time_field(params.end, tz),
            }
            if params.attendees:
                body["attendees"] = [{"email": a} for a in params.attendees]
            if params.add_meet:
                body["conferenceData"] = {
                    "createRequest": {
                        "requestId": str(uuid.uuid4()),
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                }

            # Recurrence — power user can pass raw RRULE; otherwise build one
            # from the friendly knobs.
            recurrence_rules = _build_recurrence(
                pattern=params.recurrence_pattern,
                count=params.recurrence_count,
                until=params.recurrence_until,
                rrule=params.recurrence_rrule,
            )
            if recurrence_rules:
                body["recurrence"] = recurrence_rules

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "calendar_create_event",
                    {"calendar_id": _cal_id(params.calendar_id), "body": body},
                )

            created = (
                _service()
                .events()
                .insert(
                    calendarId=_cal_id(params.calendar_id),
                    body=body,
                    sendUpdates=params.send_updates,
                    conferenceDataVersion=1 if params.add_meet else 0,
                )
                .execute()
            )
            return json.dumps(
                {
                    "status": "created",
                    "id": created["id"],
                    "html_link": created.get("htmlLink"),
                    "meet_link": (
                        (created.get("conferenceData") or {})
                        .get("entryPoints", [{}])[0]
                        .get("uri")
                        if params.add_meet
                        else None
                    ),
                },
                indent=2,
            )
        except Exception as e:
            log.error("calendar_create_event failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="calendar_update_event",
        annotations={
            "title": "Update a calendar event",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def calendar_update_event(params: UpdateEventInput) -> str:
        """Patch fields on an existing event. Only provided fields are changed."""
        try:
            tz = _tz(params.timezone)
            patch: dict = {}
            if params.summary is not None:
                patch["summary"] = params.summary
            if params.description is not None:
                patch["description"] = params.description
            if params.location is not None:
                patch["location"] = params.location
            if params.start is not None:
                patch["start"] = _time_field(params.start, tz)
            if params.end is not None:
                patch["end"] = _time_field(params.end, tz)

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "calendar_update_event",
                    {"event_id": params.event_id, "patch": patch},
                )

            updated = (
                _service()
                .events()
                .patch(
                    calendarId=_cal_id(params.calendar_id),
                    eventId=params.event_id,
                    body=patch,
                    sendUpdates=params.send_updates,
                )
                .execute()
            )
            return json.dumps(
                {"status": "updated", "id": updated["id"]}, indent=2
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="calendar_delete_event",
        annotations={
            "title": "Delete a calendar event",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def calendar_delete_event(params: DeleteEventInput) -> str:
        """Delete an event. This cannot be undone via the API."""
        try:
            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "calendar_delete_event", {"event_id": params.event_id}
                )
            _service().events().delete(
                calendarId=_cal_id(params.calendar_id),
                eventId=params.event_id,
                sendUpdates=params.send_updates,
            ).execute()
            return json.dumps({"status": "deleted", "id": params.event_id})
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="calendar_find_free_busy",
        annotations={
            "title": "Check free/busy across calendars",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def calendar_find_free_busy(params: FreeBusyInput) -> str:
        """Return busy time ranges for one or more calendars over a window.

        Useful for finding overlapping free slots before scheduling.
        """
        try:
            resp = (
                _service()
                .freebusy()
                .query(
                    body={
                        "timeMin": params.time_min,
                        "timeMax": params.time_max,
                        "items": [{"id": c} for c in params.calendar_ids],
                    }
                )
                .execute()
            )
            return json.dumps(resp.get("calendars", {}), indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="calendar_quick_add",
        annotations={
            "title": "Quick-add a calendar event from natural language",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def calendar_quick_add(params: QuickAddInput) -> str:
        """Create an event from free-text (e.g. 'Coffee with Josh next Tue 3pm').

        Uses Google's own quick-add parser so phrasing matches what works in the
        Google Calendar UI's 'Create' box.
        """
        try:
            if is_dry_run(params.dry_run):
                return dry_run_preview("calendar_quick_add", {"text": params.text})
            created = (
                _service()
                .events()
                .quickAdd(
                    calendarId=_cal_id(params.calendar_id),
                    text=params.text,
                    sendUpdates=params.send_updates,
                )
                .execute()
            )
            return json.dumps(
                {
                    "status": "created",
                    "id": created["id"],
                    "summary": created.get("summary"),
                    "start": created.get("start"),
                    "end": created.get("end"),
                    "html_link": created.get("htmlLink"),
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="calendar_respond_to_event",
        annotations={
            "title": "RSVP to a calendar event",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def calendar_respond_to_event(params: RespondToEventInput) -> str:
        """Set your response status (accepted/declined/tentative) on an invitation."""
        try:
            svc = _service()
            cid = _cal_id(params.calendar_id)
            event = svc.events().get(calendarId=cid, eventId=params.event_id).execute()
            # Find our own attendee entry and update it. If we're not an attendee, bail.
            me_email = (
                svc.calendarList().get(calendarId="primary").execute().get("id")
            )
            attendees = event.get("attendees", [])
            hit = next((a for a in attendees if a.get("email") == me_email or a.get("self")), None)
            if not hit:
                return f"Not an attendee on event {params.event_id}."
            hit["responseStatus"] = params.response
            updated = (
                svc.events()
                .patch(
                    calendarId=cid,
                    eventId=params.event_id,
                    body={"attendees": attendees},
                    sendUpdates="all",
                )
                .execute()
            )
            return json.dumps(
                {"status": "responded", "response": params.response, "id": updated["id"]},
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="calendar_list_calendars",
        annotations={
            "title": "List calendars this user can access",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def calendar_list_calendars(params: ListCalendarsInput) -> str:
        """List all calendars the authenticated user can see (primary, secondary, shared)."""
        try:
            resp = _service().calendarList().list().execute()
            out = [
                {
                    "id": c["id"],
                    "summary": c.get("summary"),
                    "timezone": c.get("timeZone"),
                    "access_role": c.get("accessRole"),
                    "primary": c.get("primary", False),
                }
                for c in resp.get("items", [])
            ]
            return json.dumps({"count": len(out), "calendars": out}, indent=2)
        except Exception as e:
            return format_error(e)
