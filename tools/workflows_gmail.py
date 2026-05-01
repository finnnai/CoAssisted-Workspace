# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Gmail-driven workflows (send, attach, doc-export, mail-merge, OOO).

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
    _drive,
    _extract_plaintext,
    _gmail,
    _list_attachments_meta,
    _log_activity_on_contact,
    _parse_email_address,
    _resolve_recipient,
    _strip_reply_prefixes,
)

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

