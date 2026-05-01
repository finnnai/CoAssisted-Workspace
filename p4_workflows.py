# © 2026 CoAssisted Workspace. Licensed under MIT.
"""P4 workflows — VIP escalation, exchange calibrator, vendor onboarding."""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from typing import Iterable, Optional

import crm_events


# --------------------------------------------------------------------------- #
# #3 VIP escalation
# --------------------------------------------------------------------------- #


# Already-fired VIP events to avoid duplicate alerts within window.
VIP_DEDUP_WINDOW_HOURS = 4


def is_vip_alert_recent(email: str,
                       window_hours: int = VIP_DEDUP_WINDOW_HOURS,
                       now: _dt.datetime | None = None) -> bool:
    """Did we already fire a VIP alert for this contact in the dedup window?"""
    last = crm_events.last_event(email, kind="vip_alert")
    if not last:
        return False
    now = now or _dt.datetime.now().astimezone()
    try:
        ts = _dt.datetime.fromisoformat((last.get("ts") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    age_hours = (now - ts).total_seconds() / 3600.0
    return age_hours < window_hours


def find_vip_escalations(
    new_messages: Iterable[dict],
    vip_emails: set[str],
    now: _dt.datetime | None = None,
) -> list[dict]:
    """Identify which inbound messages from VIPs should trigger an alert.

    Each new_message dict needs: from_email, subject, snippet, thread_id, link.
    Records a vip_alert event in crm_events for each fire (used for dedup).
    """
    now = now or _dt.datetime.now().astimezone()
    alerts = []
    for m in new_messages:
        sender = (m.get("from_email") or "").lower()
        if sender not in {e.lower() for e in vip_emails}:
            continue
        if is_vip_alert_recent(sender, now=now):
            continue
        alert = {
            "kind": "vip_alert",
            "email": sender,
            "subject": m.get("subject", "(no subject)"),
            "snippet": (m.get("snippet") or "")[:100],
            "thread_id": m.get("thread_id"),
            "link": m.get("link"),
        }
        alerts.append(alert)
        crm_events.append(
            sender, "vip_alert",
            f"VIP email: {alert['subject'][:60]}",
            thread_id=alert["thread_id"],
            data={"snippet": alert["snippet"]},
        )
    return alerts


# --------------------------------------------------------------------------- #
# #27 Last meaningful exchange calibrator
# --------------------------------------------------------------------------- #


# Treat replies under this many words as "thin" (acks/thanks/etc).
THIN_REPLY_WORD_LIMIT = 5


def is_substantive_message(body: str) -> bool:
    """True if the message body looks like real conversation, not an ack."""
    if not body:
        return False
    stripped = body.strip()
    word_count = len(stripped.split())
    if word_count <= THIN_REPLY_WORD_LIMIT:
        # Short-but-substantive: questions count as real.
        if "?" in stripped:
            return True
        return False
    return True


def record_message_event(
    email: str,
    body: str,
    direction: str = "received",
    *,
    ts: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> dict:
    """Record an email_* event with substantive flag + word count.

    Use this from the message-sync layer; the calibrator workflow reads
    these events to decide if the contact has had a real recent exchange.
    """
    substantive = is_substantive_message(body)
    kind = f"email_{direction}"
    if substantive:
        kind = "email_substantive"
    return crm_events.append(
        email, kind,
        summary=(body or "")[:80],
        ts=ts,
        thread_id=thread_id,
        data={"word_count": len((body or "").split()),
              "substantive": substantive,
              "direction": direction},
    )


def calibrated_staleness(
    email: str,
    *,
    today: _dt.datetime | None = None,
) -> dict:
    """Return staleness based on substantive exchanges only.

    Returns {
        days_since_substantive: int | None,
        days_since_any: int | None,
        substantive_count_60d: int,
        any_count_60d: int,
    }
    """
    days_substantive = crm_events.days_since_last_event(email, kind="email_substantive", today=today)
    # Last "any" considers both kinds
    last_any_received = crm_events.days_since_last_event(email, kind="email_received", today=today)
    last_any_sent = crm_events.days_since_last_event(email, kind="email_sent", today=today)
    last_any_substantive = days_substantive
    candidates = [d for d in (last_any_received, last_any_sent, last_any_substantive) if d is not None]
    days_any = min(candidates) if candidates else None
    return {
        "email": email,
        "days_since_substantive": days_substantive,
        "days_since_any": days_any,
        "substantive_count_60d": crm_events.count_events(
            email, kind="email_substantive", since_days=60, today=today,
        ),
        "any_count_60d": (
            crm_events.count_events(email, kind="email_received", since_days=60, today=today)
            + crm_events.count_events(email, kind="email_sent", since_days=60, today=today)
            + crm_events.count_events(email, kind="email_substantive", since_days=60, today=today)
        ),
    }


# --------------------------------------------------------------------------- #
# #41 Vendor onboarding flow
# --------------------------------------------------------------------------- #


# Default onboarding checklist items per new vendor.
DEFAULT_VENDOR_CHECKLIST = (
    "Request signed W-9 / W-8BEN",
    "Collect Certificate of Insurance (COI)",
    "Confirm NDA executed",
    "Capture banking details for ACH (or remit-to address for check)",
    "File master agreement (MSA) if available",
)


@dataclass
class VendorOnboardingPlan:
    """One vendor onboarding plan ready to fan out as Tasks + CRM events."""
    vendor_email: str
    vendor_name: str
    invoice_id: Optional[str]
    checklist: list[dict]            # each: {title, due_at, slug}

    def to_dict(self) -> dict:
        return {
            "vendor_email": self.vendor_email,
            "vendor_name": self.vendor_name,
            "invoice_id": self.invoice_id,
            "checklist": list(self.checklist),
        }


def is_new_vendor(email: str) -> bool:
    """A vendor is new if we have no prior vendor_invoice events for them."""
    last = crm_events.last_event(email, kind="vendor_invoice")
    return last is None


def build_onboarding_plan(
    vendor_email: str,
    vendor_name: str,
    *,
    invoice_id: Optional[str] = None,
    items: Optional[list[str]] = None,
    base_due_days: int = 7,
    today: _dt.date | None = None,
) -> VendorOnboardingPlan:
    """Build an onboarding plan with one Task per checklist item."""
    today = today or _dt.date.today()
    items = items or list(DEFAULT_VENDOR_CHECKLIST)
    checklist = []
    for i, title in enumerate(items, 1):
        due = today + _dt.timedelta(days=base_due_days * i)
        slug = re.sub(r"\W+", "_", title.lower()).strip("_")[:40]
        checklist.append({
            "title": title,
            "due_at": due.isoformat(),
            "slug": slug,
        })
    return VendorOnboardingPlan(
        vendor_email=vendor_email,
        vendor_name=vendor_name,
        invoice_id=invoice_id,
        checklist=checklist,
    )


def record_onboarding_kicked_off(plan: VendorOnboardingPlan) -> dict:
    """Append a vendor_onboarded event to the vendor's CRM timeline."""
    return crm_events.append(
        plan.vendor_email,
        "vendor_onboarded",
        summary=f"Onboarding kicked off for {plan.vendor_name}",
        data={"checklist_items": len(plan.checklist),
              "invoice_id": plan.invoice_id},
    )
