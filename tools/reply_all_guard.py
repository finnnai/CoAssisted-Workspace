# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP tool wrapper for the reply-all guard.

Exposes one tool: gmail_check_reply_all.
The pure-logic core lives in reply_all_guard.py at the project root —
this file only handles MCP plumbing + draft hydration from the Gmail API.
"""

from __future__ import annotations

import base64
import json
import re
from email import message_from_bytes
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import gservices
import reply_all_guard as core
from errors import format_error
from logging_util import log


def _service():
    return gservices.gmail()


# --------------------------------------------------------------------------- #
# Helpers — pull a draft's body + headers from Gmail
# --------------------------------------------------------------------------- #


def _split_addrs(header: str | None) -> list[str]:
    """Split a comma-separated address header into individual entries."""
    if not header:
        return []
    # Split on top-level commas; respect quoted display names containing commas.
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in header:
        if ch == '"':
            depth = 1 - depth
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def _hydrate_draft(draft_id: str) -> dict:
    """Pull a draft from Gmail and return body + recipient headers + sender."""
    svc = _service()
    full = (
        svc.users()
        .drafts()
        .get(userId="me", id=draft_id, format="raw")
        .execute()
    )
    raw_b64 = full.get("message", {}).get("raw", "")
    raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
    msg = message_from_bytes(raw_bytes)

    # Find the plain-text body. Prefer text/plain; fall back to stripped HTML.
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                payload = part.get_payload(decode=True) or b""
                body = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    payload = part.get_payload(decode=True) or b""
                    html = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                    body = re.sub(r"<[^>]+>", "", html)
                    break
    else:
        payload = msg.get_payload(decode=True) or b""
        body = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")

    return {
        "subject": msg.get("Subject", ""),
        "to": _split_addrs(msg.get("To")),
        "cc": _split_addrs(msg.get("Cc")),
        "from": msg.get("From", ""),
        "body": body,
    }


# --------------------------------------------------------------------------- #
# Pydantic input — accept EITHER draft_id OR inline (to/cc/body)
# --------------------------------------------------------------------------- #


class CheckReplyAllInput(BaseModel):
    """Inputs for gmail_check_reply_all.

    Provide EITHER `draft_id` (preferred — pulls everything from Gmail) OR
    the inline triple (`to`, `body`, optional `cc`).
    """

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    draft_id: Optional[str] = Field(
        default=None,
        description="Gmail draft ID to analyze. If provided, all other content fields are ignored.",
    )
    to: Optional[list[str]] = Field(
        default=None,
        description="To: recipients. Required if draft_id is not provided.",
    )
    cc: Optional[list[str]] = Field(
        default=None,
        description="CC: recipients (optional).",
    )
    body: Optional[str] = Field(
        default=None,
        description="Plain-text body. Required if draft_id is not provided.",
    )
    sender: Optional[str] = Field(
        default=None,
        description="Your own address (excluded from addressed-recipient detection).",
    )


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="gmail_check_reply_all",
        annotations={
            "title": "Check a draft for unnecessary reply-all",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gmail_check_reply_all(params: CheckReplyAllInput) -> str:
        """Score a draft email for likely unnecessary reply-all before sending.

        Detection signals (P0):
          - Single-target greeting (body greets one person but goes to many)
          - Ack-only body (short / FYI / "thanks!" / "+1")
          - FYI content
          - CC-fanout (1 To, many CC)

        Returns JSON with:
          - verdict: "safe" | "warn" | "block"
          - signals: list of fired detectors with severity
          - suggested_to / suggested_cc: pruned recipient lists
          - addressed_recipient: the one person greeted by name (if any)

        Use this BEFORE calling gmail_send_email to catch reply-all mistakes.
        """
        try:
            # Hydrate either from draft_id or use inline inputs.
            if params.draft_id:
                draft = _hydrate_draft(params.draft_id)
                body = draft["body"]
                to = draft["to"]
                cc = draft["cc"]
                sender = params.sender or draft["from"]
            else:
                if not params.to or not params.body:
                    return format_error(
                        "gmail_check_reply_all",
                        ValueError("Provide either draft_id, or both to and body."),
                    )
                body = params.body
                to = params.to
                cc = params.cc or []
                sender = params.sender

            verdict = core.score_draft(body=body, to=to, cc=cc, sender=sender)
            payload = verdict.to_dict()
            payload["analyzed"] = {
                "to_count": len(to),
                "cc_count": len(cc),
                "body_word_count": len((body or "").split()),
                "from_draft": bool(params.draft_id),
            }
            log.info(
                "gmail_check_reply_all → %s (%d signals)",
                verdict.verdict,
                len(verdict.signals),
            )
            return json.dumps(payload, indent=2)
        except Exception as e:
            return format_error("gmail_check_reply_all", e)
