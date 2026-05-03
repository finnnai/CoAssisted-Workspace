# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""AP/Wave-2 MCP tools — Option B handover, retry queue, baseline-deviation
engine, and Geotab client. Layered on top of v0.9.0's Wave-1 tools.

Tools registered:

    Option B operator handover (closes the v0.9.0 stub)
      - workflow_resolve_new_project_request
      - workflow_reject_new_project_request

    Retry queue (AP-4 partial — Pub/Sub watcher deferred)
      - workflow_retry_queue_enqueue
      - workflow_retry_queue_status
      - workflow_retry_queue_run_due
      - workflow_retry_queue_forget

    Baseline-deviation engine (AP-8)
      - workflow_compute_project_baseline
      - workflow_check_baseline_alerts
      - workflow_set_project_budget
      - workflow_project_baseline_status

    Geotab Drive API client
      - workflow_geotab_authenticate
      - workflow_geotab_list_devices
      - workflow_geotab_position_lookup
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import baseline as _baseline
import geotab_client as _geotab
import option_b_handover as _opb
import retry_queue as _rq


_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Pydantic input models
# --------------------------------------------------------------------------- #


class _ResolveNewProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(..., description="NEWPROJ-XXXXXX id from receipt_new_project_requests.json.")
    code: str = Field(..., min_length=2, max_length=20, description="Stable project code (uppercased).")
    name: str = Field(..., min_length=2, max_length=120, description="Human-readable project name.")
    billing_origin_state: Optional[str] = Field(
        None, max_length=4,
        description="2-letter state code. 'NY' flips AR-9 cadence to weekly.",
    )
    customer_email: Optional[str] = Field(None, description="Where AR-9 sends customer invoices.")
    billing_terms: str = Field("Net-15", description="Payment terms shown on customer invoices.")
    billing_cadence: Optional[str] = Field(
        None, description="'monthly' (default) or 'weekly'. Auto-derived from billing_origin_state if omitted.",
    )
    create_drive_tree: bool = Field(True, description="Build the Surefox AP/Projects/{name}/ subtree.")
    create_ap_sheet: bool = Field(True, description="Bootstrap the per-project AP sheet.")
    send_chat_ack: bool = Field(True, description="Chat-DM the original submitter when the project is set up.")


class _RejectNewProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    request_id: str = Field(..., description="NEWPROJ-XXXXXX id.")
    reason: str = Field(..., min_length=2, description="Reason shown to the submitter.")
    send_chat_ack: bool = Field(True, description="Chat-DM the submitter with the reason.")


class _RetryEnqueueInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    payload: dict = Field(..., description="The failed extraction payload (e.g. {message_id, attachment_id, error}).")
    kind: str = Field(..., description="'receipt' | 'invoice' | 'card_statement' | 'labor_report'.")
    error: Optional[str] = Field(None, description="Last error string for operator visibility.")
    note: Optional[str] = Field(None, description="Optional operator note.")


class _RetryStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Optional[str] = Field(
        None, description="'pending' | 'completed' | 'escalated'. Default: all.",
    )


class _RetryRunDueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_items: int = Field(10, ge=1, le=100, description="Cap items processed in this sweep.")


class _RetryForgetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    item_id: str = Field(..., description="RETRY-XXXXXXXX id.")


class _ComputeBaselineInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_code: str = Field(..., description="Project code to compute baseline for.")


class _CheckBaselineAlertsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_codes: Optional[list[str]] = Field(
        None, description="Restrict to specific projects. Default: all active.",
    )


class _SetProjectBudgetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_code: str = Field(..., description="Project code.")
    monthly_amount: float = Field(..., ge=0, description="Manual monthly budget in currency units.")
    currency: str = Field("USD")
    note: Optional[str] = Field(None, description="Optional operator note.")


class _ProjectBaselineStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_code: str = Field(..., description="Project code.")


class _GeotabAuthInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force_refresh: bool = Field(False, description="Force re-auth even if a session is cached.")


class _GeotabListDevicesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _GeotabPositionLookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    driver_email: str = Field(..., description="Driver's email; mapped to a device via config.geotab.driver_devices.")
    at_time: str = Field(..., description="ISO 8601 timestamp to look up the position near.")
    window_seconds: int = Field(300, ge=10, le=3600, description="Half-window in seconds for the position match.")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _err(e: Exception) -> dict[str, Any]:
    return {"status": "error", "error": f"{type(e).__name__}: {e}"}


def _build_send_chat():
    """Same Chat-DM glue as tools/ap_wave1.py — kept inline so the two
    tools modules can be loaded independently.
    """
    def _send(submitter_id: str, message: str) -> dict:
        try:
            import gservices
            chat = gservices.chat_service()
            if submitter_id.startswith("spaces/"):
                space_name = submitter_id
            elif submitter_id.startswith("users/"):
                resp = chat.spaces().findDirectMessage(name=submitter_id).execute()
                space_name = resp.get("name")
            else:
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


# --------------------------------------------------------------------------- #
# MCP registration
# --------------------------------------------------------------------------- #


def register(mcp) -> None:  # noqa: ANN001
    """Register all AP/Wave-2 tools with FastMCP."""

    # ------------------------------------------------------------------ #
    # Option B operator handover
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def workflow_resolve_new_project_request(
        params: _ResolveNewProjectInput,
    ) -> dict[str, Any]:
        """Operator approves an Option B new-project request. Creates the
        Drive subtree, bootstraps the AP sheet, registers the project,
        re-files any parked receipts that referenced this request, and
        DMs the original submitter.

        Returns:
            {status, code, name, drive_folder_id?, ap_sheet_id?,
             re_filed_receipts, registry_record}
        """
        try:
            send_chat = _build_send_chat() if params.send_chat_ack else None
            return _opb.resolve_request(
                params.request_id, params.code, params.name,
                billing_origin_state=params.billing_origin_state,
                customer_email=params.customer_email,
                billing_terms=params.billing_terms,
                billing_cadence=params.billing_cadence,
                create_drive_tree=params.create_drive_tree,
                create_ap_sheet=params.create_ap_sheet,
                send_chat=send_chat,
            )
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_reject_new_project_request(
        params: _RejectNewProjectInput,
    ) -> dict[str, Any]:
        """Operator declines an Option B new-project request. Marks the
        request rejected, DMs the submitter with the reason. The receipt
        stays parked under Triage/Pending-New-Projects/ for the operator
        to discard or manually re-file.
        """
        try:
            send_chat = _build_send_chat() if params.send_chat_ack else None
            return _opb.reject_request(
                params.request_id, params.reason, send_chat=send_chat,
            )
        except Exception as e:
            return _err(e)

    # ------------------------------------------------------------------ #
    # Retry queue (AP-4 partial)
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def workflow_retry_queue_enqueue(
        params: _RetryEnqueueInput,
    ) -> dict[str, Any]:
        """Add a failed extraction to the retry queue. Schedules the next
        attempt per the backoff ladder (1m → 5m → 30m → 4h → 24h →
        operator alert).
        """
        try:
            return _rq.enqueue(
                params.payload, params.kind,
                error=params.error, note=params.note,
            )
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_retry_queue_status(
        params: _RetryStatusInput,
    ) -> dict[str, Any]:
        """List retry queue items, optionally filtered by status.
        Includes a stats summary."""
        try:
            return {
                "status": "ok",
                "stats": _rq.stats(),
                "items": _rq.list_all(status=params.status),
            }
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_retry_queue_run_due(
        params: _RetryRunDueInput,
    ) -> dict[str, Any]:
        """List due items (next_attempt_at <= now). The actual retry
        execution is wired into the inbound capture loop (separate
        runner). This tool surfaces what's due and lets the operator
        spot stuck items.
        """
        try:
            due_items = _rq.due()
            capped = due_items[:params.max_items]
            return {
                "status": "ok",
                "due_count": len(due_items),
                "returned": len(capped),
                "items": capped,
            }
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_retry_queue_forget(
        params: _RetryForgetInput,
    ) -> dict[str, Any]:
        """Permanently drop an item from the retry queue. Used when the
        operator manually handles an escalated payload.
        """
        try:
            return {
                "status": "ok",
                "removed": _rq.forget(params.item_id),
                "item_id": params.item_id,
            }
        except Exception as e:
            return _err(e)

    # ------------------------------------------------------------------ #
    # Baseline-deviation engine (AP-8)
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def workflow_compute_project_baseline(
        params: _ComputeBaselineInput,
    ) -> dict[str, Any]:
        """Compute / refresh the 30-day baseline (daily mean+std, weekly
        mean+std) for one project. Persists to project_baselines.json.
        Returns ready=True only when at least 30 days of spend observed.
        """
        try:
            return _baseline.compute_baseline_for_project(params.project_code)
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_check_baseline_alerts(
        params: _CheckBaselineAlertsInput,
    ) -> dict[str, Any]:
        """Walk every active project (or the supplied list) and return
        the set of alerts: daily/weekly deviation, budget burn/exceeded,
        budget mismatch, cold-start informational.
        """
        try:
            alerts = _baseline.check_alerts(project_codes=params.project_codes)
            return {
                "status": "ok",
                "count": len(alerts),
                "alerts": alerts,
            }
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_set_project_budget(
        params: _SetProjectBudgetInput,
    ) -> dict[str, Any]:
        """Register a manual monthly budget for a project. Triggers
        budget_burn / budget_exceeded alerts in addition to (not instead
        of) baseline deviation alerts.
        """
        try:
            return _baseline.set_project_budget(
                params.project_code, params.monthly_amount,
                currency=params.currency, note=params.note,
            )
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_project_baseline_status(
        params: _ProjectBaselineStatusInput,
    ) -> dict[str, Any]:
        """Combined baseline + budget + alerts for one project. Used by
        the project dashboard tile and by AP review to spot-check a
        project before pulling its EIB.
        """
        try:
            return _baseline.project_baseline_status(params.project_code)
        except Exception as e:
            return _err(e)

    # ------------------------------------------------------------------ #
    # Geotab Drive API client
    # ------------------------------------------------------------------ #
    @mcp.tool()
    def workflow_geotab_authenticate(
        params: _GeotabAuthInput,
    ) -> dict[str, Any]:
        """Authenticate against Geotab using the credentials in
        config.geotab. Caches the sessionId for the process lifetime;
        force_refresh=True re-auths.
        """
        try:
            return _geotab.authenticate(force_refresh=params.force_refresh)
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_geotab_list_devices(
        params: _GeotabListDevicesInput,
    ) -> dict[str, Any]:
        """List every Geotab device (vehicle) the configured user can see.
        Returns canonical {device_id, name, license_plate, vin,
        serial_number} dicts. Returns an empty list (no error) when
        Geotab credentials are not configured.
        """
        try:
            devices = _geotab.list_devices()
            return {
                "status": "ok",
                "configured": _geotab.is_configured(),
                "count": len(devices),
                "devices": devices,
            }
        except Exception as e:
            return _err(e)

    @mcp.tool()
    def workflow_geotab_position_lookup(
        params: _GeotabPositionLookupInput,
    ) -> dict[str, Any]:
        """Resolve a driver email + timestamp → vehicle position via
        Geotab. Used by AP-5 routing as the tiebreaker between candidate
        projects when sender / subject classification is ambiguous.

        Returns {status: 'no_credentials'} cleanly when Geotab isn't
        configured — the caller falls through to the next tiebreaker.
        Returns {status: 'no_match'} when the driver is configured but
        no position ping falls inside the time window.
        """
        try:
            if not _geotab.is_configured():
                return {"status": "no_credentials"}
            try:
                at_time = _dt.datetime.fromisoformat(params.at_time.replace("Z", "+00:00"))
            except ValueError as e:
                return {"status": "error", "error": f"bad at_time: {e}"}
            pos = _geotab.lookup_position_by_driver(
                params.driver_email, at_time=at_time,
                window_seconds=params.window_seconds,
            )
            if not pos:
                return {
                    "status": "no_match",
                    "driver_email": params.driver_email,
                    "at_time": params.at_time,
                    "window_seconds": params.window_seconds,
                }
            return {"status": "ok", "position": pos}
        except Exception as e:
            return _err(e)
