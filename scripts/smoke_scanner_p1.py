# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Smoke test for the background scanner + 7 P1 checks.

Stubs every fetcher with realistic data, registers all 7 P1 checks, runs
the scanner end-to-end, and asserts each check fires with sensible alerts.
"""
from __future__ import annotations

import datetime as _dt
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scanner
import p1_checks


def main() -> int:
    # Isolate state to a tempfile so this smoke doesn't pollute the real one.
    tmp = Path(tempfile.mkdtemp(prefix="smoke_p1_"))
    scanner._override_state_path_for_tests(tmp / "scan_state.json")
    scanner._reset_registry_for_tests()

    now = _dt.datetime.now().astimezone()

    # ---- Stub fetchers ------------------------------------------------- #
    def inbox_fetcher():
        return [
            {"id": "1", "from": "no-reply@somenewsletter.com",
             "subject": "Your weekly digest", "threadId": "t1",
             "link": "https://m/1"},
            {"id": "2", "from": "Sarah <sarah@x.com>",
             "subject": "Quick question",
             "threadId": "t2", "link": "https://m/2"},
            {"id": "3", "from": "promo@something.com",
             "subject": "20% off this week ends Friday",
             "threadId": "t3", "link": "https://m/3"},
        ]

    def contacts_fetcher():
        return [
            {"name": "Alex Stale", "email": "alex@old.com",
             "days_since_contact": 90, "link": None},
            {"name": "Brian Recent", "email": "brian@new.com",
             "days_since_contact": 30, "link": None},
            {"name": "Conor Ancient", "email": "conor@x.com",
             "days_since_contact": 250, "link": None},
        ]

    def reciprocity_fetcher():
        return [
            {"name": "Mark", "email": "m@x.com",
             "sent_last_60": 8, "received_last_60": 1},
            {"name": "Lin", "email": "l@x.com",
             "sent_last_60": 4, "received_last_60": 3},  # balanced
            {"name": "Pat", "email": "p@x.com",
             "sent_last_60": 6, "received_last_60": 0},
        ]

    def send_later_fetcher():
        past = (now - _dt.timedelta(minutes=5)).isoformat()
        future = (now + _dt.timedelta(hours=3)).isoformat()
        return [
            {"id": "s1", "kind": "initial", "due_at_iso": past,
             "subject": "Send to client", "link": None},
            {"id": "s2", "kind": "followup", "due_at_iso": future,
             "subject": "Future followup", "link": None},
        ]

    def week_ahead_fetcher():
        events = [
            {"summary": "Customer demo with Anthropic", "id": "e1"},
            {"summary": "1:1 with Alex", "id": "e2"},
            {"summary": "Standup", "id": "e3"},
        ]
        deadlines = [
            {"title": "Q3 filing", "due_at": "Friday"},
        ]
        commitments = []
        return events, deadlines, commitments

    def retention_fetcher():
        return [
            {"id": "th1", "subject": "Q3 invoice from Acme",
             "snippet": "Pay $5,000 by Sept 30", "age_days": 400,
             "link": "https://m/th1"},
            {"id": "th2", "subject": "Grab lunch?",
             "snippet": "Hungry?", "age_days": 500,
             "link": "https://m/th2"},
        ]

    def end_of_day_fetcher():
        tasks = [
            {"id": "t1", "title": "Finish memo"},
            {"id": "t2", "title": "Reply to Allan"},
            {"id": "t3", "title": "Push P1 PR"},
        ]
        threads = [{"id": "th1"}, {"id": "th2"}]
        return tasks, threads

    # ---- Register all 7 ------------------------------------------------ #
    p1_checks.register_p1_checks(
        inbox_fetcher=inbox_fetcher,
        contacts_fetcher=contacts_fetcher,
        reciprocity_fetcher=reciprocity_fetcher,
        send_later_fetcher=send_later_fetcher,
        week_ahead_fetcher=week_ahead_fetcher,
        retention_fetcher=retention_fetcher,
        end_of_day_fetcher=end_of_day_fetcher,
    )

    print("=" * 100)
    print("SMOKE TEST: background scanner + 7 P1 checks")
    print("=" * 100)

    checks = scanner.list_checks()
    print(f"\nRegistered checks: {len(checks)}")
    for c in checks:
        print(f"  - {c['name']:<40} cadence={c['cadence_hours']}h  channel={c['channel']}")

    # Run all due (first run = everything is due)
    summary = scanner.run_due()

    print(f"\nFirst run_due():")
    print(f"  ran:           {len(summary['ran'])}")
    print(f"  skipped:       {len(summary['skipped'])}")
    print(f"  total alerts:  {summary['total_alerts']}")

    print()
    print("Per-check alert breakdown:")
    expected_alert_kinds = {
        "p1_inbox_auto_snooze":         {"auto_snooze"},
        "p1_stale_relationship_digest": {"stale_relationship"},
        "p1_reciprocity_flag":          {"reciprocity"},
        "p1_send_later_followup":       {"send_later_initial"},
        "p1_sunday_week_ahead":         {"week_ahead_summary", "deadline"},
        "p1_retention_sweep":           {"retention_candidate"},
        "p1_end_of_day_shutdown":       {"end_of_day_summary", "task_carryover"},
    }
    fails = []
    for check_run in summary["ran"]:
        kinds = {a["kind"] for a in check_run["alerts"]}
        expected = expected_alert_kinds.get(check_run["name"], set())
        ok = expected.issubset(kinds) if expected else True
        status = "✓" if ok else "✗"
        print(f"  {status} {check_run['name']:<40} {check_run['alert_count']} alerts  kinds={sorted(kinds)}")
        if not ok:
            fails.append(f"{check_run['name']}: expected {expected}, got {kinds}")

    # Run again immediately — should skip all (cadence not yet elapsed)
    summary2 = scanner.run_due()
    if summary2["ran"]:
        fails.append(f"Second run_due should skip all, got {len(summary2['ran'])} ran")
    print(f"\nSecond run_due() (cadence test): ran={len(summary2['ran'])} skipped={len(summary2['skipped'])}")

    # Force-run one
    forced = scanner.run_one("p1_inbox_auto_snooze")
    print(f"\nrun_one(p1_inbox_auto_snooze): {forced.alert_count} alerts")
    if forced.alert_count != 2:
        fails.append(f"forced inbox check: expected 2 alerts (newsletter + 20% off), got {forced.alert_count}")

    print()
    print("=" * 100)
    if fails:
        print("FAIL")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("PASS — scanner + 7 P1 checks all fire correctly with cadence honored")

    print()
    print("Sample full first-run summary (truncated):")
    sample = {
        "ran": [{"name": r["name"], "alert_count": r["alert_count"],
                 "first_alert": (r["alerts"][0] if r["alerts"] else None)}
                for r in summary["ran"]],
        "skipped": summary["skipped"],
        "total_alerts": summary["total_alerts"],
    }
    print(json.dumps(sample, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
