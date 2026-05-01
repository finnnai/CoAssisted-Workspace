# © 2026 CoAssisted Workspace. Licensed under MIT.
"""CRM-as-event-sink — per-contact event timeline.

Custom fields aren't enough — you need a time-ordered event log per person.
Same atomic-write JSON pattern as vendor_followups + dm_email_cache, keyed
by contact email.

Event shapes:
    {
        "id": str,            # uuid
        "ts": iso,             # when the event happened
        "kind": str,           # "email_sent", "email_received", "meeting", "intro_made", "intro_accepted", "vendor_invoice", ...
        "summary": str,        # one-line description
        "thread_id": str | None,
        "event_id": str | None,
        "data": dict,          # arbitrary
    }

Per-contact rollup is computed on read.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional


_STORE_PATH = Path(__file__).resolve().parent / "crm_events.json"


# Recognized event kinds — caller can use anything but these get convenience helpers.
KNOWN_KINDS = {
    "email_sent", "email_received", "email_substantive",
    "meeting", "phone_call", "chat",
    "intro_made", "intro_accepted",
    "vendor_invoice", "vendor_onboarded",
    "vip_alert",
}


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _load() -> dict[str, list[dict]]:
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(_STORE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, list[dict]]) -> None:
    fd, tmp = tempfile.mkstemp(
        prefix="crm_events.", suffix=".tmp", dir=str(_STORE_PATH.parent),
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


def _norm(email: str) -> str:
    return (email or "").lower().strip()


# --------------------------------------------------------------------------- #
# Append + read
# --------------------------------------------------------------------------- #


def append(
    email: str,
    kind: str,
    summary: str,
    *,
    ts: str | None = None,
    thread_id: str | None = None,
    event_id: str | None = None,
    data: Optional[dict] = None,
) -> dict:
    """Add an event to a contact's timeline. Returns the stored event dict."""
    if not email:
        raise ValueError("email required")
    if not kind:
        raise ValueError("kind required")
    e = _norm(email)
    rec = {
        "id": uuid.uuid4().hex[:12],
        "ts": ts or _now_iso(),
        "kind": kind,
        "summary": summary or "",
        "thread_id": thread_id,
        "event_id": event_id,
        "data": dict(data or {}),
    }
    store = _load()
    store.setdefault(e, []).append(rec)
    # Keep timeline sorted ascending by ts.
    store[e].sort(key=lambda x: x.get("ts", ""))
    _save(store)
    return dict(rec)


def get_timeline(email: str) -> list[dict]:
    """Return the full event list for a contact, oldest first."""
    return list(_load().get(_norm(email), []))


def get_recent(email: str, *, limit: int = 10) -> list[dict]:
    """Return up to `limit` most-recent events for a contact, newest first."""
    timeline = _load().get(_norm(email), [])
    return list(reversed(timeline[-limit:]))


def all_emails() -> list[str]:
    return sorted(_load().keys())


def remove_event(email: str, event_id: str) -> bool:
    e = _norm(email)
    store = _load()
    timeline = store.get(e, [])
    before = len(timeline)
    timeline[:] = [ev for ev in timeline if ev.get("id") != event_id]
    if len(timeline) == before:
        return False
    _save(store)
    return True


def clear_contact(email: str) -> int:
    e = _norm(email)
    store = _load()
    n = len(store.get(e, []))
    store.pop(e, None)
    _save(store)
    return n


def clear_all() -> int:
    store = _load()
    n = sum(len(v) for v in store.values())
    _save({})
    return n


# --------------------------------------------------------------------------- #
# Rollups + queries
# --------------------------------------------------------------------------- #


def last_event(email: str, kind: str | None = None) -> Optional[dict]:
    timeline = _load().get(_norm(email), [])
    if kind:
        timeline = [ev for ev in timeline if ev.get("kind") == kind]
    return dict(timeline[-1]) if timeline else None


def days_since_last_event(email: str, kind: str | None = None,
                          today: _dt.datetime | None = None) -> Optional[int]:
    last = last_event(email, kind=kind)
    if not last:
        return None
    try:
        ts = _dt.datetime.fromisoformat((last.get("ts") or "").replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None
    today = today or _dt.datetime.now(tz=ts.tzinfo)
    return (today - ts).days


def count_events(email: str, kind: str | None = None,
                 since_days: int | None = None,
                 today: _dt.datetime | None = None) -> int:
    timeline = _load().get(_norm(email), [])
    today = today or _dt.datetime.now().astimezone()
    n = 0
    for ev in timeline:
        if kind and ev.get("kind") != kind:
            continue
        if since_days is not None:
            try:
                ts = _dt.datetime.fromisoformat((ev.get("ts") or "").replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=_dt.timezone.utc)
                age_days = (today - ts).days
            except ValueError:
                continue
            if age_days > since_days:
                continue
        n += 1
    return n


def find_intro_acceptance(email_a: str, email_b: str,
                          *, within_days: int = 14,
                          today: _dt.datetime | None = None) -> bool:
    """Did A↔B exchange direct messages within `within_days` of an intro?
    Returns True if either party's timeline shows an email/chat with the other."""
    today = today or _dt.datetime.now().astimezone()
    cutoff = today - _dt.timedelta(days=within_days)
    for source_email, target_email in [(email_a, email_b), (email_b, email_a)]:
        timeline = _load().get(_norm(source_email), [])
        for ev in reversed(timeline):
            if ev.get("kind") not in {"email_sent", "email_received", "chat"}:
                continue
            try:
                ts = _dt.datetime.fromisoformat((ev.get("ts") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts < cutoff:
                break
            data = ev.get("data") or {}
            if (data.get("with_email") or "").lower() == _norm(target_email):
                return True
    return False


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #


def _override_path_for_tests(p: Path) -> None:
    global _STORE_PATH
    _STORE_PATH = p
