# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP wrappers for P5 workflows."""

from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import p5_workflows as p5
from errors import format_error
from logging_util import log


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


class SpendDashboardInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    invoice_rows: list[dict] = Field(
        ...,
        description=("Invoice rows from project sheets. "
                     "Each must include project_code, vendor, total, invoice_date."),
    )
    project_field: str = Field(default="project_code")
    vendor_field: str = Field(default="vendor")
    total_field: str = Field(default="total")
    date_field: str = Field(default="invoice_date")
    budgets: Optional[dict] = Field(
        default=None,
        description="Optional {project_code: budget_usd}.",
    )
    today_override: Optional[str] = Field(default=None,
                                           description="YYYY-MM-DD for testing.")


class PnlRollupInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    invoice_rows: list[dict] = Field(..., description="AP invoices (cost side).")
    revenue_rows: list[dict] = Field(..., description="Issued invoices (revenue side).")
    project_field: str = Field(default="project_code")
    invoice_total_field: str = Field(default="total")
    revenue_total_field: str = Field(default="total")


class DupDetectInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    invoice_rows: list[dict] = Field(...)
    amount_tolerance_pct: float = Field(default=1.0, ge=0, le=10)
    date_tolerance_days: int = Field(default=7, ge=0, le=60)


class AnomalyDetectInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    invoice_rows: list[dict] = Field(...)
    iqr_k: float = Field(default=1.5, ge=0.5, le=4.0)
    min_history: int = Field(default=3, ge=2, le=20)


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_project_spend_dashboard",
        annotations={"title": "Per-project spend dashboard",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_project_spend_dashboard(params: SpendDashboardInput) -> str:
        """Roll up spend per project: YTD, last 30d, MoM delta, top vendors,
        and percent-of-budget when budgets are provided."""
        try:
            today = (_dt.date.fromisoformat(params.today_override)
                     if params.today_override else None)
            rows = p5.build_project_spend_dashboard(
                params.invoice_rows,
                project_field=params.project_field,
                vendor_field=params.vendor_field,
                total_field=params.total_field,
                date_field=params.date_field,
                budgets=params.budgets,
                today=today,
            )
            return json.dumps({"projects": rows, "count": len(rows)},
                              indent=2, default=str)
        except Exception as e:
            return format_error("workflow_project_spend_dashboard", e)

    @mcp.tool(
        name="workflow_project_pnl",
        annotations={"title": "Per-project profit & loss rollup",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_project_pnl(params: PnlRollupInput) -> str:
        """Per-project margin = revenue - spend."""
        try:
            rows = p5.build_pnl_rollup(
                params.invoice_rows, params.revenue_rows,
                project_field=params.project_field,
                invoice_total_field=params.invoice_total_field,
                revenue_total_field=params.revenue_total_field,
            )
            return json.dumps({"projects": rows, "count": len(rows)},
                              indent=2, default=str)
        except Exception as e:
            return format_error("workflow_project_pnl", e)

    @mcp.tool(
        name="workflow_duplicate_invoices",
        annotations={"title": "Find semantically duplicate invoices",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_duplicate_invoices(params: DupDetectInput) -> str:
        """Same vendor, similar amount, close dates → flag possible duplicate."""
        try:
            pairs = p5.find_duplicate_invoices(
                params.invoice_rows,
                amount_tolerance_pct=params.amount_tolerance_pct,
                date_tolerance_days=params.date_tolerance_days,
            )
            log.info("dup_invoices: %d pair(s) flagged", len(pairs))
            return json.dumps({"count": len(pairs), "pairs": pairs},
                              indent=2, default=str)
        except Exception as e:
            return format_error("workflow_duplicate_invoices", e)

    @mcp.tool(
        name="workflow_ap_anomalies",
        annotations={"title": "Detect AP invoice anomalies (Tukey IQR)",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_ap_anomalies(params: AnomalyDetectInput) -> str:
        """Per-vendor median + IQR baseline. Flag invoices outside Tukey fences."""
        try:
            anomalies = p5.detect_ap_anomalies(
                params.invoice_rows, iqr_k=params.iqr_k,
                min_history=params.min_history,
            )
            return json.dumps({"count": len(anomalies),
                               "anomalies": anomalies},
                              indent=2, default=str)
        except Exception as e:
            return format_error("workflow_ap_anomalies", e)
