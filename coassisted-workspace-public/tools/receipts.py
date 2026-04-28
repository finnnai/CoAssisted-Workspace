# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE.
"""Receipt extractor — flagship workflow + 3 manual override tools.

  - workflow_extract_receipts: orchestrator. Scan inbox + Drive folder,
    extract every receipt, dedupe, log to Sheet, archive PDFs to Drive,
    optionally export QuickBooks CSV. The demo tool.
  - workflow_extract_one_receipt: extract a single email or attachment by ID.
  - workflow_recategorize_receipt: edit the category on an existing Sheet row.
  - workflow_export_receipts_qb_csv: build a QuickBooks-importable CSV from
    the existing Sheet (no new extraction).

All four are paid-tier (uses Anthropic API + delivers high $ value).
"""

from __future__ import annotations

import base64
import csv
import datetime as _dt
import io
import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import config
import gservices
import receipts as _r
from logging_util import log
from errors import format_error
from dryrun import dry_run_preview, is_dry_run


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class ExtractReceiptsInput(BaseModel):
    """Input for workflow_extract_receipts (the flagship orchestrator)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    days: int = Field(
        default=30, ge=1, le=365,
        description="Look-back window for inbox receipts. Default 30 days.",
    )
    max_emails_to_scan: int = Field(
        default=200, ge=1, le=2000,
        description="Hard cap on inbox messages inspected. Default 200.",
    )
    drive_folder_id: Optional[str] = Field(
        default=None,
        description="Optional: also scan this Drive folder for receipt PDFs/images. "
                    "If None, the Drive branch is skipped.",
    )
    chat_space_id: Optional[str] = Field(
        default=None,
        description="Optional: also scan this Google Chat space for receipts (text "
                    "bodies + PDF/image attachments). Pass the resource name like "
                    "'spaces/AAQA...'. If None, the Chat branch is skipped.",
    )
    chat_max_messages: int = Field(
        default=200, ge=1, le=2000,
        description="Hard cap on Chat messages inspected when chat_space_id is set.",
    )
    sheet_id: Optional[str] = Field(
        default=None,
        description="Existing Sheet to append to. Takes priority over sheet_name. "
                    "If both sheet_id and sheet_name are None, the tool returns the "
                    "list of available 'Receipts — *' sheets and asks you to pick one.",
    )
    sheet_name: Optional[str] = Field(
        default=None,
        description="Friendly name of an existing expense sheet (e.g. 'Personal 2026 Q2' "
                    "or the full 'Receipts — Personal 2026 Q2'). The tool searches your "
                    "Drive for a matching sheet. Use workflow_create_receipt_sheet first "
                    "if it doesn't exist yet.",
    )
    archive_drive_folder_id: Optional[str] = Field(
        default=None,
        description="Drive folder for archived PDFs. If None and "
                    "archive_pdfs=True, auto-creates 'CoAssisted Receipts'.",
    )
    archive_pdfs: bool = Field(
        default=True,
        description="If True, save copies of receipt PDFs/images to Drive.",
    )
    export_qb_csv: bool = Field(
        default=False,
        description="If True, also produce a QuickBooks-importable CSV in the archive folder.",
    )
    skip_low_confidence: bool = Field(
        default=False,
        description="If True, skip rows where the LLM confidence < 0.4.",
    )
    dry_run: Optional[bool] = Field(default=None)


class ExtractOneReceiptInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    message_id: Optional[str] = Field(
        default=None,
        description="Gmail message ID to extract from. Mutually exclusive with drive_file_id.",
    )
    drive_file_id: Optional[str] = Field(
        default=None,
        description="Drive file ID (PDF or image) to extract from.",
    )
    sheet_id: Optional[str] = Field(
        default=None,
        description="If set, also append the extracted receipt to this Sheet.",
    )


class RecategorizeReceiptInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sheet_id: Optional[str] = Field(
        default=None,
        description="Sheet to update. Provide sheet_id OR sheet_name (not both).",
    )
    sheet_name: Optional[str] = Field(
        default=None,
        description="Friendly name of the receipt sheet (e.g. 'Personal 2026 Q2'). "
                    "Resolved against your Drive's 'Receipts — *' sheets.",
    )
    row_number: int = Field(
        ..., ge=2,
        description="Sheet row number (1-indexed; row 1 is the header).",
    )
    new_category: str = Field(
        ..., description=f"One of: {'; '.join(_r.DEFAULT_CATEGORIES)}",
    )


class ExportReceiptsQbCsvInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sheet_id: Optional[str] = Field(
        default=None,
        description="Source sheet ID. Provide sheet_id OR sheet_name (not both).",
    )
    sheet_name: Optional[str] = Field(
        default=None,
        description="Friendly name of the source sheet (e.g. 'Personal 2026 Q2'). "
                    "Resolved against your Drive's 'Receipts — *' sheets.",
    )
    save_to_drive_folder_id: Optional[str] = Field(
        default=None,
        description="If set, save the CSV to this Drive folder. Otherwise return as base64.",
    )
    date_from: Optional[str] = Field(
        default=None, description="ISO date filter — only rows with date >= this.",
    )
    date_to: Optional[str] = Field(
        default=None, description="ISO date filter — only rows with date <= this.",
    )


class CreateReceiptSheetInput(BaseModel):
    """Input for workflow_create_receipt_sheet."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(
        ..., min_length=1, max_length=80,
        description="Short label appended to 'Receipts — '. e.g. 'Personal 2026 Q2' "
                    "becomes a sheet titled 'Receipts — Personal 2026 Q2'. "
                    "Pass either the short label or the full title; the prefix is "
                    "added automatically if missing.",
    )


class ExtractReceiptsFromChatInput(BaseModel):
    """Input for workflow_extract_receipts_from_chat — scan one Gchat space."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    chat_space_id: str = Field(
        ..., min_length=3,
        description="Chat space resource name, e.g. 'spaces/AAQA...'. Get one "
                    "from chat_list_spaces.",
    )
    days: int = Field(
        default=30, ge=1, le=365,
        description="Look-back window for chat messages.",
    )
    max_messages: int = Field(
        default=200, ge=1, le=2000,
        description="Hard cap on messages inspected.",
    )
    sheet_id: Optional[str] = Field(
        default=None,
        description="Existing Sheet to append to. Either sheet_id or sheet_name is "
                    "required (resolved against your Drive's 'Receipts — *' sheets).",
    )
    sheet_name: Optional[str] = Field(default=None)
    skip_low_confidence: bool = Field(default=False)
    archive_pdfs: bool = Field(
        default=False,
        description="If True, copy PDF/image attachments to your CoAssisted Receipts "
                    "Drive folder for IRS / audit retention.",
    )
    archive_drive_folder_id: Optional[str] = Field(default=None)
    dry_run: Optional[bool] = Field(default=None)


class ForgetMerchantInput(BaseModel):
    """Input for workflow_forget_merchant — drops one cache entry."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    merchant: str = Field(
        ..., min_length=1,
        description="Merchant display name to forget. Matched after "
                    "normalization, so 'Anthropic' and 'Anthropic, PBC' "
                    "drop the same record.",
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _gmail():
    return gservices.gmail()


def _drive():
    return gservices.drive()


def _sheets():
    return gservices.sheets()


def _chat():
    return gservices.chat()


# Per-process cache for resolving Chat user resource IDs (users/N) to human
# display names. Avoids repeat People API calls within a single scan AND
# across scans within the same MCP server lifetime. Cleared on server restart.
_CHAT_SENDER_NAME_CACHE: dict[str, str] = {}


def _resolve_chat_sender_display(sender_dict: Optional[dict]) -> Optional[str]:
    """Turn a Chat `sender` object into a human-readable display name.

    Chat populates sender.displayName for messages in multi-user spaces, but
    leaves it null in self-spaces and some DMs — falling back to sender.name
    which is the opaque resource ID 'users/<numeric>'. That looks ugly in the
    submitted_by metadata block.

    Resolution order:
      1. sender.displayName (free, comes with the message)
      2. People API lookup on the numeric ID (one network call, then cached)
      3. opaque resource ID (last-resort fallback so we never crash the scan)

    Returns None only if `sender_dict` is empty.
    """
    if not sender_dict:
        return None
    display = sender_dict.get("displayName")
    if display:
        return display

    name = sender_dict.get("name") or ""
    if not name:
        return None

    if name in _CHAT_SENDER_NAME_CACHE:
        return _CHAT_SENDER_NAME_CACHE[name]

    resolved: Optional[str] = None
    try:
        # Chat user resource IDs use the same numeric ID as People API,
        # just with a different prefix. 'users/N' → 'people/N'.
        if name.startswith("users/"):
            person_resource = "people/" + name.split("/", 1)[1]
            people_svc = gservices.people()
            resp = people_svc.people().get(
                resourceName=person_resource,
                personFields="names,emailAddresses",
            ).execute()
            names = resp.get("names") or []
            if names:
                resolved = names[0].get("displayName")
            if not resolved:
                emails = resp.get("emailAddresses") or []
                if emails:
                    resolved = (emails[0].get("value") or "").split("@", 1)[0]
    except Exception as e:
        log.warning("People API lookup failed for %s: %s", name, e)

    if not resolved:
        # Final fallback — at least drop the 'users/' prefix so the row
        # shows just the numeric ID instead of the opaque-looking path.
        resolved = name

    _CHAT_SENDER_NAME_CACHE[name] = resolved
    return resolved


# Naming convention for expense sheets. Auto-discovery walks Drive looking for
# spreadsheets whose title starts with this prefix. Em-dash + space — distinct
# enough that hyphenated personal sheet names ('Receipts - Q1') won't collide.
RECEIPT_SHEET_PREFIX = "Receipts — "


def _validate_sheet(sheet_id: str) -> tuple[str, str]:
    """Confirm a sheet exists and return (sheet_id, title). Raises on 404."""
    sheets = _sheets()
    meta = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
    return sheet_id, meta.get("properties", {}).get("title", "")


def _list_receipt_sheets() -> list[dict]:
    """Auto-discover expense sheets in user's Drive by name prefix.

    Returns a list of dicts (newest-modified first), each with sheet_id, name,
    label (the part after the prefix), row_count, last_modified, url.

    Drive's `name contains` operator is case-insensitive substring; we still
    re-filter by exact prefix on the client side to drop false positives like
    'My Receipts — old'.
    """
    drive = _drive()
    query = (
        "mimeType = 'application/vnd.google-apps.spreadsheet' and "
        "name contains 'Receipts —' and trashed = false"
    )
    resp = drive.files().list(
        q=query,
        pageSize=50,
        orderBy="modifiedTime desc",
        fields="files(id,name,modifiedTime,webViewLink)",
    ).execute()
    out: list[dict] = []
    sheets = _sheets()
    for f in resp.get("files", []) or []:
        title = f.get("name", "")
        if not title.startswith(RECEIPT_SHEET_PREFIX):
            continue
        # Cheap row count: read column A only.
        row_count = None
        try:
            v = sheets.spreadsheets().values().get(
                spreadsheetId=f["id"], range="A:A",
            ).execute()
            rows = v.get("values", []) or []
            row_count = max(0, len(rows) - 1)  # subtract header
        except Exception:
            pass
        out.append({
            "sheet_id": f["id"],
            "name": title,
            "label": title[len(RECEIPT_SHEET_PREFIX):],
            "row_count": row_count,
            "last_modified": f.get("modifiedTime"),
            "url": f.get(
                "webViewLink",
                f"https://docs.google.com/spreadsheets/d/{f['id']}/edit",
            ),
        })
    return out


def _resolve_sheet(
    sheet_id: Optional[str],
    sheet_name: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """Resolve a sheet from either explicit id, name lookup, or fail with the
    discovery list so the caller can return 'pick one' to the user.

    Returns (sheet_id, title, error_dict). Exactly one of the first two pairs
    or error_dict will be populated:
      - (sheet_id, title, None)         -> resolved
      - (None, None, error_dict)        -> not resolved; error_dict has hint
    """
    if sheet_id:
        try:
            _, title = _validate_sheet(sheet_id)
            return sheet_id, title, None
        except Exception as e:
            return None, None, {
                "status": "sheet_not_accessible",
                "sheet_id": sheet_id,
                "reason": str(e),
                "hint": (
                    "Sheet ID could not be opened. Check it's correct and that "
                    "this Google account has access. Run "
                    "workflow_list_receipt_sheets to see your available sheets."
                ),
            }
    discovered = _list_receipt_sheets()
    if sheet_name:
        target = sheet_name.strip()
        # Match against full title OR label (the part after the prefix).
        # Also tolerate the user passing the bare prefix part themselves.
        candidates = [
            s for s in discovered
            if s["name"] == target or s["label"] == target
            or s["name"].lower() == target.lower()
            or s["label"].lower() == target.lower()
        ]
        if len(candidates) == 1:
            s = candidates[0]
            return s["sheet_id"], s["name"], None
        if len(candidates) > 1:
            return None, None, {
                "status": "ambiguous_sheet_name",
                "requested": target,
                "matches": candidates,
                "hint": (
                    "Multiple sheets matched. Pass sheet_id from the matches "
                    "list to disambiguate."
                ),
            }
        return None, None, {
            "status": "sheet_not_found",
            "requested": target,
            "available_sheets": discovered,
            "hint": (
                f"No sheet named '{target}' found. Pick from "
                "available_sheets, or call workflow_create_receipt_sheet "
                f"with name='{target}' to create it."
            ),
        }
    # No sheet specified at all — return the discovery list.
    return None, None, {
        "status": "needs_sheet",
        "available_sheets": discovered,
        "hint": (
            "Tell me which expense sheet to use. Either pass sheet_id from "
            "available_sheets, or sheet_name='<label>' (e.g. 'Personal "
            "2026 Q2'), or call workflow_create_receipt_sheet first."
        ),
    }


def _ensure_archive_sheet(sheet_id: str) -> tuple[str, str]:
    """Validate a resolved sheet_id has the receipts header. Add it if missing.

    Idempotent — safe to call on a sheet that already has the header. Used by
    the orchestrator after sheet resolution so a freshly-created sheet (header
    already written by workflow_create_receipt_sheet) and a long-lived sheet
    look identical from here on.
    """
    sheets = _sheets()
    meta = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
    title = meta.get("properties", {}).get("title", "")
    # Read row 1
    try:
        v = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range="A1:Q1",
        ).execute()
        first = (v.get("values") or [[]])[0]
    except Exception:
        first = []
    if not first or first[0] != _r.SHEET_COLUMNS[0]:
        sheets.spreadsheets().values().update(
            spreadsheetId=sheet_id, range="A1",
            valueInputOption="RAW",
            body={"values": [_r.SHEET_COLUMNS]},
        ).execute()
    return sheet_id, title


def _ensure_drive_folder(folder_id: Optional[str], default_name: str) -> str:
    """Get or create a Drive folder. Returns the folder ID."""
    drive = _drive()
    if folder_id:
        return folder_id
    # Search for existing folder by name in root
    query = (
        f"name = '{default_name}' and "
        "mimeType = 'application/vnd.google-apps.folder' and "
        "trashed = false"
    )
    resp = drive.files().list(q=query, pageSize=1, fields="files(id,name)").execute()
    files = resp.get("files", [])
    if files:
        return files[0]["id"]
    # Create
    created = drive.files().create(
        body={
            "name": default_name,
            "mimeType": "application/vnd.google-apps.folder",
        },
        fields="id,name",
    ).execute()
    return created["id"]


def _existing_sheet_source_ids(sheet_id: str) -> set[str]:
    """Return the set of source_ids already logged in the Sheet (for dedup)."""
    try:
        sheets = _sheets()
        # Read source_id column (14th = N) — skip header
        resp = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range="N2:N",
        ).execute()
        rows = resp.get("values", [])
        return {r[0] for r in rows if r and r[0]}
    except Exception:
        return set()


def _existing_sheet_content_keys(sheet_id: str) -> set[str]:
    """Build content-keys for every row in the Sheet, for cross-source dedup.

    Catches the case where the same physical receipt is provided through
    multiple file IDs (e.g. 3 photos of the same Chevron purchase). Each row
    is fingerprinted by `receipts.content_key()`. Rows missing merchant or
    total are skipped (they couldn't be uniquely keyed anyway).
    """
    try:
        sheets = _sheets()
        # Read columns A:Q (date=B, merchant=C, total=D, last_4=K)
        resp = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range="A2:Q",
        ).execute()
        rows = resp.get("values", []) or []
        keys: set[str] = set()
        for row in rows:
            # Pad row to full 17-col width to avoid IndexError
            row = row + [""] * (17 - len(row))
            date = row[1] or ""
            merchant = row[2] or ""
            total_str = row[3] or ""
            last_4 = row[10] or ""
            if not merchant or not total_str:
                continue
            try:
                total = float(total_str)
            except ValueError:
                continue
            k = _r.content_key(merchant, date, total, last_4)
            if k:
                keys.add(k)
        return keys
    except Exception:
        return set()


def _archive_pdf_to_drive(
    drive, folder_id: str, filename: str, content: bytes, mime_type: str,
) -> str:
    """Upload a single PDF/image to Drive. Returns the file's webViewLink."""
    from googleapiclient.http import MediaInMemoryUpload
    media = MediaInMemoryUpload(content, mimetype=mime_type, resumable=False)
    created = drive.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id,name,webViewLink",
    ).execute()
    return created.get("webViewLink", "")


def _download_chat_attachment(chat_svc, attachment: dict) -> tuple[bytes, str] | None:
    """Pull bytes for a Chat attachment.

    Returns (content_bytes, mime_type) or None if not downloadable. Drive-linked
    attachments are routed through Drive's get_media; Chat-stored attachments
    use the Chat media API.
    """
    import io
    from googleapiclient.http import MediaIoBaseDownload

    content_type = attachment.get("contentType", "")
    drive_ref = (attachment.get("driveDataRef") or {}).get("driveFileId")
    data_ref = (attachment.get("attachmentDataRef") or {}).get("resourceName")

    if drive_ref:
        try:
            content = _drive().files().get_media(fileId=drive_ref).execute()
            return content, content_type
        except Exception as e:
            log.warning("chat drive-linked download failed: %s", e)
            return None

    if not data_ref:
        return None

    try:
        buf = io.BytesIO()
        req = chat_svc.media().download_media(resourceName=data_ref)
        downloader = MediaIoBaseDownload(buf, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buf.getvalue(), content_type
    except Exception as e:
        log.warning("chat media download failed: %s", e)
        return None


def _scan_chat_space(
    *,
    space_id: str,
    days: int,
    max_messages: int,
    seen_source_ids: set[str],
    seen_content_keys: set[str],
    results: dict,
    redact_payment: bool,
    now_iso: str,
    archive_pdfs: bool,
    archive_folder_id: Optional[str],
    skip_low_confidence: bool,
) -> tuple[list[list], list[dict]]:
    """Iterate one Chat space for receipts. Mirrors the inbox + Drive scans
    so the post-extraction pipeline (enrichment, content dedup, low-conf,
    sheet append) is applied identically.

    Returns (new_rows, new_records). Mutates `results` and the seen-key sets.
    """
    chat_svc = _chat()
    new_rows: list[list] = []
    new_records: list[dict] = []

    # Filter messages newer than `days` days ago. Chat API uses RFC 3339.
    since = (
        _dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(days=days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    chat_filter = f'createTime > "{since}"'
    try:
        resp = chat_svc.spaces().messages().list(
            parent=space_id,
            pageSize=min(max_messages, 100),
            filter=chat_filter,
            orderBy="createTime desc",
        ).execute()
    except Exception as e:
        log.warning("chat list failed for %s: %s", space_id, e)
        results["errors"] += 1
        return new_rows, new_records

    msgs = resp.get("messages", []) or []
    for raw in msgs[:max_messages]:
        results["scanned"] += 1
        message_name = raw.get("name", "")
        if not message_name:
            continue

        # Pull the full message so we get attachments (list_messages doesn't
        # always include them).
        try:
            msg = chat_svc.spaces().messages().get(name=message_name).execute()
        except Exception as e:
            log.warning("chat get failed for %s: %s", message_name, e)
            results["errors"] += 1
            continue

        source_id = f"chat:{message_name}"
        if source_id in seen_source_ids:
            results["skipped_dup"] += 1
            continue

        text = (msg.get("text") or "").strip()
        attachments = msg.get("attachment") or []

        # Sender attribution — used as `submitted_by` on the receipt so the
        # row notes show "who put this in the channel" alongside the LLM
        # extraction. Falls back through People API when displayName is null.
        sender = _resolve_chat_sender_display(msg.get("sender"))

        # 1. Try the first PDF/image attachment we can download.
        rec = None
        receipt_link = ""
        for att in attachments:
            ct = att.get("contentType", "") or ""
            if ct not in ("application/pdf", "image/jpeg", "image/png", "image/gif"):
                continue
            dl = _download_chat_attachment(chat_svc, att)
            if not dl:
                continue
            content, mime = dl
            try:
                if mime == "application/pdf":
                    rec = _r.extract_from_pdf(
                        content, source_id=source_id, source_kind="chat_pdf",
                        submitted_by=sender,
                    )
                else:
                    rec = _r.extract_from_image(
                        content, mime_type=mime,
                        source_id=source_id, source_kind="chat_image",
                        submitted_by=sender,
                    )
            except Exception as e:
                log.warning("chat attachment extract failed: %s", e)
                results["errors"] += 1
                continue

            # Optionally archive the bytes to Drive.
            if archive_pdfs and archive_folder_id:
                try:
                    fname = (
                        f"{_dt.date.today().isoformat()}__"
                        f"{(rec.merchant or 'unknown').replace('/', '_')}__"
                        f"{message_name.split('/')[-1][:8]}__"
                        f"{att.get('contentName') or 'attachment'}"
                    )
                    receipt_link = _archive_pdf_to_drive(
                        _drive(), archive_folder_id, fname, content, mime,
                    )
                except Exception as e:
                    log.warning("archive failed for %s: %s", message_name, e)
            break  # first valid attachment wins

        # 2. Fall back to message text body.
        if rec is None and text:
            is_receipt, _reason = _r.classify_email_as_receipt(
                subject="", sender=sender or "", body_preview=text,
            )
            if not is_receipt:
                results["skipped_not_receipt"] += 1
                continue
            try:
                rec = _r.extract_from_text(
                    text, source_id=source_id, source_kind="chat_text",
                    submitted_by=sender,
                )
            except Exception as e:
                log.warning("chat text extract failed for %s: %s", message_name, e)
                results["errors"] += 1
                continue

        if rec is None:
            results["skipped_not_receipt"] += 1
            continue

        # Same post-extraction pipeline as inbox/Drive branches.
        try:
            rec = _r.enrich_low_confidence_receipt(rec)
        except Exception as e:
            log.warning("chat enrichment failed for %s: %s", message_name, e)

        if skip_low_confidence and rec.confidence < 0.4:
            results["skipped_low_conf"] += 1
            continue

        ckey = _r.content_key(rec.merchant, rec.date, rec.total, rec.last_4)
        if ckey and ckey in seen_content_keys:
            results["skipped_dup_content"] += 1
            continue
        if ckey:
            seen_content_keys.add(ckey)

        row = _r.receipt_to_sheet_row(
            rec, logged_at=now_iso, receipt_link=receipt_link,
            redact_payment=redact_payment,
        )
        new_rows.append(row)
        new_records.append({
            "source_id": source_id,
            "merchant": rec.merchant,
            "date": rec.date,
            "total": rec.total,
            "currency": rec.currency,
            "category": rec.category,
            "confidence": rec.confidence,
        })
        results["extracted"] += 1
        seen_source_ids.add(source_id)

    return new_rows, new_records


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:

    @mcp.tool(
        name="workflow_extract_receipts",
        annotations={
            "title": "Flagship: scan inbox + Drive for receipts, extract via LLM, log to Sheet",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_extract_receipts(params: ExtractReceiptsInput) -> str:
        """**Flagship workflow.** End-to-end receipt processing in one call.

        1. Scans inbox for receipt-shaped emails (last N days, configurable).
        2. For each: extracts merchant/date/total/category via LLM. PDFs +
           images use Claude Vision; plain emails use text-only (cheaper).
        3. Optionally also scans a Drive folder of pre-saved receipts.
        4. Dedupes against an existing Sheet (by source_id).
        5. Appends new rows to a Google Sheet (auto-creates per-year if needed).
        6. Optionally archives the receipt PDFs/images to a Drive folder for IRS.
        7. Optionally exports a QuickBooks-importable CSV.

        Cost estimate (Claude Haiku 4.5):
        - Text email: ~$0.0005 per receipt
        - PDF (1-page, vision): ~$0.005 per receipt
        - Image (vision): ~$0.005 per receipt

        Typical 30-day scan with 50 receipts: $0.05–$0.50.

        Privacy: full card numbers are NEVER extracted. By default only the
        last_4 is captured, and `receipts_redact_payment_details=true` (default)
        redacts even that before writing to the Sheet.
        """
        try:
            import llm
            ok, reason = llm.is_available()
            if not ok:
                return json.dumps({
                    "status": "no_llm",
                    "reason": reason,
                    "fix": (
                        'Set "anthropic_api_key" in config.json. '
                        "Receipt extraction requires LLM access. "
                        "See INSTALL.md 'Optional add-ons' for cost details."
                    ),
                }, indent=2)

            redact_payment = bool(
                config.get("receipts_redact_payment_details", True),
            )
            now_iso = _dt.datetime.now().astimezone().isoformat(timespec="seconds")

            # Resolve destination sheet. No silent default — if the caller
            # gave us nothing, return the discovery list so the user can pick.
            # config.receipts_sheet_id is honored only as a last-resort
            # backwards-compat fallback for users who haven't migrated yet.
            requested_id = params.sheet_id or config.get("receipts_sheet_id")
            sheet_id, sheet_title, err = _resolve_sheet(
                requested_id, params.sheet_name,
            )
            if err is not None:
                return json.dumps(err, indent=2)
            sheet_id, sheet_title = _ensure_archive_sheet(sheet_id)
            sheet_created = False
            archive_folder_id = None
            if params.archive_pdfs:
                archive_folder_id = _ensure_drive_folder(
                    params.archive_drive_folder_id
                    or config.get("receipts_drive_folder_id"),
                    default_name="CoAssisted Receipts",
                )

            seen_source_ids = _existing_sheet_source_ids(sheet_id)
            seen_content_keys = _existing_sheet_content_keys(sheet_id)
            results = {
                "extracted": 0,
                "skipped_dup": 0,             # source_id duplicates
                "skipped_dup_content": 0,     # same-receipt-different-file duplicates
                "skipped_low_conf": 0,
                "skipped_not_receipt": 0,
                "errors": 0,
                "scanned": 0,
            }
            new_rows: list[list] = []
            extracted_records: list[dict] = []

            # --- 1. Inbox scan --------------------------------------------- #
            gmail = _gmail()
            since = (
                _dt.date.today() - _dt.timedelta(days=params.days)
            ).strftime("%Y/%m/%d")
            search_q = f"after:{since} -from:me"
            search = gmail.users().messages().list(
                userId="me", q=search_q, maxResults=params.max_emails_to_scan,
            ).execute()
            msgs = search.get("messages", []) or []

            for m in msgs:
                results["scanned"] += 1
                msg_id = m["id"]
                if msg_id in seen_source_ids:
                    results["skipped_dup"] += 1
                    continue
                # Fetch headers + body preview cheaply.
                full = gmail.users().messages().get(
                    userId="me", id=msg_id, format="full",
                ).execute()
                headers = {
                    h["name"].lower(): h["value"]
                    for h in (full.get("payload", {}).get("headers") or [])
                }
                subject = headers.get("subject", "")
                sender = headers.get("from", "")
                # Use snippet as cheap pre-fetch; for hard-to-classify cases
                # (subject is empty / sender is BROAD) we'll dip into the full
                # plain-text body. Doing a full _extract_plaintext on every
                # candidate would slow the scan, so we lazy-eval it.
                snippet = full.get("snippet", "")
                is_receipt, reason = _r.classify_email_as_receipt(
                    subject=subject, sender=sender, body_preview=snippet,
                )
                if not is_receipt:
                    # Re-try the classifier with the full plain-text body in
                    # case the money signal lives past the snippet cutoff
                    # (typical for Stripe-hosted receipts: 'Total $X.XX' is
                    # buried below ~200 chars of header/logo/links).
                    full_body = _extract_plaintext(full.get("payload", {}))
                    if full_body and full_body != snippet:
                        is_receipt, reason = _r.classify_email_as_receipt(
                            subject=subject, sender=sender,
                            body_preview=full_body[:4000],
                        )
                if not is_receipt:
                    results["skipped_not_receipt"] += 1
                    continue

                # Try to find a PDF/image attachment first; fall back to body text.
                attachments = []
                def _walk(payload, acc):
                    fn = payload.get("filename")
                    body = payload.get("body", {}) or {}
                    if fn and body.get("attachmentId"):
                        acc.append({
                            "id": body["attachmentId"],
                            "filename": fn,
                            "mime": payload.get("mimeType", ""),
                            "size": body.get("size", 0),
                        })
                    for p in payload.get("parts", []) or []:
                        _walk(p, acc)
                _walk(full.get("payload", {}), attachments)

                rec = None
                pdf_or_image = next(
                    (a for a in attachments
                     if a["mime"] in ("application/pdf", "image/jpeg",
                                      "image/png", "image/gif")
                     and a["size"] < 5 * 1024 * 1024),  # skip giant attachments
                    None,
                )
                receipt_link = ""
                try:
                    if pdf_or_image:
                        att = gmail.users().messages().attachments().get(
                            userId="me", messageId=msg_id,
                            id=pdf_or_image["id"],
                        ).execute()
                        att_bytes = base64.urlsafe_b64decode(att["data"])
                        if pdf_or_image["mime"] == "application/pdf":
                            rec = _r.extract_from_pdf(
                                att_bytes, source_id=msg_id,
                                source_kind="email_pdf",
                                submitted_by=sender,
                            )
                        else:
                            rec = _r.extract_from_image(
                                att_bytes, mime_type=pdf_or_image["mime"],
                                source_id=msg_id, source_kind="email_image",
                                submitted_by=sender,
                            )
                        # Archive
                        if params.archive_pdfs and archive_folder_id:
                            try:
                                fname = (
                                    f"{_dt.date.today().isoformat()}__"
                                    f"{(rec.merchant or 'unknown').replace('/', '_')}"
                                    f"__{msg_id[:8]}__"
                                    f"{pdf_or_image['filename']}"
                                )
                                receipt_link = _archive_pdf_to_drive(
                                    _drive(), archive_folder_id, fname,
                                    att_bytes, pdf_or_image["mime"],
                                )
                            except Exception as e:
                                log.warning(
                                    "archive failed for %s: %s", msg_id, e,
                                )
                    else:
                        # Fall back to text extraction from body.
                        body_text = _extract_plaintext(full.get("payload", {}))
                        if not body_text.strip():
                            results["skipped_not_receipt"] += 1
                            continue
                        rec = _r.extract_from_text(
                            body_text, source_id=msg_id,
                            source_kind="email_text",
                            submitted_by=sender,
                        )
                except Exception as e:
                    log.warning("extract failed for msg %s: %s", msg_id, e)
                    results["errors"] += 1
                    continue

                if not rec:
                    continue

                # 3-tier enrichment: if conf < 0.6, try Maps then web_search.
                # Mutates rec in place. Safe no-op for high-conf rows.
                try:
                    rec = _r.enrich_low_confidence_receipt(rec)
                except Exception as e:
                    log.warning("enrichment failed for %s: %s", msg_id, e)

                if (params.skip_low_confidence and rec.confidence < 0.4):
                    results["skipped_low_conf"] += 1
                    continue

                # Content-based dedup: catches the same physical receipt
                # arriving via different file_ids (multiple photos of one
                # purchase). Falls back to source-id-only when the receipt
                # lacks enough identifying info to fingerprint.
                ckey = _r.content_key(
                    rec.merchant, rec.date, rec.total, rec.last_4,
                )
                if ckey and ckey in seen_content_keys:
                    results["skipped_dup_content"] += 1
                    continue
                if ckey:
                    seen_content_keys.add(ckey)

                row = _r.receipt_to_sheet_row(
                    rec, logged_at=now_iso, receipt_link=receipt_link,
                    redact_payment=redact_payment,
                )
                new_rows.append(row)
                extracted_records.append({
                    "source_id": msg_id,
                    "merchant": rec.merchant,
                    "date": rec.date,
                    "total": rec.total,
                    "currency": rec.currency,
                    "category": rec.category,
                    "confidence": rec.confidence,
                })
                results["extracted"] += 1

            # --- 2. Drive folder scan (optional) --------------------------- #
            if params.drive_folder_id:
                drive = _drive()
                df_resp = drive.files().list(
                    q=(
                        f"'{params.drive_folder_id}' in parents and "
                        "trashed = false and ("
                        "mimeType = 'application/pdf' or "
                        "mimeType contains 'image/')"
                    ),
                    pageSize=100,
                    fields="files(id,name,mimeType,size)",
                ).execute()
                for f in df_resp.get("files", []):
                    results["scanned"] += 1
                    if f["id"] in seen_source_ids:
                        results["skipped_dup"] += 1
                        continue
                    try:
                        content = drive.files().get_media(
                            fileId=f["id"],
                        ).execute()
                        if f["mimeType"] == "application/pdf":
                            rec = _r.extract_from_pdf(
                                content, source_id=f["id"],
                                source_kind="drive_pdf",
                            )
                        else:
                            rec = _r.extract_from_image(
                                content, mime_type=f["mimeType"],
                                source_id=f["id"],
                                source_kind="drive_image",
                            )
                        try:
                            rec = _r.enrich_low_confidence_receipt(rec)
                        except Exception as e:
                            log.warning(
                                "enrichment failed for drive %s: %s",
                                f["id"], e,
                            )
                        if (params.skip_low_confidence
                                and rec.confidence < 0.4):
                            results["skipped_low_conf"] += 1
                            continue
                        # Cross-source content dedup (also catches photos
                        # of receipts that already arrived via inbox).
                        ckey = _r.content_key(
                            rec.merchant, rec.date, rec.total, rec.last_4,
                        )
                        if ckey and ckey in seen_content_keys:
                            results["skipped_dup_content"] += 1
                            continue
                        if ckey:
                            seen_content_keys.add(ckey)
                        row = _r.receipt_to_sheet_row(
                            rec, logged_at=now_iso,
                            receipt_link=f"https://drive.google.com/file/d/{f['id']}/view",
                            redact_payment=redact_payment,
                        )
                        new_rows.append(row)
                        extracted_records.append({
                            "source_id": f["id"],
                            "merchant": rec.merchant,
                            "date": rec.date,
                            "total": rec.total,
                            "currency": rec.currency,
                            "category": rec.category,
                            "confidence": rec.confidence,
                        })
                        results["extracted"] += 1
                    except Exception as e:
                        log.warning("drive extract failed for %s: %s", f["id"], e)
                        results["errors"] += 1

            # --- 3. Chat space scan (optional) -------------------------- #
            if params.chat_space_id:
                chat_rows, chat_records = _scan_chat_space(
                    space_id=params.chat_space_id,
                    days=params.days,
                    max_messages=params.chat_max_messages,
                    seen_source_ids=seen_source_ids,
                    seen_content_keys=seen_content_keys,
                    results=results,
                    redact_payment=redact_payment,
                    now_iso=now_iso,
                    archive_pdfs=params.archive_pdfs,
                    archive_folder_id=archive_folder_id,
                    skip_low_confidence=params.skip_low_confidence,
                )
                new_rows.extend(chat_rows)
                extracted_records.extend(chat_records)

            if is_dry_run(params.dry_run):
                return dry_run_preview("workflow_extract_receipts", {
                    "would_append_rows": len(new_rows),
                    "stats": results,
                    "sheet_id": sheet_id,
                    "sheet_title": sheet_title,
                    "sample_extracted": extracted_records[:10],
                })

            # --- 3. Append to Sheet --------------------------------------- #
            qb_csv_link = None
            if new_rows:
                _sheets().spreadsheets().values().append(
                    spreadsheetId=sheet_id, range="A:Q",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": new_rows},
                ).execute()
                log.info(
                    "workflow_extract_receipts: appended %d rows to %s",
                    len(new_rows), sheet_id,
                )

            # --- 4. Optional QB CSV --------------------------------------- #
            if params.export_qb_csv and new_rows and archive_folder_id:
                try:
                    buf = io.StringIO()
                    w = csv.writer(buf)
                    w.writerow(_r.QB_CSV_COLUMNS)
                    for row_data in extracted_records:
                        # Re-build a thin ExtractedReceipt from the dicts to
                        # leverage receipt_to_qb_row's mapping.
                        rec = _r.ExtractedReceipt(
                            date=row_data.get("date"),
                            merchant=row_data.get("merchant"),
                            total=row_data.get("total"),
                            currency=row_data.get("currency", "USD"),
                            category=row_data.get("category", "Misc — Uncategorized"),
                            source_id=row_data.get("source_id"),
                        )
                        w.writerow(_r.receipt_to_qb_row(rec))
                    csv_bytes = buf.getvalue().encode("utf-8")
                    qb_csv_link = _archive_pdf_to_drive(
                        _drive(), archive_folder_id,
                        f"qb_export_{_dt.date.today().isoformat()}.csv",
                        csv_bytes, "text/csv",
                    )
                except Exception as e:
                    log.warning("QB CSV export failed: %s", e)

            return json.dumps({
                "status": "ok",
                "sheet_id": sheet_id,
                "sheet_title": sheet_title,
                "sheet_was_created": sheet_created,
                "sheet_url": (
                    f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
                ),
                "archive_folder_id": archive_folder_id,
                "qb_csv_link": qb_csv_link,
                "appended_rows": len(new_rows),
                "stats": results,
                "sample_extracted": extracted_records[:10],
            }, indent=2)
        except Exception as e:
            log.error("workflow_extract_receipts failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_extract_one_receipt",
        annotations={
            "title": "Extract a single receipt from one Gmail message or Drive file",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_extract_one_receipt(
        params: ExtractOneReceiptInput,
    ) -> str:
        """Extract one specific receipt by ID. Useful for testing extraction
        quality on a known sample, or recovering a missed item."""
        try:
            if bool(params.message_id) == bool(params.drive_file_id):
                return "Error: provide exactly one of message_id or drive_file_id."

            redact_payment = bool(
                config.get("receipts_redact_payment_details", True),
            )

            if params.message_id:
                gmail = _gmail()
                full = gmail.users().messages().get(
                    userId="me", id=params.message_id, format="full",
                ).execute()
                # Try PDF/image attachment first
                attachments = []
                def _walk(payload, acc):
                    fn = payload.get("filename")
                    body = payload.get("body", {}) or {}
                    if fn and body.get("attachmentId"):
                        acc.append({
                            "id": body["attachmentId"],
                            "filename": fn, "mime": payload.get("mimeType", ""),
                        })
                    for p in payload.get("parts", []) or []:
                        _walk(p, acc)
                _walk(full.get("payload", {}), attachments)
                pdf_or_image = next(
                    (a for a in attachments
                     if a["mime"] in ("application/pdf", "image/jpeg",
                                      "image/png", "image/gif")),
                    None,
                )
                if pdf_or_image:
                    att = gmail.users().messages().attachments().get(
                        userId="me", messageId=params.message_id,
                        id=pdf_or_image["id"],
                    ).execute()
                    att_bytes = base64.urlsafe_b64decode(att["data"])
                    if pdf_or_image["mime"] == "application/pdf":
                        rec = _r.extract_from_pdf(
                            att_bytes, source_id=params.message_id,
                            source_kind="email_pdf",
                        )
                    else:
                        rec = _r.extract_from_image(
                            att_bytes, mime_type=pdf_or_image["mime"],
                            source_id=params.message_id,
                            source_kind="email_image",
                        )
                else:
                    body_text = _extract_plaintext(full.get("payload", {}))
                    rec = _r.extract_from_text(
                        body_text, source_id=params.message_id,
                        source_kind="email_text",
                    )
            else:
                drive = _drive()
                meta = drive.files().get(
                    fileId=params.drive_file_id,
                    fields="id,name,mimeType",
                ).execute()
                content = drive.files().get_media(
                    fileId=params.drive_file_id,
                ).execute()
                if meta["mimeType"] == "application/pdf":
                    rec = _r.extract_from_pdf(
                        content, source_id=params.drive_file_id,
                        source_kind="drive_pdf",
                    )
                else:
                    rec = _r.extract_from_image(
                        content, mime_type=meta["mimeType"],
                        source_id=params.drive_file_id,
                        source_kind="drive_image",
                    )

            response: dict = {
                "status": "ok",
                "extracted": rec.model_dump(),
            }

            # Optionally append to a Sheet
            if params.sheet_id:
                now_iso = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
                row = _r.receipt_to_sheet_row(
                    rec, logged_at=now_iso, redact_payment=redact_payment,
                )
                _sheets().spreadsheets().values().append(
                    spreadsheetId=params.sheet_id, range="A:Q",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [row]},
                ).execute()
                response["appended_to_sheet"] = params.sheet_id

            return json.dumps(response, indent=2)
        except Exception as e:
            log.error("workflow_extract_one_receipt failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_extract_receipts_from_chat",
        annotations={
            "title": "Scan one Gchat space for receipts (text + attachments)",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_extract_receipts_from_chat(
        params: ExtractReceiptsFromChatInput,
    ) -> str:
        """Standalone Chat receipt sweep — useful when you have a dedicated
        '#receipts' or '#expenses' Gchat room and want to extract from it
        without also scanning inbox/Drive.

        For each message in the space (within `days`):
          - If it has a PDF/image attachment, extract via Claude Vision
            (chat_pdf / chat_image source_kind).
          - Otherwise, run the receipt classifier on the text body. If it
            looks receipt-shaped, extract via Claude Haiku text mode
            (chat_text source_kind).
          - Source ID is `chat:<space>/messages/<id>` so re-running won't
            duplicate prior extractions, and content_key dedup catches
            same-purchase-different-source cases.

        All the usual enrichment + cache + dedup + needs_review logic still
        applies. To scan inbox + Drive + Chat in one pass, use
        workflow_extract_receipts with chat_space_id set instead.
        """
        try:
            import llm
            ok, reason = llm.is_available()
            if not ok:
                return json.dumps({"status": "no_llm", "reason": reason}, indent=2)

            redact_payment = bool(
                config.get("receipts_redact_payment_details", True),
            )
            now_iso = _dt.datetime.now().astimezone().isoformat(timespec="seconds")

            sheet_id, sheet_title, err = _resolve_sheet(
                params.sheet_id, params.sheet_name,
            )
            if err is not None:
                return json.dumps(err, indent=2)
            sheet_id, sheet_title = _ensure_archive_sheet(sheet_id)

            archive_folder_id = None
            if params.archive_pdfs:
                archive_folder_id = _ensure_drive_folder(
                    params.archive_drive_folder_id
                    or config.get("receipts_drive_folder_id"),
                    default_name="CoAssisted Receipts",
                )

            seen_source_ids = _existing_sheet_source_ids(sheet_id)
            seen_content_keys = _existing_sheet_content_keys(sheet_id)
            results = {
                "extracted": 0,
                "skipped_dup": 0,
                "skipped_dup_content": 0,
                "skipped_low_conf": 0,
                "skipped_not_receipt": 0,
                "errors": 0,
                "scanned": 0,
            }

            new_rows, new_records = _scan_chat_space(
                space_id=params.chat_space_id,
                days=params.days,
                max_messages=params.max_messages,
                seen_source_ids=seen_source_ids,
                seen_content_keys=seen_content_keys,
                results=results,
                redact_payment=redact_payment,
                now_iso=now_iso,
                archive_pdfs=params.archive_pdfs,
                archive_folder_id=archive_folder_id,
                skip_low_confidence=params.skip_low_confidence,
            )

            if is_dry_run(params.dry_run):
                return dry_run_preview(
                    "workflow_extract_receipts_from_chat",
                    {
                        "would_append_rows": len(new_rows),
                        "stats": results,
                        "sheet_id": sheet_id,
                        "sheet_title": sheet_title,
                        "sample_extracted": new_records[:10],
                    },
                )

            if new_rows:
                _sheets().spreadsheets().values().append(
                    spreadsheetId=sheet_id, range="A:Q",
                    valueInputOption="USER_ENTERED",
                    insertDataOption="INSERT_ROWS",
                    body={"values": new_rows},
                ).execute()

            return json.dumps({
                "status": "ok",
                "sheet_id": sheet_id,
                "sheet_title": sheet_title,
                "sheet_url": (
                    f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
                ),
                "archive_folder_id": archive_folder_id,
                "appended_rows": len(new_rows),
                "stats": results,
                "sample_extracted": new_records[:10],
            }, indent=2)
        except Exception as e:
            log.error("workflow_extract_receipts_from_chat failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_list_receipt_sheets",
        annotations={
            "title": "List all expense sheets in your Drive ('Receipts — *')",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_list_receipt_sheets() -> str:
        """Return every Google Sheet in your Drive whose name starts with
        'Receipts — '. Used by the orchestrator to surface choices when no
        sheet is specified, and by the user to inventory their reports.

        Each entry includes sheet_id, name, label (the part after the prefix),
        approximate row_count, last_modified timestamp, and a webViewLink.
        Sorted newest-modified first.
        """
        try:
            sheets = _list_receipt_sheets()
            return json.dumps({
                "status": "ok",
                "count": len(sheets),
                "prefix": RECEIPT_SHEET_PREFIX,
                "sheets": sheets,
                "hint": (
                    "Pass any sheet_id (or its label as sheet_name) to "
                    "workflow_extract_receipts, workflow_recategorize_receipt, "
                    "or workflow_export_receipts_qb_csv. Create a new one with "
                    "workflow_create_receipt_sheet."
                ) if sheets else (
                    "No expense sheets found yet. Create your first with "
                    "workflow_create_receipt_sheet (e.g. name='Personal 2026 Q2')."
                ),
            }, indent=2)
        except Exception as e:
            log.error("workflow_list_receipt_sheets failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_create_receipt_sheet",
        annotations={
            "title": "Create a new expense sheet ('Receipts — {name}')",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_create_receipt_sheet(
        params: CreateReceiptSheetInput,
    ) -> str:
        """Create a fresh Google Sheet titled 'Receipts — {name}' with the
        17-column header row pre-populated. Returns the new sheet_id, full
        title, and webViewLink — pass any of those to the extractor.

        If `name` already starts with 'Receipts — ' it's used as-is so users
        can paste full titles back without doubling the prefix.
        """
        try:
            label = params.name.strip()
            if label.lower().startswith(RECEIPT_SHEET_PREFIX.lower()):
                full_title = label[:len(RECEIPT_SHEET_PREFIX)] + label[len(RECEIPT_SHEET_PREFIX):]
                # Normalize the prefix casing/dash exactly:
                full_title = RECEIPT_SHEET_PREFIX + label[len(RECEIPT_SHEET_PREFIX):].strip()
            else:
                full_title = f"{RECEIPT_SHEET_PREFIX}{label}"

            # Refuse to create a duplicate; point the user at the existing one.
            for s in _list_receipt_sheets():
                if s["name"].lower() == full_title.lower():
                    return json.dumps({
                        "status": "already_exists",
                        "sheet_id": s["sheet_id"],
                        "title": s["name"],
                        "url": s["url"],
                        "hint": (
                            "A sheet with this name exists. Use it directly "
                            "or pick a different name (e.g. add a quarter or "
                            "year suffix)."
                        ),
                    }, indent=2)

            sheets = _sheets()
            created = sheets.spreadsheets().create(
                body={"properties": {"title": full_title}},
            ).execute()
            new_id = created["spreadsheetId"]
            sheets.spreadsheets().values().update(
                spreadsheetId=new_id, range="A1",
                valueInputOption="RAW",
                body={"values": [_r.SHEET_COLUMNS]},
            ).execute()
            return json.dumps({
                "status": "ok",
                "sheet_id": new_id,
                "title": full_title,
                "label": full_title[len(RECEIPT_SHEET_PREFIX):],
                "url": f"https://docs.google.com/spreadsheets/d/{new_id}/edit",
                "hint": (
                    "Pass this sheet_id (or sheet_name) to "
                    "workflow_extract_receipts on your next run."
                ),
            }, indent=2)
        except Exception as e:
            log.error("workflow_create_receipt_sheet failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_recategorize_receipt",
        annotations={
            "title": "Edit the category on an existing Receipts-Sheet row",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_recategorize_receipt(
        params: RecategorizeReceiptInput,
    ) -> str:
        """Update one row's category. The Sheet's column F is `category`.

        Also teaches the merchant cache: the corrected category becomes a
        manual_correction record for that merchant, so the system learns
        from the fix and won't make the same mistake on the next receipt
        from the same vendor.
        """
        try:
            if params.new_category not in _r.DEFAULT_CATEGORIES:
                return json.dumps({
                    "status": "invalid_category",
                    "valid_categories": _r.DEFAULT_CATEGORIES,
                }, indent=2)
            sheet_id, _title, err = _resolve_sheet(
                params.sheet_id, params.sheet_name,
            )
            if err is not None:
                return json.dumps(err, indent=2)
            cell = f"F{params.row_number}"
            sheets = _sheets()
            sheets.spreadsheets().values().update(
                spreadsheetId=sheet_id, range=cell,
                valueInputOption="USER_ENTERED",
                body={"values": [[params.new_category]]},
            ).execute()

            # Read the merchant from column C of the same row so we can
            # teach the cache from the user's correction. Best-effort —
            # skip if the row read fails.
            merchant = None
            cache_updated = False
            try:
                row_resp = sheets.spreadsheets().values().get(
                    spreadsheetId=sheet_id,
                    range=f"C{params.row_number}",
                ).execute()
                rows = row_resp.get("values") or []
                if rows and rows[0]:
                    merchant = (rows[0][0] or "").strip() or None
            except Exception as e:
                log.warning("read merchant for cache update failed: %s", e)

            if merchant:
                try:
                    import merchant_cache as _mc
                    _mc.apply_correction(
                        merchant, category=params.new_category,
                    )
                    cache_updated = True
                except Exception as e:
                    log.warning("cache update on recategorize failed: %s", e)

            return json.dumps({
                "status": "ok",
                "sheet_id": sheet_id,
                "row_number": params.row_number,
                "new_category": params.new_category,
                "cell_updated": cell,
                "merchant_cache_updated": cache_updated,
                "merchant": merchant,
            }, indent=2)
        except Exception as e:
            log.error("workflow_recategorize_receipt failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_list_known_merchants",
        annotations={
            "title": "Inventory of merchants the receipt extractor has learned",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def workflow_list_known_merchants() -> str:
        """Show every merchant in the persistent cache, sorted by hit_count.

        Each row reports how the system learned about the merchant (Maps
        verification, web search, or your own manual correction), how many
        receipts it has been applied to, and what category it maps to.
        Use this to audit what the cache has been teaching itself.
        """
        try:
            import merchant_cache as _mc
            entries = _mc.list_all(sort_by="hit_count", limit=200)
            return json.dumps({
                "status": "ok",
                "count": len(entries),
                "stats": _mc.stats(),
                "merchants": entries,
            }, indent=2)
        except Exception as e:
            log.error("workflow_list_known_merchants failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_forget_merchant",
        annotations={
            "title": "Drop one merchant from the learned cache",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def workflow_forget_merchant(params: ForgetMerchantInput) -> str:
        """Force re-verification of one merchant on the next receipt.

        Useful when the cache learned a wrong category and you want to start
        fresh, or when a business rebrands and the old entry is now stale.
        """
        try:
            import merchant_cache as _mc
            removed = _mc.forget(params.merchant)
            return json.dumps({
                "status": "ok" if removed else "not_in_cache",
                "merchant": params.merchant,
                "removed": removed,
                "hint": (
                    "Next receipt from this merchant will run the full "
                    "enrichment ladder and re-cache the result."
                ) if removed else (
                    "No entry found. The cache normalizes names so "
                    "'Anthropic' and 'anthropic' map to the same key."
                ),
            }, indent=2)
        except Exception as e:
            log.error("workflow_forget_merchant failed: %s", e)
            return format_error(e)

    @mcp.tool(
        name="workflow_export_receipts_qb_csv",
        annotations={
            "title": "Build a QuickBooks CSV from an existing Receipts Sheet",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_export_receipts_qb_csv(
        params: ExportReceiptsQbCsvInput,
    ) -> str:
        """Read the Sheet, project each row into QB columns, save CSV to Drive.

        Date filters are inclusive. Account mapping is the default
        receipts.py table — override via config.receipts_qb_account_map.
        """
        try:
            account_map = config.get("receipts_qb_account_map") or None

            sheet_id, _title, err = _resolve_sheet(
                params.sheet_id, params.sheet_name,
            )
            if err is not None:
                return json.dumps(err, indent=2)

            sheets = _sheets()
            resp = sheets.spreadsheets().values().get(
                spreadsheetId=sheet_id, range="A:Q",
            ).execute()
            rows = resp.get("values", [])
            if len(rows) < 2:
                return json.dumps({
                    "status": "empty_sheet",
                    "row_count": 0,
                }, indent=2)
            header = rows[0]
            data_rows = rows[1:]
            # Project each row → ExtractedReceipt → QB row
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(_r.QB_CSV_COLUMNS)
            included = 0
            skipped = 0
            for r in data_rows:
                # Pad row to header length
                r = r + [""] * (len(header) - len(r))
                row_dict = dict(zip(header, r))
                row_date = row_dict.get("date") or ""
                if params.date_from and row_date < params.date_from:
                    skipped += 1
                    continue
                if params.date_to and row_date > params.date_to:
                    skipped += 1
                    continue
                try:
                    total = float(row_dict.get("total") or 0)
                except ValueError:
                    total = 0.0
                rec = _r.ExtractedReceipt(
                    date=row_date or None,
                    merchant=row_dict.get("merchant") or None,
                    total=total if total else None,
                    currency=row_dict.get("currency") or "USD",
                    category=row_dict.get("category") or "Misc — Uncategorized",
                    location=row_dict.get("location") or None,
                    notes=row_dict.get("notes") or None,
                    source_kind=row_dict.get("source_kind") or "",
                    source_id=row_dict.get("source_id") or None,
                )
                w.writerow(_r.receipt_to_qb_row(rec, account_map=account_map))
                included += 1
            csv_text = buf.getvalue()

            # Save / return
            if params.save_to_drive_folder_id:
                link = _archive_pdf_to_drive(
                    _drive(), params.save_to_drive_folder_id,
                    f"qb_export_{_dt.date.today().isoformat()}.csv",
                    csv_text.encode("utf-8"), "text/csv",
                )
                return json.dumps({
                    "status": "ok",
                    "drive_link": link,
                    "rows_included": included,
                    "rows_skipped_filter": skipped,
                }, indent=2)
            else:
                return json.dumps({
                    "status": "ok",
                    "csv_base64": base64.b64encode(
                        csv_text.encode("utf-8"),
                    ).decode("ascii"),
                    "csv_size_bytes": len(csv_text),
                    "rows_included": included,
                    "rows_skipped_filter": skipped,
                    "hint": (
                        "csv_base64 contains the full CSV. Decode and save "
                        "locally, or pass save_to_drive_folder_id to "
                        "auto-archive."
                    ),
                }, indent=2)
        except Exception as e:
            log.error("workflow_export_receipts_qb_csv failed: %s", e)
            return format_error(e)


# --------------------------------------------------------------------------- #
# Helpers (text extraction from Gmail payload)
# --------------------------------------------------------------------------- #


def _extract_plaintext(payload: dict) -> str:
    """Walk a Gmail payload, return concatenated plain-text bodies."""
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(
            payload["body"]["data"],
        ).decode("utf-8", errors="replace")
    out: list[str] = []
    for part in payload.get("parts", []) or []:
        chunk = _extract_plaintext(part)
        if chunk:
            out.append(chunk)
    return "\n\n".join(out)
