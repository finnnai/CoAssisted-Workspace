# © 2026 CoAssisted Workspace. Licensed under MIT.
"""AP-6: Forced project Drive tree — creation, audit, lazy expansion.

Day-1 hot deploy created `Surefox AP/` plus 8 active project subtrees.
This module handles everything after Day-1:

    1. `register_new_project` — operator-driven full-tree creation for
       new projects. Builds project folder + 7 subfolders + the current
       month bucket under Receipts/ and Invoices/. Stamps every Drive
       folder ID into the project_registry record so AP-2's EIB writer
       and AP-7's labor ingestion can target them directly.

    2. `ensure_month_subtree` — lazy expansion. The first receipt in a
       new month triggers creation of `{Receipts|Invoices}/{YYYY-MM}/`.
       Idempotent — repeated calls return the existing folder ID.

    3. `audit_filing_tree` — daily scan via the existing scheduled task.
       Surfaces any manual additions that crept in (someone dragged a
       file into a project folder via the Drive UI rather than going
       through the capture pipeline). Reports per-project counts +
       suspicious-file lists for operator review.

The forced-tree principle: managers can't drag-and-drop into the wrong
place because the wrong place doesn't exist. Audit catches any
violations after the fact — not preventive but detectable, which is
enough for guard-services-scale ops.

Storage:
    The project_registry holds Drive folder IDs. The audit results
    persist to ~/Developer/google_workspace_mcp/ap_tree_audit.json
    (gitignored — point-in-time inventory, not a system-of-record).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

import project_registry


# =============================================================================
# Constants
# =============================================================================

_PROJECT_ROOT = Path(__file__).resolve().parent
_AUDIT_PATH = _PROJECT_ROOT / "ap_tree_audit.json"

# The seven canonical subfolders every project gets on registration.
# Keys are stored in project_registry.drive_subfolders.
_PROJECT_SUBFOLDERS = (
    "receipts",
    "invoices",
    "labor",
    "statements_amex",
    "statements_wex",
    "workday_supplier",
    "workday_journal",
)

# Display names — for the actual Drive folder name (the registry stores
# the structured key separately).
_SUBFOLDER_DISPLAY = {
    "receipts": "Receipts",
    "invoices": "Invoices",
    "labor": "Labor",
    "statements_amex": "Statements/AMEX",
    "statements_wex": "Statements/WEX",
    "workday_supplier": "Workday-Exports/Supplier-Invoice",
    "workday_journal": "Workday-Exports/Accounting-Journal",
}

# Day-1 hot deploy already created these at the AP root. AP-6 doesn't
# recreate them — it just looks them up by name.
_AP_ROOT_NAME = "Surefox AP"
_PROJECTS_FOLDER_NAME = "Projects"


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _month_bucket_name(when: Optional[_dt.date] = None) -> str:
    when = when or _dt.date.today()
    return f"{when:%Y-%m}"


# =============================================================================
# Drive helpers — thin wrappers around the existing tools/drive surface
# =============================================================================

def _drive_create_folder(name: str, parent_id: Optional[str]) -> Optional[str]:
    """Create a Drive folder and return its ID. None on failure."""
    try:
        from gworkspace_clients import drive_client
    except ImportError:
        try:
            # Project-specific helper if drive_client isn't a thing.
            import drive as _drive_mod  # noqa: F401
        except ImportError:
            return None

    # Use the lower-level Google Drive API directly via the existing
    # OAuth token — same pattern the tools/drive.py wrappers use.
    try:
        from googleapiclient.discovery import build
        from auth import get_credentials
    except ImportError:
        return None

    try:
        creds = get_credentials()
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        body = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            body["parents"] = [parent_id]
        result = service.files().create(body=body, fields="id").execute()
        return result.get("id")
    except Exception:
        return None


def _drive_list_children(
    parent_id: str,
    *,
    name: Optional[str] = None,
) -> list[dict]:
    """Return immediate children of a Drive folder. Optional name filter."""
    try:
        from googleapiclient.discovery import build
        from auth import get_credentials
    except ImportError:
        return []
    try:
        creds = get_credentials()
        service = build("drive", "v3", credentials=creds, cache_discovery=False)
        q = f"'{parent_id}' in parents and trashed = false"
        if name:
            # Drive query string-quoting: escape single quotes by doubling.
            esc = name.replace("'", "''")
            q += f" and name = '{esc}'"
        out = []
        page_token = None
        while True:
            resp = service.files().list(
                q=q,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, owners)",
                pageToken=page_token,
                pageSize=200,
            ).execute()
            out.extend(resp.get("files") or [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return out
    except Exception:
        return []


def _find_existing_folder(parent_id: str, name: str) -> Optional[str]:
    """Lookup a folder by name under a parent. Returns first match's ID."""
    children = _drive_list_children(parent_id, name=name)
    for c in children:
        if c.get("mimeType") == "application/vnd.google-apps.folder":
            return c.get("id")
    return None


# =============================================================================
# Public — register new project (AP-6 core)
# =============================================================================

def register_new_project(
    *,
    project_name: str,
    code: str,
    client: Optional[str] = None,
    name_aliases: Optional[list[str]] = None,
    assigned_team_emails: Optional[list[str]] = None,
    sender_emails: Optional[list[str]] = None,
    staffwizard_job_number: Optional[str] = None,
    staffwizard_job_desc: Optional[str] = None,
    billing_origin_state: str = "CA",
    billing_terms: str = "Net-15",
    billing_cadence: str = "monthly",
    customer_email: Optional[str] = None,
    ap_root_folder_id: Optional[str] = None,
    projects_parent_folder_id: Optional[str] = None,
) -> dict:
    """Create a new project's full Drive subtree and persist the registry record.

    Resolves the parent folder ID by name when not passed (handles the
    Day-1 hot deploy which only stamped a few project IDs by hand).

    Returns a dict with:
        project_code:      the registered code
        drive_folder_id:   the project's Drive folder
        subfolder_ids:     {receipts, invoices, labor, ...} → ID
        registered:        True if a new registry record was made
        already_existed:   True if Drive folder existed before this call

    Idempotent: re-running this against an existing project updates the
    registry but doesn't duplicate Drive folders.
    """
    # Resolve the Projects/ parent.
    if not projects_parent_folder_id:
        if not ap_root_folder_id:
            return {
                "error": (
                    "Either projects_parent_folder_id or ap_root_folder_id "
                    "must be provided. AP-6 doesn't auto-search for "
                    "'Surefox AP/Projects' to avoid name collisions."
                ),
            }
        projects_parent_folder_id = _find_existing_folder(
            ap_root_folder_id, _PROJECTS_FOLDER_NAME
        )
        if not projects_parent_folder_id:
            return {
                "error": (
                    f"Could not locate '{_PROJECTS_FOLDER_NAME}' under "
                    f"ap_root_folder_id={ap_root_folder_id!r}."
                ),
            }

    # Project folder — reuse if exists.
    project_folder_id = _find_existing_folder(
        projects_parent_folder_id, project_name
    )
    already_existed = bool(project_folder_id)
    if not project_folder_id:
        project_folder_id = _drive_create_folder(
            project_name, projects_parent_folder_id
        )
    if not project_folder_id:
        return {"error": "Failed to create project folder in Drive."}

    # Subfolders. Workday-Exports / Statements have nested children; we
    # build the leaf folders and stamp the leaf IDs.
    subfolder_ids: dict[str, Optional[str]] = {}
    for key in _PROJECT_SUBFOLDERS:
        display = _SUBFOLDER_DISPLAY[key]
        if "/" in display:
            # e.g. "Statements/AMEX" — create the parent first.
            parent, leaf = display.split("/", 1)
            parent_id = (
                _find_existing_folder(project_folder_id, parent)
                or _drive_create_folder(parent, project_folder_id)
            )
            if not parent_id:
                subfolder_ids[key] = None
                continue
            leaf_id = (
                _find_existing_folder(parent_id, leaf)
                or _drive_create_folder(leaf, parent_id)
            )
            subfolder_ids[key] = leaf_id
        else:
            sid = (
                _find_existing_folder(project_folder_id, display)
                or _drive_create_folder(display, project_folder_id)
            )
            subfolder_ids[key] = sid

    # Current-month bucket under Receipts/ and Invoices/.
    bucket = _month_bucket_name()
    if subfolder_ids.get("receipts"):
        rid = (
            _find_existing_folder(subfolder_ids["receipts"], bucket)
            or _drive_create_folder(bucket, subfolder_ids["receipts"])
        )
        if rid:
            subfolder_ids[f"receipts_{bucket.replace('-', '_')}"] = rid
    if subfolder_ids.get("invoices"):
        iid = (
            _find_existing_folder(subfolder_ids["invoices"], bucket)
            or _drive_create_folder(bucket, subfolder_ids["invoices"])
        )
        if iid:
            subfolder_ids[f"invoices_{bucket.replace('-', '_')}"] = iid

    # Persist to project_registry.
    project_registry.register(
        code,
        name=project_name,
        client=client,
        sender_emails=sender_emails,
        drive_folder_id=project_folder_id,
        drive_subfolders=subfolder_ids,
        name_aliases=name_aliases,
        staffwizard_job_number=staffwizard_job_number,
        staffwizard_job_desc=staffwizard_job_desc,
        assigned_team_emails=assigned_team_emails,
        billing_origin_state=billing_origin_state,
        billing_terms=billing_terms,
        billing_cadence=billing_cadence,
        customer_email=customer_email,
    )

    return {
        "project_code": code,
        "drive_folder_id": project_folder_id,
        "subfolder_ids": subfolder_ids,
        "already_existed": already_existed,
        "registered": True,
    }


# =============================================================================
# Public — ensure month subtree (AP-6 lazy expansion)
# =============================================================================

def ensure_month_subtree(
    code: str,
    *,
    when: Optional[_dt.date] = None,
    kinds: tuple[str, ...] = ("receipts", "invoices"),
) -> dict[str, Optional[str]]:
    """Ensure {YYYY-MM}/ exists under the given subfolders for a project.

    Idempotent. Returns {kind: folder_id} for every kind requested.
    Lazy-creates folders that don't exist yet, and stamps their IDs
    into the project_registry under keys like 'receipts_2026_06'.
    """
    record = project_registry.get(code)
    if not record:
        return {k: None for k in kinds}
    bucket = _month_bucket_name(when)
    subs = record.get("drive_subfolders") or {}
    out: dict[str, Optional[str]] = {}
    for kind in kinds:
        parent_id = subs.get(kind)
        if not parent_id:
            out[kind] = None
            continue
        # Cached month folder?
        cache_key = f"{kind}_{bucket.replace('-', '_')}"
        cached = subs.get(cache_key)
        if cached:
            out[kind] = cached
            continue
        # Look up or create.
        existing = _find_existing_folder(parent_id, bucket)
        folder_id = existing or _drive_create_folder(bucket, parent_id)
        if folder_id:
            project_registry.update_drive_subfolder(code, cache_key, folder_id)
        out[kind] = folder_id
    return out


# =============================================================================
# Public — audit filing tree (AP-6 daily watch)
# =============================================================================

def audit_filing_tree(
    *,
    age_threshold_minutes: int = 60,
) -> dict:
    """Scan every registered project's Drive subtree for unexpected files.

    A file is "unexpected" if:
        - It was added more recently than {age_threshold_minutes} ago
          (catches things that just got dropped in)
        - AND its name doesn't match the AP-6 naming convention
          (`YYYY-MM-DD_*_amount_type.ext`)

    Reports per-project counts + a list of suspicious files (path,
    modified time, owner) for operator review. Persists the result to
    ap_tree_audit.json so the next run can show "this is new since
    last audit" too.

    Returns dict with summary + per-project findings.
    """
    import re
    naming_re = re.compile(
        r"^\d{4}-\d{2}-\d{2}_[A-Za-z0-9]+_[\d.]+_(receipt|invoice|statement|labor)"
        r"\.(pdf|jpg|jpeg|png|csv|xlsx|xls|tar\.gz|zip)$",
        re.IGNORECASE,
    )

    cutoff = _dt.datetime.now().astimezone() - _dt.timedelta(minutes=age_threshold_minutes)

    findings: dict[str, dict] = {}
    suspicious_total = 0
    scanned_total = 0

    for record in project_registry.list_all(active_only=True):
        code = record.get("code")
        subs = record.get("drive_subfolders") or {}
        per_project_suspicious: list[dict] = []
        per_project_count = 0
        for kind, folder_id in subs.items():
            if not folder_id:
                continue
            children = _drive_list_children(folder_id)
            for c in children:
                if c.get("mimeType") == "application/vnd.google-apps.folder":
                    continue
                per_project_count += 1
                scanned_total += 1
                # Parse modifiedTime.
                mtime_str = c.get("modifiedTime") or ""
                try:
                    mtime = _dt.datetime.fromisoformat(
                        mtime_str.replace("Z", "+00:00")
                    )
                except ValueError:
                    continue
                if mtime < cutoff:
                    continue
                name = c.get("name") or ""
                if naming_re.match(name):
                    continue
                # Suspicious: recent + non-conforming name.
                per_project_suspicious.append({
                    "name": name,
                    "id": c.get("id"),
                    "subfolder": kind,
                    "modified_time": mtime_str,
                    "owners": [
                        o.get("emailAddress") for o in (c.get("owners") or [])
                    ],
                })
        if per_project_suspicious:
            suspicious_total += len(per_project_suspicious)
            findings[code] = {
                "scanned": per_project_count,
                "suspicious": per_project_suspicious,
            }

    report = {
        "audited_at": _now_iso(),
        "age_threshold_minutes": age_threshold_minutes,
        "scanned_files": scanned_total,
        "suspicious_files": suspicious_total,
        "projects_with_findings": len(findings),
        "findings": findings,
    }

    # Persist for diff-against-prior-run.
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix="ap_tree_audit.", suffix=".json.tmp",
            dir=str(_AUDIT_PATH.parent),
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        os.replace(tmp, _AUDIT_PATH)
    except Exception:
        pass  # Audit data is best-effort; don't fail the call.

    return report


def last_audit() -> Optional[dict]:
    """Return the most recent audit report, or None if never run."""
    if not _AUDIT_PATH.exists():
        return None
    try:
        with _AUDIT_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
