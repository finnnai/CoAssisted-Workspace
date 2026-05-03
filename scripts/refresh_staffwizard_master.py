# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Refresh the rolling Surefox Daily Operations Master + Historic Archive.

Replaces the earlier April-only build_april_master.py + push_april_master_to_sheet.py.
Now handles:

  - Rolling 90-day window (today - 90d → today). Anything older goes to
    the historic archive Sheet, never the live master.
  - Hours broken into Reg / OT / DT / Total in Project Rollup + Daily Totals.
  - One tab per project — same 20-column shape as Daily Detail, filtered
    to that JobNumber, sorted newest first.
  - Both Sheets get cleared before writing so stale data from prior shapes
    doesn't linger.

Usage:
    python3 scripts/refresh_staffwizard_master.py [--window-days 90]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import pathlib
import re
import sys
from collections import defaultdict

import openpyxl

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import auth  # noqa: E402
import labor_ingest  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

REPORTS_DIR = PROJECT_ROOT / "staffwizard_overall_reports"

# Hard-coded Sheet IDs. If you need a fresh archive, create a new Sheet
# in Drive and replace the ID here.
#
# 2026-05-02: Master Sheet recreated to switch per-project tab naming
# from Job Number (column B) to Job Description (column C). The previous
# Sheet ID (1-k-u7jttAjDpEchSBf61UA3K1SH3wx9K7dH6nE63Axk) is orphaned —
# trash it from Drive when you're ready.
MASTER_SHEET_ID = "1Lj-3pKqJhepBLUkOjV-_P9xgJhaQD-iFIGIUlomSGAg"
ARCHIVE_SHEET_ID = "1XJwNs3Ts4_crklQpgFNjv5AURc6_pSRP-16cZ8M910U"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

# Sheet tab names can't contain these.
_TAB_FORBIDDEN = re.compile(r"[\[\]\*\?:\\/]")


def _sanitize_tab_name(name: str) -> str:
    """Replace tab-name-forbidden chars + truncate to 100."""
    safe = _TAB_FORBIDDEN.sub("-", name).strip()
    safe = safe.strip("'")
    return safe[:100] or "Untitled"


def _pct(numer: float, denom: float) -> float:
    return round((numer / denom * 100), 1) if denom else 0.0


# Column orders — keep these in one place so add/reorder/rename is clean.
DAILY_DETAIL_COLS = [
    "Date", "Job Number", "Job Description",
    "Employee #", "Employee Name", "Post",
    "Shift Start", "Shift End",
    "Reg Hours", "OT Hours", "DT Hours", "Total Hours",
    "Reg Cost $", "Holiday Cost $", "OT Cost $", "DT Cost $", "Total Cost $",
    "Billable Hours", "Billable $", "Margin $",
]

PROJECT_ROLLUP_COLS = [
    "Date", "Job Number", "Job Description",
    "Shifts",
    "Reg Hours", "OT Hours", "DT Hours", "Total Hours",
    "Reg Cost $", "Holiday Cost $", "OT Cost $", "DT Cost $", "Total Cost $",
    "Revenue $", "Margin $", "Margin %",
]

DAILY_TOTALS_COLS = [
    "Date", "Shifts", "Unique Projects", "Unique Employees",
    "Reg Hours", "OT Hours", "DT Hours", "Total Hours",
    "Cost $", "Revenue $", "Margin $", "Margin %",
]

PROJECT_TOTALS_COLS = [
    "Project", "Days Active", "Shifts", "Unique Employees",
    "Reg Hours", "OT Hours", "DT Hours", "Total Hours",
    "Cost $", "Revenue $", "Margin $", "Margin %",
]


# -----------------------------------------------------------------------------
# Parse + bucket
# -----------------------------------------------------------------------------

def _parse_all() -> list[tuple[pathlib.Path, "labor_ingest.ParsedReport"]]:
    """Parse every .xls in REPORTS_DIR. Skip non-conforming files quietly."""
    out = []
    for f in sorted(REPORTS_DIR.glob("*.xls")):
        try:
            parsed = labor_ingest.parse_overall_report(f)
            out.append((f, parsed))
        except Exception as e:
            print(f"  SKIP {f.name}: {e}")
    return out


def _row_to_detail_dict(r: "labor_ingest.LaborRow") -> dict:
    return {
        "Date": r.work_date.isoformat() if r.work_date else "",
        "Job Number": r.job_number,
        "Job Description": r.job_description,
        "Employee #": r.employee_number,
        "Employee Name": r.employee_name,
        "Post": r.post_description,
        "Shift Start": r.shift_start,
        "Shift End": r.shift_end,
        "Reg Hours": r.hours,
        "OT Hours": r.overtime_hours,
        "DT Hours": r.doubletime_hours,
        "Total Hours": round(r.total_hours, 2),
        "Reg Cost $": r.dollars,
        "Holiday Cost $": r.holiday_dollars,
        "OT Cost $": r.overtime_dollars,
        "DT Cost $": r.doubletime_dollars,
        "Total Cost $": round(r.total_cost, 2),
        "Billable Hours": r.billable_hours,
        "Billable $": r.billable_dollars,
        "Margin $": round(r.margin, 2),
    }


def _build_aggregates(detail_rows: list[dict]) -> tuple[list[list], list[list]]:
    """Build Project Rollup + Daily Totals 2D arrays from detail rows."""
    # Aggregate by (job_number, date) with hour-type AND cost-type breakdown.
    pd_agg: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "shifts": 0, "reg_h": 0.0, "ot_h": 0.0, "dt_h": 0.0,
            "total_h": 0.0,
            "reg_cost": 0.0, "holiday_cost": 0.0,
            "ot_cost": 0.0, "dt_cost": 0.0, "total_cost": 0.0,
            "revenue": 0.0, "margin": 0.0,
            "job_description": "",
        }
    )
    daily_agg: dict[str, dict] = defaultdict(
        lambda: {
            "shifts": 0, "projects": set(), "employees": set(),
            "reg_h": 0.0, "ot_h": 0.0, "dt_h": 0.0, "total_h": 0.0,
            "cost": 0.0, "revenue": 0.0, "margin": 0.0,
        }
    )
    for d in detail_rows:
        date = d["Date"]
        job = d["Job Number"]
        if not date:
            continue

        a = pd_agg[(job, date)]
        a["shifts"] += 1
        a["reg_h"] += d["Reg Hours"]
        a["ot_h"] += d["OT Hours"]
        a["dt_h"] += d["DT Hours"]
        a["total_h"] += d["Total Hours"]
        a["reg_cost"] += d["Reg Cost $"]
        a["holiday_cost"] += d["Holiday Cost $"]
        a["ot_cost"] += d["OT Cost $"]
        a["dt_cost"] += d["DT Cost $"]
        a["total_cost"] += d["Total Cost $"]
        a["revenue"] += d["Billable $"]
        a["margin"] += d["Margin $"]
        a["job_description"] = d["Job Description"]

        t = daily_agg[date]
        t["shifts"] += 1
        t["projects"].add(job)
        if d["Employee #"]:
            t["employees"].add(d["Employee #"])
        t["reg_h"] += d["Reg Hours"]
        t["ot_h"] += d["OT Hours"]
        t["dt_h"] += d["DT Hours"]
        t["total_h"] += d["Total Hours"]
        t["cost"] += d["Total Cost $"]
        t["revenue"] += d["Billable $"]
        t["margin"] += d["Margin $"]

    # Project Rollup: sorted by date asc, then job number.
    rollup = [PROJECT_ROLLUP_COLS]
    for (job, date), a in sorted(pd_agg.items(), key=lambda x: (x[0][1], x[0][0])):
        rollup.append([
            date, job, a["job_description"], a["shifts"],
            round(a["reg_h"], 2), round(a["ot_h"], 2),
            round(a["dt_h"], 2), round(a["total_h"], 2),
            round(a["reg_cost"], 2), round(a["holiday_cost"], 2),
            round(a["ot_cost"], 2), round(a["dt_cost"], 2),
            round(a["total_cost"], 2),
            round(a["revenue"], 2), round(a["margin"], 2),
            _pct(a["margin"], a["revenue"]),
        ])

    # Daily Totals: sorted by date desc (most recent on top).
    totals = [DAILY_TOTALS_COLS]
    for date, t in sorted(daily_agg.items(), reverse=True):
        totals.append([
            date, t["shifts"], len(t["projects"]), len(t["employees"]),
            round(t["reg_h"], 2), round(t["ot_h"], 2),
            round(t["dt_h"], 2), round(t["total_h"], 2),
            round(t["cost"], 2), round(t["revenue"], 2),
            round(t["margin"], 2), _pct(t["margin"], t["revenue"]),
        ])

    return rollup, totals


def _build_project_totals(detail_rows: list[dict]) -> list[list]:
    """Window-wide totals per project (Job Description).

    One row per project. Sorted by Revenue desc so the biggest accounts
    sit at the top — managers want to see scale first, drill-down via
    the per-project tabs second.
    """
    proj_agg: dict[str, dict] = defaultdict(
        lambda: {
            "shifts": 0, "dates": set(), "employees": set(),
            "reg_h": 0.0, "ot_h": 0.0, "dt_h": 0.0, "total_h": 0.0,
            "cost": 0.0, "revenue": 0.0, "margin": 0.0,
        }
    )
    for d in detail_rows:
        # Same bucketing rule as the per-project tabs — fall back to
        # Job Number when Description is empty so nothing lands in a
        # blank-named row.
        proj = d.get("Job Description") or d.get("Job Number") or "(unlabeled)"
        a = proj_agg[proj]
        a["shifts"] += 1
        if d["Date"]:
            a["dates"].add(d["Date"])
        if d["Employee #"]:
            a["employees"].add(d["Employee #"])
        a["reg_h"] += d["Reg Hours"]
        a["ot_h"] += d["OT Hours"]
        a["dt_h"] += d["DT Hours"]
        a["total_h"] += d["Total Hours"]
        a["cost"] += d["Total Cost $"]
        a["revenue"] += d["Billable $"]
        a["margin"] += d["Margin $"]

    rows = [PROJECT_TOTALS_COLS]
    for proj, a in sorted(
        proj_agg.items(),
        key=lambda x: x[1]["revenue"], reverse=True,
    ):
        rows.append([
            proj, len(a["dates"]), a["shifts"], len(a["employees"]),
            round(a["reg_h"], 2), round(a["ot_h"], 2),
            round(a["dt_h"], 2), round(a["total_h"], 2),
            round(a["cost"], 2), round(a["revenue"], 2),
            round(a["margin"], 2), _pct(a["margin"], a["revenue"]),
        ])
    return rows


def _build_per_project_tabs(detail_rows: list[dict]) -> dict[str, list[list]]:
    """One 2D array per Job Description (Daily Detail column C).

    Rows from the same Job Description but different Job Numbers
    (e.g. job_number='Heritage Auctions NYC FT' job_description='Thunderbird 4'
    and job_number='655' job_description='SUREFOX') consolidate into one
    tab named after the description. Operator's preference 2026-05-02.
    """
    by_desc: dict[str, list[dict]] = defaultdict(list)
    for d in detail_rows:
        # Fall back to Job Number when Description is empty so we don't
        # accidentally bucket rows under a blank tab name.
        bucket = d.get("Job Description") or d.get("Job Number") or "(unlabeled)"
        by_desc[bucket].append(d)
    out: dict[str, list[list]] = {}
    for desc, rows in by_desc.items():
        rows_sorted = sorted(rows, key=lambda r: r["Date"], reverse=True)
        body = [DAILY_DETAIL_COLS]
        for r in rows_sorted:
            body.append([r[c] for c in DAILY_DETAIL_COLS])
        tab = _sanitize_tab_name(desc)
        out[tab] = body
    return out


def _build_sheet_payload(detail_rows: list[dict]) -> dict[str, list[list]]:
    """Assemble all tabs (main + per-project) for one Sheet."""
    if not detail_rows:
        # Even an empty Sheet should have headers so future writes don't
        # blow up the row count detection.
        return {
            "Daily Detail": [DAILY_DETAIL_COLS],
            "Project Rollup": [PROJECT_ROLLUP_COLS],
            "Daily Totals": [DAILY_TOTALS_COLS],
            "Daily Totals by Project": [PROJECT_TOTALS_COLS],
        }

    detail = [DAILY_DETAIL_COLS] + [
        [r[c] for c in DAILY_DETAIL_COLS] for r in detail_rows
    ]
    rollup, totals = _build_aggregates(detail_rows)
    project_totals = _build_project_totals(detail_rows)
    per_project = _build_per_project_tabs(detail_rows)

    payload = {
        "Daily Detail": detail,
        "Project Rollup": rollup,
        "Daily Totals": totals,
        "Daily Totals by Project": project_totals,
    }
    # Per-project tabs sorted alphabetically.
    for name in sorted(per_project.keys()):
        payload[name] = per_project[name]
    return payload


# -----------------------------------------------------------------------------
# Sheet I/O
# -----------------------------------------------------------------------------

def _push_to_sheet(svc, sheet_id: str, payload: dict[str, list[list]]) -> None:
    """Ensure tabs exist, clear them, then write the payload to each.

    Also deletes any tabs that aren't in the payload — e.g., when a
    project description ages out of the 90-day window. The default
    'Sheet1' is left alone if it's still around (we rename it on first
    run). Google requires at least one sheet in a spreadsheet, so the
    delete-stale step skips deletion if it would leave none.
    """
    meta = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"]
                for s in meta.get("sheets", [])}

    requests = []
    want_tabs = list(payload.keys())

    # If only the default 'Sheet1' is present and we want renaming,
    # rename it to the first wanted tab.
    if "Sheet1" in existing and want_tabs and want_tabs[0] not in existing:
        requests.append({
            "updateSheetProperties": {
                "properties": {
                    "sheetId": existing["Sheet1"],
                    "title": want_tabs[0],
                },
                "fields": "title",
            }
        })
        existing[want_tabs[0]] = existing.pop("Sheet1")

    # Add any missing tabs.
    for t in want_tabs:
        if t not in existing:
            requests.append({"addSheet": {"properties": {"title": t}}})

    if requests:
        svc.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id, body={"requests": requests}
        ).execute()
        # Refresh.
        meta = svc.spreadsheets().get(spreadsheetId=sheet_id).execute()
        existing = {s["properties"]["title"]: s["properties"]["sheetId"]
                    for s in meta.get("sheets", [])}

    # Delete any stale tabs not in the new payload. Google won't let
    # you delete the last sheet in a spreadsheet, so guard against
    # that by ensuring at least one wanted tab survives.
    stale = [name for name in existing if name not in want_tabs]
    if stale and len(want_tabs) >= 1:
        del_requests = [
            {"deleteSheet": {"sheetId": existing[name]}} for name in stale
        ]
        svc.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id, body={"requests": del_requests},
        ).execute()
        for name in stale:
            existing.pop(name, None)
        print(f"    cleaned {len(stale)} stale tabs: "
              f"{', '.join(stale[:5])}{' ...' if len(stale) > 5 else ''}")

    # Clear every wanted tab first, then write fresh.
    for tab in want_tabs:
        svc.spreadsheets().values().clear(
            spreadsheetId=sheet_id, range=f"'{tab}'",
        ).execute()

    # Write each tab.
    total_cells = 0
    for tab, rows in payload.items():
        if not rows:
            continue
        resp = svc.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"'{tab}'!A1",
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        ).execute()
        n = resp.get("updatedCells", 0)
        total_cells += n
        print(f"    {tab}: {n:,} cells")
    print(f"    -- total {total_cells:,} cells across {len(payload)} tabs")


# -----------------------------------------------------------------------------
# main
# -----------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--window-days", type=int, default=90,
                   help="Rolling window size in days (default: 90).")
    args = p.parse_args(argv)

    today = _dt.date.today()
    cutoff = today - _dt.timedelta(days=args.window_days)
    print(f"Window: {cutoff.isoformat()} → {today.isoformat()} "
          f"({args.window_days} days)")
    print(f"Older rows go to the historic archive Sheet.\n")

    print(f"Reading {REPORTS_DIR}...")
    parsed_files = _parse_all()
    if not parsed_files:
        print("No reports found.")
        return 2

    current: list[dict] = []
    archive: list[dict] = []
    for f, parsed in parsed_files:
        for r in parsed.rows:
            if not r.work_date:
                continue
            d = _row_to_detail_dict(r)
            if r.work_date >= cutoff:
                current.append(d)
            else:
                archive.append(d)
        print(f"  {f.name}: {len(parsed.rows)} shifts, work_date={parsed.work_date}")

    print(f"\nTotals: {len(current):,} current rows, {len(archive):,} archive rows")

    print(f"\nConnecting to Google Sheets...")
    creds = auth.get_credentials()
    svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

    print(f"\nMaster Sheet ({MASTER_SHEET_ID}):")
    master_payload = _build_sheet_payload(current)
    _push_to_sheet(svc, MASTER_SHEET_ID, master_payload)

    print(f"\nArchive Sheet ({ARCHIVE_SHEET_ID}):")
    archive_payload = _build_sheet_payload(archive)
    _push_to_sheet(svc, ARCHIVE_SHEET_ID, archive_payload)

    print()
    print("Done.")
    print(f"  Master:  https://docs.google.com/spreadsheets/d/{MASTER_SHEET_ID}/edit")
    print(f"  Archive: https://docs.google.com/spreadsheets/d/{ARCHIVE_SHEET_ID}/edit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
