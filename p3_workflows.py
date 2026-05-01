# © 2026 CoAssisted Workspace. Licensed under MIT.
"""P3 workflows — pure-logic functions for licenses, DSR, mileage, per-diem.

  - #36 license + insurance expiration watcher
  - #47 data-subject request handler
  - #61 mileage tracker
  - #62 per-diem calculator
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Iterable, Optional

import external_feeds
import watched_sheets


# --------------------------------------------------------------------------- #
# #36 License + insurance expiration
# --------------------------------------------------------------------------- #


REMINDER_THRESHOLDS_DAYS = (90, 60, 30, 14, 7)


def licenses_to_remind(today: _dt.date | None = None) -> list[dict]:
    """Active licenses crossing a reminder threshold today.

    Reads from watched_sheets 'license' family. Each entry's fields must
    include 'expires_at' (YYYY-MM-DD) and optionally 'jurisdiction', 'name'.
    Returns entries with `days_until_expiry` ≤ 90 + a `crossed_threshold`
    field showing which reminder bucket triggered.
    """
    today = today or _dt.date.today()
    out = []
    for rec in watched_sheets.licenses_expiring(window_days=90, today=today):
        days = rec["days_until_expiry"]
        # Find SMALLEST threshold that days_left is still within (ascending).
        # (7, 14, 30, 60, 90) — for days=45, returns 60. Already past = None.
        crossed = next(
            (t for t in sorted(REMINDER_THRESHOLDS_DAYS) if days <= t),
            None,
        )
        out.append({**rec, "crossed_threshold": crossed})
    return out


# --------------------------------------------------------------------------- #
# #47 Data-subject request handler
# --------------------------------------------------------------------------- #


@dataclass
class DSRSearchResult:
    source: str           # "gmail" | "calendar" | "drive" | "contacts"
    item_type: str        # "thread", "event", "doc", "contact"
    item_id: str
    title: str            # subject / event title / file name / contact name
    timestamp: str        # ISO date
    link: Optional[str] = None

    def to_dict(self) -> dict:
        return self.__dict__


def collate_dsr_results(
    target_email: str,
    gmail_threads: Iterable[dict] = (),
    calendar_events: Iterable[dict] = (),
    drive_files: Iterable[dict] = (),
    contacts: Iterable[dict] = (),
) -> dict:
    """Aggregate DSR-relevant items across the four major data sources.

    Each parameter is a pre-fetched list of normalized dicts. The wrapper
    is responsible for the fetch — this function only collates.

    Returns a dict suitable for rendering as a DSR Doc.
    """
    items: list[DSRSearchResult] = []

    for t in gmail_threads:
        items.append(DSRSearchResult(
            source="gmail",
            item_type="thread",
            item_id=t.get("id", ""),
            title=t.get("subject") or "(no subject)",
            timestamp=t.get("date") or "",
            link=t.get("link"),
        ))
    for e in calendar_events:
        items.append(DSRSearchResult(
            source="calendar",
            item_type="event",
            item_id=e.get("id", ""),
            title=e.get("summary") or "(no title)",
            timestamp=(e.get("start") or {}).get("dateTime")
                       or (e.get("start") or {}).get("date") or "",
            link=e.get("htmlLink"),
        ))
    for f in drive_files:
        items.append(DSRSearchResult(
            source="drive",
            item_type="doc",
            item_id=f.get("id", ""),
            title=f.get("name") or "(unnamed)",
            timestamp=f.get("modifiedTime") or "",
            link=f.get("webViewLink"),
        ))
    for c in contacts:
        items.append(DSRSearchResult(
            source="contacts",
            item_type="contact",
            item_id=c.get("resourceName") or c.get("email", ""),
            title=c.get("name") or c.get("email") or "",
            timestamp=c.get("updatedAt") or "",
            link=None,
        ))

    return {
        "target_email": target_email,
        "generated_at": _dt.datetime.now().astimezone().isoformat(),
        "summary": {
            "gmail": sum(1 for i in items if i.source == "gmail"),
            "calendar": sum(1 for i in items if i.source == "calendar"),
            "drive": sum(1 for i in items if i.source == "drive"),
            "contacts": sum(1 for i in items if i.source == "contacts"),
            "total": len(items),
        },
        "items": [i.to_dict() for i in items],
    }


def render_dsr_markdown(report: dict) -> str:
    """Render a DSR report as a Doc-ready markdown body."""
    lines = []
    lines.append(f"# Data Subject Request — {report.get('target_email', '?')}")
    lines.append(f"_Generated: {report.get('generated_at', '?')}_")
    lines.append("")
    s = report.get("summary", {})
    lines.append(f"**Total items:** {s.get('total', 0)}")
    lines.append(f"  - Gmail threads: {s.get('gmail', 0)}")
    lines.append(f"  - Calendar events: {s.get('calendar', 0)}")
    lines.append(f"  - Drive files: {s.get('drive', 0)}")
    lines.append(f"  - Contact entries: {s.get('contacts', 0)}")
    lines.append("")
    by_source: dict[str, list[dict]] = {}
    for it in report.get("items", []):
        by_source.setdefault(it["source"], []).append(it)
    for source in ("gmail", "calendar", "drive", "contacts"):
        rows = by_source.get(source, [])
        if not rows:
            continue
        lines.append(f"## {source.title()}")
        for r in rows:
            ts = (r.get("timestamp") or "")[:10]
            link = f" — [open]({r['link']})" if r.get("link") else ""
            lines.append(f"- **{r['title']}** ({ts}){link}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# #61 Mileage tracker
# --------------------------------------------------------------------------- #


@dataclass
class MileageEntry:
    date: str             # YYYY-MM-DD
    miles: float
    purpose: str          # 'business' | 'medical' | 'charitable'
    rate_per_mile: float
    deduction_usd: float
    note: str = ""

    def to_dict(self) -> dict:
        return self.__dict__


def compute_mileage(
    drive_blocks: Iterable[dict],
    *,
    purpose: str = "business",
    year: int | None = None,
) -> list[MileageEntry]:
    """Convert calendar drive-time blocks into mileage entries.

    Each block needs: 'date' (YYYY-MM-DD), 'distance_miles' (float), 'note' (optional).
    """
    year = year or _dt.date.today().year
    rate = external_feeds.get_mileage_rate(year=year, purpose=purpose)
    out: list[MileageEntry] = []
    for b in drive_blocks:
        miles = float(b.get("distance_miles") or 0)
        if miles <= 0:
            continue
        out.append(MileageEntry(
            date=b.get("date", ""),
            miles=round(miles, 2),
            purpose=purpose,
            rate_per_mile=rate,
            deduction_usd=round(miles * rate, 2),
            note=b.get("note", ""),
        ))
    return out


def aggregate_mileage(entries: Iterable[MileageEntry]) -> dict:
    """Quarterly + yearly aggregations for a list of MileageEntries."""
    total_miles = 0.0
    total_deduction = 0.0
    by_quarter: dict[str, dict] = {}
    for e in entries:
        total_miles += e.miles
        total_deduction += e.deduction_usd
        try:
            d = _dt.date.fromisoformat(e.date)
            q = (d.month - 1) // 3 + 1
            qkey = f"{d.year}-Q{q}"
        except ValueError:
            qkey = "unknown"
        bucket = by_quarter.setdefault(qkey, {"miles": 0.0, "deduction_usd": 0.0, "count": 0})
        bucket["miles"] += e.miles
        bucket["deduction_usd"] += e.deduction_usd
        bucket["count"] += 1
    return {
        "total_miles": round(total_miles, 2),
        "total_deduction_usd": round(total_deduction, 2),
        "entry_count": len(list(entries)) if not isinstance(entries, list) else len(entries),
        "by_quarter": {k: {**v, "miles": round(v["miles"], 2),
                            "deduction_usd": round(v["deduction_usd"], 2)}
                        for k, v in sorted(by_quarter.items())},
    }


# --------------------------------------------------------------------------- #
# #62 Per-diem calculator
# --------------------------------------------------------------------------- #


@dataclass
class PerDiemBreakdown:
    """Per-diem calculation for one trip."""
    city: str
    state: str
    start_date: str
    end_date: str
    nights: int
    travel_days: int
    lodging_total: float
    meals_total: float
    grand_total: float
    rate: dict             # PerDiem.to_dict()

    def to_dict(self) -> dict:
        return self.__dict__


def calculate_per_diem(
    city: str, state: str,
    start_date: str, end_date: str,
    *,
    travel_day_meal_pct: float = 0.75,
    year: int | None = None,
) -> PerDiemBreakdown:
    """Compute per-diem totals for a trip.

    Following GSA convention:
      - Lodging: 1 lodging × N nights (where N = end - start days)
      - Meals: full M&IE for each full day, 75% on first + last day
    """
    s = _dt.date.fromisoformat(start_date)
    e = _dt.date.fromisoformat(end_date)
    if e < s:
        raise ValueError("end_date must be on or after start_date")
    year = year or s.year
    rate = external_feeds.get_per_diem(city, state, year)

    nights = (e - s).days  # number of nights of lodging
    total_days = nights + 1
    if total_days <= 1:
        # Day-trip: 75% M&IE × 1
        meals_total = rate.meals_usd * travel_day_meal_pct
        lodging_total = 0.0
        travel_days = 1
    else:
        # 75% on travel days (first + last), full on inner days
        full_days = max(0, total_days - 2)
        meals_total = (rate.meals_usd * 2 * travel_day_meal_pct) + (rate.meals_usd * full_days)
        lodging_total = rate.lodging_usd * nights
        travel_days = 2

    return PerDiemBreakdown(
        city=city, state=state.upper(),
        start_date=start_date, end_date=end_date,
        nights=nights,
        travel_days=travel_days,
        lodging_total=round(lodging_total, 2),
        meals_total=round(meals_total, 2),
        grand_total=round(lodging_total + meals_total, 2),
        rate=rate.to_dict(),
    )
