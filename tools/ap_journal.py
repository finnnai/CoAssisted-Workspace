# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP wrappers for the AP-2 / AP-3 surface.

Exposes seven tools that drive card-statement → Workday Journal EIB
end-to-end from Cowork:

    workflow_reconcile_card_statement
        Parse an AMEX or WEX export, classify spend GL via the AP-3
        ladder, write a two-sheet Workday EIB to disk.

    workflow_gl_classify_preview
        Dry-run: parse + classify, return what AP-2 would post without
        writing the EIB. Useful for checking confidence distribution
        before committing.

    workflow_gl_merchant_map_set
        Operator confirmation / override. Persists to gl_merchant_map.json
        so the next classification of the same merchant short-circuits
        to tier 0 (HIGH confidence).

    workflow_gl_merchant_map_list
        Inventory of learned mappings. Filterable by source.

    workflow_gl_memo_index_status
        Diagnostic: is the JE-trained tier 2 model loaded? When was it
        trained? How many GL accounts does it cover?

    workflow_cost_center_map_set
        Set a cardholder_email → cost_center OR department → cost_center
        mapping. Used by AP-2 when populating Cost Center worktags.

    workflow_cost_center_map_list
        Inventory of CC mappings.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import cost_center_map
import gl_classifier
import gl_memo_classifier
import gl_merchant_map
import workday_journal_eib as wje
from logging_util import log


# =============================================================================
# Input schemas
# =============================================================================

class ReconcileCardStatementInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    card_type: str = Field(
        ...,
        description="Card source. Supported: 'amex', 'wex'. (Chase, Divvy, Ramp on backlog.)",
    )
    csv_path: str = Field(
        ...,
        description="Path to the export CSV. Tilde and shell expansion honored.",
    )
    period_start: str = Field(
        ...,
        description="Statement period start, YYYY-MM-DD.",
    )
    period_end: str = Field(
        ...,
        description="Statement period end, YYYY-MM-DD. Drives the Workday Accounting Date.",
    )
    output_path: Optional[str] = Field(
        default=None,
        description=(
            "Where to write the EIB workbook. Defaults to "
            "~/Developer/google_workspace_mcp/dist/{card}_{period_end}_journal.xlsx."
        ),
    )
    company_id: str = Field(default="CO-100", description="Workday Company reference.")
    currency: str = Field(default="USD")
    default_cost_center: str = Field(
        default="CC100",
        description="Cost Center used for any cardholder not in cost_center_map.",
    )
    include_pending: bool = Field(
        default=False,
        description="AMEX only — when True, picks up PENDING transactions in addition to CLEARED.",
    )


class GLClassifyPreviewInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    card_type: str = Field(...)
    csv_path: str = Field(...)
    include_pending: bool = Field(default=False)
    sample_size: int = Field(
        default=0,
        ge=0,
        description="When > 0, only classify the first N transactions. 0 = all.",
    )


class GLMerchantMapSetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    merchant_name: str = Field(...)
    gl_account: str = Field(
        ...,
        description='Workday Ledger Account string, e.g. "62300:IT Expenses".',
    )
    cardholder_email: Optional[str] = Field(
        default=None,
        description="When set, the override applies only to this cardholder.",
    )
    user: Optional[str] = Field(default=None, description="Who made the change (audit trail).")


class GLMerchantMapListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    source: Optional[str] = Field(
        default=None,
        description='Filter by source. One of "operator", "import", "training".',
    )


class CostCenterMapSetInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    cardholder_email: Optional[str] = Field(default=None)
    department: Optional[str] = Field(default=None)
    cost_center: str = Field(..., description='Workday Cost Center reference, e.g. "CC100".')
    user: Optional[str] = Field(default=None)


class CostCenterMapListInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    kind: Optional[str] = Field(
        default=None,
        description='Filter by kind: "email" or "dept".',
    )


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


# =============================================================================
# Helpers
# =============================================================================

def _resolve_path(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def _parse_iso_date(raw: str) -> _dt.date:
    return _dt.datetime.strptime(raw, "%Y-%m-%d").date()


def _default_output_path(card_type: str, period_end: _dt.date) -> Path:
    project_root = Path(__file__).resolve().parent.parent
    dist = project_root / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    return dist / f"{card_type}_{period_end:%Y-%m-%d}_journal.xlsx"


# =============================================================================
# Registration
# =============================================================================

def register(mcp) -> None:

    # ----- AP-2 reconcile -----------------------------------------------------

    @mcp.tool(
        name="workflow_reconcile_card_statement",
        annotations={
            "title": "Card statement → Workday Journal EIB",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": False, "openWorldHint": False,
        },
    )
    async def workflow_reconcile_card_statement(
        params: ReconcileCardStatementInput,
    ) -> str:
        """Parse a card statement CSV, classify spend GL per transaction
        via the AP-3 ladder, and write a two-sheet Workday Journal EIB.

        Returns a JSON report with output path, transaction count,
        line count, classifier-tier breakdown, and orphan count
        (transactions that fell through to the clearing account and
        need operator review before posting).
        """
        try:
            csv_path = _resolve_path(params.csv_path)
            period_start = _parse_iso_date(params.period_start)
            period_end = _parse_iso_date(params.period_end)
            output = (
                _resolve_path(params.output_path)
                if params.output_path
                else _default_output_path(params.card_type, period_end)
            )

            # Parse.
            if params.card_type == "amex":
                txns = wje.parse_amex_csv(
                    csv_path, include_pending=params.include_pending
                )
            elif params.card_type == "wex":
                txns = wje.parse_wex_csv(csv_path)
            else:
                return json.dumps({
                    "error": f"Unsupported card_type {params.card_type!r}; "
                             f"supported: amex, wex.",
                })

            # Build EIB. Cost-center map is pulled from the persistent store.
            cc_map = cost_center_map.export_lookup_dict()
            result = wje.build_journal_eib(
                txns,
                output,
                card_type=params.card_type,
                period_start=period_start,
                period_end=period_end,
                cardholder_cost_center_map=cc_map,
                company_id=params.company_id,
                currency=params.currency,
                default_cost_center=params.default_cost_center,
            )

            log.info(
                "reconciled %s: %d txns → %d lines, %d orphans, output=%s",
                params.card_type,
                result.n_transactions,
                result.n_lines_written,
                result.n_orphan_no_gl,
                result.output_path,
            )

            return json.dumps({
                "status": "ok",
                "output_path": str(result.output_path),
                "transactions": result.n_transactions,
                "lines_written": result.n_lines_written,
                "orphans_in_clearing": result.n_orphan_no_gl,
                "tier_breakdown": result.classifier_tier_counts,
                "warnings": result.warnings,
                "next_step": (
                    f"Open {result.output_path} in Workday's Submit "
                    "Accounting Journals task. Review orphans (if any) "
                    "before clicking Submit."
                ),
            }, indent=2)
        except Exception as e:
            log.exception("reconcile_card_statement failed")
            return json.dumps({"status": "error", "error": str(e)})

    # ----- AP-3 preview / classify --------------------------------------------

    @mcp.tool(
        name="workflow_gl_classify_preview",
        annotations={
            "title": "Dry-run GL classification on a card statement",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_gl_classify_preview(
        params: GLClassifyPreviewInput,
    ) -> str:
        """Classify every transaction in a CSV without writing an EIB.

        Returns per-transaction (merchant, amount, predicted GL,
        confidence, tier, reason) so an operator can spot misroutes
        before generating the journal.
        """
        try:
            csv_path = _resolve_path(params.csv_path)
            if params.card_type == "amex":
                txns = wje.parse_amex_csv(
                    csv_path, include_pending=params.include_pending
                )
            elif params.card_type == "wex":
                txns = wje.parse_wex_csv(csv_path)
            else:
                return json.dumps({
                    "error": f"Unsupported card_type {params.card_type!r}",
                })
            if params.sample_size > 0:
                txns = txns[: params.sample_size]

            preview = []
            tier_counts: dict[str, int] = {}
            confidence_counts: dict[str, int] = {}
            for t in txns:
                memo = (t.raw_notes or "").strip()
                result = gl_classifier.classify_transaction(
                    merchant_name=t.merchant_name,
                    mcc_code=t.mcc_code,
                    memo=memo,
                    amount=abs(t.amount),
                    cardholder_email=t.cardholder_email,
                    department_hint=t.department,
                )
                tier_counts[result.tier_used.value] = (
                    tier_counts.get(result.tier_used.value, 0) + 1
                )
                confidence_counts[result.confidence.value] = (
                    confidence_counts.get(result.confidence.value, 0) + 1
                )
                preview.append({
                    "date": t.transaction_date.isoformat(),
                    "cardholder": t.cardholder_name,
                    "merchant": t.merchant_name,
                    "amount": t.amount,
                    "predicted_gl": result.gl_account,
                    "confidence": result.confidence.value,
                    "tier": result.tier_used.value,
                    "reason": result.reason,
                })

            return json.dumps({
                "status": "ok",
                "transaction_count": len(preview),
                "tier_breakdown": tier_counts,
                "confidence_breakdown": confidence_counts,
                "transactions": preview,
            }, indent=2)
        except Exception as e:
            log.exception("gl_classify_preview failed")
            return json.dumps({"status": "error", "error": str(e)})

    # ----- AP-3 merchant map management ---------------------------------------

    @mcp.tool(
        name="workflow_gl_merchant_map_set",
        annotations={
            "title": "Set or override a merchant → GL account mapping",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_gl_merchant_map_set(
        params: GLMerchantMapSetInput,
    ) -> str:
        """Persist an operator-confirmed mapping. Tier 0 short-circuits
        future classifications of this merchant to HIGH confidence."""
        try:
            gl_merchant_map.learn(
                params.merchant_name,
                params.gl_account,
                source="operator",
                cardholder_email=params.cardholder_email,
                user=params.user,
            )
            log.info(
                "gl_merchant_map.learn %r → %r (cardholder=%s)",
                params.merchant_name,
                params.gl_account,
                params.cardholder_email,
            )
            return json.dumps({"status": "ok"})
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_gl_merchant_map_list",
        annotations={
            "title": "Inventory of learned merchant → GL mappings",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_gl_merchant_map_list(
        params: GLMerchantMapListInput,
    ) -> str:
        """Return all learned mappings, optionally filtered by source.
        Includes hit counts so operators can see what's getting used."""
        try:
            entries = gl_merchant_map.list_all(source=params.source)
            stats = gl_merchant_map.stats()
            return json.dumps({
                "status": "ok",
                "stats": stats,
                "entries": entries,
            }, indent=2)
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

    # ----- AP-3 memo-index diagnostic -----------------------------------------

    @mcp.tool(
        name="workflow_gl_memo_index_status",
        annotations={
            "title": "JE-trained memo classifier diagnostic",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_gl_memo_index_status(params: EmptyInput) -> str:
        """Return whether the tier-2 JE-trained matcher is loaded, when
        it was trained, and how many GL accounts it covers."""
        try:
            return json.dumps(gl_memo_classifier.index_status(), indent=2)
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

    # ----- AP-2 cost-center map management ------------------------------------

    @mcp.tool(
        name="workflow_cost_center_map_set",
        annotations={
            "title": "Set a cardholder/department → Cost Center mapping",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_cost_center_map_set(
        params: CostCenterMapSetInput,
    ) -> str:
        """Persist a cardholder_email or department → cost_center mapping.
        AP-2's EIB writer pulls these on every reconciliation."""
        try:
            if not params.cardholder_email and not params.department:
                return json.dumps({
                    "status": "error",
                    "error": "Provide cardholder_email OR department.",
                })
            if params.cardholder_email:
                cost_center_map.set_for_email(
                    params.cardholder_email,
                    params.cost_center,
                    source="operator",
                    user=params.user,
                )
            if params.department:
                cost_center_map.set_for_department(
                    params.department,
                    params.cost_center,
                    source="operator",
                    user=params.user,
                )
            return json.dumps({"status": "ok"})
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_cost_center_map_list",
        annotations={
            "title": "Inventory of cardholder/department → CC mappings",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_cost_center_map_list(
        params: CostCenterMapListInput,
    ) -> str:
        """Return all CC mappings, optionally filtered by kind (email/dept)."""
        try:
            entries = cost_center_map.list_all(kind=params.kind)
            return json.dumps({
                "status": "ok",
                "stats": cost_center_map.stats(),
                "entries": entries,
            }, indent=2)
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})
