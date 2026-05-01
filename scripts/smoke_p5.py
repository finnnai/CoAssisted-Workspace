# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Smoke for P5 — join engine + 4 AP analytics workflows."""
from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import p5_workflows as p5
import sheet_join as sj


def main() -> int:
    print("=" * 100)
    print("SMOKE TEST: P5 — join engine + 4 AP analytics workflows")
    print("=" * 100)
    fails = []

    # ---- Join engine basics ----------------------------------------- #
    print("\n[Join engine]")
    eng = sj.Engine()
    eng.register("invoices", [
        {"id": 1, "vendor_id": "V1", "total": 1000, "project": "ALPHA"},
        {"id": 2, "vendor_id": "V2", "total": 500,  "project": "BETA"},
        {"id": 3, "vendor_id": "V1", "total": 250,  "project": "ALPHA"},
    ])
    eng.register("vendors", [
        {"vendor_id": "V1", "name": "Acme",  "domain": "acme.com"},
        {"vendor_id": "V2", "name": "Bay",   "domain": "bay.com"},
    ])
    joined = (eng.query("invoices")
              .inner_join(eng.query("vendors"), left="vendor_id")
              .rows())
    print(f"  ✓ inner join produced {len(joined)} rows")

    grouped = (eng.query("invoices")
               .group_by("project")
               .agg(total=lambda rs: sum(r["total"] for r in rs),
                    n=lambda rs: len(rs))
               .rows())
    by_project = {r["project"]: r for r in grouped}
    print(f"  ✓ group_by/agg: ALPHA total ${by_project['ALPHA']['total']}")

    # ---- #9 Project spend dashboard --------------------------------- #
    print("\n[#9 Project spend dashboard]")
    today = _dt.date(2026, 4, 28)
    invoices = [
        {"project_code": "ALPHA", "vendor": "Acme",  "total": 5000,
         "invoice_date": "2026-04-15"},
        {"project_code": "ALPHA", "vendor": "Acme",  "total": 3000,
         "invoice_date": "2026-04-01"},
        {"project_code": "ALPHA", "vendor": "Beta",  "total": 1500,
         "invoice_date": "2026-03-15"},
        {"project_code": "BETA",  "vendor": "Acme",  "total": 800,
         "invoice_date": "2026-04-20"},
        {"project_code": "BETA",  "vendor": "Capital", "total": 400,
         "invoice_date": "2026-02-15"},
    ]
    rows = p5.build_project_spend_dashboard(
        invoices, today=today,
        budgets={"ALPHA": 12000, "BETA": 5000},
    )
    for r in rows:
        print(f"  ✓ {r['project_code']}: YTD ${r['ytd_total']}, "
              f"last-30d ${r['last_30d_total']}, "
              f"top vendor: {r['top_vendors'][0]['vendor']}, "
              f"budget: {r['percent_of_budget']}%")
    if rows[0]["project_code"] != "ALPHA":
        fails.append("ALPHA should rank first by YTD")

    # ---- #29 P&L rollup --------------------------------------------- #
    print("\n[#29 Project P&L rollup]")
    revenues = [
        {"project_code": "ALPHA", "total": 25000, "invoice_date": "2026-04-01"},
        {"project_code": "BETA",  "total": 6000,  "invoice_date": "2026-04-10"},
    ]
    pnl = p5.build_pnl_rollup(invoices, revenues)
    for r in pnl:
        margin_str = f"${r['margin']} ({r['margin_pct']}%)"
        print(f"  ✓ {r['project_code']}: spend ${r['spend']}, revenue ${r['revenue']}, margin {margin_str}")

    # ---- #55 Duplicate invoice detection ---------------------------- #
    print("\n[#55 Duplicate invoice detection]")
    dup_invs = [
        {"id": "i1", "vendor": "Acme", "total": 2500.00,
         "invoice_date": "2026-04-15", "invoice_number": "INV-99"},
        {"id": "i2", "vendor": "Acme", "total": 2503.00,  # ±0.12%
         "invoice_date": "2026-04-18", "invoice_number": "INV-99-DUP"},
        {"id": "i3", "vendor": "Acme", "total": 1000.00,
         "invoice_date": "2026-04-10", "invoice_number": "INV-100"},
        {"id": "i4", "vendor": "Bay",  "total": 2500.00,
         "invoice_date": "2026-04-15", "invoice_number": "INV-1"},
    ]
    pairs = p5.find_duplicate_invoices(dup_invs)
    print(f"  ✓ {len(pairs)} duplicate pair(s) flagged")
    for p in pairs:
        print(f"    - {p['invoice_a']['id']} ↔ {p['invoice_b']['id']}: {p['reason']}")
    if len(pairs) != 1:
        fails.append(f"expected 1 dup pair, got {len(pairs)}")

    # ---- #90 AP anomaly detection ----------------------------------- #
    print("\n[#90 AP anomaly detection]")
    anomaly_invs = [
        {"id": "a1", "vendor": "Routine", "total": 200},
        {"id": "a2", "vendor": "Routine", "total": 215},
        {"id": "a3", "vendor": "Routine", "total": 195},
        {"id": "a4", "vendor": "Routine", "total": 5000},  # outlier
        {"id": "a5", "vendor": "Routine", "total": 205},
        # Vendor with too few invoices to have a baseline
        {"id": "a6", "vendor": "Solo",    "total": 9999},
    ]
    anomalies = p5.detect_ap_anomalies(anomaly_invs, min_history=3)
    print(f"  ✓ {len(anomalies)} anomaly/anomalies flagged")
    for a in anomalies:
        print(f"    - {a['vendor']}: ${a['invoice']['total']} (median ${a['baseline']['median']}, "
              f"severity={a['severity']}, trigger={a['trigger']})")
    if len(anomalies) != 1:
        fails.append(f"expected 1 anomaly, got {len(anomalies)}")

    print()
    print("=" * 100)
    if fails:
        print(f"FAIL — {len(fails)} issue(s):")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("PASS — P5 join engine + 4 AP analytics workflows operational")
    return 0


if __name__ == "__main__":
    sys.exit(main())
