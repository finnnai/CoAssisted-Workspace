# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP tool wrapper for the background scanner + P1 checks.

Exposes:
  - workflow_run_scanner       — run all due checks
  - workflow_run_scanner_check — force-run one check by name
  - workflow_list_scanner_checks — show registered checks + last-run state
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import gservices
import p1_checks
import scanner as core_scanner
import vendor_followups
from errors import format_error
from logging_util import log


def _gmail():
    return gservices.gmail()


def _people():
    return gservices.people()


# --------------------------------------------------------------------------- #
# Live fetchers — wired into p1_checks.register_p1_checks at import time.
# Each one returns the data shape the corresponding check_* function expects.
# --------------------------------------------------------------------------- #


def _fetch_recent_inbox(window_days: int = 3, limit: int = 50) -> list[dict]:
    """Pull recent inbox messages with metadata for the auto-snooze check."""
    try:
        svc = _gmail()
        q = f"is:inbox newer_than:{window_days}d"
        resp = svc.users().messages().list(
            userId="me", q=q, maxResults=limit,
        ).execute()
    except Exception as e:
        log.warning("scanner: inbox fetch failed: %s", e)
        return []
    out: list[dict] = []
    for m in resp.get("messages", []):
        try:
            full = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["From", "Subject"],
            ).execute()
        except Exception:
            continue
        headers = {h["name"]: h["value"]
                   for h in full.get("payload", {}).get("headers", [])}
        thread_id = full.get("threadId", m["id"])
        out.append({
            "id": m["id"],
            "threadId": thread_id,
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "snippet": full.get("snippet", ""),
            "link": f"https://mail.google.com/mail/u/0/#inbox/{thread_id}",
        })
    return out


def _fetch_stale_contacts(threshold_days: int = 60) -> list[dict]:
    try:
        svc = _people()
        results = svc.people().connections().list(
            resourceName="people/me",
            personFields="names,emailAddresses,userDefined",
            pageSize=2000,
        ).execute()
    except Exception as e:
        log.warning("scanner: stale contacts fetch failed: %s", e)
        return []
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    out: list[dict] = []
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
        out.append({
            "name": (names[0] or {}).get("displayName") if names else None,
            "email": ((emails[0] or {}).get("value") if emails else "") or "",
            "days_since_contact": days,
            "link": None,
        })
    return out


def _fetch_reciprocity_data() -> list[dict]:
    """Pull contacts with sent_last_60 / received_last_60 stats from People API."""
    try:
        svc = _people()
        results = svc.people().connections().list(
            resourceName="people/me",
            personFields="names,emailAddresses,userDefined",
            pageSize=2000,
        ).execute()
    except Exception as e:
        log.warning("scanner: reciprocity fetch failed: %s", e)
        return []
    out: list[dict] = []
    for p in results.get("connections", []):
        ud_map: dict[str, str] = {}
        for ud in p.get("userDefined", []) or []:
            ud_map[(ud.get("key") or "").lower()] = ud.get("value") or ""
        sent = ud_map.get("sent_last_60") or ud_map.get("sent_60d") or "0"
        recv = ud_map.get("received_last_60") or ud_map.get("received_60d") or "0"
        try:
            sent_i = int(sent)
            recv_i = int(recv)
        except ValueError:
            continue
        emails = p.get("emailAddresses", []) or []
        names = p.get("names", []) or []
        out.append({
            "name": (names[0] or {}).get("displayName") if names else None,
            "email": ((emails[0] or {}).get("value") if emails else "") or "",
            "sent_last_60": sent_i,
            "received_last_60": recv_i,
        })
    return out


def _fetch_send_later() -> list[dict]:
    """Read scheduled-send queue from vendor_followups (reuses the AP loop infra)."""
    # The send-later workflow stores entries in vendor_followups with channel="send_later".
    try:
        return [r for r in vendor_followups.list_open() if r.get("channel") == "send_later"]
    except Exception as e:
        log.warning("scanner: send-later fetch failed: %s", e)
        return []


def _fetch_week_ahead() -> tuple[list[dict], list[dict], list[dict]]:
    """Calendar events + parsed deadlines + open commitments for next 7 days."""
    cal = gservices.calendar()
    now = _dt.datetime.now().astimezone()
    end = now + _dt.timedelta(days=7)
    try:
        resp = cal.events().list(
            calendarId="primary",
            timeMin=now.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=100,
        ).execute()
        events = resp.get("items", [])
    except Exception as e:
        log.warning("scanner: week-ahead calendar fetch failed: %s", e)
        events = []
    # P1 v1: deadlines + open commitments come from the AP loop only.
    # Future P-phases will add Tasks API + decision-log integration.
    deadlines: list[dict] = []
    try:
        for r in vendor_followups.due_for_reminder():
            deadlines.append({
                "title": f"AP overdue: {r.get('vendor', '?')} — invoice {r.get('invoice_number', '?')}",
                "due_at": r.get("request_sent_at"),
                "link": None,
            })
    except Exception:
        pass
    open_commitments: list[dict] = []
    return events, deadlines, open_commitments


def _fetch_retention_threads(cutoff_days: int = 365) -> list[dict]:
    """Pull threads older than cutoff with financial-token subjects."""
    svc = _gmail()
    try:
        # Gmail's older_than: "1y" or "365d"
        q = f"older_than:{cutoff_days}d (invoice OR receipt OR wire OR payroll OR tax OR audit)"
        resp = svc.users().messages().list(
            userId="me", q=q, maxResults=30,
        ).execute()
    except Exception as e:
        log.warning("scanner: retention fetch failed: %s", e)
        return []
    out: list[dict] = []
    now = _dt.datetime.now(tz=_dt.timezone.utc)
    for m in resp.get("messages", []):
        try:
            full = svc.users().messages().get(
                userId="me", id=m["id"], format="metadata",
                metadataHeaders=["Subject", "Date"],
            ).execute()
        except Exception:
            continue
        headers = {h["name"]: h["value"]
                   for h in full.get("payload", {}).get("headers", [])}
        date_hdr = headers.get("Date")
        age_days = 0
        if date_hdr:
            try:
                from email.utils import parsedate_to_datetime
                msg_dt = parsedate_to_datetime(date_hdr)
                if msg_dt:
                    if msg_dt.tzinfo is None:
                        msg_dt = msg_dt.replace(tzinfo=_dt.timezone.utc)
                    age_days = max(0, (now - msg_dt).days)
            except Exception:
                pass
        thread_id = full.get("threadId", m["id"])
        out.append({
            "id": thread_id,
            "subject": headers.get("Subject", ""),
            "snippet": full.get("snippet", ""),
            "age_days": age_days,
            "link": f"https://mail.google.com/mail/u/0/#inbox/{thread_id}",
        })
    return out


def _fetch_end_of_day_data() -> tuple[list[dict], list[dict]]:
    """Unfinished tasks + unanswered threads for the EOD shutdown check."""
    tasks_data: list[dict] = []
    try:
        tsvc = gservices.tasks()
        lists = tsvc.tasklists().list().execute().get("items", [])
        for tl in lists:
            tasks = tsvc.tasks().list(tasklist=tl["id"], showCompleted=False).execute()
            for t in tasks.get("items", []):
                tasks_data.append({
                    "id": t["id"],
                    "title": t.get("title") or "",
                    "link": t.get("selfLink"),
                })
    except Exception as e:
        log.warning("scanner: tasks fetch failed: %s", e)

    # Unanswered: inbox threads from past 24h with no reply yet from the user.
    threads_data: list[dict] = []
    try:
        gsvc = _gmail()
        resp = gsvc.users().messages().list(
            userId="me", q="is:inbox is:unread newer_than:1d", maxResults=20,
        ).execute()
        for m in resp.get("messages", []):
            threads_data.append({"id": m.get("threadId", m["id"])})
    except Exception:
        pass
    return tasks_data, threads_data


# --------------------------------------------------------------------------- #
# Register all P1 checks at module load (no-op if scanner is not yet imported).
# --------------------------------------------------------------------------- #


_REGISTERED = False


def _ensure_registered() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    p1_checks.register_p1_checks(
        inbox_fetcher=_fetch_recent_inbox,
        contacts_fetcher=_fetch_stale_contacts,
        reciprocity_fetcher=_fetch_reciprocity_data,
        send_later_fetcher=_fetch_send_later,
        week_ahead_fetcher=_fetch_week_ahead,
        retention_fetcher=_fetch_retention_threads,
        end_of_day_fetcher=_fetch_end_of_day_data,
    )
    _REGISTERED = True


# --------------------------------------------------------------------------- #
# Pydantic input
# --------------------------------------------------------------------------- #


class RunScannerInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: Optional[str] = Field(
        default=None,
        description=("Run only this check by name (skip cadence). "
                     "Omit to run all due checks."),
    )


class ListChecksInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_run_scanner",
        annotations={
            "title": "Run the background scanner",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_run_scanner(params: RunScannerInput) -> str:
        """Run the background scanner.

        - With `name`: force-run that one check (skip cadence).
        - Without `name`: run every check whose cadence has elapsed.

        Returns JSON with what ran, what was skipped, and total alerts fired.
        """
        try:
            _ensure_registered()
            if params.name:
                result = core_scanner.run_one(params.name)
                return json.dumps(result.to_dict(), indent=2, default=str)
            result = core_scanner.run_due()
            log.info(
                "scanner: ran=%d skipped=%d alerts=%d",
                len(result["ran"]), len(result["skipped"]), result["total_alerts"],
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_run_scanner", e)

    @mcp.tool(
        name="workflow_list_scanner_checks",
        annotations={
            "title": "List registered scanner checks",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def workflow_list_scanner_checks(params: ListChecksInput) -> str:
        """List every registered scanner check with its cadence + last-run state."""
        try:
            _ensure_registered()
            checks = core_scanner.list_checks()
            return json.dumps({"checks": checks, "count": len(checks)},
                              indent=2, default=str)
        except Exception as e:
            return format_error("workflow_list_scanner_checks", e)
