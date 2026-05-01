# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP tool wrapper for the access audit.

Exposes one tool: drive_access_audit.
The pure-logic core lives in access_audit.py at the project root.
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import access_audit as core
import gservices
from errors import format_error
from logging_util import log


def _drive():
    return gservices.drive()


def _gmail():
    return gservices.gmail()


# Drive permission fields we want back from the API.
_PERM_FIELDS = (
    "id, type, role, emailAddress, domain, displayName, deleted"
)


def _list_permissions(file_id: str) -> tuple[str | None, list[dict]]:
    """Pull all permissions on a file. Returns (file_name, permissions)."""
    drive = _drive()

    # File metadata for the display name.
    meta = (
        drive.files()
        .get(fileId=file_id, fields="id, name, mimeType")
        .execute()
    )
    file_name = meta.get("name")

    # Paginate through permissions.
    perms: list[dict] = []
    page_token = None
    while True:
        resp = (
            drive.permissions()
            .list(
                fileId=file_id,
                fields=f"nextPageToken, permissions({_PERM_FIELDS})",
                pageSize=100,
                pageToken=page_token,
                supportsAllDrives=True,
            )
            .execute()
        )
        perms.extend(resp.get("permissions", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return file_name, perms


def _authed_email() -> str | None:
    """Best-effort lookup of the authenticated user's email."""
    try:
        prof = _gmail().users().getProfile(userId="me").execute()
        return prof.get("emailAddress", "").lower() or None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Pydantic input
# --------------------------------------------------------------------------- #


class AccessAuditInput(BaseModel):
    """Inputs for drive_access_audit."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    file_id: str = Field(
        ...,
        description=(
            "Drive file or folder ID to audit. Find IDs in the URL "
            "(https://drive.google.com/drive/folders/<ID>)."
        ),
    )
    include_summary: bool = Field(
        default=True,
        description="Include aggregate counts (by_relationship, by_role, risk_flags).",
    )


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="drive_access_audit",
        annotations={
            "title": "Audit who has access to a Drive file or folder",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def drive_access_audit(params: AccessAuditInput) -> str:
        """Audit Drive permissions on a file or folder.

        Pulls every grant on the file (user, group, domain, anyone-with-link),
        classifies each one as internal / subsidiary / external / public, and
        computes a risk score from flags like:
          - anyone_with_link, public_writable
          - external_owner, external_writer
          - domain_writable
          - deleted_account (account no longer exists)

        Use this before sharing sensitive folders or as a periodic security
        sweep. Returns JSON with per-grant detail + aggregate summary.
        """
        try:
            file_name, perms = _list_permissions(params.file_id)
            authed = _authed_email()
            report = core.summarize_permissions(
                file_id=params.file_id,
                file_name=file_name,
                permissions=perms,
                authed_email=authed,
            )
            payload = report.to_dict()
            if not params.include_summary:
                payload.pop("summary", None)

            log.info(
                "drive_access_audit %s (%s) → %d grants, risk_score=%d",
                params.file_id, file_name or "?",
                len(report.grants), report.risk_score,
            )
            return json.dumps(payload, indent=2, default=str)
        except Exception as e:
            return format_error("drive_access_audit", e)
