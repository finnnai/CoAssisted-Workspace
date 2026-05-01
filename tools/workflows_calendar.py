# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Calendar-driven workflows (slot finding, drive-time blocks, route opt, briefs).

Split from the legacy tools/workflows.py during P1-1
(see mcp-design-docs-2026-04-29.md). All shared helpers live
in tools/_workflow_helpers.py.
"""
from __future__ import annotations

import base64
import io
import json
from typing import Optional

from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload
from pydantic import BaseModel, ConfigDict, Field

import config
import crm_stats
import gservices
import rendering
import templates as templates_mod
from dryrun import dry_run_preview, is_dry_run
from errors import format_error
from logging_util import log
from tools.contacts import _flatten_person  # noqa: E402 — reuse the flattening logic

# Inline MIME builder import — we can't cleanly import from tools.gmail without
# a circular import, so we use the email stdlib directly here.
import mimetypes
from email.message import EmailMessage

# Shared helpers from the legacy workflows.py
from tools._workflow_helpers import (
    _build_simple_email,
    _calendar_svc,
    _contact_lat_lng,
    _extract_address_block,
    _gmail,
    _haversine_km,
    _resolve_current_location,
    _resolve_to_address,
    _walk_all_contacts,
)

# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class MeetingLocationOptionsInput(BaseModel):
    """Input for workflow_meeting_location_options."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    attendee_addresses: list[str] = Field(
        ..., min_length=2,
        description="One address per attendee (home or office). 2 minimum.",
    )
    place_type: str = Field(
        default="restaurant",
        description=(
            "Google place type to search for as the meeting venue. "
            "Common: 'restaurant', 'cafe', 'coworking_space', 'meeting_room'."
        ),
    )
    max_options: int = Field(default=5, ge=1, le=10)
    mode: str = Field(default="driving", description="Travel mode for distance calc.")


class FindMeetingSlotInput(BaseModel):
    """Input for workflow_find_meeting_slot."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    attendees: list[str] = Field(
        ..., min_length=1,
        description=(
            "Attendee email addresses. Free/busy is checked for each. "
            "Your own email is auto-included if not in the list."
        ),
    )
    duration_minutes: int = Field(
        default=30, ge=5, le=480,
        description="Meeting length in minutes. Default 30.",
    )
    time_window_start: Optional[str] = Field(
        default=None,
        description=(
            "ISO 8601 start of the window to search. Defaults to 'now + 1 hour' "
            "rounded to the next quarter-hour."
        ),
    )
    time_window_end: Optional[str] = Field(
        default=None,
        description=(
            "ISO 8601 end of the search window. Defaults to 7 days after "
            "time_window_start."
        ),
    )
    timezone: Optional[str] = Field(
        default=None,
        description="IANA timezone (e.g. 'America/Los_Angeles'). Falls back to config default.",
    )
    preferred_hours_start: int = Field(
        default=9, ge=0, le=23,
        description="Earliest hour of day to consider (local time, 24h). Default 9.",
    )
    preferred_hours_end: int = Field(
        default=17, ge=0, le=23,
        description="Latest hour of day to consider (local time, 24h). Default 17.",
    )
    skip_weekends: bool = Field(
        default=True, description="If True, exclude Saturday + Sunday.",
    )
    count: int = Field(
        default=3, ge=1, le=20,
        description="How many slot suggestions to return (top N, earliest first).",
    )


class DetectOooInput(BaseModel):
    """Input for workflow_detect_ooo."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    days: int = Field(
        default=14, ge=1, le=90,
        description="How far back to scan inbox for auto-replies. Default 14 days.",
    )
    limit_messages_scanned: int = Field(
        default=300, ge=1, le=2000,
        description="Max inbox messages to inspect.",
    )
    write_custom_field: bool = Field(
        default=True,
        description=(
            "If True, set `out_of_office: true` (and `ooo_until` if a return "
            "date can be parsed) on each matching saved contact. False = report only."
        ),
    )
    dry_run: Optional[bool] = Field(default=None)


class RouteOptimizeVisitsInput(BaseModel):
    """Input for workflow_route_optimize_visits."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    stops: list[str] = Field(
        ..., min_length=2,
        description=(
            "Stops to visit. Each can be a free-form address, 'lat,lng' string, or a "
            "contact resource_name (people/c123...) — contact addresses are auto-resolved."
        ),
    )
    start: Optional[str] = Field(
        default=None,
        description="Where you start the day. Defaults to the first stop.",
    )
    end: Optional[str] = Field(
        default=None,
        description="Where you end. Defaults to the start (round-trip).",
    )
    travel_mode: str = Field(default="driving")
    departure_time: Optional[str] = Field(
        default=None,
        description="ISO 8601 or 'now'. Adds traffic-aware times (driving only).",
    )
    return_to_start: bool = Field(default=True)


class TravelBriefInput(BaseModel):
    """Input for workflow_travel_brief."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    city: str = Field(
        ..., description="City + state/country, e.g. 'Austin, TX' or 'Berlin, DE'.",
    )
    start_date: str = Field(..., description="ISO date YYYY-MM-DD.")
    end_date: str = Field(..., description="ISO date YYYY-MM-DD.")
    radius_km: float = Field(default=40.0, gt=0, le=200)
    max_contacts: int = Field(default=15, ge=1, le=100)
    write_doc: bool = Field(
        default=False,
        description="If True, generates a Google Doc with the brief and returns its URL.",
    )
    email_to: Optional[str] = Field(
        default=None,
        description="If set, emails the brief to this address.",
    )
    require_geocoded: bool = Field(
        default=False,
        description="If True, only consider contacts already geocoded.",
    )


class MeetingMidpointInput(BaseModel):
    """Input for workflow_meeting_midpoint."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    address_a: str = Field(..., description="First attendee's address.")
    address_b: str = Field(..., description="Second attendee's address.")
    place_type: str = Field(
        default="cafe",
        description="Place type to search at the midpoint (cafe, restaurant, bar, library, etc.).",
    )
    search_radius_m: int = Field(default=2000, ge=100, le=20000)
    limit: int = Field(default=5, ge=1, le=10)
    travel_mode: str = Field(default="driving")
    create_event: bool = Field(
        default=False,
        description="If True, drafts a Google Calendar event at the top-ranked venue.",
    )
    event_start_iso: Optional[str] = Field(default=None)
    event_end_iso: Optional[str] = Field(default=None)
    event_attendees: Optional[list[str]] = Field(
        default=None,
        description="Email addresses to invite if create_event=True.",
    )
    event_summary: Optional[str] = Field(default=None)


class CommuteBriefInput(BaseModel):
    """Input for workflow_commute_brief."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    home_address: Optional[str] = Field(
        default=None,
        description="Your start point. Falls back to config.home_address.",
    )
    current_location: Optional[str] = Field(
        default=None,
        description="Manual override for 'where am I now' — geocoded. Takes priority "
                    "over auto-detection when current_location_mode='auto' or 'manual'.",
    )
    current_location_mode: str = Field(
        default="auto",
        description=(
            "'auto' (try manual override → CoreLocationCLI → IP geolocation, fall "
            "back to home_address), 'manual' (require current_location), 'home' "
            "(always use home_address — old behavior), 'off' (same as home)."
        ),
    )
    date: Optional[str] = Field(
        default=None,
        description="ISO date YYYY-MM-DD. Defaults to today.",
    )
    travel_mode: str = Field(default="driving")
    buffer_minutes: int = Field(
        default=10, ge=0, le=120,
        description="Padding to add to the suggested leave-by time.",
    )
    deliver_via: str = Field(
        default="return",
        description="'return' (just return JSON), 'email', or 'chat_dm' (sends to yourself).",
    )
    email_to: Optional[str] = Field(default=None)


class EventNearbyAmenitiesInput(BaseModel):
    """Input for workflow_event_nearby_amenities."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    event_id: str = Field(..., description="Calendar event ID.")
    calendar_id: str = Field(default="primary")
    types: list[str] = Field(
        default_factory=lambda: ["cafe", "restaurant", "parking"],
        description="Place types to search nearby.",
    )
    radius_m: int = Field(default=400, ge=50, le=5000)
    limit_per_type: int = Field(default=3, ge=1, le=10)
    append_to_event: bool = Field(
        default=False,
        description="If True, appends a summary to the event description.",
    )
    dry_run: Optional[bool] = Field(default=None)


class ErrandRouteInput(BaseModel):
    """Input for workflow_errand_route."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    addresses: list[str] = Field(
        ..., min_length=2,
        description="Addresses to visit, in any order. Min 2.",
    )
    start: Optional[str] = Field(
        default=None,
        description="Start address. Defaults to first item.",
    )
    end: Optional[str] = Field(
        default=None,
        description="End address. Defaults to start (round-trip) unless return_to_start=False.",
    )
    travel_mode: str = Field(default="driving")
    departure_time: Optional[str] = Field(default=None)
    return_to_start: bool = Field(default=True)


class RecentMeetingsHeatmapInput(BaseModel):
    """Input for workflow_recent_meetings_heatmap."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    days: int = Field(default=30, ge=1, le=365)
    calendar_id: str = Field(default="primary")
    in_person_only: bool = Field(
        default=True,
        description="Skip events with no location or with virtual-meeting URLs.",
    )
    size: str = Field(default="640x640")
    map_type: str = Field(default="roadmap")
    save_to_path: Optional[str] = Field(default=None)


class DepartureReminderInput(BaseModel):
    """Input for workflow_departure_reminder."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    event_id: str = Field(..., description="Calendar event ID.")
    calendar_id: str = Field(default="primary")
    home_address: Optional[str] = Field(
        default=None,
        description="Where you're leaving from. Falls back to config.home_address.",
    )
    current_location: Optional[str] = Field(
        default=None,
        description="Manual override for 'where am I now'. Used when current_location_mode='auto' or 'manual'.",
    )
    current_location_mode: str = Field(
        default="auto",
        description="'auto' (detect → fall back to home_address), 'manual', 'home' (skip detection), 'off'.",
    )
    travel_mode: str = Field(default="driving")
    buffer_minutes: int = Field(default=10, ge=0, le=120)
    add_popup_reminder: bool = Field(
        default=True,
        description="Add a Calendar popup reminder at leave-by time.",
    )
    add_travel_block: bool = Field(
        default=False,
        description="Create a sibling 'Travel to X' Calendar event covering the travel window.",
    )
    dry_run: Optional[bool] = Field(default=None)


class CalendarDriveTimeBlocksInput(BaseModel):
    """Input for workflow_calendar_drive_time_blocks."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    days_ahead: int = Field(
        default=7, ge=1, le=30,
        description="How many days ahead to scan. Default 7.",
    )
    home_address: Optional[str] = Field(
        default=None,
        description="Default origin when there's no preceding meeting. Falls back to config.home_address.",
    )
    current_location: Optional[str] = Field(
        default=None,
        description="Manual override for 'where am I now'. Used as origin for the FIRST drive of the FIRST day only — smart-chain (previous meeting location) takes over after.",
    )
    current_location_mode: str = Field(
        default="auto",
        description="'auto' (detect via CoreLocationCLI/IP, fall back to home), 'manual', 'home' (always home_address), 'off'.",
    )
    travel_mode: str = Field(default="driving")
    buffer_minutes: int = Field(
        default=10, ge=0, le=120,
        description="Padding to add before the meeting starts (e.g. find parking).",
    )
    min_drive_minutes: int = Field(
        default=5, ge=0, le=60,
        description="Skip events whose drive is shorter than this — not worth a calendar block.",
    )
    color_id: str = Field(
        default="4",
        description=(
            "Google Calendar color ID for drive events. Default 4=Flamingo "
            "(light red). Other useful: 11=Tomato (deep red), 6=Tangerine, "
            "8=Graphite, 7=Peacock."
        ),
    )
    reminder_minutes_before: int = Field(
        default=30, ge=0, le=120,
        description="Single popup reminder this many minutes before the drive event. Default 30.",
    )
    skip_already_blocked: bool = Field(
        default=True,
        description="Skip events that already have a drive block linked via extendedProperties.",
    )
    skip_declined_events: bool = Field(default=True)
    calendar_id: str = Field(default="primary")
    dry_run: Optional[bool] = Field(default=None)


class RemoveDriveTimeBlocksInput(BaseModel):
    """Input for workflow_remove_drive_time_blocks."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    days_ahead: int = Field(default=14, ge=1, le=90)
    days_back: int = Field(
        default=0, ge=0, le=90,
        description="Also clean up past drive blocks (0 = future only).",
    )
    calendar_id: str = Field(default="primary")
    dry_run: Optional[bool] = Field(default=None)


class AdvRouteStop(BaseModel):
    """One stop for workflow_route_optimize_advanced."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    address: str = Field(..., description="Free-form address or 'lat,lng' string.")
    label: Optional[str] = Field(
        default=None, description="Display label. Defaults to the address.",
    )
    duration_minutes: int = Field(
        default=5, ge=0, le=480,
        description="Service time spent at this stop (loading, meeting, etc).",
    )
    earliest_arrival: Optional[str] = Field(
        default=None,
        description="ISO 8601 — must arrive at or after this time.",
    )
    latest_arrival: Optional[str] = Field(
        default=None,
        description="ISO 8601 — must arrive at or before this time.",
    )
    load_demand: Optional[int] = Field(
        default=None, ge=0,
        description="Generic load units consumed by this stop (e.g. crates, kg). "
                    "Vehicle.load_capacity uses the same metric.",
    )
    skip_penalty: float = Field(
        default=10000.0, ge=0,
        description="Cost the optimizer pays to skip this stop. Higher = more "
                    "important. Default forces all stops to be visited unless impossible.",
    )


class AdvRouteVehicle(BaseModel):
    """One vehicle for workflow_route_optimize_advanced."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    start_address: str = Field(..., description="Where this vehicle starts.")
    end_address: Optional[str] = Field(
        default=None, description="Where it ends. Default = same as start (round-trip).",
    )
    label: Optional[str] = Field(default=None, description="Display label, e.g. 'Allan' or 'Van 1'.")
    load_capacity: Optional[int] = Field(
        default=None, ge=0,
        description="Max total load this vehicle can carry across all stops. "
                    "Same metric as Stop.load_demand.",
    )
    shift_start: Optional[str] = Field(
        default=None, description="ISO 8601 — earliest the vehicle can leave.",
    )
    shift_end: Optional[str] = Field(
        default=None, description="ISO 8601 — vehicle must be back by this time.",
    )
    travel_mode: str = Field(
        default="DRIVING",
        description="DRIVING (default), WALKING, or TRANSIT.",
    )
    cost_per_hour: float = Field(
        default=1.0, ge=0,
        description=(
            "Cost the optimizer pays per hour the vehicle is in use. Higher → "
            "minimizes time. Default 1.0."
        ),
    )
    cost_per_km: float = Field(
        default=0.1, ge=0,
        description=(
            "Cost per kilometer driven. Higher → minimizes distance (might "
            "prefer slower-but-shorter routes over faster-but-longer ones). "
            "Default 0.1 (mostly time-minimizing)."
        ),
    )


class RouteFromCalendarInput(BaseModel):
    """Input for workflow_route_optimize_from_calendar."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    date_start: str = Field(..., description="ISO date YYYY-MM-DD or full ISO 8601 datetime.")
    date_end: str = Field(..., description="ISO date YYYY-MM-DD or full ISO 8601 datetime.")
    home_address: Optional[str] = Field(
        default=None,
        description="Vehicle starting address. Falls back to config.home_address.",
    )
    end_address: Optional[str] = Field(
        default=None,
        description="Where the vehicle ends. Default = same as home_address (round-trip).",
    )
    early_buffer_minutes: int = Field(
        default=15, ge=0, le=60,
        description="How early the optimizer is allowed to arrive at each meeting.",
    )
    additional_stops: Optional[list[AdvRouteStop]] = Field(
        default=None,
        description="Extra free-form stops to fit around calendar events.",
    )
    calendar_id: str = Field(default="primary")
    cost_per_hour: float = Field(default=1.0, ge=0)
    cost_per_km: float = Field(default=0.1, ge=0)
    optimization_mode: str = Field(default="CONSUME_ALL_AVAILABLE_TIME")
    timeout_seconds: int = Field(default=30, ge=5, le=120)
    dry_run: Optional[bool] = Field(default=None)


class AdvRouteOptimizeInput(BaseModel):
    """Input for workflow_route_optimize_advanced — full Vehicle Routing Problem."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    stops: list[AdvRouteStop] = Field(..., min_length=1)
    vehicles: Optional[list[AdvRouteVehicle]] = Field(
        default=None,
        description="One or more vehicles. Defaults to a single vehicle starting "
                    "from the first stop's address.",
    )
    global_start: Optional[str] = Field(
        default=None,
        description="ISO 8601 floor on all visits across all vehicles.",
    )
    global_end: Optional[str] = Field(
        default=None,
        description="ISO 8601 ceiling on all visits across all vehicles.",
    )
    optimization_mode: str = Field(
        default="RETURN_FAST",
        description="'RETURN_FAST' (sub-second, good enough) or "
                    "'CONSUME_ALL_AVAILABLE_TIME' (uses up to 30s for a better solution).",
    )
    timeout_seconds: int = Field(
        default=30, ge=5, le=120,
        description="Server-side compute budget. CONSUME_ALL_AVAILABLE_TIME uses up to this.",
    )
    dry_run: Optional[bool] = Field(default=None)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_find_meeting_slot",
        annotations={
            "title": "Find a meeting slot when all attendees are free",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_find_meeting_slot(params: FindMeetingSlotInput) -> str:
        """Find the next N times when every attendee is free.

        Uses Calendar's free/busy API to fetch each attendee's busy windows,
        then walks the search range in `duration_minutes` slices, filtering
        to preferred working hours and (optionally) skipping weekends.
        Returns the earliest `count` slots that satisfy every constraint.

        Notes:
          * The user's own free/busy is included automatically.
          * Free/busy is only as accurate as Google reports — events on
            calendars you don't own may show as 'free' from your view even
            when the attendee is actually busy.
          * Time window defaults to "1 hour from now → 7 days from now".
        """
        import datetime as _dt

        try:
            calendar = _calendar_svc()
            gmail = _gmail()

            # Auto-include self.
            try:
                me = (gmail.users().getProfile(userId="me").execute()
                      .get("emailAddress") or "").lower()
            except Exception as e:
                # Profile lookup failure usually means OAuth has expired
                # or scopes were revoked. Self-attendee detection degrades
                # silently — log so it's investigable.
                log.warning("getProfile failed in find_meeting_slot: %s", e)
                me = ""
            attendees = list({a.lower() for a in params.attendees if a})
            if me and me not in attendees:
                attendees.append(me)

            # Resolve timezone.
            tz_name = params.timezone or config.get("default_timezone") or "UTC"
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(tz_name)
            except Exception:
                from datetime import timezone as _utc
                tz = _utc.utc
                tz_name = "UTC"

            # Resolve search window. Round up to next quarter-hour for the start.
            now_local = _dt.datetime.now(tz)
            if params.time_window_start:
                start_dt = _dt.datetime.fromisoformat(
                    params.time_window_start.replace("Z", "+00:00")
                )
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=tz)
            else:
                # Default: now + 1 hour, rounded to next quarter-hour.
                target = now_local + _dt.timedelta(hours=1)
                add = (15 - target.minute % 15) % 15
                start_dt = (target + _dt.timedelta(minutes=add)).replace(
                    second=0, microsecond=0
                )

            if params.time_window_end:
                end_dt = _dt.datetime.fromisoformat(
                    params.time_window_end.replace("Z", "+00:00")
                )
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=tz)
            else:
                end_dt = start_dt + _dt.timedelta(days=7)

            if end_dt <= start_dt:
                return "Error: time_window_end must be after time_window_start."

            # Query free/busy.
            fb_resp = calendar.freebusy().query(
                body={
                    "timeMin": start_dt.astimezone(_dt.timezone.utc).isoformat(),
                    "timeMax": end_dt.astimezone(_dt.timezone.utc).isoformat(),
                    "timeZone": tz_name,
                    "items": [{"id": a} for a in attendees],
                }
            ).execute()

            # Parse busy intervals.
            busy_intervals: list[tuple[_dt.datetime, _dt.datetime, str]] = []
            errors_per_attendee: dict[str, str] = {}
            for email, info in (fb_resp.get("calendars") or {}).items():
                if info.get("errors"):
                    errors_per_attendee[email] = str(info["errors"])
                for blk in info.get("busy", []) or []:
                    s = _dt.datetime.fromisoformat(blk["start"].replace("Z", "+00:00"))
                    e = _dt.datetime.fromisoformat(blk["end"].replace("Z", "+00:00"))
                    busy_intervals.append((s.astimezone(tz), e.astimezone(tz), email))

            # Walk the window in slices, looking for unbroken free spans
            # ≥ duration_minutes during preferred hours.
            duration = _dt.timedelta(minutes=params.duration_minutes)
            slots: list[dict] = []
            cursor = start_dt
            step = _dt.timedelta(minutes=15)

            while cursor + duration <= end_dt and len(slots) < params.count:
                slot_end = cursor + duration

                # Constraint: weekday (Mon=0 .. Sun=6).
                if params.skip_weekends and cursor.weekday() >= 5:
                    # Jump to Monday 09:00.
                    days_to_mon = (7 - cursor.weekday()) % 7
                    if days_to_mon == 0:
                        days_to_mon = 1
                    cursor = (cursor + _dt.timedelta(days=days_to_mon)).replace(
                        hour=params.preferred_hours_start, minute=0,
                        second=0, microsecond=0,
                    )
                    continue

                # Constraint: preferred hours (entire slot must fit within).
                if (
                    cursor.hour < params.preferred_hours_start
                    or slot_end.hour > params.preferred_hours_end
                    or (slot_end.hour == params.preferred_hours_end and slot_end.minute > 0)
                ):
                    # Jump to next day's preferred_hours_start.
                    next_day = cursor + _dt.timedelta(days=1)
                    cursor = next_day.replace(
                        hour=params.preferred_hours_start, minute=0,
                        second=0, microsecond=0,
                    )
                    continue

                # Constraint: no busy interval overlaps.
                conflict = False
                for bs, be, _email in busy_intervals:
                    if cursor < be and slot_end > bs:
                        conflict = True
                        # Skip past this conflict.
                        cursor = max(cursor + step, be)
                        break
                if conflict:
                    continue

                slots.append({
                    "start": cursor.isoformat(),
                    "end": slot_end.isoformat(),
                    "weekday": cursor.strftime("%A"),
                    "human": cursor.strftime("%a %b %d, %I:%M %p ") + tz_name,
                })
                cursor += step  # Move past this slot to find the NEXT one.

            return json.dumps({
                "attendees": attendees,
                "duration_minutes": params.duration_minutes,
                "search_window": {
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "timezone": tz_name,
                },
                "slots_found": len(slots),
                "slots": slots,
                "attendee_errors": errors_per_attendee,
            }, indent=2)
        except Exception as e:
            log.error("workflow_find_meeting_slot failed: %s", e)
            return format_error(e)

    # --- OOO detection ------------------------------------------------------

    @mcp.tool(
        name="workflow_detect_ooo",
        annotations={
            "title": "Detect out-of-office auto-replies and flag CRM contacts",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_detect_ooo(params: DetectOooInput) -> str:
        """Scan recent inbox for OOO auto-replies and flag matching contacts.

        Detection signals (any one is sufficient):
          * `Auto-Submitted: auto-replied` header
          * `X-Autoreply: yes` header
          * Subject contains "out of office", "auto-reply", "automatic reply", or "vacation"
          * Body contains "I am out of the office" / "I'll be out" / similar phrases

        For each detected OOO sender that matches a saved contact, the tool
        sets `out_of_office: true` and (when parseable) `ooo_until: <date>` as
        userDefined fields on the contact. With `dry_run`, returns the plan only.
        """
        import re as _re
        import datetime as _dt

        try:
            from tools.enrichment import (
                _list_all_saved_contacts_by_email,
                _parse_sender,
                _extract_plaintext_body,
                _message_headers,
            )

            gmail = _gmail()
            people = gservices.people()

            # 1. Preload saved contacts by email.
            saved_by_email = _list_all_saved_contacts_by_email()

            # 2. Walk recent inbox messages.
            query = f"in:inbox newer_than:{params.days}d"
            message_ids: list[str] = []
            page_token = None
            while len(message_ids) < params.limit_messages_scanned:
                kwargs: dict = {
                    "userId": "me",
                    "q": query,
                    "maxResults": min(500, params.limit_messages_scanned - len(message_ids)),
                }
                if page_token:
                    kwargs["pageToken"] = page_token
                resp = gmail.users().messages().list(**kwargs).execute()
                batch = resp.get("messages", []) or []
                message_ids.extend(m["id"] for m in batch)
                page_token = resp.get("nextPageToken")
                if not page_token or not batch:
                    break

            # 3. Detect OOO per sender.
            ooo_subj_rx = _re.compile(
                r"(?i)\b(out\s+of\s+office|auto[\s-]?reply|automatic\s+reply|"
                r"vacation|away from (?:my\s+)?(?:desk|email)|on\s+holiday|"
                r"out\s+of\s+the\s+office)\b"
            )
            ooo_body_rx = _re.compile(
                r"(?i)(i\s+am\s+(?:out|currently\s+out|away)|"
                r"i'?ll?\s+be\s+(?:out|away|back)|"
                r"i\s+will\s+be\s+(?:out|away|returning)|"
                r"out\s+of\s+(?:the\s+)?office\s+(?:until|through|from)|"
                r"on\s+vacation\s+(?:until|through|from))"
            )
            return_date_rx = _re.compile(
                r"(?:return(?:ing)?|back|until|through|on)\s+"
                r"(?:on\s+)?"
                r"(?P<date>"
                r"(?:[A-Z][a-z]+\s+\d{1,2}(?:,?\s+\d{4})?)|"
                r"(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?)|"
                r"(?:\d{4}-\d{2}-\d{2})"
                r")",
                _re.IGNORECASE,
            )

            ooo_findings: dict[str, dict] = {}  # sender_lower → details

            for mid in message_ids:
                try:
                    msg = gmail.users().messages().get(
                        userId="me", id=mid, format="full"
                    ).execute()
                except Exception as e:
                    log.warning("workflow_detect_ooo: fetch %s failed: %s", mid, e)
                    continue
                headers = _message_headers(msg)
                from_raw = headers.get("From", "") or headers.get("from", "")
                sender = _parse_sender(from_raw)
                if not sender:
                    continue

                subj = headers.get("Subject", "") or headers.get("subject", "")

                # Header signals.
                auto_sub = (
                    headers.get("Auto-Submitted", "")
                    or headers.get("auto-submitted", "")
                ).lower().strip()
                x_auto = (
                    headers.get("X-Autoreply", "")
                    or headers.get("x-autoreply", "")
                ).lower().strip()

                is_ooo = False
                signals: list[str] = []
                if auto_sub and auto_sub != "no":
                    is_ooo = True
                    signals.append(f"header:auto_submitted={auto_sub}")
                if x_auto:
                    is_ooo = True
                    signals.append(f"header:x_autoreply={x_auto}")
                if ooo_subj_rx.search(subj or ""):
                    is_ooo = True
                    signals.append("subject_match")

                body = ""
                if not is_ooo:
                    body = _extract_plaintext_body(msg.get("payload") or {})
                    if ooo_body_rx.search(body):
                        is_ooo = True
                        signals.append("body_phrase_match")

                if not is_ooo:
                    continue

                if not body:
                    body = _extract_plaintext_body(msg.get("payload") or {})
                m = return_date_rx.search(body) or return_date_rx.search(subj or "")
                return_date = m.group("date").strip() if m else None

                key = sender.lower()
                # Keep the most recent finding per sender.
                if (
                    key not in ooo_findings
                    or int(msg.get("internalDate", "0"))
                    > ooo_findings[key].get("_ts", 0)
                ):
                    ooo_findings[key] = {
                        "_ts": int(msg.get("internalDate", "0")),
                        "email": sender,
                        "subject": subj,
                        "signals": signals,
                        "return_date": return_date,
                        "message_id": mid,
                    }

            # 4. Match to saved contacts and update.
            results: list[dict] = []
            for email_lower, info in ooo_findings.items():
                contact = saved_by_email.get(email_lower)
                if not contact:
                    results.append({
                        "email": info["email"],
                        "status": "ooo_detected_but_no_saved_contact",
                        "subject": info["subject"],
                        "signals": info["signals"],
                        "return_date": info["return_date"],
                    })
                    continue

                # Build the userDefined update.
                existing = {c.get("key"): c.get("value") for c in contact.get("userDefined", [])}
                target = dict(existing)
                target["out_of_office"] = "true"
                if info["return_date"]:
                    target["ooo_until"] = info["return_date"]

                if existing == target:
                    results.append({
                        "email": info["email"],
                        "resource_name": contact.get("resourceName"),
                        "status": "no_changes_needed",
                    })
                    continue

                if is_dry_run(params.dry_run) or not params.write_custom_field:
                    results.append({
                        "email": info["email"],
                        "resource_name": contact.get("resourceName"),
                        "status": "would_set",
                        "would_set": {k: v for k, v in target.items() if k not in existing
                                       or existing.get(k) != v},
                        "return_date": info["return_date"],
                    })
                    continue

                try:
                    people.people().updateContact(
                        resourceName=contact["resourceName"],
                        updatePersonFields="userDefined",
                        body={
                            "etag": contact["etag"],
                            "userDefined": [
                                {"key": k, "value": str(v)} for k, v in target.items() if v
                            ],
                        },
                    ).execute()
                    results.append({
                        "email": info["email"],
                        "resource_name": contact.get("resourceName"),
                        "status": "updated",
                        "set": {"out_of_office": "true", "ooo_until": info["return_date"]},
                    })
                except Exception as inner:
                    log.error("workflow_detect_ooo: update %s failed: %s", info["email"], inner)
                    results.append({
                        "email": info["email"],
                        "resource_name": contact.get("resourceName"),
                        "status": "failed",
                        "error": str(inner),
                    })

            summary = {
                "messages_scanned": len(message_ids),
                "ooo_senders_detected": len(ooo_findings),
                "saved_contacts_flagged": sum(
                    1 for r in results if r.get("status") in ("updated", "would_set")
                ),
                "results": results,
            }
            log.info(
                "workflow_detect_ooo: scanned=%d ooo=%d flagged=%d",
                len(message_ids), len(ooo_findings),
                summary["saved_contacts_flagged"],
            )
            return json.dumps(summary, indent=2)
        except Exception as e:
            log.error("workflow_detect_ooo failed: %s", e)
            return format_error(e)

    # --- Maps + email composition -------------------------------------------

    @mcp.tool(
        name="workflow_meeting_location_options",
        annotations={
            "title": "Suggest meeting venues equidistant for multiple attendees",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_meeting_location_options(params: MeetingLocationOptionsInput) -> str:
        """Find fair meeting spots for a group spread across multiple addresses.

        Algorithm:
          1. Geocode each attendee's address.
          2. Compute the geographic centroid.
          3. Search nearby for places of `place_type` (default 'restaurant').
          4. For each candidate, compute travel time from each attendee.
          5. Rank by maximum travel time across attendees (lowest worst-case wins).

        Returns top N options with per-attendee travel-time breakdown — so you
        can see at a glance which venue is most equitable.
        """
        try:
            gmaps = gservices.maps()  # raises if Maps key not configured

            # 1. Geocode each address.
            attendee_coords: list[tuple[float, float]] = []
            attendee_addresses_resolved: list[str] = []
            for addr in params.attendee_addresses:
                geo = gmaps.geocode(addr)
                if not geo:
                    return json.dumps({
                        "status": "geocode_failed",
                        "address": addr,
                    }, indent=2)
                loc = (geo[0].get("geometry") or {}).get("location") or {}
                attendee_coords.append((loc["lat"], loc["lng"]))
                attendee_addresses_resolved.append(geo[0].get("formatted_address"))

            # 2. Centroid.
            avg_lat = sum(c[0] for c in attendee_coords) / len(attendee_coords)
            avg_lng = sum(c[1] for c in attendee_coords) / len(attendee_coords)

            # 3. Candidate venues near the centroid.
            nearby = gmaps.places_nearby(
                location=(avg_lat, avg_lng),
                radius=5000,  # 5km
                type=params.place_type,
            )
            candidates = nearby.get("results", [])[: params.max_options * 2]
            if not candidates:
                return json.dumps({
                    "status": "no_candidates",
                    "centroid": {"lat": avg_lat, "lng": avg_lng},
                    "place_type": params.place_type,
                }, indent=2)

            # 4. Distance matrix from each attendee to each candidate.
            candidate_addrs = [
                f"{(c.get('geometry') or {}).get('location', {}).get('lat')},"
                f"{(c.get('geometry') or {}).get('location', {}).get('lng')}"
                for c in candidates
            ]
            attendee_origins = [f"{lat},{lng}" for lat, lng in attendee_coords]
            dm = gmaps.distance_matrix(
                origins=attendee_origins,
                destinations=candidate_addrs,
                mode=params.mode,
            )

            # 5. Score each candidate by max travel time.
            ranked: list[dict] = []
            for j, cand in enumerate(candidates):
                per_attendee: list[dict] = []
                worst = 0
                ok = True
                for i, attendee_addr in enumerate(attendee_addresses_resolved):
                    el = (dm.get("rows", [])[i] or {}).get("elements", [])[j]
                    if (el or {}).get("status") != "OK":
                        ok = False
                        break
                    secs = (el.get("duration") or {}).get("value", 0)
                    per_attendee.append({
                        "from": attendee_addr,
                        "duration_text": (el.get("duration") or {}).get("text"),
                        "duration_seconds": secs,
                        "distance_text": (el.get("distance") or {}).get("text"),
                    })
                    worst = max(worst, secs)
                if not ok:
                    continue
                ranked.append({
                    "name": cand.get("name"),
                    "vicinity": cand.get("vicinity"),
                    "place_id": cand.get("place_id"),
                    "rating": cand.get("rating"),
                    "user_ratings_total": cand.get("user_ratings_total"),
                    "location": (cand.get("geometry") or {}).get("location"),
                    "max_travel_minutes": round(worst / 60, 1),
                    "per_attendee": per_attendee,
                })
            ranked.sort(key=lambda r: r["max_travel_minutes"])
            ranked = ranked[: params.max_options]

            return json.dumps({
                "status": "ok",
                "attendee_count": len(attendee_coords),
                "centroid": {"lat": avg_lat, "lng": avg_lng},
                "place_type": params.place_type,
                "options": ranked,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({
                "status": "maps_not_configured",
                "error": str(e),
                "hint": "Run system_check_maps_api_key for setup steps.",
            }, indent=2)
        except Exception as e:
            log.error("workflow_meeting_location_options failed: %s", e)
            return format_error(e)

    # --- Chat digest --------------------------------------------------------

    @mcp.tool(
        name="workflow_route_optimize_visits",
        annotations={
            "title": "Find optimal driving order for a day of stops (TSP nearest-neighbor)",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_route_optimize_visits(params: RouteOptimizeVisitsInput) -> str:
        """Order a list of stops to minimize driving time via Distance Matrix.

        Accepts free-form addresses, 'lat,lng' strings, OR contact resource_names
        (people/c123...) — contact addresses are auto-resolved. Uses a
        nearest-neighbor TSP heuristic; for ≤15 stops this typically lands
        within ~10% of optimal.

        Cost: ~$0.005 × (n+1)² for the Distance Matrix call. 10 stops ≈ $0.60.
        """
        try:
            gmaps = gservices.maps()

            # 1. Resolve all stops to addresses.
            stops_resolved: list[str] = []
            stop_labels: list[str] = []
            for s in params.stops:
                addr, _person = _resolve_to_address(s)
                stops_resolved.append(addr)
                stop_labels.append(addr if not s.startswith("people/") else f"{s} → {addr}")

            start_addr = params.start or stops_resolved[0]
            end_addr = params.end or (start_addr if params.return_to_start else stops_resolved[-1])

            # If start/end are not in stops, prepend / append (we still want them in the matrix).
            all_points = [start_addr] + stops_resolved + [end_addr]
            uniq_points = list(dict.fromkeys(all_points))  # de-dupe preserving order
            if len(uniq_points) < 2:
                return "Error: need at least 2 distinct points (start + 1 stop)."

            # 2. Distance Matrix all-pairs.
            dm_args: dict = {
                "origins": uniq_points,
                "destinations": uniq_points,
                "mode": params.travel_mode,
            }
            if params.departure_time and params.travel_mode == "driving":
                dm_args["departure_time"] = (
                    "now" if params.departure_time == "now"
                    else __import__("datetime").datetime.fromisoformat(params.departure_time)
                )
            dm = gmaps.distance_matrix(**dm_args)
            n = len(uniq_points)
            durations = [[None] * n for _ in range(n)]
            distances = [[None] * n for _ in range(n)]
            for i, row in enumerate(dm.get("rows") or []):
                for j, el in enumerate(row.get("elements") or []):
                    if el.get("status") == "OK":
                        durations[i][j] = el["duration"]["value"]
                        distances[i][j] = el["distance"]["value"]

            # 3. Nearest-neighbor TSP from start, ending at end.
            start_idx = uniq_points.index(start_addr)
            end_idx = uniq_points.index(end_addr)
            stop_indices = [uniq_points.index(s) for s in stops_resolved if uniq_points.index(s) not in (start_idx,)]
            # Remove end_idx from to-visit set if it's not also a stop.
            to_visit = list(dict.fromkeys(stop_indices))
            if end_idx in to_visit and end_addr != stops_resolved[-1]:
                pass  # end is a real stop too

            order = [start_idx]
            current = start_idx
            remaining = [i for i in to_visit if i != start_idx]
            while remaining:
                best_j = None
                best_t = None
                for j in remaining:
                    t = durations[current][j]
                    if t is None:
                        continue
                    if best_t is None or t < best_t:
                        best_t = t
                        best_j = j
                if best_j is None:
                    # Some stops unreachable; append them in original order.
                    order.extend(remaining)
                    break
                order.append(best_j)
                remaining.remove(best_j)
                current = best_j
            if order[-1] != end_idx:
                order.append(end_idx)

            # 4. Render itinerary.
            legs = []
            total_seconds = 0
            total_meters = 0
            for k in range(len(order) - 1):
                a, b = order[k], order[k + 1]
                t = durations[a][b]
                d = distances[a][b]
                legs.append({
                    "from": uniq_points[a],
                    "to": uniq_points[b],
                    "duration_min": round(t / 60, 1) if t is not None else None,
                    "distance_km": round(d / 1000, 2) if d is not None else None,
                })
                if t is not None:
                    total_seconds += t
                if d is not None:
                    total_meters += d

            return json.dumps({
                "ordered_stops": [uniq_points[i] for i in order],
                "legs": legs,
                "total_drive_time_min": round(total_seconds / 60, 1),
                "total_distance_km": round(total_meters / 1000, 2),
                "travel_mode": params.travel_mode,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_route_optimize_visits failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_meeting_midpoint",
        annotations={
            "title": "Find a fair midpoint venue between two attendees + draft invite",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_meeting_midpoint(params: MeetingMidpointInput) -> str:
        """Two attendees → fair midpoint venue.

        Geocodes both addresses, finds the midpoint, searches for `place_type`
        nearby, ranks by combined travel time symmetry. Optionally drafts a
        calendar event at the top venue.
        """
        try:
            gmaps = gservices.maps()
            ga = gmaps.geocode(params.address_a)
            gb = gmaps.geocode(params.address_b)
            if not ga or not gb:
                return json.dumps({"status": "geocode_failed",
                                   "address_a_ok": bool(ga), "address_b_ok": bool(gb)}, indent=2)
            la = ga[0]["geometry"]["location"]; lb = gb[0]["geometry"]["location"]
            mid = ((la["lat"] + lb["lat"]) / 2, (la["lng"] + lb["lng"]) / 2)

            # Search candidates near midpoint.
            results = gmaps.places_nearby(
                location=mid, radius=params.search_radius_m,
                type=params.place_type,
            ).get("results", []) or []
            if not results:
                return json.dumps({"status": "no_venues",
                                   "midpoint_lat": mid[0], "midpoint_lng": mid[1]}, indent=2)

            # Distance Matrix from each attendee to top candidates (limit search).
            top_candidates = results[: max(params.limit * 2, 5)]
            dests = [
                f"{c['geometry']['location']['lat']},{c['geometry']['location']['lng']}"
                for c in top_candidates
            ]
            dm = gmaps.distance_matrix(
                origins=[params.address_a, params.address_b],
                destinations=dests, mode=params.travel_mode,
            )
            rows = dm.get("rows") or []
            scored: list[dict] = []
            for idx, c in enumerate(top_candidates):
                el_a = (rows[0].get("elements") or [{}])[idx] if rows else {}
                el_b = (rows[1].get("elements") or [{}])[idx] if len(rows) > 1 else {}
                ta = el_a.get("duration", {}).get("value") if el_a.get("status") == "OK" else None
                tb = el_b.get("duration", {}).get("value") if el_b.get("status") == "OK" else None
                if ta is None or tb is None:
                    continue
                fairness = abs(ta - tb)  # lower is fairer
                total = ta + tb
                scored.append({
                    "name": c.get("name"),
                    "address": c.get("vicinity") or c.get("formatted_address"),
                    "place_id": c.get("place_id"),
                    "rating": c.get("rating"),
                    "user_ratings_total": c.get("user_ratings_total"),
                    "minutes_a": round(ta / 60, 1),
                    "minutes_b": round(tb / 60, 1),
                    "fairness_seconds": fairness,
                    "total_minutes": round(total / 60, 1),
                })
            scored.sort(key=lambda r: (r["fairness_seconds"], r["total_minutes"]))
            top = scored[: params.limit]

            event_id = None
            event_link = None
            if params.create_event and top and params.event_start_iso and params.event_end_iso:
                cal = gservices.calendar()
                venue = top[0]
                body = {
                    "summary": params.event_summary or f"Meeting at {venue['name']}",
                    "location": venue["address"],
                    "start": {"dateTime": params.event_start_iso},
                    "end": {"dateTime": params.event_end_iso},
                }
                if params.event_attendees:
                    body["attendees"] = [{"email": e} for e in params.event_attendees]
                created = cal.events().insert(
                    calendarId="primary", body=body, sendUpdates="all",
                ).execute()
                event_id = created.get("id")
                event_link = created.get("htmlLink")

            return json.dumps({
                "midpoint_lat": mid[0], "midpoint_lng": mid[1],
                "candidates": top,
                "event_id": event_id, "event_link": event_link,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_meeting_midpoint failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_commute_brief",
        annotations={
            "title": "Daily 'leave by' note for your first meeting given live traffic",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_commute_brief(params: CommuteBriefInput) -> str:
        """Pulls your first meeting today with a location, computes the live-traffic
        drive time from `home_address`, and tells you when to leave.

        Optionally delivers via email or self-DM in Chat.
        """
        try:
            import datetime as _dt
            cal = gservices.calendar()
            gmaps = gservices.maps()

            # Resolve origin: current location (auto-detected) wins over home_address.
            current_loc = _resolve_current_location(
                manual=params.current_location,
                mode=params.current_location_mode,
            )
            home = params.home_address or config.get("home_address")
            if current_loc:
                origin = f"{current_loc['lat']},{current_loc['lng']}"
                origin_label = (
                    current_loc.get("formatted_address")
                    or f"{current_loc['lat']:.4f},{current_loc['lng']:.4f}"
                )
                origin_source = current_loc["source"]
            elif home:
                origin = home
                origin_label = home
                origin_source = "home"
            else:
                return json.dumps({
                    "status": "no_origin",
                    "hint": (
                        "No current location detected and no home_address set. "
                        "Pass home_address, current_location, or set 'home_address' "
                        "in config.json."
                    ),
                }, indent=2)

            tz_name = config.get("default_timezone") or "America/Los_Angeles"
            try:
                from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
                tz = ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, ValueError) as e:
                # Bad tz name (e.g. typo). Falls through to UTC. Log so
                # the caller can correct the input.
                log.warning("zoneinfo lookup failed for %r: %s", tz_name, e)
                tz = None

            day = (
                _dt.date.fromisoformat(params.date) if params.date
                else _dt.date.today()
            )
            start_dt = _dt.datetime.combine(day, _dt.time(0, 0), tzinfo=tz)
            end_dt = _dt.datetime.combine(day, _dt.time(23, 59), tzinfo=tz)

            evs = cal.events().list(
                calendarId="primary",
                timeMin=start_dt.isoformat(),
                timeMax=end_dt.isoformat(),
                singleEvents=True, orderBy="startTime", maxResults=20,
            ).execute().get("items", []) or []

            first = None
            for e in evs:
                loc = e.get("location") or ""
                if loc and "://" not in loc and "@" not in loc:
                    first = e
                    break
            if not first:
                return json.dumps({"status": "no_in_person_event_today",
                                   "events_scanned": len(evs)}, indent=2)

            event_start_iso = (first.get("start") or {}).get("dateTime")
            if not event_start_iso:
                return json.dumps({"status": "no_event_start_time",
                                   "event_id": first.get("id")}, indent=2)
            event_start = _dt.datetime.fromisoformat(event_start_iso)

            dm = gmaps.distance_matrix(
                origins=[origin], destinations=[first["location"]],
                mode=params.travel_mode, departure_time=event_start,
            )
            el = (dm.get("rows") or [{}])[0].get("elements", [{}])[0]
            if el.get("status") != "OK":
                return json.dumps({"status": "directions_failed",
                                   "element_status": el.get("status")}, indent=2)
            drive_seconds = el.get("duration_in_traffic", el.get("duration"))["value"]
            drive_min = round(drive_seconds / 60)
            leave_by = event_start - _dt.timedelta(
                minutes=drive_min + params.buffer_minutes,
            )

            text = (
                f"🌅 Commute brief for {day.isoformat()}\n"
                f"First meeting: {first.get('summary','(no title)')} at {event_start.strftime('%H:%M %Z')}\n"
                f"Where: {first['location']}\n"
                f"From: {origin_label} (origin: {origin_source})\n"
                f"Drive time: ~{drive_min} min ({el.get('distance', {}).get('text','?')})\n"
                f"Leave by: {leave_by.strftime('%H:%M %Z')} (incl. {params.buffer_minutes} min buffer)\n"
            )

            delivered = None
            if params.deliver_via == "email":
                to = params.email_to or "me"
                if to == "me":
                    profile = _gmail().users().getProfile(userId="me").execute()
                    to = profile.get("emailAddress")
                msg = _build_simple_email(
                    to=[to], subject=f"Commute brief — {day.isoformat()}", body=text,
                )
                _gmail().users().messages().send(userId="me", body=msg).execute()
                delivered = "email"
            elif params.deliver_via == "chat_dm":
                chat = gservices.chat()
                profile = _gmail().users().getProfile(userId="me").execute()
                me_email = profile.get("emailAddress")
                user_resource = f"users/{me_email}"
                space_name = None
                try:
                    found = chat.spaces().findDirectMessage(name=user_resource).execute()
                    space_name = (found or {}).get("name")
                except Exception:
                    pass
                if not space_name:
                    created = chat.spaces().setup(body={
                        "space": {"spaceType": "DIRECT_MESSAGE"},
                        "memberships": [{"member": {"name": user_resource, "type": "HUMAN"}}],
                    }).execute()
                    space_name = created.get("name")
                chat.spaces().messages().create(
                    parent=space_name, body={"text": text},
                ).execute()
                delivered = "chat_dm"

            return json.dumps({
                "status": "ok",
                "event_id": first.get("id"),
                "event_summary": first.get("summary"),
                "event_start": event_start_iso,
                "event_location": first["location"],
                "origin": origin_label,
                "origin_source": origin_source,
                "current_location_accuracy_m": (
                    current_loc.get("accuracy_m") if current_loc else None
                ),
                "drive_minutes": drive_min,
                "leave_by": leave_by.isoformat(),
                "delivered": delivered,
                "text": text,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_commute_brief failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_event_nearby_amenities",
        annotations={
            "title": "Find coffee/lunch/parking near a calendar event",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_event_nearby_amenities(params: EventNearbyAmenitiesInput) -> str:
        """For an event with a location, list nearby amenities by type.

        Optionally appends a summary to the event description.
        """
        try:
            cal = gservices.calendar()
            gmaps = gservices.maps()
            ev = cal.events().get(
                calendarId=params.calendar_id, eventId=params.event_id,
            ).execute()
            loc = ev.get("location")
            if not loc:
                return json.dumps({"status": "event_has_no_location",
                                   "event_id": params.event_id}, indent=2)
            geo = gmaps.geocode(loc)
            if not geo:
                return json.dumps({"status": "geocode_failed", "location": loc}, indent=2)
            center = geo[0]["geometry"]["location"]

            results: dict = {}
            for t in params.types:
                resp = gmaps.places_nearby(
                    location=center, radius=params.radius_m, type=t,
                ).get("results", []) or []
                top = []
                for r in resp[: params.limit_per_type]:
                    top.append({
                        "name": r.get("name"),
                        "vicinity": r.get("vicinity"),
                        "rating": r.get("rating"),
                        "place_id": r.get("place_id"),
                    })
                results[t] = top

            summary_lines = [f"Nearby amenities (≤{params.radius_m}m):"]
            for t, lst in results.items():
                summary_lines.append(f"\n{t.title()}:")
                if not lst:
                    summary_lines.append("  (none found)")
                else:
                    for r in lst:
                        rating = f" ★{r['rating']}" if r.get("rating") else ""
                        summary_lines.append(f"  • {r['name']}{rating} — {r.get('vicinity','')}")
            summary_text = "\n".join(summary_lines)

            updated = False
            if params.append_to_event and not is_dry_run(params.dry_run):
                new_desc = (ev.get("description") or "") + "\n\n" + summary_text
                cal.events().patch(
                    calendarId=params.calendar_id, eventId=params.event_id,
                    body={"description": new_desc},
                ).execute()
                updated = True

            return json.dumps({
                "status": "dry_run" if is_dry_run(params.dry_run) else "ok",
                "event_id": params.event_id,
                "event_location": loc,
                "results": results,
                "appended_to_event": updated,
                "summary": summary_text,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_event_nearby_amenities failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_errand_route",
        annotations={
            "title": "Optimal driving order for a list of addresses",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_errand_route(params: ErrandRouteInput) -> str:
        """Lighter-weight cousin of `workflow_route_optimize_visits` — pure addresses,
        no contact-resolution.
        """
        try:
            gmaps = gservices.maps()
            start_addr = params.start or params.addresses[0]
            end_addr = params.end or (start_addr if params.return_to_start else params.addresses[-1])
            uniq = list(dict.fromkeys([start_addr] + params.addresses + [end_addr]))
            if len(uniq) < 2:
                return "Error: need at least 2 distinct addresses."

            dm_args: dict = {"origins": uniq, "destinations": uniq, "mode": params.travel_mode}
            if params.departure_time and params.travel_mode == "driving":
                dm_args["departure_time"] = (
                    "now" if params.departure_time == "now"
                    else __import__("datetime").datetime.fromisoformat(params.departure_time)
                )
            dm = gmaps.distance_matrix(**dm_args)
            n = len(uniq)
            durations = [[None] * n for _ in range(n)]
            distances = [[None] * n for _ in range(n)]
            for i, row in enumerate(dm.get("rows") or []):
                for j, el in enumerate(row.get("elements") or []):
                    if el.get("status") == "OK":
                        durations[i][j] = el["duration"]["value"]
                        distances[i][j] = el["distance"]["value"]

            start_idx = uniq.index(start_addr); end_idx = uniq.index(end_addr)
            # Set of indices we still need to visit. Excludes start_idx (already there)
            # and excludes end_idx ONLY if start == end (round-trip — end gets appended last).
            to_visit = [
                i for i in range(n)
                if i != start_idx and not (i == end_idx and start_idx == end_idx)
            ]
            order = [start_idx]; current = start_idx
            while to_visit:
                # Defer end_idx until last unless it's a real interim stop.
                pickable = [
                    j for j in to_visit
                    if not (j == end_idx and len(to_visit) > 1)
                ]
                if not pickable:
                    pickable = to_visit
                best = None; best_t = None
                for j in pickable:
                    t = durations[current][j]
                    if t is None:
                        continue
                    if best_t is None or t < best_t:
                        best_t = t; best = j
                if best is None:
                    order.extend(to_visit)
                    break
                order.append(best)
                to_visit.remove(best)
                current = best
            # Always end at end_idx (round-trip or explicit end).
            if order[-1] != end_idx:
                order.append(end_idx)

            legs = []
            total_t = 0
            total_d = 0
            for k in range(len(order) - 1):
                a, b = order[k], order[k + 1]
                t = durations[a][b]; d = distances[a][b]
                legs.append({
                    "from": uniq[a], "to": uniq[b],
                    "duration_min": round(t / 60, 1) if t is not None else None,
                    "distance_km": round(d / 1000, 2) if d is not None else None,
                })
                if t is not None: total_t += t
                if d is not None: total_d += d

            return json.dumps({
                "ordered_addresses": [uniq[i] for i in order],
                "legs": legs,
                "total_drive_time_min": round(total_t / 60, 1),
                "total_distance_km": round(total_d / 1000, 2),
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_errand_route failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_recent_meetings_heatmap",
        annotations={
            "title": "Static map of where your in-person meetings happened recently",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_recent_meetings_heatmap(params: RecentMeetingsHeatmapInput) -> str:
        """Pull last N days of events with locations, geocode them, render a static map."""
        try:
            import datetime as _dt
            from tools.maps import _parse_size
            cal = gservices.calendar()
            gmaps = gservices.maps()
            now = _dt.datetime.now().astimezone()
            since = now - _dt.timedelta(days=params.days)
            evs = cal.events().list(
                calendarId=params.calendar_id,
                timeMin=since.isoformat(), timeMax=now.isoformat(),
                singleEvents=True, orderBy="startTime", maxResults=2500,
            ).execute().get("items", []) or []
            locations: list[str] = []
            counts_per_loc: dict[str, int] = {}
            for e in evs:
                loc = e.get("location") or ""
                if not loc:
                    continue
                if params.in_person_only and ("://" in loc or "meet.google.com" in loc.lower()
                                              or "zoom.us" in loc.lower()):
                    continue
                counts_per_loc[loc] = counts_per_loc.get(loc, 0) + 1
            for loc in counts_per_loc.keys():
                try:
                    g = gmaps.geocode(loc)
                except Exception:
                    continue
                if g:
                    pt = g[0]["geometry"]["location"]
                    locations.append(f"{pt['lat']:.6f},{pt['lng']:.6f}")
            if not locations:
                status = (
                    "no_in_person_locations" if params.in_person_only
                    else "no_locations"
                )
                return json.dumps({
                    "status": status,
                    "events_scanned": len(evs),
                    "events_with_locations": len(counts_per_loc),
                }, indent=2)
            chunks = gmaps.static_map(
                size=_parse_size(params.size), maptype=params.map_type,
                markers=locations,
            )
            map_bytes = b"".join(chunks) if hasattr(chunks, "__iter__") else chunks
            saved_to = None; b64 = None
            if params.save_to_path:
                Path = __import__("pathlib").Path
                Path(params.save_to_path).write_bytes(map_bytes)
                saved_to = params.save_to_path
            else:
                import base64 as _b64
                b64 = _b64.b64encode(map_bytes).decode("ascii")
            top_locs = sorted(counts_per_loc.items(), key=lambda kv: -kv[1])[:10]
            return json.dumps({
                "status": "ok",
                "days": params.days,
                "events_scanned": len(evs),
                "unique_locations": len(counts_per_loc),
                "geocoded": len(locations),
                "top_locations": [{"location": k, "count": v} for k, v in top_locs],
                "map_size_kb": round(len(map_bytes) / 1024),
                "saved_to": saved_to,
                "image_base64": b64,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_recent_meetings_heatmap failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_departure_reminder",
        annotations={
            "title": "Add a 'leave by' reminder to a calendar event using live traffic",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_departure_reminder(params: DepartureReminderInput) -> str:
        """For a future event with a location, compute the live-traffic drive
        time and either add a popup reminder at leave-by time or create a
        sibling 'Travel to X' event covering the journey.
        """
        try:
            import datetime as _dt
            cal = gservices.calendar()
            gmaps = gservices.maps()
            ev = cal.events().get(
                calendarId=params.calendar_id, eventId=params.event_id,
            ).execute()
            loc = ev.get("location")
            if not loc:
                return json.dumps({"status": "event_has_no_location",
                                   "event_id": params.event_id}, indent=2)
            event_start_iso = (ev.get("start") or {}).get("dateTime")
            if not event_start_iso:
                return json.dumps({"status": "no_event_start_time",
                                   "event_id": params.event_id}, indent=2)
            event_start = _dt.datetime.fromisoformat(event_start_iso)
            current_loc = _resolve_current_location(
                manual=params.current_location,
                mode=params.current_location_mode,
            )
            home = params.home_address or config.get("home_address")
            if current_loc:
                origin = f"{current_loc['lat']},{current_loc['lng']}"
                origin_label = (
                    current_loc.get("formatted_address")
                    or f"{current_loc['lat']:.4f},{current_loc['lng']:.4f}"
                )
                origin_source = current_loc["source"]
            elif home:
                origin = home
                origin_label = home
                origin_source = "home"
            else:
                return json.dumps({
                    "status": "no_origin",
                    "hint": "Pass home_address, current_location, or set 'home_address' in config.json.",
                }, indent=2)
            dm = gmaps.distance_matrix(
                origins=[origin], destinations=[loc],
                mode=params.travel_mode, departure_time=event_start,
            )
            el = (dm.get("rows") or [{}])[0].get("elements", [{}])[0]
            if el.get("status") != "OK":
                return json.dumps({"status": "directions_failed",
                                   "element_status": el.get("status")}, indent=2)
            drive_seconds = el.get("duration_in_traffic", el.get("duration"))["value"]
            drive_min = round(drive_seconds / 60)
            total_min = drive_min + params.buffer_minutes
            leave_by = event_start - _dt.timedelta(minutes=total_min)

            if is_dry_run(params.dry_run):
                return json.dumps({
                    "status": "dry_run",
                    "event_id": params.event_id,
                    "drive_minutes": drive_min,
                    "leave_by": leave_by.isoformat(),
                }, indent=2)

            updates: dict = {}
            if params.add_popup_reminder:
                updates["reminders"] = {
                    "useDefault": False,
                    "overrides": [{"method": "popup", "minutes": total_min}],
                }
            travel_event_id = None
            if updates:
                cal.events().patch(
                    calendarId=params.calendar_id, eventId=params.event_id,
                    body=updates,
                ).execute()
            if params.add_travel_block:
                travel_body = {
                    "summary": f"Travel to {ev.get('summary','event')}",
                    "description": (
                        f"Auto-added by workflow_departure_reminder.\n"
                        f"Drive time: {drive_min} min + {params.buffer_minutes} min buffer.\n"
                        f"From: {origin_label} (origin: {origin_source})\n"
                        f"To: {loc}"
                    ),
                    "start": {"dateTime": leave_by.isoformat()},
                    "end": {"dateTime": event_start.isoformat()},
                    "transparency": "opaque",
                }
                created = cal.events().insert(
                    calendarId=params.calendar_id, body=travel_body,
                ).execute()
                travel_event_id = created.get("id")
            return json.dumps({
                "status": "ok",
                "event_id": params.event_id,
                "origin": origin_label,
                "origin_source": origin_source,
                "current_location_accuracy_m": (
                    current_loc.get("accuracy_m") if current_loc else None
                ),
                "drive_minutes": drive_min,
                "leave_by": leave_by.isoformat(),
                "popup_added": params.add_popup_reminder,
                "travel_event_id": travel_event_id,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_departure_reminder failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_calendar_drive_time_blocks",
        annotations={
            "title": "Auto-create 'Drive Time' calendar events for every meeting with a location",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_calendar_drive_time_blocks(
        params: CalendarDriveTimeBlocksInput,
    ) -> str:
        """Bulk drive-time logistics for the next N days.

        Walks every event with a real (non-virtual) location chronologically.
        For each, computes the drive time using:
          - Smart-chain origin: if the previous event has a location and ends
            before this one starts, drive starts from there. Otherwise from
            `home_address`.
          - Live-traffic departure_time = the event start.

        Creates a "🚗 Drive to <event>" calendar event with:
          - Destination address as event location (tap-to-navigate from
            Calendar app)
          - Color 11 (red) by default
          - Description block: origin, destination, drive minutes, buffer,
            leave-by, arrive-by, Google Maps directions URL, and a structured
            'assistant trip note' (HTML comment, machine-readable JSON) for
            agentic assistants to consume
          - extendedProperties.private.driveBlockFor = source event ID for
            dedup + cleanup

        Surfaces three categories of issue without auto-resolving:
          1. `conflicts` — drive window overlaps another meeting
          2. `back_to_back_impossible` — previous meeting ends after needed
             departure (you can't physically make it)
          3. `skipped` — too-short drive, no API access, etc.

        Idempotent: re-running won't double-create blocks (dedup via
        extendedProperties). Use `workflow_remove_drive_time_blocks` to undo.

        Cost: ~$0.005 per event with a location (one Distance Matrix call).
        """
        try:
            import datetime as _dt
            from urllib.parse import quote_plus
            cal = gservices.calendar()
            gmaps = gservices.maps()

            home = params.home_address or config.get("home_address")
            # Resolve current location once — used for the FIRST drive only.
            current_loc = _resolve_current_location(
                manual=params.current_location,
                mode=params.current_location_mode,
            )
            if not home and not current_loc:
                return json.dumps({
                    "status": "no_origin",
                    "hint": (
                        "No current location detected and no home_address set. "
                        "Pass home_address, current_location, or set 'home_address' "
                        "in config.json."
                    ),
                }, indent=2)

            tz_name = config.get("default_timezone") or "America/Los_Angeles"
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = None

            now = _dt.datetime.now().astimezone(tz)
            window_end = now + _dt.timedelta(days=params.days_ahead)

            evs = cal.events().list(
                calendarId=params.calendar_id,
                timeMin=now.isoformat(),
                timeMax=window_end.isoformat(),
                singleEvents=True, orderBy="startTime", maxResults=250,
            ).execute().get("items", []) or []

            # Build dedup map: source event_id → drive block event
            existing_blocks: dict[str, dict] = {}
            for e in evs:
                ext = (e.get("extendedProperties") or {}).get("private") or {}
                if ext.get("driveBlockFor"):
                    existing_blocks[ext["driveBlockFor"]] = e

            # Filter to candidate destination events (skip drive blocks themselves).
            candidates: list[dict] = []
            for e in evs:
                ext = (e.get("extendedProperties") or {}).get("private") or {}
                if ext.get("driveBlockFor"):
                    continue  # this IS a drive block — skip
                if params.skip_declined_events:
                    self_attendee = next(
                        (a for a in (e.get("attendees") or []) if a.get("self")),
                        None,
                    )
                    if self_attendee and self_attendee.get("responseStatus") == "declined":
                        continue
                loc = e.get("location") or ""
                if not loc:
                    continue
                low = loc.lower()
                if "://" in loc or "meet.google.com" in low or "zoom.us" in low \
                        or "teams.microsoft.com" in low or "webex.com" in low:
                    continue
                start_dt_iso = (e.get("start") or {}).get("dateTime")
                end_dt_iso = (e.get("end") or {}).get("dateTime")
                if not start_dt_iso or not end_dt_iso:
                    continue  # all-day
                candidates.append({
                    "id": e.get("id"),
                    "summary": e.get("summary", "(no title)"),
                    "location": loc,
                    "start": _dt.datetime.fromisoformat(start_dt_iso),
                    "end": _dt.datetime.fromisoformat(end_dt_iso),
                })

            results = {
                "created": [],
                "skipped": [],
                "conflicts": [],
                "back_to_back_impossible": [],
                "already_blocked": [],
            }

            prev = None
            for c in candidates:
                event_id = c["id"]

                if params.skip_already_blocked and event_id in existing_blocks:
                    results["already_blocked"].append({
                        "event_id": event_id,
                        "event_summary": c["summary"],
                        "drive_block_id": existing_blocks[event_id]["id"],
                    })
                    prev = c
                    continue

                # Smart-chain origin.
                if prev and prev["end"] <= c["start"]:
                    origin = prev["location"]
                    origin_kind = "previous_meeting"
                    origin_label = f"after '{prev['summary']}' at {prev['location']}"
                    origin_event_end = prev["end"]
                elif current_loc:
                    # FIRST drive of the run: prefer detected current location.
                    origin = f"{current_loc['lat']},{current_loc['lng']}"
                    origin_kind = f"current_location_{current_loc['source']}"
                    origin_label = (
                        f"from current location ({current_loc.get('formatted_address') or origin})"
                    )
                    origin_event_end = None
                else:
                    origin = home
                    origin_kind = "home"
                    origin_label = "from home"
                    origin_event_end = None

                try:
                    dm = gmaps.distance_matrix(
                        origins=[origin], destinations=[c["location"]],
                        mode=params.travel_mode, departure_time=c["start"],
                    )
                except Exception as e:
                    results["skipped"].append({
                        "event_id": event_id,
                        "event_summary": c["summary"],
                        "reason": f"distance_matrix_failed: {e}",
                    })
                    prev = c
                    continue

                el = (dm.get("rows") or [{}])[0].get("elements", [{}])[0]
                if el.get("status") != "OK":
                    results["skipped"].append({
                        "event_id": event_id,
                        "event_summary": c["summary"],
                        "reason": f"directions_unavailable_{el.get('status')}",
                    })
                    prev = c
                    continue

                drive_seconds = el.get("duration_in_traffic", el.get("duration"))["value"]
                drive_min = drive_seconds / 60
                distance_m = el.get("distance", {}).get("value", 0)
                distance_km = distance_m / 1000

                if drive_min < params.min_drive_minutes:
                    results["skipped"].append({
                        "event_id": event_id,
                        "event_summary": c["summary"],
                        "reason": f"drive_too_short ({drive_min:.1f} min)",
                        "drive_minutes": round(drive_min, 1),
                    })
                    prev = c
                    continue

                total_min = drive_min + params.buffer_minutes
                leave_by = c["start"] - _dt.timedelta(minutes=total_min)
                # Drive event ends when you arrive at the destination — i.e.
                # buffer minutes BEFORE the meeting starts. The buffer is
                # informational space-after for parking / finding the room.
                drive_end = leave_by + _dt.timedelta(minutes=drive_min)

                # Back-to-back impossibility check.
                back_to_back = False
                shortfall_min = 0.0
                if origin_event_end and leave_by < origin_event_end:
                    back_to_back = True
                    shortfall_min = round(
                        (origin_event_end - leave_by).total_seconds() / 60, 1,
                    )
                    results["back_to_back_impossible"].append({
                        "event_id": event_id,
                        "event_summary": c["summary"],
                        "prev_event": prev["summary"],
                        "prev_end": prev["end"].isoformat(),
                        "needed_leave_by": leave_by.isoformat(),
                        "shortfall_minutes": shortfall_min,
                    })

                # Overlap detection — any other meeting that intersects [leave_by, drive_end].
                # (We use drive_end, not meeting start, since the buffer slot is left "free" in calendar.)
                overlaps = []
                for other in evs:
                    if other.get("id") == event_id:
                        continue
                    other_ext = (other.get("extendedProperties") or {}).get("private") or {}
                    if other_ext.get("driveBlockFor"):
                        continue  # ignore other drive blocks
                    o_start_iso = (other.get("start") or {}).get("dateTime")
                    o_end_iso = (other.get("end") or {}).get("dateTime")
                    if not o_start_iso or not o_end_iso:
                        continue
                    o_start = _dt.datetime.fromisoformat(o_start_iso)
                    o_end = _dt.datetime.fromisoformat(o_end_iso)
                    if o_start < drive_end and o_end > leave_by:
                        overlaps.append({
                            "id": other.get("id"),
                            "summary": other.get("summary", "(no title)"),
                            "start": o_start_iso,
                            "end": o_end_iso,
                        })

                # Build event body.
                maps_url = (
                    "https://www.google.com/maps/dir/?api=1"
                    f"&origin={quote_plus(origin)}"
                    f"&destination={quote_plus(c['location'])}"
                    f"&travelmode={params.travel_mode}"
                )

                assistant_summary = {
                    "type": "drive_time_block",
                    "linked_event_id": event_id,
                    "linked_event_summary": c["summary"],
                    "linked_event_start": c["start"].isoformat(),
                    "origin": origin,
                    "origin_kind": origin_kind,
                    "destination": c["location"],
                    "drive_minutes": round(drive_min, 1),
                    "buffer_minutes_after_drive": params.buffer_minutes,
                    "distance_km": round(distance_km, 2),
                    "leave_by": leave_by.isoformat(),
                    "drive_ends_at": drive_end.isoformat(),
                    "meeting_starts_at": c["start"].isoformat(),
                    "travel_mode": params.travel_mode,
                    "reminder_minutes_before_drive": params.reminder_minutes_before,
                    "back_to_back_impossible": back_to_back,
                    "back_to_back_shortfall_minutes": shortfall_min,
                    "overlap_conflict_event_ids": [o["id"] for o in overlaps],
                }

                description_lines = [
                    f"🚗 Drive {origin_label}",
                    "",
                    f"From: {origin}",
                    f"To: {c['location']}",
                    f"Drive: {round(drive_min)} min "
                    f"({round(distance_km, 1)} km, traffic-aware)",
                    f"Leave by: {leave_by.strftime('%H:%M %Z')}",
                    f"Drive ends: {drive_end.strftime('%H:%M %Z')}",
                    f"Meeting starts: {c['start'].strftime('%H:%M %Z')}"
                    + (
                        f"  (buffer {params.buffer_minutes} min for parking/walking)"
                        if params.buffer_minutes > 0 else ""
                    ),
                ]
                if back_to_back:
                    description_lines.append("")
                    description_lines.append(
                        f"⚠️ BACK-TO-BACK: previous meeting '{prev['summary']}' "
                        f"ends {shortfall_min} min after needed departure."
                    )
                if overlaps:
                    description_lines.append("")
                    description_lines.append(
                        f"⚠️ CONFLICTS: drive overlaps {len(overlaps)} event(s):"
                    )
                    for o in overlaps:
                        description_lines.append(
                            f"  • {o['summary']} ({o['start'][:16]} → {o['end'][:16]})"
                        )
                description_lines.append("")
                description_lines.append(f"📍 Directions: {maps_url}")
                description_lines.append("")
                description_lines.append("—— Assistant trip note ——")
                description_lines.append("<!-- assistant_trip_note")
                description_lines.append(json.dumps(assistant_summary, indent=2))
                description_lines.append("-->")

                event_body = {
                    "summary": f"🚗 Drive to {c['summary']}",
                    "location": c["location"],
                    # Length = drive duration only. Buffer (if any) is the
                    # space between drive_end and meeting start, left free in
                    # calendar so the user can use it for parking/walking.
                    "start": {"dateTime": leave_by.isoformat()},
                    "end": {"dateTime": drive_end.isoformat()},
                    "transparency": "opaque",
                    "colorId": params.color_id,
                    "description": "\n".join(description_lines),
                    "extendedProperties": {
                        "private": {
                            "driveBlockFor": event_id,
                            "createdBy": "workflow_calendar_drive_time_blocks",
                        }
                    },
                    "reminders": {
                        "useDefault": False,
                        "overrides": [{
                            "method": "popup",
                            "minutes": params.reminder_minutes_before,
                        }],
                    },
                }

                if is_dry_run(params.dry_run):
                    results["created"].append({
                        "status": "dry_run",
                        "event_id": event_id,
                        "event_summary": c["summary"],
                        "drive_block_summary": event_body["summary"],
                        "leave_by": leave_by.isoformat(),
                        "drive_ends_at": drive_end.isoformat(),
                        "meeting_starts_at": c["start"].isoformat(),
                        "drive_minutes": round(drive_min, 1),
                        "buffer_minutes_after_drive": params.buffer_minutes,
                        "origin": origin,
                        "origin_kind": origin_kind,
                        "overlap_conflict_count": len(overlaps),
                        "overlap_summaries": [o["summary"] for o in overlaps],
                        "back_to_back_impossible": back_to_back,
                    })
                else:
                    created = cal.events().insert(
                        calendarId=params.calendar_id, body=event_body,
                    ).execute()
                    results["created"].append({
                        "event_id": event_id,
                        "event_summary": c["summary"],
                        "drive_block_id": created.get("id"),
                        "drive_block_link": created.get("htmlLink"),
                        "leave_by": leave_by.isoformat(),
                        "drive_ends_at": drive_end.isoformat(),
                        "meeting_starts_at": c["start"].isoformat(),
                        "drive_minutes": round(drive_min, 1),
                        "buffer_minutes_after_drive": params.buffer_minutes,
                        "origin": origin,
                        "origin_kind": origin_kind,
                        "overlap_conflict_count": len(overlaps),
                        "overlap_summaries": [o["summary"] for o in overlaps],
                        "back_to_back_impossible": back_to_back,
                    })

                if overlaps:
                    results["conflicts"].append({
                        "event_id": event_id,
                        "event_summary": c["summary"],
                        "overlap_count": len(overlaps),
                        "overlaps": overlaps,
                    })

                prev = c

            return json.dumps({
                "status": "dry_run" if is_dry_run(params.dry_run) else "ok",
                "events_scanned": len(evs),
                "candidates": len(candidates),
                "created_count": len(results["created"]),
                "skipped_count": len(results["skipped"]),
                "already_blocked_count": len(results["already_blocked"]),
                "conflict_count": len(results["conflicts"]),
                "back_to_back_impossible_count": len(results["back_to_back_impossible"]),
                **results,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_calendar_drive_time_blocks failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_remove_drive_time_blocks",
        annotations={
            "title": "Remove drive-time blocks created by workflow_calendar_drive_time_blocks",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_remove_drive_time_blocks(
        params: RemoveDriveTimeBlocksInput,
    ) -> str:
        """Clean up auto-created drive blocks within a window.

        Identifies them by `extendedProperties.private.createdBy ==
        'workflow_calendar_drive_time_blocks'`. Will not touch any drive event
        you created manually.
        """
        try:
            import datetime as _dt
            cal = gservices.calendar()

            tz_name = config.get("default_timezone") or "America/Los_Angeles"
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = None
            now = _dt.datetime.now().astimezone(tz)
            window_start = now - _dt.timedelta(days=params.days_back)
            window_end = now + _dt.timedelta(days=params.days_ahead)

            evs = cal.events().list(
                calendarId=params.calendar_id,
                timeMin=window_start.isoformat(),
                timeMax=window_end.isoformat(),
                singleEvents=True, orderBy="startTime", maxResults=500,
                privateExtendedProperty="createdBy=workflow_calendar_drive_time_blocks",
            ).execute().get("items", []) or []

            removed = []
            for e in evs:
                evt = {
                    "id": e.get("id"),
                    "summary": e.get("summary"),
                    "start": (e.get("start") or {}).get("dateTime"),
                    "end": (e.get("end") or {}).get("dateTime"),
                    "linked_event_id": (
                        (e.get("extendedProperties") or {}).get("private") or {}
                    ).get("driveBlockFor"),
                }
                if not is_dry_run(params.dry_run):
                    try:
                        cal.events().delete(
                            calendarId=params.calendar_id, eventId=e["id"],
                        ).execute()
                        evt["status"] = "deleted"
                    except Exception as inner:
                        evt["status"] = f"delete_failed: {inner}"
                else:
                    evt["status"] = "dry_run"
                removed.append(evt)

            return json.dumps({
                "status": "dry_run" if is_dry_run(params.dry_run) else "ok",
                "found": len(removed),
                "removed": removed,
            }, indent=2)
        except Exception as e:
            log.error("workflow_remove_drive_time_blocks failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_route_optimize_advanced",
        annotations={
            "title": "Solve full Vehicle Routing Problem with time windows + capacities + multi-vehicle",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_route_optimize_advanced(params: AdvRouteOptimizeInput) -> str:
        """Production routing via Google's Route Optimization API.

        Unlike `workflow_route_optimize_visits` (nearest-neighbor heuristic,
        ~$0.005, single vehicle, no constraints), this calls Google's actual
        VRP solver supporting:

          - **Time windows** per stop (earliest/latest arrival)
          - **Multiple vehicles** with their own start/end + shift windows
          - **Vehicle capacities** + per-stop load demands
          - **Service time** at each stop
          - **Skip penalties** — optimizer can choose to skip if too expensive
          - **Global start/end times** bounding the whole plan

        Cost: roughly $0.05–$0.20 per stop depending on tier. ~40× more
        expensive than the heuristic but solves problems it cannot.

        Auth: reuses your Maps API key. Requires Route Optimization API
        enabled in your GCP project (see `GCP_SETUP.md` Section 2d).

        Returns per-vehicle ordered visits with timestamps + skipped stops
        + total cost + total drive time.
        """
        try:
            import requests
            import auth as _auth
            gmaps = gservices.maps()  # raises if Maps key missing (used for geocoding)

            project_id = config.gcp_project_id()
            if not project_id:
                return json.dumps({
                    "status": "no_gcp_project_id",
                    "hint": (
                        "Couldn't auto-detect from credentials.json. Set "
                        "'gcp_project_id' in config.json (the GCP project where "
                        "Route Optimization API is enabled)."
                    ),
                }, indent=2)

            # Route Optimization API requires OAuth (cloud-platform scope), not API key.
            # The Maps API key is only used here for the upstream geocoding step.
            try:
                creds = _auth.get_credentials()
            except Exception as e:
                return json.dumps({
                    "status": "oauth_failed",
                    "error": str(e),
                    "hint": (
                        "Re-auth required. Delete token.json and run the install "
                        "wizard / first-call to re-grant scopes (cloud-platform was "
                        "added). See GCP_SETUP.md Section 2d."
                    ),
                }, indent=2)
            if not creds or not creds.token:
                return json.dumps({
                    "status": "no_oauth_token",
                    "hint": "Run the OAuth flow first; token.json missing.",
                }, indent=2)

            # Default to single vehicle starting from first stop if none provided.
            vehicles_in = params.vehicles or [
                AdvRouteVehicle(start_address=params.stops[0].address)
            ]
            if not vehicles_in:
                return "Error: at least one vehicle is required."

            # 1. Geocode every unique address.
            unique_addrs: list[str] = []
            for s in params.stops:
                if s.address not in unique_addrs:
                    unique_addrs.append(s.address)
            for v in vehicles_in:
                for a in (v.start_address, v.end_address):
                    if a and a not in unique_addrs:
                        unique_addrs.append(a)

            geo_cache: dict[str, dict] = {}
            for addr in unique_addrs:
                # Allow 'lat,lng' shortcut.
                if "," in addr and addr.replace(",", "").replace(".", "").replace("-", "").replace(" ", "").isdigit():
                    lat, lng = (float(p.strip()) for p in addr.split(",", 1))
                    geo_cache[addr] = {"latitude": lat, "longitude": lng}
                    continue
                gres = gmaps.geocode(addr)
                if not gres:
                    return json.dumps({
                        "status": "geocode_failed",
                        "address": addr,
                    }, indent=2)
                loc = gres[0]["geometry"]["location"]
                geo_cache[addr] = {"latitude": loc["lat"], "longitude": loc["lng"]}

            def _waypoint(addr: str) -> dict:
                return {"location": {"latLng": geo_cache[addr]}}

            # 2. Build the OptimizeToursRequest body.
            shipments: list[dict] = []
            for s in params.stops:
                visit = {
                    "arrivalWaypoint": _waypoint(s.address),
                    "duration": f"{s.duration_minutes * 60}s",
                }
                tw: dict = {}
                if s.earliest_arrival:
                    tw["startTime"] = s.earliest_arrival
                if s.latest_arrival:
                    tw["endTime"] = s.latest_arrival
                if tw:
                    visit["timeWindows"] = [tw]
                if s.label:
                    visit["label"] = s.label

                shipment: dict = {
                    "deliveries": [visit],
                    "penaltyCost": s.skip_penalty,
                }
                if s.load_demand is not None:
                    shipment["loadDemands"] = {
                        "units": {"amount": str(s.load_demand)}
                    }
                if s.label:
                    shipment["label"] = s.label
                shipments.append(shipment)

            vehicles: list[dict] = []
            for v in vehicles_in:
                end_addr = v.end_address or v.start_address
                veh: dict = {
                    "startWaypoint": _waypoint(v.start_address),
                    "endWaypoint": _waypoint(end_addr),
                    "travelMode": v.travel_mode,
                    "costPerHour": v.cost_per_hour,
                    "costPerKilometer": v.cost_per_km,
                }
                if v.shift_start:
                    veh["startTimeWindows"] = [{"startTime": v.shift_start}]
                if v.shift_end:
                    veh["endTimeWindows"] = [{"endTime": v.shift_end}]
                if v.load_capacity is not None:
                    veh["loadLimits"] = {
                        "units": {"maxLoad": str(v.load_capacity)}
                    }
                if v.label:
                    veh["label"] = v.label
                vehicles.append(veh)

            model: dict = {"shipments": shipments, "vehicles": vehicles}
            # Default global_start to "now" so response timestamps are real.
            # Without this, Google defaults to Unix epoch (1970-01-01) which
            # makes the per-visit times confusing.
            import datetime as _dt_default
            effective_global_start = params.global_start
            timestamps_are_relative = False
            if not effective_global_start:
                # If any stops have time-window constraints, infer global_start
                # from the earliest of those. Otherwise use now+1min.
                earliest_window = None
                for s in params.stops:
                    if s.earliest_arrival:
                        try:
                            t = _dt_default.datetime.fromisoformat(s.earliest_arrival)
                            if earliest_window is None or t < earliest_window:
                                earliest_window = t
                        except Exception:
                            pass
                if earliest_window:
                    effective_global_start = earliest_window.isoformat()
                else:
                    effective_global_start = (
                        _dt_default.datetime.now()
                        .astimezone()
                        .replace(microsecond=0)
                        .isoformat()
                    )
                    timestamps_are_relative = False
            model["globalStartTime"] = effective_global_start
            if params.global_end:
                model["globalEndTime"] = params.global_end

            body: dict = {
                "model": model,
                "searchMode": (
                    "RETURN_FAST"
                    if params.optimization_mode == "RETURN_FAST"
                    else "CONSUME_ALL_AVAILABLE_TIME"
                ),
                "timeout": f"{params.timeout_seconds}s",
            }

            if is_dry_run(params.dry_run):
                return json.dumps({
                    "status": "dry_run",
                    "would_post_to": (
                        f"https://routeoptimization.googleapis.com/v1/"
                        f"projects/{project_id}:optimizeTours"
                    ),
                    "shipments": len(shipments),
                    "vehicles": len(vehicles),
                    "request_body_kb": round(len(json.dumps(body)) / 1024, 2),
                    "geocoded_addresses": len(geo_cache),
                }, indent=2)

            url = (
                f"https://routeoptimization.googleapis.com/v1/"
                f"projects/{project_id}:optimizeTours"
            )
            headers = {
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/json",
                "X-Goog-User-Project": project_id,  # required for billing on shared keys
            }
            try:
                resp = requests.post(
                    url, json=body, headers=headers,
                    timeout=params.timeout_seconds + 30,
                )
            except requests.exceptions.RequestException as e:
                return json.dumps({
                    "status": "network_error", "error": str(e),
                }, indent=2)

            if resp.status_code == 403:
                return json.dumps({
                    "status": "permission_denied",
                    "http_status": 403,
                    "error": resp.text[:500],
                    "hint": (
                        "Likely causes: (1) Route Optimization API not enabled "
                        f"in project '{project_id}'; (2) OAuth token missing the "
                        "cloud-platform scope (delete token.json and re-auth)."
                    ),
                }, indent=2)
            if resp.status_code == 401:
                return json.dumps({
                    "status": "auth_failed",
                    "hint": (
                        "OAuth token invalid or scope missing. Delete token.json "
                        "and re-run the OAuth flow to pick up cloud-platform scope."
                    ),
                }, indent=2)
            if not resp.ok:
                return json.dumps({
                    "status": "api_error",
                    "http_status": resp.status_code,
                    "error": resp.text[:500],
                }, indent=2)

            result = resp.json()

            # 3. Parse response into a friendly per-vehicle timeline.
            routes_out: list[dict] = []
            total_drive_seconds = 0
            for r in result.get("routes", []):
                v_idx = r.get("vehicleIndex", 0)
                v_in = vehicles_in[v_idx]
                visits_out = []
                for v in r.get("visits", []):
                    s_idx = v.get("shipmentIndex", 0)
                    stop = params.stops[s_idx]
                    visits_out.append({
                        "stop_label": stop.label or stop.address,
                        "stop_address": stop.address,
                        "arrival_time": v.get("startTime"),
                        "service_minutes": stop.duration_minutes,
                        "demand": stop.load_demand,
                    })
                metrics = r.get("metrics", {})

                def _to_sec(s: str | None) -> int:
                    if not s:
                        return 0
                    return int(str(s).rstrip("s") or "0")

                travel_sec = _to_sec(metrics.get("travelDuration"))
                total_drive_seconds += travel_sec
                routes_out.append({
                    "vehicle_label": v_in.label or v_in.start_address,
                    "start": r.get("vehicleStartTime"),
                    "end": r.get("vehicleEndTime"),
                    "visit_count": metrics.get("performedShipmentCount", 0),
                    "drive_minutes": round(travel_sec / 60, 1),
                    "total_minutes": round(_to_sec(metrics.get("totalDuration")) / 60, 1),
                    "visits": visits_out,
                })

            # Build skipped output, with inferred reasons (Google's `reasons[]`
            # array is often empty — we compute likely causes from constraints).
            def _parse_iso(s: str | None):
                if not s:
                    return None
                try:
                    import datetime as _ddt
                    return _ddt.datetime.fromisoformat(s)
                except Exception:
                    return None

            def _infer_skip_reasons(stop: AdvRouteStop) -> list[str]:
                reasons = []
                # Capacity check.
                if stop.load_demand is not None:
                    max_cap = max(
                        (v.load_capacity or 0) for v in vehicles_in
                    )
                    if max_cap > 0 and stop.load_demand > max_cap:
                        reasons.append(
                            f"exceeds_all_vehicle_capacities "
                            f"(demand {stop.load_demand} > max cap {max_cap})"
                        )
                # Time-window vs vehicle shift checks.
                stop_earliest = _parse_iso(stop.earliest_arrival)
                stop_latest = _parse_iso(stop.latest_arrival)
                shift_starts = [
                    _parse_iso(v.shift_start) for v in vehicles_in
                    if v.shift_start
                ]
                shift_ends = [
                    _parse_iso(v.shift_end) for v in vehicles_in
                    if v.shift_end
                ]
                shift_starts = [s for s in shift_starts if s]
                shift_ends = [s for s in shift_ends if s]
                if stop_latest and shift_starts:
                    if all(s > stop_latest for s in shift_starts):
                        reasons.append(
                            "before_all_shift_starts "
                            f"(latest_arrival {stop.latest_arrival} earlier than "
                            f"all vehicles' shift_start)"
                        )
                if stop_earliest and shift_ends:
                    if all(e < stop_earliest for e in shift_ends):
                        reasons.append(
                            "after_all_shift_ends "
                            f"(earliest_arrival {stop.earliest_arrival} later than "
                            f"all vehicles' shift_end)"
                        )
                # Multiple stops competing for the same window.
                if stop_earliest and stop_latest:
                    competing = sum(
                        1 for s in params.stops
                        if s is not stop
                        and _parse_iso(s.earliest_arrival)
                        and _parse_iso(s.latest_arrival)
                        and _parse_iso(s.earliest_arrival) <= stop_latest
                        and _parse_iso(s.latest_arrival) >= stop_earliest
                    )
                    if competing > 0:
                        reasons.append(
                            f"competing_window ({competing} other stop(s) overlap "
                            f"this {stop.earliest_arrival}–{stop.latest_arrival} slot)"
                        )
                # Low penalty → optimizer rationally skipped.
                if stop.skip_penalty < 1000:
                    reasons.append(
                        f"low_skip_penalty ({stop.skip_penalty} — cheaper to skip "
                        f"than reroute)"
                    )
                if not reasons:
                    reasons.append("infeasible_within_constraints")
                return reasons

            skipped_out = []
            for sk in result.get("skippedShipments", []):
                s_idx = sk.get("index", 0)
                stop = params.stops[s_idx]
                google_reasons = [r.get("code") for r in sk.get("reasons", []) if r.get("code")]
                skipped_out.append({
                    "stop_label": stop.label or stop.address,
                    "stop_address": stop.address,
                    "google_reasons": google_reasons,
                    "inferred_reasons": _infer_skip_reasons(stop),
                })

            return json.dumps({
                "status": "ok",
                "project": project_id,
                "total_shipments": len(shipments),
                "total_vehicles": len(vehicles),
                "total_drive_minutes": round(total_drive_seconds / 60, 1),
                "performed_count": sum(r["visit_count"] for r in routes_out),
                "skipped_count": len(skipped_out),
                "total_cost": result.get("totalCost", 0),
                "global_start_used": effective_global_start,
                "global_start_was_inferred": params.global_start is None,
                "routes": routes_out,
                "skipped_stops": skipped_out,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_route_optimize_advanced failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_route_optimize_from_calendar",
        annotations={
            "title": "Pull a day's calendar events with locations + VRP-optimize as a feasibility check",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_route_optimize_from_calendar(
        params: RouteFromCalendarInput,
    ) -> str:
        """Take a date range, find every calendar event with a real location,
        and run them through the Route Optimization API as VRP stops.

        Each event becomes a stop where:
          - earliest_arrival = event.start - early_buffer_minutes
          - latest_arrival = event.start (must arrive by meeting start)
          - duration_minutes = (event.end - event.start)

        Useful as an *upfront feasibility check* on a planned day before
        creating drive-time blocks. Complements
        `workflow_calendar_drive_time_blocks` (which is day-of logistics).

        Skipped stops in the response indicate calendar events that can't be
        physically reached given the constraints — fix those before creating
        drive blocks.

        `additional_stops` lets you propose extra visits to fit around the
        existing meetings. The optimizer skips them with low penalty if they
        don't fit.
        """
        try:
            import datetime as _dt
            cal = gservices.calendar()

            home = params.home_address or config.get("home_address")
            if not home:
                return json.dumps({
                    "status": "no_home_address",
                    "hint": "Pass home_address or set 'home_address' in config.json.",
                }, indent=2)

            tz_name = config.get("default_timezone") or "America/Los_Angeles"
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = None

            # Normalize date_start / date_end to ISO datetimes.
            def _to_dt(s: str) -> _dt.datetime:
                # Accept YYYY-MM-DD or full ISO 8601.
                if "T" in s:
                    return _dt.datetime.fromisoformat(s)
                return _dt.datetime.combine(
                    _dt.date.fromisoformat(s), _dt.time(0, 0), tzinfo=tz,
                )

            start_dt = _to_dt(params.date_start)
            end_dt = _to_dt(params.date_end)
            if end_dt <= start_dt:
                # Default to end-of-day if user gave a date.
                end_dt = _dt.datetime.combine(
                    end_dt.date(), _dt.time(23, 59), tzinfo=tz,
                )

            evs = cal.events().list(
                calendarId=params.calendar_id,
                timeMin=start_dt.isoformat(),
                timeMax=end_dt.isoformat(),
                singleEvents=True, orderBy="startTime", maxResults=200,
            ).execute().get("items", []) or []

            calendar_stops: list[AdvRouteStop] = []
            event_meta: list[dict] = []  # parallel array for response context
            for e in evs:
                # Skip drive-time blocks created by our other workflow.
                ext = (e.get("extendedProperties") or {}).get("private") or {}
                if ext.get("driveBlockFor"):
                    continue
                loc = e.get("location") or ""
                if not loc:
                    continue
                low = loc.lower()
                if "://" in loc or "meet.google.com" in low or "zoom.us" in low \
                        or "teams.microsoft.com" in low or "webex.com" in low:
                    continue
                e_start_iso = (e.get("start") or {}).get("dateTime")
                e_end_iso = (e.get("end") or {}).get("dateTime")
                if not e_start_iso or not e_end_iso:
                    continue  # all-day
                e_start = _dt.datetime.fromisoformat(e_start_iso)
                e_end = _dt.datetime.fromisoformat(e_end_iso)
                duration = max(1, int((e_end - e_start).total_seconds() / 60))
                earliest = e_start - _dt.timedelta(
                    minutes=params.early_buffer_minutes,
                )
                calendar_stops.append(AdvRouteStop(
                    address=loc,
                    label=f"{e.get('summary', '(no title)')} @ {e_start.strftime('%H:%M')}",
                    duration_minutes=duration,
                    earliest_arrival=earliest.isoformat(),
                    latest_arrival=e_start.isoformat(),
                    skip_penalty=100000.0,  # calendar events are HARD — high penalty
                ))
                event_meta.append({
                    "event_id": e.get("id"),
                    "event_summary": e.get("summary"),
                    "event_start": e_start_iso,
                    "event_end": e_end_iso,
                    "event_location": loc,
                })

            if not calendar_stops and not (params.additional_stops or []):
                return json.dumps({
                    "status": "no_eligible_events",
                    "events_scanned": len(evs),
                    "hint": (
                        "No events with real (non-virtual) locations + dateTime "
                        "found in the window."
                    ),
                }, indent=2)

            stops_all = list(calendar_stops) + list(params.additional_stops or [])

            # Bound the global window using the actual events.
            global_start = min(
                _dt.datetime.fromisoformat(s.earliest_arrival)
                for s in calendar_stops
                if s.earliest_arrival
            ) if calendar_stops else start_dt
            global_end = max(
                _dt.datetime.fromisoformat(s.latest_arrival)
                + _dt.timedelta(minutes=s.duration_minutes + 60)
                for s in calendar_stops
                if s.latest_arrival
            ) if calendar_stops else end_dt
            # Allow some breathing room.
            global_start -= _dt.timedelta(hours=1)
            global_end += _dt.timedelta(hours=2)

            vehicle = AdvRouteVehicle(
                start_address=home,
                end_address=params.end_address or home,
                label=f"From {home}",
                shift_start=global_start.isoformat(),
                shift_end=global_end.isoformat(),
                cost_per_hour=params.cost_per_hour,
                cost_per_km=params.cost_per_km,
            )

            # Delegate to the existing advanced optimizer by building its input
            # and calling it (we'd need to call it directly — simpler to inline
            # the same request-build logic, but reuse via in-process call would
            # require refactoring. For v1, we replicate the post here.)
            inner_params = AdvRouteOptimizeInput(
                stops=stops_all,
                vehicles=[vehicle],
                global_start=global_start.isoformat(),
                global_end=global_end.isoformat(),
                optimization_mode=params.optimization_mode,
                timeout_seconds=params.timeout_seconds,
                dry_run=params.dry_run,
            )

            # Re-use the advanced optimizer fully via direct in-process call.
            result_json = await workflow_route_optimize_advanced(inner_params)
            try:
                inner_result = json.loads(result_json)
            except Exception:
                return result_json  # not JSON — pass through as-is

            # Annotate the result with calendar-event context.
            inner_result["calendar_window"] = {
                "start": params.date_start,
                "end": params.date_end,
                "events_scanned": len(evs),
                "events_used_as_stops": len(calendar_stops),
                "additional_stops": len(params.additional_stops or []),
            }
            inner_result["events"] = event_meta
            return json.dumps(inner_result, indent=2)
        except Exception as e:
            log.error("workflow_route_optimize_from_calendar failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_travel_brief",
        annotations={
            "title": "Pre-trip brief: contacts in city, suggested slots, area context",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_travel_brief(params: TravelBriefInput) -> str:
        """City + dates → contacts in area, calendar gap analysis, suggested slots.

        Optionally writes a Google Doc with the brief and/or emails it.
        """
        try:
            import datetime as _dt
            gmaps = gservices.maps()
            cal = gservices.calendar()

            # 1. Geocode the city.
            geo = gmaps.geocode(params.city)
            if not geo:
                return json.dumps({"status": "city_not_found", "city": params.city}, indent=2)
            anchor_loc = geo[0]["geometry"]["location"]
            anchor = (anchor_loc["lat"], anchor_loc["lng"])
            city_label = geo[0].get("formatted_address", params.city)

            # 2. Find contacts in the area.
            in_area: list[dict] = []
            for p in _walk_all_contacts(max_contacts=5000):
                latlng = _contact_lat_lng(p)
                if not latlng:
                    if params.require_geocoded:
                        continue
                    block = _extract_address_block(p)
                    addr = block.get("formatted")
                    if not addr:
                        continue
                    try:
                        g = gmaps.geocode(addr)
                    except Exception as e:
                        log.debug("geocode failed for %r: %s",
                                  addr, e)
                        continue
                    if not g:
                        continue
                    g0 = g[0]["geometry"]["location"]
                    latlng = (g0["lat"], g0["lng"])
                d = _haversine_km(anchor, latlng)
                if d > params.radius_km:
                    continue
                flat = _flatten_person(p)
                in_area.append({
                    "name": flat.get("name"),
                    "email": flat.get("email"),
                    "organization": flat.get("organization"),
                    "title": flat.get("title"),
                    "city": _extract_address_block(p).get("city"),
                    "distance_km": round(d, 2),
                    "last_interaction": (flat.get("custom") or {}).get("Last Interaction"),
                })
                if len(in_area) >= params.max_contacts:
                    break
            in_area.sort(key=lambda r: r["distance_km"])

            # 3. Calendar gaps in the trip window.
            tz_name = config.get("default_timezone") or "America/Los_Angeles"
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = None
            start = _dt.datetime.combine(
                _dt.date.fromisoformat(params.start_date), _dt.time(0, 0), tzinfo=tz,
            )
            end = _dt.datetime.combine(
                _dt.date.fromisoformat(params.end_date), _dt.time(23, 59), tzinfo=tz,
            )
            evs = cal.events().list(
                calendarId="primary",
                timeMin=start.isoformat(), timeMax=end.isoformat(),
                singleEvents=True, orderBy="startTime", maxResults=200,
            ).execute().get("items", []) or []
            existing_blocks = []
            for e in evs:
                s = (e.get("start") or {}).get("dateTime")
                ee = (e.get("end") or {}).get("dateTime")
                if s and ee:
                    existing_blocks.append({
                        "summary": e.get("summary", ""),
                        "start": s, "end": ee,
                        "location": e.get("location"),
                    })

            # 4. Render brief text.
            brief_lines = [
                f"# Travel Brief — {city_label}",
                f"_{params.start_date} → {params.end_date}_",
                "",
                f"## Contacts in area ({len(in_area)})",
            ]
            for c in in_area[:25]:
                org = f" ({c['organization']})" if c.get("organization") else ""
                brief_lines.append(f"- {c['name'] or c['email']}{org} — {c['distance_km']} km — last: {c.get('last_interaction','—')}")
            brief_lines.append("")
            brief_lines.append(f"## Existing calendar blocks ({len(existing_blocks)})")
            for b in existing_blocks[:25]:
                brief_lines.append(f"- {b['start'][:16]} → {b['end'][:16]}: {b['summary']} ({b.get('location') or 'no location'})")
            brief_text = "\n".join(brief_lines)

            doc_url = None
            if params.write_doc:
                try:
                    docs = gservices.docs()
                    title = f"Travel Brief — {params.city} {params.start_date}"
                    created = docs.documents().create(body={"title": title}).execute()
                    doc_id = created.get("documentId")
                    docs.documents().batchUpdate(
                        documentId=doc_id,
                        body={"requests": [{"insertText": {"location": {"index": 1}, "text": brief_text}}]},
                    ).execute()
                    doc_url = f"https://docs.google.com/document/d/{doc_id}/edit"
                except Exception as e:
                    log.warning("travel_brief doc write failed: %s", e)

            if params.email_to:
                msg = _build_simple_email(
                    to=[params.email_to],
                    subject=f"Travel Brief — {params.city} {params.start_date} → {params.end_date}",
                    body=brief_text + (f"\n\nGoogle Doc: {doc_url}" if doc_url else ""),
                )
                _gmail().users().messages().send(userId="me", body=msg).execute()

            return json.dumps({
                "city": city_label,
                "start_date": params.start_date,
                "end_date": params.end_date,
                "contacts_in_area": len(in_area),
                "calendar_blocks": len(existing_blocks),
                "doc_url": doc_url,
                "emailed_to": params.email_to,
                "top_contacts": in_area[:10],
                "brief_preview": brief_text[:1500],
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_travel_brief failed: %s", e)
            return format_error(e)
