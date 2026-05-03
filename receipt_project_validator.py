# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""Receipt → StaffWizard project validation flow (v0.9.0).

StaffWizard is the source of truth for which projects exist. Every receipt
submission must resolve to a project_code in the active StaffWizard set.
When a receipt arrives without a valid project (or the auto-router lands on
a code that isn't in the StaffWizard active set), this module:

    1. Parks the receipt under `Surefox AP/Triage/Pending-Project-Pick/`.
    2. Records a pending-pick entry keyed by receipt id.
    3. DMs the submitter with two options:

        [A] List active projects        — system replies with the top-12
                                          active StaffWizard projects;
                                          submitter replies with the code
                                          or name of the right one.
        [B] New project — request setup — stub for v0.9.0; logs a
                                          new-project-request entry, acks
                                          the submitter, parks the receipt
                                          for follow-up. Coding handover
                                          happens in v0.9.1.

A submitter's reply text routes through `handle_picker_reply()`, which
tries to match against the active project list (code → name → substring)
and either re-files the receipt or re-prompts.

Public surface
--------------
    validate(receipt_meta) -> ValidationResult
    request_options(submitter, receipt_id, *, send_chat=None) -> dict
    handle_picker_reply(submitter, reply_text, *, pending_id=None) -> dict
    request_new_project(submitter, project_name, hint=None) -> dict
    list_pending(*, status=None) -> list[dict]
    pending_count() -> int

The `send_chat` callable is injectable so the MCP wrapper in
tools/ap_wave1.py can plug in `gservices.chat_send_dm`, while tests can
plug in a recorder.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import staffwizard_project_sync as _sps


_PROJECT_ROOT = Path(__file__).resolve().parent
_PENDING_PATH = _PROJECT_ROOT / "receipt_pending_picks.json"
_NEW_PROJECT_REQUESTS_PATH = _PROJECT_ROOT / "receipt_new_project_requests.json"

# How many projects to surface in the picker list when the user picks
# Option A. Keeps the chat reply readable on a phone screen.
DEFAULT_PICKER_LIMIT = 12


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass
class ValidationResult:
    valid: bool
    project_code: Optional[str]
    reason: str                  # human-readable: matched / unknown_code /
                                 # missing / inactive
    needs_picker: bool           # true → caller should request_options(...)


# --------------------------------------------------------------------------- #
# Storage primitives
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.stem + ".", suffix=".json.tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate(receipt_meta: dict) -> ValidationResult:
    """Decide whether a receipt's project_code is shippable.

    `receipt_meta` is whatever the receipt extractor produced. We look for
    `project_code` (preferred) or `project` (legacy). An empty/missing
    code means we never resolved one — caller asks the submitter.

    The active set is `staffwizard_project_sync.active_staffwizard_codes()`.
    Anything outside that set fails validation, even if it's in
    project_registry, because StaffWizard is authoritative now.
    """
    code = (
        (receipt_meta.get("project_code") or receipt_meta.get("project") or "")
        .strip()
        .upper()
    )
    if not code:
        return ValidationResult(
            valid=False, project_code=None,
            reason="missing", needs_picker=True,
        )

    actives = _sps.active_staffwizard_codes()
    if code in actives:
        return ValidationResult(
            valid=True, project_code=code,
            reason="matched", needs_picker=False,
        )

    # Code didn't match. Two flavors of bad:
    #   - inactive: registry has it but flagged active=False
    #   - unknown_code: not in registry at all
    import project_registry as _pr  # local to avoid cycle on import
    rec = _pr.get(code)
    if rec and not rec.get("active", True):
        return ValidationResult(
            valid=False, project_code=code,
            reason="inactive", needs_picker=True,
        )
    return ValidationResult(
        valid=False, project_code=code,
        reason="unknown_code", needs_picker=True,
    )


# --------------------------------------------------------------------------- #
# Picker request (Option A/B prompt)
# --------------------------------------------------------------------------- #


def _short_id() -> str:
    """Short unique pending-pick id, easy to surface in chat replies."""
    return "PICK-" + secrets.token_hex(3).upper()


def request_options(
    submitter_id: str,
    receipt_id: str,
    *,
    receipt_meta: Optional[dict] = None,
    send_chat: Optional[Callable[[str, str], dict]] = None,
    channel_hint: str = "chat",
) -> dict:
    """Open a pending-pick session for one receipt.

    Records the entry, optionally fires the chat DM (when `send_chat` is
    supplied — typically `gservices.chat_send_dm`).

    Returns:
        {pending_id, submitter_id, receipt_id, status, chat_message?,
         chat_send_result?}
    """
    pending = _load(_PENDING_PATH)
    pending_id = _short_id()
    entry = {
        "pending_id": pending_id,
        "submitter_id": submitter_id,
        "receipt_id": receipt_id,
        "channel": channel_hint,
        "receipt_meta": receipt_meta or {},
        "status": "awaiting_pick",
        "created_at": _now_iso(),
        "options_offered": ["A", "B"],
    }
    pending[pending_id] = entry
    _atomic_write(_PENDING_PATH, pending)

    msg = _format_options_message(receipt_meta or {}, pending_id=pending_id)
    out = {
        "pending_id": pending_id,
        "submitter_id": submitter_id,
        "receipt_id": receipt_id,
        "status": "awaiting_pick",
        "chat_message": msg,
    }
    if send_chat is not None:
        try:
            out["chat_send_result"] = send_chat(submitter_id, msg)
        except Exception as e:
            out["chat_send_result"] = {"status": "error", "error": str(e)}
    return out


def _format_options_message(receipt_meta: dict, *, pending_id: str) -> str:
    vendor = receipt_meta.get("vendor") or receipt_meta.get("merchant") or "(unknown vendor)"
    amount = receipt_meta.get("total") or receipt_meta.get("amount") or ""
    date = receipt_meta.get("date") or receipt_meta.get("transaction_date") or ""
    head = f"Receipt {pending_id}: {vendor}"
    if amount:
        head += f" — ${amount}"
    if date:
        head += f" ({date})"
    return (
        f"{head}\n"
        "I couldn't match this receipt to a current StaffWizard project.\n"
        "Reply with one of:\n"
        "  A — list the active projects so you can pick one\n"
        "  B — this is a new project, please add it (we'll log the request)\n"
        "Or just reply with the project code or name directly."
    )


# --------------------------------------------------------------------------- #
# Reply handling
# --------------------------------------------------------------------------- #


_OPTION_A_RE = re.compile(r"^\s*(a|option a|list)\s*$", re.IGNORECASE)
_OPTION_B_RE = re.compile(r"^\s*(b|option b|new|new project)\s*$", re.IGNORECASE)


def handle_picker_reply(
    submitter_id: str,
    reply_text: str,
    *,
    pending_id: Optional[str] = None,
    send_chat: Optional[Callable[[str, str], dict]] = None,
) -> dict:
    """Process a submitter's reply to the picker prompt.

    The reply can be:
        - "A" / "list" → respond with the active project list.
        - "B" / "new"  → record a new-project request (Option B stub).
        - direct project code or name → resolve, accept the pick.

    `pending_id` lets the caller scope the reply to a specific pending
    entry; if omitted, we use the submitter's most recent entry.
    """
    pending = _load(_PENDING_PATH)
    entry = _find_pending(pending, submitter_id, pending_id)
    if not entry:
        return {
            "status": "no_pending",
            "submitter_id": submitter_id,
            "hint": "No open project-pick request found for this submitter.",
        }

    text = (reply_text or "").strip()
    if not text:
        return {"status": "empty_reply", "pending_id": entry["pending_id"]}

    # Option A — list projects.
    if _OPTION_A_RE.match(text):
        listing = _sps.list_active_projects(limit=DEFAULT_PICKER_LIMIT)
        msg = _format_project_list_message(listing, pending_id=entry["pending_id"])
        if send_chat is not None:
            try:
                send_chat(submitter_id, msg)
            except Exception:
                pass
        # Keep entry open; submitter replies again with code/name.
        entry["status"] = "list_sent"
        entry["last_action_at"] = _now_iso()
        pending[entry["pending_id"]] = entry
        _atomic_write(_PENDING_PATH, pending)
        return {
            "status": "list_sent",
            "pending_id": entry["pending_id"],
            "projects_offered": listing,
            "chat_message": msg,
        }

    # Option B — new project request stub.
    if _OPTION_B_RE.match(text):
        req = request_new_project(
            submitter_id, project_name="(submitter requested via Option B)",
            hint=f"From pending {entry['pending_id']}",
            receipt_meta=entry.get("receipt_meta") or {},
            send_chat=send_chat,
        )
        entry["status"] = "new_project_requested"
        entry["new_project_request_id"] = req.get("request_id")
        entry["last_action_at"] = _now_iso()
        pending[entry["pending_id"]] = entry
        _atomic_write(_PENDING_PATH, pending)
        return {
            "status": "new_project_requested",
            "pending_id": entry["pending_id"],
            "request_id": req.get("request_id"),
            "chat_message": req.get("chat_message"),
        }

    # Treat as a direct code/name pick.
    matched = _sps.lookup_by_input(text)
    if not matched:
        msg = (
            f"I didn't find an active StaffWizard project matching '{text}'. "
            "Reply 'A' to see the active list, 'B' to request a new project, "
            "or try the project code (e.g. CONDOR-9)."
        )
        if send_chat is not None:
            try:
                send_chat(submitter_id, msg)
            except Exception:
                pass
        return {
            "status": "no_match",
            "pending_id": entry["pending_id"],
            "input": text,
            "chat_message": msg,
        }

    # Resolved.
    entry["status"] = "resolved"
    entry["resolved_to"] = matched["code"]
    entry["resolved_at"] = _now_iso()
    pending[entry["pending_id"]] = entry
    _atomic_write(_PENDING_PATH, pending)
    msg = (
        f"Got it — filing receipt {entry['pending_id']} to project "
        f"{matched['code']} ({matched.get('name', '')}). Thanks!"
    )
    if send_chat is not None:
        try:
            send_chat(submitter_id, msg)
        except Exception:
            pass
    return {
        "status": "resolved",
        "pending_id": entry["pending_id"],
        "project_code": matched["code"],
        "project_name": matched.get("name"),
        "receipt_id": entry.get("receipt_id"),
        "chat_message": msg,
    }


def _find_pending(
    pending: dict, submitter_id: str, pending_id: Optional[str],
) -> Optional[dict]:
    if pending_id:
        return pending.get(pending_id)
    # Most recent open entry for this submitter.
    candidates = [
        e for e in pending.values()
        if e.get("submitter_id") == submitter_id
        and e.get("status") in ("awaiting_pick", "list_sent")
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return candidates[0]


def _format_project_list_message(listing: list[dict], *, pending_id: str) -> str:
    if not listing:
        return (
            f"No active StaffWizard projects right now (pending {pending_id}). "
            "Reply 'B' to request a new project."
        )
    lines = [f"Active projects (pending {pending_id}) — reply with the code or name:"]
    for p in listing:
        code = p["code"]
        name = (p.get("name") or "").strip()
        suffix = f"  ({name})" if name and name.upper() != code else ""
        lines.append(f"  • {code}{suffix}")
    lines.append("  …or 'B' to request a new project.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Option B — new project request stub
# --------------------------------------------------------------------------- #


def request_new_project(
    submitter_id: str,
    project_name: str,
    *,
    hint: Optional[str] = None,
    receipt_meta: Optional[dict] = None,
    send_chat: Optional[Callable[[str, str], dict]] = None,
) -> dict:
    """Log a new-project request and ack the submitter.

    The actual project setup (folder tree, sheets, registry record) is
    deferred to v0.9.1 — see roadmap. For v0.9.0, the request lands in
    `receipt_new_project_requests.json` and the receipt sits in
    `Triage/Pending-New-Projects/` until an operator handles it.
    """
    requests = _load(_NEW_PROJECT_REQUESTS_PATH)
    req_id = "NEWPROJ-" + secrets.token_hex(3).upper()
    requests[req_id] = {
        "request_id": req_id,
        "submitter_id": submitter_id,
        "project_name": project_name,
        "hint": hint or "",
        "receipt_meta": receipt_meta or {},
        "status": "logged",
        "created_at": _now_iso(),
    }
    _atomic_write(_NEW_PROJECT_REQUESTS_PATH, requests)

    msg = (
        f"New project request logged ({req_id}). I've parked the receipt "
        "under Triage/Pending-New-Projects/ for now. An operator will "
        "stand up the project; until then this option is in "
        "v0.9.0 stub mode."
    )
    if send_chat is not None:
        try:
            send_chat(submitter_id, msg)
        except Exception:
            pass
    return {
        "status": "logged",
        "request_id": req_id,
        "chat_message": msg,
    }


# --------------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------------- #


def list_pending(*, status: Optional[str] = None) -> list[dict]:
    pending = _load(_PENDING_PATH)
    out = list(pending.values())
    if status:
        out = [e for e in out if e.get("status") == status]
    out.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return out


def pending_count() -> int:
    return len([
        e for e in _load(_PENDING_PATH).values()
        if e.get("status") in ("awaiting_pick", "list_sent")
    ])


def list_new_project_requests(*, status: Optional[str] = None) -> list[dict]:
    reqs = _load(_NEW_PROJECT_REQUESTS_PATH)
    out = list(reqs.values())
    if status:
        out = [e for e in out if e.get("status") == status]
    out.sort(key=lambda e: e.get("created_at") or "", reverse=True)
    return out
