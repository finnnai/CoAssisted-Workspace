# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Wave 4 quote workflows — operator-facing, on top of pandadoc_client.

The 122 generated tools in tools/pandadoc_*.py wrap every raw API
endpoint. This module adds 5 higher-level workflows that compose
those primitives into the things an operator actually does:

  workflow_send_quote
      Template + recipients + tokens (variable values) → created
      document → sent for signature → returns the document ID,
      sharing URL, and recipient status.

  workflow_signature_status
      Single-call status check on a quote in flight. Returns the
      stage (draft / sent / viewed / completed / declined), per-
      recipient status, and the next action the operator should
      consider (resend, mark received, hand off to AR-9, etc).

  workflow_quote_pipeline
      Pipeline view across all PandaDoc documents in a date window.
      Groups by status (draft/sent/viewed/completed/declined),
      aggregates totals, flags stalled quotes (sent >7d ago, no
      view), and surfaces the top-3 oldest in each stage.

  workflow_quote_to_invoice
      Hands off a signed (status=completed) PandaDoc quote to the
      AR-9 invoicing pipeline. Pulls the quote total, customer,
      and project (from a token or hard-coded mapping), generates
      an ar_invoicing.InvoiceRecord using the configured Net-15
      default + writes it to the AR store. Optional: auto-send via
      ar_send.send_invoice. Joshua's question-3 answer means
      collections cadence is draft-by-default after this hand-off.

  workflow_resend_quote
      Chase a stalled signature. Sends a polite reminder email
      (uses brand_voice for tone if available), bumps the
      reminder counter, and logs the activity for the audit trail.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import pandadoc_client

_log = logging.getLogger(__name__)


# =============================================================================
# Helpers — internal, not exposed as MCP tools.
# =============================================================================

def _get_doc(document_id: str) -> dict:
    """Thin wrapper: GET /public/v1/documents/{id}/details."""
    return pandadoc_client.call(
        "detailsDocument",
        path_params={"id": document_id},
    )


def _send_doc(document_id: str, *, message: str = "", subject: str = "") -> dict:
    """Thin wrapper: POST /public/v1/documents/{id}/send."""
    body = {"silent": False}
    if message:
        body["message"] = message
    if subject:
        body["subject"] = subject
    return pandadoc_client.call(
        "sendDocument",
        path_params={"id": document_id},
        json_body=body,
    )


def _wait_for_draft(
    document_id: str,
    *,
    max_seconds: Optional[int] = None,
    interval_seconds: Optional[float] = None,
) -> dict:
    """Poll status_document until the doc reaches 'document.draft'.

    PandaDoc's createDocument is asynchronous: the response carries
    status='document.uploaded' while the template merge runs in the
    background. The doc transitions to 'document.draft' typically
    within 1-3 seconds. /send returns 409 until that transition lands.

    Returns the final status payload. Raises PandaDocPollTimeout if the
    transition never happens inside the window. Other terminal statuses
    (e.g. 'document.draft' is reached, or 'document.rejected') return
    immediately.

    Caps default to config.pandadoc.poll_max_seconds /
    poll_interval_seconds (60s / 1.0s) — same constants the
    pandadoc_client.call() 202-poll path uses.
    """
    import time
    import config as _cfg
    block = _cfg.get("pandadoc", {}) or {}
    max_s = max_seconds if max_seconds is not None else int(
        block.get("poll_max_seconds", 60)
    )
    interval = (
        interval_seconds
        if interval_seconds is not None
        else float(block.get("poll_interval_seconds", 1.0))
    )
    deadline = time.monotonic() + max_s
    last: dict = {}
    while True:
        last = pandadoc_client.call(
            "statusDocument",
            path_params={"id": document_id},
        )
        status = last.get("status") or ""
        # Anything non-uploaded is terminal for our purposes — usually
        # draft, but if PandaDoc surfaces an error state we want to
        # bail rather than spin.
        if status and status != "document.uploaded":
            return last
        if time.monotonic() > deadline:
            raise pandadoc_client.PandaDocPollTimeout(
                f"Document {document_id} stuck in {status!r} after {max_s}s"
            )
        time.sleep(interval)


_STALE_DAYS_DEFAULT = 7

_STAGE_NEXT_ACTION = {
    "document.draft": "Send the quote (workflow_send_quote or pandadoc_send_document).",
    "document.sent": "Wait or chase via workflow_resend_quote if stale (>7d).",
    "document.viewed": "Recipient opened it. Wait briefly or chase if stalled (>3d).",
    "document.waiting_approval": "Internal approval pending — nudge the approver.",
    "document.approved": "Approved internally. Send to recipients next.",
    "document.rejected": "Internal rejection. Investigate and re-issue.",
    "document.waiting_pay": "Awaiting payment per the quote terms.",
    "document.paid": "Paid. Hand off to AR-9 via workflow_quote_to_invoice.",
    "document.completed": "Signed — hand off to AR-9 via workflow_quote_to_invoice.",
    "document.expired": "Expired without signature. Re-issue or close out.",
    "document.declined": "Recipient declined. Follow up in person.",
}


def _next_action(status: str) -> str:
    return _STAGE_NEXT_ACTION.get(
        status, "Review status manually — unknown stage.",
    )


# =============================================================================
# Pydantic input models — MUST live at module scope.
#
# FastMCP's func_metadata calls typing.get_type_hints with
# globalns=func.__globals__. If these classes were nested inside register()
# (closure scope), get_type_hints can't resolve the deferred 'params: _Foo'
# annotations and InvalidSignature is raised on server startup.
# =============================================================================

class _SendQuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    template_uuid: str = Field(
        ..., description="PandaDoc template UUID to instantiate.",
    )
    document_name: str = Field(
        ..., description="Name for the new document (visible to recipient).",
    )
    recipients: list[dict[str, Any]] = Field(
        ...,
        description=(
            "List of recipient dicts. Each must have 'email'; may also "
            "carry 'first_name', 'last_name', 'role', 'signing_order'."
        ),
    )
    tokens: Optional[dict[str, Any]] = Field(
        None,
        description=(
            "Template token values, e.g. {'Client.Name': 'Acme Corp', "
            "'Quote.Total': '12,500.00'}. Keys match {{Token}} placeholders "
            "in the template."
        ),
    )
    pricing_tables: Optional[list[dict[str, Any]]] = Field(
        None,
        description="Optional pricing-table overrides — see PandaDoc docs.",
    )
    send_immediately: bool = Field(
        True,
        description="If True, send for signature right after creation.",
    )
    send_subject: Optional[str] = Field(
        None, description="Custom email subject when send_immediately=True.",
    )
    send_message: Optional[str] = Field(
        None, description="Custom email body when send_immediately=True.",
    )


class _SignatureStatusInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(..., description="PandaDoc document UUID.")


class _QuotePipelineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_from: Optional[str] = Field(
        None,
        description="ISO date (YYYY-MM-DD). Defaults to 90 days ago.",
    )
    date_to: Optional[str] = Field(
        None, description="ISO date (YYYY-MM-DD). Defaults to today.",
    )
    statuses: Optional[list[str]] = Field(
        None,
        description=(
            "Filter by PandaDoc status names (document.draft, document.sent, "
            "document.viewed, document.completed, document.declined, etc.). "
            "Default: all."
        ),
    )
    stale_days: int = Field(
        _STALE_DAYS_DEFAULT,
        description="Days a 'sent' doc must sit before flagged stalled.",
    )


class _QuoteToInvoiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(
        ...,
        description="PandaDoc document UUID — must be in 'document.completed' status.",
    )
    project_code: str = Field(
        ...,
        description=(
            "Surefox project code to bill against (must already be in "
            "the project_registry). The signed quote total flows into "
            "this project's AR-9 ledger."
        ),
    )
    send_invoice_immediately: bool = Field(
        False,
        description=(
            "If True, fires ar_send.send_invoice right after creating "
            "the AR record. Default False — operator usually wants to "
            "review before sending the invoice email."
        ),
    )


class _ResendQuoteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(
        ..., description="PandaDoc document UUID to chase.",
    )
    custom_message: Optional[str] = Field(
        None,
        description=(
            "Optional reminder body. If unset, uses a polite default."
        ),
    )


# =============================================================================
# MCP registration
# =============================================================================

def register(mcp) -> None:  # noqa: ANN001
    """Register the 5 Wave 4 quote workflows."""

    # -------------------------------------------------------------------------
    # workflow_send_quote
    # -------------------------------------------------------------------------

    @mcp.tool()
    def workflow_send_quote(params: _SendQuoteInput) -> dict[str, Any]:
        """Wave 4 quote-send workflow — template → document → sent for signature.

        Returns a dict with:
            document_id, document_status, sharing_link?, recipients,
            next_action.
        """
        body: dict[str, Any] = {
            "name": params.document_name,
            "template_uuid": params.template_uuid,
            "recipients": params.recipients,
        }
        if params.tokens:
            body["tokens"] = [
                {"name": k, "value": str(v)} for k, v in params.tokens.items()
            ]
        if params.pricing_tables:
            body["pricing_tables"] = params.pricing_tables

        created = pandadoc_client.call("createDocument", json_body=body)
        document_id = created.get("id")
        if not document_id:
            return {
                "error": "PandaDoc didn't return a document id.",
                "raw": created,
            }

        out: dict[str, Any] = {
            "document_id": document_id,
            "document_name": created.get("name"),
            "document_status": created.get("status"),
            "created_response": created,
        }

        if params.send_immediately:
            # createDocument is async — status starts at 'document.uploaded'
            # and transitions to 'document.draft' after the template
            # merge finishes. /send returns 409 until that transition.
            try:
                pre_send = _wait_for_draft(document_id)
                out["document_status"] = pre_send.get("status") or out["document_status"]
            except pandadoc_client.PandaDocPollTimeout as e:
                out["send_error"] = (
                    f"Timed out waiting for document.draft: {e}. "
                    "Document was created; send manually via "
                    "pandadoc_send_document once status reaches draft."
                )
                out["next_action"] = _next_action(out.get("document_status") or "")
                return out

            try:
                send_result = _send_doc(
                    document_id,
                    subject=params.send_subject or "",
                    message=params.send_message or "",
                )
                out["send_result"] = send_result
                out["document_status"] = send_result.get("status") or out["document_status"]
            except pandadoc_client.PandaDocAPIError as e:
                out["send_error"] = str(e)

        out["next_action"] = _next_action(out.get("document_status") or "")
        return out

    # -------------------------------------------------------------------------
    # workflow_signature_status
    # -------------------------------------------------------------------------

    @mcp.tool()
    def workflow_signature_status(params: _SignatureStatusInput) -> dict[str, Any]:
        """Status snapshot of a quote in flight + suggested next action.

        Returns: status, recipients (with per-person sign status),
        sent_date, completed_date, days_in_stage, next_action.
        """
        details = _get_doc(params.document_id)
        status = details.get("status") or ""
        sent = details.get("date_sent") or details.get("date_created")
        completed = details.get("date_completed")
        days_in_stage = None
        if sent:
            try:
                sent_dt = _dt.datetime.fromisoformat(sent.replace("Z", "+00:00"))
                ref = (
                    _dt.datetime.fromisoformat(completed.replace("Z", "+00:00"))
                    if completed
                    else _dt.datetime.now(_dt.timezone.utc)
                )
                days_in_stage = (ref - sent_dt).days
            except ValueError:
                pass

        return {
            "document_id": params.document_id,
            "status": status,
            "recipients": details.get("recipients") or [],
            "date_sent": sent,
            "date_completed": completed,
            "days_in_stage": days_in_stage,
            "is_stalled": (
                status == "document.sent"
                and days_in_stage is not None
                and days_in_stage > _STALE_DAYS_DEFAULT
            ),
            "next_action": _next_action(status),
        }

    # -------------------------------------------------------------------------
    # workflow_quote_pipeline
    # -------------------------------------------------------------------------

    @mcp.tool()
    def workflow_quote_pipeline(params: _QuotePipelineInput) -> dict[str, Any]:
        """Pipeline view of all quotes in a window, grouped by status.

        Returns: total_count, by_status (counts + total $ where available),
        stalled (list of doc summaries), top_oldest_per_stage.
        """
        date_from = params.date_from or (
            (_dt.date.today() - _dt.timedelta(days=90)).isoformat()
        )
        date_to = params.date_to or _dt.date.today().isoformat()

        query: dict[str, Any] = {
            "date_from": date_from,
            "date_to": date_to,
            "count": 100,
        }
        if params.statuses:
            # PandaDoc accepts a comma-separated `status` filter.
            query["status"] = ",".join(params.statuses)

        all_docs: list[dict] = []
        page = 1
        while True:
            query["page"] = page
            resp = pandadoc_client.call("listDocuments", query=query)
            results = resp.get("results") or []
            all_docs.extend(results)
            if not results or len(results) < query["count"]:
                break
            page += 1
            if page > 50:  # hard guard against runaway pagination
                break

        by_status: dict[str, dict[str, Any]] = {}
        stalled: list[dict] = []
        now = _dt.datetime.now(_dt.timezone.utc)
        cutoff = now - _dt.timedelta(days=params.stale_days)

        for d in all_docs:
            s = d.get("status") or "unknown"
            slot = by_status.setdefault(s, {"count": 0, "docs": []})
            slot["count"] += 1
            slot["docs"].append(d)
            # Stalled detection.
            if s == "document.sent":
                sent = d.get("date_modified") or d.get("date_sent")
                try:
                    sent_dt = _dt.datetime.fromisoformat(
                        (sent or "").replace("Z", "+00:00")
                    )
                    if sent_dt < cutoff:
                        stalled.append({
                            "id": d.get("id"),
                            "name": d.get("name"),
                            "date_sent": sent,
                            "days_old": (now - sent_dt).days,
                        })
                except ValueError:
                    pass

        # Top-3 oldest per stage (for at-a-glance "what's been sitting").
        top_oldest: dict[str, list[dict]] = {}
        for s, slot in by_status.items():
            sorted_docs = sorted(
                slot["docs"],
                key=lambda x: (x.get("date_modified") or x.get("date_created") or ""),
            )
            top_oldest[s] = [
                {"id": d.get("id"), "name": d.get("name"),
                 "date_modified": d.get("date_modified")}
                for d in sorted_docs[:3]
            ]

        return {
            "date_from": date_from,
            "date_to": date_to,
            "total_count": len(all_docs),
            "by_status": {
                s: {"count": slot["count"]} for s, slot in by_status.items()
            },
            "stalled": stalled,
            "top_oldest_per_stage": top_oldest,
        }

    # -------------------------------------------------------------------------
    # workflow_quote_to_invoice
    # -------------------------------------------------------------------------

    @mcp.tool()
    def workflow_quote_to_invoice(params: _QuoteToInvoiceInput) -> dict[str, Any]:
        """Hand off a signed PandaDoc quote to the AR-9 invoicing pipeline.

        Pulls the quote total + customer + period from PandaDoc, looks
        up project_code in project_registry for billing terms, generates
        an ar_invoicing.InvoiceRecord, and (optionally) sends it.

        Returns: invoice_id, invoice_number, total, customer_email,
        sent (bool).
        """
        # Heavy import is local so the workflow only imports AR modules
        # when actually used (keeps cold-start fast).
        import ar_invoicing
        import ar_send
        import project_registry

        details = _get_doc(params.document_id)
        if details.get("status") != "document.completed":
            return {
                "error": (
                    f"Document {params.document_id} is in status "
                    f"{details.get('status')!r}; expected 'document.completed'."
                ),
            }

        project = project_registry.get(params.project_code)
        if not project:
            return {
                "error": (
                    f"Project {params.project_code!r} not in registry. "
                    "Register it first via workflow_register_project."
                ),
            }

        total = float((details.get("grand_total") or {}).get("amount") or 0.0)
        if total <= 0:
            # Fall back to whatever the document carries as 'total'.
            total = float(details.get("total") or 0.0)

        # Customer info — first non-internal recipient is usually the buyer.
        recipients = details.get("recipients") or []
        primary = next(
            (r for r in recipients if r.get("recipient_type") != "Approver"),
            recipients[0] if recipients else {},
        )
        customer_email = primary.get("email") or project.get("customer_email")
        customer_name = (
            f"{primary.get('first_name', '')} {primary.get('last_name', '')}".strip()
            or project.get("client")
        )

        # Build a one-line invoice (the quote IS the line item).
        line = ar_invoicing.InvoiceLine(
            description=f"Per signed quote {details.get('name') or params.document_id}",
            quantity=1.0,
            rate=total,
            amount=total,
        )

        today = _dt.date.today()
        terms = project.get("billing_terms") or "Net-15"
        # Map terms string → days.
        days_map = {"Net-15": 15, "Net-30": 30, "Due-on-Receipt": 0, "Net-45": 45}
        due_offset = days_map.get(terms, 15)

        invoice = ar_invoicing.InvoiceRecord(
            invoice_id=f"PD-{params.document_id[:8]}-{today:%Y%m%d}",
            invoice_number=f"{params.project_code}-PD-{today:%Y-%m}",
            project_code=params.project_code,
            customer_name=customer_name or "Unknown",
            customer_email=customer_email,
            period_start=today,
            period_end=today,
            invoice_date=today,
            due_date=today + _dt.timedelta(days=due_offset),
            terms=terms,
            lines=[line],
            subtotal=total,
            total=total,
            status="draft",
            paid_amount=0.0,
        )
        ar_invoicing.persist(invoice)

        result: dict[str, Any] = {
            "invoice_id": invoice.invoice_id,
            "invoice_number": invoice.invoice_number,
            "total": total,
            "customer_email": customer_email,
            "due_date": invoice.due_date.isoformat(),
            "terms": terms,
            "sent": False,
        }
        if params.send_invoice_immediately and customer_email:
            send_result = ar_send.send_invoice(invoice.invoice_id)
            result["sent"] = bool(send_result.get("sent"))
            result["send_result"] = send_result
        return result

    # -------------------------------------------------------------------------
    # workflow_resend_quote
    # -------------------------------------------------------------------------

    @mcp.tool()
    def workflow_resend_quote(params: _ResendQuoteInput) -> dict[str, Any]:
        """Send a reminder for a stalled quote.

        Uses PandaDoc's createDocumentReminder endpoint, which fires a
        polite re-send email through PandaDoc's own delivery channel.
        """
        body: dict[str, Any] = {}
        if params.custom_message:
            body["message"] = params.custom_message

        try:
            result = pandadoc_client.call(
                "createManualReminder",
                path_params={"document_id": params.document_id},
                json_body=body or {"message": (
                    "Hi — just a quick nudge on the quote we sent over. "
                    "Let me know if you have any questions or need any "
                    "changes before signing."
                )},
            )
        except pandadoc_client.PandaDocAPIError as e:
            return {"sent": False, "error": str(e)}

        return {
            "sent": True,
            "document_id": params.document_id,
            "reminder_response": result,
            "next_action": (
                "Wait 3-5 business days. If still no movement, escalate "
                "by phone or unblock with a stakeholder ping."
            ),
        }
