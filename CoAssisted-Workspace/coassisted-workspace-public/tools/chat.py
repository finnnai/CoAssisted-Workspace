"""Google Chat tools: spaces, messages, memberships.

User-OAuth scope for Chat is narrower than the bot/app flow — these tools
cover what actually works on a personal/Workspace account:

    - List / get spaces (DMs, group chats, rooms the user is a member of)
    - List / get / send / update / delete messages in those spaces
    - List space members
    - Thread-aware sends (reply into an existing thread)

Creating new spaces or adding members through user auth is restricted by
Google; those are typically admin or Chat-app operations. Use the Google Chat
UI for those.

Resource names follow the API shape:
    spaces/AAAA123BBB
    spaces/AAAA123BBB/messages/UIA.MIDS123
    spaces/AAAA123BBB/members/users/123456789
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Optional

from googleapiclient.http import MediaIoBaseDownload
from pydantic import BaseModel, ConfigDict, Field

import gservices
from dryrun import dry_run_preview, is_dry_run
from errors import format_error
from logging_util import log
from retry import retry_call


def _service():
    return gservices.chat()


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class ListSpacesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    limit: int = Field(default=100, ge=1, le=1000)
    filter: Optional[str] = Field(
        default=None,
        description=(
            "Optional filter in Chat filter syntax. "
            "Examples: \"spaceType = 'SPACE'\" for rooms only, "
            "\"spaceType = 'DIRECT_MESSAGE'\" for DMs."
        ),
    )


class FindOrCreateDmInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: str = Field(
        ..., description="Recipient's email address (any domain).",
    )
    create_if_missing: bool = Field(
        default=True,
        description=(
            "If no DM space exists yet, create one. Cross-domain DMs require "
            "the recipient's organization to allow external Chat — the create "
            "will fail with a permission error if blocked."
        ),
    )


class GetSpaceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_name: str = Field(..., description="Space resource name (e.g. 'spaces/AAAA123').")


class ListMessagesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_name: str = Field(..., description="Space resource name.")
    limit: int = Field(default=25, ge=1, le=1000)
    order_by: str = Field(
        default="createTime desc",
        description="Sort order — 'createTime desc' (newest first) or 'createTime asc'.",
    )
    filter: Optional[str] = Field(
        default=None,
        description=(
            "Chat filter syntax. Example: \"createTime > \\\"2026-04-01T00:00:00Z\\\"\" "
            "to only get messages after a date."
        ),
    )


class GetMessageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message_name: str = Field(
        ...,
        description="Message resource name (e.g. 'spaces/AAAA123/messages/XYZ').",
    )


class SendMessageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_name: str = Field(..., description="Destination space resource name.")
    text: str = Field(..., description="Message body. Supports basic Chat markdown (*bold*, _italic_).")
    thread_name: Optional[str] = Field(
        default=None,
        description=(
            "If set, reply into an existing thread (e.g. 'spaces/X/threads/Y'). "
            "If omitted, starts a new thread."
        ),
    )
    thread_key: Optional[str] = Field(
        default=None,
        description=(
            "Custom thread key. When the same key is reused, messages group into one thread. "
            "Alternative to thread_name for idempotent threading."
        ),
    )
    reply_only_if_thread_exists: bool = Field(
        default=False,
        description=(
            "With thread_name or thread_key set: if True, fail instead of creating a new thread "
            "when the referenced thread doesn't exist."
        ),
    )
    dry_run: Optional[bool] = Field(default=None)


class UpdateMessageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message_name: str = Field(..., description="Message resource name to update.")
    text: str = Field(..., description="New message text.")
    dry_run: Optional[bool] = Field(default=None)


class DeleteMessageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message_name: str = Field(..., description="Message resource name to delete.")
    dry_run: Optional[bool] = Field(default=None)


class ListMembersInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_name: str = Field(..., description="Space resource name.")
    limit: int = Field(default=100, ge=1, le=1000)


class DownloadChatAttachmentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message_name: str = Field(
        ...,
        description="Message resource name containing the attachment (e.g. 'spaces/X/messages/Y').",
    )
    attachment_index: Optional[int] = Field(
        default=None,
        ge=0,
        description=(
            "0-based index into the message's attachments list. "
            "Omit to list every attachment on the message with metadata."
        ),
    )
    save_to_path: Optional[str] = Field(
        default=None,
        description=(
            "If given (with attachment_index set), write decoded bytes to this absolute "
            "path and return metadata only. Otherwise returns base64 in the response."
        ),
    )


# --------------------------------------------------------------------------- #
# Tier 1 — workflow gap closers
# --------------------------------------------------------------------------- #


class SendDmInput(BaseModel):
    """Resolve a DM space by recipient email and send a message in one call."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    email: str = Field(..., description="Recipient's email address.")
    text: str = Field(..., description="Message body. Supports Chat markdown.")
    create_dm_if_missing: bool = Field(
        default=True,
        description="Auto-create the DM space if none exists yet.",
    )
    log_to_contact: Optional[bool] = Field(
        default=None,
        description=(
            "Append a timestamped Chat activity note to the matching saved "
            "contact (if any). Defaults to config.log_sent_emails_to_contacts."
        ),
    )
    dry_run: Optional[bool] = Field(default=None)


class SendToSpaceByNameInput(BaseModel):
    """Find a space by display name and send a message."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_query: str = Field(
        ...,
        description=(
            "Substring to match against space display names (case-insensitive). "
            "Example: 'ISOC Briefing' matches 'Daily ISOC Briefing - Apr 25'."
        ),
    )
    text: str = Field(..., description="Message body.")
    fail_on_multiple: bool = Field(
        default=True,
        description=(
            "If True (default) and the query matches multiple spaces, fail with "
            "the candidate list so you can disambiguate. If False, send to the "
            "most recently created match."
        ),
    )
    dry_run: Optional[bool] = Field(default=None)


class SearchChatInput(BaseModel):
    """Search messages across recent spaces by content/sender/date."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: Optional[str] = Field(
        default=None,
        description="Substring to match in message text (case-insensitive). Optional.",
    )
    sender: Optional[str] = Field(
        default=None,
        description="Filter to messages from a specific user, by email or 'users/123' resource.",
    )
    days: int = Field(
        default=14, ge=1, le=365,
        description="How many days back to search. Default 14.",
    )
    space_filter: Optional[str] = Field(
        default=None,
        description="Optional substring on space display name (e.g. 'ISOC').",
    )
    limit_per_space: int = Field(
        default=200, ge=10, le=1000,
        description="Max messages to fetch per space when scanning.",
    )
    limit_total: int = Field(
        default=50, ge=1, le=500,
        description="Max matching messages to return.",
    )


class WhoIsInDmInput(BaseModel):
    """Identify the other party in a 1:1 DM space."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_name: str = Field(..., description="DM space resource name.")


# --------------------------------------------------------------------------- #
# Tier 2 — bigger projects
# --------------------------------------------------------------------------- #


class SendChatAttachmentInput(BaseModel):
    """Send a file (from disk OR base64) into a Chat space."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    space_name: str = Field(..., description="Destination space.")
    text: Optional[str] = Field(
        default=None,
        description="Optional message text to send alongside the file.",
    )
    path: Optional[str] = Field(
        default=None, description="Absolute path to a local file. Mutually exclusive with content_b64."
    )
    content_b64: Optional[str] = Field(
        default=None, description="Base64-encoded file bytes. Mutually exclusive with path."
    )
    filename: Optional[str] = Field(
        default=None,
        description="Display filename (defaults to basename of path or 'attachment.bin').",
    )
    thread_name: Optional[str] = Field(default=None)
    dry_run: Optional[bool] = Field(default=None)


class RecentChatActivityInput(BaseModel):
    """List spaces with messages since a cutoff, sorted by recency."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    hours: int = Field(
        default=24, ge=1, le=720,
        description="Look-back window in hours. Default 24.",
    )
    space_type: Optional[str] = Field(
        default=None,
        description="Optional: 'SPACE' for rooms only, 'DIRECT_MESSAGE' for DMs only.",
    )
    limit: int = Field(default=50, ge=1, le=200)


class ChatDigestInput(BaseModel):
    """Generate a daily Chat digest and email it to yourself."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    hours: int = Field(default=24, ge=1, le=168, description="Look-back window. Default 24h.")
    recipient: Optional[str] = Field(
        default=None,
        description="Email recipient for the digest. Defaults to your own primary address.",
    )
    use_llm: bool = Field(
        default=True,
        description=(
            "If True (default) and ANTHROPIC_API_KEY is configured, summarize via Claude. "
            "Otherwise emit a structured per-space message list."
        ),
    )
    dry_run: Optional[bool] = Field(default=None)


class ChatToContactGroupInput(BaseModel):
    """Send personalized Chat DMs to every saved contact in a group."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    group_resource_name: str = Field(
        ..., description="People API contact group (e.g. 'contactGroups/abc123')."
    )
    text: str = Field(
        ...,
        description=(
            "Message body. Supports {first_name|fallback}, {last_name}, "
            "{organization}, {title}, and any custom field as {custom.<key>}."
        ),
    )
    create_dm_if_missing: bool = Field(default=True)
    log_to_contact: bool = Field(
        default=True, description="Append a Chat activity note to each recipient's biography."
    )
    stop_on_first_error: bool = Field(default=False)
    dry_run: Optional[bool] = Field(default=None)


# --------------------------------------------------------------------------- #
# Tier 3 — polish
# --------------------------------------------------------------------------- #


class ReactToMessageInput(BaseModel):
    """Add an emoji reaction to a message."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    message_name: str = Field(..., description="Message resource name to react to.")
    emoji: str = Field(
        ...,
        description=(
            "Unicode emoji character (e.g. '👍', '🚀', '🎯', '✅'). The Chat API "
            "rejects emoji shortcodes — use the actual character."
        ),
    )


class GetThreadInput(BaseModel):
    """Fetch every message in a Chat thread (parent + replies)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    thread_name: str = Field(
        ...,
        description="Thread resource name (e.g. 'spaces/X/threads/Y').",
    )
    limit: int = Field(default=100, ge=1, le=1000)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="chat_list_spaces",
        annotations={
            "title": "List Google Chat spaces (DMs, group chats, rooms)",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_list_spaces(params: ListSpacesInput) -> str:
        """List every Chat space the authenticated user is a member of.

        Returns name (resource ID), display name, space type, and member count.
        Use `filter` to narrow (e.g., only rooms, only DMs).
        """
        try:
            kwargs = {"pageSize": params.limit}
            if params.filter:
                kwargs["filter"] = params.filter
            resp = retry_call(lambda: _service().spaces().list(**kwargs).execute())
            out = [
                {
                    "name": s["name"],
                    "display_name": s.get("displayName"),
                    "type": s.get("spaceType"),
                    "single_user_bot_dm": s.get("singleUserBotDm", False),
                    "threaded": s.get("spaceThreadingState"),
                    "create_time": s.get("createTime"),
                }
                for s in resp.get("spaces", [])
            ]
            return json.dumps({"count": len(out), "spaces": out}, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="chat_find_or_create_dm",
        annotations={
            "title": "Find or create a 1:1 DM space with someone by email",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_find_or_create_dm(params: FindOrCreateDmInput) -> str:
        """Look up an existing DM space with `email`, or create one if missing.

        Returns the space resource name (e.g. `spaces/AAAAxxxx`) which you can
        feed into `chat_send_message`. This removes the "open Chat in browser
        first to seed a DM space" friction.

        Caveats:
            * Cross-domain: if the recipient is in another Google Workspace
              org, that org must allow external Chat. Otherwise the create
              call fails with `403 PERMISSION_DENIED` and we surface a clear
              error message.
            * The recipient must have a Google account on the email you give.
        """
        try:
            email = params.email.strip()
            if "@" not in email:
                return f"Error: '{email}' is not a valid email address."

            svc = _service()
            user_resource = f"users/{email}"

            # 1. Try to find an existing DM.
            try:
                space = svc.spaces().findDirectMessage(name=user_resource).execute()
                if space and space.get("name"):
                    log.info("chat_find_or_create_dm: existing DM with %s → %s",
                             email, space["name"])
                    return json.dumps({
                        "status": "found_existing",
                        "space_name": space["name"],
                        "type": space.get("spaceType", "DIRECT_MESSAGE"),
                        "email": email,
                    }, indent=2)
            except Exception as e:
                # 404 here means no DM yet — fall through to create.
                if "404" not in str(e) and "Not found" not in str(e):
                    log.warning("chat_find_or_create_dm: findDirectMessage error: %s", e)

            # 2. No DM found. Create one if allowed.
            if not params.create_if_missing:
                return json.dumps({
                    "status": "not_found",
                    "email": email,
                    "hint": (
                        "No existing DM space. Pass `create_if_missing=True` to "
                        "auto-create one, or open Google Chat in your browser "
                        "and send any message to seed the space."
                    ),
                }, indent=2)

            try:
                created = svc.spaces().setup(body={
                    "space": {"spaceType": "DIRECT_MESSAGE"},
                    "memberships": [
                        {"member": {"name": user_resource, "type": "HUMAN"}}
                    ],
                }).execute()
                log.info(
                    "chat_find_or_create_dm: created DM with %s → %s",
                    email, created.get("name"),
                )
                return json.dumps({
                    "status": "created",
                    "space_name": created.get("name"),
                    "type": created.get("spaceType", "DIRECT_MESSAGE"),
                    "email": email,
                }, indent=2)
            except Exception as inner:
                err = str(inner)
                hint = "Check that the recipient has a Google account at this address."
                if "403" in err or "PERMISSION_DENIED" in err.upper():
                    hint = (
                        f"The recipient's organization may block external Chat from your "
                        f"domain. Either {email}'s admin needs to allow external Chat, or "
                        f"have them DM you first in the Google Chat UI to bootstrap the space."
                    )
                elif "404" in err or "not found" in err.lower():
                    hint = f"No Google account found for {email}."
                return json.dumps({
                    "status": "create_failed",
                    "email": email,
                    "error": err,
                    "hint": hint,
                }, indent=2)
        except Exception as e:
            log.error("chat_find_or_create_dm failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="chat_get_space",
        annotations={
            "title": "Get a Google Chat space",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_get_space(params: GetSpaceInput) -> str:
        """Return full details on a single Chat space."""
        try:
            space = retry_call(
                lambda: _service().spaces().get(name=params.space_name).execute()
            )
            return json.dumps(space, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="chat_list_messages",
        annotations={
            "title": "List messages in a Chat space",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_list_messages(params: ListMessagesInput) -> str:
        """List messages in a space with optional time filter and sort order.

        Returns trimmed message records (name, sender, text, thread, createTime).
        Pagination beyond `limit` isn't exposed here — narrow via `filter` if
        you need more precision.
        """
        try:
            kwargs: dict = {
                "parent": params.space_name,
                "pageSize": params.limit,
                "orderBy": params.order_by,
            }
            if params.filter:
                kwargs["filter"] = params.filter
            resp = retry_call(
                lambda: _service().spaces().messages().list(**kwargs).execute()
            )
            out = []
            for m in resp.get("messages", []):
                sender = m.get("sender") or {}
                out.append(
                    {
                        "name": m["name"],
                        "sender_name": sender.get("name"),
                        "sender_display": sender.get("displayName"),
                        "sender_type": sender.get("type"),
                        "thread": (m.get("thread") or {}).get("name"),
                        "text": m.get("text"),
                        "create_time": m.get("createTime"),
                    }
                )
            return json.dumps({"count": len(out), "messages": out}, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="chat_get_message",
        annotations={
            "title": "Get a single Chat message",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_get_message(params: GetMessageInput) -> str:
        """Fetch one message by resource name (full payload, including attachments)."""
        try:
            msg = retry_call(
                lambda: _service().spaces().messages().get(name=params.message_name).execute()
            )
            return json.dumps(msg, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="chat_send_message",
        annotations={
            "title": "Send a message to a Chat space",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def chat_send_message(params: SendMessageInput) -> str:
        """Send a message to a space. Can start a new thread or reply to an existing one.

        Chat markdown supported in `text`: *bold*, _italic_, ~strike~, `code`,
        ```code block```, plus [links](url). User mentions: <users/123456789>.
        """
        try:
            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "chat_send_message",
                    {
                        "space": params.space_name,
                        "thread": params.thread_name or params.thread_key,
                        "text_preview": params.text[:200],
                    },
                )

            body: dict = {"text": params.text}
            if params.thread_name or params.thread_key:
                body["thread"] = {}
                if params.thread_name:
                    body["thread"]["name"] = params.thread_name
                if params.thread_key:
                    body["thread"]["threadKey"] = params.thread_key

            kwargs = {"parent": params.space_name, "body": body}
            if params.thread_name or params.thread_key:
                kwargs["messageReplyOption"] = (
                    "REPLY_MESSAGE_OR_FAIL"
                    if params.reply_only_if_thread_exists
                    else "REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD"
                )

            sent = retry_call(
                lambda: _service().spaces().messages().create(**kwargs).execute()
            )
            log.info(
                "chat_send_message space=%s thread=%s",
                params.space_name,
                params.thread_name or params.thread_key,
            )
            return json.dumps(
                {
                    "status": "sent",
                    "name": sent["name"],
                    "thread": (sent.get("thread") or {}).get("name"),
                    "create_time": sent.get("createTime"),
                },
                indent=2,
            )
        except Exception as e:
            log.error("chat_send_message failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="chat_update_message",
        annotations={
            "title": "Edit a Chat message",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_update_message(params: UpdateMessageInput) -> str:
        """Edit the text of a message you previously sent."""
        try:
            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "chat_update_message",
                    {"message": params.message_name, "new_text_preview": params.text[:200]},
                )
            updated = retry_call(
                lambda: _service()
                .spaces()
                .messages()
                .update(
                    name=params.message_name,
                    updateMask="text",
                    body={"text": params.text},
                )
                .execute()
            )
            return json.dumps(
                {"status": "updated", "name": updated["name"]}, indent=2
            )
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="chat_delete_message",
        annotations={
            "title": "Delete a Chat message",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_delete_message(params: DeleteMessageInput) -> str:
        """Delete a message you previously sent. Cannot be undone."""
        try:
            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "chat_delete_message", {"message": params.message_name}
                )
            retry_call(
                lambda: _service().spaces().messages().delete(name=params.message_name).execute()
            )
            return json.dumps({"status": "deleted", "name": params.message_name})
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="chat_list_members",
        annotations={
            "title": "List members of a Chat space",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_list_members(params: ListMembersInput) -> str:
        """List human and bot members in a Chat space."""
        try:
            resp = retry_call(
                lambda: _service()
                .spaces()
                .members()
                .list(parent=params.space_name, pageSize=params.limit)
                .execute()
            )
            out = []
            for m in resp.get("memberships", []):
                member = m.get("member") or {}
                out.append(
                    {
                        "name": m.get("name"),
                        "member_name": member.get("name"),
                        "display_name": member.get("displayName"),
                        "type": member.get("type"),
                        "role": m.get("role"),
                        "create_time": m.get("createTime"),
                    }
                )
            return json.dumps({"count": len(out), "members": out}, indent=2)
        except Exception as e:
            return format_error(e)

    @mcp.tool(
        name="chat_download_attachment",
        annotations={
            "title": "Download an attachment from a Chat message",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_download_attachment(params: DownloadChatAttachmentInput) -> str:
        """Download an attachment from a Chat message.

        If `attachment_index` is omitted, lists every attachment on the message
        with its index, filename, content type, source, and size hint.

        If `attachment_index` is given:
          * Uploaded Chat attachments are fetched via the Media API and returned
            as base64, OR written to `save_to_path` if provided.
          * Drive-linked attachments return an error with the Drive file ID —
            use `drive_download_binary_file` for those (they live in Drive, not
            in Chat storage).
        """
        try:
            svc = _service()
            msg = retry_call(
                lambda: svc.spaces().messages().get(name=params.message_name).execute()
            )
            # Chat API stores attachments under the singular key "attachment".
            attachments = msg.get("attachment", []) or []

            if not attachments:
                return json.dumps(
                    {"message_name": params.message_name, "count": 0, "attachments": []},
                    indent=2,
                )

            # List mode — no index → return metadata for all attachments.
            if params.attachment_index is None:
                listing = []
                for i, att in enumerate(attachments):
                    data_ref = att.get("attachmentDataRef") or {}
                    drive_ref = att.get("driveDataRef") or {}
                    listing.append(
                        {
                            "index": i,
                            "content_name": att.get("contentName"),
                            "content_type": att.get("contentType"),
                            "source": att.get("source"),
                            "download_uri": att.get("downloadUri"),
                            "thumbnail_uri": att.get("thumbnailUri"),
                            "attachment_resource_name": att.get("name"),
                            "attachment_data_ref": data_ref.get("resourceName"),
                            "drive_file_id": drive_ref.get("driveFileId"),
                        }
                    )
                return json.dumps(
                    {
                        "message_name": params.message_name,
                        "count": len(listing),
                        "attachments": listing,
                    },
                    indent=2,
                )

            # Download mode.
            if params.attachment_index >= len(attachments):
                return (
                    f"Error: attachment_index {params.attachment_index} is out of range "
                    f"(message has {len(attachments)} attachments)."
                )

            att = attachments[params.attachment_index]
            content_name = att.get("contentName") or f"attachment_{params.attachment_index}"
            content_type = att.get("contentType") or "application/octet-stream"
            drive_ref = att.get("driveDataRef") or {}

            # Drive-linked attachments aren't served through the Chat media API.
            if drive_ref.get("driveFileId"):
                return (
                    f"Error: this attachment is a Drive file reference "
                    f"(driveFileId={drive_ref['driveFileId']}). Use "
                    f"drive_download_binary_file with that file_id to download it."
                )

            data_ref = att.get("attachmentDataRef") or {}
            resource_name = data_ref.get("resourceName")
            if not resource_name:
                return (
                    "Error: attachment has no attachmentDataRef.resourceName — "
                    "nothing to download. It may be an external link or an unsupported type."
                )

            buf = io.BytesIO()
            req = svc.media().download_media(resourceName=resource_name)
            downloader = MediaIoBaseDownload(buf, req)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            data = buf.getvalue()

            if params.save_to_path:
                path = Path(params.save_to_path).expanduser()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                log.info(
                    "chat_download_attachment saved %s (%d bytes) from %s",
                    path, len(data), params.message_name,
                )
                return json.dumps(
                    {
                        "status": "saved",
                        "path": str(path),
                        "content_name": content_name,
                        "content_type": content_type,
                        "size": len(data),
                    },
                    indent=2,
                )

            # Hard cap on inline base64 returns. Auto-save if exceeded.
            import config as _config
            max_inline = int(_config.get("max_inline_download_kb", 5120)) * 1024
            if len(data) > max_inline:
                auto_path = _config.resolve_auto_download_path(content_name)
                auto_path.write_bytes(data)
                log.info(
                    "chat_download_attachment auto-saved %s (%d KB > %d KB cap) to %s",
                    content_name, len(data) // 1024, max_inline // 1024, auto_path,
                )
                return json.dumps(
                    {
                        "status": "auto_saved",
                        "path": str(auto_path),
                        "content_name": content_name,
                        "content_type": content_type,
                        "size": len(data),
                        "size_kb": round(len(data) / 1024),
                        "max_inline_kb": max_inline // 1024,
                        "note": (
                            f"Attachment exceeded max_inline_download_kb ({max_inline // 1024} KB) "
                            "and was auto-saved to default_download_dir."
                        ),
                    },
                    indent=2,
                )

            return json.dumps(
                {
                    "content_name": content_name,
                    "content_type": content_type,
                    "size": len(data),
                    "content_b64": base64.b64encode(data).decode("ascii"),
                },
                indent=2,
            )
        except Exception as e:
            log.error("chat_download_attachment failed: %s", e)
            return format_error(e)

    # ----------------------------------------------------------------- #
    # Tier 1 — workflow gap closers
    # ----------------------------------------------------------------- #

    @mcp.tool(
        name="chat_send_dm",
        annotations={
            "title": "Send a Chat DM by recipient email (find-or-create + send)",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def chat_send_dm(params: SendDmInput) -> str:
        """Resolve a DM space by email and send a message — one tool, no juggling.

        Combines `chat_find_or_create_dm` + `chat_send_message`. If no DM exists
        and `create_dm_if_missing=True` (default), creates one first. Cross-domain
        DMs require the recipient's Workspace to allow external Chat — failures
        return a clear `403 PERMISSION_DENIED` with admin-action hints.
        """
        try:
            email = params.email.strip()
            if "@" not in email:
                return f"Error: '{email}' is not a valid email address."

            svc = _service()

            # 1. Find or create the DM space.
            user_resource = f"users/{email}"
            space_name = None
            status_origin = None
            try:
                existing = svc.spaces().findDirectMessage(name=user_resource).execute()
                if existing and existing.get("name"):
                    space_name = existing["name"]
                    status_origin = "found_existing"
            except Exception as e:
                if "404" not in str(e) and "Not found" not in str(e):
                    log.warning("chat_send_dm: findDirectMessage error: %s", e)

            if not space_name:
                if not params.create_dm_if_missing:
                    return json.dumps({
                        "status": "no_dm_space",
                        "email": email,
                        "hint": (
                            "No existing DM. Pass create_dm_if_missing=True or "
                            "have the recipient DM you first."
                        ),
                    }, indent=2)
                try:
                    created = svc.spaces().setup(body={
                        "space": {"spaceType": "DIRECT_MESSAGE"},
                        "memberships": [
                            {"member": {"name": user_resource, "type": "HUMAN"}}
                        ],
                    }).execute()
                    space_name = created.get("name")
                    status_origin = "created"
                except Exception as e:
                    err = str(e)
                    hint = "Recipient may not have a Google account at this email."
                    if "403" in err or "PERMISSION_DENIED" in err.upper():
                        hint = (
                            f"Cross-domain Chat blocked. {email}'s org may restrict "
                            "external Chat. Their Workspace admin needs to allow it, "
                            "or have them DM you first to bootstrap the space."
                        )
                    elif "404" in err or "not found" in err.lower():
                        hint = f"No Google account found for {email}."
                    return json.dumps({
                        "status": "dm_create_failed",
                        "email": email,
                        "error": err,
                        "hint": hint,
                    }, indent=2)

            # 2. Dry-run preview (after we know the space) — no send.
            if is_dry_run(params.dry_run):
                return dry_run_preview("chat_send_dm", {
                    "email": email,
                    "space_name": space_name,
                    "status_origin": status_origin,
                    "text_preview": params.text[:400],
                })

            # 3. Send.
            sent = svc.spaces().messages().create(
                parent=space_name,
                body={"text": params.text},
            ).execute()

            log.info("chat_send_dm: sent to %s in %s", email, space_name)

            # 4. Optional: log activity to matching saved contact.
            log_flag = (
                params.log_to_contact
                if params.log_to_contact is not None
                else bool(__import__("config").get("log_sent_emails_to_contacts", True))
            )
            if log_flag:
                try:
                    _log_chat_activity_on_contact(email, params.text)
                except Exception as inner:
                    log.warning("chat_send_dm: activity log skipped (%s)", inner)

            return json.dumps({
                "status": "sent",
                "dm_space_origin": status_origin,
                "space_name": space_name,
                "message_name": sent.get("name"),
                "thread": sent.get("thread", {}).get("name"),
                "create_time": sent.get("createTime"),
            }, indent=2)
        except Exception as e:
            log.error("chat_send_dm failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="chat_send_to_space_by_name",
        annotations={
            "title": "Send a Chat message to a space matched by display name",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def chat_send_to_space_by_name(params: SendToSpaceByNameInput) -> str:
        """Find a space by substring of its display name (case-insensitive) and send.

        If multiple spaces match and `fail_on_multiple=True` (default), returns
        the candidate list so you can disambiguate. With `fail_on_multiple=False`,
        sends to the most recently created match.
        """
        try:
            svc = _service()
            query_lower = params.space_query.lower().strip()

            # Pull all spaces. We list every type because the user gave a name, not a type.
            all_spaces: list[dict] = []
            page_token = None
            while True:
                kwargs: dict = {"pageSize": 1000}
                if page_token:
                    kwargs["pageToken"] = page_token
                resp = svc.spaces().list(**kwargs).execute()
                all_spaces.extend(resp.get("spaces") or [])
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

            # Match by display name (case-insensitive substring).
            matches = [
                s for s in all_spaces
                if s.get("displayName") and query_lower in s["displayName"].lower()
            ]
            if not matches:
                return json.dumps({
                    "status": "no_match",
                    "query": params.space_query,
                    "hint": "Run chat_list_spaces to see available display names.",
                }, indent=2)

            if len(matches) > 1:
                if params.fail_on_multiple:
                    return json.dumps({
                        "status": "ambiguous",
                        "query": params.space_query,
                        "candidates": [
                            {"name": m["name"], "display_name": m.get("displayName"),
                             "type": m.get("spaceType")}
                            for m in matches
                        ],
                        "hint": "Refine `space_query` or pass fail_on_multiple=false.",
                    }, indent=2)
                # Take the most recently created.
                matches.sort(key=lambda s: s.get("createTime") or "", reverse=True)

            target = matches[0]
            target_name = target["name"]

            if is_dry_run(params.dry_run):
                return dry_run_preview("chat_send_to_space_by_name", {
                    "space_name": target_name,
                    "display_name": target.get("displayName"),
                    "text_preview": params.text[:400],
                })

            sent = svc.spaces().messages().create(
                parent=target_name,
                body={"text": params.text},
            ).execute()
            log.info(
                "chat_send_to_space_by_name: sent to %s (%s)",
                target_name, target.get("displayName"),
            )
            return json.dumps({
                "status": "sent",
                "space_name": target_name,
                "display_name": target.get("displayName"),
                "message_name": sent.get("name"),
                "thread": sent.get("thread", {}).get("name"),
            }, indent=2)
        except Exception as e:
            log.error("chat_send_to_space_by_name failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="chat_search",
        annotations={
            "title": "Search Chat messages across spaces",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_search(params: SearchChatInput) -> str:
        """Search recent messages across your spaces.

        Walks every space you're in, fetches messages within the time window,
        and filters in-process by:
          - text substring (`query`, case-insensitive)
          - sender (email or 'users/...')
          - space display-name substring
        Returns up to `limit_total` matches sorted newest-first.

        Note: Chat has no native cross-space search API. This tool walks each
        space client-side, which is fine for typical usage but may be slow if
        you're in 100+ spaces with heavy message volume. Tune `limit_per_space`
        and `days` to keep it fast.
        """
        import datetime as _dt

        try:
            svc = _service()
            cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=params.days)
            cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
            time_filter = f'createTime > "{cutoff_iso}"'

            sender_lower = (params.sender or "").lower().strip()
            query_lower = (params.query or "").lower().strip()
            space_filter_lower = (params.space_filter or "").lower().strip()

            # 1. Collect all spaces (optionally filtered by display name).
            all_spaces: list[dict] = []
            page_token = None
            while True:
                kwargs: dict = {"pageSize": 1000}
                if page_token:
                    kwargs["pageToken"] = page_token
                resp = svc.spaces().list(**kwargs).execute()
                for s in resp.get("spaces") or []:
                    if space_filter_lower and (
                        not s.get("displayName")
                        or space_filter_lower not in s["displayName"].lower()
                    ):
                        continue
                    all_spaces.append(s)
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

            # 2. For each space, fetch recent messages and filter.
            matches: list[dict] = []
            for space in all_spaces:
                try:
                    page_token = None
                    fetched = 0
                    while fetched < params.limit_per_space:
                        kwargs = {
                            "parent": space["name"],
                            "filter": time_filter,
                            "pageSize": min(1000, params.limit_per_space - fetched),
                            "orderBy": "createTime desc",
                        }
                        if page_token:
                            kwargs["pageToken"] = page_token
                        resp = svc.spaces().messages().list(**kwargs).execute()
                        msgs = resp.get("messages") or []
                        for m in msgs:
                            sender_obj = m.get("sender") or {}
                            sender_name = (sender_obj.get("name") or "").lower()
                            sender_display = (sender_obj.get("displayName") or "").lower()
                            text = m.get("text") or ""
                            text_lower = text.lower()

                            if sender_lower:
                                if (
                                    sender_lower not in sender_name
                                    and sender_lower not in sender_display
                                ):
                                    continue
                            if query_lower and query_lower not in text_lower:
                                continue
                            matches.append({
                                "message_name": m.get("name"),
                                "thread": m.get("thread", {}).get("name"),
                                "space_name": space["name"],
                                "space_display": (
                                    space.get("displayName") or "(direct message)"
                                ),
                                "sender_display": (
                                    sender_obj.get("displayName")
                                    or ("(you)" if sender_obj.get("name") else "(unknown)")
                                ),
                                "sender_resource": sender_obj.get("name"),
                                "text_preview": text[:200],
                                "create_time": m.get("createTime"),
                            })
                            if len(matches) >= params.limit_total:
                                break
                        if len(matches) >= params.limit_total:
                            break
                        fetched += len(msgs)
                        page_token = resp.get("nextPageToken")
                        if not page_token:
                            break
                    if len(matches) >= params.limit_total:
                        break
                except Exception as inner:
                    log.warning(
                        "chat_search: scan of %s failed: %s", space.get("name"), inner
                    )

            # Sort by createTime desc.
            matches.sort(key=lambda m: m.get("create_time") or "", reverse=True)
            matches = matches[: params.limit_total]

            return json.dumps({
                "spaces_scanned": len(all_spaces),
                "matches_found": len(matches),
                "matches": matches,
            }, indent=2)
        except Exception as e:
            log.error("chat_search failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="chat_who_is_in_dm",
        annotations={
            "title": "Identify the other party in a 1:1 Chat DM",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_who_is_in_dm(params: WhoIsInDmInput) -> str:
        """Return the other party's identity in a DM space.

        Google's user-OAuth Chat API hides external user identities from
        `members.list` for privacy — but it DOES return your own membership.
        This tool uses that quirk: members.list returns only you, so we
        capture your numeric user ID, then scan recent messages for a sender
        whose ID differs from yours.

        Returns the other party's display name + resource name. If only your
        own messages exist in the recent window (you DM'd them but they
        haven't replied yet), returns `unknown_yet`.
        """
        try:
            svc = _service()

            # 1. Get my numeric user ID. The privacy filter on DMs returns
            #    ONLY me from members.list — so the first membership IS me.
            my_user_resource = None
            try:
                memb_resp = svc.spaces().members().list(
                    parent=params.space_name, pageSize=10,
                ).execute()
                for m in memb_resp.get("memberships") or []:
                    cand = (m.get("member") or {}).get("name")
                    # `users/<id>` form. The DM filter typically returns just self.
                    if cand and cand.startswith("users/"):
                        my_user_resource = cand
                        break
            except Exception as e:
                log.warning("chat_who_is_in_dm: members.list fallback (%s)", e)

            # 2. List recent messages newest-first.
            resp = svc.spaces().messages().list(
                parent=params.space_name,
                pageSize=20,
                orderBy="createTime desc",
            ).execute()
            msgs = resp.get("messages") or []
            if not msgs:
                return json.dumps({
                    "status": "no_messages",
                    "space_name": params.space_name,
                }, indent=2)

            # 3. Find first sender whose resource differs from mine.
            other_party = None
            for m in msgs:
                sender = m.get("sender") or {}
                sname = sender.get("name") or ""
                if sender.get("type") == "BOT":
                    continue
                if my_user_resource and sname == my_user_resource:
                    continue
                if sname:
                    other_party = sender
                    break

            if not other_party:
                return json.dumps({
                    "status": "unknown_yet",
                    "space_name": params.space_name,
                    "your_user_resource": my_user_resource,
                    "hint": (
                        "No non-self messages in the recent window. The API "
                        "doesn't reveal the other party's identity until they "
                        "post. Try again after they reply."
                    ),
                }, indent=2)

            return json.dumps({
                "status": "ok",
                "space_name": params.space_name,
                "other_party": {
                    "display_name": other_party.get("displayName"),
                    "resource_name": other_party.get("name"),
                    "type": other_party.get("type"),
                },
            }, indent=2)
        except Exception as e:
            log.error("chat_who_is_in_dm failed: %s", e)
            return format_error(e)

    # ----------------------------------------------------------------- #
    # Tier 2 (chat-side) — bigger projects
    # ----------------------------------------------------------------- #

    @mcp.tool(
        name="chat_send_attachment",
        annotations={
            "title": "Send a file as a Chat message attachment",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def chat_send_attachment(params: SendChatAttachmentInput) -> str:
        """Upload a file and post it as a Chat message attachment.

        Two upload sources supported (mutually exclusive):
            - `path`: read from disk (recommended for files on your Mac)
            - `content_b64`: inline base64 (for in-memory data)

        Workflow: media().upload() → spaces.messages.create with attachment_data
        referencing the upload's resourceName. Limits: Chat caps file size at
        200MB. Larger files should go through Drive (`drive_upload_binary_file`)
        and you can paste the link into a regular `chat_send_message` instead.
        """
        try:
            from googleapiclient.http import MediaInMemoryUpload, MediaFileUpload
            import mimetypes

            if not (params.path or params.content_b64):
                return "Error: provide either path or content_b64."
            if params.path and params.content_b64:
                return "Error: pass only one of path/content_b64, not both."

            # Resolve filename + mime.
            if params.path:
                p = Path(params.path).expanduser()
                if not p.is_file():
                    return f"Error: file not found at {p}"
                size = p.stat().st_size
                if size > 200 * 1024 * 1024:
                    return (
                        f"Error: file is {size // (1024*1024)}MB; Chat caps "
                        "attachments at 200MB. Upload via drive_upload_binary_file "
                        "and share the link in chat_send_message instead."
                    )
                filename = params.filename or p.name
                mime = (
                    mimetypes.guess_type(filename)[0]
                    or "application/octet-stream"
                )
            else:
                data = base64.b64decode(params.content_b64)
                size = len(data)
                if size > 200 * 1024 * 1024:
                    return (
                        f"Error: payload is {size // (1024*1024)}MB; over Chat's 200MB cap."
                    )
                filename = params.filename or "attachment.bin"
                mime = (
                    mimetypes.guess_type(filename)[0]
                    or "application/octet-stream"
                )

            if is_dry_run(params.dry_run):
                return dry_run_preview("chat_send_attachment", {
                    "space_name": params.space_name,
                    "filename": filename,
                    "mime_type": mime,
                    "size": size,
                    "text_preview": (params.text or "")[:400],
                })

            svc = _service()

            # Upload media. Chat API: spaces.messages.attachments.upload
            if params.path:
                media = MediaFileUpload(str(p), mimetype=mime, resumable=True)
            else:
                media = MediaInMemoryUpload(data, mimetype=mime, resumable=True)

            uploaded = svc.media().upload(
                parent=params.space_name,
                body={"filename": filename},
                media_body=media,
            ).execute()
            attachment_data_ref = uploaded.get("attachmentDataRef") or {}

            # Now send a message with the attachment.
            msg_body: dict = {
                "attachment": [
                    {
                        "contentName": filename,
                        "contentType": mime,
                        "attachmentDataRef": attachment_data_ref,
                    }
                ]
            }
            if params.text:
                msg_body["text"] = params.text
            if params.thread_name:
                msg_body["thread"] = {"name": params.thread_name}

            sent = svc.spaces().messages().create(
                parent=params.space_name,
                body=msg_body,
                messageReplyOption=(
                    "REPLY_MESSAGE_OR_FAIL" if params.thread_name else "REPLY_MESSAGE_OR_FAIL"
                ) if params.thread_name else None,
            ).execute()

            log.info(
                "chat_send_attachment: sent %s (%d bytes) to %s",
                filename, size, params.space_name,
            )
            return json.dumps({
                "status": "sent",
                "filename": filename,
                "size": size,
                "mime_type": mime,
                "space_name": params.space_name,
                "message_name": sent.get("name"),
                "thread": sent.get("thread", {}).get("name"),
            }, indent=2)
        except Exception as e:
            log.error("chat_send_attachment failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="chat_recent_activity",
        annotations={
            "title": "List Chat spaces with messages in the last N hours",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_recent_activity(params: RecentChatActivityInput) -> str:
        """Catch-up tool: which spaces have new messages, and how many.

        Walks every space, queries messages with `createTime > <cutoff>`, and
        returns spaces ordered by most-recent message timestamp. For each space,
        returns the message count, the latest sender, and a preview of the
        latest message's text.
        """
        import datetime as _dt

        try:
            svc = _service()
            cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=params.hours)
            cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
            time_filter = f'createTime > "{cutoff_iso}"'

            # 1. List spaces (optionally filter by type).
            all_spaces: list[dict] = []
            page_token = None
            while True:
                kwargs: dict = {"pageSize": 1000}
                if page_token:
                    kwargs["pageToken"] = page_token
                resp = svc.spaces().list(**kwargs).execute()
                for s in resp.get("spaces") or []:
                    if params.space_type and s.get("spaceType") != params.space_type:
                        continue
                    all_spaces.append(s)
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

            # 2. Per space, count + sample latest.
            results: list[dict] = []
            for space in all_spaces:
                try:
                    resp = svc.spaces().messages().list(
                        parent=space["name"],
                        filter=time_filter,
                        pageSize=100,
                        orderBy="createTime desc",
                    ).execute()
                    msgs = resp.get("messages") or []
                    if not msgs:
                        continue
                    latest = msgs[0]
                    sender_obj = latest.get("sender") or {}
                    # Google omits displayName for self in DMs — fall back so
                    # output is always readable.
                    sender_label = (
                        sender_obj.get("displayName")
                        or ("(you)" if sender_obj.get("name") else "(unknown)")
                    )
                    results.append({
                        "space_name": space["name"],
                        "display_name": space.get("displayName"),
                        "type": space.get("spaceType"),
                        "message_count": len(msgs),
                        "latest_sender": sender_label,
                        "latest_sender_resource": sender_obj.get("name"),
                        "latest_create_time": latest.get("createTime"),
                        "latest_preview": (latest.get("text") or "")[:200],
                    })
                except Exception as inner:
                    log.warning(
                        "chat_recent_activity: %s failed: %s", space.get("name"), inner
                    )

            results.sort(key=lambda r: r.get("latest_create_time") or "", reverse=True)
            results = results[: params.limit]

            return json.dumps({
                "window_hours": params.hours,
                "spaces_with_activity": len(results),
                "results": results,
            }, indent=2)
        except Exception as e:
            log.error("chat_recent_activity failed: %s", e)
            return format_error(e)

    # ----------------------------------------------------------------- #
    # Tier 3 — polish
    # ----------------------------------------------------------------- #

    @mcp.tool(
        name="chat_react_to_message",
        annotations={
            "title": "Add an emoji reaction to a Chat message",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_react_to_message(params: ReactToMessageInput) -> str:
        """Add a Unicode-emoji reaction to a message.

        Examples: 👍 🚀 🎯 ✅ ❤️ 🙏 💯. The Chat API accepts only Unicode
        characters, not shortcodes like ':rocket:'.
        """
        try:
            svc = _service()
            created = svc.spaces().messages().reactions().create(
                parent=params.message_name,
                body={"emoji": {"unicode": params.emoji}},
            ).execute()
            log.info(
                "chat_react_to_message: reacted %s on %s",
                params.emoji, params.message_name,
            )
            return json.dumps({
                "status": "added",
                "reaction_name": created.get("name"),
                "emoji": params.emoji,
                "message_name": params.message_name,
            }, indent=2)
        except Exception as e:
            log.error("chat_react_to_message failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="chat_get_thread",
        annotations={
            "title": "Fetch every message in a Chat thread",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def chat_get_thread(params: GetThreadInput) -> str:
        """Return all messages in a thread, oldest-first.

        Uses a thread filter on messages.list. The thread is identified by its
        full resource name (e.g. 'spaces/AAAAxxx/threads/YYYYzzz') — get it from
        any message's `thread.name` field.
        """
        try:
            svc = _service()
            # The thread_name format is spaces/X/threads/Y. Parent for messages is the space.
            parts = params.thread_name.split("/threads/")
            if len(parts) != 2:
                return f"Error: thread_name must be 'spaces/X/threads/Y'; got '{params.thread_name}'."
            space_name = parts[0]
            thread_id_only = parts[1]

            messages: list[dict] = []
            page_token = None
            while len(messages) < params.limit:
                kwargs: dict = {
                    "parent": space_name,
                    "filter": f'thread.name = "{params.thread_name}"',
                    "pageSize": min(1000, params.limit - len(messages)),
                    "orderBy": "createTime asc",
                }
                if page_token:
                    kwargs["pageToken"] = page_token
                resp = svc.spaces().messages().list(**kwargs).execute()
                batch = resp.get("messages") or []
                messages.extend(batch)
                page_token = resp.get("nextPageToken")
                if not page_token or not batch:
                    break

            return json.dumps({
                "thread_name": params.thread_name,
                "space_name": space_name,
                "message_count": len(messages),
                "messages": [
                    {
                        "name": m.get("name"),
                        "sender_display": (m.get("sender") or {}).get("displayName"),
                        "text": m.get("text"),
                        "create_time": m.get("createTime"),
                    }
                    for m in messages
                ],
            }, indent=2)
        except Exception as e:
            log.error("chat_get_thread failed: %s", e)
            return format_error(e)


def _log_chat_activity_on_contact(email: str, message_text: str) -> None:
    """Append a 'Chat sent' note to the matching saved contact's biography.

    Mirrors the email-side _log_activity_on_contact helper. Silently no-ops if
    the contact doesn't exist or the People API call fails — never let a
    logging glitch break a successful Chat send.
    """
    if not email:
        return
    try:
        import datetime as _dt
        people = gservices.people()
        # Use connections().list — searchContacts has indexing lag.
        page_token = None
        person = None
        target = email.lower()
        while not person:
            kwargs = {
                "resourceName": "people/me",
                "personFields": "names,emailAddresses,biographies,metadata",
                "pageSize": 1000,
            }
            if page_token:
                kwargs["pageToken"] = page_token
            resp = people.people().connections().list(**kwargs).execute()
            for p in resp.get("connections") or []:
                addrs = [
                    (e.get("value") or "").lower()
                    for e in (p.get("emailAddresses") or [])
                ]
                if target in addrs:
                    person = p
                    break
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        if not person:
            return

        now = _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
        preview = (message_text or "").strip().splitlines()[0][:80] if message_text else ""
        note = f'[{now}] Chat sent: "{preview}"'
        bios = person.get("biographies") or []
        prev_text = bios[0].get("value", "") if bios else ""
        combined = (prev_text + "\n\n" + note).strip() if prev_text else note
        people.people().updateContact(
            resourceName=person["resourceName"],
            updatePersonFields="biographies",
            body={
                "etag": person["etag"],
                "biographies": [{"value": combined, "contentType": "TEXT_PLAIN"}],
            },
        ).execute()
        log.info("chat activity logged on %s for %s", person["resourceName"], email)
    except Exception as e:
        log.warning("chat activity log skipped for %s: %s", email, e)
