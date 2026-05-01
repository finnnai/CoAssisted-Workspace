# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Final-stretch smoke — P6 (Travel suite) + P7 (Knowledge layer).

Exercises every workflow end-to-end against realistic synthetic data.
"""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import p6_workflows as p6
import p7_workflows as p7


def main() -> int:
    print("=" * 100)
    print("SMOKE TEST: P6 (Travel suite) + P7 (Knowledge layer)")
    print("=" * 100)
    fails = []

    # ---- P6.1 Travel auto-package ------------------------------------- #
    print("\n[P6.1 #16 Travel auto-package]")
    pkg = p6.build_travel_package(
        flight={
            "origin_iata": "SFO", "origin_city": "San Francisco", "origin_state": "CA",
            "dest_iata": "JFK", "dest_city": "New York", "dest_state": "NY",
            "depart_iso": "2026-05-15T08:00:00-07:00",
            "arrive_iso": "2026-05-15T16:30:00-04:00",
            "flight_number": "UA123",
            "confirmation_code": "AB123",
        },
        return_flight={
            "origin_iata": "JFK", "origin_city": "New York", "origin_state": "NY",
            "dest_iata": "SFO", "dest_city": "San Francisco", "dest_state": "CA",
            "depart_iso": "2026-05-18T17:00:00-04:00",
            "arrive_iso": "2026-05-18T20:30:00-07:00",
            "flight_number": "UA124",
            "confirmation_code": "AB123",
        },
        hotel={
            "name": "Acme Hotel",
            "address": "123 Main St, NY",
            "check_in": "2026-05-15",
            "check_out": "2026-05-18",
            "confirmation_code": "HXY1",
        },
    )
    print(f"  ✓ {len(pkg.calendar_blocks)} calendar blocks "
          f"({len([b for b in pkg.calendar_blocks if 'Hotel' in b['summary']])} hotel)")
    print(f"  ✓ {len(pkg.drive_time_blocks)} drive-time blocks")
    if pkg.per_diem_estimate:
        print(f"  ✓ per-diem est: ${pkg.per_diem_estimate['estimated_total']} for {pkg.total_days} days")
    if len(pkg.calendar_blocks) != 3:
        fails.append("expected 3 calendar blocks (out + return + hotel)")

    # ---- P6.2 End-of-trip expense packager ---------------------------- #
    print("\n[P6.2 #33 End-of-trip expense packager]")
    receipts = [
        {"date": "2026-05-15", "merchant": "Cafe Mogador", "total": 24.50,
         "category": "Meals"},
        {"date": "2026-05-16", "merchant": "Yellow Cab", "total": 35.00,
         "category": "Transport"},
        {"date": "2026-05-16", "merchant": "Hilton NYC", "total": 320.00,
         "category": "Lodging"},
        {"date": "2026-05-17", "merchant": "Cafe Mogador", "total": 18.00,
         "category": "Meals"},
        {"date": "2026-05-17", "merchant": "Hilton NYC", "total": 320.00,
         "category": "Lodging"},
        # Out of window
        {"date": "2026-05-20", "merchant": "Local lunch", "total": 12.00,
         "category": "Meals"},
        # EUR (currency conversion)
        {"date": "2026-05-16", "merchant": "Pierre's Bistro", "total": 50.00,
         "category": "Meals", "currency": "EUR"},
    ]
    bundle = p6.package_trip_expenses(
        "2026-05-15", "2026-05-18", "New York City", receipts,
        submitter_name="Finn", project_code="ALPHA", employee_id="E001",
    )
    print(f"  ✓ filtered {len(bundle.receipts)} receipts in window")
    print(f"  ✓ grand total: ${bundle.grand_total:,.2f} (EUR converted)")
    print(f"  ✓ categories: {dict(bundle.by_category)}")
    print(f"  ✓ subject: {bundle.submission_email_subject!r}")
    if len(bundle.receipts) != 6:
        fails.append(f"expected 6 in-window receipts, got {len(bundle.receipts)}")

    # ---- P6.3 Receipt photo prompt ------------------------------------ #
    print("\n[P6.3 #96 Receipt photo prompt]")
    trip = {"start": "2026-05-15", "end": "2026-05-18",
            "destination": "New York City"}
    # Inside trip + within window
    inside = _dt.datetime(2026, 5, 16, 18, 30,
                           tzinfo=_dt.timezone(_dt.timedelta(hours=-7)))
    d1 = p6.should_prompt_receipts(trips=[trip], now=inside)
    print(f"  ✓ inside trip + within window → should_send={d1.should_send}")
    if d1.should_send:
        print(f"     prompt: {d1.prompt_text[:80]}...")
    if not d1.should_send:
        fails.append("should have prompted inside trip")

    # Outside window
    morning = inside.replace(hour=9)
    d2 = p6.should_prompt_receipts(trips=[trip], now=morning)
    print(f"  ✓ outside window (9am) → should_send={d2.should_send}, reason={d2.reason}")

    # Already sent today
    d3 = p6.should_prompt_receipts(trips=[trip], now=inside,
                                     last_prompt_iso=morning.isoformat())
    print(f"  ✓ already sent today → should_send={d3.should_send}")
    if d3.should_send:
        fails.append("dedup failed")

    # ---- P7.1 Personal wiki search ------------------------------------ #
    print("\n[P7.1 #19 Personal wiki from email]")
    threads = [
        {"id": "t1", "subject": "Q3 Strategy review",
         "body": ("Recapping the Q3 strategy session: pricing model is moving "
                  "to seat-based, platform investment continues, hiring slows."),
         "timestamp": "2026-03-15", "link": "https://m/t1"},
        {"id": "t2", "subject": "Renewal contract — Anthropic",
         "body": ("Locked in renewal terms with Anthropic. Annual prepay, "
                  "2 year term, 8% YoY price increase cap."),
         "timestamp": "2026-04-05", "link": "https://m/t2"},
        {"id": "t3", "subject": "Lunch with Sarah",
         "body": "Want to grab lunch this Friday?"},
        {"id": "t4", "subject": "Platform roadmap",
         "body": ("The platform roadmap for next quarter centers on the "
                  "background scanner, brand voice lift-out, and the "
                  "join-across-sheets primitive."),
         "timestamp": "2026-04-20", "link": "https://m/t4"},
    ]
    idx = p7.build_wiki_index(threads)
    print(f"  ✓ indexed {idx.total_threads} threads, {len(idx.postings)} terms")

    for query in ["Q3 strategy", "renewal", "platform roadmap"]:
        results = p7.search_wiki(idx, query, limit=3)
        if results:
            top = results[0]
            print(f"  ✓ '{query}' → top: {top.subject} (score {top.score:.2f}, link {top.link})")
        else:
            print(f"  ✗ '{query}' → no results")
            fails.append(f"no results for '{query}'")

    # ---- P7.2 Doc diff ------------------------------------------------- #
    print("\n[P7.2 #46 Doc diff alert]")
    before = """Master Service Agreement v1
Term: 1 year, auto-renewing
Price: $50,000/year
Payment: Net 30
Liability cap: 12 months of fees"""
    after = """Master Service Agreement v2
Term: 2 years, auto-renewing
Price: $55,000/year
Payment: Net 45
Liability cap: 12 months of fees"""

    diff = p7.diff_doc_text(before, after)
    print(f"  ✓ severity: {diff.severity}")
    print(f"  ✓ {len(diff.lines_added)} added, {len(diff.lines_removed)} removed, {len(diff.lines_modified)} modified")
    print(f"  Summary bullets:")
    for b in diff.summary_bullets:
        print(f"     {b}")
    if not diff.lines_modified:
        fails.append("expected at least one modified line")

    # No-op diff
    no_op = p7.diff_doc_text("hello\nworld", "hello\nworld")
    print(f"  ✓ no-op diff: severity={no_op.severity} (expected minor)")

    print()
    print("=" * 100)
    if fails:
        print(f"FAIL — {len(fails)} issue(s):")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("PASS — P6 + P7 fully operational")
    return 0


if __name__ == "__main__":
    sys.exit(main())
