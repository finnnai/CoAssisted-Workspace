# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Watched-sheet schema — generalized configurable rules registry.

Generalizes the `project_registry.py` pattern so multiple rule families can
share the same plumbing:

  - "license":   tracked licenses + expirations
  - "retention": retention rules per tag/category
  - "recurring": recurring expense rules
  - "focus":     focus-time blocks
  - any caller-defined family

Each entry is keyed by family + slug + domain-specific fields. Same atomic
JSON sidecar pattern (watched_sheets.json) as vendor_followups + dm_email_cache.

The MCP wrapper (tools/watched_sheets.py) exposes register / list / update /
remove / lookup tools.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path
from typing import Optional


_STORE_PATH = Path(__file__).resolve().parent / "watched_sheets.json"


# Recognized rule families. Caller-defined families are also allowed but get
# no schema validation.
KNOWN_FAMILIES = {"license", "retention", "recurring", "focus", "deadline"}


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
        prefix="watched_sheets.", suffix=".tmp", dir=str(_STORE_PATH.parent),
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


def _make_key(family: str, slug: str) -> str:
    return f"{family}:{slug}"


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


def register(
    family: str,
    slug: str,
    *,
    fields: Optional[dict] = None,
    active: bool = True,
) -> dict:
    """Register a rule entry. Idempotent — re-registering updates."""
    if not family or not slug:
        raise ValueError("family + slug required")
    key = _make_key(family, slug)
    data = _load()
    existing = data.get(key, {})
    rec = {
        "family": family,
        "slug": slug,
        "fields": dict(fields or {}),
        "active": bool(active),
        "created_at": existing.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
    }
    data[key] = rec
    _save(data)
    return dict(rec)


def update_fields(family: str, slug: str, **fields) -> Optional[dict]:
    key = _make_key(family, slug)
    data = _load()
    rec = data.get(key)
    if not rec:
        return None
    rec["fields"].update(fields)
    rec["updated_at"] = _now_iso()
    _save(data)
    return dict(rec)


def get(family: str, slug: str) -> Optional[dict]:
    return _load().get(_make_key(family, slug))


def list_family(family: str, *, active_only: bool = False) -> list[dict]:
    out = []
    for rec in _load().values():
        if rec.get("family") != family:
            continue
        if active_only and not rec.get("active"):
            continue
        out.append(dict(rec))
    out.sort(key=lambda r: r.get("slug", ""))
    return out


def list_all(*, active_only: bool = False) -> list[dict]:
    out = []
    for rec in _load().values():
        if active_only and not rec.get("active"):
            continue
        out.append(dict(rec))
    out.sort(key=lambda r: (r.get("family", ""), r.get("slug", "")))
    return out


def remove(family: str, slug: str) -> bool:
    key = _make_key(family, slug)
    data = _load()
    if key not in data:
        return False
    del data[key]
    _save(data)
    return True


def deactivate(family: str, slug: str) -> Optional[dict]:
    key = _make_key(family, slug)
    data = _load()
    rec = data.get(key)
    if not rec:
        return None
    rec["active"] = False
    rec["updated_at"] = _now_iso()
    _save(data)
    return dict(rec)


def clear(family: Optional[str] = None) -> int:
    """Remove all entries (or all in one family). Returns count dropped."""
    data = _load()
    if family is None:
        n = len(data)
        _save({})
        return n
    keep = {k: v for k, v in data.items() if v.get("family") != family}
    n = len(data) - len(keep)
    _save(keep)
    return n


# --------------------------------------------------------------------------- #
# Convenience: licenses with expiration logic
# --------------------------------------------------------------------------- #


def licenses_expiring(window_days: int = 90,
                      today: _dt.date | None = None) -> list[dict]:
    """Return active licenses whose `expires_at` falls within window_days from today.

    Each license entry stores fields={'expires_at': 'YYYY-MM-DD', 'jurisdiction': str, ...}.
    """
    today = today or _dt.date.today()
    out = []
    for rec in list_family("license", active_only=True):
        expires_str = (rec.get("fields") or {}).get("expires_at")
        if not expires_str:
            continue
        try:
            expires = _dt.date.fromisoformat(expires_str)
        except ValueError:
            continue
        days_left = (expires - today).days
        if days_left > window_days:
            continue
        out.append({**rec, "days_until_expiry": days_left})
    out.sort(key=lambda x: x["days_until_expiry"])
    return out


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #


def _override_path_for_tests(p: Path) -> None:
    global _STORE_PATH
    _STORE_PATH = p
