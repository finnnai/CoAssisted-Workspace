# © 2026 CoAssisted Workspace. Licensed under MIT.
"""MCP wrappers for P4 workflows (CRM event sink + 3 workflows)."""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

import crm_events
import p4_workflows as p4
from errors import format_error
from logging_util import log


# --------------------------------------------------------------------------- #
# Generic CRM event tools
# --------------------------------------------------------------------------- #


class AppendEventInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    email: str = Field(...)
    kind: str = Field(...)
    summary: str = Field(default="")
    ts: Optional[str] = Field(default=None)
    thread_id: Optional[str] = Field(default=None)
    event_id: Optional[str] = Field(default=None)
    data: Optional[dict] = Field(default=None)


class TimelineInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    email: str = Field(...)
    limit: Optional[int] = Field(default=20, ge=1, le=200)


# --------------------------------------------------------------------------- #
# #3 VIP escalation
# --------------------------------------------------------------------------- #


class VipEscalationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    new_messages: list[dict] = Field(
        ...,
        description=("List of {from_email, subject, snippet, thread_id, link}. "
                     "Caller fetches the new inbound batch."),
    )
    vip_emails: list[str] = Field(...)


# --------------------------------------------------------------------------- #
# #27 Calibrator
# --------------------------------------------------------------------------- #


class RecordMessageInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    email: str = Field(...)
    body: str = Field(...)
    direction: str = Field(default="received", description="'received' | 'sent'.")
    ts: Optional[str] = Field(default=None)
    thread_id: Optional[str] = Field(default=None)


class CalibratedStalenessInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    email: str = Field(...)


# --------------------------------------------------------------------------- #
# #41 Vendor onboarding
# --------------------------------------------------------------------------- #


class VendorOnboardingInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    vendor_email: str = Field(...)
    vendor_name: str = Field(...)
    invoice_id: Optional[str] = Field(default=None)
    items: Optional[list[str]] = Field(
        default=None,
        description="Override the default checklist (W-9, COI, NDA, banking, MSA).",
    )
    base_due_days: int = Field(default=7, ge=1, le=30)
    record_kickoff: bool = Field(
        default=True,
        description="Record a vendor_onboarded CRM event after building the plan.",
    )


# --------------------------------------------------------------------------- #
# Tool registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:
    @mcp.tool(
        name="workflow_crm_event_append",
        annotations={"title": "Append a CRM event to a contact's timeline",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    async def workflow_crm_event_append(params: AppendEventInput) -> str:
        try:
            rec = crm_events.append(
                params.email, params.kind, params.summary,
                ts=params.ts, thread_id=params.thread_id,
                event_id=params.event_id, data=params.data,
            )
            return json.dumps(rec, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_crm_event_append", e)

    @mcp.tool(
        name="workflow_crm_timeline",
        annotations={"title": "Read a contact's CRM event timeline",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_crm_timeline(params: TimelineInput) -> str:
        try:
            events = crm_events.get_recent(params.email, limit=params.limit or 20)
            return json.dumps({
                "email": params.email,
                "count": len(events),
                "events": events,
            }, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_crm_timeline", e)

    @mcp.tool(
        name="workflow_vip_escalations",
        annotations={"title": "Find VIP messages needing alert",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    async def workflow_vip_escalations(params: VipEscalationInput) -> str:
        """Filter new messages to VIP-sender ones, deduped against recent alerts.
        Records a vip_alert CRM event for each fire."""
        try:
            alerts = p4.find_vip_escalations(
                params.new_messages, set(params.vip_emails),
            )
            log.info("vip_escalations: %d new alert(s)", len(alerts))
            return json.dumps({"count": len(alerts), "alerts": alerts},
                              indent=2, default=str)
        except Exception as e:
            return format_error("workflow_vip_escalations", e)

    @mcp.tool(
        name="workflow_record_message_event",
        annotations={"title": "Record an email message in CRM with substantive-flag",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False},
    )
    async def workflow_record_message_event(params: RecordMessageInput) -> str:
        """Record an email event with substantive-flag + word count.

        Used by the calibrator (#27) to track real conversations vs short acks.
        """
        try:
            rec = p4.record_message_event(
                params.email, params.body, params.direction,
                ts=params.ts, thread_id=params.thread_id,
            )
            return json.dumps(rec, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_record_message_event", e)

    @mcp.tool(
        name="workflow_calibrated_staleness",
        annotations={"title": "Get calibrated staleness for a contact",
                     "readOnlyHint": True, "destructiveHint": False,
                     "idempotentHint": True, "openWorldHint": False},
    )
    async def workflow_calibrated_staleness(params: CalibratedStalenessInput) -> str:
        try:
            return json.dumps(p4.calibrated_staleness(params.email),
                              indent=2, default=str)
        except Exception as e:
            return format_error("workflow_calibrated_staleness", e)

    @mcp.tool(
        name="workflow_vendor_onboarding",
        annotations={"title": "Build a vendor onboarding plan",
                     "readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": True},
    )
    async def workflow_vendor_onboarding(params: VendorOnboardingInput) -> str:
        """Build an onboarding plan + checklist for a (new) vendor.

        Detects whether vendor is new from CRM event history. Returns the plan
        with due-dated checklist items. If record_kickoff=True, appends a
        vendor_onboarded CRM event.
        """
        try:
            new = p4.is_new_vendor(params.vendor_email)
            plan = p4.build_onboarding_plan(
                params.vendor_email, params.vendor_name,
                invoice_id=params.invoice_id,
                items=params.items,
                base_due_days=params.base_due_days,
            )
            if params.record_kickoff:
                p4.record_onboarding_kicked_off(plan)
            return json.dumps({
                "is_new_vendor": new,
                "plan": plan.to_dict(),
            }, indent=2, default=str)
        except Exception as e:
            return format_error("workflow_vendor_onboarding", e)
