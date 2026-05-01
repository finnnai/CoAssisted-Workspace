# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Smoke for P3 — external feeds + watched sheets + 4 workflows."""
from __future__ import annotations

import datetime as _dt
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import external_feeds as ef
import p3_workflows as p3
import watched_sheets as ws


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke_p3_"))
    ef._override_cache_path_for_tests(tmp / "ef.json")
    ws._override_path_for_tests(tmp / "ws.json")

    print("=" * 100)
    print("SMOKE TEST: P3 — external feeds + watched sheets + 4 workflows")
    print("=" * 100)

    fails = []

    # ---- External feeds ---------------------------------------------- #
    print("\n[external feeds]")
    sf = ef.get_per_diem("San Francisco", "CA", year=2026)
    print(f"  ✓ SF per-diem: lodging=${sf.lodging_usd}, meals=${sf.meals_usd}")
    if sf.lodging_usd < 200: fails.append("SF lodging looks low")

    rate26 = ef.get_mileage_rate(2026)
    print(f"  ✓ 2026 IRS business mileage rate: ${rate26}/mi")

    eur = ef.get_fx_rate("EUR", "USD")
    print(f"  ✓ EUR→USD: {eur}")

    # ---- Watched sheets registry ------------------------------------- #
    print("\n[watched sheets]")
    ws.register("license", "ny-armed",
                fields={"name": "NY Armed Guard", "expires_at": "2026-06-15", "jurisdiction": "NY"})
    ws.register("license", "fl-unarmed",
                fields={"name": "FL Unarmed", "expires_at": "2026-05-15", "jurisdiction": "FL"})
    ws.register("retention", "financial-7y",
                fields={"category": "financial", "retain_years": 7})
    print(f"  ✓ registered 3 rules across 2 families")
    print(f"  ✓ list_family('license') = {len(ws.list_family('license'))}")
    print(f"  ✓ list_family('retention') = {len(ws.list_family('retention'))}")

    # ---- #36 License reminders --------------------------------------- #
    print("\n[#36 License reminders]")
    today = _dt.date(2026, 4, 28)
    reminders = p3.licenses_to_remind(today=today)
    for r in reminders:
        print(f"  ✓ {r['fields'].get('name')}: {r['days_until_expiry']}d → "
              f"bucket={r['crossed_threshold']}d")
    if len(reminders) != 2:
        fails.append(f"expected 2 reminders, got {len(reminders)}")

    # ---- #61 Mileage --------------------------------------------------- #
    print("\n[#61 Mileage tracker]")
    blocks = [
        {"date": "2026-01-15", "distance_miles": 12.5, "note": "Client A"},
        {"date": "2026-02-10", "distance_miles": 28.0, "note": "Client B"},
        {"date": "2026-04-10", "distance_miles": 45.5, "note": "Conference"},
        {"date": "2026-07-22", "distance_miles": 100.0, "note": "Q3 trip"},
    ]
    entries = p3.compute_mileage(blocks, year=2026)
    agg = p3.aggregate_mileage(entries)
    print(f"  ✓ {len(entries)} entries → ${agg['total_deduction_usd']} total deduction")
    print(f"  ✓ quarterly: {list(agg['by_quarter'].keys())}")

    # ---- #62 Per-diem ------------------------------------------------- #
    print("\n[#62 Per-diem calculator]")
    pd = p3.calculate_per_diem("San Francisco", "CA",
                                "2026-05-15", "2026-05-18", year=2026)
    print(f"  ✓ SF 3-night trip: lodging=${pd.lodging_total}, meals=${pd.meals_total}, "
          f"total=${pd.grand_total}")
    if pd.grand_total < 500:
        fails.append("SF 3-night per-diem looks too low")

    pd_day = p3.calculate_per_diem("Austin", "TX", "2026-06-01", "2026-06-01")
    print(f"  ✓ Austin day-trip: meals=${pd_day.meals_total}, lodging=${pd_day.lodging_total}")

    # ---- #47 DSR -------------------------------------------------------- #
    print("\n[#47 Data subject request]")
    report = p3.collate_dsr_results(
        "alice@external.com",
        gmail_threads=[
            {"id": "t1", "subject": "Re: pricing", "date": "2026-01-15",
             "link": "https://m/t1"},
            {"id": "t2", "subject": "Onboarding follow-up", "date": "2026-02-10",
             "link": "https://m/t2"},
        ],
        calendar_events=[
            {"id": "e1", "summary": "Demo with Alice",
             "start": {"dateTime": "2026-01-20T10:00:00"},
             "htmlLink": "https://cal/e1"},
        ],
        drive_files=[
            {"id": "f1", "name": "Alice — proposal.pdf",
             "modifiedTime": "2026-01-25T00:00:00Z",
             "webViewLink": "https://drv/f1"},
        ],
        contacts=[{"email": "alice@external.com", "name": "Alice External"}],
    )
    print(f"  ✓ DSR for {report['target_email']}: {report['summary']['total']} items")
    print(f"     Gmail={report['summary']['gmail']}, Cal={report['summary']['calendar']}, "
          f"Drive={report['summary']['drive']}, Contacts={report['summary']['contacts']}")
    md = p3.render_dsr_markdown(report)
    print(f"  ✓ markdown render: {len(md.splitlines())} lines")

    print()
    print("=" * 100)
    if fails:
        print(f"FAIL — {len(fails)} issue(s):")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("PASS — P3 infra + 4 workflows operational")
    return 0


if __name__ == "__main__":
    sys.exit(main())
