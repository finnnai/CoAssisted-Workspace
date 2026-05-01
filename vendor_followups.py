# © 2026 CoAssisted Workspace. Licensed under MIT.
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

    P1-4: This is the COLD-START fallback. When per-vendor history is
    available (>= COLD_START_THRESHOLD recorded replies),
    _adaptive_email_wait_hours() overrides this with a value derived
    from the vendor's median reply latency.
    """
    if reminder_count >= len(EMAIL_REMINDER_HOURS_LADDER):
        return 10 ** 9  # effectively "never"
    return EMAIL_REMINDER_HOURS_LADDER[reminder_count]


def _adaptive_email_wait_hours(
    vendor_email: Optional[str],
    reminder_count: int,
) -> int:
    """P1-4: per-vendor wait time. Pulls the vendor's median reply
    latency from vendor_response_history.py and maps it to a tier.
    Falls back to the constant ladder when there's no history yet."""
    default = _email_wait_hours(reminder_count)
    if not vendor_email:
        return default
    try:
        import vendor_response_history as _vrh
        return _vrh.adaptive_wait_hours(vendor_email, default)
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# Day-of-week + holiday push (P1-4)
# --------------------------------------------------------------------------- #


_HOLIDAYS_PATH = _PROJECT_ROOT / "us_federal_holidays.json"
_HOLIDAYS_CACHE: Optional[set[str]] = None


def _load_holidays() -> set[str]:
    """Load the bundled US federal holiday set (2026-2030). Cached after
    first load. Returns an empty set if the file is missing — falls back
    to weekend-only push."""
    global _HOLIDAYS_CACHE
    if _HOLIDAYS_CACHE is not None:
        return _HOLIDAYS_CACHE
    out: set[str] = set()
    try:
        with _HOLIDAYS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():
            if k.startswith("_"):
                continue
            for d in v:
                out.add(d)
    except Exception:
        pass
    _HOLIDAYS_CACHE = out
    return out


def _is_business_day(date: _dt.date) -> bool:
    """True if date is Mon-Fri AND not a US federal holiday."""
    if date.weekday() >= 5:  # 5=Sat, 6=Sun
        return False
    if date.isoformat() in _load_holidays():
        return False
    return True


def _next_business_day_at_9am(reference: _dt.datetime) -> _dt.datetime:
    """Return the next business-day 9am local from `reference`. If
    reference itself falls on a non-business day, advance day-by-day
    until we hit a Mon-Fri non-holiday at 9am local."""
    candidate = reference
    # Always set to 9am local on the candidate date
    candidate = candidate.replace(hour=9, minute=0, second=0, microsecond=0)
    # If the original moment was already past 9am on a business day, stay
    # there; otherwise push to next business day at 9am.
    if reference > candidate and _is_business_day(reference.date()):
        return reference  # already past 9am on a business day → don't push
    while not _is_business_day(candidate.date()):
        candidate = candidate + _dt.timedelta(days=1)
    return candidate


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
        # P0-2: tracks the timestamp of the newest vendor reply we've already
        # processed, so the next sweep skips messages we already acted on.
        # Initially equal to request_sent_at — anything before this is "us".
        "latest_reply_ts": None,
        # P1-5: snooze + escalation trail.
        # `snoozed_until` is an ISO timestamp; while now < snoozed_until,
        # due_for_reminder() skips this entry. Cleared by unsnooze().
        # `events` accumulates timeline records (ASK / REMINDER tier N /
        # SNOOZED / UNSNOOZED / RESOLVED), each with a timestamp + payload.
        "snoozed_until": None,
        "events": [
            {
                "ts": _now_iso(),
                "action": "ASK",
                "channel": channel,
                "fields": list(fields_requested or []),
            },
        ],
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
        # P1-5: skip snoozed entries until their snoozed_until passes.
        snoozed = rec.get("snoozed_until")
        if snoozed:
            try:
                snz_dt = _dt.datetime.fromisoformat(snoozed)
                if snz_dt.tzinfo is None:
                    snz_dt = snz_dt.replace(tzinfo=_dt.timezone.utc)
                if now < snz_dt.astimezone():
                    continue
            except ValueError:
                pass  # bad ISO — fall through and treat as not snoozed
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
            # P1-4: per-vendor adaptive wait, falls back to the constant
            # ladder when the vendor has no history yet.
            wait_hours = _adaptive_email_wait_hours(
                rec.get("vendor_email"), rcount,
            )
        elapsed_hours = (now - last_dt).total_seconds() / 3600.0
        if elapsed_hours < wait_hours:
            continue
        # P1-4: push to next business day at 9am local if the moment we'd
        # send a reminder lands on a weekend or US federal holiday. Email
        # to a vendor at Sat 11am is noisy; better to wait until Mon 9am.
        if rec.get("channel") == "gmail":
            target = last_dt + _dt.timedelta(hours=wait_hours)
            scheduled = _next_business_day_at_9am(target)
            if now < scheduled:
                continue
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
    rec.setdefault("events", []).append({
        "ts": rec["last_reminder_at"],
        "action": "REMINDER",
        "tier": rec["reminder_count"],
    })
    data[content_key] = rec
    _save(data)
    return dict(rec)


def snooze(
    content_key: str,
    until_date: str,
    reason: Optional[str] = None,
) -> bool:
    """Pause reminders for this entry until until_date (ISO 8601).
    Records a SNOOZED event in the entry's timeline. Returns True if
    the entry exists and was updated, False otherwise."""
    if not content_key or not until_date:
        return False
    # Validate ISO format (will raise if malformed; caller should catch)
    try:
        _dt.datetime.fromisoformat(until_date)
    except ValueError:
        raise ValueError(f"until_date must be ISO 8601: {until_date!r}")
    data = _load()
    rec = data.get(content_key)
    if not rec or rec.get("resolved_at"):
        return False
    rec["snoozed_until"] = until_date
    rec.setdefault("events", []).append({
        "ts": _now_iso(),
        "action": "SNOOZED",
        "until": until_date,
        "reason": reason,
    })
    data[content_key] = rec
    _save(data)
    return True


def unsnooze(content_key: str) -> bool:
    """Clear snoozed_until on an entry. Returns True if the entry was
    snoozed and is now clear; False otherwise (no entry, or wasn't snoozed)."""
    if not content_key:
        return False
    data = _load()
    rec = data.get(content_key)
    if not rec or not rec.get("snoozed_until"):
        return False
    rec["snoozed_until"] = None
    rec.setdefault("events", []).append({
        "ts": _now_iso(),
        "action": "UNSNOOZED",
    })
    data[content_key] = rec
    _save(data)
    return True


def append_event(content_key: str, event: dict) -> bool:
    """Append an arbitrary event to an entry's timeline. Caller is
    responsible for the event shape; ts is set automatically if missing.
    Returns True on success, False if the entry doesn't exist."""
    if not content_key or not event:
        return False
    data = _load()
    rec = data.get(content_key)
    if not rec:
        return False
    ev = dict(event)
    ev.setdefault("ts", _now_iso())
    rec.setdefault("events", []).append(ev)
    data[content_key] = rec
    _save(data)
    return True


def get_trail(content_key: str) -> list[dict]:
    """Return the events timeline for an entry (oldest first).
    Empty list if the entry doesn't exist or has no events yet."""
    rec = _load().get(content_key)
    if not rec:
        return []
    return list(rec.get("events", []))


def update_latest_reply_ts(content_key: str, ts_iso: str) -> bool:
    """Mark which reply timestamp we last processed for an entry.

    Subsequent sweeps will skip messages older than this. Used by
    workflow_process_vendor_replies to dedup multi-message threads.
    Returns True if the entry exists and was updated, False otherwise.
    """
    if not content_key or not ts_iso:
        return False
    data = _load()
    if content_key not in data:
        return False
    data[content_key]["latest_reply_ts"] = ts_iso
    _save(data)
    return True


def mark_resolved(content_key: str) -> bool:
    """Vendor replied, fields filled in. Stamp resolved_at."""
    if not content_key:
        return False
    data = _load()
    rec = data.get(content_key)
    if not rec or rec.get("resolved_at"):
        return False
    rec["resolved_at"] = _now_iso()
    rec.setdefault("events", []).append({
        "ts": rec["resolved_at"],
        "action": "RESOLVED",
    })
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
