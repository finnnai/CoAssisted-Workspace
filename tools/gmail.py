"""Gmail tools: send, reply, search, read threads, manage labels."""

from __future__ import annotations

import base64
import json
import mimetypes
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

from googleapiclient.http import MediaInMemoryUpload
from pydantic import BaseModel, ConfigDict, Field, field_validator

import config
import gservices
from dryrun import dry_run_preview, is_dry_run
from errors import format_error
from logging_util import log


def _service():
    """Return the cached Gmail API service (cred refresh is transparent)."""
    return gservices.gmail()


def _threshold_bytes() -> int:
    """Size above which attachments auto-route through Drive instead of inline."""
    kb = config.get("large_attachment_threshold_kb", 500)
    try:
        return max(1, int(kb)) * 1024
    except (TypeError, ValueError):
        return 500 * 1024


def _upload_attachment_to_drive_and_share(
    data: bytes,
    filename: str,
    mime_type: str,
    recipients: list[str],
) -> str:
    """Upload bytes to Drive, share with every recipient as reader, return webViewLink.

    Used when an attachment is too large to safely inline in an email (hits
    stdio buffer limits or corporate mail filter rules). Notifications are
    suppressed on the share so the caller can send a single, deliberate email.
    """
    drive = gservices.drive()
    media = MediaInMemoryUpload(data, mimetype=mime_type or "application/octet-stream")
    created = (
        drive.files()
        .create(
            body={"name": filename},
            media_body=media,
            fields="id, name, webViewLink",
        )
        .execute()
    )
    file_id = created["id"]
    link = created.get("webViewLink")

    for addr in {r.strip() for r in recipients if r and "@" in r}:
        try:
            drive.permissions().create(
                fileId=file_id,
                body={"type": "user", "role": "reader", "emailAddress": addr},
                sendNotificationEmail=False,
                fields="id",
            ).execute()
        except Exception as e:
            # Non-fatal — log and continue. The recipient can still access if the
            # permission model allows it (e.g. same Workspace).
            log.warning("Drive share to %s failed: %s", addr, e)

    log.info("auto-Drive attachment '%s' uploaded and shared (%d recipients)", filename, len(recipients))
    return link


def _build_mime(
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    html: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachments: list[dict] | None = None,
    from_alias: str | None = None,
) -> dict:
    """Build a base64url-encoded RFC 2822 message for the Gmail API.

    `attachments` is a list of dicts, each shaped as ONE of:
        {"path": "/abs/path/to/file.pdf", "filename": "optional-override.pdf"}
        {"content_b64": "<base64 string>", "filename": "report.pdf", "mime_type": "application/pdf"}
    """
    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    if from_alias:
        msg["From"] = from_alias
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    if html:
        msg.set_content(body or "")
        msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(body)

    for att in attachments or []:
        data, filename, mime = _resolve_attachment(att)
        maintype, subtype = mime.split("/", 1) if "/" in mime else ("application", "octet-stream")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": raw}


def _peek_attachment_size(att: dict) -> tuple[int, str, str, str | None]:
    """Return (size_bytes, filename, mime_type, path_or_None) WITHOUT reading bytes.

    Used to make a routing decision (inline vs. Drive) before incurring the
    memory cost of reading huge files. For path-based attachments we use stat()
    instead of read_bytes(); for inline base64 we measure the encoded length
    (a tight upper bound on decoded size).
    """
    if "path" in att:
        path = Path(att["path"]).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Attachment path not found: {path}")
        size = path.stat().st_size
        filename = att.get("filename") or path.name
        mime = att.get("mime_type") or (
            mimetypes.guess_type(filename)[0] or "application/octet-stream"
        )
        return size, filename, mime, str(path)
    if "content_b64" in att:
        # Decoded size ≈ (len(b64) * 3 / 4); good enough for routing.
        size = max(0, len(att["content_b64"]) * 3 // 4)
        filename = att.get("filename") or "attachment.bin"
        mime = att.get("mime_type") or (
            mimetypes.guess_type(filename)[0] or "application/octet-stream"
        )
        return size, filename, mime, None
    raise ValueError(
        "Each attachment must have either 'path' or 'content_b64'. Got keys: "
        + ", ".join(att.keys())
    )


def _resolve_attachment(att: dict) -> tuple[bytes, str, str]:
    """Return (bytes, filename, mime_type) for one attachment dict.

    Reads the file fully — only call this for attachments already known to be
    inline-sized (≤ threshold). For routing decisions, use _peek_attachment_size.
    """
    if "path" in att:
        path = Path(att["path"]).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"Attachment path not found: {path}")
        data = path.read_bytes()
        filename = att.get("filename") or path.name
        mime = att.get("mime_type") or (mimetypes.guess_type(filename)[0] or "application/octet-stream")
        return data, filename, mime
    if "content_b64" in att:
        data = base64.b64decode(att["content_b64"])
        filename = att.get("filename") or "attachment.bin"
        mime = att.get("mime_type") or (mimetypes.guess_type(filename)[0] or "application/octet-stream")
        return data, filename, mime
    raise ValueError(
        "Each attachment must have either 'path' or 'content_b64'. Got keys: "
        + ", ".join(att.keys())
    )


def _upload_path_to_drive_and_share(
    path_str: str,
    filename: str,
    mime_type: str,
    recipients: list[str],
) -> str:
    """Stream-upload a file from disk to Drive (no full bytes in RAM), then share.

    Used for huge attachments (e.g. 1GB+ files) where reading into memory would
    OOM the MCP process. Falls back through MediaFileUpload's resumable chunked
    transfer.
    """
    from googleapiclient.http import MediaFileUpload

    drive = gservices.drive()
    media = MediaFileUpload(
        path_str,
        mimetype=mime_type or "application/octet-stream",
        resumable=True,
        chunksize=8 * 1024 * 1024,  # 8MB chunks
    )
    request = drive.files().create(
        body={"name": filename}, media_body=media, fields="id, name, webViewLink"
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    file_id = response["id"]
    link = response.get("webViewLink")

    for addr in {r.strip() for r in recipients if r and "@" in r}:
        try:
            drive.permissions().create(
                fileId=file_id,
                body={"type": "user", "role": "reader", "emailAddress": addr},
                sendNotificationEmail=False,
                fields="id",
            ).execute()
        except Exception as e:
            log.warning("Drive share to %s failed: %s", addr, e)

    log.info(
        "auto-Drive (streamed) attachment '%s' uploaded and shared (%d recipients)",
        filename, len(recipients),
    )
    return link


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class AttachmentSpec(BaseModel):
    """One attachment. Use EITHER `path` OR `content_b64` — not both."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    path: Optional[str] = Field(
        default=None,
        description="Absolute path to a local file to attach (preferred when the file is on disk).",
    )
    content_b64: Optional[str] = Field(
        default=None,
        description="Base64-encoded file content (use when attaching in-memory data).",
    )
    filename: Optional[str] = Field(
        default=None, description="Display filename (defaults to basename of path or 'attachment.bin')."
    )
    mime_type: Optional[str] = Field(
        default=None, description="MIME type. Inferred from extension if omitted."
    )


class SendEmailInput(BaseModel):
    """Input for gmail_send_email."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    to: list[str] = Field(
        ..., min_length=1, description="Recipient email addresses. At least one required."
    )
    subject: str = Field(..., description="Email subject line.")
    body: str = Field(..., description="Plain-text body content.")
    cc: Optional[list[str]] = Field(default=None, description="Optional CC addresses.")
    bcc: Optional[list[str]] = Field(default=None, description="Optional BCC addresses.")
    html_body: Optional[str] = Field(
        default=None,
        description="Optional HTML body. If provided, `body` becomes the plain-text fallback.",
    )
    attachments: Optional[list[AttachmentSpec]] = Field(
        default=None,
        description=(
            "Optional list of attachments. Each item may be either an "
            "AttachmentSpec dict (with `path` or `content_b64`) OR a bare "
            "string file path — both are accepted."
        ),
    )
    from_alias: Optional[str] = Field(
        default=None,
        description="Send from a configured Gmail alias instead of the primary address.",
    )
    dry_run: Optional[bool] = Field(
        default=None,
        description="If True, return a preview without sending. Overrides global config.dry_run.",
    )

    @field_validator("attachments", mode="before")
    @classmethod
    def _coerce_string_attachments(cls, v):
        """Allow callers to pass bare strings (treated as `{"path": <str>}`)."""
        if not v:
            return v
        out = []
        for item in v:
            if isinstance(item, str):
                out.append({"path": item})
            else:
                out.append(item)
        return out


class CreateDraftInput(SendEmailInput):
    """Same fields as send, but saves as a draft instead of sending."""


class ReplyInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    thread_id: str = Field(..., description="Gmail thread ID to reply to.")
    body: str = Field(..., description="Plain-text reply body.")
    html_body: Optional[str] = Field(default=None, description="Optional HTML body.")
    reply_all: bool = Field(
        default=False, description="If true, CC the original recipients."
    )


class SearchEmailInput(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True, extra="forbid", populate_by_name=True
    )

    query: str = Field(
        ...,
        alias="q",
        description=(
            "Gmail search query using Gmail's search operators, "
            "e.g. 'from:josh@example.com newer_than:7d has:attachment'. "
            "Alias `q` is also accepted."
        ),
    )
    limit: int = Field(
        default=20, ge=1, le=100,
        alias="max_results",
        description="Max results. Alias `max_results` is also accepted.",
    )


class GetThreadInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    thread_id: str = Field(..., description="Gmail thread ID.")


class ListLabelsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModifyLabelsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message_id: str = Field(..., description="Gmail message ID.")
    add_label_ids: Optional[list[str]] = Field(
        default=None, description="Label IDs to apply."
    )
    remove_label_ids: Optional[list[str]] = Field(
        default=None, description="Label IDs to remove."
    )


class ListDraftsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: Optional[str] = Field(
        default=None,
        description="Optional Gmail search query to filter drafts (same syntax as gmail_search).",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max drafts to return.")


class CreateLabelInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(
        ...,
        description="Label name. Nest labels with '/' (e.g. 'Clients/Acme').",
        min_length=1,
        max_length=225,
    )
    label_list_visibility: str = Field(
        default="labelShow",
        description="Sidebar visibility: 'labelShow', 'labelShowIfUnread', or 'labelHide'.",
    )
    message_list_visibility: str = Field(
        default="show",
        description="Per-message visibility: 'show' or 'hide'.",
    )


class UpdateLabelInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    label_id: str = Field(..., description="Existing label ID (e.g. 'Label_42').")
    name: Optional[str] = Field(
        default=None, description="New label name. Omit to keep the existing name."
    )
    label_list_visibility: Optional[str] = Field(
        default=None,
        description="'labelShow', 'labelShowIfUnread', or 'labelHide'. Omit to keep current.",
    )
    message_list_visibility: Optional[str] = Field(
        default=None, description="'show' or 'hide'. Omit to keep current."
    )


class DeleteLabelInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    label_id: str = Field(..., description="Label ID to delete (e.g. 'Label_42').")


class ForwardInput(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True, extra="forbid", populate_by_name=True
    )

    message_id: str = Field(..., description="Gmail message ID to forward.")
    to: list[str] = Field(..., min_length=1, description="Forward recipients.")
    cc: Optional[list[str]] = Field(default=None)
    bcc: Optional[list[str]] = Field(default=None)
    comment: Optional[str] = Field(
        default=None,
        alias="note",
        description="Optional note to prepend above the forwarded content. Alias `note` is also accepted.",
    )
    dry_run: Optional[bool] = Field(default=None)


class TrashInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message_id: str = Field(..., description="Gmail message ID.")
    dry_run: Optional[bool] = Field(default=None)


class UntrashInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message_id: str = Field(..., description="Gmail message ID currently in Trash.")


class DownloadAttachmentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message_id: str = Field(..., description="Gmail message ID containing the attachment.")
    attachment_id: Optional[str] = Field(
        default=None,
        description=(
            "Specific attachment ID. If omitted, lists attachments on the message. "
            "Note: Gmail's attachment IDs can rotate between calls — if you fetched "
            "the ID from a previous call and it now reports 'not found', re-fetch "
            "or pass `filename` instead."
        ),
    )
    filename: Optional[str] = Field(
        default=None,
        description=(
            "Stable alternative to attachment_id: match by attachment filename "
            "(case-insensitive). Useful when attachment IDs rotate. If both "
            "attachment_id and filename are given, attachment_id is tried first "
            "and filename is the fallback."
        ),
    )
    save_to_path: Optional[str] = Field(
        default=None,
        description="If given, write decoded bytes to this absolute path and return metadata.",
    )


class ListFiltersInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateFilterInput(BaseModel):
    # populate_by_name lets callers use either `from` (alias) or `from_` (field name).
    # Without this, Pydantic v2 + extra="forbid" rejects `from_`.
    model_config = ConfigDict(
        str_strip_whitespace=True, extra="forbid", populate_by_name=True
    )

    from_: Optional[str] = Field(default=None, alias="from", description="Match sender.")
    to: Optional[str] = Field(default=None, description="Match recipient.")
    subject: Optional[str] = Field(default=None, description="Match subject substring.")
    query: Optional[str] = Field(
        default=None, description="Full Gmail search query to match against."
    )
    has_attachment: Optional[bool] = Field(default=None, description="Match only messages with attachments.")
    add_label_ids: Optional[list[str]] = Field(default=None, description="Labels to add on match.")
    remove_label_ids: Optional[list[str]] = Field(default=None, description="Labels to remove on match.")
    forward_to: Optional[str] = Field(
        default=None, description="Forward matching mail to this address (must be a verified forwarding address)."
    )


class DeleteFilterInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    filter_id: str = Field(..., description="Filter ID to delete.")


class ListAliasesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="gmail_send_email",
        annotations={
            "title": "Send a Gmail email",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def gmail_send_email(params: SendEmailInput) -> str:
        """Send an email from the authenticated user's Gmail account.

        Supports attachments (local file paths or inline base64), HTML body,
        send-as aliases, and dry-run previewing.

        Attachment size behavior:
            Attachments larger than config.large_attachment_threshold_kb
            (default 22000KB / ~22MB) automatically upload to Drive, share as
            'reader' with every recipient, and the share link is appended to
            the body. Anything below that ships as a real Gmail attachment.
            Lower the threshold if stdio buffer limits surface on the Cowork
            MCP channel or corporate mail filters bounce large .tar.gz / .zip.

        Returns JSON with the sent message ID and thread ID, or an error string.
        """
        try:
            from_alias = params.from_alias or config.get("default_from_alias")
            atts = [a.model_dump(exclude_none=True) for a in params.attachments or []]

            # Split attachments: inline (≤ threshold) vs. Drive-hosted (> threshold).
            # Crucially: peek file SIZE first (no read), so a 5GB file doesn't OOM
            # the MCP process before we route it through streaming Drive upload.
            inline_atts: list[dict] = []
            drive_shares: list[dict] = []  # each: {filename, link}
            if atts:
                threshold = _threshold_bytes()
                all_recipients = [*(params.to or []), *(params.cc or []), *(params.bcc or [])]

                # First pass — peek every attachment's size + path WITHOUT reading bytes.
                peeks: list[tuple[dict, int, str, str, str | None]] = []
                inline_total = 0
                for att in atts:
                    size, filename, mime, path_str = _peek_attachment_size(att)
                    peeks.append((att, size, filename, mime, path_str))
                    if size <= threshold:
                        inline_total += size

                # Total inline + body must stay under Gmail's 25MB hard limit.
                # If we'd exceed, downgrade ALL inline attachments to Drive too.
                gmail_max = int(config.get("gmail_max_message_kb", 22528)) * 1024
                # Gmail base64-encodes attachments in transit (~33% overhead).
                # Apply a safety factor of 1.4x to the inline_total estimate.
                if int(inline_total * 1.4) > gmail_max:
                    log.warning(
                        "gmail_send_email: inline total %dKB would exceed Gmail's "
                        "%dKB limit — routing all attachments through Drive",
                        round(inline_total / 1024), gmail_max // 1024,
                    )
                    # Force every attachment to the Drive path.
                    new_threshold = 0
                else:
                    new_threshold = threshold

                # Second pass — actually route.
                for att, size, filename, mime, path_str in peeks:
                    if size <= new_threshold:
                        inline_atts.append(att)
                    else:
                        # Path-based + huge → stream upload (no full bytes in RAM).
                        if path_str is not None and size > threshold:
                            link = _upload_path_to_drive_and_share(
                                path_str=path_str,
                                filename=filename,
                                mime_type=mime,
                                recipients=all_recipients,
                            )
                        else:
                            # In-memory base64 input, or downgrade-due-to-total —
                            # use the in-memory uploader.
                            data, _, _ = _resolve_attachment(att)
                            link = _upload_attachment_to_drive_and_share(
                                data=data,
                                filename=filename,
                                mime_type=mime,
                                recipients=all_recipients,
                            )
                        drive_shares.append(
                            {"filename": filename, "link": link, "size_kb": round(size / 1024)}
                        )

            # Build effective body with the Drive-links footer if any were offloaded.
            effective_body = params.body
            effective_html = params.html_body
            if drive_shares:
                lines = ["", "", "— Attachments (shared via Drive) —"]
                for s in drive_shares:
                    lines.append(f"{s['filename']} ({s['size_kb']} KB): {s['link']}")
                footer = "\n".join(lines)
                effective_body = (effective_body or "") + footer
                if effective_html:
                    html_links = "".join(
                        f'<li><a href="{s["link"]}">{s["filename"]}</a> ({s["size_kb"]} KB)</li>'
                        for s in drive_shares
                    )
                    effective_html = (
                        effective_html
                        + f'<hr/><p><strong>Attachments (shared via Drive):</strong></p><ul>{html_links}</ul>'
                    )

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "gmail_send_email",
                    {
                        "to": params.to,
                        "cc": params.cc,
                        "bcc": params.bcc,
                        "subject": params.subject,
                        "body_preview": effective_body[:400],
                        "html": bool(effective_html),
                        "inline_attachments": [
                            {"filename": a.get("filename"), "source": "path" if a.get("path") else "base64"}
                            for a in inline_atts
                        ],
                        "drive_shared_attachments": drive_shares,
                        "from_alias": from_alias,
                    },
                )

            msg = _build_mime(
                to=params.to,
                subject=params.subject,
                body=effective_body,
                cc=params.cc,
                bcc=params.bcc,
                html=effective_html,
                attachments=inline_atts,
                from_alias=from_alias,
            )
            sent = (
                _service()
                .users()
                .messages()
                .send(userId="me", body=msg)
                .execute()
            )
            log.info(
                "gmail_send_email sent to=%s subject=%s (inline=%d, drive=%d)",
                params.to, params.subject, len(inline_atts), len(drive_shares),
            )
            return json.dumps(
                {
                    "status": "sent",
                    "id": sent.get("id"),
                    "thread_id": sent.get("threadId"),
                    "inline_attachments": len(inline_atts),
                    "drive_shared_attachments": drive_shares,
                },
                indent=2,
            )
        except Exception as e:
            log.error("gmail_send_email failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="gmail_create_draft",
        annotations={
            "title": "Create a Gmail draft",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def gmail_create_draft(params: CreateDraftInput) -> str:
        """Create a Gmail draft without sending.

        Returns JSON with the draft ID.
        """
        try:
            msg = _build_mime(
                to=params.to,
                subject=params.subject,
                body=params.body,
                cc=params.cc,
                bcc=params.bcc,
                html=params.html_body,
            )
            draft = (
                _service()
                .users()
                .drafts()
                .create(userId="me", body={"message": msg})
                .execute()
            )
            return json.dumps({"status": "drafted", "id": draft.get("id")}, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_reply_to_thread",
        annotations={
            "title": "Reply to a Gmail thread",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def gmail_reply_to_thread(params: ReplyInput) -> str:
        """Send a reply within an existing Gmail thread.

        Fetches the latest message in the thread to pull headers (From, Subject,
        Message-ID), then builds a properly-threaded reply. If `reply_all` is
        true, CCs the original non-self recipients.
        """
        try:
            svc = _service()
            thread = (
                svc.users().threads().get(userId="me", id=params.thread_id).execute()
            )
            msgs = thread.get("messages", [])
            if not msgs:
                return f"Thread {params.thread_id} has no messages."
            last = msgs[-1]
            headers = {h["name"].lower(): h["value"] for h in last["payload"]["headers"]}

            to = [headers.get("from", "")]
            subject = headers.get("subject", "")
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
            msg_id = headers.get("message-id")
            refs = headers.get("references", "")
            refs = (refs + " " + msg_id).strip() if msg_id else refs

            cc = None
            if params.reply_all:
                cc_raw = headers.get("cc", "")
                if cc_raw:
                    cc = [addr.strip() for addr in cc_raw.split(",") if addr.strip()]

            body = _build_mime(
                to=[t for t in to if t],
                subject=subject,
                body=params.body,
                cc=cc,
                html=params.html_body,
                in_reply_to=msg_id,
                references=refs or None,
            )
            body["threadId"] = params.thread_id

            sent = svc.users().messages().send(userId="me", body=body).execute()
            return json.dumps(
                {"status": "sent", "id": sent.get("id"), "thread_id": sent.get("threadId")},
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_search",
        annotations={
            "title": "Search Gmail messages",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gmail_search(params: SearchEmailInput) -> str:
        """Search Gmail with native Gmail query syntax.

        Returns JSON list of message snippets (id, thread_id, snippet, from, subject, date).
        """
        try:
            svc = _service()
            resp = (
                svc.users()
                .messages()
                .list(userId="me", q=params.query, maxResults=params.limit)
                .execute()
            )
            ids = [m["id"] for m in resp.get("messages", [])]
            out = []
            for mid in ids:
                m = (
                    svc.users()
                    .messages()
                    .get(
                        userId="me",
                        id=mid,
                        format="metadata",
                        metadataHeaders=["From", "Subject", "Date"],
                    )
                    .execute()
                )
                headers = {h["name"]: h["value"] for h in m["payload"]["headers"]}
                out.append(
                    {
                        "id": m["id"],
                        "thread_id": m["threadId"],
                        "snippet": m.get("snippet", ""),
                        "from": headers.get("From"),
                        "subject": headers.get("Subject"),
                        "date": headers.get("Date"),
                    }
                )
            return json.dumps({"count": len(out), "messages": out}, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_get_thread",
        annotations={
            "title": "Get full Gmail thread",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gmail_get_thread(params: GetThreadInput) -> str:
        """Fetch a full Gmail thread with all messages, headers, and plain-text bodies."""
        try:
            svc = _service()
            thread = (
                svc.users().threads().get(userId="me", id=params.thread_id, format="full").execute()
            )
            out_msgs = []
            for m in thread.get("messages", []):
                headers = {h["name"]: h["value"] for h in m["payload"]["headers"]}
                body_text = _extract_plaintext(m["payload"])
                out_msgs.append(
                    {
                        "id": m["id"],
                        "from": headers.get("From"),
                        "to": headers.get("To"),
                        "cc": headers.get("Cc"),
                        "subject": headers.get("Subject"),
                        "date": headers.get("Date"),
                        "body": body_text,
                    }
                )
            return json.dumps(
                {"thread_id": thread["id"], "message_count": len(out_msgs), "messages": out_msgs},
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_list_labels",
        annotations={
            "title": "List Gmail labels",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gmail_list_labels(params: ListLabelsInput) -> str:
        """Return all Gmail labels on this account (system + user)."""
        try:
            labels = _service().users().labels().list(userId="me").execute().get("labels", [])
            return json.dumps(
                [{"id": l["id"], "name": l["name"], "type": l["type"]} for l in labels],
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_modify_labels",
        annotations={
            "title": "Apply/remove Gmail labels on a message",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gmail_modify_labels(params: ModifyLabelsInput) -> str:
        """Add or remove labels on a specific Gmail message.

        Common label IDs include INBOX, STARRED, UNREAD, IMPORTANT, TRASH.
        Use gmail_list_labels to find custom label IDs.
        """
        try:
            body = {
                "addLabelIds": params.add_label_ids or [],
                "removeLabelIds": params.remove_label_ids or [],
            }
            _service().users().messages().modify(
                userId="me", id=params.message_id, body=body
            ).execute()
            return json.dumps({"status": "ok", "message_id": params.message_id})
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_list_drafts",
        annotations={
            "title": "List Gmail drafts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gmail_list_drafts(params: ListDraftsInput) -> str:
        """List drafts in the authenticated account, with optional Gmail-syntax filter.

        Returns JSON with each draft's id, message id, subject, to, and snippet.
        """
        try:
            svc = _service()
            kwargs = {"userId": "me", "maxResults": params.limit}
            if params.query:
                kwargs["q"] = params.query
            resp = svc.users().drafts().list(**kwargs).execute()
            drafts_raw = resp.get("drafts", [])

            out = []
            for d in drafts_raw:
                try:
                    full = (
                        svc.users()
                        .drafts()
                        .get(userId="me", id=d["id"], format="metadata")
                        .execute()
                    )
                    msg = full.get("message", {})
                    headers = {
                        h["name"]: h["value"]
                        for h in msg.get("payload", {}).get("headers", [])
                    }
                    out.append(
                        {
                            "draft_id": full["id"],
                            "message_id": msg.get("id"),
                            "thread_id": msg.get("threadId"),
                            "to": headers.get("To"),
                            "subject": headers.get("Subject"),
                            "snippet": msg.get("snippet", ""),
                        }
                    )
                except Exception:
                    # If one draft fails to hydrate, skip it rather than breaking the whole list.
                    out.append({"draft_id": d["id"], "error": "could not hydrate metadata"})
            return json.dumps({"count": len(out), "drafts": out}, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_create_label",
        annotations={
            "title": "Create a Gmail label",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def gmail_create_label(params: CreateLabelInput) -> str:
        """Create a new user label. Nest labels by using '/' in the name (e.g. 'Clients/Acme').

        Returns the new label's id, name, and visibility settings. If a label with the
        same name already exists, Gmail returns a 409 — handled as a clear error.
        """
        try:
            body = {
                "name": params.name,
                "labelListVisibility": params.label_list_visibility,
                "messageListVisibility": params.message_list_visibility,
            }
            label = _service().users().labels().create(userId="me", body=body).execute()
            return json.dumps(
                {
                    "id": label["id"],
                    "name": label["name"],
                    "type": label.get("type", "user"),
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_update_label",
        annotations={
            "title": "Rename or update a Gmail label",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gmail_update_label(params: UpdateLabelInput) -> str:
        """Update a label's name and/or visibility settings.

        Only fields you provide are changed; others stay as-is. Useful for
        renaming labels or toggling their sidebar/per-message visibility.
        """
        try:
            body: dict = {}
            if params.name is not None:
                body["name"] = params.name
            if params.label_list_visibility is not None:
                body["labelListVisibility"] = params.label_list_visibility
            if params.message_list_visibility is not None:
                body["messageListVisibility"] = params.message_list_visibility
            if not body:
                return "Error: provide at least one field to update."
            label = (
                _service()
                .users()
                .labels()
                .patch(userId="me", id=params.label_id, body=body)
                .execute()
            )
            return json.dumps(
                {
                    "status": "updated",
                    "id": label["id"],
                    "name": label.get("name"),
                    "labelListVisibility": label.get("labelListVisibility"),
                    "messageListVisibility": label.get("messageListVisibility"),
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_delete_label",
        annotations={
            "title": "Delete a Gmail label",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def gmail_delete_label(params: DeleteLabelInput) -> str:
        """Permanently delete a user label by ID.

        Cannot be undone. Messages with the label will lose it but won't be
        affected otherwise. System labels (INBOX, STARRED, etc.) cannot be deleted.
        """
        try:
            _service().users().labels().delete(
                userId="me", id=params.label_id
            ).execute()
            log.info("gmail_delete_label deleted %s", params.label_id)
            return json.dumps({"status": "deleted", "label_id": params.label_id})
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_forward_message",
        annotations={
            "title": "Forward a Gmail message",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def gmail_forward_message(params: ForwardInput) -> str:
        """Forward an existing message to new recipients, optionally with a note.

        Retains the original message's subject (prefixed with 'Fwd:') and body.
        Attachments on the original message ARE re-attached to the forward.
        """
        try:
            svc = _service()
            orig = svc.users().messages().get(userId="me", id=params.message_id, format="full").execute()
            headers = {h["name"]: h["value"] for h in orig["payload"]["headers"]}
            orig_subject = headers.get("Subject", "")
            subject = orig_subject if orig_subject.lower().startswith("fwd:") else f"Fwd: {orig_subject}"
            orig_text = _extract_plaintext(orig["payload"])
            orig_from = headers.get("From", "")
            orig_date = headers.get("Date", "")
            header_block = (
                f"\n\n---------- Forwarded message ---------\n"
                f"From: {orig_from}\n"
                f"Date: {orig_date}\n"
                f"Subject: {orig_subject}\n\n"
            )
            body = (params.comment or "") + header_block + orig_text

            # Collect attachments from the original.
            forwarded_atts = _collect_attachments(svc, params.message_id, orig["payload"])

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "gmail_forward_message",
                    {
                        "original_message_id": params.message_id,
                        "to": params.to,
                        "cc": params.cc,
                        "subject": subject,
                        "attachments_carried": [a["filename"] for a in forwarded_atts],
                    },
                )

            mime = _build_mime(
                to=params.to,
                subject=subject,
                body=body,
                cc=params.cc,
                bcc=params.bcc,
                attachments=forwarded_atts,
                from_alias=config.get("default_from_alias"),
            )
            sent = svc.users().messages().send(userId="me", body=mime).execute()
            log.info("gmail_forward_message forwarded %s to=%s", params.message_id, params.to)
            return json.dumps(
                {"status": "sent", "id": sent.get("id"), "thread_id": sent.get("threadId")},
                indent=2,
            )
        except Exception as e:
            log.error("gmail_forward_message failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="gmail_trash_message",
        annotations={
            "title": "Move a Gmail message to Trash",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gmail_trash_message(params: TrashInput) -> str:
        """Move a message to Trash (recoverable via gmail_untrash_message for 30 days)."""
        try:
            if is_dry_run(params.dry_run):
                return dry_run_preview("gmail_trash_message", {"message_id": params.message_id})
            _service().users().messages().trash(userId="me", id=params.message_id).execute()
            return json.dumps({"status": "trashed", "message_id": params.message_id})
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_untrash_message",
        annotations={
            "title": "Restore a trashed Gmail message",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gmail_untrash_message(params: UntrashInput) -> str:
        """Restore a message from Trash back to its previous labels."""
        try:
            _service().users().messages().untrash(userId="me", id=params.message_id).execute()
            return json.dumps({"status": "restored", "message_id": params.message_id})
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_download_attachment",
        annotations={
            "title": "Download an attachment from a Gmail message",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gmail_download_attachment(params: DownloadAttachmentInput) -> str:
        """Download an attachment.

        If `attachment_id` is omitted, lists all attachments on the message with
        their IDs, filenames, MIME types, and sizes.

        If `attachment_id` is given, returns the attachment as base64. If
        `save_to_path` is also given, writes bytes to that path and returns
        metadata only (no base64 in response — keeps the context window clean).
        """
        try:
            svc = _service()
            msg = svc.users().messages().get(userId="me", id=params.message_id, format="full").execute()
            atts = _list_attachments_meta(msg["payload"])

            # Listing mode: no attachment_id and no filename → return all attachments.
            if not params.attachment_id and not params.filename:
                return json.dumps({"count": len(atts), "attachments": atts}, indent=2)

            # Try attachment_id first (if given), then fall back to filename match.
            target = None
            if params.attachment_id:
                target = next(
                    (a for a in atts if a["attachment_id"] == params.attachment_id), None
                )
            if target is None and params.filename:
                want = params.filename.lower()
                target = next(
                    (a for a in atts if (a.get("filename") or "").lower() == want), None
                )
            if target is None:
                available = [a.get("filename") for a in atts]
                hint = (
                    "Gmail attachment IDs can rotate between calls — re-fetch the "
                    "list and try again, or pass `filename` for a stable match."
                )
                return (
                    f"Attachment not found on message {params.message_id}. "
                    f"Available filenames: {available}. {hint}"
                )

            # Always use the freshly-fetched attachment_id from `target` (not the
            # caller-provided one, which might be stale).
            current_id = target["attachment_id"]
            data = (
                svc.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=params.message_id, id=current_id)
                .execute()
            )
            raw = base64.urlsafe_b64decode(data["data"])

            if params.save_to_path:
                path = Path(params.save_to_path).expanduser()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                return json.dumps(
                    {
                        "status": "saved",
                        "path": str(path),
                        "filename": target["filename"],
                        "mime_type": target["mime_type"],
                        "size": len(raw),
                    },
                    indent=2,
                )

            # Hard cap on inline base64 returns. If exceeded, AUTO-SAVE to the
            # configured default_download_dir (default: ~/Downloads) and return
            # the path — never just fail.
            max_inline = int(config.get("max_inline_download_kb", 5120)) * 1024
            if len(raw) > max_inline:
                auto_path = config.resolve_auto_download_path(target["filename"])
                auto_path.write_bytes(raw)
                log.info(
                    "gmail_download_attachment auto-saved %s (%d KB > %d KB cap) to %s",
                    target["filename"], len(raw) // 1024, max_inline // 1024, auto_path,
                )
                return json.dumps(
                    {
                        "status": "auto_saved",
                        "path": str(auto_path),
                        "filename": target["filename"],
                        "mime_type": target["mime_type"],
                        "size": len(raw),
                        "size_kb": round(len(raw) / 1024),
                        "max_inline_kb": max_inline // 1024,
                        "note": (
                            f"File exceeded max_inline_download_kb ({max_inline // 1024} KB) "
                            "and was auto-saved to default_download_dir."
                        ),
                    },
                    indent=2,
                )

            return json.dumps(
                {
                    "filename": target["filename"],
                    "mime_type": target["mime_type"],
                    "size": len(raw),
                    "content_b64": base64.b64encode(raw).decode("ascii"),
                },
                indent=2,
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_list_filters",
        annotations={
            "title": "List Gmail filters (auto-rules)",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gmail_list_filters(params: ListFiltersInput) -> str:
        """Return all Gmail filters (server-side rules) on this account."""
        try:
            resp = _service().users().settings().filters().list(userId="me").execute()
            return json.dumps(resp.get("filter", []), indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_create_filter",
        annotations={
            "title": "Create a Gmail filter",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def gmail_create_filter(params: CreateFilterInput) -> str:
        """Create a server-side filter that fires automatically on new incoming mail."""
        try:
            criteria: dict = {}
            if params.from_:
                criteria["from"] = params.from_
            if params.to:
                criteria["to"] = params.to
            if params.subject:
                criteria["subject"] = params.subject
            if params.query:
                criteria["query"] = params.query
            if params.has_attachment:
                criteria["hasAttachment"] = True
            if not criteria:
                return "Error: filter must have at least one criterion."

            action: dict = {}
            if params.add_label_ids:
                action["addLabelIds"] = params.add_label_ids
            if params.remove_label_ids:
                action["removeLabelIds"] = params.remove_label_ids
            if params.forward_to:
                action["forward"] = params.forward_to
            if not action:
                return "Error: filter must have at least one action."

            created = (
                _service()
                .users()
                .settings()
                .filters()
                .create(userId="me", body={"criteria": criteria, "action": action})
                .execute()
            )
            return json.dumps(created, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_delete_filter",
        annotations={
            "title": "Delete a Gmail filter",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gmail_delete_filter(params: DeleteFilterInput) -> str:
        """Delete a Gmail filter by ID."""
        try:
            _service().users().settings().filters().delete(
                userId="me", id=params.filter_id
            ).execute()
            return json.dumps({"status": "deleted", "filter_id": params.filter_id})
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="gmail_list_send_as",
        annotations={
            "title": "List Gmail send-as addresses",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def gmail_list_send_as(params: ListAliasesInput) -> str:
        """List all Send-As addresses (aliases + delegated addresses) on this account.

        Use the `sendAsEmail` value here as `from_alias` on gmail_send_email.
        """
        try:
            resp = _service().users().settings().sendAs().list(userId="me").execute()
            return json.dumps(resp.get("sendAs", []), indent=2)
        except Exception as e:
            return format_error(e)


def _list_attachments_meta(payload: dict, acc: list | None = None) -> list[dict]:
    """Walk a payload tree and return a flat list of attachment metadata."""
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


def _collect_attachments(svc, message_id: str, payload: dict) -> list[dict]:
    """Fetch attachment bytes from a message for re-sending (e.g. forwarding)."""
    out: list[dict] = []
    for meta in _list_attachments_meta(payload):
        data = (
            svc.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=meta["attachment_id"])
            .execute()
        )
        raw = base64.urlsafe_b64decode(data["data"])
        out.append(
            {
                "content_b64": base64.b64encode(raw).decode("ascii"),
                "filename": meta["filename"],
                "mime_type": meta["mime_type"],
            }
        )
    return out


def _extract_plaintext(payload: dict) -> str:
    """Walk a Gmail payload tree and concatenate any text/plain parts."""
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
