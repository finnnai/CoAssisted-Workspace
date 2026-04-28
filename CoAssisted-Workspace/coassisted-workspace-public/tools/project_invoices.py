# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE.
"""Project-invoice extractor — extends the receipt pipeline for AP-style work.

Six tools, all paid-tier (uses Anthropic API):

  - workflow_register_project           — bootstrap a project + AP sheet.
  - workflow_list_projects              — registered projects + counts.
  - workflow_create_project_sheet       — re-create a project's sheet.
  - workflow_extract_project_invoices   — flagship: scan inbox + Drive +
                                          Chat, auto-classify receipt vs.
                                          invoice, route to per-project sheet.
  - workflow_move_invoice_to_project    — move a row between project sheets.
  - workflow_export_project_invoices_qb_csv — QB Bills-importable CSV per project.

Project resolution is delegated to project_registry.resolve(...). Invoice rows
that can't be resolved (confidence < 0.65) get parked in a 'Needs Project
Assignment' sheet and surfaced in the result so the user can move them via
workflow_move_invoice_to_project.
"""

from __future__ import annotations

import base64
import csv
import datetime as _dt
import io
import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import config
import gservices
import ap_drive_layout as _drive_layout
import project_invoices as _pi
import project_registry as _pr
import receipts as _r
import sender_classifier as _sc
import vendor_followups as _vf
from logging_util import log
from errors import format_error


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

# Naming convention for invoice sheets — mirrors RECEIPT_SHEET_PREFIX in
# tools/receipts.py. Em-dash + space avoids collision with hyphenated names.
PROJECT_SHEET_PREFIX = "Project Invoices — "

# Special "park here" sheet for unresolved invoices.
NEEDS_REVIEW_SHEET = "Project Invoices — Needs Project Assignment"


# --------------------------------------------------------------------------- #
# Input models
# --------------------------------------------------------------------------- #


class RegisterProjectInput(BaseModel):
    """Input for workflow_register_project."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    code: str = Field(
        ..., min_length=1, max_length=20,
        description="Short uppercase code, e.g. 'ALPHA'. Used as the project's "
                    "stable key in routing rules and the sheet title.",
    )
    name: str = Field(
        ..., min_length=1, max_length=120,
        description="Human-readable project name, e.g. 'Surefox HQ Build'.",
    )
    client: Optional[str] = Field(default=None, max_length=120)
    sender_emails: list[str] = Field(
        default_factory=list,
        description="Vendor emails that should auto-route to this project.",
    )
    chat_space_ids: list[str] = Field(
        default_factory=list,
        description="Gchat space IDs ('spaces/AAQA...') that should auto-route here.",
    )
    filename_patterns: list[str] = Field(
        default_factory=list,
        description="Regex patterns matched against attachment filenames "
                    "(e.g. '^INV-ALPHA-' or '(?i)\\\\balpha\\\\b').",
    )
    default_billable: bool = Field(default=True)
    default_markup_pct: float = Field(default=0.0, ge=0, le=200)
    currency: str = Field(default="USD")
    create_sheet: bool = Field(
        default=True,
        description="If True, also create the per-project AP sheet now.",
    )


class ListProjectsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    active_only: bool = Field(default=True)


class CreateProjectSheetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    code: str = Field(..., min_length=1)


class ExtractProjectInvoicesInput(BaseModel):
    """Input for the flagship invoice extraction workflow."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_code: Optional[str] = Field(
        default=None,
        description="If set, ALL extracted invoices route to this project. "
                    "If None, registry rules + LLM inference resolve per-invoice.",
    )
    days: int = Field(default=30, ge=1, le=365)
    max_emails_to_scan: int = Field(default=200, ge=1, le=2000)
    drive_folder_id: Optional[str] = None
    chat_space_id: Optional[str] = None
    chat_max_messages: int = Field(default=200, ge=1, le=2000)
    skip_low_confidence: bool = Field(default=False)
    classify_threshold: float = Field(
        default=0.6, ge=0, le=1,
        description="Min classifier confidence to treat a doc as an invoice. "
                    "Below this, the doc is skipped (or routed to receipts).",
    )
    min_total: float = Field(
        default=1.00, ge=0,
        description="Sanity floor on extracted invoice total. Rows with "
                    "total < this go to 'Needs Review' instead of the project "
                    "sheet — they're almost always misparses or test emails.",
    )
    max_total: float = Field(
        default=250000.00, ge=0,
        description="Sanity ceiling on extracted invoice total. Rows with "
                    "total > this go to 'Needs Review' so an annual benefits "
                    "statement or YTD spend summary can't masquerade as one "
                    "huge invoice.",
    )
    require_invoice_number: bool = Field(
        default=True,
        description="If True (default), invoices missing an extracted "
                    "invoice_number get routed to 'Needs Review' regardless "
                    "of project resolution. The classifier already filters "
                    "out most number-less docs; this is the second gate.",
    )
    request_missing_info: bool = Field(
        default=True,
        description="If True (default), when the quality guard fires we "
                    "auto-reply to the original sender on the same channel "
                    "(Gmail thread or Chat space) asking for the specific "
                    "missing fields, mark the row AWAITING_INFO, and route "
                    "to the project sheet. If False, fail rows just park in "
                    "Needs Review with no outbound message.",
    )


class SendVendorRemindersInput(BaseModel):
    """Input for workflow_send_vendor_reminders — bulk reminder loop."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    max_to_send: int = Field(
        default=20, ge=1, le=200,
        description="Hard cap on reminders this run.",
    )
    channel: Optional[str] = Field(
        default=None,
        description="Filter to one channel ('gmail' or 'chat'). None = both.",
    )


class ProcessVendorRepliesInput(BaseModel):
    """Input for workflow_process_vendor_replies — scan for replies."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    max_to_process: int = Field(
        default=50, ge=1, le=500,
        description="Hard cap on outstanding requests inspected this run.",
    )


class MigrateProjectSheetsToApLayoutInput(BaseModel):
    """Input for workflow_migrate_project_sheets_to_ap_layout."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_code: Optional[str] = Field(
        default=None,
        description="If set, migrate only this project. Otherwise migrate "
                    "every registered project's sheet that's still at "
                    "Drive root.",
    )
    dry_run: bool = Field(
        default=False,
        description="If True, report what WOULD move without touching Drive.",
    )


class MoveInvoiceToProjectInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    from_project_code: str = Field(..., min_length=1)
    to_project_code: str = Field(..., min_length=1)
    content_key: Optional[str] = Field(
        default=None,
        description="Identify the row by its content_key (vendor|invoice#|cents). "
                    "Mutually exclusive with row_number.",
    )
    row_number: Optional[int] = Field(
        default=None, ge=2,
        description="Sheet row number. Use when content_key is missing/ambiguous.",
    )


class ExportProjectInvoicesQbCsvInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_code: str = Field(..., min_length=1)
    save_to_drive_folder_id: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    statuses: list[str] = Field(
        default_factory=lambda: ["OPEN", "APPROVED"],
        description="Only include rows whose status is in this list.",
    )


class ExtractProjectReceiptsInput(BaseModel):
    """Input for workflow_extract_project_receipts — receipt sibling to the
    invoice extractor. Writes into the same per-project sheet as invoices,
    with doc_type='receipt'."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_code: Optional[str] = Field(
        default=None,
        description="If set, ALL extracted receipts route to this project. "
                    "If None, registry rules + LLM inference resolve per-receipt.",
    )
    days: int = Field(default=30, ge=1, le=365)
    max_emails_to_scan: int = Field(default=200, ge=1, le=2000)
    drive_folder_id: Optional[str] = None
    chat_space_id: Optional[str] = None
    chat_max_messages: int = Field(default=200, ge=1, le=2000)
    skip_low_confidence: bool = Field(default=False)


# --------------------------------------------------------------------------- #
# Service shortcuts
# --------------------------------------------------------------------------- #


def _gmail():
    return gservices.gmail()


def _drive():
    return gservices.drive()


def _sheets():
    return gservices.sheets()


def _chat():
    return gservices.chat()


# --------------------------------------------------------------------------- #
# Sheet helpers
# --------------------------------------------------------------------------- #


def _sheet_title_for_project(code: str, project_name: str) -> str:
    """Canonical sheet title — '<prefix><code> — <name>'."""
    return f"{PROJECT_SHEET_PREFIX}{code} — {project_name}"


def _ensure_project_sheet(code: str) -> tuple[str, str]:
    """Create or reuse the MASTER AP sheet for a project.

    Routed through ap_drive_layout — the master sheet lives in
    AP Submissions/Master/. This is the roll-up that aggregates all
    employees' submissions to the project. Returns (sheet_id, title).

    The project registry's `sheet_id` field tracks this master sheet.
    Existing root-level sheets from before the AP-folder layout was
    introduced are NOT migrated automatically — they stay where they
    are; new submissions land in the new structure.
    """
    proj = _pr.get(code)
    if not proj:
        raise ValueError(f"unknown project_code='{code}'")

    cached_id = proj.get("sheet_id")
    if cached_id:
        try:
            meta = _sheets().spreadsheets().get(spreadsheetId=cached_id).execute()
            return cached_id, meta.get("properties", {}).get("title", "")
        except Exception:
            # The stored sheet was deleted/revoked — fall through and re-create.
            cached_id = None

    name = proj.get("name") or code
    new_id = _drive_layout.ensure_master_sheet(
        code, name, _pi.PROJECT_SHEET_COLUMNS,
    )
    title = _sheet_title_for_project(code, name)
    _pr.register(
        code=code, name=name,
        sheet_id=new_id, sheet_name=title,
    )
    return new_id, title


def _ensure_employee_project_sheet(
    code: str, employee_email: str,
) -> tuple[str, str]:
    """Get/create the per-employee-per-project sheet under
    AP Submissions/Last, First/<code>/. Returns (sheet_id, title)."""
    proj = _pr.get(code)
    if not proj:
        raise ValueError(f"unknown project_code='{code}'")
    name = proj.get("name") or code
    sid = _drive_layout.ensure_employee_project_sheet(
        employee_email, code, name, _pi.PROJECT_SHEET_COLUMNS,
    )
    title = (
        f"{_drive_layout.EMPLOYEE_SHEET_PREFIX}{code.upper()} — "
        f"{name} [{_drive_layout.employee_display_name(employee_email)}]"
    )
    return sid, title


def _dual_write_row(
    *,
    code: str,
    employee_email: Optional[str],
    row: list,
) -> dict:
    """Append a row to BOTH the master sheet and the per-employee-per-
    project sheet. When employee_email is None (unresolved sender),
    writes to the master only. Returns dict with sheet_ids written.

    Each side is its own try/except — a failure on one doesn't block
    the other.
    """
    written = {"master_sheet_id": None, "employee_sheet_id": None,
               "errors": []}
    # Master.
    try:
        master_id, _t = _ensure_project_sheet(code)
        _append_invoice_row(master_id, row)
        written["master_sheet_id"] = master_id
    except Exception as e:
        log.warning("dual_write master append failed (%s): %s", code, e)
        written["errors"].append(f"master: {e}")

    # Per-employee.
    if employee_email:
        try:
            emp_id, _t = _ensure_employee_project_sheet(code, employee_email)
            _append_invoice_row(emp_id, row)
            written["employee_sheet_id"] = emp_id
        except Exception as e:
            log.warning(
                "dual_write employee append failed (%s, %s): %s",
                code, employee_email, e,
            )
            written["errors"].append(f"employee: {e}")
    return written


def _ensure_needs_review_sheet() -> tuple[str, str]:
    """Get/create the 'Needs Project Assignment' parking sheet."""
    drive = _drive()
    query = (
        "mimeType = 'application/vnd.google-apps.spreadsheet' and "
        f"name = '{NEEDS_REVIEW_SHEET}' and trashed = false"
    )
    resp = drive.files().list(
        q=query, pageSize=1, fields="files(id,name)",
    ).execute()
    files = resp.get("files", []) or []
    if files:
        return files[0]["id"], files[0]["name"]
    sheets = _sheets()
    created = sheets.spreadsheets().create(
        body={
            "properties": {"title": NEEDS_REVIEW_SHEET},
            "sheets": [{"properties": {"title": "Invoices"}, "data": []}],
        },
    ).execute()
    sheet_id = created["spreadsheetId"]
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id, range="A1",
        valueInputOption="RAW",
        body={"values": [_pi.PROJECT_SHEET_COLUMNS]},
    ).execute()
    return sheet_id, NEEDS_REVIEW_SHEET


def _existing_invoice_keys(sheet_id: str) -> tuple[set[str], set[str]]:
    """Return (source_ids, content_keys) already logged. For dedup."""
    try:
        sheets = _sheets()
        # AA covers up to 27 columns (PROJECT_SHEET_COLUMNS).
        resp = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range="A2:AA",
        ).execute()
        rows = resp.get("values", []) or []
    except Exception:
        return set(), set()

    # Look up indices once from the schema so column shifts (like the
    # doc_type insertion) don't silently break dedup.
    sid_idx = _pi.PROJECT_SHEET_COLUMNS.index("source_id")
    ck_idx = _pi.PROJECT_SHEET_COLUMNS.index("content_key")

    source_ids: set[str] = set()
    content_keys: set[str] = set()
    for row in rows:
        if len(row) > sid_idx and row[sid_idx]:
            source_ids.add(row[sid_idx])
        if len(row) > ck_idx and row[ck_idx]:
            content_keys.add(row[ck_idx])
    return source_ids, content_keys


def _append_invoice_row(sheet_id: str, row: list) -> None:
    _sheets().spreadsheets().values().append(
        spreadsheetId=sheet_id, range="A:AA",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()


# --------------------------------------------------------------------------- #
# Resolution + finalization
# --------------------------------------------------------------------------- #


_BRAND_VOICE_PATH = "brand-voice.md"


def _load_brand_voice() -> str:
    """Read brand-voice.md if present. Empty string if missing — the LLM
    falls back to a neutral polite tone in that case."""
    try:
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / _BRAND_VOICE_PATH
        if p.exists():
            return p.read_text(encoding="utf-8")[:4000]  # cap prompt cost
    except Exception:
        pass
    return ""


_FIELD_PROMPT_LABELS = {
    "invoice_number":  "Invoice number (as printed on the invoice)",
    "po_number":       "PO / purchase-order number (if applicable)",
    "invoice_date":    "Invoice issue date (YYYY-MM-DD)",
    "due_date":        "Payment due date (YYYY-MM-DD)",
    "total":           "Total amount due (with currency)",
    "subtotal":        "Subtotal before tax",
    "tax":             "Tax amount",
    "payment_terms":   "Payment terms (e.g. Net 30, Due on receipt)",
    "remit_to":        "Remit-to address (where to send payment)",
    "vendor":          "Vendor / company legal name",
    "project_code":    "Project this should be billed to (see options below)",
}


def _missing_field_list(
    inv: _pi.ExtractedInvoice,
    *,
    project_resolved: bool = True,
) -> list[str]:
    """Decide which fields to ask the vendor about. Always-required core
    plus any obvious gaps, plus project_code when the orchestrator couldn't
    resolve it from registry rules. Order matters — most important first."""
    missing: list[str] = []
    if not (inv.invoice_number or "").strip():
        missing.append("invoice_number")
    if inv.total is None:
        missing.append("total")
    if not (inv.invoice_date or "").strip():
        missing.append("invoice_date")
    if not (inv.due_date or "").strip() and not (inv.payment_terms or "").strip():
        # Either due_date OR payment_terms is fine — only ask if both blank.
        missing.append("due_date")
    if not (inv.vendor or "").strip():
        missing.append("vendor")
    if not project_resolved:
        missing.append("project_code")
    return missing


def _project_picker_block() -> str:
    """Render the active-project list as a compact block for the outbound
    request. Plain text — the composer wraps it in markup as needed.
    Returns empty string when no projects are registered."""
    try:
        rows = _pr.list_all(active_only=True)
    except Exception:
        return ""
    if not rows:
        return ""
    lines = []
    for r in rows[:25]:  # cap so the email doesn't get unwieldy
        client = f" (client: {r.get('client')})" if r.get("client") else ""
        lines.append(f"  • {r['code']} — {r.get('name', '')}{client}")
    extra = (
        f"\n  …and {len(rows) - 25} more — reply 'all projects' for the full list."
        if len(rows) > 25 else ""
    )
    return "\n".join(lines) + extra


def _greeting_name(recipient_name: Optional[str]) -> str:
    """Pick a first-name greeting from a free-form display name.

    'Alice Smith'      → 'Alice'
    'Alice Smith (CEO)'→ 'Alice'
    'alice@example.com'→ ''  (no greeting personalization for raw emails)
    None / ''          → ''
    """
    if not recipient_name:
        return ""
    s = recipient_name.strip()
    # Strip trailing parenthetical suffix.
    if "(" in s:
        s = s.split("(", 1)[0].strip()
    # Strip quotes if Gmail wrapped the display name.
    s = s.strip("\"'")
    if "@" in s:
        return ""  # raw email, no name
    parts = s.split()
    return parts[0] if parts else ""


def _compose_info_request(
    inv: _pi.ExtractedInvoice,
    missing_fields: list[str],
    *,
    is_reminder: bool = False,
    reminder_count: int = 0,
    audience: str = "vendor",
    recipient_name: Optional[str] = None,
) -> dict:
    """Build the outbound request body. Returns {'subject', 'plain', 'html'}.

    `audience` flips tone:
      - 'vendor'   (default): polite, customer-relationship language. Reserved
                              for the rare case the user wants to message a
                              vendor; in normal AP flows MCP never auto-replies
                              to externals — the orchestrator parks instead.
      - 'employee': direct accountability tone for internal employees who
                    submitted an incomplete invoice. They're paid to be
                    accurate, so the ask is brisker and clearer about what's
                    blocking the payment.

    Uses Claude with the user's brand-voice.md as context so the tone matches
    their existing outbound mail. Falls back to a deterministic default if the
    LLM is unavailable — the message still ships, it just sounds generic.

    Also surfaces project context so the recipient knows which job we're
    tracking this against and can correct/pick if needed.
    """
    is_employee = audience == "employee"
    field_lines = "\n".join(
        f"  • {_FIELD_PROMPT_LABELS.get(f, f)}"
        for f in missing_fields
    )

    invoice_summary = []
    if inv.vendor:
        invoice_summary.append(f"Vendor: {inv.vendor}")
    if inv.total:
        invoice_summary.append(f"Total we found: {inv.currency} {inv.total}")
    if inv.invoice_date:
        invoice_summary.append(f"Date we found: {inv.invoice_date}")
    summary_block = "\n".join(invoice_summary) or "(no fields parsed)"

    # Project context — what we routed to, what alternatives exist.
    project_picker = _project_picker_block()
    proj_resolved_to = None
    if inv.project_code:
        try:
            proj_rec = _pr.get(inv.project_code)
            if proj_rec:
                proj_resolved_to = (
                    f"{inv.project_code} — {proj_rec.get('name', '')}"
                )
        except Exception:
            pass
    project_block = ""
    if proj_resolved_to:
        project_block = (
            f"\nProject we're tracking this against: {proj_resolved_to}\n"
            "If this should be billed to a different project, just say which "
            "in your reply."
        )
    elif project_picker:
        project_block = (
            "\nWe weren't sure which project this is for — could you let "
            "us know? Active projects:\n"
            f"{project_picker}\n"
            "Reply with the project code (e.g. 'ALPHA') or the name."
        )

    nudge_clause = (
        " (just a quick follow-up to the note I sent earlier)"
        if is_reminder else ""
    )

    # LLM-composed brand-voiced version — preferred path
    try:
        import llm
        ok, _ = llm.is_available()
        if ok:
            brand = _load_brand_voice()
            brand_block = (
                f"BRAND VOICE GUIDELINES (mirror this tone):\n{brand}\n\n"
                if brand else ""
            )
            audience_note = (
                "AUDIENCE: This is going to an EMPLOYEE who submitted an "
                "incomplete invoice for AP processing. They're paid to be "
                "accurate, so be direct and clear about what's blocking the "
                "payment — no over-apologizing, no soft-pedaling. Brisk and "
                "professional. Two-three sentences max plus the bullet list."
                if is_employee else
                "AUDIENCE: This is going to a vendor. Conversational, warm, "
                "polite. No jargon."
            )
            first_name = _greeting_name(recipient_name)
            greeting_clause = (
                f"Open with 'Hi {first_name},'. "
                if first_name else
                "Open with a generic greeting (no name)."
            )
            prompt = (
                f"{brand_block}"
                f"{audience_note}\n\n"
                f"{greeting_clause}"
                "Write a short message asking for a few missing fields on an "
                "invoice. Address them as 'you'. Don't sign off with a name — "
                "the client adds the signature.\n\n"
                "MUST INCLUDE — keep these as a labeled block in the body so "
                "the recipient sees what we already extracted (don't paraphrase, "
                "render verbatim):\n"
                f"What I have so far:\n{summary_block}\n"
                f"{project_block}\n\n"
                "What's missing — list these as bullet points in the body "
                "exactly as they appear here:\n"
                f"{field_lines}\n\n"
                "Make sure the project context above is included clearly so "
                "they can confirm or correct it.\n\n"
                "Close with: 'Just reply line-by-line on this thread — "
                "I'll pick it up automatically.'\n\n"
                f"This is{' a quick nudge — ' if is_reminder else ' the first ask. '}"
                f"{'reminder #' + str(reminder_count) if is_reminder else ''}\n\n"
                "Return ONLY a JSON object — no prose, no fences:\n"
                '{"subject": "...", "plain": "...", "html": "<full HTML body>"}'
            )
            resp = llm.call_simple(prompt, max_tokens=900, temperature=0.4)
            text = (resp.get("text") or "").strip()
            if text.startswith("```"):
                import re as _re
                text = _re.sub(r"^```(?:json)?\s*\n?", "", text)
                text = _re.sub(r"\n?```\s*$", "", text)
            data = json.loads(text)
            if all(k in data for k in ("subject", "plain", "html")):
                return data
    except Exception as e:
        log.warning("brand-voice composition failed, using fallback: %s", e)

    # Deterministic fallback — tone shifts with audience.
    first_name = _greeting_name(recipient_name)
    greet = f"Hi {first_name}" if first_name else "Hi there"
    if is_employee:
        subj = (
            f"AP submission needs a couple of fields{nudge_clause} — "
            f"{inv.vendor or 'invoice'}"
        )
        plain = (
            f"{greet}{nudge_clause},\n\n"
            "I can't push this through to AP without the following — "
            "could you grab them and reply here?\n\n"
            f"{field_lines}\n\n"
            f"What I have so far:\n{summary_block}\n"
            f"{project_block}\n\n"
            "Just reply line-by-line on this thread — I'll pick it up "
            "automatically.\n\n"
            "Thanks!"
        )
    else:
        subj = (
            f"Invoice follow-up{nudge_clause}: missing details from "
            f"{inv.vendor or 'your invoice'}"
        )
        plain = (
            f"{greet}{nudge_clause},\n\n"
            "Thanks for sending the invoice. Before I can get this routed "
            "for payment, I need a few details that weren't on the document "
            "I received:\n\n"
            f"{field_lines}\n\n"
            f"What I have so far:\n{summary_block}\n"
            f"{project_block}\n\n"
            "Just reply line-by-line on this thread — I'll pick it up "
            "automatically.\n\n"
            "Thanks!"
        )
    project_html = ""
    if proj_resolved_to:
        project_html = (
            f"<p><b>Project we're tracking this against:</b> "
            f"{proj_resolved_to}<br>"
            "If this should be billed to a different project, just say which "
            "in your reply.</p>"
        )
    elif project_picker:
        # Render the picker as an HTML list
        picker_items = "".join(
            f"<li>{line.strip().lstrip('•').strip()}</li>"
            for line in project_picker.split("\n")
            if line.strip().startswith("•") or line.strip().startswith("…")
        )
        project_html = (
            "<p><b>We weren't sure which project this is for — could you "
            "let us know?</b> Active projects:</p>"
            f"<ul>{picker_items}</ul>"
            "<p>Reply with the project code (e.g. <code>ALPHA</code>) "
            "or the name.</p>"
        )
    html = (
        f"<p>{greet}{nudge_clause},</p>"
        + (
            "<p>I can't push this through to AP without the following — "
            "could you grab them and reply here?</p>"
            if is_employee else
            "<p>Thanks for sending the invoice. Before I can get this routed "
            "for payment, I need a few details that weren't on the document "
            "I received:</p>"
        )
        + "<ul>"
        + "".join(
            f"<li>{_FIELD_PROMPT_LABELS.get(f, f)}</li>"
            for f in missing_fields
        )
        + "</ul>"
        f"<p><b>What I have so far:</b><br>{summary_block.replace(chr(10), '<br>')}</p>"
        f"{project_html}"
        "<p>Just reply line-by-line on this thread — I'll pick it up "
        "automatically.</p>"
        "<p>Thanks!</p>"
    )
    return {"subject": subj, "plain": plain, "html": html}


def _send_info_request_via_gmail(
    *, thread_id: str, to: str, subject: str, plain: str, html: str,
) -> tuple[bool, Optional[str]]:
    """Reply on the original Gmail thread. Returns (sent, error_msg).

    On failure the second element carries the actual error reason (HTTP
    status + Google's error string when available, else the exception
    text) so the caller can stamp it into the row's notes — silent
    failures were the whole reason a recent reply attempt to a real
    employee dropped on the floor.
    """
    try:
        import base64
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        gmail = _gmail()
        msg = MIMEMultipart("alternative")
        msg["To"] = to
        msg["Subject"] = subject
        plain_with_marker = (
            plain + "\n\n--\n" + _r.BOT_FOOTER_MARKER
        )
        html_with_marker = (
            html + f"<hr><small>{_r.BOT_FOOTER_MARKER}</small>"
        )
        msg.attach(MIMEText(plain_with_marker, "plain"))
        msg.attach(MIMEText(html_with_marker, "html"))
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        gmail.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": thread_id},
        ).execute()
        return True, None
    except Exception as e:
        # Pull the most descriptive string we can from googleapiclient's
        # HttpError so logs and notes don't just say 'HttpError'.
        err = str(e)
        try:
            from googleapiclient.errors import HttpError
            if isinstance(e, HttpError):
                content = e.content.decode("utf-8", errors="replace")
                err = (
                    f"HttpError {e.resp.status if hasattr(e, 'resp') else '?'} "
                    f"on threadId={thread_id!r} to={to!r}: {content[:400]}"
                )
        except Exception:
            pass
        log.warning("info-request gmail send failed: %s", err)
        return False, err


def _send_info_request_via_chat(
    *, space_name: str, message_text: str,
) -> tuple[bool, Optional[str]]:
    """Post a message in the original Chat space. Returns (sent, error_msg).

    Chat doesn't support inline HTML the way email does, so we send the
    plain-text variant with the field bullets rendered as ASCII bullets.
    """
    try:
        chat = _chat()
        body_with_marker = message_text + "\n\n— " + _r.BOT_FOOTER_MARKER
        chat.spaces().messages().create(
            parent=space_name,
            body={"text": body_with_marker},
        ).execute()
        return True, None
    except Exception as e:
        err = str(e)
        try:
            from googleapiclient.errors import HttpError
            if isinstance(e, HttpError):
                content = e.content.decode("utf-8", errors="replace")
                err = (
                    f"HttpError {e.resp.status if hasattr(e, 'resp') else '?'} "
                    f"on space={space_name!r}: {content[:400]}"
                )
        except Exception:
            pass
        log.warning("info-request chat send failed: %s", err)
        return False, err


def _send_info_request_via_employee_dm(
    *, employee_email: str, message_text: str,
) -> tuple[bool, Optional[str], Optional[str]]:
    """DM the employee directly via Google Chat.
    Returns (sent, space_name, error_msg).

    `find_or_create_dm` is idempotent — same DM space each call for the same
    person — so over time MCP maintains a 'default chat open with each
    employee' for AP follow-ups.

    error_msg carries the actual reason on failure (HttpError details when
    available) so the orchestrator can log + stamp into row notes. The
    caller is expected to fall back to Gmail-thread reply on (False, None, _).
    """
    if not employee_email:
        return False, None, "no_employee_email"
    try:
        chat = _chat()
        # find_or_create_dm by user email lookup → People API resourceName.
        # gservices exposes a thin wrapper, but we can call People directly
        # to keep this self-contained.
        people = gservices.people()
        # People search by email is the most reliable; fall back to direct
        # findDirectMessage if we already have a resource name.
        resp = people.people().searchContacts(
            query=employee_email,
            readMask="names,emailAddresses,metadata",
        ).execute()
        results = resp.get("results", []) or []
        person_resource = None
        for r in results:
            for e in (r.get("person", {}).get("emailAddresses") or []):
                if (e.get("value") or "").lower() == employee_email.lower():
                    person_resource = r["person"]["resourceName"]
                    break
            if person_resource:
                break

        # Some directories don't return contacts; try directory lookup.
        if not person_resource:
            try:
                resp2 = people.people().searchDirectoryPeople(
                    query=employee_email,
                    readMask="names,emailAddresses",
                    sources=["DIRECTORY_SOURCE_TYPE_DOMAIN_PROFILE"],
                ).execute()
                for r in (resp2.get("people") or []):
                    for e in (r.get("emailAddresses") or []):
                        if (e.get("value") or "").lower() == employee_email.lower():
                            person_resource = r["resourceName"]
                            break
                    if person_resource:
                        break
            except Exception:
                pass

        if not person_resource:
            err = (
                f"could not resolve {employee_email!r} to a People resource "
                "(not in contacts or directory)"
            )
            log.warning("employee DM: %s", err)
            return False, None, err

        # Chat user resource: people/N → users/N
        chat_user = "users/" + person_resource.split("/", 1)[1]

        space = chat.spaces().findDirectMessage(name=chat_user).execute()
        if not space or not space.get("name"):
            # No DM yet — create one. Chat has spaces.create for DMs.
            space = chat.spaces().create(
                body={
                    "spaceType": "DIRECT_MESSAGE",
                    "singleUserBotDm": False,
                    "members": [
                        {"member": {"name": chat_user, "type": "HUMAN"}},
                    ],
                },
            ).execute()
        space_name = space.get("name")
        if not space_name:
            return False, None, "chat_space_create_returned_no_name"

        body_with_marker = message_text + "\n\n— " + _r.BOT_FOOTER_MARKER
        chat.spaces().messages().create(
            parent=space_name,
            body={"text": body_with_marker},
        ).execute()
        return True, space_name, None
    except Exception as e:
        err = str(e)
        try:
            from googleapiclient.errors import HttpError
            if isinstance(e, HttpError):
                content = e.content.decode("utf-8", errors="replace")
                err = (
                    f"HttpError {e.resp.status if hasattr(e, 'resp') else '?'} "
                    f"for {employee_email!r}: {content[:400]}"
                )
        except Exception:
            pass
        log.warning("employee DM send failed for %s: %s", employee_email, err)
        return False, None, err


def _validate_invoice_quality(
    inv: _pi.ExtractedInvoice,
    *,
    min_total: float,
    max_total: float,
    require_invoice_number: bool,
) -> Optional[str]:
    """Post-extraction sanity check. Returns a 'park reason' string if the
    invoice fails any guard (caller should route to Needs Review and stamp
    the reason into notes), or None if the row is clean.

    Guards:
      - missing_invoice_number: extractor returned None for invoice_number
      - total_below_min:        amount looks like a misparse / test row
      - total_above_max:        amount looks like an annual / YTD summary
      - missing_total:          extractor couldn't find a grand total
    """
    if require_invoice_number and not (inv.invoice_number or "").strip():
        return "missing_invoice_number"
    if inv.total is None:
        return "missing_total"
    try:
        t = float(inv.total)
    except (TypeError, ValueError):
        return "missing_total"
    if t < min_total:
        return f"total_below_min={t}"
    if t > max_total:
        return f"total_above_max={t}"
    return None


def _finalize_invoice(
    inv: _pi.ExtractedInvoice,
    *,
    project_code_hint: Optional[str],
    filename: Optional[str],
    sender_email: Optional[str],
    chat_space_id: Optional[str],
) -> tuple[_pi.ExtractedInvoice, _pr.ResolveResult]:
    """Resolve the project, apply project defaults, compute markup amount.

    Returns (invoice, resolve_result). The invoice's project_code is set
    when resolved with confidence ≥ RESOLVE_THRESHOLD; otherwise it stays
    None so the caller routes to the Needs Review sheet.
    """
    # The invoice text we feed to the LLM is the body we already extracted.
    # Use vendor + line item descriptions + notes as a compact summary so
    # the inference call stays cheap.
    inv_summary_parts: list[str] = []
    if inv.vendor:
        inv_summary_parts.append(f"Vendor: {inv.vendor}")
    if inv.line_items:
        items = "; ".join(
            (li.description or "")[:80] for li in inv.line_items[:5]
        )
        if items:
            inv_summary_parts.append(f"Lines: {items}")
    if inv.notes:
        inv_summary_parts.append(f"Notes: {inv.notes[:200]}")
    inv_summary = " | ".join(inv_summary_parts) or (inv.vendor or "")

    rr = _pr.resolve(
        project_code_hint=project_code_hint,
        filename=filename,
        sender_email=sender_email,
        chat_space_id=chat_space_id,
        invoice_text=inv_summary,
    )

    if rr.project_code and rr.confidence >= _pr.RESOLVE_THRESHOLD:
        inv.project_code = rr.project_code
        proj = _pr.get(rr.project_code)
        if proj:
            # Apply project defaults — only if the LLM didn't already decide.
            # billable defaults from project; markup_pct overlays on top.
            inv.billable = bool(proj.get("default_billable", True))
            inv.markup_pct = float(proj.get("default_markup_pct") or 0.0)

    inv.compute_invoiceable_amount()
    return inv, rr


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:

    # ------------------------------------------------------------------ #
    # 1) workflow_register_project
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="workflow_register_project",
        annotations={
            "title": "Register a project for invoice routing + create its AP sheet",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_register_project(params: RegisterProjectInput) -> str:
        """Register a project so the invoice extractor can auto-route invoices
        to it. Re-registering a known code merges new sender_emails / patterns
        rather than overwriting.

        If `create_sheet=True` (default), also creates the per-project AP
        sheet titled 'Project Invoices — <CODE> — <Name>' with the canonical
        invoice columns.
        """
        try:
            rec = _pr.register(
                code=params.code,
                name=params.name,
                client=params.client,
                sender_emails=params.sender_emails,
                chat_space_ids=params.chat_space_ids,
                filename_patterns=params.filename_patterns,
                default_billable=params.default_billable,
                default_markup_pct=params.default_markup_pct,
                currency=params.currency,
            )
            sheet_info = None
            if params.create_sheet:
                sheet_id, sheet_title = _ensure_project_sheet(params.code)
                sheet_info = {
                    "sheet_id": sheet_id,
                    "title": sheet_title,
                    "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit",
                }
                rec = _pr.get(params.code) or rec
            return json.dumps(
                {"status": "ok", "project": rec, "sheet": sheet_info},
                indent=2,
            )
        except Exception as e:
            return format_error("workflow_register_project", e)

    # ------------------------------------------------------------------ #
    # 2) workflow_list_projects
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="workflow_list_projects",
        annotations={
            "title": "List registered projects + invoice counts",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def workflow_list_projects(params: ListProjectsInput) -> str:
        """Inventory of all registered projects. Includes routing rules
        (sender emails, chat spaces, filename patterns) and invoice counts."""
        try:
            rows = _pr.list_all(active_only=params.active_only)
            return json.dumps(
                {
                    "status": "ok",
                    "count": len(rows),
                    "projects": rows,
                },
                indent=2,
            )
        except Exception as e:
            return format_error("workflow_list_projects", e)

    # ------------------------------------------------------------------ #
    # 3) workflow_create_project_sheet
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="workflow_create_project_sheet",
        annotations={
            "title": "Create (or re-create) the AP sheet for a registered project",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_create_project_sheet(params: CreateProjectSheetInput) -> str:
        """Create the per-project invoice sheet. Idempotent — if the registry
        already has a working sheet_id for this project, returns its info
        unchanged. Use this when you've deleted the original sheet and need
        to rebuild."""
        try:
            sheet_id, title = _ensure_project_sheet(params.code)
            return json.dumps(
                {
                    "status": "ok",
                    "project_code": params.code.upper(),
                    "sheet_id": sheet_id,
                    "title": title,
                    "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit",
                },
                indent=2,
            )
        except Exception as e:
            return format_error("workflow_create_project_sheet", e)

    # ------------------------------------------------------------------ #
    # 4) workflow_extract_project_invoices  (flagship)
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="workflow_extract_project_invoices",
        annotations={
            "title": "Flagship: scan inbox + Drive + Chat for project invoices",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_extract_project_invoices(
        params: ExtractProjectInvoicesInput,
    ) -> str:
        """Flagship workflow for project-tracked vendor invoices.

        1. Scans inbox + optional Drive folder + optional Chat space for
           invoice-shaped documents.
        2. Auto-classifies each candidate as receipt vs invoice (cheap
           heuristic). Receipts are skipped here — use the receipt extractor.
        3. Extracts vendor + invoice_number + due_date + line items via LLM.
        4. Resolves project_code via the 5-tier registry ladder.
        5. Routes each invoice to its project's AP sheet (or 'Needs Project
           Assignment' if unresolved). Per-project sheets are auto-created
           if missing.
        6. Dedupes by source_id AND vendor|invoice_number content key.
        """
        try:
            import llm
            ok, reason = llm.is_available()
            if not ok:
                return json.dumps({
                    "status": "no_llm",
                    "reason": reason,
                    "fix": (
                        'Set "anthropic_api_key" in config.json. Invoice '
                        "extraction requires LLM access."
                    ),
                }, indent=2)

            now_iso = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
            results = {
                "scanned": 0,
                "invoices_extracted": 0,
                "skipped_not_invoice": 0,
                "skipped_dup": 0,
                "skipped_dup_content": 0,
                "skipped_low_conf": 0,
                "parked_needs_review": 0,
                "parked_quality_failed": 0,  # missing #/total or out-of-range
                "errors": 0,
                "by_project": {},  # project_code → count
            }

            # Per-sheet dedup caches keyed by sheet_id, so we don't re-fetch
            # the same sheet's rows on every invoice routed to it.
            sheet_cache: dict[str, dict] = {}

            def _get_sheet_caches(sheet_id: str) -> dict:
                if sheet_id not in sheet_cache:
                    sids, cks = _existing_invoice_keys(sheet_id)
                    sheet_cache[sheet_id] = {
                        "source_ids": sids,
                        "content_keys": cks,
                        "rows_appended": 0,
                    }
                return sheet_cache[sheet_id]

            new_records: list[dict] = []

            # --- INBOX --------------------------------------------------- #
            gmail = _gmail()
            search_q = (
                f"newer_than:{params.days}d (invoice OR \"due date\" OR "
                "\"net 30\" OR \"net 15\" OR \"remit to\" OR \"bill to\" "
                "OR \"po number\")"
            )
            try:
                msgs_resp = gmail.users().messages().list(
                    userId="me", q=search_q,
                    maxResults=min(params.max_emails_to_scan, 100),
                ).execute()
            except Exception as e:
                log.warning("invoice inbox search failed: %s", e)
                msgs_resp = {"messages": []}

            for entry in (msgs_resp.get("messages") or [])[:params.max_emails_to_scan]:
                results["scanned"] += 1
                mid = entry.get("id")
                if not mid:
                    continue
                source_id = f"gmail:{mid}"

                try:
                    msg = gmail.users().messages().get(
                        userId="me", id=mid, format="full",
                    ).execute()
                except Exception as e:
                    log.warning("invoice gmail get failed for %s: %s", mid, e)
                    results["errors"] += 1
                    continue

                # Pull headers.
                headers = {
                    h["name"].lower(): h["value"]
                    for h in (msg.get("payload", {}).get("headers") or [])
                }
                sender = headers.get("from", "")
                subject = headers.get("subject", "")

                # Body (text/plain first, fall back to text/html stripped).
                body = _extract_body_text(msg.get("payload") or {})
                if not body:
                    continue

                # Cheap classifier — bail on receipts.
                kind, conf, reason = _pi.classify_document(
                    f"{subject}\n{body}",
                )
                if kind != "invoice" or conf < params.classify_threshold:
                    results["skipped_not_invoice"] += 1
                    continue

                try:
                    inv = _pi.extract_invoice_from_text(
                        body, source_id=source_id, source_kind="email_text",
                    )
                except Exception as e:
                    log.warning("invoice text extract failed: %s", e)
                    results["errors"] += 1
                    continue

                inv, rr = _finalize_invoice(
                    inv,
                    project_code_hint=params.project_code,
                    filename=None,
                    sender_email=sender,
                    chat_space_id=None,
                )

                if params.skip_low_confidence and inv.confidence < 0.4:
                    results["skipped_low_conf"] += 1
                    continue

                # Quality guards: missing invoice_number / total out of range.
                # When request_missing_info=True (default), we route based on
                # sender classification:
                #   - INTERNAL (employee): send DM (preferred) or email
                #     reply, mark AWAITING_INFO, project routing stays.
                #   - EXTERNAL (vendor / client): NEVER auto-reply. Park
                #     the row for human handoff, status AWAITING_INFO,
                #     stamp [external_sender:awaiting_employee_handoff].
                quality_fail = _validate_invoice_quality(
                    inv,
                    min_total=params.min_total,
                    max_total=params.max_total,
                    require_invoice_number=params.require_invoice_number,
                )
                request_sent = False
                request_channel = None
                missing_fields: list[str] = []
                sender_classification = None

                if quality_fail:
                    sender_classification = _sc.classify(sender)
                    is_employee = sender_classification.get("internal", False)

                    if not params.request_missing_info:
                        # Old behavior — silent park, no outbound.
                        inv.project_code = None
                        flag = (
                            f"[needs_review] quality_check_failed: "
                            f"{quality_fail}"
                        )
                        inv.notes = flag + ("\n" + inv.notes if inv.notes else "")
                        results["parked_quality_failed"] += 1
                    elif is_employee:
                        # Employee submission — auto-follow-up via DM/email.
                        inv.status = "AWAITING_INFO"
                        missing_fields = _missing_field_list(
                            inv, project_resolved=bool(inv.project_code),
                        )
                        flag = (
                            "[awaiting_info] quality_check_failed: "
                            f"{quality_fail}; pinged employee for: "
                            f"{', '.join(missing_fields)}"
                        )
                        inv.notes = flag + ("\n" + inv.notes if inv.notes else "")
                        results.setdefault("requests_sent", 0)
                    else:
                        # External sender — MCP NEVER auto-replies.
                        inv.project_code = None
                        inv.status = "AWAITING_INFO"
                        flag = (
                            f"[external_sender:awaiting_employee_handoff] "
                            f"quality_check_failed: {quality_fail}; "
                            f"sender={sender_classification.get('email')}"
                        )
                        inv.notes = flag + ("\n" + inv.notes if inv.notes else "")
                        results.setdefault("parked_external_sender", 0)
                        results["parked_external_sender"] += 1

                ck = _pi.invoice_content_key(
                    inv.vendor, inv.invoice_number, inv.total,
                )

                # Pick destination sheet.
                if inv.project_code:
                    try:
                        sheet_id, _t = _ensure_project_sheet(inv.project_code)
                    except Exception as e:
                        log.warning(
                            "couldn't ensure sheet for %s: %s",
                            inv.project_code, e,
                        )
                        results["errors"] += 1
                        continue
                else:
                    sheet_id, _t = _ensure_needs_review_sheet()
                    if not quality_fail:
                        results["parked_needs_review"] += 1

                cache = _get_sheet_caches(sheet_id)
                if source_id in cache["source_ids"]:
                    results["skipped_dup"] += 1
                    continue
                if ck and ck in cache["content_keys"]:
                    results["skipped_dup_content"] += 1
                    continue

                # Employee info-request side-effect runs BEFORE the row
                # append so any [send_failed] stamp lands in the row's
                # notes column the FIRST time it's written.
                #
                # IMPORTANT: this trigger is NOT gated on `ck`. The
                # content_key requires invoice_number — but the most
                # common reason we're sending the ask is that the
                # invoice_number is what's missing. Gating on ck would
                # silently skip exactly the rows that need follow-up
                # the most. We fall back to source_id-based tracking
                # for vendor_followups when ck is None.
                sent = False
                request_channel = None
                request_thread_id = None
                tracking_key = ck or f"src:{source_id}"
                if (
                    quality_fail
                    and params.request_missing_info
                    and sender_classification
                    and sender_classification.get("internal")
                ):
                    thread_id = msg.get("threadId") or mid
                    employee_email = sender_classification.get("email") or sender
                    recipient_name = None
                    if "<" in (sender or ""):
                        recipient_name = sender.split("<", 1)[0].strip().strip('"')
                    composed = _compose_info_request(
                        inv, missing_fields,
                        audience="employee",
                        recipient_name=recipient_name,
                    )

                    send_errors: list[str] = []
                    dm_sent, dm_space, dm_err = _send_info_request_via_employee_dm(
                        employee_email=employee_email,
                        message_text=composed["plain"],
                    )
                    if dm_err:
                        send_errors.append(f"dm: {dm_err}")
                    if dm_sent and dm_space:
                        request_channel = "chat"
                        request_thread_id = dm_space
                        sent = True
                    else:
                        gm_sent, gm_err = _send_info_request_via_gmail(
                            thread_id=thread_id,
                            to=employee_email,
                            subject=composed["subject"],
                            plain=composed["plain"],
                            html=composed["html"],
                        )
                        if gm_err:
                            send_errors.append(f"gmail: {gm_err}")
                        if gm_sent:
                            request_channel = "gmail"
                            request_thread_id = thread_id
                            sent = True

                    # Stamp BEFORE row build so the failure reason lands
                    # in the notes column the first time it's written.
                    if not sent and send_errors:
                        err_block = (
                            "[send_failed] "
                            + "; ".join(send_errors)[:600]
                        )
                        inv.notes = err_block + (
                            "\n" + (inv.notes or "")
                        )

                # Build + append row AFTER the send so notes contain
                # any [send_failed] flag. Hybrid model — append to BOTH
                # the master roll-up sheet AND the per-employee-per-
                # project sheet (when sender was internal). External /
                # unresolved senders write master + parking only.
                row = _pi.invoice_to_sheet_row(
                    inv, logged_at=now_iso, asof_iso=now_iso,
                )
                archived_links: list[str] = []
                if inv.project_code:
                    employee_for_dual = (
                        sender_classification.get("email")
                        if sender_classification
                        and sender_classification.get("internal")
                        else None
                    )
                    _dual_write_row(
                        code=inv.project_code,
                        employee_email=employee_for_dual,
                        row=row,
                    )
                    # Archive original attachments (PDFs / images) to the
                    # employee's project subfolder.
                    if employee_for_dual:
                        archived_links = _archive_gmail_attachments_to_project(
                            payload=msg.get("payload") or {},
                            gmail_svc=gmail,
                            message_id=mid,
                            employee_email=employee_for_dual,
                            project_code=inv.project_code,
                            inv=inv,
                        )
                else:
                    # Unresolved → parking lot stays single-write.
                    _append_invoice_row(sheet_id, row)
                cache["source_ids"].add(source_id)
                if ck:
                    cache["content_keys"].add(ck)
                cache["rows_appended"] += 1
                results["invoices_extracted"] += 1
                if inv.project_code:
                    _pr.increment_invoice_count(inv.project_code, 1)
                    results["by_project"][inv.project_code] = (
                        results["by_project"].get(inv.project_code, 0) + 1
                    )

                # Register the outbound in vendor_followups now that the
                # row is on the sheet (so we have a stable row pointer).
                if (
                    quality_fail
                    and params.request_missing_info
                    and sender_classification
                    and sender_classification.get("internal")
                ):
                    if sent:
                        try:
                            row_number = (
                                len(cache["source_ids"]) + 1
                            )  # approximate
                            _vf.register_request(
                                content_key=tracking_key,
                                thread_id=request_thread_id,
                                channel=request_channel,
                                vendor_email=employee_email,
                                vendor_name=inv.vendor,
                                fields_requested=missing_fields,
                                sheet_id=sheet_id,
                                row_number=row_number,
                                project_code=inv.project_code,
                            )
                            results["requests_sent"] = (
                                results.get("requests_sent", 0) + 1
                            )
                            request_sent = True
                        except Exception as e:
                            log.warning("vendor_followups register failed: %s", e)

                new_records.append({
                    "source_id": source_id,
                    "vendor": inv.vendor,
                    "invoice_number": inv.invoice_number,
                    "total": inv.total,
                    "currency": inv.currency,
                    "project_code": inv.project_code,
                    "status": inv.status,
                    "info_request_sent": request_sent,
                    "request_channel": request_channel,
                    "missing_fields": missing_fields,
                    "sender_classification": (
                        sender_classification if sender_classification else None
                    ),
                    "archived_attachments": archived_links,
                    "resolution": rr.as_dict(),
                    "confidence": inv.confidence,
                })

            # --- DRIVE FOLDER (optional) -------------------------------- #
            if params.drive_folder_id:
                _scan_drive_folder_for_invoices(
                    params, results, sheet_cache, new_records, now_iso,
                )

            # --- CHAT SPACE (optional) ---------------------------------- #
            if params.chat_space_id:
                _scan_chat_for_invoices(
                    params, results, sheet_cache, new_records, now_iso,
                )

            return json.dumps(
                {
                    "status": "ok",
                    "results": results,
                    "records": new_records[:50],  # cap inline preview
                    "records_total": len(new_records),
                },
                indent=2,
            )
        except Exception as e:
            return format_error("workflow_extract_project_invoices", e)

    # ------------------------------------------------------------------ #
    # 5) workflow_move_invoice_to_project
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="workflow_move_invoice_to_project",
        annotations={
            "title": "Move an invoice row from one project's sheet to another",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_move_invoice_to_project(
        params: MoveInvoiceToProjectInput,
    ) -> str:
        """Move a single invoice row from one project's sheet to another.

        Use the `content_key` returned by extract — it survives a row delete.
        Provide `row_number` only when content_key is missing or ambiguous.

        The destination project must already be registered. The source row
        gets cleared (set to blank) rather than physically deleted, so row
        numbers in subsequent operations don't shift.
        """
        try:
            from_code = params.from_project_code.upper()
            to_code = params.to_project_code.upper()
            if from_code == to_code:
                return json.dumps({
                    "status": "noop",
                    "reason": "source and destination projects are the same",
                }, indent=2)

            # Resolve source sheet — could be a project sheet or the parking lot.
            from_sheet_id, from_title = _resolve_project_or_parking_sheet(from_code)
            to_sheet_id, to_title = _ensure_project_sheet(to_code)

            sheets = _sheets()
            # Read the whole source sheet. Project sheets stay small (low
            # hundreds), so reading A:AA is fine.
            resp = sheets.spreadsheets().values().get(
                spreadsheetId=from_sheet_id, range="A:AA",
            ).execute()
            rows = resp.get("values", []) or []
            if len(rows) < 2:
                return json.dumps({
                    "status": "not_found",
                    "reason": "source sheet has no data rows",
                    "from_sheet": from_title,
                }, indent=2)

            header = rows[0]
            data_rows = rows[1:]

            # Locate the row.
            target_idx = None
            ck_idx = _pi.PROJECT_SHEET_COLUMNS.index("content_key")
            if params.content_key:
                for i, row in enumerate(data_rows):
                    if len(row) > ck_idx and row[ck_idx] == params.content_key:
                        target_idx = i
                        break
            elif params.row_number is not None:
                # row_number is 1-indexed including the header.
                if params.row_number - 2 < 0 or params.row_number - 2 >= len(data_rows):
                    return json.dumps({
                        "status": "not_found",
                        "reason": (
                            f"row_number={params.row_number} out of range "
                            f"(sheet has {len(data_rows)} data rows)"
                        ),
                    }, indent=2)
                target_idx = params.row_number - 2
            else:
                return json.dumps({
                    "status": "bad_request",
                    "reason": "must provide content_key or row_number",
                }, indent=2)

            if target_idx is None:
                return json.dumps({
                    "status": "not_found",
                    "reason": "no matching invoice in source sheet",
                    "from_sheet": from_title,
                }, indent=2)

            row_to_move = list(data_rows[target_idx])
            # Pad to header length.
            while len(row_to_move) < len(header):
                row_to_move.append("")

            # Update project_code in place (column index from schema).
            pc_idx = _pi.PROJECT_SHEET_COLUMNS.index("project_code")
            if pc_idx < len(row_to_move):
                row_to_move[pc_idx] = to_code

            # Append to destination.
            sheets.spreadsheets().values().append(
                spreadsheetId=to_sheet_id, range="A:AA",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row_to_move]},
            ).execute()

            # Clear source row (preserve row numbers).
            src_row_number = target_idx + 2
            sheets.spreadsheets().values().clear(
                spreadsheetId=from_sheet_id,
                range=f"A{src_row_number}:AA{src_row_number}",
            ).execute()

            # Update invoice counts.
            try:
                # Decrement by 1 only if the source was a registered project.
                if _pr.get(from_code):
                    _pr.increment_invoice_count(from_code, -1)
                _pr.increment_invoice_count(to_code, 1)
            except Exception as e:
                log.warning("invoice count update failed: %s", e)

            return json.dumps({
                "status": "ok",
                "moved_from": {"project_code": from_code, "sheet": from_title,
                               "row_number": src_row_number},
                "moved_to":   {"project_code": to_code,   "sheet": to_title},
                "row_preview": dict(zip(header, row_to_move)),
            }, indent=2)
        except Exception as e:
            return format_error("workflow_move_invoice_to_project", e)

    # ------------------------------------------------------------------ #
    # 6) workflow_export_project_invoices_qb_csv
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="workflow_export_project_invoices_qb_csv",
        annotations={
            "title": "Export a project's invoices as a QuickBooks Bills CSV",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_export_project_invoices_qb_csv(
        params: ExportProjectInvoicesQbCsvInput,
    ) -> str:
        """Export a project's AP sheet rows as a QuickBooks Bills-importable
        CSV. Filters by status (default OPEN/APPROVED) and optional date range.

        Returns base64 CSV inline by default; pass `save_to_drive_folder_id`
        to write the file straight to Drive.
        """
        try:
            code = params.project_code.upper()
            proj = _pr.get(code)
            if not proj:
                return json.dumps({
                    "status": "unknown_project",
                    "project_code": code,
                    "hint": "Call workflow_list_projects to see registered codes.",
                }, indent=2)
            sheet_id = proj.get("sheet_id")
            if not sheet_id:
                return json.dumps({
                    "status": "no_sheet",
                    "project_code": code,
                    "hint": "Call workflow_create_project_sheet to bootstrap.",
                }, indent=2)

            sheets = _sheets()
            resp = sheets.spreadsheets().values().get(
                spreadsheetId=sheet_id, range="A:AA",
            ).execute()
            rows = resp.get("values", []) or []
            if len(rows) < 2:
                return json.dumps({
                    "status": "empty",
                    "project_code": code,
                    "rows": 0,
                }, indent=2)

            header = rows[0]
            try:
                idx = {col: header.index(col) for col in _pi.PROJECT_SHEET_COLUMNS}
            except ValueError:
                return json.dumps({
                    "status": "header_mismatch",
                    "project_code": code,
                    "hint": "Sheet header doesn't match the canonical schema. "
                            "Was it edited manually?",
                }, indent=2)

            statuses = {s.upper() for s in (params.statuses or [])}
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(_pi.QB_INVOICE_CSV_COLUMNS)
            kept = 0
            for row in rows[1:]:
                if not row:
                    continue
                # Pad short rows so index access is safe.
                while len(row) < len(header):
                    row.append("")
                # Skip receipts — QB Bills CSV is for invoices only. Receipts
                # belong in QB's Expenses import (different schema).
                if (row[idx["doc_type"]] or "").lower() == "receipt":
                    continue
                status = (row[idx["status"]] or "").upper()
                if statuses and status not in statuses:
                    continue
                inv_date = row[idx["invoice_date"]] or ""
                if params.date_from and inv_date and inv_date < params.date_from:
                    continue
                if params.date_to and inv_date and inv_date > params.date_to:
                    continue

                # Reconstruct an ExtractedInvoice-shaped dict for the QB row builder.
                qb_row = [
                    row[idx["invoice_number"]] or "",
                    row[idx["vendor"]] or "",
                    row[idx["invoice_date"]] or "",
                    row[idx["due_date"]] or "",
                    row[idx["category"]] or "Miscellaneous Expense",
                    row[idx["total"]] or "",
                    row[idx["currency"]] or "USD",
                    " | ".join(filter(None, [
                        f"Project: {code}",
                        f"PO: {row[idx['po_number']]}" if row[idx["po_number"]] else "",
                        row[idx["notes"]] or "",
                    ])),
                ]
                writer.writerow(qb_row)
                kept += 1

            csv_bytes = buf.getvalue().encode("utf-8")

            out: dict = {
                "status": "ok",
                "project_code": code,
                "rows_exported": kept,
            }

            if params.save_to_drive_folder_id:
                from googleapiclient.http import MediaInMemoryUpload
                fname = (
                    f"{code}_invoices_{_dt.date.today().isoformat()}.csv"
                )
                media = MediaInMemoryUpload(
                    csv_bytes, mimetype="text/csv", resumable=False,
                )
                created = _drive().files().create(
                    body={"name": fname,
                          "parents": [params.save_to_drive_folder_id]},
                    media_body=media,
                    fields="id,name,webViewLink",
                ).execute()
                out["drive_file"] = {
                    "id": created["id"],
                    "name": created["name"],
                    "url": created.get("webViewLink"),
                }
            else:
                out["csv_base64"] = base64.b64encode(csv_bytes).decode("ascii")
                out["filename_suggestion"] = (
                    f"{code}_invoices_{_dt.date.today().isoformat()}.csv"
                )

            return json.dumps(out, indent=2)
        except Exception as e:
            return format_error("workflow_export_project_invoices_qb_csv", e)

    # ------------------------------------------------------------------ #
    # 7) workflow_extract_project_receipts
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="workflow_extract_project_receipts",
        annotations={
            "title": "Scan inbox/Drive/Chat for receipts and route them to per-project sheets",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_extract_project_receipts(
        params: ExtractProjectReceiptsInput,
    ) -> str:
        """Pull email + chat receipts and write them into per-project sheets
        alongside invoices. Same 5-tier project resolver as the invoice flow.

        1. Inbox: uses the existing receipt classifier (STRONG/BROAD sender
           tiers, money-pattern body match) to pick out real receipts.
        2. Drive folder (optional): scans PDFs/images in the folder.
        3. Chat space (optional): pulls receipts from a `#receipts` channel,
           with the same People-API-resolved sender attribution as the
           regular receipt extractor.
        4. Each receipt is mapped through the project resolver. Resolved
           receipts append a `doc_type='receipt'` row to the project's
           sheet; unresolved receipts park in `Project Invoices — Needs
           Project Assignment`.
        5. Dedupe: source_id (Gmail message_id / Drive file_id /
           `chat:<space>/<msg>`) AND content_key
           (merchant|date|total_cents|last_4) so the same physical receipt
           arriving via two paths only logs once.

        Useful when the regular receipt extractor's sheet is the personal
        expense log and you want a separate per-project rollup for client
        billing — or when you only ever want this project's spend in one
        place. Pair with `workflow_extract_project_invoices` to fill the
        unpaid side of the same sheet.
        """
        try:
            import llm
            ok, reason = llm.is_available()
            if not ok:
                return json.dumps({
                    "status": "no_llm",
                    "reason": reason,
                    "fix": (
                        'Set "anthropic_api_key" in config.json. '
                        "Receipt extraction requires LLM access."
                    ),
                }, indent=2)

            now_iso = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
            results = {
                "scanned": 0,
                "receipts_extracted": 0,
                "skipped_not_receipt": 0,
                "skipped_dup": 0,
                "skipped_dup_content": 0,
                "skipped_low_conf": 0,
                "parked_needs_review": 0,
                "errors": 0,
                "by_project": {},
            }
            sheet_cache: dict[str, dict] = {}
            new_records: list[dict] = []

            # --- INBOX ---------------------------------------------------- #
            gmail = _gmail()
            search_q = (
                f"newer_than:{params.days}d (receipt OR invoice OR order OR "
                "purchase OR \"thank you for your\" OR \"order confirmation\" "
                "OR \"trip receipt\")"
            )
            try:
                msgs_resp = gmail.users().messages().list(
                    userId="me", q=search_q,
                    maxResults=min(params.max_emails_to_scan, 100),
                ).execute()
            except Exception as e:
                log.warning("project receipt inbox search failed: %s", e)
                msgs_resp = {"messages": []}

            for entry in (msgs_resp.get("messages") or [])[:params.max_emails_to_scan]:
                results["scanned"] += 1
                mid = entry.get("id")
                if not mid:
                    continue
                source_id = f"gmail:{mid}"
                try:
                    msg = gmail.users().messages().get(
                        userId="me", id=mid, format="full",
                    ).execute()
                except Exception as e:
                    log.warning("project receipt gmail get failed for %s: %s", mid, e)
                    results["errors"] += 1
                    continue

                headers = {
                    h["name"].lower(): h["value"]
                    for h in (msg.get("payload", {}).get("headers") or [])
                }
                sender = headers.get("from", "")
                subject = headers.get("subject", "")
                body = _extract_body_text(msg.get("payload") or {})
                if not body:
                    continue

                # Use the receipt-side classifier — same heuristic the regular
                # receipt extractor uses, so we treat the same set of emails
                # as receipts.
                is_receipt, _why = _r.classify_email_as_receipt(
                    subject=subject, sender=sender, body_preview=body,
                )
                if not is_receipt:
                    results["skipped_not_receipt"] += 1
                    continue

                try:
                    rec = _r.extract_from_text(
                        body, source_id=source_id,
                        source_kind="email_text", submitted_by=sender,
                    )
                except Exception as e:
                    log.warning("project receipt text extract failed: %s", e)
                    results["errors"] += 1
                    continue

                # Run the receipt enrichment ladder so low-conf rows get the
                # same Maps + web_search treatment they'd get on the regular
                # receipt path.
                try:
                    rec = _r.enrich_low_confidence_receipt(rec)
                except Exception as e:
                    log.warning("project receipt enrichment failed: %s", e)

                if params.skip_low_confidence and rec.confidence < 0.4:
                    results["skipped_low_conf"] += 1
                    continue

                # Resolve project. We pass the merchant + sender as the
                # text the LLM tier uses; sender + space match deterministic
                # tiers first.
                rr = _pr.resolve(
                    project_code_hint=params.project_code,
                    filename=None,
                    sender_email=sender,
                    chat_space_id=None,
                    invoice_text=(
                        f"Merchant: {rec.merchant or '?'} | "
                        f"Notes: {(rec.notes or '')[:200]}"
                    ),
                )
                project_code = (
                    rr.project_code if rr.confidence >= _pr.RESOLVE_THRESHOLD
                    else None
                )

                # Apply project defaults.
                if project_code:
                    proj = _pr.get(project_code)
                    billable = bool((proj or {}).get("default_billable", True))
                    markup_pct = float((proj or {}).get("default_markup_pct") or 0.0)
                else:
                    billable = True
                    markup_pct = 0.0

                # Pick destination sheet.
                if project_code:
                    try:
                        sheet_id, _t = _ensure_project_sheet(project_code)
                    except Exception as e:
                        log.warning(
                            "ensure project sheet for receipt failed: %s", e,
                        )
                        results["errors"] += 1
                        continue
                else:
                    sheet_id, _t = _ensure_needs_review_sheet()
                    results["parked_needs_review"] += 1

                if sheet_id not in sheet_cache:
                    sids, cks = _existing_invoice_keys(sheet_id)
                    sheet_cache[sheet_id] = {
                        "source_ids": sids, "content_keys": cks,
                        "rows_appended": 0,
                    }
                cache = sheet_cache[sheet_id]
                if source_id in cache["source_ids"]:
                    results["skipped_dup"] += 1
                    continue
                ck = _r.content_key(rec.merchant, rec.date, rec.total, rec.last_4)
                if ck and ck in cache["content_keys"]:
                    results["skipped_dup_content"] += 1
                    continue

                row = _pi.receipt_to_project_sheet_row(
                    rec, project_code=project_code,
                    billable=billable, markup_pct=markup_pct,
                    logged_at=now_iso,
                )
                _append_invoice_row(sheet_id, row)
                cache["source_ids"].add(source_id)
                if ck:
                    cache["content_keys"].add(ck)
                cache["rows_appended"] += 1
                results["receipts_extracted"] += 1
                if project_code:
                    _pr.increment_invoice_count(project_code, 1)
                    results["by_project"][project_code] = (
                        results["by_project"].get(project_code, 0) + 1
                    )
                new_records.append({
                    "source_id": source_id,
                    "doc_type": "receipt",
                    "merchant": rec.merchant,
                    "date": rec.date,
                    "total": rec.total,
                    "currency": rec.currency,
                    "project_code": project_code,
                    "resolution": rr.as_dict(),
                    "confidence": rec.confidence,
                })

            # --- DRIVE + CHAT --------------------------------------------- #
            if params.drive_folder_id:
                _scan_drive_folder_for_project_receipts(
                    params, results, sheet_cache, new_records, now_iso,
                )
            if params.chat_space_id:
                _scan_chat_for_project_receipts(
                    params, results, sheet_cache, new_records, now_iso,
                )

            return json.dumps(
                {
                    "status": "ok",
                    "results": results,
                    "records": new_records[:50],
                    "records_total": len(new_records),
                },
                indent=2,
            )
        except Exception as e:
            return format_error("workflow_extract_project_receipts", e)

    # ------------------------------------------------------------------ #
    # workflow_migrate_project_sheets_to_ap_layout
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="workflow_migrate_project_sheets_to_ap_layout",
        annotations={
            "title": "Move legacy project sheets into AP Submissions/Master/",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_migrate_project_sheets_to_ap_layout(
        params: MigrateProjectSheetsToApLayoutInput,
    ) -> str:
        """One-shot migrator. For each registered project's sheet, ensure
        it lives inside AP Submissions/Master/ instead of at Drive root.

        Implementation: Drive's addParents/removeParents — the sheet_id is
        unchanged, no row migration needed. Idempotent: if a sheet is
        already inside the master folder, the call is a no-op for that
        project.

        Use after upgrading to the AP-folder layout to bring legacy
        root-level sheets into the new tree without breaking any
        existing references (registry sheet_id, prior chat shares, etc.).
        """
        try:
            drive = _drive()
            try:
                master_folder_id = _drive_layout.ensure_master_subfolder()
            except Exception as e:
                return json.dumps({
                    "status": "error",
                    "reason": f"could not ensure Master subfolder: {e}",
                }, indent=2)

            target_codes = (
                [params.project_code.upper()]
                if params.project_code
                else [p["code"] for p in _pr.list_all(active_only=False)]
            )

            results: list[dict] = []
            counts = {"moved": 0, "already_in_master": 0,
                      "missing_sheet": 0, "errors": 0, "would_move": 0}

            for code in target_codes:
                proj = _pr.get(code)
                if not proj:
                    results.append({
                        "code": code, "status": "unknown_project",
                    })
                    counts["errors"] += 1
                    continue
                sheet_id = proj.get("sheet_id")
                if not sheet_id:
                    results.append({
                        "code": code, "status": "no_sheet_in_registry",
                    })
                    counts["missing_sheet"] += 1
                    continue
                try:
                    meta = drive.files().get(
                        fileId=sheet_id,
                        fields="id,name,parents",
                    ).execute()
                except Exception as e:
                    results.append({
                        "code": code, "sheet_id": sheet_id,
                        "status": "fetch_failed", "error": str(e),
                    })
                    counts["errors"] += 1
                    continue

                current_parents = meta.get("parents", []) or []
                if master_folder_id in current_parents:
                    results.append({
                        "code": code, "sheet_id": sheet_id,
                        "status": "already_in_master",
                    })
                    counts["already_in_master"] += 1
                    continue

                if params.dry_run:
                    results.append({
                        "code": code, "sheet_id": sheet_id,
                        "status": "would_move",
                        "from_parents": current_parents,
                        "to_parent": master_folder_id,
                    })
                    counts["would_move"] += 1
                    continue

                try:
                    drive.files().update(
                        fileId=sheet_id,
                        addParents=master_folder_id,
                        removeParents=",".join(current_parents),
                        fields="id,parents",
                    ).execute()
                    results.append({
                        "code": code, "sheet_id": sheet_id,
                        "status": "moved",
                        "to_parent": master_folder_id,
                    })
                    counts["moved"] += 1
                except Exception as e:
                    results.append({
                        "code": code, "sheet_id": sheet_id,
                        "status": "move_failed", "error": str(e),
                    })
                    counts["errors"] += 1

            return json.dumps({
                "status": "ok",
                "dry_run": params.dry_run,
                "master_folder_id": master_folder_id,
                "counts": counts,
                "results": results,
            }, indent=2)
        except Exception as e:
            return format_error(
                "workflow_migrate_project_sheets_to_ap_layout", e,
            )

    # ------------------------------------------------------------------ #
    # 8) workflow_send_vendor_reminders
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="workflow_send_vendor_reminders",
        annotations={
            "title": "Send brand-voiced reminders for outstanding vendor info requests",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def workflow_send_vendor_reminders(
        params: SendVendorRemindersInput,
    ) -> str:
        """Bulk reminder loop. For each AWAITING_INFO row whose original
        request is past its cadence (24h for email, immediate for chat),
        post a polite nudge on the same thread/space.

        Caps:
          - max_to_send (default 20) — hard cap per run.
          - Hard ceiling of 2 reminders per row, ever. Past that the row
            stays AWAITING_INFO and the user has to handle it manually.

        Returns a summary of what was nudged + skipped.
        """
        try:
            due = _vf.due_for_reminder()
            if params.channel:
                due = [r for r in due if r.get("channel") == params.channel]

            results = {
                "outstanding_total": len(_vf.list_open()),
                "due_for_reminder": len(due),
                "reminders_sent": 0,
                "skipped_capped": 0,
                "errors": 0,
            }
            sent_records: list[dict] = []

            for rec in due[:params.max_to_send]:
                ck = rec.get("content_key")
                channel = rec.get("channel")
                # Build a lightweight ExtractedInvoice stand-in just so the
                # composer can format the field-list block.
                inv_stub = _pi.ExtractedInvoice(
                    vendor=rec.get("vendor_name"),
                )
                missing = rec.get("fields_requested") or []
                composed = _compose_info_request(
                    inv_stub, missing,
                    is_reminder=True,
                    reminder_count=int(rec.get("reminder_count", 0)) + 1,
                )

                ok = False
                send_err = None
                if channel == "gmail" and rec.get("vendor_email"):
                    ok, send_err = _send_info_request_via_gmail(
                        thread_id=rec["thread_id"],
                        to=rec["vendor_email"],
                        subject=composed["subject"],
                        plain=composed["plain"],
                        html=composed["html"],
                    )
                elif channel == "chat":
                    ok, send_err = _send_info_request_via_chat(
                        space_name=rec["thread_id"],  # space resource name
                        message_text=composed["plain"],
                    )

                if ok:
                    updated = _vf.record_reminder(ck)
                    if updated:
                        results["reminders_sent"] += 1
                        sent_records.append({
                            "content_key": ck,
                            "channel": channel,
                            "reminder_number": updated["reminder_count"],
                            "vendor_name": rec.get("vendor_name"),
                            "fields_requested": missing,
                        })
                    else:
                        results["skipped_capped"] += 1
                else:
                    results["errors"] += 1
                    if send_err:
                        sent_records.append({
                            "content_key": ck,
                            "channel": channel,
                            "error": send_err,
                            "vendor_name": rec.get("vendor_name"),
                        })

            return json.dumps(
                {
                    "status": "ok",
                    "results": results,
                    "sent": sent_records,
                },
                indent=2,
            )
        except Exception as e:
            return format_error("workflow_send_vendor_reminders", e)

    # ------------------------------------------------------------------ #
    # 9) workflow_process_vendor_replies
    # ------------------------------------------------------------------ #
    @mcp.tool(
        name="workflow_process_vendor_replies",
        annotations={
            "title": "Parse vendor replies on outstanding requests + promote rows",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def workflow_process_vendor_replies(
        params: ProcessVendorRepliesInput,
    ) -> str:
        """For each outstanding AWAITING_INFO request, scan the original
        thread for any vendor message that arrived after we sent the ask.
        If a reply is found, LLM-parse it for the missing fields, update the
        parked sheet row in place, re-run the quality guard, and (if it
        passes now) flip status from AWAITING_INFO to OPEN.

        Returns a per-row summary of what was found, parsed, and promoted.
        """
        try:
            import llm
            ok, reason = llm.is_available()
            if not ok:
                return json.dumps({
                    "status": "no_llm",
                    "reason": reason,
                    "fix": (
                        'Set "anthropic_api_key" in config.json. Reply '
                        "parsing requires LLM access."
                    ),
                }, indent=2)

            outstanding = _vf.list_open()[:params.max_to_process]
            results = {
                "outstanding_inspected": len(outstanding),
                "replies_found": 0,
                "rows_updated": 0,
                "rows_promoted": 0,
                "rows_still_awaiting": 0,
                "errors": 0,
            }
            updates: list[dict] = []

            for rec in outstanding:
                ck = rec.get("content_key")
                channel = rec.get("channel")
                request_sent_at = rec.get("request_sent_at") or ""

                reply_body = None
                try:
                    if channel == "gmail":
                        reply_body = _find_gmail_reply_body(
                            thread_id=rec["thread_id"],
                            sent_at_iso=request_sent_at,
                        )
                    elif channel == "chat":
                        reply_body = _find_chat_reply_body(
                            space_name=rec["thread_id"],
                            sent_at_iso=request_sent_at,
                        )
                except Exception as e:
                    log.warning("reply lookup failed for %s: %s", ck, e)
                    results["errors"] += 1
                    continue

                if not reply_body:
                    results["rows_still_awaiting"] += 1
                    continue

                results["replies_found"] += 1

                # Parse the reply for the requested fields.
                fields = rec.get("fields_requested") or []
                parsed = _parse_vendor_reply(reply_body, fields)
                if not parsed:
                    results["rows_still_awaiting"] += 1
                    continue

                # Update the sheet row in place.
                try:
                    promoted = _apply_reply_update(rec, parsed)
                except Exception as e:
                    log.warning("apply_reply_update failed for %s: %s", ck, e)
                    results["errors"] += 1
                    continue

                results["rows_updated"] += 1
                if promoted:
                    results["rows_promoted"] += 1
                    _vf.mark_resolved(ck)
                else:
                    results["rows_still_awaiting"] += 1

                updates.append({
                    "content_key": ck,
                    "vendor_name": rec.get("vendor_name"),
                    "parsed": parsed,
                    "promoted_to_open": promoted,
                })

            return json.dumps(
                {
                    "status": "ok",
                    "results": results,
                    "updates": updates,
                },
                indent=2,
            )
        except Exception as e:
            return format_error("workflow_process_vendor_replies", e)


# --------------------------------------------------------------------------- #
# Helpers used by the orchestrator (kept module-level so they're testable
# without bouncing through register()).
# --------------------------------------------------------------------------- #


def _gmail_attachment_parts(payload: dict) -> list[dict]:
    """Walk a Gmail payload tree and return parts that look like a
    receipt/invoice artifact (PDF or image). Each item is the raw part
    dict — caller fetches its body via attachments.get."""
    out: list[dict] = []
    if not payload:
        return out
    stack = list(payload.get("parts") or [])
    if not stack and payload.get("body", {}).get("attachmentId"):
        stack = [payload]
    while stack:
        p = stack.pop(0)
        if p.get("parts"):
            stack.extend(p["parts"])
            continue
        mt = (p.get("mimeType") or "").lower()
        if mt == "application/pdf" or mt.startswith("image/"):
            if (p.get("body") or {}).get("attachmentId"):
                out.append(p)
    return out


def _download_gmail_attachment(
    gmail_svc, message_id: str, part: dict,
) -> tuple[Optional[bytes], str, str]:
    """Pull bytes for a single Gmail attachment part.
    Returns (content_bytes, mime_type, filename)."""
    import base64
    aid = (part.get("body") or {}).get("attachmentId")
    if not aid:
        return None, "", ""
    try:
        att = gmail_svc.users().messages().attachments().get(
            userId="me", messageId=message_id, id=aid,
        ).execute()
        data = att.get("data") or ""
        if not data:
            return None, "", ""
        content = base64.urlsafe_b64decode(data)
        mime = (part.get("mimeType") or "").lower() or "application/octet-stream"
        filename = part.get("filename") or f"attachment_{aid[:8]}"
        return content, mime, filename
    except Exception as e:
        log.warning("gmail attachment download failed: %s", e)
        return None, "", ""


def _archive_gmail_attachments_to_project(
    *,
    payload: dict,
    gmail_svc,
    message_id: str,
    employee_email: str,
    project_code: str,
    inv: _pi.ExtractedInvoice,
) -> list[str]:
    """For every PDF/image attachment in the Gmail message, save a copy
    into AP Submissions/Last, First/<project_code>/. Returns the list of
    Drive webViewLinks written (for inclusion in records output).

    Filename pattern: {YYYY-MM-DD}__{vendor}__{invoice_or_msg}.{ext}
    """
    parts = _gmail_attachment_parts(payload)
    if not parts:
        return []
    try:
        employee_folder = _drive_layout.ensure_employee_folder(employee_email)
        project_folder = _drive_layout.ensure_project_subfolder(
            employee_folder, project_code,
        )
    except Exception as e:
        log.warning("could not ensure archive folder: %s", e)
        return []

    links: list[str] = []
    invoice_date = (inv.invoice_date or "")[:10] or _dt.date.today().isoformat()
    vendor_token = (inv.vendor or "vendor").replace("/", "_").replace(" ", "_")[:40]
    inv_token = (inv.invoice_number or message_id[:10]).replace("/", "_")[:40]

    for i, part in enumerate(parts, start=1):
        content, mime, original_name = _download_gmail_attachment(
            gmail_svc, message_id, part,
        )
        if not content:
            continue
        # Preserve the file extension from the original filename if present.
        ext = ""
        if "." in original_name:
            ext = "." + original_name.rsplit(".", 1)[-1]
        elif mime == "application/pdf":
            ext = ".pdf"
        elif mime.startswith("image/"):
            ext = "." + mime.split("/", 1)[1]
        suffix = f"_{i}" if len(parts) > 1 else ""
        archive_name = (
            f"{invoice_date}__{vendor_token}__{inv_token}{suffix}{ext}"
        )
        try:
            link = _drive_layout.archive_to_project_folder(
                project_folder, content, mime, archive_name,
            )
            if link:
                links.append(link)
        except Exception as e:
            log.warning("attachment archive failed for %s: %s", archive_name, e)
    return links


def _extract_body_text(payload: dict) -> str:
    """Walk a Gmail payload and return the best text body. text/plain wins."""
    if not payload:
        return ""

    def decode(b: dict) -> str:
        data = (b.get("body") or {}).get("data")
        if not data:
            return ""
        try:
            import base64
            return base64.urlsafe_b64decode(data).decode(
                "utf-8", errors="replace",
            )
        except Exception:
            return ""

    # Single-part payload
    if not payload.get("parts"):
        if payload.get("mimeType", "").startswith("text/"):
            return decode(payload)
        return ""

    # Multi-part — prefer text/plain, fall back to text/html.
    plain, html = "", ""
    stack = list(payload.get("parts") or [])
    while stack:
        p = stack.pop(0)
        mt = p.get("mimeType", "")
        if p.get("parts"):
            stack.extend(p["parts"])
            continue
        if mt == "text/plain" and not plain:
            plain = decode(p)
        elif mt == "text/html" and not html:
            html = decode(p)
    if plain:
        return plain
    # Strip simple HTML tags as a last resort.
    if html:
        import re as _re
        return _re.sub(r"<[^>]+>", " ", html)
    return ""


def _resolve_project_or_parking_sheet(code: str) -> tuple[str, str]:
    """Get the sheet for a project, falling back to the parking-lot sheet
    when `code` is the literal token 'NEEDS_REVIEW' or 'PARKING'."""
    upper = code.upper()
    if upper in ("NEEDS_REVIEW", "PARKING", "UNASSIGNED"):
        return _ensure_needs_review_sheet()
    return _ensure_project_sheet(upper)


def _scan_drive_folder_for_invoices(
    params, results, sheet_cache, new_records, now_iso,
) -> None:
    """Pull PDF/image attachments from a Drive folder and route them through
    the same finalize + dedup + append pipeline as the inbox branch."""
    drive = _drive()
    try:
        resp = drive.files().list(
            q=(f"'{params.drive_folder_id}' in parents and trashed = false "
               "and (mimeType = 'application/pdf' "
               "or mimeType contains 'image/')"),
            pageSize=200,
            fields="files(id,name,mimeType,webViewLink)",
        ).execute()
    except Exception as e:
        log.warning("drive folder list failed: %s", e)
        results["errors"] += 1
        return

    for f in resp.get("files", []) or []:
        results["scanned"] += 1
        fid = f["id"]
        source_id = f"drive:{fid}"
        try:
            content = drive.files().get_media(fileId=fid).execute()
        except Exception as e:
            log.warning("drive download failed: %s", e)
            results["errors"] += 1
            continue

        try:
            if f["mimeType"] == "application/pdf":
                inv = _pi.extract_invoice_from_pdf(
                    content, source_id=source_id, source_kind="drive_pdf",
                )
            else:
                inv = _pi.extract_invoice_from_image(
                    content, mime_type=f["mimeType"],
                    source_id=source_id, source_kind="drive_image",
                )
        except Exception as e:
            log.warning("drive invoice extract failed: %s", e)
            results["errors"] += 1
            continue

        inv, rr = _finalize_invoice(
            inv,
            project_code_hint=params.project_code,
            filename=f.get("name"),
            sender_email=None,
            chat_space_id=None,
        )

        if params.skip_low_confidence and inv.confidence < 0.4:
            results["skipped_low_conf"] += 1
            continue

        quality_fail = _validate_invoice_quality(
            inv,
            min_total=params.min_total,
            max_total=params.max_total,
            require_invoice_number=params.require_invoice_number,
        )
        if quality_fail:
            inv.project_code = None
            flag = f"[needs_review] quality_check_failed: {quality_fail}"
            inv.notes = flag + ("\n" + inv.notes if inv.notes else "")
            results["parked_quality_failed"] += 1

        ck = _pi.invoice_content_key(inv.vendor, inv.invoice_number, inv.total)
        if inv.project_code:
            try:
                sheet_id, _t = _ensure_project_sheet(inv.project_code)
            except Exception as e:
                log.warning("ensure project sheet failed: %s", e)
                results["errors"] += 1
                continue
        else:
            sheet_id, _t = _ensure_needs_review_sheet()
            if not quality_fail:
                results["parked_needs_review"] += 1

        if sheet_id not in sheet_cache:
            sids, cks = _existing_invoice_keys(sheet_id)
            sheet_cache[sheet_id] = {
                "source_ids": sids, "content_keys": cks, "rows_appended": 0,
            }
        cache = sheet_cache[sheet_id]
        if source_id in cache["source_ids"]:
            results["skipped_dup"] += 1
            continue
        if ck and ck in cache["content_keys"]:
            results["skipped_dup_content"] += 1
            continue

        row = _pi.invoice_to_sheet_row(
            inv, logged_at=now_iso, asof_iso=now_iso,
            invoice_link=f.get("webViewLink", ""),
        )
        _append_invoice_row(sheet_id, row)
        cache["source_ids"].add(source_id)
        if ck:
            cache["content_keys"].add(ck)
        cache["rows_appended"] += 1
        results["invoices_extracted"] += 1
        if inv.project_code:
            _pr.increment_invoice_count(inv.project_code, 1)
            results["by_project"][inv.project_code] = (
                results["by_project"].get(inv.project_code, 0) + 1
            )
        new_records.append({
            "source_id": source_id,
            "vendor": inv.vendor,
            "invoice_number": inv.invoice_number,
            "total": inv.total,
            "project_code": inv.project_code,
            "resolution": rr.as_dict(),
            "confidence": inv.confidence,
        })


def _scan_chat_for_invoices(
    params, results, sheet_cache, new_records, now_iso,
) -> None:
    """Scan a Gchat space for invoice-bearing attachments and text bodies.

    Mirrors the inbox + Drive branches; chat_space is also passed to the
    project resolver so a project-specific channel auto-routes its uploads.
    """
    # Lazy import so tests that don't touch chat don't need the chat helpers.
    from tools.receipts import (
        _scan_chat_space as _scan_chat_for_receipts,  # type: ignore  # noqa: F401
    )
    chat_svc = _chat()
    since = (
        _dt.datetime.now(tz=_dt.timezone.utc)
        - _dt.timedelta(days=params.days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        resp = chat_svc.spaces().messages().list(
            parent=params.chat_space_id,
            pageSize=min(params.chat_max_messages, 100),
            filter=f'createTime > "{since}"',
            orderBy="createTime desc",
        ).execute()
    except Exception as e:
        log.warning("chat list failed: %s", e)
        results["errors"] += 1
        return

    msgs = resp.get("messages", []) or []
    for raw in msgs[:params.chat_max_messages]:
        results["scanned"] += 1
        message_name = raw.get("name", "")
        if not message_name:
            continue

        try:
            msg = chat_svc.spaces().messages().get(name=message_name).execute()
        except Exception as e:
            log.warning("chat get failed: %s", e)
            results["errors"] += 1
            continue

        source_id = f"chat:{message_name}"
        text = (msg.get("text") or "").strip()
        attachments = msg.get("attachment") or []

        # Prefer attachments — that's where most invoices live.
        inv = None
        used_filename = None
        for att in attachments:
            ct = att.get("contentType", "") or ""
            if ct not in ("application/pdf", "image/jpeg", "image/png"):
                continue
            try:
                from tools.receipts import _download_chat_attachment
                dl = _download_chat_attachment(chat_svc, att)
            except Exception as e:
                log.warning("chat download failed: %s", e)
                continue
            if not dl:
                continue
            content, mime = dl
            used_filename = att.get("contentName") or ""
            try:
                if mime == "application/pdf":
                    inv = _pi.extract_invoice_from_pdf(
                        content, source_id=source_id, source_kind="chat_pdf",
                    )
                else:
                    inv = _pi.extract_invoice_from_image(
                        content, mime_type=mime,
                        source_id=source_id, source_kind="chat_image",
                    )
            except Exception as e:
                log.warning("chat invoice extract failed: %s", e)
                results["errors"] += 1
                continue
            break

        # Fall back to message text — but classify first.
        if inv is None and text:
            kind, conf, _ = _pi.classify_document(text)
            if kind != "invoice" or conf < params.classify_threshold:
                results["skipped_not_invoice"] += 1
                continue
            try:
                inv = _pi.extract_invoice_from_text(
                    text, source_id=source_id, source_kind="chat_text",
                )
            except Exception as e:
                log.warning("chat text extract failed: %s", e)
                results["errors"] += 1
                continue

        if inv is None:
            results["skipped_not_invoice"] += 1
            continue

        inv, rr = _finalize_invoice(
            inv,
            project_code_hint=params.project_code,
            filename=used_filename,
            sender_email=None,
            chat_space_id=params.chat_space_id,
        )

        if params.skip_low_confidence and inv.confidence < 0.4:
            results["skipped_low_conf"] += 1
            continue

        # Chat-source quality guard with optional info-request send.
        chat_quality_fail = _validate_invoice_quality(
            inv,
            min_total=params.min_total,
            max_total=params.max_total,
            require_invoice_number=params.require_invoice_number,
        )
        chat_missing_fields: list[str] = []
        chat_request_sent = False
        if chat_quality_fail:
            if params.request_missing_info:
                inv.status = "AWAITING_INFO"
                chat_missing_fields = _missing_field_list(
                    inv, project_resolved=bool(inv.project_code),
                )
                flag = (
                    "[awaiting_info] quality_check_failed: "
                    f"{chat_quality_fail}; sent vendor request for: "
                    f"{', '.join(chat_missing_fields)}"
                )
                inv.notes = flag + ("\n" + inv.notes if inv.notes else "")
            else:
                inv.project_code = None
                flag = (
                    f"[needs_review] quality_check_failed: "
                    f"{chat_quality_fail}"
                )
                inv.notes = flag + ("\n" + inv.notes if inv.notes else "")
                results["parked_quality_failed"] += 1

        ck = _pi.invoice_content_key(inv.vendor, inv.invoice_number, inv.total)
        if inv.project_code:
            try:
                sheet_id, _t = _ensure_project_sheet(inv.project_code)
            except Exception as e:
                log.warning("ensure project sheet failed: %s", e)
                results["errors"] += 1
                continue
        else:
            sheet_id, _t = _ensure_needs_review_sheet()
            results["parked_needs_review"] += 1

        if sheet_id not in sheet_cache:
            sids, cks = _existing_invoice_keys(sheet_id)
            sheet_cache[sheet_id] = {
                "source_ids": sids, "content_keys": cks, "rows_appended": 0,
            }
        cache = sheet_cache[sheet_id]
        if source_id in cache["source_ids"]:
            results["skipped_dup"] += 1
            continue
        if ck and ck in cache["content_keys"]:
            results["skipped_dup_content"] += 1
            continue

        row = _pi.invoice_to_sheet_row(
            inv, logged_at=now_iso, asof_iso=now_iso,
        )
        _append_invoice_row(sheet_id, row)
        cache["source_ids"].add(source_id)
        if ck:
            cache["content_keys"].add(ck)
        cache["rows_appended"] += 1
        results["invoices_extracted"] += 1
        if inv.project_code:
            _pr.increment_invoice_count(inv.project_code, 1)
            results["by_project"][inv.project_code] = (
                results["by_project"].get(inv.project_code, 0) + 1
            )
        # Chat info-request side-effect.
        if (
            chat_quality_fail
            and params.request_missing_info
            and ck
            and params.chat_space_id
        ):
            composed = _compose_info_request(inv, chat_missing_fields)
            sent, send_err = _send_info_request_via_chat(
                space_name=params.chat_space_id,
                message_text=composed["plain"],
            )
            if not sent and send_err:
                inv.notes = (
                    f"[send_failed] chat: {send_err[:400]}\n"
                    + (inv.notes or "")
                )
            if sent:
                try:
                    _vf.register_request(
                        content_key=ck,
                        thread_id=params.chat_space_id,
                        channel="chat",
                        vendor_email=None,
                        vendor_name=inv.vendor,
                        fields_requested=chat_missing_fields,
                        sheet_id=sheet_id,
                        row_number=cache.get("rows_appended", 0) + 1,
                        project_code=inv.project_code,
                    )
                    results["requests_sent"] = (
                        results.get("requests_sent", 0) + 1
                    )
                    chat_request_sent = True
                except Exception as e:
                    log.warning("vendor_followups (chat) register failed: %s", e)

        new_records.append({
            "source_id": source_id,
            "vendor": inv.vendor,
            "invoice_number": inv.invoice_number,
            "total": inv.total,
            "project_code": inv.project_code,
            "status": inv.status,
            "info_request_sent": chat_request_sent,
            "missing_fields": chat_missing_fields,
            "resolution": rr.as_dict(),
            "confidence": inv.confidence,
        })


# --------------------------------------------------------------------------- #
# Receipt-side scanners — Drive folder + Chat space → per-project sheet
# --------------------------------------------------------------------------- #


def _resolve_for_receipt(rec, params, *, filename=None, chat_space_id=None,
                        sender_email=None):
    """Run the project resolver against a receipt and return (project_code,
    billable, markup_pct, resolve_result). Pulls project defaults from the
    registry when resolved."""
    rr = _pr.resolve(
        project_code_hint=params.project_code,
        filename=filename,
        sender_email=sender_email,
        chat_space_id=chat_space_id,
        invoice_text=(
            f"Merchant: {getattr(rec, 'merchant', '?')} | "
            f"Notes: {(getattr(rec, 'notes', None) or '')[:200]}"
        ),
    )
    project_code = (
        rr.project_code if rr.confidence >= _pr.RESOLVE_THRESHOLD else None
    )
    if project_code:
        proj = _pr.get(project_code) or {}
        return (
            project_code,
            bool(proj.get("default_billable", True)),
            float(proj.get("default_markup_pct") or 0.0),
            rr,
        )
    return None, True, 0.0, rr


def _route_project_receipt(
    rec, *, source_id, params, results, sheet_cache, new_records, now_iso,
    filename=None, chat_space_id=None, sender_email=None, receipt_link="",
):
    """Resolve project + dedup + append a single receipt row. Mutates results
    and sheet_cache in place. Single source of truth for the receipt-write
    path so Drive/Chat scanners stay short."""
    if params.skip_low_confidence and rec.confidence < 0.4:
        results["skipped_low_conf"] += 1
        return

    project_code, billable, markup_pct, rr = _resolve_for_receipt(
        rec, params, filename=filename,
        chat_space_id=chat_space_id, sender_email=sender_email,
    )

    if project_code:
        try:
            sheet_id, _t = _ensure_project_sheet(project_code)
        except Exception as e:
            log.warning("ensure project sheet for receipt failed: %s", e)
            results["errors"] += 1
            return
    else:
        sheet_id, _t = _ensure_needs_review_sheet()
        results["parked_needs_review"] += 1

    if sheet_id not in sheet_cache:
        sids, cks = _existing_invoice_keys(sheet_id)
        sheet_cache[sheet_id] = {
            "source_ids": sids, "content_keys": cks, "rows_appended": 0,
        }
    cache = sheet_cache[sheet_id]
    if source_id in cache["source_ids"]:
        results["skipped_dup"] += 1
        return
    ck = _r.content_key(rec.merchant, rec.date, rec.total, rec.last_4)
    if ck and ck in cache["content_keys"]:
        results["skipped_dup_content"] += 1
        return

    row = _pi.receipt_to_project_sheet_row(
        rec, project_code=project_code,
        billable=billable, markup_pct=markup_pct,
        logged_at=now_iso, receipt_link=receipt_link,
    )
    _append_invoice_row(sheet_id, row)
    cache["source_ids"].add(source_id)
    if ck:
        cache["content_keys"].add(ck)
    cache["rows_appended"] += 1
    results["receipts_extracted"] += 1
    if project_code:
        _pr.increment_invoice_count(project_code, 1)
        results["by_project"][project_code] = (
            results["by_project"].get(project_code, 0) + 1
        )
    new_records.append({
        "source_id": source_id,
        "doc_type": "receipt",
        "merchant": rec.merchant,
        "date": rec.date,
        "total": rec.total,
        "currency": rec.currency,
        "project_code": project_code,
        "resolution": rr.as_dict(),
        "confidence": rec.confidence,
    })


def _scan_drive_folder_for_project_receipts(
    params, results, sheet_cache, new_records, now_iso,
) -> None:
    """Pull PDF/image files from a Drive folder, run them through the
    receipt extractor + enrichment ladder, route to per-project sheet."""
    drive = _drive()
    try:
        resp = drive.files().list(
            q=(f"'{params.drive_folder_id}' in parents and trashed = false "
               "and (mimeType = 'application/pdf' "
               "or mimeType contains 'image/')"),
            pageSize=200,
            fields="files(id,name,mimeType,webViewLink)",
        ).execute()
    except Exception as e:
        log.warning("drive folder list failed: %s", e)
        results["errors"] += 1
        return

    for f in resp.get("files", []) or []:
        results["scanned"] += 1
        fid = f["id"]
        source_id = f"drive:{fid}"
        try:
            content = drive.files().get_media(fileId=fid).execute()
        except Exception as e:
            log.warning("drive download failed: %s", e)
            results["errors"] += 1
            continue

        try:
            if f["mimeType"] == "application/pdf":
                rec = _r.extract_from_pdf(
                    content, source_id=source_id, source_kind="drive_pdf",
                )
            else:
                rec = _r.extract_from_image(
                    content, mime_type=f["mimeType"],
                    source_id=source_id, source_kind="drive_image",
                )
        except Exception as e:
            log.warning("drive receipt extract failed: %s", e)
            results["errors"] += 1
            continue

        try:
            rec = _r.enrich_low_confidence_receipt(rec)
        except Exception:
            pass

        _route_project_receipt(
            rec, source_id=source_id, params=params, results=results,
            sheet_cache=sheet_cache, new_records=new_records, now_iso=now_iso,
            filename=f.get("name"),
            receipt_link=f.get("webViewLink", ""),
        )


def _scan_chat_for_project_receipts(
    params, results, sheet_cache, new_records, now_iso,
) -> None:
    """Scan a Gchat space for receipt-bearing attachments and text bodies,
    route to per-project sheet. Mirrors the receipt extractor's chat scan."""
    chat_svc = _chat()
    since = (
        _dt.datetime.now(tz=_dt.timezone.utc)
        - _dt.timedelta(days=params.days)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        resp = chat_svc.spaces().messages().list(
            parent=params.chat_space_id,
            pageSize=min(params.chat_max_messages, 100),
            filter=f'createTime > "{since}"',
            orderBy="createTime desc",
        ).execute()
    except Exception as e:
        log.warning("chat list failed: %s", e)
        results["errors"] += 1
        return

    msgs = resp.get("messages", []) or []
    for raw in msgs[:params.chat_max_messages]:
        results["scanned"] += 1
        message_name = raw.get("name", "")
        if not message_name:
            continue
        try:
            msg = chat_svc.spaces().messages().get(name=message_name).execute()
        except Exception as e:
            log.warning("chat get failed: %s", e)
            results["errors"] += 1
            continue

        source_id = f"chat:{message_name}"
        text = (msg.get("text") or "").strip()
        attachments = msg.get("attachment") or []

        # Resolve sender display name via the same People-API path the
        # regular receipt extractor uses, so the [Metadata] block is human.
        try:
            from tools.receipts import _resolve_chat_sender_display
            sender = _resolve_chat_sender_display(msg.get("sender"))
        except Exception:
            sender = None

        rec = None
        used_filename = None
        # Prefer attachments (most receipts).
        for att in attachments:
            ct = att.get("contentType", "") or ""
            if ct not in ("application/pdf", "image/jpeg", "image/png"):
                continue
            try:
                from tools.receipts import _download_chat_attachment
                dl = _download_chat_attachment(chat_svc, att)
            except Exception as e:
                log.warning("chat download failed: %s", e)
                continue
            if not dl:
                continue
            content, mime = dl
            used_filename = att.get("contentName") or ""
            try:
                if mime == "application/pdf":
                    rec = _r.extract_from_pdf(
                        content, source_id=source_id, source_kind="chat_pdf",
                        submitted_by=sender,
                    )
                else:
                    rec = _r.extract_from_image(
                        content, mime_type=mime,
                        source_id=source_id, source_kind="chat_image",
                        submitted_by=sender,
                    )
            except Exception as e:
                log.warning("chat receipt extract failed: %s", e)
                results["errors"] += 1
                continue
            break

        # Fall back to message body — same classifier as the regular
        # receipt extractor so we never re-extract bot-generated reports.
        if rec is None and text:
            is_receipt, _why = _r.classify_email_as_receipt(
                subject="", sender=sender or "", body_preview=text,
            )
            if not is_receipt:
                results["skipped_not_receipt"] += 1
                continue
            try:
                rec = _r.extract_from_text(
                    text, source_id=source_id, source_kind="chat_text",
                    submitted_by=sender,
                )
            except Exception as e:
                log.warning("chat text extract failed: %s", e)
                results["errors"] += 1
                continue

        if rec is None:
            results["skipped_not_receipt"] += 1
            continue

        try:
            rec = _r.enrich_low_confidence_receipt(rec)
        except Exception:
            pass

        _route_project_receipt(
            rec, source_id=source_id, params=params, results=results,
            sheet_cache=sheet_cache, new_records=new_records, now_iso=now_iso,
            filename=used_filename, chat_space_id=params.chat_space_id,
        )


# --------------------------------------------------------------------------- #
# Vendor-reply ingestion helpers
# --------------------------------------------------------------------------- #


def _iso_to_epoch_ms(iso: str) -> int:
    """Convert an ISO timestamp to epoch ms. Returns 0 on bad input."""
    try:
        return int(_dt.datetime.fromisoformat(iso).timestamp() * 1000)
    except Exception:
        return 0


def _find_gmail_reply_body(*, thread_id: str, sent_at_iso: str) -> Optional[str]:
    """Walk a Gmail thread for any message that arrived AFTER our request
    AND is not from us (the authenticated user). Returns the message body
    (text/plain preferred) or None if no qualifying reply exists yet.
    """
    try:
        gmail = _gmail()
        thread = gmail.users().threads().get(
            userId="me", id=thread_id, format="full",
        ).execute()
    except Exception as e:
        log.warning("gmail thread fetch failed: %s", e)
        return None

    sent_ms = _iso_to_epoch_ms(sent_at_iso)
    me_email = ""
    try:
        prof = gmail.users().getProfile(userId="me").execute()
        me_email = (prof.get("emailAddress") or "").lower()
    except Exception:
        pass

    for m in (thread.get("messages") or []):
        try:
            internal_ms = int(m.get("internalDate") or 0)
        except (TypeError, ValueError):
            internal_ms = 0
        if internal_ms <= sent_ms:
            continue
        # Skip messages we sent ourselves.
        headers = {
            h["name"].lower(): h["value"]
            for h in (m.get("payload", {}).get("headers") or [])
        }
        from_addr = (headers.get("from", "") or "").lower()
        if me_email and me_email in from_addr:
            continue
        # Reject messages stamped with our own bot-footer marker — those are
        # other automated outbounds, not vendor replies.
        body = _extract_body_text(m.get("payload") or {})
        if _r.BOT_FOOTER_MARKER.lower() in (body or "").lower():
            continue
        if body and body.strip():
            return body
    return None


def _find_chat_reply_body(*, space_name: str, sent_at_iso: str) -> Optional[str]:
    """Walk a Chat space for any non-bot message after the request was sent.
    Returns the text body or None.
    """
    try:
        chat = _chat()
        # Chat list filter accepts createTime > "<RFC3339>". Use sent_at - 1s.
        # Convert ISO to RFC3339 (same format).
        resp = chat.spaces().messages().list(
            parent=space_name,
            pageSize=50,
            filter=f'createTime > "{sent_at_iso}"',
            orderBy="createTime asc",
        ).execute()
    except Exception as e:
        log.warning("chat list failed: %s", e)
        return None

    me_email = ""
    try:
        people = gservices.people()
        prof = people.people().get(
            resourceName="people/me",
            personFields="emailAddresses",
        ).execute()
        emails = prof.get("emailAddresses") or []
        if emails:
            me_email = (emails[0].get("value") or "").lower()
    except Exception:
        pass

    for m in (resp.get("messages") or []):
        text = (m.get("text") or "").strip()
        if not text:
            continue
        # Skip our own bot-marker outbounds.
        if _r.BOT_FOOTER_MARKER.lower() in text.lower():
            continue
        # Best-effort: skip messages from us. Chat sender is users/N — if
        # we can resolve it to our email and it matches, skip.
        sender = m.get("sender") or {}
        if me_email:
            try:
                from tools.receipts import _resolve_chat_sender_display
                resolved = _resolve_chat_sender_display(sender) or ""
                if resolved and me_email.split("@")[0] in resolved.lower():
                    continue
            except Exception:
                pass
        return text
    return None


def _parse_vendor_reply(reply_body: str, fields_requested: list[str]) -> Optional[dict]:
    """Use Claude to pull the requested fields out of a free-text reply.

    Returns a dict like {"invoice_number": "INV-99", "total": 1234.56, ...}
    containing only the fields the LLM was confident about. Returns None on
    extraction failure or empty response.
    """
    try:
        import llm
    except Exception:
        return None
    ok, _ = llm.is_available()
    if not ok:
        return None

    field_lines = "\n".join(
        f"  - {f}: {_FIELD_PROMPT_LABELS.get(f, f)}"
        for f in fields_requested
    )
    # Active project codes — the vendor's reply may name a project either
    # by code or by name. The LLM normalizes either to a known code.
    try:
        active_projects = _pr.list_all(active_only=True)
    except Exception:
        active_projects = []
    project_lines = "\n".join(
        f"  - {p['code']}: {p.get('name', '')}"
        for p in active_projects[:50]
    )
    project_block = (
        f"\n\nActive project codes (vendor may name one in their reply, "
        "either by code or name — return the matching CODE only):\n"
        f"{project_lines}"
    ) if project_lines else ""

    prompt = (
        "A vendor replied to our request for missing invoice fields. "
        "Pull values for the fields listed below out of their reply. "
        "Return ONLY a JSON object — no prose, no fences. Use null for any "
        "field they didn't answer or where you're not confident.\n\n"
        f"Fields we asked for:\n{field_lines}"
        f"{project_block}\n\n"
        f"Vendor reply (verbatim, first 3000 chars):\n{reply_body[:3000]}\n\n"
        "JSON shape (use null for unanswered fields):\n"
        "{\n"
        '  "invoice_number": "string or null",\n'
        '  "po_number": "string or null",\n'
        '  "invoice_date": "YYYY-MM-DD or null",\n'
        '  "due_date": "YYYY-MM-DD or null",\n'
        '  "total": <number or null>,\n'
        '  "subtotal": <number or null>,\n'
        '  "tax": <number or null>,\n'
        '  "payment_terms": "string or null",\n'
        '  "remit_to": "string or null",\n'
        '  "vendor": "string or null",\n'
        '  "project_code": "ALPHA-style code from list, or null if vendor '
        'didn\'t mention one"\n'
        "}"
    )

    try:
        resp = llm.call_simple(prompt, max_tokens=600, temperature=0.0)
        text = (resp.get("text") or "").strip()
        import re as _re
        if text.startswith("```"):
            text = _re.sub(r"^```(?:json)?\s*\n?", "", text)
            text = _re.sub(r"\n?```\s*$", "", text)
        data = json.loads(text)
    except Exception as e:
        log.warning("vendor reply parse failed: %s", e)
        return None

    # Only keep the fields the vendor actually answered (non-null).
    answered = {k: v for k, v in data.items() if v not in (None, "", "null")}
    return answered or None


def _apply_reply_update(rec: dict, parsed: dict) -> bool:
    """Find the parked row in the project sheet, merge the parsed fields,
    re-run the quality guard, and (if it passes) flip status to OPEN.

    Returns True if the row was promoted (status now OPEN), False if it's
    still pending more info.
    """
    sheet_id = rec.get("sheet_id")
    if not sheet_id:
        return False
    sheets = _sheets()
    try:
        resp = sheets.spreadsheets().values().get(
            spreadsheetId=sheet_id, range="A:AA",
        ).execute()
    except Exception as e:
        log.warning("apply_reply_update fetch failed: %s", e)
        return False
    rows = resp.get("values", []) or []
    if len(rows) < 2:
        return False
    header = rows[0]
    data_rows = rows[1:]

    # Locate by content_key.
    ck_idx = (
        _pi.PROJECT_SHEET_COLUMNS.index("content_key")
        if "content_key" in _pi.PROJECT_SHEET_COLUMNS else None
    )
    target_idx = None
    if ck_idx is not None:
        target_ck = rec.get("content_key")
        for i, row in enumerate(data_rows):
            if len(row) > ck_idx and row[ck_idx] == target_ck:
                target_idx = i
                break
    if target_idx is None:
        return False

    row = list(data_rows[target_idx])
    while len(row) < len(header):
        row.append("")

    # Merge parsed fields. Map LLM keys → column names.
    field_map = {
        "invoice_number": "invoice_number",
        "po_number":      "po_number",
        "invoice_date":   "invoice_date",
        "due_date":       "due_date",
        "total":          "total",
        "subtotal":       "subtotal",
        "tax":            "tax",
        "payment_terms":  "payment_terms",
        "remit_to":       "remit_to",
        "vendor":         "vendor",
    }
    for src_key, col_name in field_map.items():
        if src_key in parsed and col_name in header:
            col_idx = header.index(col_name)
            row[col_idx] = parsed[src_key]

    # Vendor-supplied project_code — possibly different from what we
    # resolved to. If they named a project AND it's a known code AND it
    # differs from the row's current project_code, we move the row to the
    # right sheet and rewrite project_code in place.
    pc_idx = header.index("project_code")
    current_pc = (row[pc_idx] or "").strip().upper()
    vendor_pc = (parsed.get("project_code") or "").strip().upper()
    project_changed = False
    if vendor_pc and vendor_pc != current_pc:
        if _pr.get(vendor_pc):  # only honor known codes
            row[pc_idx] = vendor_pc
            project_changed = True

    # Re-run quality guard via a synthetic ExtractedInvoice from the row.
    inv = _pi.ExtractedInvoice(
        vendor=row[header.index("vendor")] or None,
        invoice_number=row[header.index("invoice_number")] or None,
        invoice_date=row[header.index("invoice_date")] or None,
        due_date=row[header.index("due_date")] or None,
        total=(
            float(row[header.index("total")])
            if row[header.index("total")] not in ("", None) else None
        ),
        currency=row[header.index("currency")] or "USD",
    )
    quality_fail = _validate_invoice_quality(
        inv, min_total=1.0, max_total=250000.0,
        require_invoice_number=True,
    )

    promoted = quality_fail is None
    status_idx = header.index("status")
    if promoted:
        row[status_idx] = "OPEN"
        # Stamp resolution into notes.
        notes_idx = header.index("notes")
        existing = row[notes_idx] or ""
        proj_note = (
            f" (re-routed to {vendor_pc})" if project_changed else ""
        )
        row[notes_idx] = (
            f"[resolved] vendor filled in missing fields{proj_note}\n"
            + existing
        ).strip()

    # If the vendor named a different project AND we have the source row in
    # one sheet, append to the destination sheet and clear the source row.
    # Otherwise, just rewrite the existing row in place.
    src_row_number = target_idx + 2
    if project_changed:
        try:
            dest_sheet_id, _t = _ensure_project_sheet(vendor_pc)
        except Exception as e:
            log.warning("ensure dest sheet failed for re-route: %s", e)
            dest_sheet_id = sheet_id  # fall back to in-place

        if dest_sheet_id != sheet_id:
            # Append to destination
            sheets.spreadsheets().values().append(
                spreadsheetId=dest_sheet_id, range="A:AA",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            ).execute()
            # Clear source row
            sheets.spreadsheets().values().clear(
                spreadsheetId=sheet_id,
                range=f"A{src_row_number}:AA{src_row_number}",
            ).execute()
            # Update invoice counts on both sides.
            try:
                if current_pc and _pr.get(current_pc):
                    _pr.increment_invoice_count(current_pc, -1)
                _pr.increment_invoice_count(vendor_pc, 1)
            except Exception as e:
                log.warning("count update on re-route failed: %s", e)
            # Update the followup record's sheet pointer so future reminders
            # land on the correct thread/sheet.
            try:
                rec["sheet_id"] = dest_sheet_id
            except Exception:
                pass
            return promoted

    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"A{src_row_number}:AA{src_row_number}",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()
    return promoted
