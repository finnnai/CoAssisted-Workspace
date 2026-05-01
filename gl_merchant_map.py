# © 2026 CoAssisted Workspace. Licensed under MIT.
"""GL merchant map — operator-confirmed merchant→GL account learning store.

Tier 0 of the GL classifier ladder (`gl_classifier.py`). When a transaction
comes in we check this store FIRST, before any MCC table lookup or
JE-trained matcher. A hit is HIGH confidence by definition — an operator
confirmed this exact merchant maps to this exact GL account, on a prior run.

Storage:
    ~/Developer/google_workspace_mcp/gl_merchant_map.json (gitignored).
    Atomic writes via tempfile + os.replace — partial writes can't happen
    even if the process is killed mid-update.

Cache key:
    Composite (normalized_merchant_name, cardholder_email_or_None).
    The same vendor can post to different GL accounts depending on which
    cost center the cardholder belongs to — e.g., AMAZON for an admin
    cardholder might be 62200:Supplies & Equipment, but for an operations
    cardholder might be 52200:Supplies & Equipment - COS. Keying on
    cardholder email lets us learn that distinction without conflating.

    cardholder_email=None entries are the global default — used when no
    cardholder context is available, or to override every cardholder.

Record shape (one entry per (merchant, cardholder) pair):
    {
        "merchant_display_name": "Amazon",
        "cardholder_email":      "michael.vetre@xenture.com" | None,
        "gl_account":            "62300:IT Expenses",
        "source":                "operator" | "training" | "import",
        "first_seen":            "2026-05-01T22:14:00-07:00",
        "last_seen":             "2026-05-01T22:14:00-07:00",
        "hit_count":             1,
        "history": [
            {"source": "training", "iso": "...", "from_je_id": "JE-3935"},
            {"source": "operator", "iso": "...", "user": "josh@..."},
        ],
    }

Operations:
    lookup(merchant, cardholder_email=None) -> Optional[str]
        Return the GL account if a hit, else None. Falls back to the
        cardholder=None entry if no per-cardholder match.

    learn(merchant, gl_account, source, ...) -> None
        Upsert with provenance. Operator overrides win over training
        seed entries.

    forget(merchant, cardholder_email=None) -> bool
        Drop one entry. Used when an operator marks a learned mapping
        as wrong.

    list_all(...) -> list[dict]
        Inventory for review-queue UIs.

    stats() -> dict
        Hit-rate, total entries by source, top hits.

    clear() -> int
        Nuke (for tests + admin reset). Returns number of entries removed.

This module is the ONLY writer to gl_merchant_map.json. The classifier
calls into here on every transaction; the AP review queue writes here
when an operator approves a classification.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Optional


# =============================================================================
# Storage paths + constants
# =============================================================================

_PROJECT_ROOT = Path(__file__).resolve().parent
_MAP_PATH = _PROJECT_ROOT / "gl_merchant_map.json"

# Sources we recognize. Order matters for `learn()` precedence — operator
# overrides win even if a training entry already exists.
_SOURCE_PRECEDENCE = {
    "operator": 3,   # human approval / explicit correction
    "import":   2,   # bulk import from prior systems
    "training": 1,   # auto-derived from JE history
}


# =============================================================================
# Internal helpers
# =============================================================================

def _normalize(name: str) -> str:
    """Lowercase + strip punctuation + collapse whitespace.

    Same intent as receipts._normalize_merchant_name but kept independent
    here to avoid the circular-import gymnastics merchant_cache plays.

    Examples:
        "AMAZON MARKEPLACE NA  PA"  → "amazon markeplace na pa"
        "Amazon, Inc."              → "amazon"
        "Pirate Ship"               → "pirate ship"
        "CHEVRON 0090562"           → "chevron"  (drop store-id digits)
    """
    if not name:
        return ""
    s = name.lower().strip()
    # Drop trailing store-ID digits (common in fuel/retail merchant names).
    s = re.sub(r"\s+\d{4,}\b", "", s)
    # Drop common corporate suffixes.
    s = re.sub(r"\b(inc|llc|corp|corporation|ltd|pbc|co)\.?$", "", s).strip(", ")
    # Collapse internal whitespace.
    s = re.sub(r"\s+", " ", s)
    # Drop punctuation except spaces and digits.
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return s.strip()


def _key(merchant_name: str, cardholder_email: Optional[str]) -> Optional[str]:
    """Build the composite store key. Returns None on empty merchant."""
    norm = _normalize(merchant_name)
    if not norm:
        return None
    if cardholder_email:
        return f"{norm}|{cardholder_email.strip().lower()}"
    return f"{norm}|"  # trailing pipe so we can tell global from per-cardholder


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _load() -> dict[str, dict]:
    """Read the JSON store off disk. Empty dict if missing or unparseable."""
    if not _MAP_PATH.exists():
        return {}
    try:
        with _MAP_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        # Corrupt store → ignore. Better to re-learn than crash a posting.
        return {}


def _save(data: dict[str, dict]) -> None:
    """Atomic write — tempfile + os.replace."""
    _MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix="gl_merchant_map.", suffix=".json.tmp",
        dir=str(_MAP_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_path, _MAP_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# =============================================================================
# Public API
# =============================================================================

def lookup(
    merchant_name: str,
    cardholder_email: Optional[str] = None,
) -> Optional[str]:
    """Return the GL account learned for this merchant, or None.

    Lookup order:
        1. (merchant, cardholder_email) — per-cardholder override
        2. (merchant, None) — global default for the merchant

    On a hit, hit_count and last_seen advance. Caller does not need to
    record anything — this function handles the bookkeeping.
    """
    if not merchant_name:
        return None
    data = _load()

    # Try per-cardholder first.
    if cardholder_email:
        key_specific = _key(merchant_name, cardholder_email)
        if key_specific and key_specific in data:
            data[key_specific]["hit_count"] = (
                int(data[key_specific].get("hit_count", 0)) + 1
            )
            data[key_specific]["last_seen"] = _now_iso()
            _save(data)
            return str(data[key_specific].get("gl_account", "")) or None

    # Fall back to global.
    key_global = _key(merchant_name, None)
    if key_global and key_global in data:
        data[key_global]["hit_count"] = (
            int(data[key_global].get("hit_count", 0)) + 1
        )
        data[key_global]["last_seen"] = _now_iso()
        _save(data)
        return str(data[key_global].get("gl_account", "")) or None

    return None


def learn(
    merchant_name: str,
    gl_account: str,
    *,
    source: str = "operator",
    cardholder_email: Optional[str] = None,
    user: Optional[str] = None,
    je_id: Optional[str] = None,
) -> None:
    """Upsert a (merchant, cardholder) → gl_account mapping.

    Precedence rules:
        - operator > import > training
        - A higher-precedence source can ALWAYS overwrite a lower one.
        - A lower-precedence source CANNOT overwrite a higher one.
          (e.g., training data won't clobber an operator confirmation)
        - Same-precedence overwrites — operator can change their mind.

    Each call appends to history (capped at 5 most recent events).

    Args:
        merchant_name: as captured from the transaction
        gl_account: e.g. "62300:IT Expenses"
        source: "operator" | "import" | "training" — controls precedence
        cardholder_email: optional, for per-cardholder override
        user: who made the change (for operator events)
        je_id: source JE id (for training events)
    """
    if not merchant_name or not gl_account:
        return
    if source not in _SOURCE_PRECEDENCE:
        raise ValueError(
            f"Unknown source {source!r}; expected one of "
            f"{sorted(_SOURCE_PRECEDENCE)}"
        )

    key = _key(merchant_name, cardholder_email)
    if not key:
        return

    data = _load()
    now = _now_iso()
    existing = data.get(key)

    # Precedence guard.
    if existing:
        existing_rank = _SOURCE_PRECEDENCE.get(
            existing.get("source", "training"), 0
        )
        new_rank = _SOURCE_PRECEDENCE[source]
        if new_rank < existing_rank:
            # Lower-precedence write attempt — ignore silently. (We don't
            # want training-pass-2 noise to overwrite operator decisions.)
            return

    # Build the history event.
    event: dict = {"source": source, "iso": now}
    if user:
        event["user"] = user
    if je_id:
        event["from_je_id"] = je_id

    if existing:
        history = list(existing.get("history") or [])
        history.append(event)
        history = history[-5:]  # keep last 5
        record = {
            **existing,
            "gl_account": gl_account,
            "source": source,
            "last_seen": now,
            "history": history,
            "hit_count": int(existing.get("hit_count", 0)),
        }
    else:
        record = {
            "merchant_display_name": merchant_name.strip(),
            "cardholder_email": cardholder_email,
            "gl_account": gl_account,
            "source": source,
            "first_seen": now,
            "last_seen": now,
            "hit_count": 0,
            "history": [event],
        }
    data[key] = record
    _save(data)


def forget(
    merchant_name: str,
    cardholder_email: Optional[str] = None,
) -> bool:
    """Drop one entry. Returns True if removed, False if not found."""
    key = _key(merchant_name, cardholder_email)
    if not key:
        return False
    data = _load()
    if key not in data:
        return False
    del data[key]
    _save(data)
    return True


def list_all(
    *,
    source: Optional[str] = None,
    cardholder_email: Optional[str] = None,
) -> list[dict]:
    """Return all entries, optionally filtered by source or cardholder.

    Records are returned as defensive copies — callers can mutate freely
    without affecting the on-disk store.
    """
    data = _load()
    out = []
    for record in data.values():
        if source and record.get("source") != source:
            continue
        if (
            cardholder_email is not None
            and record.get("cardholder_email") != cardholder_email
        ):
            continue
        out.append(dict(record))
    out.sort(key=lambda r: int(r.get("hit_count", 0)), reverse=True)
    return out


def stats() -> dict:
    """Summary stats: total entries, by source, by cardholder, top merchants."""
    data = _load()
    by_source: dict[str, int] = {}
    by_cardholder: dict[str, int] = {}
    total_hits = 0
    for record in data.values():
        src = record.get("source", "unknown")
        by_source[src] = by_source.get(src, 0) + 1
        ch = record.get("cardholder_email") or "(global)"
        by_cardholder[ch] = by_cardholder.get(ch, 0) + 1
        total_hits += int(record.get("hit_count", 0))
    top = sorted(
        data.values(),
        key=lambda r: int(r.get("hit_count", 0)),
        reverse=True,
    )[:5]
    return {
        "total_entries": len(data),
        "by_source": by_source,
        "by_cardholder": by_cardholder,
        "total_hits": total_hits,
        "top_5_by_hits": [
            {
                "merchant": r.get("merchant_display_name"),
                "cardholder": r.get("cardholder_email"),
                "gl_account": r.get("gl_account"),
                "hits": int(r.get("hit_count", 0)),
            }
            for r in top
        ],
    }


def clear() -> int:
    """Remove all entries. Returns the count removed. Use only in tests/admin."""
    data = _load()
    n = len(data)
    if n:
        _save({})
    return n
