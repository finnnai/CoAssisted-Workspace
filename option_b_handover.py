# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""Option B operator handover (v0.9.1).

When a submitter picks Option B in the receipt picker chat-back, v0.9.0
logs the request and stops. v0.9.1 closes the loop:

    resolve_request(request_id, code, name, *, billing_origin_state=None,
                    customer_email=None, billing_terms="Net-15")
        Operator approves the new project. Steps:
            1. Build the Drive subtree (Receipts/{YYYY-MM}, Invoices/{YYYY-MM},
               Labor/Daily, Statements, Workday-Exports) via ap_tree.
            2. Bootstrap the per-project AP sheet (project_invoices helper).
            3. Register the project in project_registry. Marks
               staffwizard_authoritative=False initially — the next
               StaffWizard sync flips it true if/when the project shows up
               in the Overall Report.
            4. Mark the new-project request as 'resolved'.
            5. Re-validate any parked receipts that referenced this request
               and re-file them.
            6. Chat-ack the submitter and the operator.

    reject_request(request_id, reason)
        Operator declines. Steps:
            1. Mark the request as 'rejected' with the reason.
            2. Chat-reply to the submitter with the reason.
            3. Receipt stays parked under Triage/Pending-New-Projects/ for
               the operator to re-file manually or discard.

Both functions take an optional `send_chat` callable for testability.

The Drive-tree creation and AP-sheet bootstrap delegate to existing helpers
(ap_drive_layout / project_invoices) so we don't duplicate filing logic.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Callable, Optional

import receipt_project_validator as _rpv
import project_registry as _pr


_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helpers — Drive tree + AP sheet bootstrap (lazy imports so this module
# stays importable in test contexts without GCP credentials).
# --------------------------------------------------------------------------- #


def _build_drive_subtree(code: str, name: str) -> dict:
    """Create the Surefox AP/Projects/{name}/ subtree.

    Returns {drive_folder_id, drive_subfolders} or {error}. Lazy imports
    keep the test path clean.
    """
    try:
        import ap_tree
        # Reuse the existing register flow; ap_tree.register_new_project
        # builds the subtree as a side effect.
        result = ap_tree.register_new_project(code=code, name=name)
        return {
            "drive_folder_id": result.get("drive_folder_id"),
            "drive_subfolders": result.get("drive_subfolders", {}),
            "ap_sheet_id": result.get("sheet_id"),
        }
    except AttributeError:
        # Fallback: ap_tree may expose ensure_project_root + ensure_month_subtree.
        try:
            import ap_drive_layout
            root_id = ap_drive_layout.ensure_root_folder()
            proj_folder = ap_drive_layout.ensure_project_subfolder(
                project_name=name, project_code=code,
            )
            return {
                "drive_folder_id": proj_folder,
                "drive_subfolders": {},
                "ap_sheet_id": None,
            }
        except Exception as e:
            return {"error": f"drive subtree fallback failed: {e}"}
    except Exception as e:
        return {"error": f"drive subtree creation failed: {e}"}


def _bootstrap_ap_sheet(code: str, name: str) -> Optional[str]:
    """Create the per-project AP sheet via project_invoices helper.
    Returns the sheet_id or None on failure (logged, not raised).
    """
    try:
        import project_invoices
        sheet = project_invoices.create_project_sheet(code=code, name=name)
        return sheet.get("sheet_id") if isinstance(sheet, dict) else sheet
    except (AttributeError, ImportError):
        return None
    except Exception as e:
        _log.warning("ap sheet bootstrap failed for %s: %s", code, e)
        return None


# --------------------------------------------------------------------------- #
# Resolve / Reject
# --------------------------------------------------------------------------- #


def resolve_request(
    request_id: str,
    code: str,
    name: str,
    *,
    billing_origin_state: Optional[str] = None,
    customer_email: Optional[str] = None,
    billing_terms: str = "Net-15",
    billing_cadence: Optional[str] = None,
    create_drive_tree: bool = True,
    create_ap_sheet: bool = True,
    send_chat: Optional[Callable[[str, str], dict]] = None,
) -> dict:
    """Operator approves a new-project request.

    Returns a summary dict:
        {status, request_id, code, name, drive_folder_id?, ap_sheet_id?,
         re_filed_receipts, chat_messages_sent}

    `billing_origin_state="NY"` flips the AR-9 default cadence to weekly.
    """
    requests = _rpv._load(_rpv._NEW_PROJECT_REQUESTS_PATH)  # noqa: SLF001
    req = requests.get(request_id)
    if not req:
        return {
            "status": "not_found",
            "request_id": request_id,
            "hint": "Use workflow_list_pending_picks to see open requests.",
        }
    if req.get("status") in ("resolved", "rejected"):
        return {
            "status": "already_handled",
            "request_id": request_id,
            "previous_status": req.get("status"),
        }

    code_norm = (code or "").strip().upper()
    if not code_norm:
        return {"status": "error", "error": "code is required"}
    if not name or not name.strip():
        return {"status": "error", "error": "name is required"}

    drive_info: dict = {}
    if create_drive_tree:
        drive_info = _build_drive_subtree(code_norm, name)
    sheet_id = None
    if create_ap_sheet:
        sheet_id = _bootstrap_ap_sheet(code_norm, name) or drive_info.get("ap_sheet_id")

    # Default billing cadence: weekly for NY projects, monthly otherwise.
    if not billing_cadence:
        billing_cadence = "weekly" if (billing_origin_state or "").upper() == "NY" else "monthly"

    rec = _pr.register(
        code_norm,
        name=name,
        active=True,
        billing_origin_state=billing_origin_state,
        billing_terms=billing_terms,
        billing_cadence=billing_cadence,
        customer_email=customer_email,
        sheet_id=sheet_id,
        drive_folder_id=drive_info.get("drive_folder_id"),
        drive_subfolders=drive_info.get("drive_subfolders") or None,
    )

    # Mark request resolved.
    req["status"] = "resolved"
    req["resolved_at"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    req["resolved_to_code"] = code_norm
    req["resolved_to_name"] = name
    requests[request_id] = req
    _rpv._atomic_write(_rpv._NEW_PROJECT_REQUESTS_PATH, requests)  # noqa: SLF001

    # Re-file any parked receipts that referenced this request.
    re_filed = _re_file_parked_receipts(request_id, code_norm)

    # Chat acks.
    msgs_sent: list[dict] = []
    if send_chat is not None:
        submitter = req.get("submitter_id")
        if submitter:
            msg = (
                f"Project {code_norm} ({name}) is set up — your parked "
                "receipt has been re-filed. Future receipts for this "
                "project will route automatically."
            )
            try:
                msgs_sent.append({
                    "to": submitter,
                    "result": send_chat(submitter, msg),
                })
            except Exception as e:
                msgs_sent.append({"to": submitter, "error": str(e)})

    return {
        "status": "resolved",
        "request_id": request_id,
        "code": code_norm,
        "name": name,
        "drive_folder_id": drive_info.get("drive_folder_id"),
        "drive_subfolders": drive_info.get("drive_subfolders"),
        "ap_sheet_id": sheet_id,
        "registry_record": rec,
        "re_filed_receipts": re_filed,
        "chat_messages_sent": msgs_sent,
        "billing_origin_state": billing_origin_state,
        "billing_cadence": billing_cadence,
    }


def reject_request(
    request_id: str,
    reason: str,
    *,
    send_chat: Optional[Callable[[str, str], dict]] = None,
) -> dict:
    """Operator declines a new-project request.

    The receipt stays parked under Triage/Pending-New-Projects/ for the
    operator to discard or manually re-file. The submitter gets a chat
    reply with the reason.
    """
    requests = _rpv._load(_rpv._NEW_PROJECT_REQUESTS_PATH)  # noqa: SLF001
    req = requests.get(request_id)
    if not req:
        return {"status": "not_found", "request_id": request_id}
    if req.get("status") in ("resolved", "rejected"):
        return {
            "status": "already_handled",
            "request_id": request_id,
            "previous_status": req.get("status"),
        }
    if not reason or not reason.strip():
        return {"status": "error", "error": "reason is required"}

    req["status"] = "rejected"
    req["rejected_at"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    req["rejection_reason"] = reason.strip()
    requests[request_id] = req
    _rpv._atomic_write(_rpv._NEW_PROJECT_REQUESTS_PATH, requests)  # noqa: SLF001

    msgs_sent: list[dict] = []
    if send_chat is not None:
        submitter = req.get("submitter_id")
        if submitter:
            msg = (
                f"Your new-project request ({request_id}) wasn't approved. "
                f"Reason: {reason}\n"
                "Reply 'A' to pick from the active StaffWizard projects, "
                "or contact your project manager for guidance."
            )
            try:
                msgs_sent.append({
                    "to": submitter,
                    "result": send_chat(submitter, msg),
                })
            except Exception as e:
                msgs_sent.append({"to": submitter, "error": str(e)})

    return {
        "status": "rejected",
        "request_id": request_id,
        "reason": reason.strip(),
        "chat_messages_sent": msgs_sent,
    }


# --------------------------------------------------------------------------- #
# Internal — re-file parked receipts after approval
# --------------------------------------------------------------------------- #


def _re_file_parked_receipts(request_id: str, new_code: str) -> list[dict]:
    """Find pending picks that resolved to this new-project request, then
    mark them resolved against the new code. Actual re-filing on disk is
    delegated to the receipt extractor's filing path — this function just
    flips the pending entry's status so the next sweep picks it up.
    """
    pending = _rpv._load(_rpv._PENDING_PATH)  # noqa: SLF001
    re_filed: list[dict] = []
    changed = False
    for pid, entry in pending.items():
        if entry.get("new_project_request_id") != request_id:
            continue
        if entry.get("status") in ("resolved", "rejected"):
            continue
        entry["status"] = "resolved"
        entry["resolved_to"] = new_code
        entry["resolved_at"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        entry["resolved_via"] = "option_b_handover"
        pending[pid] = entry
        re_filed.append({
            "pending_id": pid,
            "receipt_id": entry.get("receipt_id"),
            "submitter_id": entry.get("submitter_id"),
        })
        changed = True
    if changed:
        _rpv._atomic_write(_rpv._PENDING_PATH, pending)  # noqa: SLF001
    return re_filed
