# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""Baseline-deviation engine (AP-8 — v0.9.1).

Per-project spend-rate baseline. We can't expect every project to have a
hand-curated budget on day 1 — that doesn't exist. Instead, after a 30-day
cold-start window, we build a daily and weekly baseline (mean + std) of
total spend (receipts + invoices + labor cost). Past day 30, alert when:

    - Any single day's actuals deviate >2σ from the project's daily mean.
    - 7-day rolling sum deviates >2σ from the project's weekly mean.

Operator can also register a manual budget for a project. When set:

    - The manual budget overrides baseline alerting (alerts fire when
      cumulative spend approaches/exceeds the budget instead).
    - The manual budget is delta-checked against the baseline-projected
      spend; if the projection differs from the budget by >25%, the
      project surfaces as 'budget mismatch' so the operator can revise.

Public surface
--------------
    compute_baseline_for_project(code, *, days=30, today=None) -> dict
        {project_code, daily_mean, daily_std, weekly_mean, weekly_std,
         days_observed, days_required=30, ready, sample_total_spend}.
        ready=True only when days_observed >= 30.

    check_alerts(*, today=None) -> list[dict]
        Walks every active project, returns the set of >2σ deviations
        and budget-burn alerts.

    set_project_budget(code, monthly_amount, *, currency="USD") -> dict
    project_baseline_status(code) -> dict
        Combined: baseline + budget + alert state for one project.
    list_baselines() -> list[dict]
        For dashboard rendering.

State files (gitignored): `project_baselines.json`, `project_budgets.json`.

The actual spend rows come from per-project AP sheets + the StaffWizard
master rollup. Data fetch is delegated to `_fetch_spend_series(code, ...)`
which is split out so tests can inject synthetic data.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Optional


_PROJECT_ROOT = Path(__file__).resolve().parent
_BASELINES_PATH = _PROJECT_ROOT / "project_baselines.json"
_BUDGETS_PATH = _PROJECT_ROOT / "project_budgets.json"

# Cold-start window: number of days of observed spend required before
# baseline alerts fire. Below this, we report 'cold_start' status.
COLD_START_DAYS = 30

# Daily and weekly z-score thresholds for alerting.
ALERT_Z_DAILY = 2.0
ALERT_Z_WEEKLY = 2.0

# Budget-vs-baseline mismatch threshold. If the manual budget projects
# spend that differs from the baseline-projected spend by more than this
# fraction, we surface a 'budget_mismatch' alert so the operator revises.
BUDGET_MISMATCH_THRESHOLD = 0.25


# --------------------------------------------------------------------------- #
# Storage primitives
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=path.stem + ".", suffix=".json.tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp, path)
    except (OSError, TypeError, ValueError):
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# --------------------------------------------------------------------------- #
# Spend-series fetch (split out for testability)
# --------------------------------------------------------------------------- #


def _fetch_spend_series(
    code: str,
    *,
    days: int = COLD_START_DAYS,
    today: Optional[_dt.date] = None,
) -> list[tuple[_dt.date, float]]:
    """Return (date, total_spend) pairs for the last `days` days for one
    project. Aggregates receipts + invoices + labor cost.

    This is the live-data path — reads from per-project AP sheets and the
    StaffWizard master rollup. Tests can monkeypatch this.

    Returns an empty list if the project has no recorded spend yet.
    """
    today = today or _dt.date.today()
    start = today - _dt.timedelta(days=days - 1)
    series: dict[_dt.date, float] = defaultdict(float)

    # 1) Per-project AP sheet — receipts + invoices.
    try:
        import gservices
        import project_invoices as _pi
        import project_registry as _pr

        proj = _pr.get(code)
        if proj and proj.get("sheet_id"):
            sheets = gservices.sheets_service()
            resp = sheets.spreadsheets().values().get(
                spreadsheetId=proj["sheet_id"], range="A:AA",
            ).execute()
            rows = resp.get("values", []) or []
            if len(rows) >= 2:
                header = rows[0]
                try:
                    idx_date = header.index("invoice_date")
                    idx_total = header.index("total")
                except ValueError:
                    idx_date = idx_total = -1
                if idx_date >= 0 and idx_total >= 0:
                    for r in rows[1:]:
                        if not r or len(r) <= max(idx_date, idx_total):
                            continue
                        try:
                            d = _dt.date.fromisoformat((r[idx_date] or "")[:10])
                            amt = float(r[idx_total] or 0.0)
                        except (ValueError, TypeError):
                            continue
                        if start <= d <= today and amt > 0:
                            series[d] += amt
    except Exception:
        # Live data path may not be available in test contexts. Caller
        # gets whatever we did manage to fetch.
        pass

    # 2) StaffWizard labor cost (if mapped to this project).
    try:
        import labor_ingest  # type: ignore
        import project_registry as _pr

        proj = _pr.get(code)
        if proj and proj.get("staffwizard_job_number"):
            for d, cost in _staffwizard_daily_cost(
                proj["staffwizard_job_number"], start, today,
            ):
                series[d] += cost
    except Exception:
        pass

    return sorted(series.items())


def _staffwizard_daily_cost(
    job_number: str, start: _dt.date, end: _dt.date,
) -> list[tuple[_dt.date, float]]:
    """Sum Total Cost $ per day from parsed StaffWizard rows.

    Falls back to an empty list if the StaffWizard reports dir is empty
    or the parser can't be loaded.
    """
    try:
        import staffwizard_pipeline as _pipe
        rows = _pipe._parse_all_reports(_pipe.reports_dir())  # noqa: SLF001
    except Exception:
        return []
    daily: dict[_dt.date, float] = defaultdict(float)
    for r in rows:
        if str(r.get("Job Number") or "").strip() != str(job_number).strip():
            continue
        try:
            d = _dt.date.fromisoformat(str(r.get("Date") or "")[:10])
        except (ValueError, TypeError):
            continue
        if start <= d <= end:
            daily[d] += float(r.get("Total Cost $") or 0.0)
    return sorted(daily.items())


# --------------------------------------------------------------------------- #
# Baseline computation
# --------------------------------------------------------------------------- #


def compute_baseline_for_project(
    code: str,
    *,
    days: int = COLD_START_DAYS,
    today: Optional[_dt.date] = None,
    series: Optional[list[tuple[_dt.date, float]]] = None,
) -> dict:
    """Compute baseline stats for one project. If `series` is provided, use
    that directly (useful for tests + replays). Otherwise call the live
    fetch.

    Returns:
        {project_code, daily_mean, daily_std, weekly_mean, weekly_std,
         days_observed, days_required, ready, sample_total_spend,
         total_spend_30d}
    """
    today = today or _dt.date.today()
    if series is None:
        series = _fetch_spend_series(code, days=days, today=today)

    # Build a complete date-indexed array so days with zero spend count.
    daily_map = {d: amt for d, amt in series}
    full_days = [
        daily_map.get(today - _dt.timedelta(days=i), 0.0)
        for i in range(days)
    ]

    days_observed = sum(1 for v in full_days if v > 0)
    total = sum(full_days)
    daily_mean = total / days if days else 0.0
    daily_std = statistics.pstdev(full_days) if len(full_days) > 1 else 0.0

    # Weekly = sum of 7-day rolling. Build (days - 6) windows.
    weekly_sums: list[float] = []
    if days >= 7:
        for start in range(0, days - 6):
            weekly_sums.append(sum(full_days[start:start + 7]))
    weekly_mean = statistics.mean(weekly_sums) if weekly_sums else 0.0
    weekly_std = statistics.pstdev(weekly_sums) if len(weekly_sums) > 1 else 0.0

    record = {
        "project_code": (code or "").strip().upper(),
        "computed_at": _now_iso(),
        "as_of_date": today.isoformat(),
        "days": days,
        "days_observed": days_observed,
        "days_required": COLD_START_DAYS,
        "ready": days_observed >= COLD_START_DAYS,
        "daily_mean": round(daily_mean, 2),
        "daily_std": round(daily_std, 2),
        "weekly_mean": round(weekly_mean, 2),
        "weekly_std": round(weekly_std, 2),
        "total_spend_30d": round(total, 2),
        "sample_total_spend": round(total, 2),
    }

    # Persist for dashboard reads.
    cache = _load(_BASELINES_PATH)
    cache[record["project_code"]] = record
    _atomic_write(_BASELINES_PATH, cache)
    return record


# --------------------------------------------------------------------------- #
# Budget overlay
# --------------------------------------------------------------------------- #


def set_project_budget(
    code: str, monthly_amount: float, *, currency: str = "USD",
    note: Optional[str] = None,
) -> dict:
    """Register a manual monthly budget for a project. Currency defaults
    to USD. Returns the stored record.
    """
    code_norm = (code or "").strip().upper()
    if not code_norm:
        raise ValueError("code is required")
    try:
        amount = float(monthly_amount)
    except (TypeError, ValueError) as e:
        raise ValueError(f"monthly_amount must be numeric: {e}") from e
    if amount < 0:
        raise ValueError("monthly_amount must be non-negative")

    budgets = _load(_BUDGETS_PATH)
    budgets[code_norm] = {
        "project_code": code_norm,
        "monthly_amount": round(amount, 2),
        "currency": currency or "USD",
        "set_at": _now_iso(),
        "note": (note or "").strip() or None,
    }
    _atomic_write(_BUDGETS_PATH, budgets)
    return dict(budgets[code_norm])


def get_project_budget(code: str) -> Optional[dict]:
    return _load(_BUDGETS_PATH).get((code or "").strip().upper())


# --------------------------------------------------------------------------- #
# Alert checks
# --------------------------------------------------------------------------- #


def check_alerts(
    *,
    today: Optional[_dt.date] = None,
    project_codes: Optional[list[str]] = None,
) -> list[dict]:
    """Walk every active project (or the supplied list) and return the
    set of alerts.

    Alert types:
        'daily_deviation'  — yesterday's spend > daily_mean + Z*daily_std
        'weekly_deviation' — last 7 days sum > weekly_mean + Z*weekly_std
        'cold_start'       — project has less than 30 days observed
                              (informational, not actionable)
        'budget_burn'      — month-to-date spend > 80% of monthly budget
        'budget_exceeded'  — month-to-date spend > 100% of monthly budget
        'budget_mismatch'  — manual budget differs from baseline projection
                              by more than BUDGET_MISMATCH_THRESHOLD
    """
    today = today or _dt.date.today()
    yesterday = today - _dt.timedelta(days=1)

    # Decide the project set.
    if project_codes:
        codes = [c.strip().upper() for c in project_codes if c]
    else:
        try:
            import project_registry as _pr
            codes = [
                p["code"] for p in _pr.list_all(active_only=True)
                if p.get("active", True)
            ]
        except Exception:
            return []

    alerts: list[dict] = []
    for code in codes:
        baseline = compute_baseline_for_project(code, today=today)
        budget = get_project_budget(code)
        series = _fetch_spend_series(code, days=COLD_START_DAYS, today=today)

        # Cold-start guard for baseline alerts.
        if not baseline["ready"]:
            alerts.append({
                "project_code": code,
                "type": "cold_start",
                "severity": "info",
                "message": (
                    f"Cold-start: {baseline['days_observed']}/"
                    f"{baseline['days_required']} days observed. "
                    "Baseline alerts gated until ready."
                ),
                "as_of_date": today.isoformat(),
            })
        else:
            # Daily deviation — yesterday's spend.
            y_amt = next(
                (amt for d, amt in series if d == yesterday), 0.0,
            )
            threshold = baseline["daily_mean"] + ALERT_Z_DAILY * baseline["daily_std"]
            if y_amt > threshold and baseline["daily_std"] > 0:
                z = (y_amt - baseline["daily_mean"]) / baseline["daily_std"]
                alerts.append({
                    "project_code": code,
                    "type": "daily_deviation",
                    "severity": "warning",
                    "as_of_date": yesterday.isoformat(),
                    "amount": round(y_amt, 2),
                    "daily_mean": baseline["daily_mean"],
                    "daily_std": baseline["daily_std"],
                    "z_score": round(z, 2),
                    "message": (
                        f"{code}: yesterday's spend ${y_amt:,.2f} is {z:.1f}σ "
                        f"above daily mean ${baseline['daily_mean']:,.2f}."
                    ),
                })

            # Weekly deviation — last 7 days.
            week_total = sum(
                amt for d, amt in series
                if d > today - _dt.timedelta(days=7)
            )
            w_threshold = baseline["weekly_mean"] + ALERT_Z_WEEKLY * baseline["weekly_std"]
            if week_total > w_threshold and baseline["weekly_std"] > 0:
                wz = (week_total - baseline["weekly_mean"]) / baseline["weekly_std"]
                alerts.append({
                    "project_code": code,
                    "type": "weekly_deviation",
                    "severity": "warning",
                    "as_of_date": today.isoformat(),
                    "week_total": round(week_total, 2),
                    "weekly_mean": baseline["weekly_mean"],
                    "weekly_std": baseline["weekly_std"],
                    "z_score": round(wz, 2),
                    "message": (
                        f"{code}: 7-day spend ${week_total:,.2f} is {wz:.1f}σ "
                        f"above weekly mean ${baseline['weekly_mean']:,.2f}."
                    ),
                })

        # Budget overlay alerts (independent of baseline readiness).
        if budget:
            mtd = sum(
                amt for d, amt in series
                if d.year == today.year and d.month == today.month
            )
            ratio = (mtd / budget["monthly_amount"]) if budget["monthly_amount"] else 0
            if ratio >= 1.0:
                alerts.append({
                    "project_code": code,
                    "type": "budget_exceeded",
                    "severity": "critical",
                    "month_to_date": round(mtd, 2),
                    "monthly_budget": budget["monthly_amount"],
                    "ratio": round(ratio, 2),
                    "message": (
                        f"{code}: MTD ${mtd:,.2f} exceeds budget "
                        f"${budget['monthly_amount']:,.2f}."
                    ),
                })
            elif ratio >= 0.80:
                alerts.append({
                    "project_code": code,
                    "type": "budget_burn",
                    "severity": "warning",
                    "month_to_date": round(mtd, 2),
                    "monthly_budget": budget["monthly_amount"],
                    "ratio": round(ratio, 2),
                    "message": (
                        f"{code}: MTD ${mtd:,.2f} at {ratio:.0%} of budget."
                    ),
                })

            # Mismatch check — only when baseline is ready.
            if baseline["ready"]:
                projected = baseline["daily_mean"] * 30  # rough monthly projection
                if budget["monthly_amount"] > 0:
                    delta = abs(projected - budget["monthly_amount"]) / budget["monthly_amount"]
                    if delta > BUDGET_MISMATCH_THRESHOLD:
                        alerts.append({
                            "project_code": code,
                            "type": "budget_mismatch",
                            "severity": "info",
                            "monthly_budget": budget["monthly_amount"],
                            "projected_monthly_from_baseline": round(projected, 2),
                            "delta_pct": round(delta * 100, 1),
                            "message": (
                                f"{code}: budget ${budget['monthly_amount']:,.2f} "
                                f"differs from baseline projection "
                                f"${projected:,.2f} by {delta:.0%}."
                            ),
                        })

    return alerts


def project_baseline_status(code: str, *, today: Optional[_dt.date] = None) -> dict:
    """Combined baseline + budget + alerts for one project. Used by the
    project dashboard tile.
    """
    today = today or _dt.date.today()
    baseline = compute_baseline_for_project(code, today=today)
    budget = get_project_budget(code)
    alerts = [a for a in check_alerts(today=today, project_codes=[code])]
    return {
        "project_code": baseline["project_code"],
        "baseline": baseline,
        "budget": budget,
        "alerts": alerts,
        "as_of_date": today.isoformat(),
    }


def list_baselines() -> list[dict]:
    cache = _load(_BASELINES_PATH)
    rows = list(cache.values())
    rows.sort(key=lambda r: r.get("project_code", ""))
    return rows
