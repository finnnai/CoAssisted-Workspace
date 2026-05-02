# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP wrappers for AP-7 (labor) + AP-8 (rollup) + AR-9 (invoicing).

Eight tools:

    AP-7:
      workflow_ingest_labor_report   StaffWizard Overall Report → per-project
                                      labor workbooks + structured ingest report
    AP-8:
      workflow_record_daily_fact     Operator/system-driven daily-fact write
      workflow_build_master_rollup   Three-tab master workbook (All Projects,
                                      PM Dashboard, Anomalies)

    AR-9:
      workflow_generate_customer_invoice  Build draft invoice from labor rows
      workflow_invoice_mark_sent          Transition draft → sent
      workflow_invoice_apply_payment      Track payment + status transitions
      workflow_ar_aging_report            Aging buckets + summary
      workflow_collections_due_today      Cadence-driven reminder candidates
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import ar_invoicing
import ar_send
import labor_ingest
import master_rollup
import project_registry
from logging_util import log


# =============================================================================
# Input schemas
# =============================================================================

class IngestLaborReportInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    report_path: str = Field(
        ...,
        description=(
            "Path to a StaffWizard Overall Report (.xls or .xlsx). "
            "Tilde and shell expansion honored."
        ),
    )
    output_dir: Optional[str] = Field(
        default=None,
        description=(
            "Where per-project labor workbooks land. Defaults to the "
            "report's parent directory; production callers pass the "
            "Surefox AP/Projects/ root so each project's Labor/Daily/ "
            "subtree gets stamped."
        ),
    )
    record_to_master: bool = Field(
        default=True,
        description=(
            "When True, also record the per-project labor totals into "
            "master_rollup_history.json so AP-8 dashboards stay current."
        ),
    )


class RecordDailyFactInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_code: str = Field(...)
    work_date: str = Field(..., description="YYYY-MM-DD")
    receipts: float = Field(default=0.0, ge=0)
    invoices: float = Field(default=0.0, ge=0)
    labor_cost: float = Field(default=0.0, ge=0)
    labor_revenue: float = Field(default=0.0, ge=0)


class BuildMasterRollupInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    output_path: str = Field(...)
    target_date: Optional[str] = Field(
        default=None,
        description=(
            "YYYY-MM-DD anchor for the PM Dashboard 'today' column. "
            "Defaults to yesterday (typical 6am refresh)."
        ),
    )


class GenerateCustomerInvoiceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_code: str = Field(...)
    period_start: str = Field(..., description="YYYY-MM-DD")
    period_end: str = Field(..., description="YYYY-MM-DD")
    labor_report_path: str = Field(
        ...,
        description=(
            "Path to a StaffWizard Overall Report covering the period. "
            "The shifts tagged to this project_code are pulled out and "
            "rolled up into invoice line items by post_description."
        ),
    )
    invoice_date: Optional[str] = Field(default=None, description="Defaults to today.")
    markup_pct: float = Field(default=0.0, ge=0, le=100)
    persist: bool = Field(
        default=True,
        description="When True, save the draft invoice to ar_invoices.json.",
    )


class InvoiceMarkSentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    invoice_id: str = Field(...)
    sent_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")


class InvoiceApplyPaymentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    invoice_id: str = Field(...)
    amount: float = Field(..., gt=0)
    paid_date: Optional[str] = Field(default=None, description="YYYY-MM-DD")


class AgingReportInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    as_of: Optional[str] = Field(default=None, description="YYYY-MM-DD; defaults to today.")
    project_code: Optional[str] = Field(default=None)
    customer_name: Optional[str] = Field(default=None)


class CollectionsDueInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    as_of: Optional[str] = Field(default=None, description="YYYY-MM-DD; defaults to today.")


class SendInvoiceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    invoice_id: str = Field(...)
    attach_xlsx: bool = Field(
        default=True,
        description="When True, attach an Excel rendering of the invoice alongside the HTML body.",
    )
    override_to: Optional[str] = Field(
        default=None,
        description=(
            "Send to this address instead of invoice.customer_email "
            "(e.g., for a test send to yourself before going live)."
        ),
    )


class SendCollectionReminderInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    invoice_id: str = Field(...)
    tier: Optional[str] = Field(
        default=None,
        description=(
            "Force a specific cadence tier (courtesy_reminder, "
            "first_followup, second_followup, third_followup, "
            "escalation_to_legal). When omitted, picks whatever the "
            "cadence ladder says is due today."
        ),
    )
    as_of: Optional[str] = Field(default=None, description="YYYY-MM-DD; defaults to today.")
    override_to: Optional[str] = Field(default=None)
    mode_override: Optional[str] = Field(
        default=None,
        description=(
            "One-shot override of config.ar.collections_mode for this "
            "specific call. One of 'send', 'draft', 'disabled'. "
            "Defaults to whatever config + per-tier override resolve to."
        ),
    )


class SetCollectionsModeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    mode: Optional[str] = Field(
        default=None,
        description=(
            "Set the base mode for all tiers. One of 'send', 'draft', "
            "'disabled'. Omit to leave the base unchanged and only "
            "update the per-tier override."
        ),
    )
    tier: Optional[str] = Field(
        default=None,
        description=(
            "When set with mode, applies the mode to this single tier "
            "(in collections_mode_per_tier). Tiers: courtesy_reminder, "
            "first_followup, second_followup, third_followup, "
            "escalation_to_legal."
        ),
    )


# =============================================================================
# Helpers
# =============================================================================

def _parse_date(raw: Optional[str]) -> Optional[_dt.date]:
    if not raw:
        return None
    return _dt.datetime.strptime(raw, "%Y-%m-%d").date()


# =============================================================================
# Registration
# =============================================================================

def register(mcp) -> None:

    # ----- AP-7: labor ingestion -----------------------------------------------

    @mcp.tool(
        name="workflow_ingest_labor_report",
        annotations={
            "title": "AP-7 — ingest StaffWizard Overall Report → per-project labor",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def workflow_ingest_labor_report(
        params: IngestLaborReportInput,
    ) -> str:
        """Parse a StaffWizard Overall Report, group shifts by project,
        write per-project Labor/Daily workbooks, and (by default) stamp
        the daily totals into master_rollup_history.json so AP-8 stays
        current.
        """
        try:
            report = labor_ingest.ingest_report(
                params.report_path, output_dir=params.output_dir,
            )

            # Optional: feed master_rollup history.
            if params.record_to_master:
                work_date = _parse_date(report.get("work_date"))
                if work_date:
                    for proj in report.get("projects", []):
                        master_rollup.record_daily_fact(
                            master_rollup.DailyFact(
                                project_code=proj["project_code"],
                                work_date=work_date,
                                labor_cost=float(proj.get("total_cost") or 0),
                                labor_revenue=float(proj.get("total_revenue") or 0),
                            )
                        )

            log.info(
                "ingest_labor_report: %d shifts, %d projects, %d unmapped",
                report.get("shifts", 0),
                len(report.get("projects") or []),
                len(report.get("unmapped") or []),
            )
            return json.dumps(report, indent=2, default=str)
        except Exception as e:
            log.exception("ingest_labor_report failed")
            return json.dumps({"status": "error", "error": str(e)})

    # ----- AP-8: master rollup -------------------------------------------------

    @mcp.tool(
        name="workflow_record_daily_fact",
        annotations={
            "title": "AP-8 — record one day's spend for a project",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_record_daily_fact(params: RecordDailyFactInput) -> str:
        """Stamp a (project, date) → spend record. Idempotent — same
        (project, date) overwrites prior values (use for corrections)."""
        try:
            master_rollup.record_daily_fact(master_rollup.DailyFact(
                project_code=params.project_code,
                work_date=_parse_date(params.work_date),
                receipts=params.receipts,
                invoices=params.invoices,
                labor_cost=params.labor_cost,
                labor_revenue=params.labor_revenue,
            ))
            return json.dumps({"status": "ok"})
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_build_master_rollup",
        annotations={
            "title": "AP-8 — build the three-tab master rollup workbook",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_build_master_rollup(
        params: BuildMasterRollupInput,
    ) -> str:
        """Generate the master rollup .xlsx with All Projects + PM
        Dashboard + Anomalies tabs. Pulls from
        master_rollup_history.json (populated by AP-7 ingestion or
        manual record_daily_fact calls)."""
        try:
            target = _parse_date(params.target_date)
            out = master_rollup.build_master_workbook(
                output_path=params.output_path, target_date=target,
            )
            return json.dumps({
                "status": "ok",
                "output_path": str(out),
                "target_date": (target or (_dt.date.today() - _dt.timedelta(days=1))).isoformat(),
            }, indent=2)
        except Exception as e:
            log.exception("build_master_rollup failed")
            return json.dumps({"status": "error", "error": str(e)})

    # ----- AR-9: invoicing -----------------------------------------------------

    @mcp.tool(
        name="workflow_generate_customer_invoice",
        annotations={
            "title": "AR-9 — generate a customer invoice from labor",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": False, "openWorldHint": False,
        },
    )
    async def workflow_generate_customer_invoice(
        params: GenerateCustomerInvoiceInput,
    ) -> str:
        """Build a draft customer invoice from the project's billable
        labor in the period. Persists to ar_invoices.json by default."""
        try:
            registry_match = project_registry.get(params.project_code)
            if not registry_match:
                return json.dumps({
                    "status": "error",
                    "error": f"Unknown project_code {params.project_code!r}",
                })

            parsed = labor_ingest.parse_overall_report(params.labor_report_path)
            # Filter labor rows to this project's StaffWizard job.
            sw_job = registry_match.get("staffwizard_job_number") or ""
            sw_desc = registry_match.get("staffwizard_job_desc") or ""
            project_rows = [
                r for r in parsed.rows
                if (r.job_number or "").strip().lower() == sw_job.strip().lower()
                and (r.job_description or "").strip().lower() == sw_desc.strip().lower()
            ]

            invoice = ar_invoicing.generate_invoice_from_labor(
                params.project_code,
                period_start=_parse_date(params.period_start),
                period_end=_parse_date(params.period_end),
                labor_rows=project_rows,
                invoice_date=_parse_date(params.invoice_date),
                markup_pct=params.markup_pct,
            )
            if params.persist:
                ar_invoicing.persist(invoice)
            log.info(
                "generated invoice %s: %d lines, total $%.2f",
                invoice.invoice_number, len(invoice.lines), invoice.total,
            )
            return json.dumps(ar_invoicing._serialize(invoice), indent=2, default=str)
        except Exception as e:
            log.exception("generate_customer_invoice failed")
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_invoice_mark_sent",
        annotations={
            "title": "AR-9 — transition invoice from draft to sent",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_invoice_mark_sent(params: InvoiceMarkSentInput) -> str:
        try:
            ok = ar_invoicing.mark_sent(
                params.invoice_id, sent_date=_parse_date(params.sent_date),
            )
            return json.dumps({
                "status": "ok" if ok else "not_found",
                "invoice_id": params.invoice_id,
            })
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_invoice_apply_payment",
        annotations={
            "title": "AR-9 — apply a customer payment",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": False, "openWorldHint": False,
        },
    )
    async def workflow_invoice_apply_payment(
        params: InvoiceApplyPaymentInput,
    ) -> str:
        """Apply a payment to an invoice. Tracks partial → paid when
        the cumulative paid_amount reaches total."""
        try:
            ok = ar_invoicing.apply_payment(
                params.invoice_id,
                params.amount,
                paid_date=_parse_date(params.paid_date),
            )
            if not ok:
                return json.dumps({
                    "status": "not_found", "invoice_id": params.invoice_id,
                })
            updated = ar_invoicing.get(params.invoice_id)
            return json.dumps({
                "status": "ok",
                "invoice_id": params.invoice_id,
                "new_status": updated.status if updated else None,
                "paid_amount": updated.paid_amount if updated else None,
                "outstanding": (
                    round(updated.total - updated.paid_amount, 2)
                    if updated else None
                ),
            }, indent=2)
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_ar_aging_report",
        annotations={
            "title": "AR-9 — aging report (open invoices by bucket)",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_ar_aging_report(params: AgingReportInput) -> str:
        """Return the aging buckets for open invoices as of a date.
        Optional filters by project_code or customer_name."""
        try:
            as_of = _parse_date(params.as_of)
            entries = ar_invoicing.compute_aging(
                as_of=as_of,
                project_code=params.project_code,
                customer_name=params.customer_name,
            )
            summary = ar_invoicing.aging_summary(as_of=as_of)
            return json.dumps({
                "status": "ok",
                "as_of": (as_of or _dt.date.today()).isoformat(),
                "summary_by_bucket": summary,
                "entries": [
                    {
                        "invoice_id": e.invoice.invoice_id,
                        "invoice_number": e.invoice.invoice_number,
                        "project_code": e.invoice.project_code,
                        "customer_name": e.invoice.customer_name,
                        "total": e.invoice.total,
                        "paid": e.invoice.paid_amount,
                        "outstanding": e.outstanding,
                        "due_date": e.invoice.due_date.isoformat(),
                        "days_past_due": e.days_past_due,
                        "bucket": e.bucket,
                    }
                    for e in entries
                ],
            }, indent=2)
        except Exception as e:
            log.exception("ar_aging_report failed")
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_collections_due_today",
        annotations={
            "title": "AR-9 — collections candidates per cadence ladder",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_collections_due_today(
        params: CollectionsDueInput,
    ) -> str:
        """Return invoices that should get a collections reminder today.
        Each candidate has a `reminder_type` from the 5-tier cadence
        (courtesy_reminder → escalation_to_legal). Already-sent tiers
        are filtered out so reminders don't double-fire."""
        try:
            as_of = _parse_date(params.as_of)
            candidates = ar_invoicing.collections_due_today(as_of=as_of)
            return json.dumps({
                "status": "ok",
                "as_of": (as_of or _dt.date.today()).isoformat(),
                "count": len(candidates),
                "candidates": [
                    {
                        "invoice_id": c.invoice.invoice_id,
                        "invoice_number": c.invoice.invoice_number,
                        "project_code": c.invoice.project_code,
                        "customer_name": c.invoice.customer_name,
                        "customer_email": c.invoice.customer_email,
                        "outstanding": round(
                            c.invoice.total - c.invoice.paid_amount, 2
                        ),
                        "days_past_due": c.days_past_due,
                        "reminder_type": c.reminder_type,
                        "last_event_iso": c.last_event_iso,
                    }
                    for c in candidates
                ],
            }, indent=2)
        except Exception as e:
            log.exception("collections_due_today failed")
            return json.dumps({"status": "error", "error": str(e)})

    # ----- AR-9 send wire-up ---------------------------------------------------

    @mcp.tool(
        name="workflow_send_invoice",
        annotations={
            "title": "AR-9 — send a draft invoice to the customer",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": False, "openWorldHint": True,
        },
    )
    async def workflow_send_invoice(params: SendInvoiceInput) -> str:
        """Render the invoice as an HTML email body + (optional) Excel
        attachment, send via Gmail to invoice.customer_email (or
        override_to), and on success transition the invoice from
        draft to sent."""
        try:
            result = ar_send.send_invoice(
                params.invoice_id,
                attach_xlsx=params.attach_xlsx,
                override_to=params.override_to,
            )
            log.info(
                "send_invoice %s → %s: sent=%s",
                params.invoice_id,
                result.get("recipient"),
                result.get("sent"),
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            log.exception("send_invoice failed")
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_send_collection_reminder",
        annotations={
            "title": "AR-9 — send (or draft) a collection reminder",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": False, "openWorldHint": True,
        },
    )
    async def workflow_send_collection_reminder(
        params: SendCollectionReminderInput,
    ) -> str:
        """Send (or draft) a collections reminder per the configured mode.

        Per Finnn 2026-05-01 Part F + Joshua's question-3 answer:
        every tier defaults to "draft" (operator approves via
        workflow_approve_draft, which advances state on send), and
        escalation_to_legal defaults to "disabled" (operator composes
        by hand). Override the global behavior via
        config.ar.collections_mode + collections_mode_per_tier; override
        per-call via the `mode_override` param.

        Returns include the `mode` and a `status` string:
            - sent      : went out via Gmail (mode=send only)
            - drafted   : queued in workflow_list_drafts (mode=draft)
            - skipped   : tier disabled by config (mode=disabled)
        """
        try:
            as_of_date = _parse_date(params.as_of)
            result = ar_send.send_collection_reminder(
                params.invoice_id,
                tier=params.tier,
                as_of=as_of_date,
                override_to=params.override_to,
                mode_override=params.mode_override,
            )
            log.info(
                "send_collection_reminder %s tier=%s mode=%s status=%s",
                params.invoice_id,
                result.get("tier"),
                result.get("mode"),
                result.get("status"),
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            log.exception("send_collection_reminder failed")
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_set_collections_mode",
        annotations={
            "title": "AR-9 — set the collections-mode gate",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_set_collections_mode(
        params: SetCollectionsModeInput,
    ) -> str:
        """Update config.ar.collections_mode + collections_mode_per_tier.

        Three modes: send / draft / disabled. Per Finnn 2026-05-01 Part
        F, the safe default is 'draft' for every tier with
        escalation_to_legal at 'disabled' (operator composes by hand).

        Use `mode` alone to update the base.
        Use `mode` + `tier` to update a single tier's per-tier override.
        Returns the new resolved config.
        """
        try:
            import config as _config_mod
            valid = {"send", "draft", "disabled"}
            if params.mode and params.mode not in valid:
                return json.dumps({
                    "status": "error",
                    "error": f"Invalid mode {params.mode!r}; expected one of {sorted(valid)}",
                })

            ar_block = dict(_config_mod.get("ar", {}) or {})
            per_tier = dict(ar_block.get("collections_mode_per_tier") or {})

            if params.mode and not params.tier:
                ar_block["collections_mode"] = params.mode
            elif params.mode and params.tier:
                per_tier[params.tier] = params.mode
                ar_block["collections_mode_per_tier"] = per_tier
            else:
                return json.dumps({
                    "status": "error",
                    "error": "Provide `mode` (and optionally `tier`).",
                })

            _config_mod.set("ar", ar_block)
            log.info(
                "set_collections_mode: base=%s per_tier=%s",
                ar_block.get("collections_mode"),
                ar_block.get("collections_mode_per_tier"),
            )
            return json.dumps({
                "status": "ok",
                "ar": ar_block,
            }, indent=2)
        except Exception as e:
            log.exception("set_collections_mode failed")
            return json.dumps({"status": "error", "error": str(e)})
