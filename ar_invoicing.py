# © 2026 CoAssisted Workspace. Licensed under MIT.
"""AR-9: Customer invoicing + aging + collections cadence.

Generates customer invoices from per-project billable hours captured by
AP-7, tracks aging across the standard buckets (current, 1-15, 16-30,
31-60, 61-90, 90+), and surfaces collections candidates for the existing
vendor-follow-up loop to chase down on the AR side.

Per the operator's roadmap:
    - Default: monthly per-project invoice, Net-15 standard.
    - Per-customer overrides on terms (Net-15 / Net-30 / Net-45 / etc.)
    - Weekly cadence option triggered by `billing_origin_state == "NY"`
      on the project record (per the New York project rule).

Storage:
    ~/Developer/google_workspace_mcp/ar_invoices.json
    Map: invoice_id → InvoiceRecord dict (status, terms, line items,
    sent/due/paid dates, collections history).

This module ships the deterministic logic. Email send / payment
ingestion / collection-reminder posting integrations live in
tools/ar_invoicing.py (next commit) which wraps these calls in MCP
tool surfaces and connects to the existing tools/gmail + Drive
plumbing.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import labor_ingest
import project_registry


# =============================================================================
# Storage
# =============================================================================

_PROJECT_ROOT = Path(__file__).resolve().parent
_INVOICES_PATH = _PROJECT_ROOT / "ar_invoices.json"


def _load() -> dict[str, dict]:
    if not _INVOICES_PATH.exists():
        return {}
    try:
        with _INVOICES_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, dict]) -> None:
    _INVOICES_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix="ar_invoices.", suffix=".json.tmp",
        dir=str(_INVOICES_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True, default=str)
        os.replace(tmp, _INVOICES_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# =============================================================================
# Constants
# =============================================================================

# Aging bucket boundaries — days past due.
AGING_BUCKETS = [
    ("current", 0, 0),       # Not yet due
    ("1-15", 1, 15),
    ("16-30", 16, 30),
    ("31-60", 31, 60),
    ("61-90", 61, 90),
    ("90+", 91, 10_000),
]


# Collections cadence — when do we send the next reminder, and how
# strongly worded? Tuples of (days_past_due, reminder_type).
COLLECTIONS_CADENCE = [
    (5, "courtesy_reminder"),       # "Just a heads-up..."
    (15, "first_followup"),         # "Past due, please process"
    (30, "second_followup"),        # "30+ past due, escalating"
    (45, "third_followup"),         # "45+ past due, internal escalation"
    (60, "escalation_to_legal"),    # Hand off to legal/AR manager
]


# Standard terms — used as fallback when project's billing_terms isn't set.
DEFAULT_TERMS = "Net-15"


def _terms_to_days(terms: str) -> int:
    """Parse 'Net-15' / 'Net-30' / 'Due on Receipt' to days-until-due."""
    if not terms:
        return 15
    t = terms.lower().strip()
    if "receipt" in t:
        return 0
    if t.startswith("net-") or t.startswith("net "):
        try:
            return int(t.replace("net-", "").replace("net ", "").strip())
        except ValueError:
            return 15
    return 15


# =============================================================================
# Data model
# =============================================================================

@dataclass
class InvoiceLine:
    """One line on a customer invoice — typically one shift class or markup."""
    description: str
    quantity: float          # hours
    rate: float              # billable rate $/hr
    amount: float            # quantity * rate (precomputed for clarity)


@dataclass
class InvoiceRecord:
    """In-memory representation of one customer invoice."""

    invoice_id: str
    invoice_number: str       # human-friendly e.g. "GE1-2026-04"
    project_code: str
    customer_name: str
    customer_email: Optional[str]
    period_start: _dt.date
    period_end: _dt.date
    invoice_date: _dt.date
    due_date: _dt.date
    terms: str
    cadence: str              # "monthly" | "weekly"
    lines: list[InvoiceLine] = field(default_factory=list)
    subtotal: float = 0.0
    total: float = 0.0
    status: str = "draft"     # draft | sent | partial | paid | overdue | written_off
    sent_date: Optional[_dt.date] = None
    paid_date: Optional[_dt.date] = None
    paid_amount: float = 0.0
    collection_events: list[dict] = field(default_factory=list)
    notes: str = ""


# =============================================================================
# Invoice generation
# =============================================================================

def generate_invoice_from_labor(
    project_code: str,
    *,
    period_start: _dt.date,
    period_end: _dt.date,
    labor_rows: list[labor_ingest.LaborRow],
    invoice_date: Optional[_dt.date] = None,
    markup_pct: float = 0.0,
) -> InvoiceRecord:
    """Build an InvoiceRecord from a project's labor for a billing period.

    Groups labor rows by `post_description` so the customer sees
    per-post line items rather than per-shift sprawl. Each post becomes
    one line item with summed billable hours × representative rate.

    Args:
        project_code: registered project code.
        period_start, period_end: billing period bounds (inclusive).
        labor_rows: filtered to the period's billable shifts (caller's
            responsibility — typical caller pulls from per-project
            Labor/ folders or master_rollup history).
        invoice_date: defaults to today.
        markup_pct: optional client markup beyond the cost rate (the
            BillableDollars field already carries the customer-facing
            rate, so this is normally 0).
    """
    record = project_registry.get(project_code)
    if not record:
        raise ValueError(f"Unknown project code {project_code!r}")

    invoice_date = invoice_date or _dt.date.today()
    terms = record.get("billing_terms") or DEFAULT_TERMS
    cadence = record.get("billing_cadence") or "monthly"
    due_date = invoice_date + _dt.timedelta(days=_terms_to_days(terms))
    customer_name = record.get("client") or project_code
    customer_email = record.get("customer_email")

    # Group labor rows by post_description.
    by_post: dict[str, dict[str, float]] = {}
    for row in labor_rows:
        if not row.work_date or row.work_date < period_start or row.work_date > period_end:
            continue
        key = row.post_description or "(unspecified post)"
        bucket = by_post.setdefault(key, {"hours": 0.0, "amount": 0.0})
        bucket["hours"] += row.billable_hours
        bucket["amount"] += row.billable_dollars

    # Convert to invoice lines.
    lines: list[InvoiceLine] = []
    subtotal = 0.0
    for post_desc, agg in sorted(by_post.items()):
        hours = round(agg["hours"], 2)
        amount = round(agg["amount"], 2)
        if hours <= 0 or amount <= 0:
            continue
        rate = round(amount / hours, 2) if hours else 0.0
        if markup_pct:
            amount = round(amount * (1 + markup_pct / 100), 2)
            rate = round(rate * (1 + markup_pct / 100), 2)
        lines.append(InvoiceLine(
            description=post_desc, quantity=hours, rate=rate, amount=amount,
        ))
        subtotal += amount

    invoice_id = str(uuid.uuid4())
    invoice_number = (
        f"{project_code}-{period_start:%Y-%m}"
        if cadence == "monthly"
        else f"{project_code}-{period_start:%Y-%m-%d}"
    )

    return InvoiceRecord(
        invoice_id=invoice_id,
        invoice_number=invoice_number,
        project_code=project_code,
        customer_name=customer_name,
        customer_email=customer_email,
        period_start=period_start,
        period_end=period_end,
        invoice_date=invoice_date,
        due_date=due_date,
        terms=terms,
        cadence=cadence,
        lines=lines,
        subtotal=round(subtotal, 2),
        total=round(subtotal, 2),  # No tax handling at this layer.
        status="draft",
    )


def persist(record: InvoiceRecord) -> None:
    """Save an InvoiceRecord to the JSON store."""
    data = _load()
    data[record.invoice_id] = _serialize(record)
    _save(data)


def _serialize(record: InvoiceRecord) -> dict:
    return {
        "invoice_id": record.invoice_id,
        "invoice_number": record.invoice_number,
        "project_code": record.project_code,
        "customer_name": record.customer_name,
        "customer_email": record.customer_email,
        "period_start": record.period_start.isoformat(),
        "period_end": record.period_end.isoformat(),
        "invoice_date": record.invoice_date.isoformat(),
        "due_date": record.due_date.isoformat(),
        "terms": record.terms,
        "cadence": record.cadence,
        "lines": [
            {
                "description": ln.description,
                "quantity": ln.quantity,
                "rate": ln.rate,
                "amount": ln.amount,
            }
            for ln in record.lines
        ],
        "subtotal": record.subtotal,
        "total": record.total,
        "status": record.status,
        "sent_date": record.sent_date.isoformat() if record.sent_date else None,
        "paid_date": record.paid_date.isoformat() if record.paid_date else None,
        "paid_amount": record.paid_amount,
        "collection_events": record.collection_events,
        "notes": record.notes,
    }


def _deserialize(data: dict) -> InvoiceRecord:
    return InvoiceRecord(
        invoice_id=data["invoice_id"],
        invoice_number=data["invoice_number"],
        project_code=data["project_code"],
        customer_name=data["customer_name"],
        customer_email=data.get("customer_email"),
        period_start=_dt.date.fromisoformat(data["period_start"]),
        period_end=_dt.date.fromisoformat(data["period_end"]),
        invoice_date=_dt.date.fromisoformat(data["invoice_date"]),
        due_date=_dt.date.fromisoformat(data["due_date"]),
        terms=data["terms"],
        cadence=data["cadence"],
        lines=[
            InvoiceLine(**ln) for ln in (data.get("lines") or [])
        ],
        subtotal=float(data.get("subtotal", 0)),
        total=float(data.get("total", 0)),
        status=data.get("status", "draft"),
        sent_date=(
            _dt.date.fromisoformat(data["sent_date"]) if data.get("sent_date") else None
        ),
        paid_date=(
            _dt.date.fromisoformat(data["paid_date"]) if data.get("paid_date") else None
        ),
        paid_amount=float(data.get("paid_amount", 0)),
        collection_events=list(data.get("collection_events") or []),
        notes=data.get("notes", ""),
    )


def get(invoice_id: str) -> Optional[InvoiceRecord]:
    data = _load().get(invoice_id)
    return _deserialize(data) if data else None


def list_all(*, status: Optional[str] = None) -> list[InvoiceRecord]:
    out: list[InvoiceRecord] = []
    for data in _load().values():
        record = _deserialize(data)
        if status and record.status != status:
            continue
        out.append(record)
    out.sort(key=lambda r: r.invoice_date, reverse=True)
    return out


# =============================================================================
# Status transitions
# =============================================================================

def mark_sent(invoice_id: str, *, sent_date: Optional[_dt.date] = None) -> bool:
    data = _load()
    if invoice_id not in data:
        return False
    data[invoice_id]["status"] = "sent"
    data[invoice_id]["sent_date"] = (sent_date or _dt.date.today()).isoformat()
    _save(data)
    return True


def apply_payment(
    invoice_id: str,
    amount: float,
    *,
    paid_date: Optional[_dt.date] = None,
) -> bool:
    """Apply a customer payment. Tracks partials → paid when total reached."""
    data = _load()
    if invoice_id not in data:
        return False
    rec = data[invoice_id]
    new_paid = float(rec.get("paid_amount", 0)) + amount
    rec["paid_amount"] = round(new_paid, 2)
    if new_paid >= float(rec.get("total", 0)) - 0.01:
        rec["status"] = "paid"
        rec["paid_date"] = (paid_date or _dt.date.today()).isoformat()
    else:
        rec["status"] = "partial"
    _save(data)
    return True


def add_collection_event(
    invoice_id: str,
    event_type: str,
    *,
    note: str = "",
) -> bool:
    """Append a collections event to the invoice's audit trail."""
    data = _load()
    if invoice_id not in data:
        return False
    events = list(data[invoice_id].get("collection_events") or [])
    events.append({
        "iso": _now_iso(),
        "type": event_type,
        "note": note,
    })
    data[invoice_id]["collection_events"] = events
    _save(data)
    return True


# =============================================================================
# Aging
# =============================================================================

@dataclass
class AgingEntry:
    invoice: InvoiceRecord
    days_past_due: int
    bucket: str
    outstanding: float

    @property
    def is_overdue(self) -> bool:
        return self.days_past_due > 0


def compute_aging(
    *,
    as_of: Optional[_dt.date] = None,
    project_code: Optional[str] = None,
    customer_name: Optional[str] = None,
) -> list[AgingEntry]:
    """Compute aging for all open invoices as of `as_of` (default today).

    Returns one AgingEntry per open invoice, sorted by days_past_due
    descending (most overdue first).
    """
    as_of = as_of or _dt.date.today()
    out: list[AgingEntry] = []
    for record in list_all():
        if record.status in ("paid", "written_off"):
            continue
        if project_code and record.project_code != project_code:
            continue
        if customer_name and record.customer_name != customer_name:
            continue
        days_past = (as_of - record.due_date).days
        bucket = _bucket_for_days(days_past)
        outstanding = round(record.total - record.paid_amount, 2)
        if outstanding <= 0:
            continue
        out.append(AgingEntry(
            invoice=record,
            days_past_due=days_past,
            bucket=bucket,
            outstanding=outstanding,
        ))
    out.sort(key=lambda e: e.days_past_due, reverse=True)
    return out


def _bucket_for_days(days_past_due: int) -> str:
    """Map days_past_due to a bucket label."""
    if days_past_due <= 0:
        return "current"
    for label, lo, hi in AGING_BUCKETS:
        if label == "current":
            continue
        if lo <= days_past_due <= hi:
            return label
    return "90+"


def aging_summary(
    *,
    as_of: Optional[_dt.date] = None,
) -> dict[str, float]:
    """Total outstanding $ per aging bucket. Returns {bucket: amount}."""
    summary = {label: 0.0 for label, _lo, _hi in AGING_BUCKETS}
    for entry in compute_aging(as_of=as_of):
        summary[entry.bucket] = round(
            summary[entry.bucket] + entry.outstanding, 2
        )
    return summary


# =============================================================================
# Collections — what reminders are due to send today?
# =============================================================================

@dataclass
class CollectionsCandidate:
    invoice: InvoiceRecord
    days_past_due: int
    reminder_type: str
    last_event_iso: Optional[str]


def collections_due_today(
    *,
    as_of: Optional[_dt.date] = None,
) -> list[CollectionsCandidate]:
    """Return invoices that should get a collections reminder today.

    Logic: each invoice climbs the COLLECTIONS_CADENCE ladder once. If
    the invoice is N days past due and we haven't already sent the
    reminder for that tier, it's a candidate.

    Returns list ordered by days_past_due descending.
    """
    as_of = as_of or _dt.date.today()
    out: list[CollectionsCandidate] = []

    for record in list_all():
        if record.status in ("paid", "written_off", "draft"):
            continue
        days_past = (as_of - record.due_date).days
        if days_past < 0:
            continue

        # What tier do we belong in?
        next_tier: Optional[str] = None
        for threshold_days, tier_name in COLLECTIONS_CADENCE:
            if days_past >= threshold_days:
                next_tier = tier_name

        if not next_tier:
            continue

        # Have we already sent this tier's reminder?
        already_sent = any(
            ev.get("type") == next_tier
            for ev in (record.collection_events or [])
        )
        if already_sent:
            continue

        last_event = (record.collection_events or [None])[-1]
        out.append(CollectionsCandidate(
            invoice=record,
            days_past_due=days_past,
            reminder_type=next_tier,
            last_event_iso=last_event.get("iso") if last_event else None,
        ))
    out.sort(key=lambda c: c.days_past_due, reverse=True)
    return out
