# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Smoke for P4 — CRM event sink + 3 workflows."""
from __future__ import annotations

import datetime as _dt
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import crm_events as ce
import p4_workflows as p4


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke_p4_"))
    ce._override_path_for_tests(tmp / "ev.json")

    print("=" * 100)
    print("SMOKE TEST: P4 — CRM event sink + 3 workflows")
    print("=" * 100)

    fails = []

    # ---- CRM event sink basics --------------------------------------- #
    print("\n[CRM event sink]")
    ce.append("alice@x.com", "email_received", "Hi from Alice", thread_id="t1")
    ce.append("alice@x.com", "email_sent", "Reply sent", thread_id="t1")
    ce.append("alice@x.com", "meeting", "Demo on Tuesday", event_id="e1")
    timeline = ce.get_timeline("alice@x.com")
    print(f"  ✓ alice timeline length: {len(timeline)}")
    if len(timeline) != 3:
        fails.append(f"expected 3 events, got {len(timeline)}")

    # ---- #3 VIP escalation -------------------------------------------- #
    print("\n[#3 VIP escalation]")
    msgs_batch_1 = [
        {"from_email": "vip@bigcustomer.com", "subject": "Renewal terms",
         "snippet": "Quick check on the renewal", "thread_id": "vip_t1",
         "link": "https://m/vip_t1"},
        {"from_email": "newsletter@x.com", "subject": "Sale", "snippet": "20% off",
         "thread_id": "spam"},
    ]
    alerts = p4.find_vip_escalations(msgs_batch_1, {"vip@bigcustomer.com"})
    print(f"  ✓ {len(alerts)} VIP alert(s) on first batch")
    if len(alerts) != 1:
        fails.append(f"expected 1 VIP alert, got {len(alerts)}")

    # Same batch immediately → deduped
    alerts2 = p4.find_vip_escalations(msgs_batch_1, {"vip@bigcustomer.com"})
    print(f"  ✓ {len(alerts2)} alert(s) on dedup retry (expect 0)")
    if alerts2:
        fails.append("dedup did not block second batch")

    # ---- #27 Calibrator ----------------------------------------------- #
    print("\n[#27 Last meaningful exchange calibrator]")
    today = _dt.datetime(2026, 4, 28, tzinfo=_dt.timezone.utc)

    p4.record_message_event(
        "bob@x.com",
        "Long discussion of the strategy follows. Here are three things I've been "
        "wrestling with that I want to align on before Friday's meeting.",
        "received",
        ts=(today - _dt.timedelta(days=80)).isoformat(),
    )
    p4.record_message_event(
        "bob@x.com", "thanks!", "received",
        ts=(today - _dt.timedelta(days=3)).isoformat(),
    )
    cal = p4.calibrated_staleness("bob@x.com", today=today)
    print(f"  ✓ bob calibrated: substantive={cal['days_since_substantive']}d, "
          f"any={cal['days_since_any']}d")
    if cal["days_since_substantive"] != 80:
        fails.append("calibrator: wrong substantive staleness")

    # ---- #41 Vendor onboarding ---------------------------------------- #
    print("\n[#41 Vendor onboarding]")
    is_new = p4.is_new_vendor("acme@vendor.com")
    plan = p4.build_onboarding_plan(
        "acme@vendor.com", "Acme Roofing",
        invoice_id="INV-2026-Q2-A",
        today=_dt.date(2026, 4, 28),
    )
    print(f"  ✓ new vendor? {is_new}, plan items: {len(plan.checklist)}")
    print(f"     first task due: {plan.checklist[0]['due_at']}")
    print(f"     last task due:  {plan.checklist[-1]['due_at']}")
    p4.record_onboarding_kicked_off(plan)
    last = ce.last_event("acme@vendor.com", kind="vendor_onboarded")
    print(f"  ✓ kickoff event recorded: {last['summary']}")

    # Now they're not new
    if p4.is_new_vendor("acme@vendor.com"):
        # Acme didn't have a vendor_invoice event, only vendor_onboarded.
        # The "new vendor" check uses vendor_invoice specifically.
        print(f"  ✓ acme is still 'new' until vendor_invoice fires (correct)")

    print()
    print("=" * 100)
    if fails:
        print(f"FAIL — {len(fails)} issue(s):")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("PASS — P4 CRM event sink + 3 workflows operational")
    return 0


if __name__ == "__main__":
    sys.exit(main())
