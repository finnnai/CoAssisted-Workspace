# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE.
"""Recent-actions log — persistent JSONL audit trail for write operations.

Foundation for the undo capability and bulk-op rollback. Every write tool
that wants to be revertable calls `record()` with a before-snapshot, the
action it's about to take, and the after-snapshot once it's done.

Storage: `logs/recent_actions.jsonl` — append-only, one JSON record per line.
Survives restarts. To clear: delete the file.

Why JSONL:
- Append-only — no risk of corrupting previous records on a write
- Streamable — `tail -100 recent_actions.jsonl | jq` works
- Trivial to parse without lock contention

Record shape:
    {
      "id": "<uuid>",
      "timestamp": "<ISO 8601>",
      "tool": "calendar_create_event" | etc,
      "action": "create" | "update" | "delete",
      "target_kind": "calendar_event" | "contact" | "drive_file" | etc,
      "target_id": "<resource id>",
      "snapshot_before": {...} | null,
      "snapshot_after": {...} | null,
      "summary": "<human-readable one-liner>",
      "reverted": false,
      "revert_target_action_id": null  # if this record IS a revert, points at original
    }
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from pathlib import Path
from threading import Lock
from typing import Any, Optional


_PROJECT_DIR = Path(__file__).resolve().parent
_LOG_PATH = _PROJECT_DIR / "logs" / "recent_actions.jsonl"
_write_lock = Lock()


def _ensure_log_dir() -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def record(
    *,
    tool: str,
    action: str,                     # "create" | "update" | "delete"
    target_kind: str,                # "calendar_event" | "contact" | etc
    target_id: str,
    summary: str,
    snapshot_before: Optional[dict] = None,
    snapshot_after: Optional[dict] = None,
    revert_target_action_id: Optional[str] = None,
) -> str:
    """Append one record to the log. Returns the action's UUID."""
    _ensure_log_dir()
    record_id = str(uuid.uuid4())
    line = json.dumps({
        "id": record_id,
        "timestamp": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "tool": tool,
        "action": action,
        "target_kind": target_kind,
        "target_id": target_id,
        "snapshot_before": snapshot_before,
        "snapshot_after": snapshot_after,
        "summary": summary,
        "reverted": False,
        "revert_target_action_id": revert_target_action_id,
    })
    with _write_lock:
        with _LOG_PATH.open("a") as f:
            f.write(line + "\n")
    return record_id


def list_recent(
    limit: int = 50,
    tool_filter: Optional[str] = None,
    target_kind_filter: Optional[str] = None,
    only_revertable: bool = False,
    since_iso: Optional[str] = None,
) -> list[dict]:
    """Read the most-recent N records, newest-first.

    Filters are AND-combined. `only_revertable` excludes records that
    have already been reverted, OR records that ARE reverts (you don't
    revert a revert from this surface — re-run the original).
    """
    if not _LOG_PATH.exists():
        return []
    out: list[dict] = []
    with _LOG_PATH.open() as f:
        # Read all lines into memory then reverse — log files won't grow
        # to GB scale in practice. If they do, switch to a deque iteration.
        lines = f.readlines()
    for line in reversed(lines):
        if len(out) >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if tool_filter and rec.get("tool") != tool_filter:
            continue
        if target_kind_filter and rec.get("target_kind") != target_kind_filter:
            continue
        if only_revertable:
            if rec.get("reverted"):
                continue
            if rec.get("revert_target_action_id"):
                continue
        if since_iso:
            try:
                cutoff = _dt.datetime.fromisoformat(since_iso)
                rec_ts = _dt.datetime.fromisoformat(rec["timestamp"])
                if rec_ts < cutoff:
                    continue
            except (ValueError, KeyError):
                pass
        out.append(rec)
    return out


def get_action(action_id: str) -> Optional[dict]:
    """Fetch a single record by id, or None if not found."""
    if not _LOG_PATH.exists():
        return None
    with _LOG_PATH.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("id") == action_id:
                return rec
    return None


def mark_reverted(action_id: str, revert_action_id: str) -> bool:
    """Mark a record as reverted, pointing at the new revert action's id.

    JSONL is append-only so we can't edit in place — we instead append
    a new record with `revert_target_action_id` pointing at the original,
    then mutate the in-memory consumer's view. For persistent flag, we
    rewrite the whole file with the flag updated. This is the one
    operation that touches existing records.
    """
    if not _LOG_PATH.exists():
        return False
    with _write_lock:
        with _LOG_PATH.open() as f:
            lines = f.readlines()
        rewritten = []
        found = False
        for line in lines:
            line_s = line.strip()
            if not line_s:
                rewritten.append(line)
                continue
            try:
                rec = json.loads(line_s)
            except json.JSONDecodeError:
                rewritten.append(line)
                continue
            if rec.get("id") == action_id and not rec.get("reverted"):
                rec["reverted"] = True
                rec["revert_action_id"] = revert_action_id
                rewritten.append(json.dumps(rec) + "\n")
                found = True
            else:
                rewritten.append(line)
        if found:
            with _LOG_PATH.open("w") as f:
                f.writelines(rewritten)
    return found
