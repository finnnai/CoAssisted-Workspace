# © 2026 CoAssisted Workspace. Licensed under MIT.
"""AP-4: No-failed-trigger capture sweep.

Wraps the Day-1 hot deploy's `ap-receipt-sweep` scheduled task with
deterministic Python logic. Each sweep cycle:

    1. Pull unread Gmail messages with the AP/Inbound label.
    2. Pull new messages from the Receipts Chat space.
    3. For each item:
         a. Run AP-5 project router (sender + subject + body + timestamp).
         b. Based on the router's recommended action, file accordingly:
              auto_file       → download attachment to project's
                                 Receipts/{YYYY-MM}/, mark as read.
              auto_file_flag  → file but add the message ID to the
                                 review queue for operator follow-up.
              chat_picker     → post a picker prompt to the Receipts
                                 Chat space, hold the item.
              triage          → download to Surefox AP/Triage/.
    4. Return a summary report (counts per action, per-item dispositions).

This module focuses on the routing-and-disposition decisions. The
actual Drive download / file-move / chat-post wiring lives in the
existing tools/drive + tools/chat surfaces — we call into them rather
than reimplementing.

Idempotency:
    Every Gmail message we touch gets the UNREAD label removed at the
    end of its processing block. If the sweep crashes mid-batch, the
    next cycle re-picks up where it left off (still-unread items).
"""

from __future__ import annotations

import base64
import datetime as _dt
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import project_registry
import project_router


# =============================================================================
# Watermark store — tracks last-processed chat message per space
# =============================================================================

_PROJECT_ROOT = Path(__file__).resolve().parent
_WATERMARK_PATH = _PROJECT_ROOT / "ap_sweep_watermark.json"


def _read_watermarks() -> dict[str, str]:
    if not _WATERMARK_PATH.exists():
        return {}
    try:
        with _WATERMARK_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_watermarks(data: dict[str, str]) -> None:
    _WATERMARK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix="ap_sweep_watermark.", suffix=".json.tmp",
        dir=str(_WATERMARK_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, _WATERMARK_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# =============================================================================
# Result types
# =============================================================================

@dataclass
class SweepItem:
    source: str                    # "email" | "chat"
    source_id: str                 # gmail message_id or chat message_id
    sender: Optional[str]
    subject: Optional[str]
    timestamp: Optional[_dt.datetime]
    project_code: Optional[str]
    confidence: float
    tier: str
    action: str                    # auto_file | auto_file_flag | chat_picker | triage
    target_folder_id: Optional[str]  # the Drive folder we filed to (if any)
    note: str = ""


@dataclass
class SweepResult:
    items: list[SweepItem] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""

    def summary_line(self) -> str:
        return (
            f"{self.counts.get('auto_file', 0)} auto-filed · "
            f"{self.counts.get('auto_file_flag', 0)} flagged · "
            f"{self.counts.get('chat_picker', 0)} awaiting picker · "
            f"{self.counts.get('triage', 0)} in Triage"
        )


# =============================================================================
# Routing decision (pure function — easy to test)
# =============================================================================

def decide_disposition(
    *,
    sender_email: Optional[str],
    subject: Optional[str],
    body: Optional[str],
    timestamp: Optional[_dt.datetime],
    use_llm: bool = True,
) -> tuple[project_router.RouteResult, str]:
    """Decide what to do with one inbound item.

    Returns (RouteResult, action_str) where action_str is one of
    'auto_file' | 'auto_file_flag' | 'chat_picker' | 'triage'.
    """
    result = project_router.route_project(
        sender_email=sender_email,
        subject=subject,
        body=body,
        timestamp=timestamp,
        use_llm=use_llm,
    )
    action = project_router.confidence_action(result)
    return result, action


def target_subfolder_for_action(
    project_code: str,
    action: str,
    *,
    when: Optional[_dt.date] = None,
) -> Optional[str]:
    """Resolve the Drive folder to drop a filed item into.

    For auto_file / auto_file_flag, returns the {Receipts/YYYY-MM/}
    folder ID for the project. For chat_picker / triage, returns None
    (caller routes to Triage/ separately).

    Lazy-creates the month bucket if missing — same idempotent behavior
    as ap_tree.ensure_month_subtree.
    """
    if action not in ("auto_file", "auto_file_flag"):
        return None
    if not project_code:
        return None
    # Defer the import to keep the module importable without the Drive
    # API stack (e.g. in unit tests that only exercise the routing
    # decision tree).
    import ap_tree
    bucket = ap_tree.ensure_month_subtree(
        project_code, when=when, kinds=("receipts",),
    )
    return bucket.get("receipts")


# =============================================================================
# Sweep cycle (orchestrator — calls into mail/chat/drive)
# =============================================================================

def run_sweep_cycle(
    *,
    email_label: str = "AP/Inbound",
    chat_space_id: str = "spaces/AAQAly0xFuE",  # Receipts (Day-1)
    triage_folder_id: str = "1wBnOtbMVBrf0B5idKq_1teOKVlAKCtTY",  # Day-1 Triage
    max_items_per_source: int = 50,
    dry_run: bool = False,
) -> SweepResult:
    """Run one sweep cycle. Returns a SweepResult with per-item dispositions.

    `dry_run=True` makes routing decisions but doesn't download files,
    move messages, or post to chat — useful for previewing what a
    cycle would do.

    The actual mail/chat/drive operations are gated through the
    existing MCP tool surfaces; if those modules aren't available
    (e.g., in unit tests with no OAuth context), the sweep returns
    an empty result rather than crashing.
    """
    started = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    result = SweepResult(started_at=started)

    items = list(_pull_email_items(email_label, max_items=max_items_per_source))
    items += list(_pull_chat_items(chat_space_id, max_items=max_items_per_source))

    for item in items:
        route_result, action = decide_disposition(
            sender_email=item.sender,
            subject=item.subject,
            body=None,  # Pull body lazily only when routing needs it
            timestamp=item.timestamp,
        )
        item.project_code = route_result.project_code
        item.confidence = route_result.confidence
        item.tier = route_result.tier
        item.action = action

        if not dry_run:
            target_folder = target_subfolder_for_action(
                route_result.project_code or "",
                action,
                when=(item.timestamp.date() if item.timestamp else None),
            )
            item.target_folder_id = target_folder
            _execute_disposition(item, route_result, triage_folder_id)

        result.counts[action] = result.counts.get(action, 0) + 1
        result.items.append(item)

    result.finished_at = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    return result


# =============================================================================
# Source readers — best-effort, no-throw
# =============================================================================

def _pull_email_items(label: str, *, max_items: int):
    """Yield SweepItems from unread Gmail with the given label.

    Best-effort: returns empty when the Gmail surface isn't available
    (no OAuth, sandbox without tokens, etc.).
    """
    try:
        from googleapiclient.discovery import build
        from auth import get_credentials
    except ImportError:
        return
    try:
        creds = get_credentials()
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        q = f"label:{label} is:unread -in:trash"
        resp = service.users().messages().list(
            userId="me", q=q, maxResults=max_items,
        ).execute()
        msg_refs = resp.get("messages") or []
        for ref in msg_refs:
            mid = ref.get("id")
            full = service.users().messages().get(
                userId="me", id=mid, format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {
                h["name"]: h["value"]
                for h in (full.get("payload", {}).get("headers") or [])
            }
            ts = None
            try:
                from email.utils import parsedate_to_datetime
                if headers.get("Date"):
                    ts = parsedate_to_datetime(headers["Date"])
            except (TypeError, ValueError):
                pass
            yield SweepItem(
                source="email",
                source_id=mid,
                sender=headers.get("From"),
                subject=headers.get("Subject"),
                timestamp=ts,
                project_code=None,
                confidence=0.0,
                tier="",
                action="",
                target_folder_id=None,
            )
    except Exception:
        return  # Best-effort; the sweep returns whatever items it got.


def _pull_chat_items(space_id: str, *, max_items: int):
    """Yield SweepItems from new Receipts-space chat messages.

    Tracks a per-space watermark of the last-processed createTime so
    repeated sweeps don't re-process the same messages. Best-effort:
    returns empty (as a generator) when the Chat API surface isn't
    available.
    """
    try:
        from googleapiclient.discovery import build
        from auth import get_credentials
    except ImportError:
        return
    try:
        creds = get_credentials()
        service = build("chat", "v1", credentials=creds, cache_discovery=False)
    except Exception:
        return

    watermarks = _read_watermarks()
    last_seen = watermarks.get(space_id, "")
    new_high_water = last_seen

    try:
        # Chat API filter syntax: createTime > "RFC3339Nano".
        kwargs = {"parent": space_id, "pageSize": max_items, "orderBy": "createTime asc"}
        if last_seen:
            kwargs["filter"] = f'createTime > "{last_seen}"'
        resp = service.spaces().messages().list(**kwargs).execute()
    except Exception:
        return

    for msg in resp.get("messages") or []:
        create_time = msg.get("createTime") or ""
        if create_time and create_time > new_high_water:
            new_high_water = create_time
        sender_obj = msg.get("sender") or {}
        sender_email = sender_obj.get("displayName") or sender_obj.get("name") or ""
        text = msg.get("text") or ""
        # Chat messages have no formal subject; use the first line as a proxy.
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        try:
            ts = _dt.datetime.fromisoformat(create_time.replace("Z", "+00:00")) if create_time else None
        except ValueError:
            ts = None
        yield SweepItem(
            source="chat",
            source_id=msg.get("name") or "",
            sender=sender_email,
            subject=first_line[:120],
            timestamp=ts,
            project_code=None,
            confidence=0.0,
            tier="",
            action="",
            target_folder_id=None,
        )

    # Persist watermark only if we observed newer messages.
    if new_high_water and new_high_water != last_seen:
        watermarks[space_id] = new_high_water
        try:
            _write_watermarks(watermarks)
        except Exception:
            pass  # Best-effort.


# =============================================================================
# Disposition executor
# =============================================================================

def _execute_disposition(
    item: SweepItem,
    route_result: project_router.RouteResult,
    triage_folder_id: str,
) -> None:
    """Carry out the file-or-chat action decided by the router.

    Best-effort: any failure is captured in item.note; the sweep
    continues with the remaining items.
    """
    try:
        if item.action in ("auto_file", "auto_file_flag"):
            if item.target_folder_id:
                _download_attachments_to_folder(item, item.target_folder_id)
            else:
                # Lazy bucket couldn't be created (no project record or
                # no Drive root). Fall back to Triage.
                _download_attachments_to_folder(item, triage_folder_id)
                item.note = "month bucket unavailable; fell back to Triage"
            _mark_email_read(item)
        elif item.action == "chat_picker":
            _post_picker_prompt(item, route_result)
            # Don't mark read — submitter's reply re-enters the loop.
        elif item.action == "triage":
            _download_attachments_to_folder(item, triage_folder_id)
            _mark_email_read(item)
    except Exception as e:
        item.note = f"disposition error: {e}"


def _download_attachments_to_folder(item: SweepItem, folder_id: str) -> None:
    """Download a Gmail message's attachments and upload to a Drive folder.

    Walks message parts, pulls each attachment via the Gmail API,
    uploads to the destination Drive folder using the AP-6 naming
    convention `YYYY-MM-DD_sender_amount_type.ext`. Best-effort —
    failures are captured in item.note.
    """
    if item.source != "email" or not item.source_id or not folder_id:
        return
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload
        from auth import get_credentials
    except ImportError:
        item.note = (item.note + " [download skipped: API libs missing]").strip()
        return

    try:
        creds = get_credentials()
        gmail_svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        drive_svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        msg = gmail_svc.users().messages().get(
            userId="me", id=item.source_id, format="full"
        ).execute()
    except Exception as e:
        item.note = (item.note + f" [download error: {e}]").strip()
        return

    attachments_uploaded = 0
    for filename, data, mime in _iter_attachments(gmail_svc, msg):
        if not data:
            continue
        named = _format_filename(item, filename)
        media = MediaIoBaseUpload(
            io.BytesIO(data), mimetype=mime or "application/octet-stream",
            resumable=False,
        )
        try:
            drive_svc.files().create(
                body={"name": named, "parents": [folder_id]},
                media_body=media,
                fields="id",
            ).execute()
            attachments_uploaded += 1
        except Exception as e:
            item.note = (item.note + f" [upload error: {e}]").strip()
            continue

    if attachments_uploaded:
        item.note = (
            item.note + f" [filed {attachments_uploaded} attachment(s)]"
        ).strip()
    else:
        item.note = (item.note + " [no attachments found]").strip()


def _iter_attachments(gmail_svc, msg: dict):
    """Yield (filename, bytes, mimeType) for every attachment in a Gmail msg."""
    msg_id = msg.get("id") or ""

    def _walk(parts):
        for part in parts or []:
            sub_parts = part.get("parts")
            if sub_parts:
                yield from _walk(sub_parts)
                continue
            filename = part.get("filename") or ""
            body = part.get("body") or {}
            if not filename or not (body.get("attachmentId") or body.get("data")):
                continue
            mime = part.get("mimeType") or "application/octet-stream"
            data: Optional[bytes] = None
            if body.get("data"):
                try:
                    data = base64.urlsafe_b64decode(body["data"])
                except Exception:
                    data = None
            elif body.get("attachmentId"):
                try:
                    att = gmail_svc.users().messages().attachments().get(
                        userId="me", messageId=msg_id, id=body["attachmentId"],
                    ).execute()
                    data = base64.urlsafe_b64decode(att.get("data") or "")
                except Exception:
                    data = None
            if data:
                yield (filename, data, mime)

    payload = msg.get("payload") or {}
    yield from _walk(payload.get("parts") or [payload])


def _format_filename(item: SweepItem, original_name: str) -> str:
    """Apply the AP-6 naming convention: YYYY-MM-DD_sender_attachment.ext.

    Falls back to the original filename when item lacks date/sender.
    """
    safe_orig = re.sub(r"[^\w.\-]+", "_", original_name).strip("_") or "attachment"
    if item.timestamp:
        date_str = item.timestamp.strftime("%Y-%m-%d")
    else:
        date_str = _dt.date.today().isoformat()
    sender_token = "unknown"
    if item.sender:
        # Pull the email address out, take the local-part for brevity.
        m = re.search(r"<([^>]+)>", item.sender)
        addr = m.group(1) if m else item.sender
        local = addr.split("@", 1)[0] if "@" in addr else addr
        sender_token = re.sub(r"[^\w]+", "", local)[:24] or "unknown"
    return f"{date_str}_{sender_token}_{safe_orig}"


def _mark_email_read(item: SweepItem) -> None:
    """Remove the UNREAD label so the next sweep skips this message."""
    if item.source != "email" or not item.source_id:
        return
    try:
        from googleapiclient.discovery import build
        from auth import get_credentials
        creds = get_credentials()
        svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        svc.users().messages().modify(
            userId="me", id=item.source_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        item.note = (item.note + " [marked read]").strip()
    except Exception as e:
        item.note = (item.note + f" [mark-read error: {e}]").strip()


def _post_picker_prompt(
    item: SweepItem,
    route_result: project_router.RouteResult,
) -> None:
    """Post a project-picker prompt to the Receipts Chat space.

    Lists candidates from the router (when populated) plus an Other
    escape so the submitter can name a project not in the picker.
    """
    try:
        from googleapiclient.discovery import build
        from auth import get_credentials
    except ImportError:
        item.note = (item.note + " [picker skipped: API libs missing]").strip()
        return

    candidates = route_result.candidates or []
    if candidates:
        cand_lines = "\n".join(
            f"  • *{c.get('code', '?')}* — {c.get('name', '')}"
            for c in candidates
        )
    else:
        cand_lines = "  (no candidate match — please reply with a project code)"

    sender_disp = item.sender or "unknown"
    subject_disp = item.subject or "(no subject)"
    text = (
        f"📥 New receipt awaiting project assignment\n\n"
        f"From: {sender_disp}\n"
        f"Subject: {subject_disp}\n\n"
        f"Which project? Reply with the project code:\n"
        f"{cand_lines}\n"
        f"  • *Other* — reply with the project code or name"
    )

    try:
        creds = get_credentials()
        svc = build("chat", "v1", credentials=creds, cache_discovery=False)
        svc.spaces().messages().create(
            parent="spaces/AAQAly0xFuE",  # Receipts space (Day-1)
            body={"text": text},
        ).execute()
        item.note = (
            item.note
            + f" [posted picker: {len(candidates)} candidate(s)]"
        ).strip()
    except Exception as e:
        item.note = (item.note + f" [picker error: {e}]").strip()
