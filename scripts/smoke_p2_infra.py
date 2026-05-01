# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Smoke for P2 infra — brand voice composer + draft queue end-to-end."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import brand_voice
import draft_queue as dq


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke_p2_"))
    dq._override_path_for_tests(tmp / "draft_queue.json")

    fails = []

    print("=" * 100)
    print("SMOKE TEST: P2 infra — brand voice composer + draft queue")
    print("=" * 100)

    # ---- Compose 6 different intent types --------------------------- #
    scenarios = [
        ("reply",            {"recipient_name": "Sarah Fields", "sender_name": "Finn",
                              "subject_hint": "Renewal", "context": "Yes — terms hold."}),
        ("decline",          {"recipient_name": "Mark Adams", "sender_name": "Finn",
                              "subject_hint": "Speaking opportunity",
                              "context": "Schedule too packed this quarter."}),
        ("nudge",            {"recipient_name": "Allan", "sender_name": "Finn",
                              "subject_hint": "Contract redlines",
                              "context": "Following up — any blockers?"}),
        ("agenda",           {"recipient_name": "Brian", "sender_name": "Finn",
                              "context": "Q3 review prep, blockers, runway"}),
        ("rsvp_alternative", {"recipient_name": "Customer", "sender_name": "Finn",
                              "subject_hint": "Tuesday 2pm sync",
                              "context": "I have a conflict; alternates: Tue 10am, Wed 3pm, Fri 1pm."}),
        ("birthday",         {"recipient_name": "Linda Cho", "sender_name": "Finn",
                              "context": "Happy birthday — hope you're celebrating big."}),
    ]

    print("\nComposed drafts (template path — no LLM key required):")
    for intent, kwargs in scenarios:
        req = brand_voice.DraftRequest(intent=intent, audience="customer", **kwargs)
        d = brand_voice.compose_template_only(req)
        ok = bool(d.subject and d.plain)
        print(f"  {'✓' if ok else '✗'} {intent:<20} subject={d.subject[:40]!r}")
        if not ok:
            fails.append(f"{intent} produced empty draft")

    # ---- Queue lifecycle: enqueue → list → edit → approve → discard --- #
    print("\nQueue lifecycle:")
    eid1 = dq.enqueue(
        kind="auto_reply", subject="Re: Renewal", body_plain="Yes, locking in.",
        target="sarah@x.com", source_ref="thread:abc",
    )
    eid2 = dq.enqueue(
        kind="auto_reply", subject="Re: Q", body_plain="Looking into it.",
        target="brian@y.com",
    )
    eid3 = dq.enqueue(
        kind="rsvp", subject="Re: Tuesday sync", body_plain="Conflict, propose Tue 10am.",
        target="customer@x.com",
    )
    print(f"  ✓ enqueued 3 drafts")

    pending = dq.list_pending()
    if len(pending) != 3:
        fails.append(f"expected 3 pending, got {len(pending)}")
    print(f"  ✓ list_pending returns {len(pending)}")

    # Edit one
    updated = dq.update_body(eid1, body_plain="Yes, locking in.\nAttached is the signed copy.")
    if not updated or "<br>" not in updated["body_html"]:
        fails.append("update_body failed to refresh html")
    print(f"  ✓ edit_draft refreshed HTML on multi-line body")

    # Approve one (simulating send)
    approved = dq.approve(eid1)
    if not approved or approved["status"] != "approved":
        fails.append("approve didn't change status")
    print(f"  ✓ approve set status=approved")

    # Mark sent
    sent = dq.mark_sent(eid1)
    if not sent or sent["status"] != "sent":
        fails.append("mark_sent didn't change status")
    print(f"  ✓ mark_sent set status=sent")

    # Discard another
    if not dq.discard(eid3):
        fails.append("discard failed")
    print(f"  ✓ discard set status=discarded")

    # Filter by status
    pending2 = dq.list_pending()
    if len(pending2) != 1:
        fails.append(f"expected 1 pending after lifecycle, got {len(pending2)}")
    print(f"  ✓ remaining pending: {len(pending2)} (expected 1)")

    # Cross-status counts
    all_recs = dq.list_all()
    by_status = {}
    for r in all_recs:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    print(f"  ✓ status breakdown: {by_status}")

    print()
    print("=" * 100)
    if fails:
        print(f"FAIL — {len(fails)}")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("PASS — brand voice composer + draft queue working end-to-end")
    return 0


if __name__ == "__main__":
    sys.exit(main())
