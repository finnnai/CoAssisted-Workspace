# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""Persistent merchant cache — Tier 0 of the receipt enrichment ladder.

Stores everything we've learned about a merchant from prior Maps verifications,
web searches, or manual user corrections. On the next receipt from the same
merchant, we apply the cached category + business type and skip the paid
enrichment tiers.

Storage:
    ~/Claude/google_workspace_mcp/merchants.json (relative to project root).
    Atomic writes via tempfile + rename — no partial writes if the process
    is killed mid-update.

Cache key:
    Normalized merchant name (lowercase, stripped of "The " prefix and
    Inc/LLC/PBC suffix). Defined in receipts._normalize_merchant_name so
    "Anthropic", "anthropic", "Anthropic, PBC" all collapse to one entry.

Record shape (one entry per normalized merchant):
    {
        "display_name":      "Anthropic",
        "business_type":     "AI / SaaS",
        "category":          "Office — Software & SaaS",
        "source":            "web_search" | "maps" | "manual_correction",
        "confidence":        0.85,
        "location":          "San Francisco, CA",     # optional, for context
        "first_seen":        "2026-04-26T18:32:00-07:00",
        "last_seen":         "2026-09-15T09:14:33-07:00",
        "hit_count":         12,
        "history": [                                  # last 5 source events
            {"source": "web_search", "iso": "..."},
            {"source": "manual_correction", "iso": "..."},
        ],
    }

Operations:
    lookup(name) -> dict | None     - cache check, with TTL filter
    update(...)                     - upsert from a successful enrichment
    apply_correction(...)           - upsert from manual user override
    forget(name) -> bool            - drop one entry
    list_all(...) -> list           - inventory for the workflow_list tool
    stats() -> dict                 - hit-rate / size summary
    clear() -> int                  - nuke (for tests + admin)

Concurrency note: file is read/written on every op. The MCP server is
single-threaded so contention isn't a concern in normal use; if multiple
processes run scans simultaneously the last writer wins.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path
from typing import Optional


# Re-imported lazily to avoid a circular import (receipts → merchant_cache
# during enrichment).
def _normalize(name: str) -> str:
    from receipts import _normalize_merchant_name
    return _normalize_merchant_name(name)


# Resolve cache file path relative to this module so it works regardless
# of cwd at runtime. Project root = parent of this file.
_PROJECT_ROOT = Path(__file__).resolve().parent
_CACHE_PATH = _PROJECT_ROOT / "merchants.json"

# Re-verify entries this old. One year is conservative — businesses change
# (rebrand, change category, close). Longer than a year and we risk acting
# on stale data; shorter and we churn paid searches needlessly.
_TTL_DAYS = 365

# Boosts for cache hits. Same as the original tier that captured the entry,
# since a cache hit is "remembering a real verification" — not a new guess.
_CACHE_HIT_BOOST_MAPS = 0.20         # mirrors _MAPS_BOOST in receipts
_CACHE_HIT_BOOST_WEBSEARCH = 0.15    # mirrors _WEBSEARCH_BOOST
_CACHE_HIT_BOOST_MANUAL = 0.25       # manual is the strongest signal


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _load() -> dict[str, dict]:
    """Read the JSON cache off disk. Returns {} if missing or unparseable."""
    if not _CACHE_PATH.exists():
        return {}
    try:
        with _CACHE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        # Corrupt cache → ignore. Better to re-learn than crash the orchestrator.
        return {}


def _save(data: dict[str, dict]) -> None:
    """Atomic write — tempfile + os.replace so we never leave a partial file."""
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix="merchants.", suffix=".json.tmp",
        dir=str(_CACHE_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_path, _CACHE_PATH)
    except Exception:
        # Best-effort cleanup if rename failed
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _is_expired(record: dict) -> bool:
    """True if the entry hasn't been seen within TTL window."""
    last_seen_str = record.get("last_seen") or record.get("first_seen")
    if not last_seen_str:
        return True
    try:
        last_seen = _dt.datetime.fromisoformat(last_seen_str)
    except ValueError:
        return True
    age = _dt.datetime.now().astimezone() - last_seen
    return age.days > _TTL_DAYS


def lookup(merchant_name: str) -> Optional[dict]:
    """Return the cached record for a merchant, or None if absent/expired.

    On a cache hit, the caller is responsible for recording usage via
    `record_hit()` so hit_count and last_seen advance.
    """
    if not merchant_name:
        return None
    key = _normalize(merchant_name)
    if not key:
        return None
    data = _load()
    record = data.get(key)
    if not record or _is_expired(record):
        return None
    return dict(record)  # defensive copy so callers can't mutate via reference


def record_hit(merchant_name: str) -> None:
    """Increment hit_count and update last_seen on a cache hit."""
    if not merchant_name:
        return
    key = _normalize(merchant_name)
    data = _load()
    if key not in data:
        return
    data[key]["hit_count"] = int(data[key].get("hit_count", 0)) + 1
    data[key]["last_seen"] = _now_iso()
    _save(data)


def boost_for(record: dict) -> float:
    """Return the confidence boost a cache hit should grant, based on the
    source that originally captured it."""
    src = (record.get("source") or "").lower()
    if src == "manual_correction":
        return _CACHE_HIT_BOOST_MANUAL
    if src == "web_search":
        return _CACHE_HIT_BOOST_WEBSEARCH
    return _CACHE_HIT_BOOST_MAPS  # default — Maps is the floor


def update(
    merchant_name: str,
    *,
    display_name: str | None = None,
    business_type: str | None = None,
    category: str | None = None,
    source: str,
    confidence: float | None = None,
    location: str | None = None,
) -> dict:
    """Upsert a record after a successful enrichment.

    `source` should be one of: "maps", "web_search", "manual_correction".
    Manual corrections always overwrite (highest authority); other sources
    only fill empty fields on an existing record.
    """
    if not merchant_name:
        return {}
    key = _normalize(merchant_name)
    if not key:
        return {}
    data = _load()
    now = _now_iso()
    existing = data.get(key, {})
    is_new = not existing

    if is_new:
        record: dict = {
            "display_name": display_name or merchant_name,
            "business_type": business_type,
            "category": category,
            "source": source,
            "confidence": confidence,
            "location": location,
            "first_seen": now,
            "last_seen": now,
            "hit_count": 0,
            "history": [{"source": source, "iso": now}],
        }
    else:
        record = dict(existing)
        record["last_seen"] = now
        # Manual corrections override everything. Other sources only fill
        # missing fields so we don't downgrade a higher-quality earlier capture.
        manual = source == "manual_correction"
        if display_name and (manual or not record.get("display_name")):
            record["display_name"] = display_name
        if business_type and (manual or not record.get("business_type")):
            record["business_type"] = business_type
        if category and (manual or not record.get("category")):
            record["category"] = category
        if location and (manual or not record.get("location")):
            record["location"] = location
        if confidence is not None and (manual or confidence > (record.get("confidence") or 0)):
            record["confidence"] = confidence
        # Track source on every update so we can see how an entry evolved.
        if manual or record.get("source") != source:
            record["source"] = source if manual else record.get("source")
        history = list(record.get("history") or [])
        history.append({"source": source, "iso": now})
        record["history"] = history[-5:]  # keep last 5 events

    data[key] = record
    _save(data)
    return dict(record)


def apply_correction(
    merchant_name: str,
    *,
    category: str | None = None,
    business_type: str | None = None,
) -> dict:
    """User said 'this merchant is actually X.' Highest-authority update.

    Convenience wrapper around `update()` with source='manual_correction'."""
    return update(
        merchant_name,
        category=category,
        business_type=business_type,
        source="manual_correction",
        confidence=0.95,  # we trust user corrections strongly
    )


def forget(merchant_name: str) -> bool:
    """Drop one entry. Returns True if removed, False if not present."""
    if not merchant_name:
        return False
    key = _normalize(merchant_name)
    data = _load()
    if key not in data:
        return False
    del data[key]
    _save(data)
    return True


def list_all(
    *,
    sort_by: str = "hit_count",
    limit: int = 100,
    include_expired: bool = False,
) -> list[dict]:
    """Inventory of cached merchants, sorted by `sort_by` (hit_count, last_seen,
    or first_seen). Used by workflow_list_known_merchants."""
    data = _load()
    rows: list[dict] = []
    for key, rec in data.items():
        if not include_expired and _is_expired(rec):
            continue
        rows.append({"key": key, **rec})
    if sort_by == "last_seen":
        rows.sort(key=lambda r: r.get("last_seen") or "", reverse=True)
    elif sort_by == "first_seen":
        rows.sort(key=lambda r: r.get("first_seen") or "", reverse=True)
    else:
        rows.sort(key=lambda r: r.get("hit_count", 0), reverse=True)
    return rows[:limit]


def stats() -> dict:
    """Summary of cache state for diagnostics."""
    data = _load()
    total = len(data)
    expired = sum(1 for rec in data.values() if _is_expired(rec))
    by_source: dict[str, int] = {}
    total_hits = 0
    for rec in data.values():
        src = rec.get("source") or "unknown"
        by_source[src] = by_source.get(src, 0) + 1
        total_hits += int(rec.get("hit_count", 0))
    return {
        "total_merchants": total,
        "active": total - expired,
        "expired": expired,
        "by_source": by_source,
        "total_cache_hits_lifetime": total_hits,
        "cache_path": str(_CACHE_PATH),
        "ttl_days": _TTL_DAYS,
    }


def clear() -> int:
    """Drop ALL entries. Returns count removed. Admin/test only."""
    data = _load()
    n = len(data)
    _save({})
    return n


# Test helpers — let unit tests redirect the cache file to a tempdir.
def _override_path_for_tests(p: Path) -> None:
    global _CACHE_PATH
    _CACHE_PATH = Path(p)
