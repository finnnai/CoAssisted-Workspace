# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Per-vendor reply-time history → adaptive reminder cadence (P1-4).

Records (request_sent_at, replied_at) pairs per vendor_email. Computes
the median reply latency and exposes it to vendor_followups so the
reminder scheduler can adapt to a vendor's actual responsiveness instead
of using a one-size-fits-all 24/48/48 hour ladder.

Storage: ~/Developer/google_workspace_mcp/vendor_response_history.json
{
  "vendor@example.com": {
    "pairs": [
      {"request_sent_at": "...", "replied_at": "...", "hours": 4.2},
      ...
    ],
    "last_updated": "2026-04-30T..."
  }
}

Atomic writes via tempfile + os.replace.
Capped at MAX_PAIRS_PER_VENDOR rolling window (default 20).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional


_PROJECT_ROOT = Path(__file__).resolve().parent
_STORE_PATH = _PROJECT_ROOT / "vendor_response_history.json"

# Rolling window per vendor. Keeps the median reflective of recent
# behavior — old slow replies get aged out as new fast ones come in.
MAX_PAIRS_PER_VENDOR = 20

# Vendors with fewer than COLD_START_THRESHOLD recorded replies use the
# default constant table in vendor_followups, not the per-vendor median.
COLD_START_THRESHOLD = 3


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _load() -> dict[str, dict[str, Any]]:
    if not _STORE_PATH.exists():
        return {}
    try:
        with _STORE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix="vendor_response_history.", suffix=".json.tmp",
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


def _hours_between(a_iso: str, b_iso: str) -> Optional[float]:
    """Return (b - a) in hours, or None if either timestamp is unparseable."""
    try:
        a = _dt.datetime.fromisoformat(a_iso)
        b = _dt.datetime.fromisoformat(b_iso)
        if a.tzinfo is None:
            a = a.replace(tzinfo=_dt.timezone.utc)
        if b.tzinfo is None:
            b = b.replace(tzinfo=_dt.timezone.utc)
        return (b - a).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return None


def record_response_pair(
    vendor_email: str,
    request_sent_at: str,
    replied_at: str,
) -> bool:
    """Log a (request, reply) pair for a vendor. Trims to the rolling
    window. Returns True on success, False on bad input."""
    if not vendor_email or not request_sent_at or not replied_at:
        return False
    hours = _hours_between(request_sent_at, replied_at)
    if hours is None or hours < 0:
        return False
    data = _load()
    rec = data.setdefault(vendor_email.lower(), {"pairs": [], "last_updated": None})
    rec["pairs"].append({
        "request_sent_at": request_sent_at,
        "replied_at": replied_at,
        "hours": round(hours, 2),
    })
    # Trim to rolling window — keep the most recent N
    if len(rec["pairs"]) > MAX_PAIRS_PER_VENDOR:
        rec["pairs"] = rec["pairs"][-MAX_PAIRS_PER_VENDOR:]
    rec["last_updated"] = _now_iso()
    _save(data)
    return True


def median_reply_hours(vendor_email: str) -> Optional[float]:
    """Compute median reply latency for a vendor (in hours).
    Returns None if the vendor has fewer than COLD_START_THRESHOLD
    recorded replies — caller should fall back to the constant table.
    """
    if not vendor_email:
        return None
    rec = _load().get(vendor_email.lower())
    if not rec:
        return None
    pairs = rec.get("pairs", [])
    if len(pairs) < COLD_START_THRESHOLD:
        return None
    hours = sorted(p["hours"] for p in pairs if "hours" in p)
    if not hours:
        return None
    n = len(hours)
    if n % 2 == 1:
        return float(hours[n // 2])
    return float((hours[n // 2 - 1] + hours[n // 2]) / 2.0)


def adaptive_wait_hours(vendor_email: str, default_hours: int) -> int:
    """Pick a wait window for the next reminder based on the vendor's
    median reply time:
      - <12hr median   → 24hr next reminder
      - 12-48hr median → 72hr next reminder
      - >=48hr median  → 120hr next reminder
      - cold-start (no/sparse history) → default_hours

    Returns an integer hour count.
    """
    median = median_reply_hours(vendor_email)
    if median is None:
        return default_hours
    if median < 12:
        return 24
    if median < 48:
        return 72
    return 120


def get_history(vendor_email: str) -> Optional[dict[str, Any]]:
    """Inspect a vendor's recorded history (or None if no record)."""
    if not vendor_email:
        return None
    return _load().get(vendor_email.lower())


def list_known_vendors() -> list[str]:
    return sorted(_load().keys())


def clear() -> int:
    """Drop everything. Returns the number of vendor records cleared.
    Test/admin use only."""
    data = _load()
    n = len(data)
    _save({})
    return n


def _override_path_for_tests(p: Path) -> None:
    global _STORE_PATH
    _STORE_PATH = p
