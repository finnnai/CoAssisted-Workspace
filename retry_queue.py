# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""Retry queue with exponential backoff (AP-4 partial — v0.9.1).

When the receipt or invoice extractor fails on a payload (LLM rate-limited,
attachment download timed out, malformed PDF), v0.7-0.8 raised the error
back to the operator and dropped the inbound. v0.9.1 ships a delayed-retry
queue: the failed payload lands here with a scheduled next-attempt time,
and a periodic sweep retries it on a 1m / 5m / 30m / 4h / 24h ladder before
escalating to an operator alert.

This is the in-memory + JSON-persisted half of AP-4. The Pub/Sub watcher
half (Gmail push notifications) is environment work that lands in v0.9.2
once the GCP topic + subscription are provisioned.

Schedule (max_attempts=5):
    Attempt 1 → 1 minute later
    Attempt 2 → 5 minutes later
    Attempt 3 → 30 minutes later
    Attempt 4 → 4 hours later
    Attempt 5 → 24 hours later
    Attempt 6 → operator alert (no further auto-retry)

Public surface
--------------
    enqueue(payload, kind, *, error=None, attempts=0) -> dict
        Add a failed extraction to the queue. `kind` is a free-text label
        ('receipt' | 'invoice' | 'card_statement' | 'labor_report').

    due(now=None) -> list[dict]
        Items whose next_attempt_at <= now. The retry runner pulls these.

    mark_attempted(item_id, *, succeeded, error=None) -> dict
        Update an item after a retry. Successful items are marked
        'completed'. Failed items get re-scheduled if attempts remain,
        otherwise flagged 'escalated' for operator alert.

    list_all(*, status=None) -> list[dict]
        Operator visibility.

    forget(item_id) -> bool
        Drop an item permanently — operator chose to discard.

    stats() -> dict
        Summary by status.

State file (gitignored): `retry_queue.json` next to this module.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Optional


_PROJECT_ROOT = Path(__file__).resolve().parent
_QUEUE_PATH = _PROJECT_ROOT / "retry_queue.json"


# Backoff schedule. Index = attempts already made; value = minutes to wait
# before the next attempt. After the last entry, the item escalates.
BACKOFF_MINUTES = [1, 5, 30, 4 * 60, 24 * 60]
MAX_ATTEMPTS = len(BACKOFF_MINUTES)


# --------------------------------------------------------------------------- #
# Storage primitives
# --------------------------------------------------------------------------- #


def _now() -> _dt.datetime:
    return _dt.datetime.now().astimezone()


def _iso(dt: _dt.datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _atomic_write(data: dict) -> None:
    _QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix="retry_queue.", suffix=".json.tmp",
        dir=str(_QUEUE_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, _QUEUE_PATH)
    except (OSError, TypeError, ValueError):
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load() -> dict:
    if not _QUEUE_PATH.exists():
        return {}
    try:
        return json.loads(_QUEUE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _short_id() -> str:
    return "RETRY-" + secrets.token_hex(4).upper()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def enqueue(
    payload: dict,
    kind: str,
    *,
    error: Optional[str] = None,
    attempts: int = 0,
    note: Optional[str] = None,
) -> dict:
    """Add a failed extraction to the queue. Returns the queue entry."""
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")
    if not kind or not str(kind).strip():
        raise ValueError("kind is required")

    data = _load()
    item_id = _short_id()
    now = _now()
    next_at = now + _dt.timedelta(minutes=BACKOFF_MINUTES[min(attempts, MAX_ATTEMPTS - 1)])

    entry = {
        "item_id": item_id,
        "kind": str(kind).strip(),
        "payload": payload,
        "attempts": int(attempts),
        "max_attempts": MAX_ATTEMPTS,
        "status": "pending",
        "first_enqueued_at": _iso(now),
        "last_attempted_at": None,
        "next_attempt_at": _iso(next_at),
        "last_error": (error or "").strip() or None,
        "note": (note or "").strip() or None,
        "history": [],
    }
    data[item_id] = entry
    _atomic_write(data)
    return entry


def due(now: Optional[_dt.datetime] = None) -> list[dict]:
    """Items whose next_attempt_at <= now AND status == 'pending'.
    Sorted oldest first.
    """
    now = now or _now()
    data = _load()
    out = []
    for entry in data.values():
        if entry.get("status") != "pending":
            continue
        try:
            scheduled = _dt.datetime.fromisoformat(entry.get("next_attempt_at") or "")
        except ValueError:
            continue
        if scheduled <= now:
            out.append(entry)
    out.sort(key=lambda e: e.get("next_attempt_at") or "")
    return out


def mark_attempted(
    item_id: str,
    *,
    succeeded: bool,
    error: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """Update the item after a retry attempt. Returns the updated entry.

    Successful retries → status='completed'.
    Failed retries with attempts left → status='pending', next_attempt_at
        scheduled per backoff ladder.
    Failed retries past max_attempts → status='escalated' (operator action
        required).
    """
    data = _load()
    entry = data.get(item_id)
    if not entry:
        raise KeyError(f"unknown item_id: {item_id}")
    now = _now()

    entry["last_attempted_at"] = _iso(now)
    entry["attempts"] = int(entry.get("attempts", 0)) + 1
    history_event = {
        "at": _iso(now),
        "succeeded": bool(succeeded),
        "error": (error or "").strip() or None,
        "note": (note or "").strip() or None,
    }
    entry.setdefault("history", []).append(history_event)

    if succeeded:
        entry["status"] = "completed"
        entry["completed_at"] = _iso(now)
        entry["last_error"] = None
    elif entry["attempts"] >= MAX_ATTEMPTS:
        entry["status"] = "escalated"
        entry["escalated_at"] = _iso(now)
        entry["last_error"] = error or entry.get("last_error")
    else:
        entry["status"] = "pending"
        idx = min(entry["attempts"], MAX_ATTEMPTS - 1)
        entry["next_attempt_at"] = _iso(now + _dt.timedelta(minutes=BACKOFF_MINUTES[idx]))
        entry["last_error"] = error or entry.get("last_error")

    data[item_id] = entry
    _atomic_write(data)
    return entry


def forget(item_id: str) -> bool:
    """Permanently drop an item. Returns True if removed."""
    data = _load()
    if item_id not in data:
        return False
    del data[item_id]
    _atomic_write(data)
    return True


def list_all(*, status: Optional[str] = None) -> list[dict]:
    data = _load()
    rows = list(data.values())
    if status:
        rows = [e for e in rows if e.get("status") == status]
    rows.sort(key=lambda e: e.get("first_enqueued_at") or "", reverse=True)
    return rows


def stats() -> dict:
    data = _load()
    counts = {"pending": 0, "completed": 0, "escalated": 0, "total": 0}
    for entry in data.values():
        counts["total"] += 1
        s = entry.get("status", "pending")
        counts[s] = counts.get(s, 0) + 1
    counts["due_now"] = len(due())
    return counts
