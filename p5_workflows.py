# © 2026 CoAssisted Workspace. Licensed under MIT.
"""P5 workflows — AP analytics on top of the join engine.

  - #9  per-project spend dashboard
  - #29 project P&L rollup (spend + revenue)
  - #55 duplicate invoice detection (semantic)
  - #90 AP anomaly detection (statistical)
"""

from __future__ import annotations

import datetime as _dt
import re
from collections import defaultdict
from typing import Iterable, Optional

import sheet_join as sj


# --------------------------------------------------------------------------- #
# #9 Per-project spend dashboard
# --------------------------------------------------------------------------- #


def build_project_spend_dashboard(
    invoice_rows: Iterable[dict],
    *,
    project_field: str = "project_code",
    vendor_field: str = "vendor",
    total_field: str = "total",
    date_field: str = "invoice_date",
    today: _dt.date | None = None,
    budgets: Optional[dict[str, float]] = None,
) -> list[dict]:
    """Roll up spend per project. Returns one dict per project with:
        project_code, ytd_total, last_30d_total, prev_30d_total,
        mom_delta, top_vendors[], invoice_count, budget, percent_of_budget.
    """
    today = today or _dt.date.today()
    budgets = budgets or {}
    rows = list(invoice_rows)
    eng = sj.Engine()
    eng.register("invoices", rows)

    # Add normalized fields.
    normalized = []
    for r in eng.query("invoices").rows():
        d = sj.parse_date(r.get(date_field))
        normalized.append({
            **r,
            "_total_f": sj.safe_float(r.get(total_field)),
            "_date_norm": d,
        })
    eng.register("invoices_norm", normalized)

    out: list[dict] = []
    for project_code, project_rows in (
        eng.query("invoices_norm").group_by(project_field).groups.items()
    ):
        project_code_val = project_code[0] if isinstance(project_code, tuple) else project_code
        if project_code_val is None:
            project_code_val = "(unassigned)"

        ytd_total = sum(r["_total_f"] for r in project_rows)
        # Last 30 days vs previous 30
        last_30d_cutoff = (today - _dt.timedelta(days=30)).isoformat()
        prev_30d_cutoff = (today - _dt.timedelta(days=60)).isoformat()
        last_30d = sum(
            r["_total_f"] for r in project_rows
            if r["_date_norm"] and r["_date_norm"] >= last_30d_cutoff
        )
        prev_30d = sum(
            r["_total_f"] for r in project_rows
            if r["_date_norm"]
            and prev_30d_cutoff <= r["_date_norm"] < last_30d_cutoff
        )
        mom_delta = last_30d - prev_30d

        # Top vendors by spend
        vendor_totals: dict[str, float] = {}
        for r in project_rows:
            v = r.get(vendor_field) or "(unknown)"
            vendor_totals[v] = vendor_totals.get(v, 0) + r["_total_f"]
        top_vendors = sorted(
            vendor_totals.items(), key=lambda kv: -kv[1],
        )[:5]

        budget = budgets.get(project_code_val)
        pct = (ytd_total / budget * 100) if budget else None

        out.append({
            "project_code": project_code_val,
            "ytd_total": round(ytd_total, 2),
            "last_30d_total": round(last_30d, 2),
            "prev_30d_total": round(prev_30d, 2),
            "mom_delta": round(mom_delta, 2),
            "invoice_count": len(project_rows),
            "top_vendors": [{"vendor": v, "spend": round(s, 2)}
                            for v, s in top_vendors],
            "budget": budget,
            "percent_of_budget": round(pct, 1) if pct is not None else None,
        })
    out.sort(key=lambda r: -r["ytd_total"])
    return out


# --------------------------------------------------------------------------- #
# #29 Project P&L rollup
# --------------------------------------------------------------------------- #


def build_pnl_rollup(
    invoice_rows: Iterable[dict],
    revenue_rows: Iterable[dict],
    *,
    project_field: str = "project_code",
    invoice_total_field: str = "total",
    revenue_total_field: str = "total",
) -> list[dict]:
    """Compute per-project margin = revenue - spend.

    invoice_rows: AP invoices (cost side)
    revenue_rows: invoices issued (revenue side, separate sheet)
    """
    eng = sj.Engine()

    def normalize(rows, total_field):
        out = []
        for r in rows:
            out.append({
                **r,
                "_total_f": sj.safe_float(r.get(total_field)),
            })
        return out

    invoices = normalize(invoice_rows, invoice_total_field)
    revenues = normalize(revenue_rows, revenue_total_field)

    spend_by_project: dict[str, float] = defaultdict(float)
    spend_count_by_project: dict[str, int] = defaultdict(int)
    for r in invoices:
        p = r.get(project_field) or "(unassigned)"
        spend_by_project[p] += r["_total_f"]
        spend_count_by_project[p] += 1

    revenue_by_project: dict[str, float] = defaultdict(float)
    revenue_count_by_project: dict[str, int] = defaultdict(int)
    for r in revenues:
        p = r.get(project_field) or "(unassigned)"
        revenue_by_project[p] += r["_total_f"]
        revenue_count_by_project[p] += 1

    all_projects = set(spend_by_project) | set(revenue_by_project)
    out: list[dict] = []
    for p in sorted(all_projects):
        spend = spend_by_project[p]
        revenue = revenue_by_project[p]
        margin = revenue - spend
        margin_pct = (margin / revenue * 100) if revenue else None
        out.append({
            "project_code": p,
            "spend": round(spend, 2),
            "revenue": round(revenue, 2),
            "margin": round(margin, 2),
            "margin_pct": round(margin_pct, 1) if margin_pct is not None else None,
            "spend_count": spend_count_by_project[p],
            "revenue_count": revenue_count_by_project[p],
        })
    out.sort(key=lambda r: -r["margin"])
    return out


# --------------------------------------------------------------------------- #
# #55 Duplicate invoice detection (semantic)
# --------------------------------------------------------------------------- #


def find_duplicate_invoices(
    invoice_rows: Iterable[dict],
    *,
    vendor_field: str = "vendor",
    total_field: str = "total",
    date_field: str = "invoice_date",
    invoice_number_field: str = "invoice_number",
    amount_tolerance_pct: float = 1.0,
    date_tolerance_days: int = 7,
) -> list[dict]:
    """Find pairs of invoices that look like duplicates across channels.

    Pair criteria:
      - Same vendor (case-insensitive)
      - Amount within ±tolerance_pct
      - Date within ±tolerance_days
      - Different invoice_number (if both have one) — same number is a
        well-known dup, not "semantic"

    Returns list of {invoice_a, invoice_b, reason} dicts.
    """
    rows = []
    for r in invoice_rows:
        rows.append({
            **r,
            "_vendor_norm": (r.get(vendor_field) or "").strip().lower(),
            "_total_f": sj.safe_float(r.get(total_field)),
            "_date_norm": sj.parse_date(r.get(date_field)),
            "_inv_num": (r.get(invoice_number_field) or "").strip(),
        })

    pairs = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if not a["_vendor_norm"] or a["_vendor_norm"] != b["_vendor_norm"]:
                continue
            if a["_total_f"] <= 0 or b["_total_f"] <= 0:
                continue
            # Amount tolerance
            if a["_total_f"] == 0:
                continue
            pct_diff = abs(a["_total_f"] - b["_total_f"]) / max(a["_total_f"], b["_total_f"]) * 100
            if pct_diff > amount_tolerance_pct:
                continue
            # Date tolerance
            if a["_date_norm"] and b["_date_norm"]:
                try:
                    da = _dt.date.fromisoformat(a["_date_norm"])
                    db = _dt.date.fromisoformat(b["_date_norm"])
                    if abs((da - db).days) > date_tolerance_days:
                        continue
                except ValueError:
                    pass
            # Same invoice number = obvious dup, but flag separately
            same_invnum = bool(a["_inv_num"] and a["_inv_num"] == b["_inv_num"])
            reason = (
                "exact invoice_number match" if same_invnum
                else f"same vendor, ${a['_total_f']:.2f} ≈ ${b['_total_f']:.2f}, dates within {date_tolerance_days}d"
            )
            pairs.append({
                "invoice_a": _strip_internal(a),
                "invoice_b": _strip_internal(b),
                "reason": reason,
                "exact_invnum_match": same_invnum,
                "amount_pct_diff": round(pct_diff, 2),
            })
    return pairs


def _strip_internal(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


# --------------------------------------------------------------------------- #
# #90 AP anomaly detection
# --------------------------------------------------------------------------- #


def detect_ap_anomalies(
    invoice_rows: Iterable[dict],
    *,
    vendor_field: str = "vendor",
    total_field: str = "total",
    iqr_k: float = 1.5,
    min_history: int = 3,
) -> list[dict]:
    """For each vendor, find invoices that are statistical outliers vs their history.

    Uses Tukey's IQR fences (default k=1.5). Vendors with fewer than `min_history`
    invoices are skipped.

    Returns list of {vendor, invoice, baseline, deviation, severity} dicts.
    """
    by_vendor: dict[str, list[dict]] = defaultdict(list)
    for r in invoice_rows:
        v = (r.get(vendor_field) or "").strip().lower()
        if not v:
            continue
        amount = sj.safe_float(r.get(total_field))
        if amount <= 0:
            continue
        by_vendor[v].append({**r, "_total_f": amount})

    anomalies = []
    for vendor_norm, invoices in by_vendor.items():
        if len(invoices) < min_history:
            continue
        amounts = [r["_total_f"] for r in invoices]
        q1, med, q3 = sj.iqr(amounts)
        for r in invoices:
            value = r["_total_f"]
            tukey_outlier = sj.is_outlier_iqr(value, q1, q3, k=iqr_k)
            # Robust fold-test catches the small-sample pathology where one
            # extreme value pulls q3 up enough to mask itself. Flag if value
            # is more than 4× away from median in either direction.
            fold_outlier = (
                med > 0 and (value > 4 * med or value < med / 4)
            )
            if not (tukey_outlier or fold_outlier):
                continue
            iqr_range = q3 - q1
            deviation = abs(value - med)
            if iqr_range > 0 and deviation > 5 * iqr_range:
                severity = "high"
            elif med > 0 and (value > 10 * med or value < med / 10):
                severity = "high"
            else:
                severity = "medium"
            anomalies.append({
                "vendor": r.get(vendor_field) or vendor_norm,
                "invoice": _strip_internal(r),
                "baseline": {
                    "median": round(med, 2),
                    "q1": round(q1, 2),
                    "q3": round(q3, 2),
                    "history_count": len(invoices),
                },
                "deviation_from_median": round(deviation, 2),
                "severity": severity,
                "trigger": "iqr" if tukey_outlier else "fold",
            })
    anomalies.sort(key=lambda a: -a["deviation_from_median"])
    return anomalies
