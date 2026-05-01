# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""AP Drive layout — folder + sheet hierarchy for project-tracked spend.

Structure (per the hybrid model):

    My Drive/
    └── AP Submissions/                     <— root, auto-created
        ├── Master/                         <— master roll-up sheets
        │   ├── Project Invoices — SMOKE     (one row per submission, all employees)
        │   └── Project Invoices — ALPHA
        └── Szott, Joshua/                  <— per-employee folder
            ├── SMOKE/                      <— per-employee-per-project folder
            │   ├── Project Invoices — SMOKE [Szott, Joshua]   <— employee's sheet
            │   ├── 2026-04-26__Acme__INV-TEST-2026-042.pdf    <— archived original
            │   └── …
            └── ALPHA/

Every extracted row gets appended to BOTH the master roll-up AND the per-
employee-per-project sheet. Original PDF/image attachments get saved to the
employee's project subfolder for audit retention.

Public API:
    ensure_root_folder()                       -> folder_id
    ensure_master_subfolder()                  -> folder_id
    ensure_employee_folder(email)              -> folder_id        ("Last, First")
    ensure_project_subfolder(employee_id, code) -> folder_id       ("SMOKE")
    employee_display_name(email)               -> "Last, First"
    ensure_master_sheet(code, project_name)    -> sheet_id
    ensure_employee_project_sheet(email, code, project_name) -> sheet_id
    archive_to_project_folder(folder_id, content, mime, filename) -> webViewLink

All folder/sheet calls are idempotent — same lookup key returns the same
resource on repeat invocation.
"""

from __future__ import annotations

from typing import Optional

from logging_util import log


# Configurable root folder name. Default works out of the box; users with
# multiple AP workflows can override via config.json.
DEFAULT_ROOT_NAME = "AP Submissions"
MASTER_SUBFOLDER = "Master"
EMPLOYEE_SHEET_PREFIX = "Project Invoices — "

# Process-lifetime cache — Drive folder ids by name path. Cleared on
# server restart.
_FOLDER_CACHE: dict[str, str] = {}
_DISPLAY_NAME_CACHE: dict[str, str] = {}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _drive():
    import gservices
    return gservices.drive()


def _sheets():
    import gservices
    return gservices.sheets()


def _people():
    import gservices
    return gservices.people()


def _config_root_name() -> str:
    try:
        import config
        return (config.get("ap_drive_root_name") or DEFAULT_ROOT_NAME).strip()
    except Exception:
        return DEFAULT_ROOT_NAME


def _find_folder_in_parent(name: str, parent_id: Optional[str]) -> Optional[str]:
    """Look up an existing Drive folder by exact name + parent."""
    drive = _drive()
    parent_clause = f"and '{parent_id}' in parents" if parent_id else ""
    q = (
        f"mimeType = 'application/vnd.google-apps.folder' "
        f"and name = '{name.replace(chr(39), chr(92) + chr(39))}' "
        f"and trashed = false {parent_clause}"
    )
    resp = drive.files().list(
        q=q, pageSize=2, fields="files(id,name)",
    ).execute()
    files = resp.get("files", []) or []
    if files:
        return files[0]["id"]
    return None


def _create_folder(name: str, parent_id: Optional[str]) -> str:
    """Create a Drive folder under `parent_id` (or root if None) and
    return the new folder's id."""
    drive = _drive()
    body = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_id:
        body["parents"] = [parent_id]
    created = drive.files().create(body=body, fields="id,name").execute()
    return created["id"]


def _ensure_folder(name: str, parent_id: Optional[str]) -> str:
    """Get or create a folder. Returns the folder id. Cached per process."""
    cache_key = f"{parent_id or 'root'}::{name}"
    if cache_key in _FOLDER_CACHE:
        return _FOLDER_CACHE[cache_key]
    fid = _find_folder_in_parent(name, parent_id)
    if not fid:
        fid = _create_folder(name, parent_id)
    _FOLDER_CACHE[cache_key] = fid
    return fid


# --------------------------------------------------------------------------- #
# Folder hierarchy
# --------------------------------------------------------------------------- #


def ensure_root_folder() -> str:
    """Get or create the AP root folder at My Drive root.
    Returns the folder id."""
    return _ensure_folder(_config_root_name(), parent_id=None)


def ensure_master_subfolder() -> str:
    """Get or create AP Submissions/Master/. Returns folder id."""
    return _ensure_folder(MASTER_SUBFOLDER, parent_id=ensure_root_folder())


def ensure_employee_folder(employee_email: str) -> str:
    """Get or create AP Submissions/Last, First/.
    Returns folder id. Uses People API directory lookup with Gmail
    display-name fallback to derive 'Last, First' from the email."""
    if not employee_email:
        raise ValueError("employee_email is required")
    name = employee_display_name(employee_email)
    return _ensure_folder(name, parent_id=ensure_root_folder())


def ensure_project_subfolder(
    employee_folder_id: str,
    project_code: str,
) -> str:
    """Get or create AP Submissions/Last, First/<project_code>/.
    Returns folder id."""
    if not project_code:
        raise ValueError("project_code is required")
    return _ensure_folder(project_code.upper(), parent_id=employee_folder_id)


# Top-level "Reply Attachments" tree, parallel to the per-employee tree.
# When a vendor sends a missing W-9/COI/etc. as a reply to our follow-up,
# we drop it under: AP Submissions/Reply Attachments/<PROJECT>/<vendor>/.
REPLY_ATTACHMENTS_SUBFOLDER = "Reply Attachments"


def ensure_reply_attachments_folder(
    project_code: str,
    vendor_name: Optional[str] = None,
) -> str:
    """Get or create AP Submissions/Reply Attachments/<PROJECT>/<vendor>/.

    If vendor_name is empty/None, returns the project-level folder so the
    file lands at AP Submissions/Reply Attachments/<PROJECT>/.
    Returns the leaf folder id.
    """
    if not project_code:
        raise ValueError("project_code is required")
    root = ensure_root_folder()
    base = _ensure_folder(REPLY_ATTACHMENTS_SUBFOLDER, parent_id=root)
    project_folder = _ensure_folder(project_code.upper(), parent_id=base)
    if not vendor_name:
        return project_folder
    safe_vendor = vendor_name.strip().replace("/", "_")[:80] or "unknown-vendor"
    return _ensure_folder(safe_vendor, parent_id=project_folder)


# --------------------------------------------------------------------------- #
# Display-name resolution — "Last, First"
# --------------------------------------------------------------------------- #


def _last_first(full_name: str) -> str:
    """Convert a free-form display name to 'Last, First' format.

    'Joshua Szott'              → 'Szott, Joshua'
    'Joshua Szott (CEO)'        → 'Szott, Joshua'
    'finnn@surefox.com'         → 'Finnn'           (no last name available)
    'Smith Jr., John'           → 'Smith Jr., John' (already in form, keep)
    ''                          → 'Unknown'
    """
    if not full_name:
        return "Unknown"
    s = full_name.strip()
    # Strip trailing parenthetical (e.g. titles)
    if "(" in s:
        s = s.split("(", 1)[0].strip()
    s = s.strip("\"'").strip()
    if not s:
        return "Unknown"
    # Already in "Last, First" form? Keep it.
    if "," in s:
        return s
    parts = s.split()
    if len(parts) == 1:
        # Single token — capitalize and use as-is.
        return parts[0].capitalize()
    # Heuristic: last token is the surname, the rest is first/middle.
    last = parts[-1]
    rest = " ".join(parts[:-1])
    return f"{last}, {rest}"


def employee_display_name(email: str) -> str:
    """Resolve an employee email to 'Last, First' format. People API
    directory first, Gmail display-name fallback. Cached per process.
    Falls back to the local-part of the email if both lookups fail."""
    if not email:
        return "Unknown"
    e = email.strip().lower()
    if e in _DISPLAY_NAME_CACHE:
        return _DISPLAY_NAME_CACHE[e]

    # Tier 1: People API directory lookup — most authoritative.
    try:
        people = _people()
        resp = people.people().searchDirectoryPeople(
            query=e,
            readMask="names",
            sources=["DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE"],
        ).execute()
        for p in (resp.get("people") or []):
            for n in (p.get("names") or []):
                full = n.get("displayName") or ""
                if full:
                    name = _last_first(full)
                    _DISPLAY_NAME_CACHE[e] = name
                    return name
    except Exception as exc:
        # Falls through to email-based fallback below.
        # Logged at debug since this fallback chain is normal.
        log.debug("display_name resolution failed for %s: %s",
                  e, exc)

    # Tier 2: People API contacts search — for non-domain people.
    try:
        people = _people()
        resp = people.people().searchContacts(
            query=e, readMask="names,emailAddresses",
        ).execute()
        for r in (resp.get("results") or []):
            for n in (r.get("person", {}).get("names") or []):
                full = n.get("displayName") or ""
                if full:
                    name = _last_first(full)
                    _DISPLAY_NAME_CACHE[e] = name
                    return name
    except Exception:
        pass

    # Tier 3: fall back to email local-part, capitalized.
    local = e.split("@", 1)[0]
    fallback = local.replace(".", " ").replace("_", " ").title()
    _DISPLAY_NAME_CACHE[e] = fallback
    return fallback


# --------------------------------------------------------------------------- #
# Sheet placement
# --------------------------------------------------------------------------- #


def ensure_master_sheet(
    project_code: str, project_name: str,
    columns: list[str],
) -> str:
    """Get or create the master roll-up sheet for a project.
    Lives in AP Submissions/Master/. Returns sheet_id."""
    if not project_code:
        raise ValueError("project_code is required")
    title = f"{EMPLOYEE_SHEET_PREFIX}{project_code.upper()} — {project_name}"
    parent_id = ensure_master_subfolder()
    return _ensure_sheet_in_folder(title, parent_id, columns)


def ensure_employee_project_sheet(
    employee_email: str,
    project_code: str,
    project_name: str,
    columns: list[str],
) -> str:
    """Get or create the per-employee-per-project sheet.
    Lives in AP Submissions/Last, First/<code>/. Returns sheet_id."""
    if not employee_email or not project_code:
        raise ValueError("employee_email + project_code required")
    employee_folder = ensure_employee_folder(employee_email)
    project_folder = ensure_project_subfolder(employee_folder, project_code)
    last_first = employee_display_name(employee_email)
    title = (
        f"{EMPLOYEE_SHEET_PREFIX}{project_code.upper()} — "
        f"{project_name} [{last_first}]"
    )
    return _ensure_sheet_in_folder(title, project_folder, columns)


def _ensure_sheet_in_folder(
    title: str, parent_folder_id: str, columns: list[str],
) -> str:
    """Find an existing sheet by exact name in the given folder, or create
    a new one with `columns` as the header row. Returns sheet_id."""
    drive = _drive()
    safe_title = title.replace("'", "\\'")
    q = (
        f"mimeType = 'application/vnd.google-apps.spreadsheet' "
        f"and name = '{safe_title}' "
        f"and '{parent_folder_id}' in parents and trashed = false"
    )
    resp = drive.files().list(
        q=q, pageSize=2, fields="files(id,name)",
    ).execute()
    existing = resp.get("files", []) or []
    if existing:
        return existing[0]["id"]

    sheets = _sheets()
    created = sheets.spreadsheets().create(
        body={
            "properties": {"title": title},
            "sheets": [{"properties": {"title": "Invoices"}, "data": []}],
        },
    ).execute()
    new_id = created["spreadsheetId"]
    # Move into the target folder (Sheets.create puts files at root).
    drive.files().update(
        fileId=new_id,
        addParents=parent_folder_id,
        removeParents="root",
        fields="id,parents",
    ).execute()
    # Write header row.
    sheets.spreadsheets().values().update(
        spreadsheetId=new_id, range="A1",
        valueInputOption="RAW",
        body={"values": [columns]},
    ).execute()
    return new_id


# --------------------------------------------------------------------------- #
# Original-file archive
# --------------------------------------------------------------------------- #


def archive_to_project_folder(
    project_folder_id: str,
    content: bytes,
    mime_type: str,
    filename: str,
) -> str:
    """Upload an original PDF/image into the employee's project subfolder.
    Returns the file's webViewLink. Idempotent on filename — same name
    overwrites the prior copy (Drive treats name as a reusable label)."""
    from googleapiclient.http import MediaInMemoryUpload
    drive = _drive()

    # Look for an existing file with the same name in the same folder.
    safe_name = filename.replace("'", "\\'")
    q = (
        f"name = '{safe_name}' and '{project_folder_id}' in parents "
        f"and trashed = false"
    )
    resp = drive.files().list(
        q=q, pageSize=1, fields="files(id,webViewLink)",
    ).execute()
    existing = resp.get("files", []) or []
    media = MediaInMemoryUpload(content, mimetype=mime_type, resumable=False)
    if existing:
        updated = drive.files().update(
            fileId=existing[0]["id"], media_body=media,
            fields="id,webViewLink",
        ).execute()
        return updated.get("webViewLink", "")
    created = drive.files().create(
        body={"name": filename, "parents": [project_folder_id]},
        media_body=media,
        fields="id,name,webViewLink",
    ).execute()
    return created.get("webViewLink", "")


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #


def _reset_caches_for_tests() -> None:
    _FOLDER_CACHE.clear()
    _DISPLAY_NAME_CACHE.clear()
