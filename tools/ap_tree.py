# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP wrappers for AP-5 (project router) + AP-6 (project tree).

Five tools:

    workflow_register_new_project
        Operator-driven full Drive subtree creation for a new project.
        Wraps ap_tree.register_new_project — builds the 7 subfolders +
        current month bucket, persists Drive IDs into project_registry.

    workflow_audit_filing_tree
        Daily scan for unexpected files (manual additions that bypassed
        the capture pipeline). Returns a per-project report with
        suspicious-file lists.

    workflow_ensure_month_subtree
        Lazy month-bucket creation. Idempotent — safe to call on every
        receipt write, does nothing when the bucket already exists.

    workflow_route_project
        AP-5 routing decision. Takes (sender, subject, body, timestamp,
        explicit_code) → returns a project_code + confidence + tier.
        Used by the sweep loop and exposed for ad-hoc operator queries.

    workflow_project_registry_list
        Inventory of registered projects with all Wave 2 fields.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import ap_sweep
import ap_tree
import project_registry
import project_router
from logging_util import log


# =============================================================================
# Input schemas
# =============================================================================

class RegisterNewProjectInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    project_name: str = Field(...)
    code: str = Field(..., description='Project code (uppercase preferred). E.g. "GE1".')
    client: Optional[str] = Field(default=None)
    name_aliases: Optional[list[str]] = Field(default=None)
    assigned_team_emails: Optional[list[str]] = Field(default=None)
    sender_emails: Optional[list[str]] = Field(default=None)
    staffwizard_job_number: Optional[str] = Field(default=None)
    staffwizard_job_desc: Optional[str] = Field(default=None)
    billing_origin_state: str = Field(
        default="CA",
        description='2-letter state. "NY" unlocks the weekly-billing option.',
    )
    billing_terms: str = Field(default="Net-15")
    billing_cadence: str = Field(default="monthly")
    customer_email: Optional[str] = Field(default=None)
    ap_root_folder_id: Optional[str] = Field(
        default=None,
        description=(
            "Drive ID of 'Surefox AP'. Use this if you don't know the "
            "Projects subfolder ID — AP-6 will look it up by name."
        ),
    )
    projects_parent_folder_id: Optional[str] = Field(
        default=None,
        description=(
            "Drive ID of 'Surefox AP/Projects'. Direct path — preferred "
            "when known."
        ),
    )


class AuditFilingTreeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    age_threshold_minutes: int = Field(
        default=60,
        ge=1,
        description="Files modified within this window are checked against AP-6 naming.",
    )


class EnsureMonthSubtreeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    code: str = Field(...)
    yyyy_mm: Optional[str] = Field(
        default=None,
        description='YYYY-MM bucket. Defaults to current month.',
    )
    kinds: list[str] = Field(
        default=["receipts", "invoices"],
        description="Which subfolders to ensure month buckets under.",
    )


class RouteProjectInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    sender_email: Optional[str] = Field(default=None)
    subject: Optional[str] = Field(default=None)
    body: Optional[str] = Field(default=None)
    timestamp: Optional[str] = Field(
        default=None,
        description='ISO-8601 timestamp. Used for calendar/Geotab tiebreakers.',
    )
    explicit_code: Optional[str] = Field(default=None)
    use_llm: bool = Field(default=True)


class ProjectRegistryListInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_only: bool = Field(default=True)


class APSweepCycleInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    email_label: str = Field(default="AP/Inbound")
    chat_space_id: str = Field(default="spaces/AAQAly0xFuE")
    triage_folder_id: str = Field(default="1wBnOtbMVBrf0B5idKq_1teOKVlAKCtTY")
    max_items_per_source: int = Field(default=50, ge=1, le=200)
    dry_run: bool = Field(
        default=False,
        description=(
            "When True, decide routing for each item but don't download "
            "files or modify mail/chat state. Useful for previewing."
        ),
    )


# =============================================================================
# Registration
# =============================================================================

def register(mcp) -> None:

    @mcp.tool(
        name="workflow_register_new_project",
        annotations={
            "title": "Create a new project's Drive subtree + register routing",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def workflow_register_new_project(
        params: RegisterNewProjectInput,
    ) -> str:
        """Build the full Drive subtree for a new project (7 subfolders +
        current-month bucket under Receipts and Invoices), persist all
        Drive folder IDs into project_registry, and configure the AP-5
        routing fields (name aliases, team, StaffWizard job link,
        billing config).

        Idempotent — safe to re-run; folders that already exist are
        reused, registry record is updated.
        """
        try:
            result = ap_tree.register_new_project(
                project_name=params.project_name,
                code=params.code,
                client=params.client,
                name_aliases=params.name_aliases,
                assigned_team_emails=params.assigned_team_emails,
                sender_emails=params.sender_emails,
                staffwizard_job_number=params.staffwizard_job_number,
                staffwizard_job_desc=params.staffwizard_job_desc,
                billing_origin_state=params.billing_origin_state,
                billing_terms=params.billing_terms,
                billing_cadence=params.billing_cadence,
                customer_email=params.customer_email,
                ap_root_folder_id=params.ap_root_folder_id,
                projects_parent_folder_id=params.projects_parent_folder_id,
            )
            log.info(
                "register_new_project: %s code=%s",
                params.project_name, params.code,
            )
            return json.dumps(result, indent=2, default=str)
        except Exception as e:
            log.exception("register_new_project failed")
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_audit_filing_tree",
        annotations={
            "title": "Daily AP-6 audit — surface manual Drive additions",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def workflow_audit_filing_tree(
        params: AuditFilingTreeInput,
    ) -> str:
        """Scan every active project's Drive subtree for files that
        bypassed the capture pipeline. A 'suspicious' file is one that
        was modified within the threshold window AND has a name not
        matching the AP-6 convention `YYYY-MM-DD_*_amount_type.ext`.

        Persists the report to ap_tree_audit.json (gitignored) so the
        next run can diff against it.
        """
        try:
            report = ap_tree.audit_filing_tree(
                age_threshold_minutes=params.age_threshold_minutes
            )
            return json.dumps(report, indent=2, default=str)
        except Exception as e:
            log.exception("audit_filing_tree failed")
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_ensure_month_subtree",
        annotations={
            "title": "Lazy-create {YYYY-MM}/ buckets under a project's subfolders",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def workflow_ensure_month_subtree(
        params: EnsureMonthSubtreeInput,
    ) -> str:
        """Ensure {YYYY-MM}/ exists under each requested subfolder for
        the given project. Idempotent — repeated calls return the same
        folder IDs.
        """
        try:
            when = None
            if params.yyyy_mm:
                year, month = params.yyyy_mm.split("-")
                when = _dt.date(int(year), int(month), 1)
            result = ap_tree.ensure_month_subtree(
                params.code,
                when=when,
                kinds=tuple(params.kinds),
            )
            return json.dumps(result, indent=2)
        except Exception as e:
            log.exception("ensure_month_subtree failed")
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_route_project",
        annotations={
            "title": "AP-5 routing — pick the project a doc belongs to",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": True,
        },
    )
    async def workflow_route_project(params: RouteProjectInput) -> str:
        """Resolve a project_code from inbound signal. Returns confidence,
        tier, and a recommended action ('auto_file' | 'auto_file_flag' |
        'chat_picker' | 'triage').
        """
        try:
            timestamp = None
            if params.timestamp:
                try:
                    timestamp = _dt.datetime.fromisoformat(
                        params.timestamp.replace("Z", "+00:00")
                    )
                except ValueError:
                    pass
            result = project_router.route_project(
                sender_email=params.sender_email,
                subject=params.subject,
                body=params.body,
                timestamp=timestamp,
                explicit_code=params.explicit_code,
                use_llm=params.use_llm,
            )
            action = project_router.confidence_action(result)
            return json.dumps({
                "project_code": result.project_code,
                "confidence": round(result.confidence, 3),
                "tier": result.tier,
                "reason": result.reason,
                "action": action,
                "candidates": [
                    {"code": c.get("code"), "name": c.get("name")}
                    for c in (result.candidates or [])
                ],
            }, indent=2)
        except Exception as e:
            log.exception("route_project failed")
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_ap_sweep_cycle",
        annotations={
            "title": "AP-4 sweep — pull inbound, route, file or escalate",
            "readOnlyHint": False, "destructiveHint": False,
            "idempotentHint": False, "openWorldHint": True,
        },
    )
    async def workflow_ap_sweep_cycle(params: APSweepCycleInput) -> str:
        """Run one AP-4 sweep cycle.

        Pulls unread Gmail messages with the AP/Inbound label and new
        Receipts-space chat messages, routes each via AP-5, files
        receipts into the right Receipts/{YYYY-MM}/ bucket (auto_file
        or auto_file_flag), posts a chat picker for ambiguous cases,
        or dumps to Triage/ when no signal matches.

        Returns a summary report with per-action counts and per-item
        dispositions for review.
        """
        try:
            result = ap_sweep.run_sweep_cycle(
                email_label=params.email_label,
                chat_space_id=params.chat_space_id,
                triage_folder_id=params.triage_folder_id,
                max_items_per_source=params.max_items_per_source,
                dry_run=params.dry_run,
            )
            log.info("ap_sweep_cycle: %s", result.summary_line())
            return json.dumps({
                "status": "ok",
                "summary": result.summary_line(),
                "counts": result.counts,
                "started_at": result.started_at,
                "finished_at": result.finished_at,
                "items": [
                    {
                        "source": it.source,
                        "source_id": it.source_id,
                        "sender": it.sender,
                        "subject": it.subject,
                        "project_code": it.project_code,
                        "confidence": round(it.confidence, 3),
                        "tier": it.tier,
                        "action": it.action,
                        "target_folder_id": it.target_folder_id,
                        "note": it.note,
                    }
                    for it in result.items
                ],
            }, indent=2)
        except Exception as e:
            log.exception("ap_sweep_cycle failed")
            return json.dumps({"status": "error", "error": str(e)})

    @mcp.tool(
        name="workflow_project_registry_list",
        annotations={
            "title": "Inventory of registered projects (Wave 2 fields)",
            "readOnlyHint": True, "destructiveHint": False,
            "idempotentHint": True, "openWorldHint": False,
        },
    )
    async def workflow_project_registry_list(
        params: ProjectRegistryListInput,
    ) -> str:
        """Return all registered projects. Includes Wave 2 fields:
        Drive folder IDs, name aliases, assigned team, StaffWizard job
        link, billing config, customer email."""
        try:
            entries = project_registry.list_all(
                active_only=params.active_only
            )
            return json.dumps({
                "status": "ok",
                "count": len(entries),
                "projects": entries,
            }, indent=2, default=str)
        except Exception as e:
            return json.dumps({"status": "error", "error": str(e)})
