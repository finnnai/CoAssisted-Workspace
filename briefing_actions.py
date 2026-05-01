# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Briefing action token queue.

Each actionable button in the morning briefing is backed by a short token.
Clicking the button (or invoking workflow_briefing_execute_action with the
token) executes the planned action.

Token kinds (and the MCP tool each dispatches to):

    EMAIL TAB
      approve_send       → gmail send_email (or gmail.drafts.send)
      schedule_send      → vendor_followups.register_request kind=send_later
      mark_read          → gmail.modify_labels (remove UNREAD)
      mark_as_task       → tasks.tasks_create_task with link to thread

    MEETING TAB
      accept_meeting     → calendar.respond_to_event (yes)
      decline_meeting    → calendar.respond_to_event (no)
      suggest_new_time   → gmail compose with brand-voice rsvp_alternative

    TASK TAB
      complete_task      → tasks.tasks_complete_task
      ignore_task        → no-op (status=ignored)
      schedule_to_calendar → calendar.create_event from task title/notes

State persisted to briefing_actions.json (atomic write).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Optional


_STORE_PATH = Path(__file__).resolve().parent / "briefing_actions.json"


# Recognized kinds (used for validation + dispatch)
KNOWN_KINDS = {
    "approve_send", "schedule_send", "mark_read", "mark_as_task",
    "accept_meeting", "decline_meeting", "suggest_new_time",
    "complete_task", "ignore_task", "schedule_to_calendar",
}


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _load() -> dict[str, dict]:
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(_STORE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, dict]) -> None:
    fd, tmp = tempfile.mkstemp(
        prefix="briefing_actions.", suffix=".tmp", dir=str(_STORE_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _STORE_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat()


def _new_token() -> str:
    """8-char URL-safe token. Collision risk is negligible at our scale."""
    return secrets.token_urlsafe(6)[:8]


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


def enqueue(
    kind: str,
    payload: dict,
    *,
    label: str = "",
    ttl_hours: int = 24,
) -> str:
    """Register a planned action and return its token.

    Token is unique within the store; collision is detected and re-rolled.
    """
    if kind not in KNOWN_KINDS:
        raise ValueError(f"Unknown action kind: {kind!r}")
    data = _load()
    token = _new_token()
    while token in data:
        token = _new_token()
    expires = (_dt.datetime.now().astimezone()
               + _dt.timedelta(hours=ttl_hours)).isoformat()
    data[token] = {
        "token": token,
        "kind": kind,
        "label": label,
        "payload": dict(payload or {}),
        "status": "pending",
        "created_at": _now_iso(),
        "expires_at": expires,
        "executed_at": None,
        "result": None,
    }
    _save(data)
    return token


def get(token: str) -> Optional[dict]:
    return _load().get(token)


def list_pending(kind: Optional[str] = None) -> list[dict]:
    out = []
    for rec in _load().values():
        if rec.get("status") != "pending":
            continue
        if kind and rec.get("kind") != kind:
            continue
        out.append(dict(rec))
    out.sort(key=lambda r: r.get("created_at") or "")
    return out


def mark_executed(token: str, result: dict) -> Optional[dict]:
    data = _load()
    rec = data.get(token)
    if not rec or rec.get("status") != "pending":
        return None
    rec["status"] = "executed"
    rec["executed_at"] = _now_iso()
    rec["result"] = dict(result)
    _save(data)
    return dict(rec)


def mark_failed(token: str, error: str) -> Optional[dict]:
    data = _load()
    rec = data.get(token)
    if not rec or rec.get("status") != "pending":
        return None
    rec["status"] = "failed"
    rec["executed_at"] = _now_iso()
    rec["result"] = {"error": error}
    _save(data)
    return dict(rec)


def expire_old() -> int:
    """Mark expired pending tokens as expired. Returns count expired."""
    now = _dt.datetime.now().astimezone()
    n = 0
    data = _load()
    for rec in data.values():
        if rec.get("status") != "pending":
            continue
        try:
            exp = _dt.datetime.fromisoformat(rec.get("expires_at"))
        except (ValueError, TypeError):
            continue
        if now > exp:
            rec["status"] = "expired"
            n += 1
    if n:
        _save(data)
    return n


def clear_all() -> int:
    n = len(_load())
    _save({})
    return n


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #


def _override_path_for_tests(p: Path) -> None:
    global _STORE_PATH
    _STORE_PATH = p
