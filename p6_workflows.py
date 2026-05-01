# © 2026 CoAssisted Workspace. Licensed under MIT.
"""P6 — Travel suite (3 workflows).

  - #16 Travel auto-package (flight + hotel parsing → calendar + drive-time + per-diem)
  - #33 End-of-trip expense packager (receipt window + Doc submission draft)
  - #96 Receipt photo prompt during trip (daily prompt during trip window)
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

import external_feeds
import sheet_join as sj


# --------------------------------------------------------------------------- #
# #16 Travel auto-package
# --------------------------------------------------------------------------- #


# Patterns to detect a travel-confirmation email at a glance. Real flight
# confirmations also expose machine-readable schema.org/FlightReservation
# JSON-LD blocks; we keep this regex layer for the dumb cases.
_FLIGHT_CONFIRMATION_PATTERNS = [
    r"\bflight\s+confirmation\b",
    r"\bbooking\s+confirmation\b",
    r"\bconfirmation\s+(?:number|code)\b",
    r"\b(boarding|itinerary)\b",
    r"\b(?:flight|departing)\s+to\b",
]
_FLIGHT_REGEX = [re.compile(p, re.IGNORECASE) for p in _FLIGHT_CONFIRMATION_PATTERNS]

_HOTEL_PATTERNS = [
    r"\bhotel\s+confirmation\b",
    r"\breservation\s+confirmation\b",
    r"\bcheck[-\s]?in\b",
    r"\bcheck[-\s]?out\b",
]
_HOTEL_REGEX = [re.compile(p, re.IGNORECASE) for p in _HOTEL_PATTERNS]


def is_travel_confirmation(subject: str, body: str) -> dict:
    """Quick classification of a possible travel email.

    Returns dict with {is_flight: bool, is_hotel: bool, confidence: 0.0-1.0}.
    """
    text = f"{subject or ''} {body or ''}"
    flight_hits = sum(1 for rx in _FLIGHT_REGEX if rx.search(text))
    hotel_hits = sum(1 for rx in _HOTEL_REGEX if rx.search(text))
    confidence = min(1.0, max(flight_hits, hotel_hits) / 3.0)
    return {
        "is_flight": flight_hits >= 2,
        "is_hotel":  hotel_hits  >= 2,
        "confidence": round(confidence, 2),
        "flight_hits": flight_hits,
        "hotel_hits": hotel_hits,
    }


@dataclass
class FlightLeg:
    """Normalized one-leg flight info."""
    origin_iata: str           # e.g. 'SFO'
    origin_city: str
    origin_state: str          # for per-diem
    dest_iata: str
    dest_city: str
    dest_state: str
    depart_iso: str            # local time at origin (ISO with tz)
    arrive_iso: str            # local time at dest
    flight_number: str
    confirmation_code: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class HotelStay:
    name: str
    address: str
    check_in: str              # YYYY-MM-DD
    check_out: str             # YYYY-MM-DD
    confirmation_code: str | None = None

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class TravelPackage:
    """The fan-out plan from a travel confirmation."""
    flight: Optional[FlightLeg]
    return_flight: Optional[FlightLeg]
    hotel: Optional[HotelStay]
    calendar_blocks: list[dict] = field(default_factory=list)
    drive_time_blocks: list[dict] = field(default_factory=list)
    per_diem_estimate: Optional[dict] = None
    total_days: int = 0

    def to_dict(self) -> dict:
        return {
            "flight": self.flight.to_dict() if self.flight else None,
            "return_flight": self.return_flight.to_dict() if self.return_flight else None,
            "hotel": self.hotel.to_dict() if self.hotel else None,
            "calendar_blocks": list(self.calendar_blocks),
            "drive_time_blocks": list(self.drive_time_blocks),
            "per_diem_estimate": self.per_diem_estimate,
            "total_days": self.total_days,
        }


def build_travel_package(
    flight: Optional[dict] = None,
    return_flight: Optional[dict] = None,
    hotel: Optional[dict] = None,
    *,
    drive_time_to_airport_min: int = 90,
    drive_time_from_airport_min: int = 60,
) -> TravelPackage:
    """Build a TravelPackage from already-parsed flight/hotel dicts.

    The MCP wrapper handles parsing flight emails (or accepts JSON-LD blocks);
    this function is purely about composing the resulting calendar fan-out.
    """
    flight_obj = FlightLeg(**flight) if flight else None
    return_obj = FlightLeg(**return_flight) if return_flight else None
    hotel_obj = HotelStay(**hotel) if hotel else None

    calendar_blocks = []
    drive_time_blocks = []

    # Calendar block for outbound flight (in dest TZ for sanity)
    if flight_obj:
        calendar_blocks.append({
            "summary": f"Flight {flight_obj.flight_number}: {flight_obj.origin_iata}→{flight_obj.dest_iata}",
            "start": {"dateTime": flight_obj.depart_iso},
            "end": {"dateTime": flight_obj.arrive_iso},
            "location": f"{flight_obj.origin_iata} airport",
            "color": "tomato",
        })
        # Drive-time block to airport
        try:
            depart_dt = _dt.datetime.fromisoformat(flight_obj.depart_iso)
            drive_start = depart_dt - _dt.timedelta(minutes=drive_time_to_airport_min)
            drive_time_blocks.append({
                "summary": f"Drive to {flight_obj.origin_iata}",
                "start": {"dateTime": drive_start.isoformat()},
                "end": {"dateTime": depart_dt.isoformat()},
                "color": "graphite",
            })
        except ValueError:
            pass

    # Calendar block for return flight + drive home
    if return_obj:
        calendar_blocks.append({
            "summary": f"Return {return_obj.flight_number}: {return_obj.origin_iata}→{return_obj.dest_iata}",
            "start": {"dateTime": return_obj.depart_iso},
            "end": {"dateTime": return_obj.arrive_iso},
            "location": f"{return_obj.origin_iata} airport",
            "color": "tomato",
        })
        try:
            arrive_dt = _dt.datetime.fromisoformat(return_obj.arrive_iso)
            drive_end = arrive_dt + _dt.timedelta(minutes=drive_time_from_airport_min)
            drive_time_blocks.append({
                "summary": f"Drive home from {return_obj.dest_iata}",
                "start": {"dateTime": arrive_dt.isoformat()},
                "end": {"dateTime": drive_end.isoformat()},
                "color": "graphite",
            })
        except ValueError:
            pass

    # Hotel block (all-day spanning)
    if hotel_obj:
        calendar_blocks.append({
            "summary": f"Hotel: {hotel_obj.name}",
            "start": {"date": hotel_obj.check_in},
            "end": {"date": hotel_obj.check_out},
            "location": hotel_obj.address,
            "color": "lavender",
        })

    # Per-diem estimate (uses dest of outbound, default to ZZ for fallback)
    per_diem = None
    total_days = 0
    if flight_obj and hotel_obj:
        try:
            ci = _dt.date.fromisoformat(hotel_obj.check_in)
            co = _dt.date.fromisoformat(hotel_obj.check_out)
            total_days = (co - ci).days + 1
            pd = external_feeds.get_per_diem(
                flight_obj.dest_city, flight_obj.dest_state,
                year=ci.year,
            )
            per_diem = {
                "city": pd.city,
                "state": pd.state,
                "lodging_per_night": pd.lodging_usd,
                "meals_per_day": pd.meals_usd,
                "estimated_total": round(
                    pd.lodging_usd * (total_days - 1) + pd.meals_usd * total_days,
                    2,
                ),
            }
        except (ValueError, TypeError):
            pass

    return TravelPackage(
        flight=flight_obj,
        return_flight=return_obj,
        hotel=hotel_obj,
        calendar_blocks=calendar_blocks,
        drive_time_blocks=drive_time_blocks,
        per_diem_estimate=per_diem,
        total_days=total_days,
    )


# --------------------------------------------------------------------------- #
# #33 End-of-trip expense packager
# --------------------------------------------------------------------------- #


@dataclass
class TripExpenseBundle:
    trip_start: str
    trip_end: str
    destination: str
    receipts: list[dict]
    by_category: dict[str, float]
    grand_total: float
    submission_email_subject: str
    submission_email_body: str

    def to_dict(self) -> dict:
        return self.__dict__


def package_trip_expenses(
    trip_start: str,
    trip_end: str,
    destination: str,
    all_receipts: Iterable[dict],
    *,
    submitter_name: str = "",
    employee_id: str | None = None,
    project_code: str | None = None,
) -> TripExpenseBundle:
    """Filter receipts to the trip window, summarize by category, draft the
    submission email body.

    Each receipt: {date, merchant, total, category, currency (optional), note}.
    """
    try:
        start = _dt.date.fromisoformat(trip_start)
        end = _dt.date.fromisoformat(trip_end)
    except ValueError:
        raise ValueError("trip_start and trip_end must be YYYY-MM-DD")

    in_window: list[dict] = []
    for r in all_receipts:
        date_str = sj.parse_date(r.get("date"))
        if not date_str:
            continue
        try:
            d = _dt.date.fromisoformat(date_str)
        except ValueError:
            continue
        if start <= d <= end:
            in_window.append({**r, "_date_norm": date_str})

    by_category: dict[str, float] = {}
    grand_total = 0.0
    for r in in_window:
        cat = r.get("category") or "uncategorized"
        amt = sj.safe_float(r.get("total"))
        # FX conversion if currency != USD
        ccy = (r.get("currency") or "USD").upper()
        if ccy != "USD":
            rate = external_feeds.get_fx_rate(ccy, "USD")
            amt *= rate
        by_category[cat] = round(by_category.get(cat, 0) + amt, 2)
        grand_total += amt
    grand_total = round(grand_total, 2)

    # Compose submission email
    subject_parts = ["Expense report"]
    if project_code:
        subject_parts.append(project_code)
    subject_parts.append(destination)
    subject_parts.append(f"{trip_start} to {trip_end}")
    subject = " — ".join(subject_parts)

    body_lines = []
    body_lines.append(f"Hi AP,")
    body_lines.append("")
    body_lines.append(
        f"Submitting expense report for my trip to {destination} "
        f"from {trip_start} through {trip_end}."
    )
    body_lines.append("")
    body_lines.append(f"Total: ${grand_total:,.2f}")
    if project_code:
        body_lines.append(f"Project: {project_code}")
    if employee_id:
        body_lines.append(f"Employee ID: {employee_id}")
    body_lines.append(f"Receipts attached: {len(in_window)}")
    body_lines.append("")
    body_lines.append("Breakdown by category:")
    for cat, amt in sorted(by_category.items(), key=lambda kv: -kv[1]):
        body_lines.append(f"  - {cat}: ${amt:,.2f}")
    body_lines.append("")
    body_lines.append(f"Thanks,")
    body_lines.append(submitter_name or "Finn")

    return TripExpenseBundle(
        trip_start=trip_start,
        trip_end=trip_end,
        destination=destination,
        receipts=in_window,
        by_category=by_category,
        grand_total=grand_total,
        submission_email_subject=subject,
        submission_email_body="\n".join(body_lines),
    )


# --------------------------------------------------------------------------- #
# #96 Receipt photo prompt during trip
# --------------------------------------------------------------------------- #


@dataclass
class ReceiptPromptDecision:
    """Whether to send a receipt-photo prompt right now + the prompt text."""
    should_send: bool
    reason: str
    trip_destination: Optional[str] = None
    prompt_text: Optional[str] = None
    suggested_send_at_local: Optional[str] = None  # ISO HH:MM in local tz

    def to_dict(self) -> dict:
        return self.__dict__


# Default time of day to send the prompt (local time, 24-hour).
DEFAULT_PROMPT_HOUR = 18
DEFAULT_PROMPT_MINUTE = 30


def should_prompt_receipts(
    *,
    trips: Iterable[dict],
    now: _dt.datetime | None = None,
    target_hour: int = DEFAULT_PROMPT_HOUR,
    target_minute: int = DEFAULT_PROMPT_MINUTE,
    last_prompt_iso: Optional[str] = None,
    window_minutes: int = 90,
) -> ReceiptPromptDecision:
    """Decide if right now is a good moment to send the receipt prompt.

    Each trip dict needs: {start, end, destination}. Multiple trips ok —
    we use the active one if any.

    Returns:
        ReceiptPromptDecision with should_send + reason + prompt text.
    """
    now = now or _dt.datetime.now().astimezone()
    today = now.date()

    # Find an active trip
    active = None
    for t in trips:
        try:
            s = _dt.date.fromisoformat(t["start"])
            e = _dt.date.fromisoformat(t["end"])
        except (KeyError, ValueError):
            continue
        if s <= today <= e:
            active = t
            break
    if not active:
        return ReceiptPromptDecision(should_send=False, reason="not in any trip window")

    # Are we near the target hour?
    target_dt = now.replace(hour=target_hour, minute=target_minute,
                             second=0, microsecond=0)
    delta_minutes = abs((now - target_dt).total_seconds() / 60)
    if delta_minutes > window_minutes:
        return ReceiptPromptDecision(
            should_send=False,
            reason=f"outside ±{window_minutes} min window of {target_hour:02d}:{target_minute:02d}",
            trip_destination=active.get("destination"),
            suggested_send_at_local=f"{target_hour:02d}:{target_minute:02d}",
        )

    # Already prompted today?
    if last_prompt_iso:
        try:
            last_dt = _dt.datetime.fromisoformat(last_prompt_iso)
            if last_dt.date() == today:
                return ReceiptPromptDecision(
                    should_send=False,
                    reason="already prompted today",
                    trip_destination=active.get("destination"),
                )
        except ValueError:
            pass

    dest = active.get("destination") or "your trip"
    prompt = (
        f"Day {(today - _dt.date.fromisoformat(active['start'])).days + 1} of "
        f"your {dest} trip — keep today's receipts? Snap pics now and I'll route "
        f"them through the extractor. Reply with photos or 'none today'."
    )
    return ReceiptPromptDecision(
        should_send=True,
        reason="active trip + within prompt window",
        trip_destination=dest,
        prompt_text=prompt,
        suggested_send_at_local=f"{target_hour:02d}:{target_minute:02d}",
    )
