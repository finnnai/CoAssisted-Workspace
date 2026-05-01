# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Daily-standup action dispatcher — separate from the MCP wrapper so the
local webhook can call it without circular imports.

The MCP wrapper at tools/executive_briefing.py just calls dispatch(token).
The webhook at briefing_webhook.py also calls dispatch(token).
Both paths share the same execution semantics + state transitions.
"""

from __future__ import annotations

import base64
import datetime as _dt
from email.message import EmailMessage

import briefing_actions
import gservices
from logging_util import log


def dispatch(token: str, *, body_override: str | None = None) -> dict:
    """Execute the action behind a token. Returns {result} or {error}.

    `body_override` is honored for approve_send — when present, the staged
    Gmail draft is updated with this body before sending. Used by the
    inline-edit form in the standup email.

    Idempotent — re-running an already-executed token returns the cached result.
    """
    rec = briefing_actions.get(token)
    if not rec:
        return {"error": f"unknown token: {token}", "token": token}
    if rec["status"] != "pending":
        return {"token": token, "status": rec["status"],
                "result": rec.get("result"), "skipped": True}

    if body_override is not None:
        rec.setdefault("payload", {})["body_override"] = body_override

    result = _dispatch_action(rec)
    if "error" in result:
        briefing_actions.mark_failed(token, result["error"])
    else:
        briefing_actions.mark_executed(token, result)
    log.info("standup action %s (%s) → %s",
             token, rec["kind"],
             "ok" if "error" not in result else f"err: {result['error']}")
    return {"token": token, "kind": rec["kind"], "result": result}


def _dispatch_action(rec: dict) -> dict:
    kind = rec["kind"]
    payload = rec.get("payload") or {}
    try:
        if kind == "approve_send":
            return _action_approve_send(payload)
        if kind == "schedule_send":
            return _action_schedule_send(payload)
        if kind == "mark_read":
            return _action_mark_read(payload)
        if kind == "mark_as_task":
            return _action_mark_as_task(payload)
        if kind == "accept_meeting":
            return _action_respond_meeting(payload, "accepted")
        if kind == "decline_meeting":
            return _action_respond_meeting(payload, "declined")
        if kind == "suggest_new_time":
            return _action_suggest_new_time(payload)
        if kind == "complete_task":
            return _action_complete_task(payload)
        if kind == "ignore_task":
            return {"ignored": True, "task_id": payload.get("task_id")}
        if kind == "schedule_to_calendar":
            return _action_schedule_to_calendar(payload)
        return {"error": f"unhandled kind: {kind}"}
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------------------------------- #
# Service builders (lazy so test/import paths don't trigger OAuth)
# --------------------------------------------------------------------------- #


def _gmail():
    return gservices.gmail()


def _calendar():
    return gservices.calendar()


def _tasks():
    return gservices.tasks()


def _authed_email() -> str | None:
    try:
        prof = _gmail().users().getProfile(userId="me").execute()
        return prof.get("emailAddress", "").lower() or None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Action implementations
# --------------------------------------------------------------------------- #


def _action_approve_send(payload: dict) -> dict:
    draft_id = payload.get("draft_id")
    body_override = (payload.get("body_override") or "").strip()
    if not draft_id:
        return {"error": "no draft_id; nothing to send"}

    if body_override:
        # Update the draft's body before sending. Pull existing headers so
        # we preserve To, Subject, Cc, threading etc.
        try:
            full = _gmail().users().drafts().get(
                userId="me", id=draft_id, format="raw",
            ).execute()
            existing_raw_b64 = full.get("message", {}).get("raw", "")
            existing_raw = base64.urlsafe_b64decode(
                existing_raw_b64.encode("ascii")
            )
            from email import message_from_bytes
            existing_msg = message_from_bytes(existing_raw)
            new_msg = EmailMessage()
            for hdr in ("To", "Cc", "Bcc", "Subject", "From",
                        "In-Reply-To", "References"):
                v = existing_msg.get(hdr)
                if v:
                    new_msg[hdr] = v
            new_msg.set_content(body_override)
            new_raw = base64.urlsafe_b64encode(new_msg.as_bytes()).decode("ascii")
            thread_id = full.get("message", {}).get("threadId")
            update_body = {"message": {"raw": new_raw}}
            if thread_id:
                update_body["message"]["threadId"] = thread_id
            _gmail().users().drafts().update(
                userId="me", id=draft_id, body=update_body,
            ).execute()
            log.info("approve_send: updated draft %s with edited body "
                     "(%d chars)", draft_id, len(body_override))
        except Exception as e:
            return {"error": f"failed to apply body override: {e}"}

    resp = _gmail().users().drafts().send(
        userId="me", body={"id": draft_id},
    ).execute()
    return {"sent": True, "message_id": resp.get("id"),
            "via": "draft", "draft_id": draft_id,
            "body_overridden": bool(body_override)}


def _action_schedule_send(payload: dict) -> dict:
    draft_id = payload.get("draft_id")
    when = payload.get("default_send_at_local", "09:00")
    return {
        "scheduled": True,
        "draft_id": draft_id,
        "send_at_local": when,
        "note": ("Draft remains in your Gmail Drafts folder. v1 doesn't auto-fire "
                 "scheduled sends; click 'Approve & Send' or open the draft when "
                 "ready."),
    }


def _action_mark_read(payload: dict) -> dict:
    thread_id = payload.get("thread_id")
    _gmail().users().threads().modify(
        userId="me", id=thread_id, body={"removeLabelIds": ["UNREAD"]},
    ).execute()
    return {"marked_read": True, "thread_id": thread_id}


def _action_mark_as_task(payload: dict) -> dict:
    title = payload.get("title", "Reply needed")
    notes = payload.get("link", "")
    tlists = _tasks().tasklists().list().execute().get("items", [])
    if not tlists:
        return {"error": "no task lists"}
    tasklist_id = tlists[0]["id"]
    body = {"title": title, "notes": notes}
    resp = _tasks().tasks().insert(tasklist=tasklist_id, body=body).execute()
    return {"task_id": resp.get("id"), "tasklist_id": tasklist_id}


def _action_respond_meeting(payload: dict, response: str) -> dict:
    event_id = payload.get("event_id")
    user = _authed_email()
    if not user:
        return {"error": "could not resolve auth user"}
    cal = _calendar()
    event = cal.events().get(calendarId="primary", eventId=event_id).execute()
    attendees = event.get("attendees", []) or []
    found = False
    for a in attendees:
        if (a.get("email") or "").lower() == user.lower():
            a["responseStatus"] = response
            found = True
            break
    if not found:
        attendees.append({"email": user, "responseStatus": response, "self": True})
    cal.events().patch(
        calendarId="primary", eventId=event_id,
        body={"attendees": attendees}, sendUpdates="all",
    ).execute()
    return {"event_id": event_id, "response": response}


def _action_suggest_new_time(payload: dict) -> dict:
    import brand_voice
    event_id = payload.get("event_id")
    cal = _calendar()
    event = cal.events().get(calendarId="primary", eventId=event_id).execute()
    organizer = (event.get("organizer") or {}).get("email", "")
    if not organizer:
        return {"error": "no organizer email"}
    req = brand_voice.DraftRequest(
        intent="rsvp_alternative", audience="internal_peer",
        recipient_name=(event.get("organizer") or {}).get("displayName"),
        subject_hint=event.get("summary"),
        context="I have a conflict at the proposed time and want to suggest alternates.",
    )
    out = brand_voice.compose(req)
    msg = EmailMessage()
    msg["To"] = organizer
    msg["Subject"] = f"Re: {event.get('summary', 'meeting')}"
    msg.set_content(out.plain)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    drft = _gmail().users().drafts().create(
        userId="me", body={"message": {"raw": raw}},
    ).execute()
    return {"draft_id": drft.get("id"), "to": organizer,
            "open_url": f"https://mail.google.com/mail/u/0/#drafts/{drft.get('id')}"}


def _action_complete_task(payload: dict) -> dict:
    tsvc = _tasks()
    task_id = payload.get("task_id")
    tasklist_id = payload.get("tasklist_id")
    body = {"id": task_id, "status": "completed"}
    resp = tsvc.tasks().patch(
        tasklist=tasklist_id, task=task_id, body=body,
    ).execute()
    return {"completed": True, "task_id": task_id, "result": resp}


def _action_schedule_to_calendar(payload: dict) -> dict:
    title = payload.get("title", "Task")
    notes = payload.get("notes", "")
    duration_min = int(payload.get("default_duration_min") or 30)
    now = _dt.datetime.now().astimezone()
    start = now.replace(minute=0, second=0, microsecond=0) + _dt.timedelta(hours=1)
    end = start + _dt.timedelta(minutes=duration_min)
    body = {
        "summary": title,
        "description": notes,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    resp = _calendar().events().insert(calendarId="primary", body=body).execute()
    return {"event_id": resp.get("id"),
            "html_link": resp.get("htmlLink"),
            "start_iso": start.isoformat()}
