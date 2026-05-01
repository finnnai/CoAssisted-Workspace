# © 2026 CoAssisted Workspace. Licensed under MIT.
"""DM space ↔ email sidecar.

Why this exists
---------------
Google's People API resolves a Chat user resource (`users/<numeric>`) to an
email cleanly *within* your Workspace org. Cross-org users (subsidiaries,
external vendors) commonly fail that lookup silently — `people.get` returns
no `emailAddresses`, leaving us without an email for the AP-automation
auto-share path. The result: when Amanda at staffwizard.com submits a
receipt, the orchestrator can't grant her sheet access because it can't
figure out which email she has.

But every time we *initiate* a DM to a user via `chat_send_dm(email=...)`,
we already know the email — that's literally the input. We just throw the
mapping away after the API call returns the resolved space name. This
sidecar keeps that mapping so downstream orchestrators (receipt extractor,
ack composer, auto-share) can answer "what email is the other party in
this DM space?" without asking Google.

Storage
-------
~/Claude/google_workspace_mcp/dm_emails.json — atomic writes via tempfile +
os.replace, same pattern as `vendor_followups.py` and `merchant_cache.py`.

Schema
------
    {
        "spaces/AAAAxxxx": {
            "email": "amanda.miller@staffwizard.com",
            "first_seen": "2026-04-28T15:01:30-07:00",
            "last_seen":  "2026-04-28T17:42:11-07:00",
            "send_count": 3
        },
        ...
    }

Privacy
-------
This file contains internal/external email addresses you've DMed. Treat it
the same as your address book — never check into source control, exclude
from `make handoff` tarballs (already done via the secrets-exclusion list,
but `dm_emails.json` is added to that list explicitly here for safety).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path
from typing import Optional


_PROJECT_ROOT = Path(__file__).resolve().parent
_STORE_PATH = _PROJECT_ROOT / "dm_emails.json"


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
        prefix="dm_emails.", suffix=".json.tmp",
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


def record(space_name: str, email: str) -> None:
    """Capture (space_name → email) mapping. Idempotent — re-recording the
    same pair just bumps `last_seen` and `send_count`. Different email for
    the same space overwrites (DM space is 1:1 with the other party).
    """
    if not space_name or not email:
        return
    email = email.strip().lower()
    data = _load()
    rec = data.get(space_name) or {}
    if rec.get("email") == email:
        rec["last_seen"] = _now_iso()
        rec["send_count"] = int(rec.get("send_count", 0)) + 1
    else:
        rec = {
            "email": email,
            "first_seen": _now_iso(),
            "last_seen": _now_iso(),
            "send_count": 1,
        }
    data[space_name] = rec
    _save(data)


def lookup_by_space(space_name: Optional[str]) -> Optional[str]:
    """Return the recipient email for a DM space, or None if we don't know.

    Used by the receipt orchestrator's auto-share path as a fallback when
    People API can't resolve the chat sender's email (cross-org, etc.).
    """
    if not space_name:
        return None
    rec = _load().get(space_name)
    if not rec:
        return None
    return rec.get("email")


def lookup_space_by_email(email: Optional[str]) -> Optional[str]:
    """Reverse lookup — return the space_name we last DMed this email at,
    or None. Useful for the "send another note" flow where the user
    references a recipient by email rather than space ID.
    """
    if not email:
        return None
    target = email.strip().lower()
    for space_name, rec in _load().items():
        if rec.get("email") == target:
            return space_name
    return None


def all_known_dms() -> dict[str, dict]:
    """Read-only view of the full cache — for diagnostics + tests."""
    return dict(_load())


def clear() -> int:
    """Drop everything. Returns previous record count. (Admin/test use.)"""
    n = len(_load())
    _save({})
    return n


def _override_path_for_tests(p: Path) -> None:
    global _STORE_PATH
    _STORE_PATH = Path(p)
