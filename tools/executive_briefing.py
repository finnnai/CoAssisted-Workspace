# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP wrappers for the daily standup.

Tools:
  workflow_executive_briefing               — generate (and optionally send) the brief
  workflow_briefing_execute_action    — fire one queued action token
  workflow_briefing_list_pending      — list pending action tokens
"""

from __future__ import annotations

import base64
import datetime as _dt
import email.utils
import json
from email.message import EmailMessage
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import briefing_actions
import briefing_dispatcher
import briefing_webhook
import executive_briefing as core
import gservices
import news_feed
import weather as _weather
from errors import format_error
from logging_util import log


# --------------------------------------------------------------------------- #
# Live fetchers
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


def _today_window(tz: _dt.tzinfo) -> tuple[str, str]:
    now = _dt.datetime.now(tz=tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + _dt.timedelta(days=1) - _dt.timedelta(seconds=1)
    return start.isoformat(), end.isoformat()


def _fetch_today_events(calendar_id: str = "primary") -> list[dict]:
    tz = _dt.datetime.now().astimezone().tzinfo
    time_min, time_max = _today_window(tz)
    resp = (
        _calendar().events().list(
            calendarId=calendar_id, timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy="startTime", maxResults=50,
        ).execute()
    )
    return resp.get("items", [])


def _fetch_inbound_threads(window_days: int, limit: int) -> list[dict]:
    """Fetch inbox threads that look like they need a reply."""
    svc = _gmail()
    q = (
        f"is:inbox is:unread newer_than:{window_days}d "
        "-from:noreply -from:no-reply -category:promotions "
        "-category:updates -category:forums -category:social"
    )
    resp = svc.users().messages().list(
        userId="me", q=q, maxResults=limit,
    ).execute()
    msg_ids = [m["id"] for m in resp.get("messages", [])]
    seen_threads: set[str] = set()
    out: list[dict] = []
    for mid in msg_ids:
        try:
            full = svc.users().messages().get(
                userId="me", id=mid, format="metadata",
                metadataHeaders=["From", "Subject"],
            ).execute()
        except Exception:
            continue
        thread_id = full.get("threadId", mid)
        if thread_id in seen_threads:
            continue
        seen_threads.add(thread_id)
        headers = {h["name"]: h["value"]
                   for h in full.get("payload", {}).get("headers", [])}
        from_hdr = headers.get("From", "")
        subject = headers.get("Subject", "")
        # Parse name + email
        sender_name, sender_email = email.utils.parseaddr(from_hdr)
        out.append({
            "thread_id": thread_id,
            "sender_name": sender_name or sender_email,
            "sender_email": sender_email,
            "subject": subject,
            "snippet": full.get("snippet", "") or "",
            "body": full.get("snippet", "") or "",  # body for compose context
        })
    return out


def _fetch_active_tasks(limit: int) -> list[dict]:
    """Pull active (incomplete) Google Tasks."""
    out: list[dict] = []
    try:
        tsvc = _tasks()
        lists = tsvc.tasklists().list().execute().get("items", [])
        for tl in lists:
            tasks = tsvc.tasks().list(
                tasklist=tl["id"], showCompleted=False, maxResults=limit,
            ).execute()
            for t in tasks.get("items", []):
                out.append({
                    "task_id": t["id"],
                    "tasklist_id": tl["id"],
                    "title": t.get("title", "") or "",
                    "notes": t.get("notes", "") or "",
                    "due_iso": t.get("due"),
                })
    except Exception as e:
        log.warning("executive_briefing: tasks fetch failed: %s", e)
    return out[:limit]


def _stage_gmail_draft(item_dict: dict, drafted_reply: str,
                      sender_authed: str | None) -> Optional[str]:
    """Create a Gmail draft so 'Approve Send' = open + click send. Returns draft ID."""
    try:
        msg = EmailMessage()
        msg["To"] = item_dict["sender_email"]
        if sender_authed:
            msg["From"] = sender_authed
        original_subject = item_dict.get("subject", "")
        if original_subject and not original_subject.lower().startswith("re:"):
            msg["Subject"] = f"Re: {original_subject}"
        else:
            msg["Subject"] = original_subject or "Re: your message"
        msg.set_content(drafted_reply or "")
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        # Optionally attach to original thread
        body = {"message": {"raw": raw, "threadId": item_dict.get("thread_id")}}
        resp = _gmail().users().drafts().create(userId="me", body=body).execute()
        return resp.get("id")
    except Exception as e:
        log.warning("executive_briefing: draft staging failed for %s: %s",
                    item_dict.get("thread_id"), e)
        return None


def _resolve_weather_location(events: list[dict]) -> str:
    """Auto-detect: try _resolve_current_location, else first event's location, else SF."""
    try:
        from tools.maps import _resolve_current_location  # type: ignore
        loc = _resolve_current_location()
        if loc:
            return loc
    except Exception:
        pass
    for e in events:
        if e.get("location"):
            return e["location"]
    return "San Francisco, CA"


def _format_event_start(start_iso: str) -> str:
    """Format calendar event start to '9:00 AM' style local time."""
    if not start_iso:
        return ""
    try:
        dt = _dt.datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        return dt.strftime("%-I:%M %p")
    except (ValueError, AttributeError):
        return start_iso[11:16] if len(start_iso) >= 16 else start_iso


# --------------------------------------------------------------------------- #
# workflow_executive_briefing
# --------------------------------------------------------------------------- #


class ExecutiveBriefingInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    deliver: str = Field(
        default="generate",
        description="'send' to email it; 'generate' to return without sending.",
    )
    recipient: Optional[str] = Field(
        default=None,
        description="Override recipient. Default: authenticated user.",
    )
    inbox_window_days: int = Field(default=14, ge=1, le=60)
    inbox_limit: int = Field(default=10, ge=1, le=30)
    task_limit: int = Field(default=15, ge=1, le=50)
    news_limit: int = Field(
        default=5, ge=0, le=12,
        description="Number of World News cards. 0 disables the news column.",
    )
    news_rss_url: Optional[str] = Field(
        default=None,
        description=("Override RSS feed URL. Default: config['news_rss_url'] "
                     "or BBC World."),
    )
    weather_location: Optional[str] = Field(
        default=None,
        description=("Override weather location. Default: auto-detect via "
                     "_resolve_current_location, fall back to first event's "
                     "location, then 'San Francisco, CA'."),
    )
    stage_drafts: bool = Field(
        default=True,
        description="Create Gmail drafts for each email so 'Approve Send' is one click.",
    )
    greeting_name: Optional[str] = Field(
        default=None, description="Override the 'Good morning, X' name.",
    )


def register(mcp) -> None:
    # Boot the local webhook server (idempotent — second call is a no-op).
    # Buttons in the standup email link to http://127.0.0.1:7799/briefing/action
    # and dispatch through briefing_dispatcher.dispatch().
    try:
        briefing_webhook.start()
    except Exception as e:
        log.warning("standup webhook failed to start: %s", e)

    @mcp.tool(
        name="workflow_executive_briefing",
        annotations={"title": "Generate the daily standup",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    async def workflow_executive_briefing(params: ExecutiveBriefingInput) -> str:
        """Compose + (optionally send) the daily standup.

        Pulls today's calendar, inbox threads needing reply, and active tasks.
        Pre-stages a brand-voice reply draft for each email (so 'Approve Send'
        is one click in Gmail). Builds an action-token queue for every button
        and renders both an HTML email + JSON spec.

        Set deliver='send' to actually send to the user's inbox; 'generate'
        returns the rendered output for inspection.
        """
        try:
            user_email = _authed_email() or "user"
            recipient = params.recipient or user_email
            today = _dt.datetime.now().astimezone().date().isoformat()
            greeting_name = params.greeting_name or _greeting_from_email(user_email)

            # ---- Fetch source data --------------------------------------- #
            events = _fetch_today_events()
            log.info("executive_briefing: %d events today", len(events))

            inbound = _fetch_inbound_threads(
                window_days=params.inbox_window_days, limit=params.inbox_limit,
            )
            log.info("executive_briefing: %d inbound threads", len(inbound))

            tasks = _fetch_active_tasks(params.task_limit)
            log.info("executive_briefing: %d active tasks", len(tasks))

            # ---- Weather --------------------------------------------------- #
            location = params.weather_location or _resolve_weather_location(events)
            try:
                forecast = _weather.get_today_forecast(location)
            except Exception as e:
                log.warning("executive_briefing: weather fetch failed: %s", e)
                forecast = None

            # ---- Compose drafted replies for inbox items ----------------- #
            try:
                import brand_voice
                composer_available, _ = (True, "")
                # Use template path by default — the LLM path runs only if
                # ANTHROPIC_API_KEY is configured.
            except Exception:
                composer_available = False

            email_items: list[core.EmailItem] = []
            for t in inbound:
                drafted_reply = ""
                if composer_available:
                    try:
                        req = brand_voice.DraftRequest(
                            intent="reply", audience="customer",
                            recipient_name=t["sender_name"],
                            sender_name=greeting_name,
                            subject_hint=t["subject"],
                            context=t.get("body", ""),
                            seed_hint=t["thread_id"],
                        )
                        out = brand_voice.compose(req)
                        drafted_reply = out.plain
                    except Exception as e:
                        log.warning("executive_briefing: compose failed: %s", e)

                draft_id = None
                if params.stage_drafts and drafted_reply:
                    draft_id = _stage_gmail_draft(t, drafted_reply, user_email)

                email_items.append(core.EmailItem(
                    thread_id=t["thread_id"],
                    sender_name=t["sender_name"],
                    sender_email=t["sender_email"],
                    subject=t["subject"],
                    snippet=t["snippet"],
                    drafted_reply=drafted_reply,
                    draft_id=draft_id,
                ))

            # ---- Build meeting items -------------------------------------- #
            meeting_items: list[core.MeetingItem] = []
            for e in events:
                start = e.get("start") or {}
                end = e.get("end") or {}
                start_iso = start.get("dateTime") or start.get("date") or ""
                end_iso = end.get("dateTime") or end.get("date") or ""
                attendees = e.get("attendees") or []
                organizer = (e.get("organizer") or {}).get("email", "").lower()
                meeting_items.append(core.MeetingItem(
                    event_id=e.get("id", ""),
                    summary=e.get("summary", "(no title)"),
                    start_iso=start_iso,
                    end_iso=end_iso,
                    start_label=_format_event_start(start_iso),
                    location=e.get("location") or "",
                    attendee_count=len(attendees),
                    is_organizer=(organizer == user_email.lower() if user_email else False),
                ))

            # ---- Build task items ----------------------------------------- #
            task_items = [core.TaskItem(**t) for t in tasks]

            # ---- Fetch news ----------------------------------------------- #
            news_items: list[dict] = []
            if params.news_limit > 0:
                try:
                    news_items = news_feed.get_top_news(
                        limit=params.news_limit,
                        rss_url=params.news_rss_url,
                    )
                except Exception as e:
                    log.warning("executive_briefing: news fetch failed: %s", e)
            log.info("executive_briefing: %d news items", len(news_items))

            # ---- Compose briefing + render ------------------------------- #
            brief = core.compose_briefing(
                date=today,
                greeting_name=greeting_name,
                user_email=user_email,
                weather_forecast=forecast,
                email_items=email_items,
                meeting_items=meeting_items,
                task_items=task_items,
                news_items=news_items,
            )
            html_body = core.render_email_html(brief)
            payload = brief.to_dict()

            # ---- Optionally send ------------------------------------------ #
            #
            # Send pattern (new):
            #   - Body: narrative-prose summary (plain-text + light HTML).
            #     Text-only — no images, no fancy layout, so Gmail's external-
            #     image blocker can't hide anything.
            #   - Attachment: the full interactive HTML brief as
            #     `executive-briefing-<date>.html`. Opens in the user's browser
            #     where every image, weather chart, action button, and tab
            #     works without restriction.
            sent_info = None
            if params.deliver == "send":
                try:
                    msg = EmailMessage()
                    msg["To"] = recipient
                    msg["From"] = user_email
                    msg["Subject"] = (
                        f"Executive Briefing · {today} · "
                        f"{brief.summary_line()}"
                    )
                    # Body: narrative summary (text + lightweight HTML)
                    msg.set_content(_narrative_summary_text(brief))
                    msg.add_alternative(
                        _narrative_summary_html(brief), subtype="html",
                    )
                    # Attachment: the full interactive brief
                    attachment_filename = f"executive-briefing-{today}.html"
                    msg.add_attachment(
                        html_body.encode("utf-8"),
                        maintype="text", subtype="html",
                        filename=attachment_filename,
                    )
                    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
                    resp = _gmail().users().messages().send(
                        userId="me", body={"raw": raw},
                    ).execute()
                    sent_info = {
                        "message_id": resp.get("id"),
                        "thread_id": resp.get("threadId"),
                        "attachment": attachment_filename,
                    }
                    log.info(
                        "executive_briefing: sent to %s (msg %s, attached %s)",
                        recipient, resp.get("id"), attachment_filename,
                    )
                except Exception as e:
                    log.warning("executive_briefing: send failed: %s", e)
                    sent_info = {"error": str(e)}

            return json.dumps({
                "delivered": params.deliver == "send",
                "sent": sent_info,
                "html_body_len": len(html_body),
                "html_body": html_body,
                "json": payload,
            }, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_executive_briefing", e)

    @mcp.tool(
        name="workflow_briefing_execute_action",
        annotations={"title": "Execute one queued briefing action token",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    async def workflow_briefing_execute_action(params: ExecuteActionInput) -> str:
        """Execute a briefing action by token. Use this when the user clicks
        an action button or runs `execute brief <token>` in chat.
        """
        try:
            token = params.token
            rec = briefing_actions.get(token)
            if not rec:
                return format_error(
                    "workflow_briefing_execute_action",
                    ValueError(f"unknown token: {token}"),
                )
            if rec["status"] != "pending":
                return json.dumps({
                    "token": token, "skipped": True,
                    "status": rec["status"], "result": rec.get("result"),
                })

            # Delegate to the shared dispatcher (also used by the webhook).
            return json.dumps(
                briefing_dispatcher.dispatch(
                    token, body_override=params.body_override,
                ),
                indent=2, default=str,
            )
        except Exception as e:
            return format_error("workflow_briefing_execute_action", e)

    @mcp.tool(
        name="workflow_briefing_list_pending",
        annotations={"title": "List pending briefing action tokens",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_briefing_list_pending(params: ListPendingInput) -> str:
        try:
            briefing_actions.expire_old()
            rows = briefing_actions.list_pending(kind=params.kind)
            return json.dumps({"count": len(rows), "actions": rows},
                              indent=2, default=str)
        except Exception as e:
            return format_error("workflow_briefing_list_pending", e)


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


class ExecuteActionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    token: str = Field(..., description="The action token (8 chars).")
    body_override: Optional[str] = Field(
        default=None,
        description=("For approve_send tokens: edited reply body. Updates "
                     "the staged draft before firing send."),
    )


class ListPendingInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    kind: Optional[str] = Field(
        default=None,
        description="Filter by kind (approve_send, accept_meeting, etc).",
    )


# Action helpers were moved to briefing_dispatcher.py so the local webhook
# (briefing_webhook.py) can dispatch without importing this MCP-level module.

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _greeting_from_email(email: str) -> str:
    if not email:
        return "Finn"
    local = email.split("@", 1)[0]
    first = local.split(".", 1)[0].split("+", 1)[0]
    return first.capitalize() or "Finn"


def _format_long_date(iso_date: str) -> str:
    """'2026-04-29' → 'Wednesday, April 29, 2026' — falls back to the input."""
    try:
        d = _dt.date.fromisoformat(iso_date)
    except (ValueError, TypeError):
        return iso_date
    return d.strftime("%A, %B %-d, %Y")


def _meeting_clauses(meetings: list) -> list[str]:
    """Build narrative clauses for a meeting list, e.g.
       'a 1:1 with Alex (CTO) at 9:00 AM'.
    """
    out: list[str] = []
    for m in meetings:
        title = (m.summary or "").strip() or "an untitled block"
        when = (m.start_label or "").strip()
        if when:
            out.append(f"{title} at {when}")
        else:
            out.append(title)
    return out


def _meetings_narrative(meetings: list) -> str:
    """Compose a one-sentence narrative for the day's meeting cadence."""
    if not meetings:
        return "Calendar is clear today — no meetings on the books."
    clauses = _meeting_clauses(meetings)
    n = len(clauses)
    if n == 1:
        return f"One meeting today: {clauses[0]}."
    if n == 2:
        return (f"Two meetings on deck: {clauses[0]}, then {clauses[1]}.")
    if n == 3:
        return (f"Three meetings shape the day — {clauses[0]}, "
                f"followed by {clauses[1]}, and wrapping with {clauses[2]}.")
    head = ", ".join(clauses[:-1])
    return (f"{n} meetings shape the day: {head}, and then {clauses[-1]}.")


def _emails_narrative(emails: list) -> str:
    if not emails:
        return "Inbox is clean — nothing flagged for reply."
    n = len(emails)
    senders = []
    for e in emails[:3]:
        nm = (e.sender_name or "").strip() or e.sender_email or "unknown"
        senders.append(nm)
    if n == 1:
        return f"One thread is waiting on a reply: {senders[0]}."
    head = f"{n} threads are waiting on a reply"
    if senders:
        if len(senders) == 1:
            return f"{head}, including a note from {senders[0]}."
        if len(senders) == 2:
            return f"{head}, including notes from {senders[0]} and {senders[1]}."
        return (f"{head} — top of the stack are {senders[0]}, "
                f"{senders[1]}, and {senders[2]}.")
    return f"{head}."


def _tasks_narrative(tasks: list) -> str:
    if not tasks:
        return "No active tasks pulling at you."
    n = len(tasks)
    titles = [(t.title or "").strip() for t in tasks[:3] if (t.title or "").strip()]
    if not titles:
        return f"{n} active tasks open."
    if n == 1:
        return f"One active task: {titles[0]}."
    head = f"{n} active tasks open"
    if len(titles) == 2:
        return f"{head} — {titles[0]} and {titles[1]}."
    return f"{head} — top of the list are {titles[0]}, {titles[1]}, and {titles[2]}."


def _weather_narrative(brief: core.ExecutiveBriefing) -> str:
    w = brief.weather
    if not w:
        return ""
    loc = w.location_label or "your area"
    return f"Weather in {loc}: {w.summary}."


def _news_narrative(news: list) -> str:
    if not news:
        return ""
    titles = [(n.get("title") or "").strip() for n in news[:3]]
    titles = [t for t in titles if t]
    if not titles:
        return ""
    if len(titles) == 1:
        return f"In the news: \"{titles[0]}.\""
    if len(titles) == 2:
        return f"In the news: \"{titles[0]}\" and \"{titles[1]}.\""
    return (f"In the news: \"{titles[0]},\" \"{titles[1]},\" "
            f"and \"{titles[2]}.\"")


def _narrative_summary_text(brief: core.ExecutiveBriefing) -> str:
    """Prose-style plain-text summary suitable for the email body.

    Reads like a colleague briefing you on the day rather than a bullet list.
    The full interactive brief (charts, draft editor, action buttons) ships
    as an HTML attachment so this body stays clean.
    """
    long_date = _format_long_date(brief.date)
    greeting = (
        f"Good {core._greeting_word().capitalize()}, {brief.greeting_name}."
    )
    opener = (
        f"{greeting} Here's your Executive Briefing for {long_date}."
    )
    paras: list[str] = [opener]
    weather = _weather_narrative(brief)
    if weather:
        paras.append(weather)
    paras.append(_meetings_narrative(brief.meetings))
    paras.append(_emails_narrative(brief.emails))
    paras.append(_tasks_narrative(brief.tasks))
    news = _news_narrative(brief.news)
    if news:
        paras.append(news)
    paras.append(
        "The full interactive briefing — with the weather chart, editable "
        "drafted replies, action buttons for emails and meetings, and the "
        "news tab — is attached as an HTML file. Open it in your browser "
        "to interact."
    )
    return "\n\n".join(paras) + "\n"


def _narrative_summary_html(brief: core.ExecutiveBriefing) -> str:
    """Lightweight HTML version of the narrative — text only, no images.

    Mirrors `_narrative_summary_text` paragraphs so MIME alternatives
    stay in lockstep. Inline styles only, no external assets.
    """
    text = _narrative_summary_text(brief)
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    body = "".join(
        f'<p style="margin:0 0 14px 0;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:14px;line-height:1.55;color:#181a1f;">'
        f'{html_escape(p)}</p>'
        for p in paras
    )
    return (
        f'<div style="max-width:640px;margin:0 auto;padding:18px;">'
        f'{body}'
        f'<p style="margin:24px 0 0 0;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:11px;color:#6a7079;">'
        f'CoAssisted Workspace · Executive Briefing v0.7'
        f'</p></div>'
    )


# Local html.escape import to avoid the top-level `import html` collision
# with the email-policy `html_body` variable above.
def html_escape(s: str) -> str:
    import html as _h
    return _h.escape(s or "", quote=True)
