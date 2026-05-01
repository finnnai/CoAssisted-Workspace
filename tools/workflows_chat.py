# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Chat-driven workflows (digest, share-place, group routing, meeting brief).

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
    _gmail,
)

# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
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

