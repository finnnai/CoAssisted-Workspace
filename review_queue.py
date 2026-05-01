# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms. Removing or altering this header is prohibited.
"""Review queue for medium-confidence vendor replies.

When workflow_process_vendor_replies parses a reply with confidence ==
"medium", the row is updated in place but kept in AWAITING_INFO state and
an entry is pushed here. A human reviews via workflow_list_review_queue,
then promotes (or rejects) explicitly.

State store: ~/Developer/google_workspace_mcp/review_queue.json
Atomic writes via tempfile + os.replace, same pattern as vendor_followups.py.
"""

from __future__ import annotations

import json
import os
import tempfile
import datetime as _dt
from pathlib import Path
from typing import Any, Optional


_PROJECT_ROOT = Path(__file__).resolve().parent
_STORE_PATH = _PROJECT_ROOT / "review_queue.json"


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
        prefix="review_queue.", suffix=".json.tmp",
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


def add_for_review(
    *,
    content_key: str,
    vendor_name: Optional[str],
    vendor_email: Optional[str],
    project_code: Optional[str],
    fields_requested: list[str],
    parsed_fields: dict[str, Any],
    confidence: str,
    reply_excerpt: str,
    sheet_id: Optional[str] = None,
    row_number: Optional[int] = None,
) -> dict[str, Any]:
    """Queue an entry for human review.

    Re-adding an existing content_key updates the entry in place (most
    recent parsed_fields wins) — useful when a vendor sends multiple
    medium-confidence replies in a row.
    """
    if not content_key:
        raise ValueError("content_key is required")
    if confidence not in ("high", "medium", "low"):
        raise ValueError(f"confidence must be high/medium/low, got {confidence!r}")
    data = _load()
    rec = {
        "content_key": content_key,
        "vendor_name": vendor_name,
        "vendor_email": vendor_email,
        "project_code": project_code,
        "fields_requested": list(fields_requested or []),
        "parsed_fields": dict(parsed_fields or {}),
        "confidence": confidence,
        "reply_excerpt": reply_excerpt[:500] if reply_excerpt else "",
        "sheet_id": sheet_id,
        "row_number": row_number,
        "queued_at": _now_iso(),
    }
    data[content_key] = rec
    _save(data)
    return dict(rec)


def get(content_key: str) -> Optional[dict[str, Any]]:
    return _load().get(content_key)


def list_open(*, project_code: Optional[str] = None) -> list[dict[str, Any]]:
    """All entries currently awaiting human review.

    Most recently queued first. Optionally filter by project_code.
    """
    data = _load()
    entries = list(data.values())
    if project_code:
        entries = [e for e in entries if e.get("project_code") == project_code]
    entries.sort(key=lambda e: e.get("queued_at") or "", reverse=True)
    return entries


def mark_promoted(content_key: str) -> bool:
    """Remove from queue when a human approves and promotes the row.

    Returns True if the entry existed and was removed, False otherwise.
    """
    data = _load()
    if content_key not in data:
        return False
    del data[content_key]
    _save(data)
    return True


def forget(content_key: str) -> bool:
    """Remove from queue without promotion (e.g. rejected)."""
    return mark_promoted(content_key)  # same operation, different intent


def clear() -> int:
    """Drop everything. Returns count cleared. Test/admin use only."""
    data = _load()
    n = len(data)
    _save({})
    return n


def _override_path_for_tests(p: Path) -> None:
    """Tests use this to redirect the store at a tmp_path file."""
    global _STORE_PATH
    _STORE_PATH = p
