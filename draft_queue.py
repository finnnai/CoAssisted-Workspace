# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Draft queue — generic 'enqueue draft, review, approve, send' pattern.

Generalizes the AP info-request flow so any P2+ workflow can produce a
draft and queue it for human-in-the-loop review before send.

Lifecycle:

    PENDING ───approve───▶ APPROVED  (caller can now send)
       │
       └────discard───────▶ DISCARDED

State persisted to draft_queue.json (atomic write, same pattern as
vendor_followups.py + dm_email_cache.py).

Usage:
    entry_id = enqueue(
        kind='reply', subject='...', body_plain='...', body_html='...',
        target='someone@x.com', source_ref='thread:abc',
        meta={'thread_id': 'abc'},
    )
    # ... user reviews ...
    approved = approve(entry_id)   # returns the entry; caller sends it
    discard(entry_id)              # alternative
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional


_QUEUE_PATH = Path(__file__).resolve().parent / "draft_queue.json"


# --------------------------------------------------------------------------- #
# Status constants
# --------------------------------------------------------------------------- #


STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DISCARDED = "discarded"
STATUS_SENT = "sent"


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _load() -> dict[str, dict]:
    if not _QUEUE_PATH.exists():
        return {}
    try:
        return json.loads(_QUEUE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, dict]) -> None:
    fd, tmp = tempfile.mkstemp(
        prefix="draft_queue.", suffix=".tmp", dir=str(_QUEUE_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _QUEUE_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat()


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


def enqueue(
    *,
    kind: str,
    subject: str,
    body_plain: str,
    body_html: str = "",
    target: str | list[str] = "",
    cc: Optional[list[str]] = None,
    source_ref: Optional[str] = None,
    meta: Optional[dict] = None,
) -> str:
    """Add a new draft to the queue. Returns the entry_id.

    Args:
        kind: workflow code (e.g. "auto_reply_inbound", "rsvp_alternative").
        subject: email subject line.
        body_plain: plain text body.
        body_html: optional HTML body (defaults to autoconverted plain).
        target: To: recipient or list of recipients.
        cc: optional CC list.
        source_ref: free-text reference to the source (e.g. "thread:abc").
        meta: arbitrary dict of extra context (thread_id, event_id, etc).

    Returns:
        entry_id (a UUID-like string) — used for approve/discard.
    """
    if not kind:
        raise ValueError("kind required")
    if not subject and not body_plain:
        raise ValueError("subject or body required")

    entry_id = uuid.uuid4().hex[:12]
    if isinstance(target, str):
        target_list = [target] if target else []
    else:
        target_list = list(target)

    rec = {
        "id": entry_id,
        "kind": kind,
        "subject": subject,
        "body_plain": body_plain,
        "body_html": body_html or "<br>".join(body_plain.splitlines()).replace("\n", "<br>"),
        "to": target_list,
        "cc": list(cc or []),
        "source_ref": source_ref,
        "meta": dict(meta or {}),
        "status": STATUS_PENDING,
        "created_at": _now_iso(),
        "decided_at": None,
        "sent_at": None,
    }
    data = _load()
    data[entry_id] = rec
    _save(data)
    return entry_id


def get(entry_id: str) -> Optional[dict]:
    return _load().get(entry_id)


def list_pending(kind: Optional[str] = None) -> list[dict]:
    """Return all pending entries, optionally filtered by kind."""
    out = []
    for rec in _load().values():
        if rec.get("status") != STATUS_PENDING:
            continue
        if kind and rec.get("kind") != kind:
            continue
        out.append(dict(rec))
    out.sort(key=lambda r: r.get("created_at") or "")
    return out


def list_all(status: Optional[str] = None) -> list[dict]:
    """All entries, optionally filtered by status."""
    out = []
    for rec in _load().values():
        if status and rec.get("status") != status:
            continue
        out.append(dict(rec))
    out.sort(key=lambda r: r.get("created_at") or "")
    return out


def approve(entry_id: str) -> Optional[dict]:
    """Mark a pending entry as approved. Returns the updated record or None."""
    data = _load()
    rec = data.get(entry_id)
    if not rec or rec.get("status") != STATUS_PENDING:
        return None
    rec["status"] = STATUS_APPROVED
    rec["decided_at"] = _now_iso()
    _save(data)
    return dict(rec)


def discard(entry_id: str) -> bool:
    data = _load()
    rec = data.get(entry_id)
    if not rec or rec.get("status") != STATUS_PENDING:
        return False
    rec["status"] = STATUS_DISCARDED
    rec["decided_at"] = _now_iso()
    _save(data)
    return True


def mark_sent(entry_id: str) -> Optional[dict]:
    """Caller calls this after actually sending an approved draft."""
    data = _load()
    rec = data.get(entry_id)
    if not rec or rec.get("status") != STATUS_APPROVED:
        return None
    rec["status"] = STATUS_SENT
    rec["sent_at"] = _now_iso()
    _save(data)
    return dict(rec)


def clear() -> int:
    """Remove every entry. Returns count dropped. Use with care."""
    data = _load()
    n = len(data)
    _save({})
    return n


def clear_by_status(status: str) -> int:
    """Remove every entry with the given status."""
    data = _load()
    keep = {k: v for k, v in data.items() if v.get("status") != status}
    n = len(data) - len(keep)
    _save(keep)
    return n


def update_body(entry_id: str, *, subject: Optional[str] = None,
                body_plain: Optional[str] = None,
                body_html: Optional[str] = None) -> Optional[dict]:
    """Edit a pending draft before approving. Returns updated record or None."""
    data = _load()
    rec = data.get(entry_id)
    if not rec or rec.get("status") != STATUS_PENDING:
        return None
    if subject is not None:
        rec["subject"] = subject
    if body_plain is not None:
        rec["body_plain"] = body_plain
        # Auto-update HTML if not explicitly overridden.
        if body_html is None:
            rec["body_html"] = "<br>".join(body_plain.splitlines()).replace("\n", "<br>")
    if body_html is not None:
        rec["body_html"] = body_html
    _save(data)
    return dict(rec)


# --------------------------------------------------------------------------- #
# Test helper
# --------------------------------------------------------------------------- #


def _override_path_for_tests(p: Path) -> None:
    global _QUEUE_PATH
    _QUEUE_PATH = p
