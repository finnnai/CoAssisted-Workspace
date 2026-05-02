# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP tool wrapper for the draft queue + brand voice composer.

Exposes:
  - workflow_compose_draft     — compose + queue (most callers use this)
  - workflow_list_drafts       — list pending drafts
  - workflow_approve_draft     — approve + send via Gmail
  - workflow_discard_draft     — discard a pending draft
  - workflow_edit_draft        — edit subject/body of a pending draft
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import brand_voice
import draft_queue as core
import gservices
from errors import format_error
from logging_util import log


def _gmail():
    return gservices.gmail()


def _send_email(to: list[str], cc: list[str], subject: str,
                plain: str, html: str) -> dict:
    """Send a queued draft via Gmail. Returns the API response."""
    from email.message import EmailMessage
    import base64

    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject
    msg.set_content(plain)
    if html:
        msg.add_alternative(html, subtype="html")
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return _gmail().users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()


# --------------------------------------------------------------------------- #
# compose_draft
# --------------------------------------------------------------------------- #


class ComposeDraftInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    intent: str = Field(
        ...,
        description=("Draft intent: 'reply', 'decline', 'nudge', 'acknowledge', "
                     "'agenda', 'intro_followup', 'birthday', 'rsvp_alternative', "
                     "'translate_reply', 'scheduling_poll'."),
    )
    audience: str = Field(
        default="customer",
        description=("'customer', 'vendor', 'employee', 'internal_peer', or 'personal'."),
    )
    target: str | list[str] = Field(
        ..., description="Recipient email or list of emails.",
    )
    cc: Optional[list[str]] = Field(default=None)
    recipient_name: Optional[str] = Field(default=None)
    subject_hint: Optional[str] = Field(
        default=None, description="Subject line of the original message (for 'Re:'-style replies).",
    )
    context: str = Field(
        default="", description="Inbound message body, situation summary, or other context.",
    )
    facts: Optional[dict] = Field(
        default=None,
        description="Structured facts to weave in (e.g. {'amount': '$5000', 'due': '2026-04-30'}).",
    )
    constraints: Optional[list[str]] = Field(
        default=None, description="Hard requirements: 'must mention X', 'avoid Y', etc.",
    )
    target_language: Optional[str] = Field(
        default=None, description="ISO code (e.g. 'fr', 'es') for translate_reply intent.",
    )
    source_ref: Optional[str] = Field(
        default=None, description="e.g. 'thread:abc' or 'event:xyz' for traceability.",
    )
    template_only: bool = Field(
        default=False, description="Skip LLM and use canned template (faster, cheaper).",
    )
    enqueue_for_review: bool = Field(
        default=True,
        description="Add to draft queue for review-then-send. False returns the draft inline.",
    )


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_compose_draft",
        annotations={
            "title": "Compose a brand-voice draft + queue for review",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_compose_draft(params: ComposeDraftInput) -> str:
        """Compose a draft email in your brand voice and queue it for review.

        Uses the lifted-out brand voice composer + the draft queue.
        Set enqueue_for_review=False to get the draft back inline instead
        of queuing it.

        Returns JSON with subject, body, and (if queued) an entry_id you
        can pass to workflow_approve_draft / workflow_discard_draft /
        workflow_edit_draft.
        """
        try:
            req = brand_voice.DraftRequest(
                intent=params.intent,
                audience=params.audience,
                recipient_name=params.recipient_name,
                subject_hint=params.subject_hint,
                context=params.context,
                facts=params.facts or {},
                constraints=params.constraints or [],
                target_language=params.target_language,
            )
            if params.template_only:
                draft = brand_voice.compose_template_only(req)
            else:
                draft = brand_voice.compose(req)

            if not params.enqueue_for_review:
                return json.dumps({
                    "queued": False,
                    "draft": draft.to_dict(),
                }, indent=2, default=str)

            entry_id = core.enqueue(
                kind=f"compose_{params.intent}",
                subject=draft.subject,
                body_plain=draft.plain,
                body_html=draft.html,
                target=params.target,
                cc=params.cc,
                source_ref=params.source_ref,
                meta={
                    "intent": params.intent,
                    "audience": params.audience,
                    "voice_used": draft.voice_used,
                    "estimated_cost_usd": draft.estimated_cost_usd,
                },
            )
            log.info("compose_draft queued %s (intent=%s, voice=%s)",
                     entry_id, params.intent, draft.voice_used)
            return json.dumps({
                "queued": True,
                "entry_id": entry_id,
                "draft": draft.to_dict(),
            }, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_compose_draft", e)


# --------------------------------------------------------------------------- #
# list / approve / discard / edit
# --------------------------------------------------------------------------- #


class ListDraftsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    kind: Optional[str] = Field(default=None, description="Filter by kind code.")
    status: Optional[str] = Field(
        default="pending",
        description="'pending' | 'approved' | 'sent' | 'discarded' | None for all.",
    )


class ApproveDraftInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    entry_id: str = Field(..., description="The draft's entry_id from the queue.")
    send: bool = Field(
        default=True, description="Actually send via Gmail. False = mark approved only.",
    )


class DiscardDraftInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    entry_id: str = Field(...)


class EditDraftInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    entry_id: str = Field(...)
    subject: Optional[str] = Field(default=None)
    body_plain: Optional[str] = Field(default=None)
    body_html: Optional[str] = Field(default=None)


def register_actions(mcp) -> None:
    @mcp.tool(
        name="workflow_list_drafts",
        annotations={
            "title": "List queued drafts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def workflow_list_drafts(params: ListDraftsInput) -> str:
        """List queued drafts, optionally filtered by status + kind."""
        try:
            if params.status is None:
                rows = core.list_all()
            elif params.status == "pending":
                rows = core.list_pending(kind=params.kind)
            else:
                rows = [r for r in core.list_all(status=params.status)
                        if not params.kind or r.get("kind") == params.kind]
            return json.dumps({"count": len(rows), "drafts": rows},
                              indent=2, default=str)
        except Exception as e:
            return format_error("workflow_list_drafts", e)

    @mcp.tool(
        name="workflow_approve_draft",
        annotations={
            "title": "Approve (and send) a queued draft",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_approve_draft(params: ApproveDraftInput) -> str:
        """Approve a pending draft. If send=True, actually send via Gmail."""
        try:
            rec = core.approve(params.entry_id)
            if not rec:
                return format_error(
                    "workflow_approve_draft",
                    ValueError(f"No pending draft with id {params.entry_id}"),
                )
            sent_info = None
            if params.send:
                resp = _send_email(
                    to=rec.get("to", []),
                    cc=rec.get("cc", []),
                    subject=rec["subject"],
                    plain=rec["body_plain"],
                    html=rec.get("body_html", ""),
                )
                core.mark_sent(params.entry_id)
                sent_info = {"message_id": resp.get("id"),
                             "thread_id": resp.get("threadId")}
                # Fire any kind-specific post-approval hooks (e.g. AR
                # collections advancing add_collection_event). Best-
                # effort — exceptions caught inside fire_post_approval_hooks
                # so the approve response always returns cleanly.
                final_rec = core.get(params.entry_id) or rec
                core.fire_post_approval_hooks(final_rec)
            return json.dumps({
                "entry_id": rec["id"],
                "status": "sent" if params.send else "approved",
                "subject": rec["subject"],
                "sent": sent_info,
            }, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_approve_draft", e)

    @mcp.tool(
        name="workflow_discard_draft",
        annotations={
            "title": "Discard a queued draft",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def workflow_discard_draft(params: DiscardDraftInput) -> str:
        try:
            ok = core.discard(params.entry_id)
            return json.dumps({"discarded": ok, "entry_id": params.entry_id},
                              indent=2)
        except Exception as e:
            return format_error("workflow_discard_draft", e)

    @mcp.tool(
        name="workflow_edit_draft",
        annotations={
            "title": "Edit a pending queued draft",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    )
    async def workflow_edit_draft(params: EditDraftInput) -> str:
        try:
            rec = core.update_body(
                params.entry_id, subject=params.subject,
                body_plain=params.body_plain, body_html=params.body_html,
            )
            if not rec:
                return format_error(
                    "workflow_edit_draft",
                    ValueError(f"No pending draft with id {params.entry_id}"),
                )
            return json.dumps(rec, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_edit_draft", e)


def register_all(mcp) -> None:
    """Convenience — register both compose + action tools in one call."""
    register(mcp)
    register_actions(mcp)
