# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
# See LICENSE file for terms.
"""Vendor info-request tracker — sidecar store for outstanding follow-ups.

When the invoice orchestrator's quality guard fires (missing invoice number,
missing total, etc.), instead of silently parking the row in 'Needs Review',
we send a brand-voiced reply to the vendor on the original channel asking for
the missing fields line-by-line. This module tracks those outstanding asks.

Storage:
    ~/Claude/google_workspace_mcp/awaiting_info.json — atomic writes via
    tempfile + os.replace, same pattern as merchant_cache.py.

Record shape (one entry per content_key — the row's natural ID):
    {
        "content_key":      "acme|inv-1|10000",         # primary key
        "thread_id":        "Gmail thread_id" or
                            "spaces/AAQA..." (chat space),
        "channel":          "gmail" | "chat",
        "vendor_email":     "billing@vendor.io"  | None,
        "vendor_name":      "Acme Vendor Inc",
        "fields_requested": ["invoice_number", "total", "due_date"],
        "request_sent_at":  "2026-04-27T...",
        "reminder_count":   0,
        "last_reminder_at": null,
        "sheet_id":         "1AbC...",                  # parking sheet
        "row_number":       7,                          # 1-indexed including header
        "project_code":     "ALPHA" | None,
        "chat_thread_name": "spaces/AAQA.../threads/<id>" | null,
                                                          # chat-only:
                                                          # threads reminders
                                                          # back into the same
                                                          # conversation as
                                                          # the original
                                                          # receipt
        "resolved_at":      null,                       # set when reply lands
    }

Cadence:
  - Chat channels: no wait between reminders (real-time interaction).
  - Email channels: per-stage ladder so we ramp up slowly:
      - Initial ask:      sent immediately when the guard fires (orchestrator)
      - 1st reminder:     24h after the initial ask
      - 2nd reminder:     48h after the 1st reminder
      - 3rd reminder:     48h after the 2nd reminder
    See EMAIL_REMINDER_HOURS_LADDER below.
  - Hard cap of 3 reminders for either channel (4 total messages
    counting the initial ask) — after that the row stays in AWAITING_INFO
    and the user has to nudge manually or re-extract.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path
from typing import Optional


_PROJECT_ROOT = Path(__file__).resolve().parent
_STORE_PATH = _PROJECT_ROOT / "awaiting_info.json"

# Cadence config — see module docstring.
#
# EMAIL_REMINDER_HOURS_LADDER[i] = wait hours BEFORE sending nudge i+1.
# Index 0 is the wait before the 1st reminder (i.e. after the initial ask),
# index 1 is the wait before the 2nd, etc.
#
# Tuple length must equal MAX_REMINDERS.
EMAIL_REMINDER_HOURS_LADDER: tuple[int, ...] = (24, 48, 48)
CHAT_REMINDER_HOURS = 0
MAX_REMINDERS = len(EMAIL_REMINDER_HOURS_LADDER)

# Backwards-compat: a few callers still read EMAIL_REMINDER_HOURS as a flat
# default. Point it at the first stage so existing callers don't break.
EMAIL_REMINDER_HOURS = EMAIL_REMINDER_HOURS_LADDER[0]


def _email_wait_hours(reminder_count: int) -> int:
    """Wait hours before sending the next email reminder.

    `reminder_count` is the count BEFORE we send (i.e. how many reminders
    have already gone out). When reminder_count is 0, we return the wait
    before the 1st reminder (24h). When it equals MAX_REMINDERS, no more
    reminders should fire — we return a very large number as a guard,
    though due_for_reminder() also short-circuits via the cap check.
    """
    if reminder_count >= len(EMAIL_REMINDER_HOURS_LADDER):
        return 10 ** 9  # effectively "never"
    return EMAIL_REMINDER_HOURS_LADDER[reminder_count]


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _load() -> dict[str, dict]:
    if not _STORE_PATH.exists():
        return {}
    try:
        with _STORE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, dict]) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix="awaiting_info.", suffix=".json.tmp",
        dir=str(_STORE_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_path, _STORE_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def register_request(
    content_key: str,
    *,
    thread_id: str,
    channel: str,
    vendor_email: Optional[str],
    vendor_name: Optional[str],
    fields_requested: list[str],
    sheet_id: str,
    row_number: int,
    project_code: Optional[str] = None,
    chat_thread_name: Optional[str] = None,
) -> dict:
    """Record a fresh outstanding info request. Caller is responsible for
    actually sending the message — this module just tracks state.

    Re-registering the same content_key resets the reminder counter (treats
    it as a fresh ask) — useful when the row was bounced from one project to
    another and needs a re-prompt.

    `chat_thread_name` (chat channel only) is the Google Chat thread
    resource (e.g. "spaces/AAQA.../threads/<id>") — when present, all
    reminders thread back to the same conversation so the AP back-and-forth
    stays visually attached to the original receipt message instead of
    showing up as fresh top-level messages.
    """
    if not content_key:
        raise ValueError("content_key is required")
    if channel not in ("gmail", "chat"):
        raise ValueError(f"channel must be 'gmail' or 'chat', got {channel!r}")
    data = _load()
    rec = {
        "content_key": content_key,
        "thread_id": thread_id,
        "channel": channel,
        "vendor_email": vendor_email,
        "vendor_name": vendor_name,
        "fields_requested": list(fields_requested or []),
        "request_sent_at": _now_iso(),
        "reminder_count": 0,
        "last_reminder_at": None,
        "sheet_id": sheet_id,
        "row_number": int(row_number),
        "project_code": project_code,
        "chat_thread_name": chat_thread_name,
        "resolved_at": None,
    }
    data[content_key] = rec
    _save(data)
    return dict(rec)


def get(content_key: str) -> Optional[dict]:
    if not content_key:
        return None
    data = _load()
    rec = data.get(content_key)
    return dict(rec) if rec else None


def list_open(*, channel: Optional[str] = None) -> list[dict]:
    """All outstanding (un-resolved) requests, optionally filtered by channel."""
    rows = []
    for rec in _load().values():
        if rec.get("resolved_at"):
            continue
        if channel and rec.get("channel") != channel:
            continue
        rows.append(dict(rec))
    rows.sort(key=lambda r: r.get("request_sent_at") or "")
    return rows


def due_for_reminder() -> list[dict]:
    """Outstanding requests ready for a nudge.

      - Email: per-stage ladder via _email_wait_hours(reminder_count).
               1st reminder: 24h after initial ask.
               2nd reminder: 48h after 1st reminder.
               3rd reminder: 48h after 2nd reminder.
      - Chat:  no wait (CHAT_REMINDER_HOURS = 0) — real-time channel.
      - Cap:   reminder_count < MAX_REMINDERS for either channel.

    'Last touch' = last_reminder_at if set, otherwise request_sent_at.
    """
    out: list[dict] = []
    now = _dt.datetime.now().astimezone()
    for rec in _load().values():
        if rec.get("resolved_at"):
            continue
        rcount = int(rec.get("reminder_count", 0))
        if rcount >= MAX_REMINDERS:
            continue

        last_touch = rec.get("last_reminder_at") or rec.get("request_sent_at")
        if not last_touch:
            out.append(dict(rec))
            continue
        try:
            last_dt = _dt.datetime.fromisoformat(last_touch)
        except ValueError:
            out.append(dict(rec))
            continue

        if rec.get("channel") == "chat":
            wait_hours = CHAT_REMINDER_HOURS
        else:
            wait_hours = _email_wait_hours(rcount)
        elapsed_hours = (now - last_dt).total_seconds() / 3600.0
        if elapsed_hours >= wait_hours:
            out.append(dict(rec))
    out.sort(key=lambda r: r.get("request_sent_at") or "")
    return out


def record_reminder(content_key: str) -> Optional[dict]:
    """Bump reminder_count and set last_reminder_at. Returns the updated
    record, or None if content_key isn't tracked or is already capped."""
    if not content_key:
        return None
    data = _load()
    rec = data.get(content_key)
    if not rec or rec.get("resolved_at"):
        return None
    if int(rec.get("reminder_count", 0)) >= MAX_REMINDERS:
        return None
    rec["reminder_count"] = int(rec.get("reminder_count", 0)) + 1
    rec["last_reminder_at"] = _now_iso()
    data[content_key] = rec
    _save(data)
    return dict(rec)


def mark_resolved(content_key: str) -> bool:
    """Vendor replied, fields filled in. Stamp resolved_at."""
    if not content_key:
        return False
    data = _load()
    rec = data.get(content_key)
    if not rec or rec.get("resolved_at"):
        return False
    rec["resolved_at"] = _now_iso()
    data[content_key] = rec
    _save(data)
    return True


def forget(content_key: str) -> bool:
    """Drop an entry entirely (admin/test use)."""
    if not content_key:
        return False
    data = _load()
    if content_key not in data:
        return False
    del data[content_key]
    _save(data)
    return True


def clear() -> int:
    n = len(_load())
    _save({})
    return n


def _override_path_for_tests(p: Path) -> None:
    global _STORE_PATH
    _STORE_PATH = Path(p)
