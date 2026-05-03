# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""AP/Wave-1 MCP tools — AP-1 EIB exporter, GL→Spend Category map,
StaffWizard project sync, and the receipt project-pick chat-back flow.

Tools registered:

    Workday Supplier Invoice EIB (AP-1)
      - workflow_export_workday_supplier_invoice_eib
      - workflow_build_gl_spend_category_map
      - workflow_list_ambiguous_spend_categories
      - workflow_set_spend_category_override

    StaffWizard authoritative project registry (v0.9.0 spec change)
      - workflow_staffwizard_sync_projects
      - workflow_list_active_staffwizard_projects

    Receipt → project validation + chat-back picker
      - workflow_validate_receipt_project
      - workflow_request_receipt_project_options
      - workflow_handle_picker_reply
      - workflow_receipt_new_project_request
      - workflow_list_pending_picks
"""

from __future__ import annotations

import csv
import datetime as _dt
import io
import json
import logging
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import gl_spend_category_map as _scm
import project_registry as _pr
import receipt_project_validator as _rpv
import staffwizard_project_sync as _sps
import workday_supplier_invoice_eib as _eib


_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Pydantic input models — module scope (FastMCP requirement)
# --------------------------------------------------------------------------- #


class _ExportSupplierInvoiceEibInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    start_date: str = Field(..., description="ISO date — earliest invoice_date to include.")
    end_date: str = Field(..., description="ISO date — latest invoice_date to include.")
    project_code: Optional[str] = Field(
        None, description="Restrict to one project. Omit for all projects.",
    )
    output_path: Optional[str] = Field(
        None, description="Path to write the EIB workbook. Default: ~/Surefox AP/Workday-Exports/Supplier-Invoice/{daterange}.xlsx",
    )
    submit: bool = Field(
        False, description="If True, EIB header rows ship with Submit=1 (auto-post on import).",
    )
    allow_ambiguous_spend_cat: bool = Field(
        False, description="If True, ship ambiguous Spend Categories using the dominant pick. Otherwise park them.",
    )


class _BuildGlSpendCategoryMapInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    je_workbook_path: str = Field(
        ...,
        description="Path to the JE training workbook (e.g. samples/Wolfhound Corp JEs Jan-Mar'26.xlsx).",
    )


class _ListAmbiguousSpendCategoriesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SetSpendCategoryOverrideInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    gl_account: str = Field(..., description="5-digit GL code (e.g. 53000).")
    spend_category: str = Field(..., description="Workday Spend Category display name.")
    note: Optional[str] = Field(None, description="Optional operator note explaining the choice.")


class _StaffwizardSyncProjectsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_window_days: int = Field(
        14, ge=1, le=180,
        description="Projects last seen within this many days are marked active.",
    )


class _ListActiveStaffwizardProjectsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: Optional[int] = Field(
        None, ge=1, le=200,
        description="Cap the list. Default returns every active project.",
    )


class _ValidateReceiptProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_code: Optional[str] = Field(
        None, description="The code the extractor or submitter resolved.",
    )
    receipt_meta: Optional[dict] = Field(
        None, description="Optional full receipt metadata for context.",
    )


class _RequestReceiptProjectOptionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    submitter_id: str = Field(..., description="Chat user ID or email of the submitter.")
    receipt_id: str = Field(..., description="Stable identifier for the parked receipt.")
    receipt_meta: Optional[dict] = Field(None, description="Vendor / amount / date for the prompt.")
    channel: str = Field(
        "chat", description="'chat' (Google Chat DM) or 'email'. Default: chat.",
    )
    send: bool = Field(
        True, description="If True, fire the chat DM now via gservices. If False, return the message text only.",
    )


class _HandlePickerReplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    submitter_id: str = Field(..., description="Chat user ID or email of the submitter.")
    reply_text: str = Field(..., description="Submitter's reply: 'A', 'B', a project code, or a name.")
    pending_id: Optional[str] = Field(
        None, description="Specific pending pick to resolve. Default: most recent open one.",
    )
    send: bool = Field(
        True, description="If True, send acknowledgments via chat. If False, return text only.",
    )


class _RequestNewProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    submitter_id: str = Field(..., description="Chat user ID or email of the submitter.")
    project_name: str = Field(..., description="Free-text name the submitter suggests.")
    hint: Optional[str] = Field(None, description="Operator-facing context (project lead, location, etc.).")
    send: bool = Field(True, description="If True, send ack via chat. If False, return text only.")


class _ListPendingPicksInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Optional[str] = Field(
        None, description="Filter by status: 'awaiting_pick', 'list_sent', 'resolved', 'new_project_requested'.",
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _err(e: Exception) -> dict[str, Any]:
    return {"status": "error", "error": f"{type(e).__name__}: {e}"}


def _default_eib_output_path(start_date: str, end_date: str, project_code: Optional[str]) -> Path:
    base = Path("~/Surefox AP/Workday-Exports/Supplier-Invoice").expanduser()
    label = (project_code or "ALL").upper()
    fname = f"{label}_supplier_invoice_eib_{start_date}_{end_date}.xlsx"
    return base / fname


def _project_invoice_rows_for_export(
    start_date: str, end_date: str, project_code: Optional[str],
) -> tuple[list[_eib.Invoice], list[dict]]:
    """Pull invoice rows from the per-project AP Sheets and convert to
    Invoice/InvoiceLine objects.

    Returns (invoices, skipped_rows). Skipped rows include receipts (this
    EIB is for vendor invoices only), out-of-range dates, and rows already
    flagged as exported.
    """
    import gservices  # local import — keeps this file importable in tests
    import project_invoices as _pi

    sheets = gservices.sheets_service()

    # Decide which projects to scan.
    if project_code:
        proj = _pr.get(project_code.upper())
        candidates = [proj] if proj and proj.get("sheet_id") else []
    else:
        candidates = [p for p in _pr.list_all(active_only=True) if p.get("sheet_id")]

    invoices: list[_eib.Invoice] = []
    skipped: list[dict] = []

    for proj in candidates:
        sheet_id = proj["sheet_id"]
        try:
            resp = sheets.spreadsheets().values().get(
                spreadsheetId=sheet_id, range="A:AA",
            ).execute()
        except Exception as e:
            skipped.append({
                "project_code": proj["code"],
                "reason": f"sheet read failed: {e}",
            })
            continue
        rows = resp.get("values", []) or []
        if len(rows) < 2:
            continue
        header = rows[0]
        try:
            idx = {col: header.index(col) for col in _pi.PROJECT_SHEET_COLUMNS}
        except (ValueError, AttributeError):
            skipped.append({
                "project_code": proj["code"],
                "reason": "header mismatch — sheet was likely edited manually",
            })
            continue

        for row in rows[1:]:
            if not row:
                continue
            while len(row) < len(header):
                row.append("")
            if (row[idx["doc_type"]] or "").lower() == "receipt":
                continue
            inv_date = row[idx["invoice_date"]] or ""
            if not inv_date:
                continue
            if inv_date < start_date or inv_date > end_date:
                continue

            inv_no = row[idx["invoice_number"]] or ""
            vendor = row[idx["vendor"]] or ""
            try:
                amount = float(row[idx["total"]] or 0.0)
            except (ValueError, TypeError):
                amount = 0.0
            if not inv_no or not vendor or amount <= 0:
                skipped.append({
                    "project_code": proj["code"],
                    "row_data": row[:6],
                    "reason": "missing required field (invoice_number/vendor/total)",
                })
                continue

            line = _eib.InvoiceLine(
                amount=round(amount, 2),
                memo=row[idx["notes"]] or row[idx["category"]] or "",
                cost_center=proj.get("staffwizard_job_number"),
            )
            inv = _eib.Invoice(
                invoice_number=inv_no,
                vendor=vendor,
                invoice_date=inv_date,
                due_date=row[idx["due_date"]] or None,
                received_date=inv_date,
                project_code=proj["code"],
                currency=row[idx["currency"]] or "USD",
                memo=f"Project: {proj['code']} — {proj.get('name', '')}",
                lines=[line],
            )
            invoices.append(inv)

    return invoices, skipped


# --------------------------------------------------------------------------- #
# MCP registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:  # noqa: ANN001
    """Register all AP/Wave-1 tools with FastMCP."""

    # ------------------------------------------------------------------ #
    # AP-1 — Workday Supplier Invoice EIB
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def workflow_export_workday_supplier_invoice_eib(
        params: _ExportSupplierInvoiceEibInput,
    ) -> dict[str, Any]:
        """Build a Workday Submit_Supplier_Invoice_v39.1 EIB workbook from
        the captured invoice rows in the per-project AP Sheets.

        Each line resolves its Ledger Account via the gl_classifier and
        its Spend Category via gl_spend_category_map. Lines that can't
        resolve are parked in the response — re-run after operator
        confirms via workflow_set_spend_category_override.

        Returns:
            {status, output_path, headers_written, lines_written,
             total_amount, parked_invoices, parked_lines, skipped_rows,
             ambiguous_lines}
        """
        try:
            invoices, skipped = _project_invoice_rows_for_export(
                params.start_date, params.end_date, params.project_code,
            )
            if not invoices:
                return {
                    "status": "empty",
                    "skipped_rows": skipped,
                    "hint": "No invoice rows matched the date range. Check the per-project AP sheets.",
                }
            for inv in invoices:
                inv.submit = bool(params.submit)
            out_path = (
                Path(params.output_path).expanduser()
                if params.output_path
                else _default_eib_output_path(
                    params.start_date, params.end_date, params.project_code,
                )
            )
            summary = _eib.classify_and_build(
                invoices,
                output_path=out_path,
                allow_ambiguous_spend_cat=params.allow_ambiguous_spend_cat,
            )
            summary["skipped_rows"] = skipped
            return summary
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_build_gl_spend_category_map(
        params: _BuildGlSpendCategoryMapInput,
    ) -> dict[str, Any]:
        """Derive the GL → Workday Spend Category map from the JE training
        workbook. Highest-frequency Spend Category per GL account; mappings
        with dominance ≥0.80 are auto-confirmed, the rest land in the
        ambiguous bucket for operator review.
        """
        try:
            return _scm.derive_from_je_workbook(params.je_workbook_path)
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_list_ambiguous_spend_categories(
        params: _ListAmbiguousSpendCategoriesInput,
    ) -> dict[str, Any]:
        """Every expense GL where the auto-derived map's dominance is
        below 80%, sorted by sample count desc. Each entry includes the
        candidate Spend Categories with their JE counts so the operator
        can pick the right one in one pass.
        """
        try:
            ambiguous = _scm.list_ambiguous()
            return {
                "status": "ok",
                "count": len(ambiguous),
                "stats": _scm.stats(),
                "ambiguous": ambiguous,
            }
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_set_spend_category_override(
        params: _SetSpendCategoryOverrideInput,
    ) -> dict[str, Any]:
        """Set an operator-confirmed Spend Category for a GL account.
        Overrides always win, regardless of what the auto-derived map says.
        """
        try:
            return {
                "status": "ok",
                "override": _scm.set_override(
                    params.gl_account, params.spend_category, note=params.note,
                ),
            }
        except Exception as e:
            return _err(e)

    # ------------------------------------------------------------------ #
    # StaffWizard authoritative project sync (v0.9.0 spec change)
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def workflow_staffwizard_sync_projects(
        params: _StaffwizardSyncProjectsInput,
    ) -> dict[str, Any]:
        """Re-run the StaffWizard → project_registry sync against whatever
        Overall Reports are on disk. Normally this runs automatically as
        step 4 of `workflow_staffwizard_refresh_all`; call this manually
        after a hot-fix or when bootstrapping the registry from a backlog
        of reports.
        """
        try:
            import staffwizard_pipeline as _pipe
            detail = _pipe._parse_all_reports(_pipe.reports_dir())  # noqa: SLF001
            return _sps.sync_projects_from_rows(
                detail, active_window_days=params.active_window_days,
            )
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_list_active_staffwizard_projects(
        params: _ListActiveStaffwizardProjectsInput,
    ) -> dict[str, Any]:
        """List the currently-active StaffWizard projects, sorted by
        recency. This is the authoritative project set every receipt
        validates against.
        """
        try:
            return {
                "status": "ok",
                "count": len(_sps.list_active_projects()),
                "projects": _sps.list_active_projects(limit=params.limit),
            }
        except Exception as e:
            return _err(e)

    # ------------------------------------------------------------------ #
    # Receipt → project validation + chat-back picker
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def workflow_validate_receipt_project(
        params: _ValidateReceiptProjectInput,
    ) -> dict[str, Any]:
        """Decide whether a receipt's project_code is shippable. Returns
        valid/invalid plus the reason ('matched' / 'missing' /
        'unknown_code' / 'inactive'). When invalid, caller should fire
        `workflow_request_receipt_project_options` to chat-back the
        submitter.
        """
        try:
            meta = dict(params.receipt_meta or {})
            if params.project_code:
                meta["project_code"] = params.project_code
            res = _rpv.validate(meta)
            return {
                "status": "ok",
                "valid": res.valid,
                "project_code": res.project_code,
                "reason": res.reason,
                "needs_picker": res.needs_picker,
            }
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_request_receipt_project_options(
        params: _RequestReceiptProjectOptionsInput,
    ) -> dict[str, Any]:
        """Open a pending project-pick session for a parked receipt.
        DMs the submitter with two options:

            A — list active projects (system replies with the top-12 active
                StaffWizard projects; submitter replies with the code or name)
            B — new project (logs a request, parks the receipt, acks the
                submitter; coding handover deferred to v0.9.1)

        Returns the pending_id and the chat message text. With send=True,
        also fires the DM via gservices.
        """
        try:
            send_chat = _build_send_chat() if params.send else None
            return _rpv.request_options(
                params.submitter_id, params.receipt_id,
                receipt_meta=params.receipt_meta,
                channel_hint=params.channel,
                send_chat=send_chat,
            )
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_handle_picker_reply(
        params: _HandlePickerReplyInput,
    ) -> dict[str, Any]:
        """Handle a submitter's reply to the picker prompt.

        Reply rules:
            'A' / 'list'     → respond with the active project list
            'B' / 'new'      → log a new-project request (Option B stub)
            <code> / <name>  → resolve directly; on match, file the receipt;
                               on no match, re-prompt
        """
        try:
            send_chat = _build_send_chat() if params.send else None
            return _rpv.handle_picker_reply(
                params.submitter_id, params.reply_text,
                pending_id=params.pending_id,
                send_chat=send_chat,
            )
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_receipt_new_project_request(
        params: _RequestNewProjectInput,
    ) -> dict[str, Any]:
        """Option B stub: log a new-project request. Acks the submitter
        that the request is logged and the receipt is parked. Operator
        handover (folder tree creation, sheet, registry record) is the
        v0.9.1 work item — see roadmap.
        """
        try:
            send_chat = _build_send_chat() if params.send else None
            return _rpv.request_new_project(
                params.submitter_id, params.project_name,
                hint=params.hint, send_chat=send_chat,
            )
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_list_pending_picks(
        params: _ListPendingPicksInput,
    ) -> dict[str, Any]:
        """List pending project-pick sessions across all submitters.
        Filter by status to see only the open ones (`awaiting_pick`,
        `list_sent`) or the resolved/new-project entries.
        """
        try:
            return {
                "status": "ok",
                "open": _rpv.pending_count(),
                "entries": _rpv.list_pending(status=params.status),
                "new_project_requests": _rpv.list_new_project_requests(),
            }
        except Exception as e:
            return _err(e)


# --------------------------------------------------------------------------- #
# Chat-DM glue
# --------------------------------------------------------------------------- #


def _build_send_chat():
    """Returns a `(submitter_id, message_text) -> dict` callable that
    sends a Google Chat DM via the in-tree chat helper. Submitter id can
    be a chat user resource name or an email — chat_find_or_create_dm
    handles both.
    """
    def _send(submitter_id: str, message: str) -> dict:
        try:
            import gservices
            chat = gservices.chat_service()
            # Resolve submitter to a DM space.
            if submitter_id.startswith("spaces/"):
                space_name = submitter_id
            elif submitter_id.startswith("users/"):
                # Find or create a DM.
                resp = chat.spaces().findDirectMessage(name=submitter_id).execute()
                space_name = resp.get("name")
            else:
                # Treat as email — look up the user.
                resp = chat.spaces().findDirectMessage(
                    name=f"users/{submitter_id}",
                ).execute()
                space_name = resp.get("name")
            sent = chat.spaces().messages().create(
                parent=space_name, body={"text": message},
            ).execute()
            return {"status": "ok", "message_name": sent.get("name")}
        except Exception as e:
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}
    return _send
