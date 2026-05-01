# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP tool wrapper for the morning brief.

Exposes one tool: workflow_morning_brief. Pulls calendar, inbox-needs-reply,
AP outstanding, and stale relationships from the live APIs and feeds them
into morning_brief.compose_brief().
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import config
import gservices
import morning_brief as core
import vendor_followups
from errors import format_error
from logging_util import log


def _gmail():
    return gservices.gmail()


def _calendar():
    return gservices.calendar()


def _people():
    return gservices.people()


# --------------------------------------------------------------------------- #
# Data fetchers
# --------------------------------------------------------------------------- #


def _today_window(tz: _dt.tzinfo) -> tuple[str, str]:
    """Return ISO bounds for today (00:00 → 23:59:59) in the user's tz."""
    now = _dt.datetime.now(tz=tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + _dt.timedelta(days=1) - _dt.timedelta(seconds=1)
    return start.isoformat(), end.isoformat()


def _fetch_today_events(calendar_id: str = "primary") -> list[dict]:
    """Pull today's events. Returns Calendar API event shape."""
    tz = _dt.datetime.now().astimezone().tzinfo
    time_min, time_max = _today_window(tz)
    resp = (
        _calendar()
        .events()
        .list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        )
        .execute()
    )
    return resp.get("items", [])


def _fetch_authed_email() -> str | None:
    try:
        prof = _gmail().users().getProfile(userId="me").execute()
        return prof.get("emailAddress", "").lower() or None
    except Exception as e:
        # Gmail profile lookup is normally instant; if it fails the
        # caller upstream will degrade silently. Log so we know.
        log.warning("_get_my_email failed: %s", e)
        return None


def _fetch_inbox_needs_reply(window_days: int = 14, limit: int = 25) -> list[dict]:
    """Find inbox threads that look like they need a reply.

    Heuristic: unread threads from real humans (skip lists, calendar invites,
    Gmail auto-replies) within the last N days. We use Gmail search for the
    initial cut, then filter results.
    """
    svc = _gmail()
    # 'is:inbox is:unread newer_than:N -from:noreply -from:no-reply -category:promotions'
    query = (
        f"is:inbox is:unread newer_than:{window_days}d "
        "-from:noreply -from:no-reply -category:promotions -category:updates "
        "-category:forums -category:social"
    )
    resp = svc.users().messages().list(
        userId="me", q=query, maxResults=limit,
    ).execute()
    msg_ids = [m["id"] for m in resp.get("messages", [])]

    # Hydrate enough metadata to compute stale_days and identify the sender.
    out: list[dict] = []
    seen_threads: set[str] = set()
    for mid in msg_ids:
        try:
            full = svc.users().messages().get(
                userId="me", id=mid, format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
        except Exception as e:
            log.warning("morning_brief inbox hydrate failed for %s: %s", mid, e)
            continue
        thread_id = full.get("threadId", mid)
        if thread_id in seen_threads:
            continue
        seen_threads.add(thread_id)
        headers = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
        from_hdr = headers.get("From") or ""
        subject = headers.get("Subject") or ""
        date_hdr = headers.get("Date")

        stale_days = 0
        if date_hdr:
            try:
                from email.utils import parsedate_to_datetime
                msg_dt = parsedate_to_datetime(date_hdr)
                if msg_dt:
                    if msg_dt.tzinfo is None:
                        msg_dt = msg_dt.replace(tzinfo=_dt.timezone.utc)
                    now = _dt.datetime.now(tz=msg_dt.tzinfo)
                    stale_days = max(0, (now - msg_dt).days)
            except Exception:
                pass

        out.append({
            "id": thread_id,
            "from": from_hdr,
            "subject": subject,
            "snippet": full.get("snippet", ""),
            "stale_days": stale_days,
            "link": f"https://mail.google.com/mail/u/0/#inbox/{thread_id}",
        })
    return out


def _fetch_inbox_unread_count() -> int:
    """Total unread inbox volume (informational signal only)."""
    try:
        svc = _gmail()
        # Use the INBOX label resource — has a threadsUnread count.
        label = svc.users().labels().get(userId="me", id="INBOX").execute()
        return int(label.get("threadsUnread", 0) or 0)
    except Exception:
        return 0


def _fetch_vip_emails() -> set[str]:
    """Pull contacts tagged as VIP from People API.

    The convention: contacts with a userDefined entry { 'key': 'tier', 'value': 'vip' }
    or with a label/membership named 'VIP' count as VIP.
    """
    try:
        svc = _people()
        results = svc.people().connections().list(
            resourceName="people/me",
            personFields="emailAddresses,userDefined,memberships",
            pageSize=2000,
        ).execute()
    except Exception as e:
        log.warning("morning_brief: VIP fetch failed: %s", e)
        return set()

    vips: set[str] = set()
    for p in results.get("connections", []):
        is_vip = False
        for ud in p.get("userDefined", []) or []:
            if (ud.get("key") or "").lower() == "tier" and (ud.get("value") or "").lower() == "vip":
                is_vip = True
                break
            if (ud.get("key") or "").lower() == "vip":
                is_vip = True
                break
        # Memberships → contact group resourceName matches "*VIP*" name
        if not is_vip:
            for m in p.get("memberships", []) or []:
                grp = (m.get("contactGroupMembership") or {})
                name = (grp.get("contactGroupResourceName") or "").lower()
                if "vip" in name:
                    is_vip = True
                    break
        if not is_vip:
            continue
        for ea in p.get("emailAddresses", []) or []:
            v = (ea.get("value") or "").lower().strip()
            if v:
                vips.add(v)
    return vips


def _fetch_ap_outstanding() -> tuple[list[dict], list[dict]]:
    """Wrap vendor_followups (no API call — local sidecar)."""
    try:
        outstanding = vendor_followups.list_open()
        overdue = vendor_followups.due_for_reminder()
        return outstanding, overdue
    except Exception as e:
        log.warning("morning_brief: AP fetch failed: %s", e)
        return [], []


def _fetch_stale_contacts(threshold_days: int = 60, limit: int = 8) -> list[dict]:
    """Approximation: contacts with last_interaction stamps older than threshold.

    Reads from the contacts custom-field 'last_interaction' written by crm_stats.
    Returns at most `limit` of the staleness with VIPs prioritized.
    """
    try:
        svc = _people()
        results = svc.people().connections().list(
            resourceName="people/me",
            personFields="names,emailAddresses,userDefined",
            pageSize=2000,
        ).execute()
    except Exception as e:
        log.warning("morning_brief: stale-contact fetch failed: %s", e)
        return []

    now = _dt.datetime.now(tz=_dt.timezone.utc)
    rows: list[dict] = []
    for p in results.get("connections", []):
        last_iso: str | None = None
        for ud in p.get("userDefined", []) or []:
            if (ud.get("key") or "").lower() == "last_interaction":
                last_iso = ud.get("value")
                break
        if not last_iso:
            continue
        try:
            last_dt = _dt.datetime.fromisoformat(last_iso.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            continue
        days = (now - last_dt).days
        if days < threshold_days:
            continue
        emails = p.get("emailAddresses", []) or []
        names = p.get("names", []) or []
        rows.append({
            "name": (names[0] or {}).get("displayName") if names else None,
            "email": ((emails[0] or {}).get("value") if emails else "") or "",
            "days_since_contact": days,
            "link": None,
        })
    rows.sort(key=lambda r: -r["days_since_contact"])
    return rows[:limit]


# --------------------------------------------------------------------------- #
# Pydantic input
# --------------------------------------------------------------------------- #


class MorningBriefInput(BaseModel):
    """Inputs for workflow_morning_brief."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    output_format: str = Field(
        default="both",
        description="'json', 'markdown', or 'both' (default).",
    )
    inbox_window_days: int = Field(
        default=14, ge=1, le=60,
        description="How far back to look for inbox needs-reply candidates.",
    )
    stale_threshold_days: int = Field(
        default=60, ge=14, le=365,
        description="Days-since-contact for the relationship section.",
    )
    skip_inbox: bool = Field(
        default=False, description="Skip inbox fetch (faster, lower-noise debug).",
    )
    skip_relationships: bool = Field(
        default=False, description="Skip the stale-relationships section.",
    )


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_morning_brief",
        annotations={
            "title": "Generate the morning brief",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_morning_brief(params: MorningBriefInput) -> str:
        """Compose your morning brief.

        Pulls today's calendar, inbox threads that look like they need a reply,
        outstanding AP info-requests (with overdue flagging), and stale
        relationships, then ranks them into a 'top 5 things to do today' list.

        VIPs are read from People API contact groups (any group with 'VIP' in
        its name) or contacts with a userDefined `tier=vip` field.

        Returns either JSON (`output_format=json`), markdown (`markdown`), or
        both wrapped in a single response (`both`, default).
        """
        try:
            today = _dt.datetime.now().astimezone().date().isoformat()
            user_email = _fetch_authed_email()
            events = _fetch_today_events()

            if params.skip_inbox:
                inbox = []
                unread_total = 0
                vip_emails: set[str] = set()
            else:
                inbox = _fetch_inbox_needs_reply(window_days=params.inbox_window_days)
                unread_total = _fetch_inbox_unread_count()
                vip_emails = _fetch_vip_emails()

            ap_outstanding, ap_overdue = _fetch_ap_outstanding()

            if params.skip_relationships:
                stale = []
            else:
                stale = _fetch_stale_contacts(threshold_days=params.stale_threshold_days)

            brief = core.compose_brief(
                date=today,
                user_email=user_email,
                calendar_events=events,
                inbox_needs_reply=inbox,
                inbox_unread_count=unread_total,
                vip_emails=vip_emails,
                ap_outstanding=ap_outstanding,
                ap_overdue=ap_overdue,
                stale_contacts=stale,
            )

            log.info(
                "morning_brief: %d events, %d inbox needs-reply, "
                "%d AP outstanding (%d overdue), %d stale relationships",
                len(events), len(inbox),
                len(ap_outstanding), len(ap_overdue), len(stale),
            )

            payload = brief.to_dict()
            if params.output_format == "json":
                return json.dumps(payload, indent=2, default=str)
            md = core.render_markdown(brief)
            if params.output_format == "markdown":
                return md
            # both
            return json.dumps(
                {"json": payload, "markdown": md},
                indent=2, default=str,
            )
        except Exception as e:
            return format_error("workflow_morning_brief", e)
