"""Cross-service workflow tools.

These are compositions of primitives from gmail/calendar/drive/docs — wrapped
as single tools when the composition has non-trivial glue (intermediate data
transformation, error handling for partial success, etc.). If a workflow is
just "call A then B," prefer letting Claude compose primitives rather than
baking a new tool.
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


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class SaveAttachmentsToDriveInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message_id: str = Field(..., description="Gmail message ID.")
    drive_folder_id: Optional[str] = Field(
        default=None,
        description="Destination Drive folder ID. Default: My Drive root.",
    )
    attachment_filter: Optional[str] = Field(
        default=None,
        description="Optional substring to match against filenames (case-insensitive).",
    )
    dry_run: Optional[bool] = Field(default=None)


class EmailDocAsPdfInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    document_id: str = Field(..., description="Google Doc ID to export.")
    to: list[str] = Field(..., min_length=1, description="Recipients.")
    subject: Optional[str] = Field(
        default=None, description="Email subject. Defaults to the doc's title."
    )
    body: Optional[str] = Field(
        default="See attached.", description="Plain-text email body."
    )
    cc: Optional[list[str]] = Field(default=None)
    filename: Optional[str] = Field(
        default=None, description="Attachment filename. Defaults to '<doc title>.pdf'."
    )
    dry_run: Optional[bool] = Field(default=None)


class ShareDriveFileViaEmailInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    file_id: str = Field(..., description="Drive file ID to share.")
    recipient_email: str = Field(..., description="Person to share with.")
    role: str = Field(default="reader", description="'reader', 'commenter', or 'writer'.")
    subject: Optional[str] = Field(
        default=None, description="Email subject. Defaults to 'Shared: <file name>'."
    )
    message: Optional[str] = Field(
        default=None, description="Optional personal note above the share link."
    )
    dry_run: Optional[bool] = Field(default=None)


class RecipientInput(BaseModel):
    """One mail-merge recipient. Provide EITHER resource_name OR inline fields."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    resource_name: Optional[str] = Field(
        default=None,
        description="People API resource name (e.g. 'people/c123'). Fetched and expanded at send time.",
    )
    email: Optional[str] = Field(
        default=None, description="Direct email address (required if resource_name not given)."
    )
    first_name: Optional[str] = Field(default=None)
    last_name: Optional[str] = Field(default=None)
    organization: Optional[str] = Field(default=None)
    title: Optional[str] = Field(default=None)
    custom: Optional[dict[str, str]] = Field(
        default=None, description="Additional key/value fields for {placeholder} use."
    )


class SendTemplatedInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    recipient: RecipientInput = Field(
        ..., description="Single recipient — either by contact resource_name or inline fields."
    )
    subject: str = Field(..., description="Email subject. Supports {placeholders}.")
    body: str = Field(..., description="Plain-text body. Supports {placeholders}.")
    html_body: Optional[str] = Field(
        default=None, description="Optional HTML body. Supports {placeholders}."
    )
    cc: Optional[list[str]] = Field(default=None)
    bcc: Optional[list[str]] = Field(default=None)
    default_fallback: str = Field(
        default="",
        description="Fallback for missing fields when no per-placeholder fallback given.",
    )
    dry_run: Optional[bool] = Field(default=None)


class SendMailMergeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    subject: str = Field(..., description="Subject template. Supports {placeholders}.")
    body: str = Field(..., description="Body template. Supports {placeholders}.")
    html_body: Optional[str] = Field(
        default=None, description="Optional HTML body template. Supports {placeholders}."
    )
    recipients: Optional[list[RecipientInput]] = Field(
        default=None,
        description="List of recipients (inline or by resource_name). Mutually exclusive with group_resource_name.",
    )
    group_resource_name: Optional[str] = Field(
        default=None,
        description="Contact group resource name — all members get the email. Mutually exclusive with recipients.",
    )
    default_fallback: str = Field(default="")
    dry_run: Optional[bool] = Field(default=None)
    stop_on_first_error: bool = Field(
        default=False,
        description="If True, abort the batch on the first failure. Default: continue and report per-recipient.",
    )


class ListTemplatesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetTemplateInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., description="Template name (filename minus '.md').")


class SeedResponseTemplatesInput(BaseModel):
    """Input for workflow_seed_response_templates."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    force: bool = Field(
        default=False,
        description=(
            "Overwrite templates that already exist. Default False so the "
            "tool never silently clobbers a user-edited template."
        ),
    )
    only: Optional[str] = Field(
        default=None,
        description=(
            "If set, generate only this one slug (e.g. 'welcome_response'). "
            "If unset, generates all 8 starter response templates."
        ),
    )


class SendTemplatedByNameInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    template_name: str = Field(..., description="Name of a template in templates/.")
    recipient: RecipientInput = Field(...)
    cc: Optional[list[str]] = Field(default=None)
    bcc: Optional[list[str]] = Field(default=None)
    default_fallback: str = Field(default="")
    log_to_contact: Optional[bool] = Field(
        default=None,
        description="Append a timestamped activity note to the recipient's contact. "
                    "Defaults to config.log_sent_emails_to_contacts.",
    )
    dry_run: Optional[bool] = Field(default=None)


class SendMailMergeByNameInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    template_name: str = Field(...)
    recipients: Optional[list[RecipientInput]] = Field(default=None)
    group_resource_name: Optional[str] = Field(default=None)
    default_fallback: str = Field(default="")
    log_to_contact: Optional[bool] = Field(default=None)
    dry_run: Optional[bool] = Field(default=None)
    stop_on_first_error: bool = Field(default=False)


class ChatWithMapInput(BaseModel):
    """Input for workflow_chat_with_map."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_name: Optional[str] = Field(
        default=None,
        description="Existing Chat space resource (e.g. 'spaces/AAA...'). Mutually exclusive with email.",
    )
    email: Optional[str] = Field(
        default=None,
        description="Recipient email — auto-resolves DM via findDirectMessage. Mutually exclusive with space_name.",
    )
    text: str = Field(..., description="Message body (sent above the map).")
    location: str = Field(
        ..., description="Address, place name, or 'lat,lng' for the map center.",
    )
    zoom: int = Field(default=15, ge=0, le=21)
    size: str = Field(default="600x400")
    map_type: str = Field(default="roadmap")
    create_dm_if_missing: bool = Field(default=True)
    dry_run: Optional[bool] = Field(default=None)


class ChatSharePlaceInput(BaseModel):
    """Input for workflow_chat_share_place."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    place_id: str = Field(..., description="Place ID from a prior maps_search_* call.")
    space_name: Optional[str] = Field(default=None)
    email: Optional[str] = Field(default=None)
    prefix_text: Optional[str] = Field(
        default=None,
        description="Optional text to prepend to the place card (e.g. 'Lunch spot for Friday?').",
    )
    include_map: bool = Field(default=True)
    create_dm_if_missing: bool = Field(default=True)
    dry_run: Optional[bool] = Field(default=None)


class ChatMeetingBriefAttendee(BaseModel):
    """One attendee for workflow_chat_meeting_brief."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: str = Field(..., description="Recipient's email — used for DM resolution.")
    address: str = Field(
        ..., description="Their starting address — used to compute travel time to the venue.",
    )
    first_name: Optional[str] = Field(default=None, description="Used to personalize the message.")


class ChatMeetingBriefInput(BaseModel):
    """Input for workflow_chat_meeting_brief."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    attendees: list[ChatMeetingBriefAttendee] = Field(..., min_length=1)
    venue: str = Field(
        ...,
        description="Venue address, place name, or place_id (use 'place_id:CHIJ...' prefix for IDs).",
    )
    venue_label: Optional[str] = Field(
        default=None,
        description="Friendly venue name to show in the message. Defaults to the resolved address.",
    )
    meeting_time_iso: Optional[str] = Field(
        default=None,
        description=(
            "ISO 8601 of when the meeting starts. If set, traffic-aware travel times are "
            "calculated using that departure time (Google's traffic prediction)."
        ),
    )
    custom_message: Optional[str] = Field(
        default=None,
        description="Extra message to include after the standard travel-time block.",
    )
    travel_mode: str = Field(default="driving")
    create_dm_if_missing: bool = Field(default=True)
    log_to_contact: bool = Field(default=True)
    stop_on_first_error: bool = Field(default=False)
    dry_run: Optional[bool] = Field(default=None)


class EmailWithMapInput(BaseModel):
    """Input for workflow_email_with_map."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    to: list[str] = Field(..., min_length=1)
    subject: str = Field(...)
    body: str = Field(..., description="Plain-text body. The map image is attached after this text.")
    location: str = Field(
        ...,
        description="Address, place name, or 'lat,lng' string for the map center.",
    )
    zoom: int = Field(default=15, ge=0, le=21)
    size: str = Field(default="600x400")
    map_type: str = Field(default="roadmap")
    cc: Optional[list[str]] = Field(default=None)
    bcc: Optional[list[str]] = Field(default=None)
    dry_run: Optional[bool] = Field(default=None)


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


class ChatDigestWorkflowInput(BaseModel):
    """Input for workflow_chat_digest."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    hours: int = Field(default=24, ge=1, le=168, description="Look-back window. Default 24h.")
    recipient: Optional[str] = Field(
        default=None,
        description="Email recipient for the digest. Defaults to your own primary address.",
    )
    use_llm: bool = Field(
        default=True,
        description="Summarize via Claude when ANTHROPIC_API_KEY is set. Otherwise emit raw per-space groupings.",
    )
    include_dms: bool = Field(default=True)
    include_rooms: bool = Field(default=True)
    dry_run: Optional[bool] = Field(default=None)


class ChatToContactGroupWorkflowInput(BaseModel):
    """Input for workflow_chat_to_contact_group."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    group_resource_name: str = Field(...)
    text: str = Field(
        ...,
        description=(
            "Message body. Supports {first_name|fallback}, {last_name}, "
            "{organization}, {title}, {custom.<key>} placeholders."
        ),
    )
    create_dm_if_missing: bool = Field(default=True)
    log_to_contact: bool = Field(default=True)
    stop_on_first_error: bool = Field(default=False)
    dry_run: Optional[bool] = Field(default=None)


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


class SendHandoffArchiveInput(BaseModel):
    """Input for workflow_send_handoff_archive."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    recipients: list[str] = Field(
        ..., min_length=1,
        description="One or more email addresses to send the handoff to.",
    )
    archive_path: Optional[str] = Field(
        default=None,
        description=(
            "Absolute path to the .tar.gz archive. If omitted, the newest "
            "file matching dist/google-workspace-mcp-*.tar.gz in the project "
            "folder is used."
        ),
    )
    note: Optional[str] = Field(
        default=None,
        description="Optional personal message to prepend to the default handoff email body.",
    )
    subject: Optional[str] = Field(
        default=None,
        description="Override the default handoff email subject.",
    )
    dry_run: Optional[bool] = Field(default=None)


class EmailThreadToEventInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    thread_id: str = Field(..., description="Gmail thread ID to convert into an event.")
    start: str = Field(..., description="ISO 8601 start time of the new event.")
    end: str = Field(..., description="ISO 8601 end time.")
    summary: Optional[str] = Field(
        default=None,
        description="Event title. Defaults to the thread's subject (stripping Re:/Fwd: prefixes).",
    )
    timezone: Optional[str] = Field(default=None)
    add_meet: bool = Field(default=True, description="Auto-add a Google Meet link.")
    send_updates: str = Field(default="all", description="'all', 'externalOnly', or 'none'.")
    include_thread_body: bool = Field(
        default=True,
        description="If True, puts the original thread text in the event description.",
    )
    dry_run: Optional[bool] = Field(default=None)


class CreateContactsFromSentMailInput(BaseModel):
    """Input for workflow_create_contacts_from_sent_mail."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    days: int = Field(
        default=30,
        ge=1,
        le=3650,
        description="How many days back to scan sent mail. Default 30.",
    )
    limit_emails_scanned: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="Max messages to scan from 'in:sent' (newest first). Default 500.",
    )
    skip_existing: bool = Field(
        default=True,
        description="Skip addresses that already exist as saved contacts.",
    )
    exclude_domains: Optional[list[str]] = Field(
        default=None,
        description="Domains to exclude (e.g. 'noreply.com'). Matched case-insensitively.",
    )
    exclude_self: bool = Field(
        default=True,
        description="Exclude your own address and any configured send-as aliases.",
    )
    only_domains: Optional[list[str]] = Field(
        default=None,
        description="If set, ONLY create contacts whose address matches one of these domains.",
    )
    apply_rules_after: bool = Field(
        default=True,
        description="After creating contacts, run contacts_apply_rules so auto-tagging fires.",
    )
    enrich_from_inbox: bool = Field(
        default=False,
        description="After creating contacts, enrich each one from their most recent inbound "
                    "email signature (title, phone E.164, website, social URLs, etc.).",
    )
    enrich_days: int = Field(
        default=180,
        ge=1,
        le=3650,
        description="When enrich_from_inbox is True: how far back to look for a message from "
                    "each new contact.",
    )
    dry_run: Optional[bool] = Field(default=None)


# --- Maps × CRM × Calendar workflow inputs ---------------------------------- #


class NearbyContactsInput(BaseModel):
    """Input for workflow_nearby_contacts."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    location: str = Field(
        ..., description="Address, place name, or 'lat,lng' to search around.",
    )
    radius_km: float = Field(
        default=25.0, gt=0, le=500,
        description="Radius in kilometers. Default 25.",
    )
    limit: int = Field(default=20, ge=1, le=100)
    sort_by: str = Field(
        default="distance",
        description="'distance' or 'recency' (most recently contacted first).",
    )
    only_groups: Optional[list[str]] = Field(
        default=None,
        description="Restrict to contacts in these contactGroup resource names.",
    )
    require_geocoded: bool = Field(
        default=False,
        description=(
            "If True, only consider contacts that already have stored lat/lng custom fields "
            "(fast). If False, geocode on-the-fly for any contact missing them (costs API calls)."
        ),
    )
    include_travel_time: bool = Field(
        default=False,
        description="If True, compute live driving time from `location` to top results via Distance Matrix.",
    )
    travel_mode: str = Field(default="driving")


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


class GeocodeContactsBatchInput(BaseModel):
    """Input for workflow_geocode_contacts_batch."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    only_groups: Optional[list[str]] = Field(
        default=None,
        description="Restrict to contacts in these contactGroup resource names.",
    )
    force: bool = Field(
        default=False,
        description="If True, re-geocode contacts that already have stored lat/lng.",
    )
    max_contacts: int = Field(
        default=500, ge=1, le=5000,
        description="Safety cap on how many contacts to process in one run.",
    )
    dry_run: Optional[bool] = Field(default=None)


class AddressHygieneAuditInput(BaseModel):
    """Input for workflow_address_hygiene_audit."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    only_groups: Optional[list[str]] = Field(
        default=None,
        description="Restrict to contacts in these contactGroup resource names.",
    )
    max_contacts: int = Field(default=500, ge=1, le=5000)
    region_code: Optional[str] = Field(
        default=None,
        description="ISO-3166 country code to bias validation, e.g. 'US'.",
    )
    write_to_sheet: bool = Field(
        default=True,
        description="If True, write the report to a new Google Sheet and return the URL.",
    )
    sheet_title: Optional[str] = Field(default=None)


class ContactDensityMapInput(BaseModel):
    """Input for workflow_contact_density_map."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    only_groups: Optional[list[str]] = Field(default=None)
    region_filter: Optional[str] = Field(
        default=None,
        description="Free-text filter applied to contact city/region (e.g. 'CA' or 'Austin').",
    )
    max_markers: int = Field(default=80, ge=1, le=500)
    size: str = Field(default="640x640")
    map_type: str = Field(default="roadmap")
    save_to_path: Optional[str] = Field(
        default=None,
        description="If set, write PNG to this absolute path. Otherwise return base64.",
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


class BulkContactPatch(BaseModel):
    """One target+patch for workflow_bulk_update_contacts."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    resource_name: str = Field(
        ..., description="Contact resource_name (e.g. 'people/c123abc456').",
    )
    patch: dict = Field(
        ...,
        description=(
            "Fields to update on this contact. Supported keys: organization, "
            "title, notes, custom_fields (dict). Set a key to null to clear."
        ),
    )


class BulkUpdateContactsInput(BaseModel):
    """Input for workflow_bulk_update_contacts."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    updates: list[BulkContactPatch] = Field(..., min_length=1, max_length=500)
    rollback_on_partial_failure: bool = Field(
        default=True,
        description=(
            "If any individual update fails, restore all already-applied "
            "updates from snapshots taken before the run. Atomic-ish — best "
            "you can do without a real transaction."
        ),
    )
    dry_run: Optional[bool] = Field(default=None)


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


# --- Receipt + brand-voice composed workflows ---------------------------- #


class ReceiptChatDigestInput(BaseModel):
    """Input for workflow_receipt_chat_digest."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    chat_space_id: str = Field(
        ..., min_length=3,
        description="Chat space to post the digest into (e.g. 'spaces/AAQA...').",
    )
    sheet_id: Optional[str] = Field(default=None)
    sheet_name: Optional[str] = Field(
        default=None,
        description="Source receipts sheet (resolved against your 'Receipts — *' sheets).",
    )
    days: int = Field(
        default=30, ge=1, le=365,
        description="Look-back window for the digest.",
    )
    dry_run: Optional[bool] = Field(default=None)


class MonthlyExpenseReportInput(BaseModel):
    """Input for workflow_monthly_expense_report."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    month: str = Field(
        ...,
        description="Target month as 'YYYY-MM' (e.g. '2026-04'). Filters rows whose date falls in this month.",
    )
    recipient_email: str = Field(
        ...,
        description="Email address to send the report to (e.g. accountant@firm.com).",
    )
    sheet_id: Optional[str] = Field(default=None)
    sheet_name: Optional[str] = Field(
        default=None,
        description="Source receipts sheet (resolved against your 'Receipts — *' sheets).",
    )
    drive_folder_id: Optional[str] = Field(
        default=None,
        description="Drive folder for the QB CSV. Auto-creates 'CoAssisted Receipts' if not set.",
    )
    dry_run: Optional[bool] = Field(default=None)


class SuggestResponseTemplateInput(BaseModel):
    """Input for workflow_suggest_response_template."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    message_id: Optional[str] = Field(
        default=None,
        description="Gmail message ID. Either this or `text` is required.",
    )
    text: Optional[str] = Field(
        default=None,
        description="Raw text to classify (subject + body). Use when no Gmail thread.",
    )


class SmartFollowupFinderInput(BaseModel):
    """Input for workflow_smart_followup_finder."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    days_stale: int = Field(
        default=7, ge=1, le=90,
        description="Threads with no reply in at least this many days are candidates.",
    )
    max_threads: int = Field(
        default=20, ge=1, le=100,
        description="Cap on candidate threads (Gmail search size).",
    )
    only_external: bool = Field(
        default=True,
        description="Skip threads where last sender is from your own domain.",
    )
    create_drafts: bool = Field(
        default=False,
        description="If True, create a Gmail draft for each candidate using the suggested template.",
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _gmail():
    return gservices.gmail()


def _drive():
    return gservices.drive()


def _calendar_svc():
    return gservices.calendar()


def _log_activity_on_contact(email: str, subject: str, template_name: str | None = None) -> None:
    """If the email matches a saved contact, append a timestamped activity note to its biography.

    Silently no-ops if the contact isn't found or if the append fails. We don't
    want a note-logging problem to break the send path.
    """
    if not email:
        return
    try:
        import datetime as _dt
        people = gservices.people()
        # Warm up search (documented People API quirk).
        people.people().searchContacts(query="", readMask="names,metadata").execute()
        resp = people.people().searchContacts(
            query=email, readMask="names,emailAddresses,biographies,metadata", pageSize=5
        ).execute()
        person = None
        for r in resp.get("results", []):
            addrs = [
                (e.get("value") or "").lower()
                for e in r["person"].get("emailAddresses", []) or []
            ]
            if email.lower() in addrs:
                person = r["person"]
                break
        if not person:
            return
        now = _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
        suffix = f" (template: {template_name})" if template_name else ""
        note = f'[{now}] Sent: "{subject}"{suffix}'
        bios = person.get("biographies") or []
        prev = bios[0].get("value", "") if bios else ""
        combined = (prev + "\n\n" + note).strip() if prev else note
        people.people().updateContact(
            resourceName=person["resourceName"],
            updatePersonFields="biographies",
            body={
                "etag": person["etag"],
                "biographies": [{"value": combined, "contentType": "TEXT_PLAIN"}],
            },
        ).execute()
        log.info("activity logged on %s for %s", person["resourceName"], email)
    except Exception as e:
        log.warning("activity log skipped for %s: %s", email, e)


def _list_attachments_meta(payload: dict, acc: list | None = None) -> list[dict]:
    if acc is None:
        acc = []
    filename = payload.get("filename")
    body = payload.get("body", {})
    if filename and body.get("attachmentId"):
        acc.append(
            {
                "attachment_id": body["attachmentId"],
                "filename": filename,
                "mime_type": payload.get("mimeType"),
                "size": body.get("size", 0),
            }
        )
    for part in payload.get("parts", []) or []:
        _list_attachments_meta(part, acc)
    return acc


def _extract_plaintext(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
            "utf-8", errors="replace"
        )
    out: list[str] = []
    for part in payload.get("parts", []) or []:
        chunk = _extract_plaintext(part)
        if chunk:
            out.append(chunk)
    return "\n\n".join(out)


def _extract_address_block(p: dict) -> dict:
    """Pick the best mailing address from a People API person record.

    Preference order: 'work' → 'home' → first listed. Returns an empty-ish dict
    if no addresses are present.
    """
    addrs = p.get("addresses") or []
    if not addrs:
        return {}
    by_type = {a.get("type"): a for a in addrs if a.get("type")}
    chosen = by_type.get("work") or by_type.get("home") or addrs[0]
    return {
        "formatted": chosen.get("formattedValue"),
        "street": chosen.get("streetAddress"),
        "city": chosen.get("city"),
        "region": chosen.get("region"),
        "postal_code": chosen.get("postalCode"),
        "country": chosen.get("country"),
        "type": chosen.get("type"),
    }


# --- Geocode cache ---------------------------------------------------------- #
# A simple JSON-file backed cache of geocoded addresses, keyed by lowercased
# trimmed address string. Cuts Maps spend significantly for steady-state usage
# since the same addresses come up repeatedly across all 16 Maps×CRM workflows.
# Cache lives at <project>/logs/geocode_cache.json — survives restarts, can be
# cleared by deleting the file.

_GEOCODE_CACHE: dict[str, dict] | None = None
_GEOCODE_CACHE_PATH: "Path | None" = None  # type: ignore  # noqa


def _geocode_cache_path():
    from pathlib import Path as _P
    import config as _cfg
    project_dir = _P(_cfg.__file__).parent
    cache_dir = project_dir / "logs"
    cache_dir.mkdir(exist_ok=True)
    return cache_dir / "geocode_cache.json"


def _load_geocode_cache() -> dict[str, dict]:
    global _GEOCODE_CACHE
    if _GEOCODE_CACHE is not None:
        return _GEOCODE_CACHE
    path = _geocode_cache_path()
    if path.exists():
        try:
            _GEOCODE_CACHE = json.loads(path.read_text())
        except Exception:
            log.warning("geocode_cache.json corrupt — starting fresh")
            _GEOCODE_CACHE = {}
    else:
        _GEOCODE_CACHE = {}
    return _GEOCODE_CACHE


def _save_geocode_cache():
    if _GEOCODE_CACHE is None:
        return
    try:
        _geocode_cache_path().write_text(json.dumps(_GEOCODE_CACHE, indent=2))
    except Exception as e:
        log.warning("Failed to save geocode_cache: %s", e)


def _geocode_cached(address: str) -> dict | None:
    """Geocode `address` using the JSON cache; fall through to live API on miss.

    Returns a dict with keys: lat, lng, formatted_address, place_id, source ('cache' or 'api').
    Returns None if both cache miss AND live API call fail.
    """
    if not address or not address.strip():
        return None
    key = address.strip().lower()
    cache = _load_geocode_cache()
    if key in cache:
        hit = dict(cache[key])
        hit["source"] = "cache"
        return hit
    try:
        gmaps = gservices.maps()
        gres = gmaps.geocode(address)
        if not gres:
            return None
        loc = gres[0]["geometry"]["location"]
        record = {
            "lat": loc["lat"], "lng": loc["lng"],
            "formatted_address": gres[0].get("formatted_address", address),
            "place_id": gres[0].get("place_id"),
        }
        cache[key] = record
        _save_geocode_cache()
        out = dict(record); out["source"] = "api"
        return out
    except Exception as e:
        log.warning("Geocode failed for %r: %s", address, e)
        return None


def _resolve_current_location(
    manual: str | None = None,
    mode: str = "auto",
) -> dict | None:
    """Resolve the user's current physical location.

    Priority order: manual override → macOS CoreLocationCLI → IP geolocation.
    Best-effort. Returns None if all methods fail or mode is 'off'/'home'.

    Returns:
        dict with keys: lat (float), lng (float), accuracy_m (float),
        source (str), formatted_address (str | None), or None on failure.

    Args:
        manual: If provided AND mode is 'auto' or 'manual', geocode this
            string and return immediately. Always wins.
        mode: 'auto' = try all methods, 'home' = skip (caller falls back to
            home_address), 'off' = skip entirely, 'manual' = require `manual`.
    """
    if mode in ("off", "home"):
        return None

    # 1. Manual override — always wins if provided.
    if manual:
        try:
            gmaps = gservices.maps()
            gres = gmaps.geocode(manual)
            if gres:
                loc = gres[0]["geometry"]["location"]
                return {
                    "lat": loc["lat"], "lng": loc["lng"],
                    "accuracy_m": 0.0, "source": "manual",
                    "formatted_address": gres[0].get("formatted_address", manual),
                }
            log.warning("Manual location geocode returned no results: %s", manual)
        except Exception as e:
            log.warning("Manual location geocode failed: %s", e)

    if mode == "manual":
        # If manual was required but failed, don't fall through to detection.
        return None

    # 2. macOS CoreLocationCLI (~10m accuracy, requires `brew install corelocationcli`).
    # Cowork's MCP subprocess often doesn't inherit the user's shell PATH, so
    # the bare command name `CoreLocationCLI` may not be found. Try common
    # Homebrew install paths explicitly before falling back to PATH lookup.
    import subprocess
    import shutil
    from pathlib import Path as _CLPath
    candidate_paths = [
        "/opt/homebrew/bin/CoreLocationCLI",   # Apple Silicon Homebrew
        "/usr/local/bin/CoreLocationCLI",      # Intel Homebrew
        "/opt/local/bin/CoreLocationCLI",      # MacPorts
    ]
    cli_path = next((p for p in candidate_paths if _CLPath(p).exists()), None)
    if cli_path is None:
        # Final fallback: PATH lookup.
        cli_path = shutil.which("CoreLocationCLI")
    if cli_path:
        try:
            proc = subprocess.run(
                [cli_path, "-format",
                 "%latitude,%longitude,%horizontalAccuracy"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                out = proc.stdout.strip()
                # CoreLocationCLI's `-format` flag is permissive: comma-format
                # works on some installs but most return whitespace-separated
                # `lat lng [accuracy]` regardless. Parse robustly: try comma
                # first, then any whitespace.
                if "," in out:
                    parts = [p.strip() for p in out.split(",") if p.strip()]
                else:
                    parts = out.split()
                if len(parts) >= 2:
                    try:
                        lat = float(parts[0])
                        lng = float(parts[1])
                    except ValueError:
                        log.warning(
                            "CoreLocationCLI [%s] output failed numeric parse: %r",
                            cli_path, out[:200],
                        )
                        lat = lng = None  # type: ignore
                    if lat is not None and lng is not None:
                        accuracy = 50.0  # ~50m typical for Wi-Fi triangulation
                        if len(parts) > 2:
                            try:
                                accuracy = float(parts[2])
                            except ValueError:
                                pass
                        # Reverse-geocode for a friendly address (best-effort).
                        addr = None
                        try:
                            gmaps = gservices.maps()
                            rev = gmaps.reverse_geocode((lat, lng))
                            if rev:
                                addr = rev[0].get("formatted_address")
                        except Exception:
                            pass
                        log.info(
                            "current_location via CoreLocationCLI [%s]: "
                            "%.5f,%.5f (±%.0fm)",
                            cli_path, lat, lng, accuracy,
                        )
                        return {
                            "lat": lat, "lng": lng, "accuracy_m": accuracy,
                            "source": "corelocationcli",
                            "formatted_address": addr,
                        }
                else:
                    log.warning(
                        "CoreLocationCLI [%s] output couldn't be split into "
                        "lat/lng: %r", cli_path, out[:200],
                    )
            else:
                log.warning(
                    "CoreLocationCLI [%s] returned rc=%d, stdout=%r, stderr=%r",
                    cli_path, proc.returncode,
                    proc.stdout[:200], proc.stderr[:200],
                )
        except subprocess.TimeoutExpired:
            log.warning(
                "CoreLocationCLI [%s] timed out after 10s — likely waiting for "
                "macOS Location Services permission. Open System Settings → "
                "Privacy & Security → Location Services and enable for the "
                "calling process (Terminal/Cowork).",
                cli_path,
            )
        except Exception as e:
            log.warning("CoreLocationCLI [%s] failed: %s", cli_path, e)
    else:
        log.info(
            "CoreLocationCLI not found in any of: %s — falling through to "
            "Google Geolocation API",
            ", ".join(candidate_paths),
        )

    # 3. Google Geolocation API (uses Maps API key, ~city accuracy via IP).
    try:
        import os
        import requests
        api_key = (
            config.get("google_maps_api_key")
            or os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
        )
        if api_key:
            url = f"https://www.googleapis.com/geolocation/v1/geolocate?key={api_key}"
            resp = requests.post(url, json={"considerIp": True}, timeout=8)
            if resp.ok:
                data = resp.json()
                loc = data.get("location") or {}
                if "lat" in loc and "lng" in loc:
                    accuracy = float(data.get("accuracy", 5000))
                    addr = None
                    try:
                        gmaps = gservices.maps()
                        rev = gmaps.reverse_geocode((loc["lat"], loc["lng"]))
                        if rev:
                            addr = rev[0].get("formatted_address")
                    except Exception:
                        pass
                    log.info(
                        "current_location via Google Geolocation API: "
                        "%.4f,%.4f (±%.0fm)", loc["lat"], loc["lng"], accuracy,
                    )
                    return {
                        "lat": float(loc["lat"]), "lng": float(loc["lng"]),
                        "accuracy_m": accuracy,
                        "source": "google_geolocation",
                        "formatted_address": addr,
                    }
    except Exception as e:
        log.warning("Google Geolocation API failed: %s", e)

    # 4. IP geolocation final fallback (~10km city-level, no API key needed).
    try:
        import requests
        resp = requests.get("https://ipapi.co/json/", timeout=5)
        if resp.ok:
            data = resp.json()
            if "latitude" in data and "longitude" in data:
                addr_parts = [
                    str(data.get("city") or "").strip(),
                    str(data.get("region") or "").strip(),
                    str(data.get("country_name") or data.get("country") or "").strip(),
                ]
                addr = ", ".join(p for p in addr_parts if p)
                log.info(
                    "current_location via ipapi.co: %.4f,%.4f (~%s)",
                    data["latitude"], data["longitude"], addr,
                )
                return {
                    "lat": float(data["latitude"]),
                    "lng": float(data["longitude"]),
                    "accuracy_m": 10000.0,
                    "source": "ipapi_co",
                    "formatted_address": addr or None,
                }
    except Exception as e:
        log.warning("ipapi.co fallback failed: %s", e)

    log.warning("current_location: all detection methods failed")
    return None


def _contact_lat_lng(p: dict) -> tuple[float, float] | None:
    """Read stored lat/lng from a contact's userDefined fields. None if missing/invalid."""
    custom = {u.get("key"): u.get("value") for u in (p.get("userDefined") or [])}
    try:
        lat = float(custom.get("lat"))
        lng = float(custom.get("lng"))
        return (lat, lng)
    except (TypeError, ValueError):
        return None


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lng) points in kilometers."""
    import math
    lat1, lng1 = a
    lat2, lng2 = b
    R = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _walk_all_contacts(
    person_fields: str = "names,emailAddresses,addresses,userDefined,memberships,organizations",
    only_groups: list[str] | None = None,
    max_contacts: int = 5000,
):
    """Generator yielding every saved contact (raw People API dicts).

    Honors `only_groups` membership filter. Pages through `connections.list`
    so it works on large address books.
    """
    svc = gservices.people()
    only_set = set(only_groups or []) or None
    page_token = None
    yielded = 0
    while yielded < max_contacts:
        kwargs: dict = {
            "resourceName": "people/me",
            "personFields": person_fields,
            "pageSize": 1000,
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = svc.people().connections().list(**kwargs).execute()
        for p in resp.get("connections", []) or []:
            if only_set:
                memb = {
                    (m.get("contactGroupMembership") or {}).get("contactGroupResourceName")
                    for m in (p.get("memberships") or [])
                }
                if not (memb & only_set):
                    continue
            yield p
            yielded += 1
            if yielded >= max_contacts:
                return
        page_token = resp.get("nextPageToken")
        if not page_token:
            return


def _set_contact_custom_fields(
    person_resource_name: str, etag: str, existing_userdefined: list[dict],
    updates: dict[str, str],
) -> str:
    """Merge `updates` into a contact's userDefined fields and PATCH via People API.

    Returns the new etag.
    """
    svc = gservices.people()
    by_key = {u.get("key"): dict(u) for u in (existing_userdefined or [])}
    for k, v in updates.items():
        if v is None:
            by_key.pop(k, None)
        else:
            by_key[k] = {"key": k, "value": str(v)}
    body = {"etag": etag, "userDefined": list(by_key.values())}
    resp = svc.people().updateContact(
        resourceName=person_resource_name,
        updatePersonFields="userDefined",
        body=body,
    ).execute()
    return resp.get("etag", etag)


def _resolve_to_address(stop: str) -> tuple[str, dict | None]:
    """Resolve a 'stop' (free-form address, lat,lng, or contact resource_name) to an address.

    Returns (address_string, contact_record_or_None).
    """
    if stop.startswith("people/"):
        svc = gservices.people()
        person = svc.people().get(
            resourceName=stop,
            personFields="names,addresses,emailAddresses,userDefined,organizations",
        ).execute()
        block = _extract_address_block(person)
        formatted = block.get("formatted")
        if not formatted:
            raise ValueError(f"Contact {stop} has no address on record.")
        return formatted, person
    return stop, None


def _build_simple_email(
    to: list[str], subject: str, body: str, *, cc: list[str] | None = None,
    attachment: tuple[bytes, str, str] | None = None, from_alias: str | None = None,
) -> dict:
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    if from_alias:
        msg["From"] = from_alias
    msg.set_content(body)
    if attachment is not None:
        data, filename, mime = attachment
        maintype, subtype = mime.split("/", 1) if "/" in mime else ("application", "octet-stream")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": raw}


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_save_email_attachments_to_drive",
        annotations={
            "title": "Save Gmail attachments to Drive",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_save_email_attachments_to_drive(
        params: SaveAttachmentsToDriveInput,
    ) -> str:
        """For a given Gmail message, upload each matching attachment to Drive.

        Returns a list describing each attachment and whether the upload succeeded.
        Partial failures don't abort — every attachment is attempted independently.
        """
        try:
            gmail = _gmail()
            drive = _drive()
            msg = gmail.users().messages().get(userId="me", id=params.message_id, format="full").execute()
            atts = _list_attachments_meta(msg["payload"])
            if params.attachment_filter:
                needle = params.attachment_filter.lower()
                atts = [a for a in atts if needle in (a["filename"] or "").lower()]

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "workflow_save_email_attachments_to_drive",
                    {
                        "message_id": params.message_id,
                        "drive_folder_id": params.drive_folder_id,
                        "attachments": [a["filename"] for a in atts],
                    },
                )

            results = []
            for a in atts:
                try:
                    data_resp = (
                        gmail.users()
                        .messages()
                        .attachments()
                        .get(userId="me", messageId=params.message_id, id=a["attachment_id"])
                        .execute()
                    )
                    raw = base64.urlsafe_b64decode(data_resp["data"])
                    body: dict = {"name": a["filename"]}
                    if params.drive_folder_id:
                        body["parents"] = [params.drive_folder_id]
                    media = MediaInMemoryUpload(
                        raw, mimetype=a["mime_type"] or "application/octet-stream"
                    )
                    created = (
                        drive.files()
                        .create(body=body, media_body=media, fields="id, name, webViewLink")
                        .execute()
                    )
                    results.append({"filename": a["filename"], "status": "uploaded", **created})
                except Exception as inner:
                    results.append({"filename": a["filename"], "status": "failed", "error": str(inner)})
            return json.dumps({"count": len(results), "results": results}, indent=2)
        except Exception as e:
            log.error("save_email_attachments_to_drive failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_email_doc_as_pdf",
        annotations={
            "title": "Export a Google Doc as PDF and email it",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_email_doc_as_pdf(params: EmailDocAsPdfInput) -> str:
        """Export a Google Doc as PDF in-memory, then send it as an email attachment.

        No temp files on disk. Subject/filename default to the doc title.
        """
        try:
            drive = _drive()
            meta = drive.files().get(fileId=params.document_id, fields="name, mimeType").execute()
            if not meta["mimeType"].startswith("application/vnd.google-apps."):
                return f"Error: {params.document_id} is not a Google-native doc."

            buf = io.BytesIO()
            downloader = MediaIoBaseDownload(
                buf, drive.files().export_media(fileId=params.document_id, mimeType="application/pdf")
            )
            done = False
            while not done:
                _, done = downloader.next_chunk()
            pdf_bytes = buf.getvalue()

            subject = params.subject or meta["name"]
            filename = params.filename or f"{meta['name']}.pdf"

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "workflow_email_doc_as_pdf",
                    {
                        "document_id": params.document_id,
                        "to": params.to,
                        "subject": subject,
                        "attachment_filename": filename,
                        "pdf_size": len(pdf_bytes),
                    },
                )

            mime = _build_simple_email(
                to=params.to,
                subject=subject,
                body=params.body or "See attached.",
                cc=params.cc,
                attachment=(pdf_bytes, filename, "application/pdf"),
                from_alias=config.get("default_from_alias"),
            )
            sent = _gmail().users().messages().send(userId="me", body=mime).execute()
            log.info("email_doc_as_pdf sent doc=%s to=%s", params.document_id, params.to)
            return json.dumps(
                {
                    "status": "sent",
                    "message_id": sent.get("id"),
                    "pdf_size": len(pdf_bytes),
                    "attachment_filename": filename,
                },
                indent=2,
            )
        except Exception as e:
            log.error("email_doc_as_pdf failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_share_drive_file_via_email",
        annotations={
            "title": "Share Drive file and send the link via email",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_share_drive_file_via_email(params: ShareDriveFileViaEmailInput) -> str:
        """Grant the recipient access to a Drive file AND send them the link.

        Two steps in one call — because every time this is done manually, one
        of them gets forgotten.
        """
        try:
            drive = _drive()
            meta = drive.files().get(fileId=params.file_id, fields="name, webViewLink").execute()
            subject = params.subject or f"Shared: {meta['name']}"

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "workflow_share_drive_file_via_email",
                    {
                        "file_id": params.file_id,
                        "recipient": params.recipient_email,
                        "role": params.role,
                    },
                )

            drive.permissions().create(
                fileId=params.file_id,
                body={"type": "user", "role": params.role, "emailAddress": params.recipient_email},
                sendNotificationEmail=False,  # we'll send our own
                fields="id",
            ).execute()

            body_text = (
                (params.message + "\n\n" if params.message else "")
                + f"I've shared '{meta['name']}' with you:\n{meta['webViewLink']}"
            )
            mime = _build_simple_email(
                to=[params.recipient_email],
                subject=subject,
                body=body_text,
                from_alias=config.get("default_from_alias"),
            )
            sent = _gmail().users().messages().send(userId="me", body=mime).execute()
            log.info("share_drive_file_via_email %s to=%s", params.file_id, params.recipient_email)
            return json.dumps(
                {
                    "status": "shared_and_emailed",
                    "file": meta["name"],
                    "link": meta["webViewLink"],
                    "message_id": sent.get("id"),
                },
                indent=2,
            )
        except Exception as e:
            log.error("share_drive_file_via_email failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_email_thread_to_event",
        annotations={
            "title": "Create a calendar event from an email thread",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_email_thread_to_event(params: EmailThreadToEventInput) -> str:
        """Turn a Gmail thread into a calendar invite.

        Extracts attendees from the thread (From + all Tos/Ccs, minus yourself),
        uses the thread subject as the event title (unless overridden), and
        optionally pastes the thread text into the event description. Sends
        invites to all attendees by default.
        """
        try:
            gmail = _gmail()
            thread = gmail.users().threads().get(userId="me", id=params.thread_id, format="full").execute()
            msgs = thread.get("messages", [])
            if not msgs:
                return f"Thread {params.thread_id} has no messages."

            # Aggregate addresses across all messages.
            addresses: set[str] = set()
            my_email: str | None = None
            first_subject = ""
            for m in msgs:
                headers = {h["name"].lower(): h["value"] for h in m["payload"]["headers"]}
                if not first_subject:
                    first_subject = headers.get("subject", "")
                for key in ("from", "to", "cc"):
                    raw = headers.get(key, "")
                    for part in raw.split(","):
                        addr = _parse_email_address(part)
                        if addr:
                            addresses.add(addr)

            # Identify self to exclude from attendees.
            try:
                profile = gmail.users().getProfile(userId="me").execute()
                my_email = profile.get("emailAddress", "").lower()
            except Exception:
                my_email = None
            attendees = sorted(a for a in addresses if a.lower() != (my_email or ""))

            summary = params.summary or _strip_reply_prefixes(first_subject) or "Follow-up from email"
            description = ""
            if params.include_thread_body:
                parts = []
                for m in msgs:
                    headers = {h["name"]: h["value"] for h in m["payload"]["headers"]}
                    parts.append(
                        f"From: {headers.get('From', '')}\nDate: {headers.get('Date', '')}\n\n"
                        + _extract_plaintext(m["payload"])
                    )
                description = "\n\n---\n\n".join(parts)[:8000]  # Calendar description soft limit

            tz = params.timezone or config.get("default_timezone")
            body: dict = {
                "summary": summary,
                "description": description,
                "start": {"dateTime": params.start, **({"timeZone": tz} if tz else {})},
                "end": {"dateTime": params.end, **({"timeZone": tz} if tz else {})},
                "attendees": [{"email": a} for a in attendees],
            }
            if params.add_meet:
                import uuid
                body["conferenceData"] = {
                    "createRequest": {
                        "requestId": str(uuid.uuid4()),
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                }

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "workflow_email_thread_to_event",
                    {"summary": summary, "attendees": attendees, "start": params.start, "end": params.end},
                )

            created = (
                _calendar_svc()
                .events()
                .insert(
                    calendarId=config.get("default_calendar_id", "primary"),
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
                    "attendees": attendees,
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
            log.error("email_thread_to_event failed: %s", e)
            return format_error(e)

    # --- Mail merge ----------------------------------------------------------

    @mcp.tool(
        name="gmail_send_templated",
        annotations={
            "title": "Send a templated email to one contact (with dynamic fields)",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def gmail_send_templated(params: SendTemplatedInput) -> str:
        """Send a single email with {placeholders} substituted from a contact.

        Recipient can be given by People API resource_name (looked up live) or
        by inline fields (email + first_name + ...). Supported placeholders:
        first_name, last_name, full_name, email, organization, title, and any
        key from the contact's userDefined fields (as `custom.<key>` or just
        `<key>` if not shadowed).

        Template syntax: `{field}` or `{field|fallback}`. Example:
            "Hi {first_name|there}, hope the {organization} team is doing well."

        If you want to preview without sending, set dry_run=true.
        """
        try:
            fields = _resolve_recipient(params.recipient)
            if not fields.get("email"):
                return "Error: recipient has no email address."

            subject = rendering.render(params.subject, fields, params.default_fallback)
            body = rendering.render(params.body, fields, params.default_fallback)
            html = (
                rendering.render(params.html_body, fields, params.default_fallback)
                if params.html_body
                else None
            )

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "gmail_send_templated",
                    {
                        "to": fields["email"],
                        "subject": subject,
                        "body_preview": body[:400],
                        "fields_resolved": {k: v for k, v in fields.items() if k not in ("etag",)},
                    },
                )

            mime_msg = EmailMessage()
            mime_msg["To"] = fields["email"]
            if params.cc:
                mime_msg["Cc"] = ", ".join(params.cc)
            if params.bcc:
                mime_msg["Bcc"] = ", ".join(params.bcc)
            mime_msg["Subject"] = subject
            from_alias = config.get("default_from_alias")
            if from_alias:
                mime_msg["From"] = from_alias
            if html:
                mime_msg.set_content(body)
                mime_msg.add_alternative(html, subtype="html")
            else:
                mime_msg.set_content(body)
            raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")
            sent = _gmail().users().messages().send(userId="me", body={"raw": raw}).execute()
            log.info("gmail_send_templated to=%s subject=%s", fields["email"], subject)

            # Activity log on contact (if enabled).
            if config.get("log_sent_emails_to_contacts", True):
                _log_activity_on_contact(fields["email"], subject)

            return json.dumps(
                {
                    "status": "sent",
                    "to": fields["email"],
                    "subject": subject,
                    "message_id": sent.get("id"),
                    "thread_id": sent.get("threadId"),
                },
                indent=2,
            )
        except Exception as e:
            log.error("gmail_send_templated failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="gmail_send_mail_merge",
        annotations={
            "title": "Send a templated email to many contacts (mail merge)",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def gmail_send_mail_merge(params: SendMailMergeInput) -> str:
        """Send the same templated email to many recipients, personalized per contact.

        Specify recipients in ONE of two ways:
            1. `recipients` — list of RecipientInput (inline or by resource_name)
            2. `group_resource_name` — a contact group; all members get the mail

        Behavior on failure: by default, the batch continues on individual
        errors and returns a per-recipient status list. Pass
        `stop_on_first_error=true` to abort on the first failure.

        Dry-run is highly recommended before a real send — returns the rendered
        subject/body for each recipient so you can verify personalization.
        """
        try:
            if bool(params.recipients) == bool(params.group_resource_name):
                return "Error: provide exactly one of `recipients` OR `group_resource_name`."

            # Resolve recipient list to flat field dicts.
            resolved: list[dict] = []
            if params.recipients:
                for r in params.recipients:
                    resolved.append(_resolve_recipient(r))
            else:
                # Fetch group members.
                people = gservices.people()
                grp = (
                    people.contactGroups()
                    .get(resourceName=params.group_resource_name, maxMembers=500)
                    .execute()
                )
                member_names = grp.get("memberResourceNames", []) or []
                if not member_names:
                    return f"Group {params.group_resource_name} has no members."
                batch = (
                    people.people()
                    .getBatchGet(
                        resourceNames=member_names,
                        personFields="names,emailAddresses,organizations,userDefined,metadata",
                    )
                    .execute()
                )
                for r in batch.get("responses", []):
                    p = r.get("person")
                    if p:
                        resolved.append(_flatten_person(p))

            # Render per-recipient.
            prepared = []
            for fields in resolved:
                if not fields.get("email"):
                    prepared.append({"fields": fields, "skip_reason": "no_email"})
                    continue
                subj = rendering.render(params.subject, fields, params.default_fallback)
                body = rendering.render(params.body, fields, params.default_fallback)
                html = (
                    rendering.render(params.html_body, fields, params.default_fallback)
                    if params.html_body
                    else None
                )
                prepared.append(
                    {"fields": fields, "subject": subj, "body": body, "html": html}
                )

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "gmail_send_mail_merge",
                    {
                        "recipient_count": len([p for p in prepared if "subject" in p]),
                        "skipped": [
                            p["fields"].get("resource_name") or p["fields"].get("email")
                            for p in prepared if "skip_reason" in p
                        ],
                        "previews": [
                            {
                                "to": p["fields"]["email"],
                                "subject": p["subject"],
                                "body_preview": p["body"][:200],
                            }
                            for p in prepared if "subject" in p
                        ][:10],  # cap to keep context manageable
                    },
                )

            # Send.
            gmail = _gmail()
            from_alias = config.get("default_from_alias")
            results = []
            for p in prepared:
                if "skip_reason" in p:
                    results.append(
                        {
                            "to": p["fields"].get("email"),
                            "resource_name": p["fields"].get("resource_name"),
                            "status": "skipped",
                            "reason": p["skip_reason"],
                        }
                    )
                    continue
                try:
                    mime_msg = EmailMessage()
                    mime_msg["To"] = p["fields"]["email"]
                    mime_msg["Subject"] = p["subject"]
                    if from_alias:
                        mime_msg["From"] = from_alias
                    if p["html"]:
                        mime_msg.set_content(p["body"])
                        mime_msg.add_alternative(p["html"], subtype="html")
                    else:
                        mime_msg.set_content(p["body"])
                    raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")
                    sent = gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
                    if config.get("log_sent_emails_to_contacts", True):
                        _log_activity_on_contact(p["fields"]["email"], p["subject"])
                    results.append(
                        {
                            "to": p["fields"]["email"],
                            "status": "sent",
                            "message_id": sent.get("id"),
                        }
                    )
                except Exception as inner:
                    log.error("mail_merge partial failure: %s", inner)
                    results.append(
                        {
                            "to": p["fields"].get("email"),
                            "status": "failed",
                            "error": str(inner),
                        }
                    )
                    if params.stop_on_first_error:
                        break
            sent_count = sum(1 for r in results if r["status"] == "sent")
            failed_count = sum(1 for r in results if r["status"] == "failed")
            return json.dumps(
                {
                    "total": len(results),
                    "sent": sent_count,
                    "failed": failed_count,
                    "results": results,
                },
                indent=2,
            )
        except Exception as e:
            log.error("gmail_send_mail_merge failed: %s", e)
            return format_error(e)

    # --- Template library ----------------------------------------------------

    @mcp.tool(
        name="gmail_list_templates",
        annotations={
            "title": "List saved email templates",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def gmail_list_templates(params: ListTemplatesInput) -> str:
        """List every saved template (files in templates/*.md)."""
        try:
            return json.dumps(templates_mod.list_templates(), indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_get_template",
        annotations={
            "title": "Get a saved email template",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def gmail_get_template(params: GetTemplateInput) -> str:
        """Return a saved template's subject, body, and HTML body (if any)."""
        try:
            tpl = templates_mod.load(params.name)
            return json.dumps(
                {
                    "name": tpl.name,
                    "subject": tpl.subject,
                    "body": tpl.body,
                    "html_body": tpl.html_body,
                    "description": tpl.description,
                },
                indent=2,
            )
        except templates_mod.TemplateError as e:
            return f"Error: {e}"
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="workflow_seed_response_templates",
        annotations={
            "title": "Seed 8 brand-voice response templates as HTML",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def workflow_seed_response_templates(
        params: SeedResponseTemplatesInput,
    ) -> str:
        """Generate 8 reusable response templates in your brand voice.

        Reads `brand-voice.md` for tone, then drafts 8 HTML email templates
        via Claude Haiku and writes them to `templates/`:

          - customer_complaint_response
          - upset_client_response
          - thanks_for_reply
          - great_to_meet_you
          - client_feedback_response
          - welcome_response
          - renewal_reminder_response
          - followup_response

        Existing templates are PRESERVED unless `force=true`. Each generated
        template carries an `html_body` plus a plain-text body so the
        send-templated tools can pick whichever the recipient supports.

        Cost: ~$0.02 for the full set (8 Haiku calls). Free first run, since
        you can re-run with `force=true` later if you want a fresh draft
        after updating brand-voice.md.
        """
        try:
            # Re-use the script's logic without shelling out so the MCP can
            # produce a structured JSON result.
            import sys as _sys
            scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
            if scripts_dir not in _sys.path:
                _sys.path.insert(0, scripts_dir)
            import seed_response_templates as _seed

            import llm
            ok, reason = llm.is_available()
            if not ok:
                return json.dumps({"status": "no_llm", "reason": reason}, indent=2)

            brand_voice = _seed._read_brand_voice()
            templates_dir = Path(_seed._PROJECT) / "templates"
            templates_dir.mkdir(exist_ok=True)

            cats = _seed.CATEGORIES
            if params.only:
                cats = [c for c in _seed.CATEGORIES if c["slug"] == params.only]
                if not cats:
                    return json.dumps({
                        "status": "unknown_slug",
                        "requested": params.only,
                        "available": [c["slug"] for c in _seed.CATEGORIES],
                    }, indent=2)

            results: list[dict] = []
            created = skipped = failed = 0
            for category in cats:
                slug = category["slug"]
                path = templates_dir / f"{slug}.md"
                if path.exists() and not params.force:
                    skipped += 1
                    results.append({"slug": slug, "status": "skipped",
                                    "reason": "already_exists"})
                    continue
                try:
                    generated = _seed._generate_one(category, brand_voice)
                    body = _seed._render_template_file(category, generated)
                    path.write_text(body, encoding="utf-8")
                    created += 1
                    results.append({
                        "slug": slug, "status": "created",
                        "subject": generated.get("subject"),
                        "description": generated.get("description"),
                    })
                except Exception as e:
                    log.warning("seed template %s failed: %s", slug, e)
                    failed += 1
                    results.append({"slug": slug, "status": "failed",
                                    "error": str(e)})
            return json.dumps({
                "status": "ok" if failed == 0 else "partial",
                "summary": {
                    "created": created,
                    "skipped": skipped,
                    "failed": failed,
                    "total_evaluated": len(cats),
                },
                "templates": results,
                "estimated_cost_usd": round(created * 0.0025, 4),
                "hint": (
                    "Templates land in templates/*.md. Use "
                    "gmail_get_template to inspect, gmail_send_templated_by_name "
                    "to send. Existing files were preserved (pass force=true to "
                    "regenerate)."
                ),
            }, indent=2)
        except Exception as e:
            log.error("workflow_seed_response_templates failed: %s", e)
            return format_error(e)

    # --- Receipt + brand-voice composed workflows --------------------------- #

    @mcp.tool(
        name="workflow_receipt_chat_digest",
        annotations={
            "title": "Post a deduplicated expense digest to a Gchat space",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,  # creates a chat message each call
            "openWorldHint": False,
        },
    )
    async def workflow_receipt_chat_digest(
        params: ReceiptChatDigestInput,
    ) -> str:
        """Read receipts from a sheet, dedupe by content_key, post a formatted
        digest to a Gchat space. Stamps the BOT_FOOTER_MARKER so re-scans
        won't re-extract the digest as a receipt.
        """
        try:
            import datetime as _dt
            import receipts as _r
            from tools.receipts import (
                _resolve_sheet, _existing_sheet_content_keys,
            )
            sheets = gservices.sheets()
            chat = gservices.chat()

            sheet_id, _title, err = _resolve_sheet(
                params.sheet_id, params.sheet_name,
            )
            if err is not None:
                return json.dumps(err, indent=2)

            # Read all rows; filter by date in window.
            resp = sheets.spreadsheets().values().get(
                spreadsheetId=sheet_id, range="A2:Q",
            ).execute()
            rows = resp.get("values", []) or []
            cutoff = (
                _dt.date.today() - _dt.timedelta(days=params.days)
            ).isoformat()

            # Dedupe by content_key. Carry first-seen row only.
            seen: set[str] = set()
            unique_rows: list[dict] = []
            grand_total = 0.0
            by_category: dict[str, dict] = {}
            for row in rows:
                row = row + [""] * (17 - len(row))
                date = row[1] or ""
                merchant = row[2] or ""
                total_str = row[3] or ""
                category = row[5] or "Miscellaneous Expense"
                last_4 = row[10] or ""
                if not merchant or not total_str:
                    continue
                if date and date < cutoff:
                    continue
                try:
                    total = float(total_str)
                except ValueError:
                    continue
                key = _r.content_key(merchant, date, total, last_4)
                if not key or key in seen:
                    continue
                seen.add(key)
                unique_rows.append({
                    "date": date or "(no date)",
                    "merchant": merchant,
                    "total": total,
                    "category": category,
                })
                grand_total += total
                bucket = by_category.setdefault(
                    category, {"total": 0.0, "count": 0},
                )
                bucket["total"] += total
                bucket["count"] += 1

            unique_rows.sort(key=lambda r: r["date"], reverse=True)

            # Build the digest text (Gchat markdown).
            lines = [
                "*📊 Receipt Digest*\n",
                f"_Window: last {params.days} days_",
                f"_Source: {_title}_\n",
                "*📈 Summary*",
                f"• Unique purchases: *{len(unique_rows)}*",
                f"• Grand total: *${grand_total:,.2f} USD*\n",
                "*🏷️ By Category*",
            ]
            for cat, info in sorted(by_category.items(),
                                    key=lambda x: -x[1]["total"]):
                lines.append(
                    f"• {cat}: *${info['total']:,.2f}* ({info['count']} receipts)"
                )
            lines.append("\n*📋 Detail*")
            lines.append("```")
            for r in unique_rows[:20]:  # top 20 most recent
                merchant = (r["merchant"] or "")[:24]
                lines.append(
                    f"{r['date']:<12}{merchant:<26}${r['total']:>10,.2f}  {r['category']}"
                )
            if len(unique_rows) > 20:
                lines.append(f"  …and {len(unique_rows) - 20} more")
            lines.append("```\n")
            lines.append(f"_— {_r.BOT_FOOTER_MARKER}_")
            text = "\n".join(lines)

            if is_dry_run(params.dry_run):
                return dry_run_preview("workflow_receipt_chat_digest", {
                    "would_post_to": params.chat_space_id,
                    "preview": text,
                    "stats": {
                        "unique_purchases": len(unique_rows),
                        "grand_total": round(grand_total, 2),
                        "categories": len(by_category),
                    },
                })

            sent = chat.spaces().messages().create(
                parent=params.chat_space_id,
                body={"text": text},
            ).execute()
            return json.dumps({
                "status": "sent",
                "message_name": sent.get("name"),
                "stats": {
                    "unique_purchases": len(unique_rows),
                    "grand_total": round(grand_total, 2),
                    "categories": len(by_category),
                },
            }, indent=2)
        except Exception as e:
            log.error("workflow_receipt_chat_digest failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_monthly_expense_report",
        annotations={
            "title": "Build month's QB CSV + email it to a recipient",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def workflow_monthly_expense_report(
        params: MonthlyExpenseReportInput,
    ) -> str:
        """End-to-end month-end close: filter receipts by month, export QB
        CSV to Drive, email recipient with the link + summary."""
        try:
            import datetime as _dt
            from tools.receipts import (
                _resolve_sheet, _ensure_drive_folder, _archive_pdf_to_drive,
            )
            import receipts as _r
            import csv
            import io as _io

            sheet_id, sheet_title, err = _resolve_sheet(
                params.sheet_id, params.sheet_name,
            )
            if err is not None:
                return json.dumps(err, indent=2)

            archive_folder_id = _ensure_drive_folder(
                params.drive_folder_id
                or config.get("receipts_drive_folder_id"),
                default_name="CoAssisted Receipts",
            )

            sheets = gservices.sheets()
            resp = sheets.spreadsheets().values().get(
                spreadsheetId=sheet_id, range="A:Q",
            ).execute()
            data_rows = resp.get("values", []) or []
            if len(data_rows) < 2:
                return json.dumps({"status": "empty_sheet"}, indent=2)
            header, body_rows = data_rows[0], data_rows[1:]

            # Filter to the requested month (YYYY-MM)
            month_prefix = params.month + "-"
            account_map = config.get("receipts_qb_account_map") or None
            buf = _io.StringIO()
            w = csv.writer(buf)
            w.writerow(_r.QB_CSV_COLUMNS)
            included = 0
            grand_total = 0.0
            by_category: dict[str, float] = {}
            for r in body_rows:
                r = r + [""] * (len(header) - len(r))
                row_dict = dict(zip(header, r))
                date = row_dict.get("date") or ""
                if not date.startswith(month_prefix):
                    continue
                try:
                    total = float(row_dict.get("total") or 0)
                except ValueError:
                    total = 0.0
                rec = _r.ExtractedReceipt(
                    date=date or None,
                    merchant=row_dict.get("merchant") or None,
                    total=total if total else None,
                    currency=row_dict.get("currency") or "USD",
                    category=row_dict.get("category") or "Miscellaneous Expense",
                    location=row_dict.get("location") or None,
                    notes=row_dict.get("notes") or None,
                    source_kind=row_dict.get("source_kind") or "",
                    source_id=row_dict.get("source_id") or None,
                )
                w.writerow(_r.receipt_to_qb_row(rec, account_map=account_map))
                included += 1
                grand_total += float(rec.total or 0)
                by_category[rec.category] = (
                    by_category.get(rec.category, 0) + float(rec.total or 0)
                )
            csv_bytes = buf.getvalue().encode("utf-8")

            if is_dry_run(params.dry_run):
                return dry_run_preview("workflow_monthly_expense_report", {
                    "month": params.month,
                    "rows": included,
                    "grand_total": round(grand_total, 2),
                    "by_category": {k: round(v, 2) for k, v in by_category.items()},
                    "would_email": params.recipient_email,
                })

            # Upload CSV to Drive
            drive_link = _archive_pdf_to_drive(
                gservices.drive(), archive_folder_id,
                f"qb_export_{params.month}.csv",
                csv_bytes, "text/csv",
            )

            # Compose + send email
            from email.message import EmailMessage
            cat_lines = "\n".join(
                f"  • {k}: ${v:,.2f}"
                for k, v in sorted(by_category.items(), key=lambda x: -x[1])
            )
            body = (
                f"Hi,\n\n"
                f"Monthly expense report for {params.month} attached as a "
                f"QuickBooks-importable CSV.\n\n"
                f"Summary:\n"
                f"  • Receipts: {included}\n"
                f"  • Grand total: ${grand_total:,.2f}\n"
                f"  • By category:\n{cat_lines}\n\n"
                f"CSV download (Drive): {drive_link}\n\n"
                f"— sent by CoAssisted Workspace receipt extractor"
            )
            msg = EmailMessage()
            msg["To"] = params.recipient_email
            msg["Subject"] = f"Expense report — {params.month}"
            msg.set_content(body)
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
            sent = gservices.gmail().users().messages().send(
                userId="me", body={"raw": raw},
            ).execute()

            return json.dumps({
                "status": "ok",
                "month": params.month,
                "rows_included": included,
                "grand_total": round(grand_total, 2),
                "by_category": {k: round(v, 2) for k, v in by_category.items()},
                "csv_drive_link": drive_link,
                "email_message_id": sent.get("id"),
                "recipient": params.recipient_email,
            }, indent=2)
        except Exception as e:
            log.error("workflow_monthly_expense_report failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_suggest_response_template",
        annotations={
            "title": "Pick the right inbound_* template for an email + draft a reply",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def workflow_suggest_response_template(
        params: SuggestResponseTemplateInput,
    ) -> str:
        """Classify an inbound email and recommend the closest inbound_*
        template. Returns the slug + a draft body with placeholders resolved
        from the sender (when message_id is provided)."""
        try:
            import llm as _llm
            ok, why = _llm.is_available()
            if not ok:
                return json.dumps({"status": "no_llm", "reason": why}, indent=2)

            # Resolve content
            subject = ""
            sender = ""
            body_text = params.text or ""
            if params.message_id and not body_text:
                full = gservices.gmail().users().messages().get(
                    userId="me", id=params.message_id, format="full",
                ).execute()
                hdrs = {h["name"].lower(): h["value"]
                        for h in (full.get("payload", {}).get("headers") or [])}
                subject = hdrs.get("subject", "")
                sender = hdrs.get("from", "")
                body_text = full.get("snippet", "")
            if not body_text and not params.text:
                return json.dumps({
                    "status": "no_content",
                    "hint": "Pass message_id or text.",
                }, indent=2)

            # List candidate inbound_* templates
            all_templates = templates_mod.list_templates()
            candidates = [
                t for t in all_templates
                if t.get("name", "").startswith("inbound_")
            ]
            if not candidates:
                return json.dumps({
                    "status": "no_templates",
                    "hint": (
                        "No inbound_* templates exist yet. Run "
                        "workflow_seed_response_templates first."
                    ),
                }, indent=2)

            cand_list = "\n".join(
                f"- {c['name']}: {c.get('description', '(no description)')}"
                for c in candidates
            )
            prompt = (
                "An inbound email needs a reply. Pick the SINGLE closest "
                "template from the candidates below. Return ONLY a JSON "
                "object — no prose:\n\n"
                "{\n"
                '  "template_name": "<exact slug from candidates>",\n'
                '  "confidence": <0.0-1.0>,\n'
                '  "reasoning": "<one sentence>"\n'
                "}\n\n"
                f"Candidates:\n{cand_list}\n\n"
                f"Inbound message:\nSubject: {subject}\nFrom: {sender}\n"
                f"Body: {body_text[:2000]}"
            )
            resp = _llm.call_simple(prompt, model="claude-haiku-4-5", max_tokens=300)
            try:
                pick = json.loads(
                    resp["text"].strip().strip("`").lstrip("json").strip()
                )
            except Exception:
                pick = {"template_name": candidates[0]["name"],
                        "confidence": 0.0,
                        "reasoning": f"LLM returned unparseable JSON: {resp['text'][:120]}"}

            chosen = pick.get("template_name", "")
            if chosen not in {c["name"] for c in candidates}:
                chosen = candidates[0]["name"]
                pick["confidence"] = 0.0
                pick["reasoning"] = (
                    f"LLM picked unknown slug; defaulted to {chosen}"
                )

            try:
                tpl = templates_mod.load(chosen)
            except templates_mod.TemplateError as e:
                return json.dumps({
                    "status": "template_load_failed",
                    "template_name": chosen,
                    "error": str(e),
                }, indent=2)

            return json.dumps({
                "status": "ok",
                "suggested_template": chosen,
                "confidence": pick.get("confidence"),
                "reasoning": pick.get("reasoning"),
                "draft": {
                    "subject": tpl.subject,
                    "body": tpl.body,
                    "html_body": tpl.html_body,
                },
                "sender": sender,
                "candidates_considered": [c["name"] for c in candidates],
            }, indent=2)
        except Exception as e:
            log.error("workflow_suggest_response_template failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_smart_followup_finder",
        annotations={
            "title": "Find stale unresponded threads + suggest follow-up templates",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def workflow_smart_followup_finder(
        params: SmartFollowupFinderInput,
    ) -> str:
        """Sweep the inbox for threads where the last message is from an
        external contact and you haven't replied in `days_stale` days.
        Returns a list of candidates each paired with a suggested
        inbound_followup_response template draft. Optionally creates Gmail
        drafts (`create_drafts=true`)."""
        try:
            import datetime as _dt
            gmail = gservices.gmail()
            # Get our own email so we can detect 'external' senders
            try:
                profile = gmail.users().getProfile(userId="me").execute()
                my_email = (profile.get("emailAddress") or "").lower()
                my_domain = my_email.split("@")[-1] if "@" in my_email else ""
            except Exception:
                my_email = ""
                my_domain = ""

            # Gmail query: unread or unreplied threads older than N days
            since = (
                _dt.date.today() - _dt.timedelta(days=params.days_stale)
            ).strftime("%Y/%m/%d")
            q = f"-from:me older_than:{params.days_stale}d"
            search = gmail.users().messages().list(
                userId="me", q=q, maxResults=params.max_threads,
            ).execute()
            msgs = search.get("messages", []) or []

            candidates: list[dict] = []
            seen_threads: set[str] = set()
            for m in msgs[:params.max_threads]:
                full = gmail.users().messages().get(
                    userId="me", id=m["id"], format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ).execute()
                tid = full.get("threadId")
                if tid in seen_threads:
                    continue
                seen_threads.add(tid)
                hdrs = {h["name"].lower(): h["value"]
                        for h in (full.get("payload", {}).get("headers") or [])}
                sender = hdrs.get("from", "")
                # External check
                if params.only_external and my_domain:
                    sender_l = sender.lower()
                    if my_domain in sender_l:
                        continue
                candidates.append({
                    "thread_id": tid,
                    "message_id": m["id"],
                    "subject": hdrs.get("subject", ""),
                    "from": sender,
                    "date": hdrs.get("date", ""),
                    "snippet": full.get("snippet", ""),
                    "suggested_template": "inbound_followup_response",
                })

            # Optionally create drafts
            drafts_created = 0
            if params.create_drafts and candidates:
                try:
                    tpl = templates_mod.load("inbound_followup_response")
                except templates_mod.TemplateError:
                    tpl = None
                if tpl:
                    from email.message import EmailMessage
                    for c in candidates[:10]:  # cap at 10 drafts
                        try:
                            msg = EmailMessage()
                            msg["To"] = c["from"]
                            msg["Subject"] = f"Re: {c['subject']}"
                            msg.set_content(tpl.body)
                            raw = base64.urlsafe_b64encode(
                                msg.as_bytes()
                            ).decode("ascii")
                            gmail.users().drafts().create(
                                userId="me",
                                body={"message": {
                                    "raw": raw, "threadId": c["thread_id"],
                                }},
                            ).execute()
                            drafts_created += 1
                        except Exception as e:
                            log.warning("draft creation failed: %s", e)

            return json.dumps({
                "status": "ok",
                "candidates": candidates,
                "count": len(candidates),
                "drafts_created": drafts_created,
                "hint": (
                    "Pass create_drafts=true to auto-draft replies. The "
                    "inbound_followup_response template fills with placeholders "
                    "you can edit before sending."
                ),
            }, indent=2)
        except Exception as e:
            log.error("workflow_smart_followup_finder failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="gmail_send_templated_by_name",
        annotations={
            "title": "Send a saved template to one contact",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def gmail_send_templated_by_name(params: SendTemplatedByNameInput) -> str:
        """Render a saved template against a contact and send it.

        The template's subject, body, and html_body are rendered with
        {placeholder} substitution. Activity is logged on the contact
        (unless disabled via log_to_contact=false or config).
        """
        try:
            tpl = templates_mod.load(params.template_name)
            fields = _resolve_recipient(params.recipient)
            if not fields.get("email"):
                return "Error: recipient has no email address."

            subject = rendering.render(tpl.subject, fields, params.default_fallback)
            body = rendering.render(tpl.body, fields, params.default_fallback)
            html = (
                rendering.render(tpl.html_body, fields, params.default_fallback)
                if tpl.html_body
                else None
            )

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "gmail_send_templated_by_name",
                    {
                        "template": tpl.name,
                        "to": fields["email"],
                        "subject": subject,
                        "body_preview": body[:400],
                    },
                )

            mime_msg = EmailMessage()
            mime_msg["To"] = fields["email"]
            if params.cc:
                mime_msg["Cc"] = ", ".join(params.cc)
            if params.bcc:
                mime_msg["Bcc"] = ", ".join(params.bcc)
            mime_msg["Subject"] = subject
            from_alias = config.get("default_from_alias")
            if from_alias:
                mime_msg["From"] = from_alias
            if html:
                mime_msg.set_content(body)
                mime_msg.add_alternative(html, subtype="html")
            else:
                mime_msg.set_content(body)
            raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")
            sent = _gmail().users().messages().send(userId="me", body={"raw": raw}).execute()
            log.info("gmail_send_templated_by_name template=%s to=%s", tpl.name, fields["email"])

            log_flag = params.log_to_contact
            if log_flag is None:
                log_flag = config.get("log_sent_emails_to_contacts", True)
            if log_flag:
                _log_activity_on_contact(fields["email"], subject, template_name=tpl.name)

            return json.dumps(
                {
                    "status": "sent",
                    "template": tpl.name,
                    "to": fields["email"],
                    "subject": subject,
                    "message_id": sent.get("id"),
                    "thread_id": sent.get("threadId"),
                },
                indent=2,
            )
        except templates_mod.TemplateError as e:
            return f"Error: {e}"
        except Exception as e:
            log.error("gmail_send_templated_by_name failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="gmail_send_mail_merge_by_name",
        annotations={
            "title": "Send a saved template to many contacts (mail merge)",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def gmail_send_mail_merge_by_name(params: SendMailMergeByNameInput) -> str:
        """Batch-send a saved template to recipients or a contact group.

        Template syntax, fallback behavior, and partial-failure handling all
        match gmail_send_mail_merge. Activity logging respects
        log_to_contact / config.log_sent_emails_to_contacts.
        """
        try:
            tpl = templates_mod.load(params.template_name)
            if bool(params.recipients) == bool(params.group_resource_name):
                return "Error: provide exactly one of `recipients` OR `group_resource_name`."

            # Resolve recipients — same logic as gmail_send_mail_merge.
            resolved: list[dict] = []
            if params.recipients:
                for r in params.recipients:
                    resolved.append(_resolve_recipient(r))
            else:
                people = gservices.people()
                grp = (
                    people.contactGroups()
                    .get(resourceName=params.group_resource_name, maxMembers=500)
                    .execute()
                )
                member_names = grp.get("memberResourceNames", []) or []
                if not member_names:
                    return f"Group {params.group_resource_name} has no members."
                batch = (
                    people.people()
                    .getBatchGet(
                        resourceNames=member_names,
                        personFields="names,emailAddresses,organizations,userDefined,metadata",
                    )
                    .execute()
                )
                for r in batch.get("responses", []):
                    p = r.get("person")
                    if p:
                        resolved.append(_flatten_person(p))

            prepared = []
            for fields in resolved:
                if not fields.get("email"):
                    prepared.append({"fields": fields, "skip_reason": "no_email"})
                    continue
                prepared.append({
                    "fields": fields,
                    "subject": rendering.render(tpl.subject, fields, params.default_fallback),
                    "body":    rendering.render(tpl.body, fields, params.default_fallback),
                    "html":    rendering.render(tpl.html_body, fields, params.default_fallback) if tpl.html_body else None,
                })

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "gmail_send_mail_merge_by_name",
                    {
                        "template": tpl.name,
                        "recipient_count": len([p for p in prepared if "subject" in p]),
                        "skipped": [
                            p["fields"].get("resource_name") or p["fields"].get("email")
                            for p in prepared if "skip_reason" in p
                        ],
                        "previews": [
                            {"to": p["fields"]["email"], "subject": p["subject"], "body_preview": p["body"][:200]}
                            for p in prepared if "subject" in p
                        ][:10],
                    },
                )

            log_flag = params.log_to_contact
            if log_flag is None:
                log_flag = config.get("log_sent_emails_to_contacts", True)

            gmail = _gmail()
            from_alias = config.get("default_from_alias")
            results = []
            for p in prepared:
                if "skip_reason" in p:
                    results.append({
                        "to": p["fields"].get("email"),
                        "resource_name": p["fields"].get("resource_name"),
                        "status": "skipped",
                        "reason": p["skip_reason"],
                    })
                    continue
                try:
                    mime_msg = EmailMessage()
                    mime_msg["To"] = p["fields"]["email"]
                    mime_msg["Subject"] = p["subject"]
                    if from_alias:
                        mime_msg["From"] = from_alias
                    if p["html"]:
                        mime_msg.set_content(p["body"])
                        mime_msg.add_alternative(p["html"], subtype="html")
                    else:
                        mime_msg.set_content(p["body"])
                    raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")
                    sent = gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
                    if log_flag:
                        _log_activity_on_contact(p["fields"]["email"], p["subject"], template_name=tpl.name)
                    results.append({
                        "to": p["fields"]["email"],
                        "status": "sent",
                        "message_id": sent.get("id"),
                    })
                except Exception as inner:
                    log.error("mail_merge_by_name partial failure: %s", inner)
                    results.append({
                        "to": p["fields"].get("email"),
                        "status": "failed",
                        "error": str(inner),
                    })
                    if params.stop_on_first_error:
                        break
            return json.dumps(
                {
                    "template": tpl.name,
                    "total": len(results),
                    "sent": sum(1 for r in results if r["status"] == "sent"),
                    "failed": sum(1 for r in results if r["status"] == "failed"),
                    "results": results,
                },
                indent=2,
            )
        except templates_mod.TemplateError as e:
            return f"Error: {e}"
        except Exception as e:
            log.error("gmail_send_mail_merge_by_name failed: %s", e)
            return format_error(e)

    # --- Handoff ------------------------------------------------------------

    @mcp.tool(
        name="workflow_send_handoff_archive",
        annotations={
            "title": "Send the handoff archive to a coworker",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_send_handoff_archive(params: SendHandoffArchiveInput) -> str:
        """Send the Google Workspace MCP handoff archive to one or more coworkers.

        The full flow in one call:
            1. Locate the tarball — either `archive_path` or the newest file
               matching dist/google-workspace-mcp-*.tar.gz in the project folder.
            2. Upload it to Drive.
            3. Share it with every recipient as 'reader' (no Drive notification
               email — we send a single, deliberate email ourselves).
            4. Send an email with a friendly default body that explains what's
               inside, what they do next, and the Drive link.

        Idempotent-ish: re-running uploads a new copy to Drive each time. Run
        `make handoff` first to rebuild the tarball if you changed code.
        """
        try:
            from pathlib import Path as _Path
            import glob as _glob

            project_dir = _Path(__file__).resolve().parent.parent

            # 1. Resolve the archive path.
            if params.archive_path:
                archive = _Path(params.archive_path).expanduser()
            else:
                candidates = sorted(
                    _glob.glob(str(project_dir / "dist" / "google-workspace-mcp-*.tar.gz")),
                    key=lambda p: _Path(p).stat().st_mtime,
                    reverse=True,
                )
                if not candidates:
                    return (
                        "Error: no archive found in dist/. Run 'make handoff' in "
                        f"{project_dir} first, then try again."
                    )
                archive = _Path(candidates[0])

            if not archive.is_file():
                return f"Error: archive not found at {archive}."

            data = archive.read_bytes()
            size_kb = round(len(data) / 1024)

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "workflow_send_handoff_archive",
                    {
                        "archive": str(archive),
                        "size_kb": size_kb,
                        "recipients": params.recipients,
                    },
                )

            # 2. Upload to Drive.
            drive = _drive()
            media = MediaInMemoryUpload(data, mimetype="application/gzip")
            created = (
                drive.files()
                .create(
                    body={"name": archive.name},
                    media_body=media,
                    fields="id, name, webViewLink",
                )
                .execute()
            )
            file_id = created["id"]
            link = created.get("webViewLink")

            # 3. Share with every recipient as reader (no notification).
            share_results = []
            for addr in params.recipients:
                try:
                    drive.permissions().create(
                        fileId=file_id,
                        body={"type": "user", "role": "reader", "emailAddress": addr},
                        sendNotificationEmail=False,
                        fields="id",
                    ).execute()
                    share_results.append({"recipient": addr, "shared": True})
                except Exception as inner:
                    log.warning("share to %s failed: %s", addr, inner)
                    share_results.append({"recipient": addr, "shared": False, "error": str(inner)})

            # 4. Email everyone with the link + default handoff body.
            subject = params.subject or "Google Workspace MCP — installer + user manual"
            note_block = (params.note.strip() + "\n\n") if params.note else ""
            body = (
                f"Hi there,\n\n"
                f"{note_block}"
                f"Sharing the Google Workspace MCP. It's a local MCP server that gives Claude\n"
                f"Cowork about 90 tools across Gmail, Calendar, Drive, Sheets, Docs, Tasks,\n"
                f"Contacts (with a real CRM layer), Chat, and cross-service workflows — including\n"
                f"actual email send, not just drafts.\n\n"
                f"Download the archive:\n{link}\n\n"
                f"What's inside the tarball:\n"
                f"  - Source code + install script (./install.sh)\n"
                f"  - HANDOFF.md and INSTALL.md — start with HANDOFF.md; it walks you through the ~15-min setup\n"
                f"  - GCP_SETUP.md for the one-time Google Cloud steps. You'll create your own\n"
                f"    Google Cloud project — OAuth credentials are personal and can't be shared.\n"
                f"  - A full user manual under docs/ in both Markdown and Word formats, covering\n"
                f"    100 workflow ideas plus guides for extending it.\n\n"
                f"Takes about 15 minutes of hands-on time plus a couple of minutes waiting for\n"
                f"installs. Ping me if anything breaks or you want a walk-through.\n"
            )

            mime_msg = EmailMessage()
            mime_msg["To"] = ", ".join(params.recipients)
            mime_msg["Subject"] = subject
            from_alias = config.get("default_from_alias")
            if from_alias:
                mime_msg["From"] = from_alias
            mime_msg.set_content(body)
            raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")
            sent = _gmail().users().messages().send(userId="me", body={"raw": raw}).execute()

            log.info(
                "workflow_send_handoff_archive sent archive=%s size=%dKB to=%s",
                archive.name, size_kb, params.recipients,
            )
            return json.dumps(
                {
                    "status": "sent",
                    "archive": archive.name,
                    "size_kb": size_kb,
                    "drive_file_id": file_id,
                    "drive_link": link,
                    "message_id": sent.get("id"),
                    "thread_id": sent.get("threadId"),
                    "shares": share_results,
                },
                indent=2,
            )
        except Exception as e:
            log.error("workflow_send_handoff_archive failed: %s", e)
            return format_error(e)

    # --- Bulk contact creation from sent mail -----------------------------

    @mcp.tool(
        name="workflow_create_contacts_from_sent_mail",
        annotations={
            "title": "Create saved contacts from recent sent mail",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_create_contacts_from_sent_mail(
        params: CreateContactsFromSentMailInput,
    ) -> str:
        """Bulk-populate saved contacts from your sent mail history.

        Scans `in:sent newer_than:<days>d`, extracts the To/Cc addresses (and
        display names) across those messages, dedupes against your existing
        saved contacts, and creates a new saved contact for every new address.

        Notes:
          - Universe is your Saved Contacts (People API `connections`). "Other
            contacts" are the auto-suggested directory; they're not touched.
          - `exclude_self` also excludes every address returned by Gmail's
            send-as list.
          - With `apply_rules_after=True`, domain-based auto-tagging rules
            fire after creation so organization/tier fields get filled in.
          - Supports `dry_run` — returns the planned additions without writing.
        """
        try:
            gmail = _gmail()
            people = gservices.people()

            # 1. Gather exclusion set: self + send-as + domain blocklist.
            exclude_addrs: set[str] = set()
            if params.exclude_self:
                try:
                    prof = gmail.users().getProfile(userId="me").execute()
                    me_addr = (prof.get("emailAddress") or "").lower()
                    if me_addr:
                        exclude_addrs.add(me_addr)
                except Exception as e:
                    log.warning("could not fetch my profile for exclusion: %s", e)
                try:
                    sendas = (
                        gmail.users().settings().sendAs().list(userId="me").execute()
                    )
                    for s in sendas.get("sendAs", []) or []:
                        addr = (s.get("sendAsEmail") or "").lower()
                        if addr:
                            exclude_addrs.add(addr)
                except Exception as e:
                    log.warning("could not fetch send-as list: %s", e)

            exclude_doms = {
                d.strip().lower().lstrip("@")
                for d in (params.exclude_domains or [])
                if d and d.strip()
            }
            only_doms = {
                d.strip().lower().lstrip("@")
                for d in (params.only_domains or [])
                if d and d.strip()
            }

            # 2. Gather existing saved-contact addresses (for skip_existing).
            existing_addrs: set[str] = set()
            existing_resource_by_addr: dict[str, str] = {}
            if params.skip_existing:
                page_token = None
                while True:
                    kwargs = {
                        "resourceName": "people/me",
                        "personFields": "emailAddresses,metadata",
                        "pageSize": 1000,
                    }
                    if page_token:
                        kwargs["pageToken"] = page_token
                    resp = (
                        people.people().connections().list(**kwargs).execute()
                    )
                    for p in resp.get("connections", []) or []:
                        for ea in p.get("emailAddresses") or []:
                            v = (ea.get("value") or "").lower()
                            if v:
                                existing_addrs.add(v)
                                existing_resource_by_addr.setdefault(
                                    v, p.get("resourceName", "")
                                )
                    page_token = resp.get("nextPageToken")
                    if not page_token:
                        break

            # 3. Scan sent mail for To/Cc addresses with display names.
            query = f"in:sent newer_than:{params.days}d"
            message_ids: list[str] = []
            page_token = None
            fetched = 0
            while fetched < params.limit_emails_scanned:
                remaining = params.limit_emails_scanned - fetched
                page_size = min(500, remaining)
                kwargs = {
                    "userId": "me",
                    "q": query,
                    "maxResults": page_size,
                }
                if page_token:
                    kwargs["pageToken"] = page_token
                resp = gmail.users().messages().list(**kwargs).execute()
                batch = resp.get("messages", []) or []
                message_ids.extend(m["id"] for m in batch)
                fetched += len(batch)
                page_token = resp.get("nextPageToken")
                if not page_token or not batch:
                    break

            # 4. For each message, fetch just the headers we need.
            #    Using metadata format keeps the payload tiny.
            candidates: dict[str, dict] = {}  # addr -> {display_name, first_seen_id}
            for mid in message_ids:
                try:
                    msg = (
                        gmail.users()
                        .messages()
                        .get(
                            userId="me",
                            id=mid,
                            format="metadata",
                            metadataHeaders=["To", "Cc"],
                        )
                        .execute()
                    )
                except Exception as e:
                    log.warning("fetch header for %s failed: %s", mid, e)
                    continue
                headers = {
                    h.get("name", "").lower(): h.get("value", "")
                    for h in (msg.get("payload", {}) or {}).get("headers", []) or []
                }
                for key in ("to", "cc"):
                    raw = headers.get(key, "")
                    if not raw:
                        continue
                    for part in raw.split(","):
                        part = part.strip()
                        if not part:
                            continue
                        addr = _parse_email_address(part)
                        if not addr:
                            continue
                        addr_lower = addr.lower()
                        if addr_lower in exclude_addrs:
                            continue
                        domain = addr_lower.rsplit("@", 1)[-1] if "@" in addr_lower else ""
                        if exclude_doms and domain in exclude_doms:
                            continue
                        if only_doms and domain not in only_doms:
                            continue
                        if params.skip_existing and addr_lower in existing_addrs:
                            continue
                        display = _extract_display_name(part)
                        # If the display name is just the email address repeated
                        # (common form: "addr@x" <addr@x>), treat as no display name.
                        if display and display.lower() == addr_lower:
                            display = ""
                        if addr_lower not in candidates:
                            candidates[addr_lower] = {
                                "email": addr,  # preserve original case
                                "display_name": display,
                                "first_seen_message_id": mid,
                            }
                        elif display and not candidates[addr_lower].get("display_name"):
                            # Upgrade the display name if we find a better one later.
                            candidates[addr_lower]["display_name"] = display

            # 5. Build creation plan.
            plan = []
            for addr_lower, info in sorted(candidates.items()):
                display = info.get("display_name") or ""
                if display:
                    first, last = _split_display_name(display)
                else:
                    # No display name — guess from the email's local-part so
                    # the contact isn't anonymous.
                    first, last = _guess_name_from_email(info["email"])
                plan.append(
                    {
                        "email": info["email"],
                        "first_name": first,
                        "last_name": last,
                        "display_name": display,
                    }
                )

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "workflow_create_contacts_from_sent_mail",
                    {
                        "query": query,
                        "messages_scanned": len(message_ids),
                        "existing_saved_contacts_checked": len(existing_addrs),
                        "to_create": len(plan),
                        "sample": plan[:20],
                    },
                )

            # 6. Create them, applying auto-tagging rules inline (same pattern as contacts_create).
            import rules as rules_mod  # lazy — avoid a module-level dep

            created: list[dict] = []
            failed: list[dict] = []
            rules_hits = 0
            for row in plan:
                # Apply rules: domain-based auto-tagging (organization, tier, custom fields, etc.).
                rule_top: dict = {}
                rule_custom: dict = {}
                rules_applied: list = []
                if params.apply_rules_after:
                    try:
                        rule_top, rule_custom, rules_applied = rules_mod.apply_rules(
                            row["email"],
                            existing_fields={
                                "first_name": row["first_name"] or None,
                                "last_name": row["last_name"] or None,
                                "organization": None,
                                "title": None,
                            },
                            existing_custom={},
                        )
                    except Exception as re:
                        log.warning("apply_rules for %s failed: %s", row["email"], re)

                first_name = row["first_name"] or rule_top.get("first_name") or ""
                last_name = row["last_name"] or rule_top.get("last_name") or ""

                body: dict = {
                    "emailAddresses": [{"value": row["email"], "type": "work"}],
                }
                name_part: dict = {}
                if first_name:
                    name_part["givenName"] = first_name
                if last_name:
                    name_part["familyName"] = last_name
                if name_part:
                    body["names"] = [name_part]
                org = rule_top.get("organization")
                title = rule_top.get("title")
                if org or title:
                    org_entry: dict = {}
                    if org:
                        org_entry["name"] = org
                    if title:
                        org_entry["title"] = title
                    body["organizations"] = [org_entry]
                if rule_custom:
                    body["userDefined"] = [
                        {"key": k, "value": str(v)} for k, v in rule_custom.items()
                    ]
                try:
                    person = (
                        people.people()
                        .createContact(
                            body=body,
                            personFields="names,emailAddresses,organizations,userDefined,metadata",
                        )
                        .execute()
                    )
                    if rules_applied:
                        rules_hits += 1
                    created.append(
                        {
                            "email": row["email"],
                            "resource_name": person.get("resourceName"),
                            "first_name": first_name,
                            "last_name": last_name,
                            "organization": org or None,
                            "rules_applied": rules_applied or None,
                        }
                    )
                except Exception as inner:
                    log.error("create contact %s failed: %s", row["email"], inner)
                    failed.append({"email": row["email"], "error": str(inner)})

            rules_result = (
                {"contacts_touched_by_rules": rules_hits}
                if params.apply_rules_after
                else "skipped"
            )

            # 8. Optionally enrich each newly-created contact from their inbound mail.
            enrichment_result: dict | str = "skipped"
            if params.enrich_from_inbox and created:
                try:
                    from tools.enrichment import _enrich_one  # lazy import
                    enriched_summaries: list[dict] = []
                    for c in created:
                        try:
                            summary_row = _enrich_one(
                                email=c["email"],
                                days=params.enrich_days,
                                overwrite=True,
                                conservative_titles=True,
                                dry_run=False,
                            )
                            enriched_summaries.append(summary_row)
                        except Exception as inner:
                            log.warning(
                                "enrich_from_inbox for %s failed: %s", c["email"], inner
                            )
                            enriched_summaries.append(
                                {"email": c["email"], "status": "failed", "error": str(inner)}
                            )
                    enrichment_result = {
                        "enriched_updated": sum(
                            1 for r in enriched_summaries if r.get("status") == "updated"
                        ),
                        "enriched_no_mail_found": sum(
                            1
                            for r in enriched_summaries
                            if r.get("status") == "skipped_no_recent_mail"
                        ),
                        "enriched_no_changes": sum(
                            1
                            for r in enriched_summaries
                            if r.get("status") == "no_changes_needed"
                        ),
                        "sample": enriched_summaries[:10],
                    }
                except Exception as e:
                    log.warning("enrich_from_inbox step failed: %s", e)
                    enrichment_result = {"error": str(e)}

            summary = {
                "query": query,
                "messages_scanned": len(message_ids),
                "unique_candidates": len(plan),
                "created": len(created),
                "failed": len(failed),
                "skipped_existing": params.skip_existing,
                "rules_applied": rules_result,
                "enrichment": enrichment_result,
                "created_sample": created[:20],
                "failed_sample": failed[:20],
            }
            log.info(
                "workflow_create_contacts_from_sent_mail: scanned=%d created=%d failed=%d",
                len(message_ids),
                len(created),
                len(failed),
            )
            return json.dumps(summary, indent=2)
        except Exception as e:
            log.error("workflow_create_contacts_from_sent_mail failed: %s", e)
            return format_error(e)

    # --- Find meeting slot --------------------------------------------------

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
            except Exception:
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
        name="workflow_email_with_map",
        annotations={
            "title": "Send an email with a static map image attached",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_email_with_map(params: EmailWithMapInput) -> str:
        """Email with an embedded static map for 'where to meet' situations.

        Renders a PNG map of `location` via Maps Static API, attaches it to
        the message, and sends. The body text appears above the attachment.

        Cost: ~$0.002 (Maps Static) + standard Gmail send.
        """
        try:
            gmaps = gservices.maps()  # raises if Maps key not configured

            # 1. Render the map.
            # The SDK requires `size` as a (width, height) tuple of ints — parse
            # the friendly "600x400" string form before passing through.
            from tools.maps import _parse_size
            size_tuple = _parse_size(params.size)
            chunks = gmaps.static_map(
                center=params.location,
                zoom=params.zoom,
                size=size_tuple,
                maptype=params.map_type,
                markers=[params.location],
            )
            map_bytes = b"".join(chunks) if hasattr(chunks, "__iter__") else chunks

            # 2. Build + send email.
            from email.message import EmailMessage as _EmailMessage
            mime_msg = _EmailMessage()
            mime_msg["To"] = ", ".join(params.to)
            if params.cc:
                mime_msg["Cc"] = ", ".join(params.cc)
            if params.bcc:
                mime_msg["Bcc"] = ", ".join(params.bcc)
            mime_msg["Subject"] = params.subject
            from_alias = config.get("default_from_alias")
            if from_alias:
                mime_msg["From"] = from_alias
            mime_msg.set_content(params.body + f"\n\nMap of: {params.location}")
            mime_msg.add_attachment(
                map_bytes, maintype="image", subtype="png", filename="map.png"
            )

            if is_dry_run(params.dry_run):
                return dry_run_preview("workflow_email_with_map", {
                    "to": params.to,
                    "subject": params.subject,
                    "location": params.location,
                    "map_size_kb": round(len(map_bytes) / 1024),
                })

            raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")
            sent = _gmail().users().messages().send(userId="me", body={"raw": raw}).execute()
            log.info(
                "workflow_email_with_map: sent to %s, map %dKB", params.to,
                len(map_bytes) // 1024,
            )
            return json.dumps({
                "status": "sent",
                "id": sent.get("id"),
                "thread_id": sent.get("threadId"),
                "map_size_kb": round(len(map_bytes) / 1024),
                "location": params.location,
            }, indent=2)
        except RuntimeError as e:
            # Maps key not configured.
            return json.dumps({
                "status": "maps_not_configured",
                "error": str(e),
                "hint": "Run system_check_maps_api_key for setup steps.",
            }, indent=2)
        except Exception as e:
            log.error("workflow_email_with_map failed: %s", e)
            return format_error(e)

    # --- Chat + Maps composition --------------------------------------------

    @mcp.tool(
        name="workflow_chat_with_map",
        annotations={
            "title": "Send a Chat message with a map image attached",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_chat_with_map(params: ChatWithMapInput) -> str:
        """Chat parallel to `workflow_email_with_map`.

        Renders a Maps Static PNG of `location`, posts it to a Chat space
        (by `space_name`) or to a DM (by `email`), with `text` as the body.
        Cost: ~$0.002 (Maps Static) + standard Chat send.
        """
        try:
            if bool(params.space_name) == bool(params.email):
                return "Error: provide exactly one of `space_name` or `email`."

            chat = gservices.chat()
            gmaps = gservices.maps()  # raises if Maps key missing

            # 1. Resolve target space.
            target_space = params.space_name
            if params.email:
                user_resource = f"users/{params.email}"
                try:
                    found = chat.spaces().findDirectMessage(name=user_resource).execute()
                    if found and found.get("name"):
                        target_space = found["name"]
                except Exception:
                    pass
                if not target_space and params.create_dm_if_missing:
                    try:
                        created = chat.spaces().setup(body={
                            "space": {"spaceType": "DIRECT_MESSAGE"},
                            "memberships": [
                                {"member": {"name": user_resource, "type": "HUMAN"}}
                            ],
                        }).execute()
                        target_space = created.get("name")
                    except Exception as inner:
                        return json.dumps({
                            "status": "dm_create_failed",
                            "email": params.email,
                            "error": str(inner),
                        }, indent=2)
                if not target_space:
                    return json.dumps({
                        "status": "no_dm_space",
                        "email": params.email,
                    }, indent=2)

            # 2. Render map.
            from tools.maps import _parse_size
            size_tuple = _parse_size(params.size)
            chunks = gmaps.static_map(
                center=params.location,
                zoom=params.zoom,
                size=size_tuple,
                maptype=params.map_type,
                markers=[params.location],
            )
            map_bytes = b"".join(chunks) if hasattr(chunks, "__iter__") else chunks

            if is_dry_run(params.dry_run):
                return dry_run_preview("workflow_chat_with_map", {
                    "space_name": target_space,
                    "email": params.email,
                    "location": params.location,
                    "map_size_kb": round(len(map_bytes) / 1024),
                    "text_preview": params.text[:300],
                })

            # 3. Upload media + send message.
            from googleapiclient.http import MediaInMemoryUpload
            media = MediaInMemoryUpload(
                map_bytes, mimetype="image/png", resumable=True,
            )
            uploaded = chat.media().upload(
                parent=target_space,
                body={"filename": "map.png"},
                media_body=media,
            ).execute()
            attachment_data_ref = uploaded.get("attachmentDataRef") or {}

            sent = chat.spaces().messages().create(
                parent=target_space,
                body={
                    "text": params.text + f"\n\nMap of: {params.location}",
                    "attachment": [
                        {
                            "contentName": "map.png",
                            "contentType": "image/png",
                            "attachmentDataRef": attachment_data_ref,
                        }
                    ],
                },
            ).execute()

            log.info(
                "workflow_chat_with_map: sent to %s, map %dKB",
                target_space, len(map_bytes) // 1024,
            )
            return json.dumps({
                "status": "sent",
                "space_name": target_space,
                "message_name": sent.get("name"),
                "thread": sent.get("thread", {}).get("name"),
                "map_size_kb": round(len(map_bytes) / 1024),
                "location": params.location,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({
                "status": "maps_not_configured",
                "error": str(e),
            }, indent=2)
        except Exception as e:
            log.error("workflow_chat_with_map failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_chat_share_place",
        annotations={
            "title": "Share a Google Place to Chat as a rich card",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_chat_share_place(params: ChatSharePlaceInput) -> str:
        """Share a Place (restaurant, office, etc.) as a Chat message.

        Pulls full place details (name, address, rating, hours, website,
        Google Maps URL), formats as a clean markdown message, and optionally
        attaches a static map. Use a `place_id` from any maps_search_* call.
        """
        try:
            if bool(params.space_name) == bool(params.email):
                return "Error: provide exactly one of `space_name` or `email`."

            chat = gservices.chat()
            gmaps = gservices.maps()

            # 1. Pull place details.
            fields = [
                "name", "formatted_address", "formatted_phone_number",
                "website", "url", "rating", "user_ratings_total",
                "opening_hours", "geometry", "type", "price_level",
                "editorial_summary", "business_status",
            ]
            details = gmaps.place(place_id=params.place_id, fields=fields).get("result", {})
            if not details:
                return f"Error: no details found for place_id '{params.place_id}'."

            # 2. Resolve target space.
            target_space = params.space_name
            if params.email:
                user_resource = f"users/{params.email}"
                try:
                    found = chat.spaces().findDirectMessage(name=user_resource).execute()
                    if found and found.get("name"):
                        target_space = found["name"]
                except Exception:
                    pass
                if not target_space and params.create_dm_if_missing:
                    try:
                        created = chat.spaces().setup(body={
                            "space": {"spaceType": "DIRECT_MESSAGE"},
                            "memberships": [
                                {"member": {"name": user_resource, "type": "HUMAN"}}
                            ],
                        }).execute()
                        target_space = created.get("name")
                    except Exception as inner:
                        return json.dumps({
                            "status": "dm_create_failed",
                            "email": params.email,
                            "error": str(inner),
                        }, indent=2)
                if not target_space:
                    return json.dumps({
                        "status": "no_dm_space",
                        "email": params.email,
                    }, indent=2)

            # 3. Format the message.
            lines: list[str] = []
            if params.prefix_text:
                lines.append(params.prefix_text.strip())
                lines.append("")
            lines.append(f"*{details.get('name', '(unknown)')}*")
            if details.get("editorial_summary"):
                lines.append(f"_{details['editorial_summary'].get('overview', '')}_")
            lines.append(f"📍 {details.get('formatted_address', '')}")
            if details.get("rating"):
                stars = "⭐" * int(round(details["rating"]))
                lines.append(
                    f"{stars} {details['rating']:.1f} "
                    f"({details.get('user_ratings_total', 0)} reviews)"
                )
            if details.get("price_level") is not None:
                lines.append("💰 " + ("$" * int(details["price_level"])))
            if details.get("formatted_phone_number"):
                lines.append(f"📞 {details['formatted_phone_number']}")
            if details.get("website"):
                lines.append(f"🌐 {details['website']}")
            if (details.get("opening_hours") or {}).get("open_now") is not None:
                status = "Open now" if details["opening_hours"]["open_now"] else "Closed"
                lines.append(f"🕐 {status}")
            if details.get("url"):
                lines.append(f"🗺️ <{details['url']}|Open in Google Maps>")
            if details.get("business_status") and details["business_status"] != "OPERATIONAL":
                lines.append(f"⚠️ {details['business_status']}")
            chat_text = "\n".join(lines)

            map_bytes = None
            if params.include_map:
                loc = (details.get("geometry") or {}).get("location") or {}
                if loc:
                    chunks = gmaps.static_map(
                        center=f"{loc['lat']},{loc['lng']}",
                        zoom=15,
                        size=(600, 400),
                        markers=[f"{loc['lat']},{loc['lng']}"],
                    )
                    map_bytes = b"".join(chunks) if hasattr(chunks, "__iter__") else chunks

            if is_dry_run(params.dry_run):
                return dry_run_preview("workflow_chat_share_place", {
                    "space_name": target_space,
                    "place_name": details.get("name"),
                    "message_preview": chat_text[:600],
                    "map_attached": map_bytes is not None,
                    "map_size_kb": round(len(map_bytes) / 1024) if map_bytes else 0,
                })

            # 4. Send.
            msg_body: dict = {"text": chat_text}
            if map_bytes:
                from googleapiclient.http import MediaInMemoryUpload
                media = MediaInMemoryUpload(map_bytes, mimetype="image/png", resumable=True)
                uploaded = chat.media().upload(
                    parent=target_space,
                    body={"filename": "map.png"},
                    media_body=media,
                ).execute()
                msg_body["attachment"] = [{
                    "contentName": "map.png",
                    "contentType": "image/png",
                    "attachmentDataRef": uploaded.get("attachmentDataRef") or {},
                }]

            sent = chat.spaces().messages().create(
                parent=target_space, body=msg_body,
            ).execute()
            log.info(
                "workflow_chat_share_place: shared %s to %s",
                details.get("name"), target_space,
            )
            return json.dumps({
                "status": "sent",
                "space_name": target_space,
                "message_name": sent.get("name"),
                "place_name": details.get("name"),
                "place_address": details.get("formatted_address"),
                "map_attached": map_bytes is not None,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({
                "status": "maps_not_configured",
                "error": str(e),
            }, indent=2)
        except Exception as e:
            log.error("workflow_chat_share_place failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_chat_meeting_brief",
        annotations={
            "title": "DM each attendee a personalized meeting brief with map + travel time",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_chat_meeting_brief(params: ChatMeetingBriefInput) -> str:
        """Send each attendee a personalized 'here's the venue, here's a map, here's how long it'll take you' Chat DM.

        For each attendee:
          1. Computes their travel time from their address to the venue.
          2. Resolves (or creates) a DM space.
          3. Sends a personalized message with the venue, their ETA, and a map.
          4. Optionally logs the activity to their saved contact.

        Cost per attendee: ~$0.005 (geocode) + ~$0.005 (distance matrix) + ~$0.002 (map).
        For 5 attendees: ~$0.06 total.

        This is the flagship Maps + Chat + CRM tool — combines venue logistics
        with personalized messaging and contact tracking.
        """
        try:
            chat = gservices.chat()
            gmaps = gservices.maps()

            # Resolve venue address (handle place_id: prefix).
            venue_arg = params.venue
            if venue_arg.startswith("place_id:"):
                pid = venue_arg.split(":", 1)[1]
                detail = gmaps.place(place_id=pid, fields=["formatted_address", "name", "geometry"]).get("result", {})
                venue_address = detail.get("formatted_address", venue_arg)
                venue_label = params.venue_label or detail.get("name") or venue_address
                loc = (detail.get("geometry") or {}).get("location") or {}
                venue_coords = (loc.get("lat"), loc.get("lng")) if loc else None
            else:
                venue_address = venue_arg
                venue_label = params.venue_label or venue_arg
                venue_coords = None

            # Distance matrix from each attendee's address → venue.
            origins = [a.address for a in params.attendees]
            kwargs: dict = {
                "origins": origins,
                "destinations": [venue_address],
                "mode": params.travel_mode,
            }
            if params.meeting_time_iso:
                import datetime as _dt
                dt = _dt.datetime.fromisoformat(params.meeting_time_iso.replace("Z", "+00:00"))
                kwargs["departure_time"] = dt
            dm = gmaps.distance_matrix(**kwargs)

            # Pre-render one map (same for everyone).
            chunks = gmaps.static_map(
                center=venue_address,
                zoom=15,
                size=(600, 400),
                markers=[venue_address],
            )
            map_bytes = b"".join(chunks) if hasattr(chunks, "__iter__") else chunks

            results: list[dict] = []
            for i, attendee in enumerate(params.attendees):
                # Get travel info for this attendee.
                el = (dm.get("rows") or [])[i].get("elements", [])[0] if i < len(dm.get("rows", [])) else {}
                travel_text = "(travel time unavailable)"
                if el and el.get("status") == "OK":
                    duration = (el.get("duration_in_traffic") or el.get("duration") or {}).get("text")
                    distance = (el.get("distance") or {}).get("text")
                    if duration and distance:
                        travel_text = f"{duration} ({distance})"

                # Build personalized message.
                first = attendee.first_name or attendee.email.split("@")[0]
                lines = [
                    f"Hi {first} —",
                    "",
                    f"📍 *{venue_label}*",
                    f"{venue_address}",
                    "",
                    f"🚗 From your end: {travel_text}",
                ]
                if params.meeting_time_iso:
                    lines.append(f"🕐 Meeting time: {params.meeting_time_iso}")
                if params.custom_message:
                    lines.append("")
                    lines.append(params.custom_message)
                lines.append("")
                lines.append("— Finnn")
                msg_text = "\n".join(lines)

                if is_dry_run(params.dry_run):
                    results.append({
                        "email": attendee.email,
                        "first_name": first,
                        "travel_text": travel_text,
                        "message_preview": msg_text[:400],
                        "status": "dry_run",
                    })
                    continue

                # Resolve DM.
                user_resource = f"users/{attendee.email}"
                target_space = None
                try:
                    found = chat.spaces().findDirectMessage(name=user_resource).execute()
                    if found and found.get("name"):
                        target_space = found["name"]
                except Exception:
                    pass
                if not target_space and params.create_dm_if_missing:
                    try:
                        created = chat.spaces().setup(body={
                            "space": {"spaceType": "DIRECT_MESSAGE"},
                            "memberships": [
                                {"member": {"name": user_resource, "type": "HUMAN"}}
                            ],
                        }).execute()
                        target_space = created.get("name")
                    except Exception as inner:
                        results.append({
                            "email": attendee.email,
                            "status": "dm_create_failed",
                            "error": str(inner),
                        })
                        if params.stop_on_first_error:
                            break
                        continue
                if not target_space:
                    results.append({
                        "email": attendee.email, "status": "no_dm_space",
                    })
                    continue

                # Upload map + send.
                try:
                    from googleapiclient.http import MediaInMemoryUpload
                    media = MediaInMemoryUpload(
                        map_bytes, mimetype="image/png", resumable=True,
                    )
                    uploaded = chat.media().upload(
                        parent=target_space,
                        body={"filename": "venue_map.png"},
                        media_body=media,
                    ).execute()
                    sent = chat.spaces().messages().create(
                        parent=target_space,
                        body={
                            "text": msg_text,
                            "attachment": [{
                                "contentName": "venue_map.png",
                                "contentType": "image/png",
                                "attachmentDataRef": uploaded.get("attachmentDataRef") or {},
                            }],
                        },
                    ).execute()

                    # Log activity to CRM if requested.
                    if params.log_to_contact:
                        try:
                            from tools.chat import _log_chat_activity_on_contact
                            _log_chat_activity_on_contact(
                                attendee.email,
                                f"Meeting brief: {venue_label} — {travel_text} travel",
                            )
                        except Exception as inner:
                            log.warning("activity log %s: %s", attendee.email, inner)

                    results.append({
                        "email": attendee.email,
                        "first_name": first,
                        "status": "sent",
                        "space_name": target_space,
                        "message_name": sent.get("name"),
                        "travel_text": travel_text,
                    })
                except Exception as inner:
                    log.error("workflow_chat_meeting_brief: send to %s failed: %s",
                              attendee.email, inner)
                    results.append({
                        "email": attendee.email,
                        "status": "failed",
                        "error": str(inner),
                    })
                    if params.stop_on_first_error:
                        break

            counts = {
                k: sum(1 for r in results if r.get("status") == k)
                for k in ("sent", "failed", "dm_create_failed", "no_dm_space", "dry_run")
            }
            log.info("workflow_chat_meeting_brief: %s", counts)
            return json.dumps({
                "venue": venue_label,
                "venue_address": venue_address,
                "attendee_count": len(params.attendees),
                "counts": counts,
                "map_size_kb": round(len(map_bytes) / 1024),
                "results": results,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({
                "status": "maps_not_configured",
                "error": str(e),
            }, indent=2)
        except Exception as e:
            log.error("workflow_chat_meeting_brief failed: %s", e)
            return format_error(e)

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
        name="workflow_chat_digest",
        annotations={
            "title": "Email a daily Chat digest summarizing recent activity",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_chat_digest(params: ChatDigestWorkflowInput) -> str:
        """Summarize the last N hours of Chat activity and email yourself a recap.

        Walks every Chat space, groups recent messages by space, optionally
        summarizes the corpus with Claude, and emails the result. Designed for
        a daily cron — run it at 7am and you've already got the gist of what
        happened overnight before opening Chat.

        Output: markdown-formatted email body with each space getting a section,
        plus an attached `chat_digest.json` for programmatic readers.
        """
        import datetime as _dt

        try:
            chat = gservices.chat()
            gmail = _gmail()

            # Resolve recipient.
            recipient = params.recipient
            if not recipient:
                try:
                    profile = gmail.users().getProfile(userId="me").execute()
                    recipient = profile.get("emailAddress")
                except Exception as e:
                    return f"Error: couldn't determine recipient: {e}"

            cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=params.hours)
            cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
            time_filter = f'createTime > "{cutoff_iso}"'

            # 1. Walk spaces.
            all_spaces: list[dict] = []
            page_token = None
            while True:
                kwargs: dict = {"pageSize": 1000}
                if page_token:
                    kwargs["pageToken"] = page_token
                resp = chat.spaces().list(**kwargs).execute()
                for s in resp.get("spaces") or []:
                    if s.get("spaceType") == "DIRECT_MESSAGE" and not params.include_dms:
                        continue
                    if s.get("spaceType") == "SPACE" and not params.include_rooms:
                        continue
                    all_spaces.append(s)
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

            # 2. Per space, fetch messages within window.
            space_buckets: list[dict] = []
            total_msgs = 0
            for space in all_spaces:
                try:
                    resp = chat.spaces().messages().list(
                        parent=space["name"],
                        filter=time_filter,
                        pageSize=200,
                        orderBy="createTime asc",
                    ).execute()
                    msgs = resp.get("messages") or []
                    if not msgs:
                        continue
                    # Build per-message dicts. Google's API omits displayName
                    # for messages YOU sent in DMs, so fall back to "(you)"
                    # whenever there's a sender resource without a display name.
                    rendered_msgs = []
                    for m in msgs:
                        sender_obj = m.get("sender") or {}
                        sender_label = (
                            sender_obj.get("displayName")
                            or ("(you)" if sender_obj.get("name") else "(unknown)")
                        )
                        rendered_msgs.append({
                            "sender": sender_label,
                            "sender_resource": sender_obj.get("name"),
                            "text": m.get("text") or "",
                            "create_time": m.get("createTime"),
                        })
                    space_buckets.append({
                        "space_name": space["name"],
                        "display_name": space.get("displayName") or "(direct message)",
                        "type": space.get("spaceType"),
                        "messages": rendered_msgs,
                    })
                    total_msgs += len(msgs)
                except Exception as inner:
                    log.warning(
                        "workflow_chat_digest: skipping %s (%s)",
                        space.get("name"), inner,
                    )

            if not space_buckets:
                summary_text = (
                    f"_No Chat activity in the last {params.hours} hours._\n\n"
                    "Nothing to digest — go enjoy the quiet."
                )
                llm_summary = None
            else:
                # 3. Optional LLM summary.
                llm_summary = None
                if params.use_llm:
                    try:
                        import llm
                        ok, _ = llm.is_available()
                        if ok:
                            corpus = []
                            for b in space_buckets[:30]:  # cap to keep prompt size sane
                                hdr = f"### {b['display_name']} ({b['type']})"
                                lines = [hdr] + [
                                    f"- **{m['sender']}**: {m['text'][:300]}"
                                    for m in b["messages"][:50]
                                ]
                                corpus.append("\n".join(lines))
                            joined = "\n\n".join(corpus)[:60_000]
                            prompt = (
                                f"Summarize the last {params.hours} hours of Google Chat activity "
                                "for me. Group by space; for each space give a 2-3 sentence summary "
                                "of the key threads, decisions, and any items that need my attention. "
                                "Use markdown. Be concise but specific — quote actual phrasing where "
                                "useful. Flag urgent / blocking / question-for-me items at the top.\n\n"
                                "Here's the raw activity:\n\n"
                                f"{joined}"
                            )
                            res = llm.call_simple(
                                prompt,
                                model="claude-sonnet-4-6",
                                max_tokens=2500,
                                temperature=0.2,
                            )
                            llm_summary = res["text"]
                            log.info(
                                "workflow_chat_digest: LLM summary done, %d tokens, ~$%s",
                                res["input_tokens"] + res["output_tokens"],
                                res["estimated_cost_usd"],
                            )
                    except Exception as e:
                        log.warning("workflow_chat_digest: LLM summary failed: %s", e)

                # 4. Build markdown body.
                lines = [
                    f"# Chat digest — last {params.hours} hours",
                    f"_{total_msgs} message(s) across {len(space_buckets)} space(s)._",
                    "",
                ]
                if llm_summary:
                    lines.extend(["## Summary", "", llm_summary, "", "---", ""])
                lines.append("## Raw activity")
                for b in space_buckets:
                    lines.append("")
                    lines.append(f"### {b['display_name']} _({b['type']})_")
                    for m in b["messages"][:25]:
                        ts = (m.get("create_time") or "")[:16]
                        text = (m.get("text") or "").replace("\n", " ")[:300]
                        lines.append(f"- _{ts}_ — **{m['sender']}**: {text}")
                    if len(b["messages"]) > 25:
                        lines.append(f"- _… and {len(b['messages']) - 25} more_")
                summary_text = "\n".join(lines)

            # 5. Build JSON attachment.
            json_attachment = json.dumps({
                "generated": _dt.datetime.now(_dt.timezone.utc).isoformat() + "Z",
                "window_hours": params.hours,
                "total_messages": total_msgs,
                "spaces": space_buckets,
            }, indent=2)

            if is_dry_run(params.dry_run):
                return dry_run_preview("workflow_chat_digest", {
                    "recipient": recipient,
                    "spaces_with_activity": len(space_buckets),
                    "total_messages": total_msgs,
                    "preview_first_500": summary_text[:500],
                    "json_attachment_size": len(json_attachment),
                })

            # 6. Send email with markdown body + JSON attached.
            from email.message import EmailMessage as _EmailMessage
            mime_msg = _EmailMessage()
            mime_msg["To"] = recipient
            mime_msg["Subject"] = f"Chat digest — last {params.hours}h"
            from_alias = config.get("default_from_alias")
            if from_alias:
                mime_msg["From"] = from_alias
            mime_msg.set_content(summary_text)

            mime_msg.add_attachment(
                json_attachment.encode("utf-8"),
                maintype="application",
                subtype="json",
                filename="chat_digest.json",
            )

            raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode("utf-8")
            sent = gmail.users().messages().send(userId="me", body={"raw": raw}).execute()

            log.info(
                "workflow_chat_digest: sent to %s (%d spaces, %d msgs, llm=%s)",
                recipient, len(space_buckets), total_msgs, llm_summary is not None,
            )
            return json.dumps({
                "status": "sent",
                "recipient": recipient,
                "spaces_with_activity": len(space_buckets),
                "total_messages": total_msgs,
                "llm_summary": bool(llm_summary),
                "message_id": sent.get("id"),
                "thread_id": sent.get("threadId"),
            }, indent=2)
        except Exception as e:
            log.error("workflow_chat_digest failed: %s", e)
            return format_error(e)

    # --- Chat to contact group ---------------------------------------------

    @mcp.tool(
        name="workflow_chat_to_contact_group",
        annotations={
            "title": "Send personalized Chat DMs to every saved contact in a group",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_chat_to_contact_group(params: ChatToContactGroupWorkflowInput) -> str:
        """Mail-merge but for Chat. Sends a templated DM to each group member.

        Walks the contact group, for each member with an email:
          1. Renders the template against their fields ({first_name}, {organization}, etc.)
          2. Calls findDirectMessage / setup to resolve the DM space
          3. Sends the rendered message
          4. Optionally appends a Chat-activity note to the contact

        Cross-domain failures don't abort the batch by default; per-recipient
        status is returned. Use `stop_on_first_error=True` to abort on the first
        failure if you'd rather investigate.
        """
        try:
            from tools.contacts import _flatten_person  # noqa: E402 — reuse flattening
            people = gservices.people()
            chat = gservices.chat()

            # 1. Walk the contact group.
            group_url = (
                f"contactGroups/{params.group_resource_name.split('/')[-1]}"
            )
            grp = people.contactGroups().get(
                resourceName=group_url, maxMembers=500,
            ).execute()
            member_resource_names = grp.get("memberResourceNames") or []
            if not member_resource_names:
                return f"Error: group '{params.group_resource_name}' has no members."

            # 2. Resolve each person.
            recipients: list[dict] = []
            for chunk_start in range(0, len(member_resource_names), 50):
                chunk = member_resource_names[chunk_start : chunk_start + 50]
                resp = people.people().getBatchGet(
                    resourceNames=chunk,
                    personFields="names,emailAddresses,organizations,userDefined,biographies,metadata",
                ).execute()
                for r in resp.get("responses") or []:
                    person = r.get("person")
                    if not person:
                        continue
                    flat = _flatten_person(person)
                    if not flat.get("email"):
                        continue
                    recipients.append({"flat": flat, "person": person})

            if not recipients:
                return "Error: no group members have an email address."

            results: list[dict] = []
            for r in recipients:
                flat = r["flat"]
                email = flat["email"]

                # Render the template.
                rendered = rendering.render(params.text, flat)

                if is_dry_run(params.dry_run):
                    results.append({
                        "email": email,
                        "first_name": flat.get("first_name"),
                        "rendered_preview": rendered[:300],
                        "status": "dry_run",
                    })
                    continue

                # Find or create DM.
                user_resource = f"users/{email}"
                space_name = None
                try:
                    existing = chat.spaces().findDirectMessage(
                        name=user_resource
                    ).execute()
                    if existing and existing.get("name"):
                        space_name = existing["name"]
                except Exception as inner:
                    if "404" not in str(inner):
                        log.warning("findDirectMessage %s: %s", email, inner)

                if not space_name and params.create_dm_if_missing:
                    try:
                        created = chat.spaces().setup(body={
                            "space": {"spaceType": "DIRECT_MESSAGE"},
                            "memberships": [
                                {"member": {"name": user_resource, "type": "HUMAN"}}
                            ],
                        }).execute()
                        space_name = created.get("name")
                    except Exception as inner:
                        err = str(inner)
                        results.append({
                            "email": email,
                            "status": "dm_create_failed",
                            "error": err,
                        })
                        if params.stop_on_first_error:
                            break
                        continue

                if not space_name:
                    results.append({
                        "email": email,
                        "status": "no_dm_space",
                    })
                    continue

                # Send.
                try:
                    sent = chat.spaces().messages().create(
                        parent=space_name,
                        body={"text": rendered},
                    ).execute()
                    if params.log_to_contact:
                        try:
                            from tools.chat import _log_chat_activity_on_contact
                            _log_chat_activity_on_contact(email, rendered)
                        except Exception as inner:
                            log.warning("activity log %s: %s", email, inner)
                    results.append({
                        "email": email,
                        "first_name": flat.get("first_name"),
                        "status": "sent",
                        "space_name": space_name,
                        "message_name": sent.get("name"),
                    })
                except Exception as inner:
                    log.error("workflow_chat_to_contact_group: send to %s failed: %s",
                              email, inner)
                    results.append({
                        "email": email,
                        "status": "failed",
                        "error": str(inner),
                    })
                    if params.stop_on_first_error:
                        break

            counts = {
                k: sum(1 for r in results if r.get("status") == k)
                for k in ("sent", "failed", "dm_create_failed", "no_dm_space",
                          "dry_run")
            }
            log.info("workflow_chat_to_contact_group: %s", counts)
            return json.dumps({
                "group_resource_name": params.group_resource_name,
                "total": len(results),
                "counts": counts,
                "results": results,
            }, indent=2)
        except Exception as e:
            log.error("workflow_chat_to_contact_group failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_bulk_update_contacts",
        annotations={
            "title": "Patch many contacts with a single dry-run preview + rollback-on-failure",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_bulk_update_contacts(params: BulkUpdateContactsInput) -> str:
        """Apply multiple contact patches with dry-run preview and rollback.

        Pattern (template for future workflow_bulk_*):
          1. Snapshot every target before any write.
          2. If `dry_run=True`, return what WOULD happen without writing.
          3. Apply patches one by one.
          4. If any fail and `rollback_on_partial_failure=True`, restore from snapshots.
          5. Record every successful op + the rollback (if any) to recent_actions.

        Each patch supports: organization, title, notes, custom_fields (dict).
        Set a key to None to clear it. Custom fields with managed CRM keys
        (Last Interaction, Sent/Received tallies) are silently ignored.
        """
        try:
            import recent_actions as _ra
            import crm_stats
            from tools.contacts import _flatten_person
            people = gservices.people()

            # 1. Snapshot every target.
            snapshots: dict[str, dict] = {}
            failed_lookups: list[dict] = []
            for u in params.updates:
                try:
                    person = people.people().get(
                        resourceName=u.resource_name,
                        personFields="names,emailAddresses,organizations,biographies,userDefined,memberships,metadata",
                    ).execute()
                    snapshots[u.resource_name] = person
                except Exception as e:
                    failed_lookups.append({
                        "resource_name": u.resource_name,
                        "error": str(e)[:200],
                    })
            if failed_lookups:
                return json.dumps({
                    "status": "lookup_failed",
                    "failed": failed_lookups,
                    "hint": (
                        "Couldn't fetch some contacts. Check resource_names or "
                        "remove failed entries before re-running."
                    ),
                }, indent=2)

            # 2. Dry-run preview.
            previews: list[dict] = []
            for u in params.updates:
                snap = snapshots[u.resource_name]
                flat = _flatten_person(snap)
                changes: dict = {}
                for k, v in u.patch.items():
                    if k == "custom_fields" and isinstance(v, dict):
                        cleaned = {
                            ck: cv for ck, cv in v.items()
                            if not crm_stats.is_managed_key(ck)
                        }
                        if cleaned:
                            changes["custom_fields"] = cleaned
                    elif k in ("organization", "title", "notes"):
                        if v != flat.get(k):
                            changes[k] = {"from": flat.get(k), "to": v}
                previews.append({
                    "resource_name": u.resource_name,
                    "name": flat.get("name"),
                    "changes": changes,
                })

            if is_dry_run(params.dry_run):
                return json.dumps({
                    "status": "dry_run",
                    "would_update": len(previews),
                    "previews": previews,
                }, indent=2)

            # 3. Apply.
            applied: list[dict] = []
            failed: list[dict] = []
            action_ids: list[str] = []
            for u in params.updates:
                snap = snapshots[u.resource_name]
                etag = snap.get("etag")
                body: dict = {"etag": etag}
                update_fields: list[str] = []
                if "organization" in u.patch or "title" in u.patch:
                    org_block: dict = {}
                    if "organization" in u.patch:
                        org_block["name"] = u.patch["organization"] or ""
                    if "title" in u.patch:
                        org_block["title"] = u.patch["title"] or ""
                    body["organizations"] = [org_block]
                    update_fields.append("organizations")
                if "notes" in u.patch:
                    body["biographies"] = [{
                        "value": u.patch["notes"] or "",
                        "contentType": "TEXT_PLAIN",
                    }]
                    update_fields.append("biographies")
                if "custom_fields" in u.patch and isinstance(u.patch["custom_fields"], dict):
                    cleaned = {
                        ck: str(cv) for ck, cv in u.patch["custom_fields"].items()
                        if not crm_stats.is_managed_key(ck) and cv is not None
                    }
                    existing_ud = snap.get("userDefined") or []
                    by_key = {
                        ux.get("key"): dict(ux) for ux in existing_ud
                        if ux.get("key")
                    }
                    for ck, cv in cleaned.items():
                        by_key[ck] = {"key": ck, "value": cv}
                    body["userDefined"] = list(by_key.values())
                    update_fields.append("userDefined")
                if not update_fields:
                    continue
                try:
                    updated = people.people().updateContact(
                        resourceName=u.resource_name,
                        updatePersonFields=",".join(update_fields),
                        body=body,
                    ).execute()
                    flat_before = _flatten_person(snap)
                    flat_after = _flatten_person(updated)
                    rec_id = _ra.record(
                        tool="workflow_bulk_update_contacts",
                        action="update",
                        target_kind="contact",
                        target_id=u.resource_name,
                        summary=(
                            f"Updated {flat_after.get('name') or u.resource_name}: "
                            f"{', '.join(update_fields)}"
                        ),
                        snapshot_before={
                            "organization": flat_before.get("organization"),
                            "title": flat_before.get("title"),
                            "notes": flat_before.get("notes"),
                            "custom": flat_before.get("custom"),
                        },
                        snapshot_after={
                            "organization": flat_after.get("organization"),
                            "title": flat_after.get("title"),
                            "notes": flat_after.get("notes"),
                            "custom": flat_after.get("custom"),
                        },
                    )
                    action_ids.append(rec_id)
                    applied.append({
                        "resource_name": u.resource_name,
                        "name": flat_after.get("name"),
                        "action_id": rec_id,
                    })
                except Exception as e:
                    failed.append({
                        "resource_name": u.resource_name,
                        "error": str(e)[:300],
                    })
                    if params.rollback_on_partial_failure:
                        # Roll back already-applied changes from snapshots.
                        rollback_results = []
                        for already in applied:
                            rn = already["resource_name"]
                            snap = snapshots[rn]
                            try:
                                # Restore organizations + biographies + userDefined
                                # to their pre-batch state.
                                rollback_body: dict = {"etag": None}
                                # We need a fresh etag — fetch current.
                                fresh = people.people().get(
                                    resourceName=rn,
                                    personFields="metadata",
                                ).execute()
                                rollback_body["etag"] = fresh.get("etag")
                                rollback_body["organizations"] = snap.get("organizations") or [{}]
                                rollback_body["biographies"] = snap.get("biographies") or []
                                rollback_body["userDefined"] = snap.get("userDefined") or []
                                people.people().updateContact(
                                    resourceName=rn,
                                    updatePersonFields="organizations,biographies,userDefined",
                                    body=rollback_body,
                                ).execute()
                                # Record the revert.
                                _ra.record(
                                    tool="workflow_bulk_update_contacts",
                                    action="update",
                                    target_kind="contact",
                                    target_id=rn,
                                    summary=f"Rollback for {rn} after partial failure",
                                    snapshot_before=already.get("snapshot_after"),
                                    snapshot_after=snap,
                                    revert_target_action_id=already["action_id"],
                                )
                                _ra.mark_reverted(already["action_id"], "rollback")
                                rollback_results.append({
                                    "resource_name": rn, "status": "rolled_back",
                                })
                            except Exception as rb_err:
                                rollback_results.append({
                                    "resource_name": rn,
                                    "status": "rollback_failed",
                                    "error": str(rb_err)[:200],
                                })
                        return json.dumps({
                            "status": "rolled_back",
                            "failed_target": u.resource_name,
                            "fail_error": str(e)[:300],
                            "rollback": rollback_results,
                            "hint": "All changes reverted to pre-batch state.",
                        }, indent=2)

            return json.dumps({
                "status": "ok" if not failed else "partial",
                "applied_count": len(applied),
                "failed_count": len(failed),
                "applied": applied,
                "failed": failed,
                "action_ids": action_ids,
                "hint": (
                    "View these later via system_recent_actions. The "
                    "snapshot_before fields can be used to undo individual "
                    "changes."
                ),
            }, indent=2)
        except Exception as e:
            log.error("workflow_bulk_update_contacts failed: %s", e)
            return format_error(e)

    # --- Maps × CRM × Calendar workflows ------------------------------------ #

    @mcp.tool(
        name="workflow_nearby_contacts",
        annotations={
            "title": "Find saved contacts near a location, ranked by distance or recency",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_nearby_contacts(params: NearbyContactsInput) -> str:
        """'I'm in Austin Thu/Fri — who should I see?'

        Geocodes the input location, walks saved contacts, and returns those
        within `radius_km` ranked by distance (default) or recency. Contacts
        with stored lat/lng custom fields are matched instantly; missing ones
        are geocoded on-the-fly unless `require_geocoded=True`.

        Cost reference: ~$0.005 per contact geocoded on-the-fly + ~$0.005 per
        result if `include_travel_time=True`.
        """
        try:
            gmaps = gservices.maps()

            # 1. Anchor: geocode the input location.
            anchor_results = gmaps.geocode(params.location)
            if not anchor_results:
                return json.dumps({"status": "anchor_not_found", "location": params.location}, indent=2)
            anchor_geom = anchor_results[0]["geometry"]["location"]
            anchor = (anchor_geom["lat"], anchor_geom["lng"])
            anchor_label = anchor_results[0].get("formatted_address", params.location)

            # 2. Walk contacts, score each.
            scored: list[dict] = []
            for p in _walk_all_contacts(
                only_groups=params.only_groups, max_contacts=5000,
            ):
                addr_block = _extract_address_block(p)
                latlng = _contact_lat_lng(p)
                if not latlng:
                    if params.require_geocoded:
                        continue
                    formatted = addr_block.get("formatted")
                    if not formatted:
                        continue
                    try:
                        gres = gmaps.geocode(formatted)
                    except Exception:
                        continue
                    if not gres:
                        continue
                    g = gres[0]["geometry"]["location"]
                    latlng = (g["lat"], g["lng"])
                dist_km = _haversine_km(anchor, latlng)
                if dist_km > params.radius_km:
                    continue
                flat = _flatten_person(p)
                custom = flat.get("custom") or {}
                last_interaction = custom.get("Last Interaction")
                scored.append({
                    "resource_name": flat.get("resource_name"),
                    "name": flat.get("name"),
                    "email": flat.get("email"),
                    "organization": flat.get("organization"),
                    "title": flat.get("title"),
                    "address": addr_block.get("formatted"),
                    "city": addr_block.get("city"),
                    "region": addr_block.get("region"),
                    "lat": latlng[0],
                    "lng": latlng[1],
                    "distance_km": round(dist_km, 2),
                    "last_interaction": last_interaction,
                })

            # 3. Sort.
            if params.sort_by == "recency":
                scored.sort(
                    key=lambda r: (r.get("last_interaction") or ""), reverse=True,
                )
            else:
                scored.sort(key=lambda r: r["distance_km"])

            top = scored[: params.limit]

            # 4. Optional travel time.
            if params.include_travel_time and top:
                origins = [params.location]
                destinations = [f"{r['lat']},{r['lng']}" for r in top]
                try:
                    dm = gmaps.distance_matrix(
                        origins=origins, destinations=destinations,
                        mode=params.travel_mode,
                    )
                    rows = (dm.get("rows") or [{}])[0].get("elements") or []
                    for r, el in zip(top, rows):
                        if el.get("status") == "OK":
                            r["travel_minutes"] = round(el["duration"]["value"] / 60, 1)
                            r["travel_distance_km"] = round(el["distance"]["value"] / 1000, 2)
                except Exception as e:
                    log.warning("workflow_nearby_contacts: distance_matrix failed: %s", e)

            return json.dumps({
                "anchor": anchor_label,
                "anchor_lat": anchor[0],
                "anchor_lng": anchor[1],
                "radius_km": params.radius_km,
                "total_in_radius": len(scored),
                "returned": len(top),
                "sort_by": params.sort_by,
                "results": top,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_nearby_contacts failed: %s", e)
            return format_error(e)

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
        name="workflow_geocode_contacts_batch",
        annotations={
            "title": "Geocode every contact's address and store lat/lng custom fields",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_geocode_contacts_batch(params: GeocodeContactsBatchInput) -> str:
        """One-shot pass to geocode every contact's address.

        Stores `lat`, `lng`, `geocoded_at` as userDefined custom fields. Skips
        contacts that already have lat/lng unless `force=True`. Run this once
        before using `workflow_nearby_contacts` etc. for fast spatial queries.

        Cost: ~$0.005 per contact geocoded. 200 contacts ≈ $1.
        """
        try:
            gmaps = gservices.maps()
            import datetime as _dt

            results = {"geocoded": 0, "already_done": 0, "no_address": 0,
                       "geocode_failed": 0, "examples": []}
            processed = 0
            for p in _walk_all_contacts(
                only_groups=params.only_groups, max_contacts=params.max_contacts,
            ):
                processed += 1
                addr_block = _extract_address_block(p)
                addr = addr_block.get("formatted")
                if not addr:
                    results["no_address"] += 1
                    continue
                existing = _contact_lat_lng(p)
                if existing and not params.force:
                    results["already_done"] += 1
                    continue
                try:
                    gres = gmaps.geocode(addr)
                except Exception as e:
                    log.warning("geocode %s failed: %s", addr, e)
                    results["geocode_failed"] += 1
                    continue
                if not gres:
                    results["geocode_failed"] += 1
                    continue
                loc = gres[0]["geometry"]["location"]
                if is_dry_run(params.dry_run):
                    if len(results["examples"]) < 5:
                        results["examples"].append({
                            "name": (p.get("names", [{}])[0] or {}).get("displayName"),
                            "address": addr, "lat": loc["lat"], "lng": loc["lng"],
                        })
                    results["geocoded"] += 1
                    continue
                try:
                    _set_contact_custom_fields(
                        person_resource_name=p["resourceName"],
                        etag=p["etag"],
                        existing_userdefined=p.get("userDefined") or [],
                        updates={
                            "lat": f"{loc['lat']:.6f}",
                            "lng": f"{loc['lng']:.6f}",
                            "geocoded_at": _dt.datetime.now().isoformat(timespec="seconds"),
                        },
                    )
                    results["geocoded"] += 1
                except Exception as e:
                    log.warning("update %s failed: %s", p.get("resourceName"), e)
                    results["geocode_failed"] += 1

            return json.dumps({
                "status": "dry_run" if is_dry_run(params.dry_run) else "done",
                "processed": processed,
                **results,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_geocode_contacts_batch failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_address_hygiene_audit",
        annotations={
            "title": "Validate every contact's address and produce a fix-it report",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_address_hygiene_audit(params: AddressHygieneAuditInput) -> str:
        """Sweep all contacts, validate addresses via Address Validation API.

        Categorizes each as VALID / SUSPECT (Google inferred components) /
        INVALID, and optionally writes a Google Sheet with the report including
        suggested replacements. Use the resulting Sheet to fix things in bulk.

        Cost: ~$0.017 per contact validated. 200 contacts ≈ $3.40.
        """
        try:
            import datetime as _dt
            import os as _os
            import requests as _requests
            api_key = (
                config.get("google_maps_api_key")
                or _os.environ.get("GOOGLE_MAPS_API_KEY", "").strip()
            )
            if not api_key:
                return json.dumps({"status": "maps_not_configured"}, indent=2)

            rows = [["Name", "Email", "Current Address", "Verdict", "Suggested",
                    "Issues", "ResourceName"]]
            counts = {"VALID": 0, "SUSPECT": 0, "INVALID": 0, "NO_ADDRESS": 0}
            processed = 0

            for p in _walk_all_contacts(
                only_groups=params.only_groups, max_contacts=params.max_contacts,
            ):
                processed += 1
                addr_block = _extract_address_block(p)
                addr = addr_block.get("formatted")
                if not addr:
                    counts["NO_ADDRESS"] += 1
                    continue
                payload: dict = {"address": {"addressLines": [addr]}}
                if params.region_code:
                    payload["address"]["regionCode"] = params.region_code
                try:
                    http = _requests.post(
                        f"https://addressvalidation.googleapis.com/v1:validateAddress?key={api_key}",
                        json=payload, timeout=10,
                    )
                    http.raise_for_status()
                    data = http.json().get("result", {})
                except Exception as e:
                    log.warning("validate %s: %s", addr, e)
                    counts["INVALID"] += 1
                    rows.append([
                        (_flatten_person(p).get("name") or ""),
                        (_flatten_person(p).get("email") or ""),
                        addr, "INVALID", "", str(e), p.get("resourceName") or "",
                    ])
                    continue
                verdict = data.get("verdict") or {}
                addr_resp = data.get("address") or {}
                inferred = bool(verdict.get("hasInferredComponents"))
                missing = bool(verdict.get("hasReplacedComponents")) or bool(
                    verdict.get("hasUnconfirmedComponents")
                )
                completeness = (verdict.get("addressComplete") is True)
                if completeness and not (inferred or missing):
                    label = "VALID"
                elif inferred or missing:
                    label = "SUSPECT"
                else:
                    label = "INVALID"
                counts[label] = counts.get(label, 0) + 1
                suggested = addr_resp.get("formattedAddress") or ""
                issues = []
                if inferred:
                    issues.append("inferred_components")
                if missing:
                    issues.append("replaced_or_unconfirmed")
                if not completeness:
                    issues.append("incomplete")
                rows.append([
                    (_flatten_person(p).get("name") or ""),
                    (_flatten_person(p).get("email") or ""),
                    addr, label, suggested, ",".join(issues),
                    p.get("resourceName") or "",
                ])

            sheet_url = None
            sheet_id = None
            if params.write_to_sheet and len(rows) > 1:
                try:
                    sheets = gservices.sheets()
                    title = params.sheet_title or (
                        f"Contact Address Audit - {_dt.date.today().isoformat()}"
                    )
                    created = sheets.spreadsheets().create(
                        body={"properties": {"title": title}}
                    ).execute()
                    sheet_id = created.get("spreadsheetId")
                    sheet_url = created.get("spreadsheetUrl")
                    sheets.spreadsheets().values().update(
                        spreadsheetId=sheet_id, range="A1",
                        valueInputOption="RAW", body={"values": rows},
                    ).execute()
                except Exception as e:
                    log.warning("audit sheet write failed: %s", e)

            return json.dumps({
                "status": "done", "processed": processed, "counts": counts,
                "sheet_url": sheet_url, "sheet_id": sheet_id,
                "row_count": len(rows) - 1,
            }, indent=2)
        except Exception as e:
            log.error("workflow_address_hygiene_audit failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_contact_density_map",
        annotations={
            "title": "Static map of where your contacts are clustered",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_contact_density_map(params: ContactDensityMapInput) -> str:
        """Render a static map showing where your saved contacts live.

        Pulls stored lat/lng from contact custom fields. Run
        `workflow_geocode_contacts_batch` first if your contacts aren't yet
        geocoded.

        Cost: ~$0.002 (one Static Maps call).
        """
        try:
            from tools.maps import _parse_size
            gmaps = gservices.maps()
            markers = []
            counts_by_region: dict[str, int] = {}
            for p in _walk_all_contacts(only_groups=params.only_groups, max_contacts=2000):
                addr_block = _extract_address_block(p)
                if params.region_filter:
                    blob = " ".join([
                        (addr_block.get("city") or ""),
                        (addr_block.get("region") or ""),
                        (addr_block.get("formatted") or ""),
                    ]).lower()
                    if params.region_filter.lower() not in blob:
                        continue
                latlng = _contact_lat_lng(p)
                if not latlng:
                    continue
                if len(markers) < params.max_markers:
                    markers.append(f"{latlng[0]:.6f},{latlng[1]:.6f}")
                key = (addr_block.get("region") or addr_block.get("country") or "Unknown")
                counts_by_region[key] = counts_by_region.get(key, 0) + 1
            if not markers:
                return json.dumps({
                    "status": "no_geocoded_contacts",
                    "hint": "Run workflow_geocode_contacts_batch first.",
                }, indent=2)
            chunks = gmaps.static_map(
                size=_parse_size(params.size),
                maptype=params.map_type,
                markers=markers,
            )
            map_bytes = b"".join(chunks) if hasattr(chunks, "__iter__") else chunks
            saved_to = None
            b64 = None
            if params.save_to_path:
                Path = __import__("pathlib").Path
                Path(params.save_to_path).write_bytes(map_bytes)
                saved_to = params.save_to_path
            else:
                import base64 as _b64
                b64 = _b64.b64encode(map_bytes).decode("ascii")
            return json.dumps({
                "status": "ok",
                "marker_count": len(markers),
                "counts_by_region": counts_by_region,
                "map_size_kb": round(len(map_bytes) / 1024),
                "saved_to": saved_to,
                "image_base64": b64,
            }, indent=2)
        except RuntimeError as e:
            return json.dumps({"status": "maps_not_configured", "error": str(e)}, indent=2)
        except Exception as e:
            log.error("workflow_contact_density_map failed: %s", e)
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
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(tz_name)
            except Exception:
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
                    except Exception:
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


# --------------------------------------------------------------------------- #
# Private helpers
# --------------------------------------------------------------------------- #


def _resolve_recipient(r) -> dict:
    """Turn a RecipientInput into a flat field dict usable by the renderer.

    If resource_name is set, fetches the contact from People API and merges any
    inline overrides on top. Otherwise uses the inline fields directly.
    """
    fields: dict = {}
    if r.resource_name:
        people = gservices.people()
        person = (
            people.people()
            .get(
                resourceName=r.resource_name,
                personFields="names,emailAddresses,organizations,userDefined,metadata",
            )
            .execute()
        )
        fields.update(_flatten_person(person))
    # Inline overrides (always win over contact fetch).
    if r.email:
        fields["email"] = r.email
    if r.first_name is not None:
        fields["first_name"] = r.first_name
    if r.last_name is not None:
        fields["last_name"] = r.last_name
    if r.organization is not None:
        fields["organization"] = r.organization
    if r.title is not None:
        fields["title"] = r.title
    # Merge inline custom fields into both top-level (for easy {key} access)
    # and nested custom dict (preserving previous tool conventions).
    if r.custom:
        existing = fields.get("custom") or {}
        existing.update(r.custom)
        fields["custom"] = existing
        for k, v in r.custom.items():
            fields.setdefault(k, v)
    # If flattening exposed custom dict keys, promote to top-level too.
    for k, v in (fields.get("custom") or {}).items():
        fields.setdefault(k, v)
    return fields


def _parse_email_address(raw: str) -> str | None:
    """Extract the bare address out of 'Name <addr>' or plain 'addr'."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if "<" in raw and ">" in raw:
        return raw[raw.rfind("<") + 1 : raw.rfind(">")].strip()
    return raw if "@" in raw else None


def _extract_display_name(raw: str) -> str:
    """Pull the human-readable part of 'Name <addr>' — '' if none present."""
    raw = (raw or "").strip()
    if not raw or "<" not in raw:
        return ""
    name = raw.split("<", 1)[0].strip()
    # Strip matched surrounding quotes.
    if len(name) >= 2 and name[0] == name[-1] and name[0] in ("'", '"'):
        name = name[1:-1].strip()
    return name


def _split_display_name(name: str) -> tuple[str, str]:
    """Split 'First Last' or 'Last, First' into (first, last). Empty fields OK."""
    name = (name or "").strip()
    if not name:
        return "", ""
    if "," in name:
        # 'Last, First [M.]' — common directory format.
        last, _, rest = name.partition(",")
        return rest.strip(), last.strip()
    parts = name.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _guess_name_from_email(email: str) -> tuple[str, str]:
    """Fallback name extraction from an email address's local-part.

    Examples:
      jane.smith@x → ('Jane', 'Smith')
      jane_smith@x → ('Jane', 'Smith')
      jsmith@x     → ('Jsmith', '')
      conor@x      → ('Conor', '')
    """
    local = (email or "").split("@", 1)[0]
    if not local:
        return "", ""
    # Split on common separators.
    import re as _re
    parts = [p for p in _re.split(r"[.\-_]+", local) if p]
    if not parts:
        return "", ""
    parts = [p.capitalize() for p in parts]
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _strip_reply_prefixes(subject: str) -> str:
    s = subject
    while True:
        low = s.lstrip().lower()
        if low.startswith("re:") or low.startswith("fwd:") or low.startswith("fw:"):
            s = s.lstrip()[len("re:") if low.startswith("re:") else 3 :].lstrip()
        else:
            break
    return s.strip()
