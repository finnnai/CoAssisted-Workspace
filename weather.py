# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Weather adapter — hour-by-hour forecast for the day.

Default source: wttr.in (free, no API key). One-hour TTL cache.

Each hourly forecast row:
    {
        "hour_local": "06:00",        # HH:MM local time
        "temp_f": 58,
        "feels_like_f": 56,
        "condition": "clear",          # normalized canonical condition
        "icon": "☀️",                  # unicode emoji, email-safe
        "description": "Sunny",
        "precip_chance_pct": 0,
        "wind_mph": 5,
    }

Conditions are normalized to a canonical set so downstream code can
detect transitions (clear → rain) and highlight them.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from typing import Iterable, Optional
from urllib.parse import quote
from urllib.request import Request, urlopen

import external_feeds


# Canonical conditions + their display icon (unicode emoji, render in Gmail/Apple/Outlook)
_CONDITION_MAP = {
    "clear":           ("☀️", "Sunny"),
    "clear_night":     ("🌙", "Clear"),
    "partly_cloudy":   ("🌤️", "Partly cloudy"),
    "mostly_cloudy":   ("⛅", "Mostly cloudy"),
    "cloudy":          ("☁️", "Cloudy"),
    "fog":             ("🌫️", "Fog"),
    "drizzle":         ("🌦️", "Light rain"),
    "rain":            ("🌧️", "Rain"),
    "heavy_rain":      ("🌧️", "Heavy rain"),
    "thunderstorm":    ("⛈️", "Thunderstorm"),
    "snow":            ("❄️", "Snow"),
    "sleet":           ("🌨️", "Sleet"),
    "windy":           ("💨", "Windy"),
    "unknown":         ("🌡️", "Unknown"),
}


def _icon_for(condition: str) -> str:
    return _CONDITION_MAP.get(condition, _CONDITION_MAP["unknown"])[0]


def _description_for(condition: str) -> str:
    return _CONDITION_MAP.get(condition, _CONDITION_MAP["unknown"])[1]


# wttr.in returns this code list. Normalize to our canonical set.
_WTTR_CODE_TO_CANON = {
    "113": "clear",            # Sunny / Clear
    "116": "partly_cloudy",
    "119": "mostly_cloudy",
    "122": "cloudy",
    "143": "fog",
    "176": "drizzle",
    "179": "sleet",
    "182": "sleet",
    "185": "drizzle",
    "200": "thunderstorm",
    "227": "snow",
    "230": "snow",
    "248": "fog",
    "260": "fog",
    "263": "drizzle",
    "266": "drizzle",
    "281": "drizzle",
    "284": "drizzle",
    "293": "rain",
    "296": "rain",
    "299": "heavy_rain",
    "302": "heavy_rain",
    "305": "heavy_rain",
    "308": "heavy_rain",
    "311": "rain",
    "314": "rain",
    "317": "sleet",
    "320": "sleet",
    "323": "snow",
    "326": "snow",
    "329": "snow",
    "332": "snow",
    "335": "snow",
    "338": "snow",
    "350": "sleet",
    "353": "drizzle",
    "356": "rain",
    "359": "heavy_rain",
    "362": "sleet",
    "365": "sleet",
    "368": "snow",
    "371": "snow",
    "374": "sleet",
    "377": "sleet",
    "386": "thunderstorm",
    "389": "thunderstorm",
    "392": "thunderstorm",
    "395": "thunderstorm",
}


@dataclass
class HourlyForecast:
    hour_local: str           # "HH:MM"
    temp_f: int
    feels_like_f: int
    condition: str
    icon: str
    description: str
    precip_chance_pct: int
    wind_mph: int

    def to_dict(self) -> dict:
        return self.__dict__


@dataclass
class DailyForecast:
    location_label: str       # "San Francisco, CA"
    fetched_at: str
    sunrise: Optional[str]
    sunset: Optional[str]
    high_f: int
    low_f: int
    summary: str              # 1-line headline
    hourly: list[HourlyForecast] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "location_label": self.location_label,
            "fetched_at": self.fetched_at,
            "sunrise": self.sunrise,
            "sunset": self.sunset,
            "high_f": self.high_f,
            "low_f": self.low_f,
            "summary": self.summary,
            "hourly": [h.to_dict() for h in self.hourly],
        }


# --------------------------------------------------------------------------- #
# Fetch + parse
# --------------------------------------------------------------------------- #


def _fetch_wttr_raw(location: str, timeout_seconds: int = 8) -> dict:
    """Hit wttr.in's JSON endpoint. Returns the raw response dict.
    Raises on network failure — caller decides whether to fall back.
    """
    url = f"https://wttr.in/{quote(location)}?format=j1"
    req = Request(url, headers={"User-Agent": "CoAssistedWorkspace/0.7"})
    with urlopen(req, timeout=timeout_seconds) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_wttr(raw: dict, location_label: str) -> DailyForecast:
    """Convert wttr.in JSON into our canonical DailyForecast."""
    today = (raw.get("weather") or [{}])[0]
    astronomy = (today.get("astronomy") or [{}])[0]
    high = int(today.get("maxtempF") or 0)
    low = int(today.get("mintempF") or 0)
    sunrise = astronomy.get("sunrise") or None
    sunset = astronomy.get("sunset") or None

    hourly_rows = today.get("hourly") or []
    hourly: list[HourlyForecast] = []
    for h in hourly_rows:
        # wttr time format: "0", "300", "600", ..., "2100"
        time_raw = h.get("time", "0")
        try:
            time_int = int(time_raw)
            hh = time_int // 100
            mm = time_int % 100
            hour_local = f"{hh:02d}:{mm:02d}"
        except ValueError:
            hour_local = "00:00"

        code = (h.get("weatherCode") or "").strip()
        condition = _WTTR_CODE_TO_CANON.get(code, "unknown")
        # Distinguish night-clear if sunset has passed by hour
        if condition == "clear" and sunset and _hour_past(hour_local, sunset):
            condition = "clear_night"

        hourly.append(HourlyForecast(
            hour_local=hour_local,
            temp_f=int(h.get("tempF") or 0),
            feels_like_f=int(h.get("FeelsLikeF") or 0),
            condition=condition,
            icon=_icon_for(condition),
            description=_description_for(condition),
            precip_chance_pct=int(h.get("chanceofrain") or 0),
            wind_mph=int(h.get("windspeedMiles") or 0),
        ))

    summary = _summarize(high, low, hourly)
    return DailyForecast(
        location_label=location_label,
        fetched_at=_dt.datetime.now().astimezone().isoformat(),
        sunrise=sunrise,
        sunset=sunset,
        high_f=high,
        low_f=low,
        summary=summary,
        hourly=hourly,
    )


def _hour_past(hour_local: str, sunset_str: str) -> bool:
    """True if hour_local (HH:MM) is past sunset_str (e.g. '08:30 PM')."""
    try:
        h_hh, h_mm = (int(x) for x in hour_local.split(":"))
        # Parse sunset like "08:30 PM"
        ss = sunset_str.strip().upper()
        is_pm = "PM" in ss
        ss_clean = ss.replace("AM", "").replace("PM", "").strip()
        ss_hh, ss_mm = (int(x) for x in ss_clean.split(":"))
        if is_pm and ss_hh != 12:
            ss_hh += 12
        if not is_pm and ss_hh == 12:
            ss_hh = 0
        return (h_hh, h_mm) >= (ss_hh, ss_mm)
    except (ValueError, AttributeError):
        return False


def _summarize(high: int, low: int,
               hourly: Iterable[HourlyForecast]) -> str:
    hourly = list(hourly)
    if not hourly:
        return f"High {high}°F · Low {low}°F"
    rain_hours = sum(1 for h in hourly if h.condition in {"rain", "heavy_rain", "drizzle", "thunderstorm"})
    snow_hours = sum(1 for h in hourly if h.condition in {"snow", "sleet"})
    if snow_hours >= 2:
        weather_note = f"snow ({snow_hours} hrs)"
    elif rain_hours >= 4:
        weather_note = f"rain expected ({rain_hours} hrs)"
    elif rain_hours >= 1:
        weather_note = "scattered rain"
    elif any(h.condition == "thunderstorm" for h in hourly):
        weather_note = "thunderstorms possible"
    else:
        clear = sum(1 for h in hourly if h.condition in {"clear", "partly_cloudy"})
        weather_note = "clear and pleasant" if clear >= len(hourly) // 2 else "mostly overcast"
    return f"{weather_note} · High {high}°F · Low {low}°F"


# --------------------------------------------------------------------------- #
# Per-city ideal temperature ranges
# --------------------------------------------------------------------------- #


# Approximate "comfort" temperature ranges (°F) tuned to each city's climate.
# Used by the daily standup chart to draw a green ideal-zone band that's
# meaningful for the local weather, not a one-size-fits-all 65-75°F.
_IDEAL_TEMP_RANGES_F: dict[str, tuple[int, int]] = {
    # West Coast / cool-summer cities
    "san francisco":   (60, 72),
    "oakland":         (60, 72),
    "san jose":        (62, 75),
    "los angeles":     (66, 78),
    "san diego":       (66, 78),
    "seattle":         (60, 72),
    "portland":        (62, 75),
    "vancouver":       (58, 70),

    # Desert + sun belt
    "phoenix":         (75, 90),
    "las vegas":       (75, 90),
    "tucson":          (72, 88),
    "albuquerque":     (68, 82),

    # Texas / Gulf
    "austin":          (72, 86),
    "houston":         (72, 86),
    "dallas":          (72, 86),
    "san antonio":     (72, 86),

    # Southeast / Florida
    "miami":           (75, 88),
    "tampa":           (74, 86),
    "orlando":         (74, 86),
    "atlanta":         (68, 82),
    "charlotte":       (66, 80),

    # Northeast
    "new york":        (65, 78),
    "boston":          (62, 75),
    "philadelphia":    (65, 78),
    "washington":      (66, 80),

    # Midwest
    "chicago":         (65, 78),
    "minneapolis":     (62, 75),
    "detroit":         (64, 76),
    "indianapolis":    (66, 78),

    # Mountain / high desert
    "denver":          (65, 80),
    "salt lake city":  (66, 80),
    "boise":           (66, 80),

    # Hawaii / tropical
    "honolulu":        (75, 85),

    # International defaults
    "london":          (60, 72),
    "paris":           (62, 75),
    "berlin":          (62, 75),
    "tokyo":           (66, 78),
    "singapore":       (76, 88),
    "sydney":          (66, 80),
    "dubai":           (75, 92),
    "mexico city":     (62, 75),
    "toronto":         (64, 76),
}


def get_ideal_range(location: str) -> tuple[int, int]:
    """Return (low_F, high_F) preferred temperature range for a location.

    Strategy:
      1. Match full lowercase string (city + state/country).
      2. Match the leading city portion only (before the comma).
      3. Substring match against any known city name in the table.
      4. Fall back to the broad CONUS default 65-75°F.
    """
    if not location:
        return (65, 75)
    norm = location.strip().lower()
    if norm in _IDEAL_TEMP_RANGES_F:
        return _IDEAL_TEMP_RANGES_F[norm]
    # First-token match (everything before the first comma)
    head = norm.split(",", 1)[0].strip()
    if head in _IDEAL_TEMP_RANGES_F:
        return _IDEAL_TEMP_RANGES_F[head]
    # Substring fallback (handles "downtown san francisco" etc.)
    for city, rng in _IDEAL_TEMP_RANGES_F.items():
        if city in head:
            return rng
    return (65, 75)


def detect_significant_changes(hourly: Iterable[HourlyForecast]) -> list[int]:
    """Return indices in `hourly` where the condition changes meaningfully.

    Significant transitions (used to highlight in email):
      - clear → any precip
      - any precip → clear
      - thunderstorm onset/end
    """
    hourly = list(hourly)
    transitions = []
    PRECIP = {"rain", "heavy_rain", "drizzle", "thunderstorm", "snow", "sleet"}
    for i in range(1, len(hourly)):
        a = hourly[i - 1].condition
        b = hourly[i].condition
        if (a in PRECIP) != (b in PRECIP):
            transitions.append(i)
        elif a == "thunderstorm" or b == "thunderstorm":
            if a != b:
                transitions.append(i)
    return transitions


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def get_today_forecast(location: str, *, ttl_seconds: int = 3600) -> DailyForecast:
    """Get today's hourly + summary forecast for a location.

    Caches for 1 hour. Falls back to a fixture if the network call fails.
    """
    key = f"weather:{location.lower()}"
    if key in external_feeds._FROZEN_VALUES:
        v = external_feeds._FROZEN_VALUES[key]
        if isinstance(v, DailyForecast):
            return v
        if isinstance(v, dict):
            return _from_dict(v)

    def fetch() -> dict:
        try:
            raw = _fetch_wttr_raw(location)
            forecast = _parse_wttr(raw, location_label=location)
            return forecast.to_dict()
        except Exception:
            return _fixture_forecast(location).to_dict()

    raw = external_feeds._cached(key, ttl_seconds=ttl_seconds, fetcher=fetch)
    return _from_dict(raw)


def _from_dict(d: dict) -> DailyForecast:
    hourly = [HourlyForecast(**h) for h in (d.get("hourly") or [])]
    return DailyForecast(
        location_label=d.get("location_label", ""),
        fetched_at=d.get("fetched_at", ""),
        sunrise=d.get("sunrise"),
        sunset=d.get("sunset"),
        high_f=int(d.get("high_f") or 0),
        low_f=int(d.get("low_f") or 0),
        summary=d.get("summary", ""),
        hourly=hourly,
    )


def _fixture_forecast(location: str) -> DailyForecast:
    """Reasonable fallback when the network is unavailable.
    8 readings at 3-hour intervals (00, 03, 06, 09, 12, 15, 18, 21) so the
    chart can plot a full 24-hour day."""
    # Start cool overnight, rise to mid-day high, drop again toward night.
    hours = [
        (0,  "clear_night",   54),
        (3,  "clear_night",   52),
        (6,  "clear",         54),
        (9,  "clear",         60),
        (12, "partly_cloudy", 66),
        (15, "partly_cloudy", 68),
        (18, "cloudy",        62),
        (21, "clear_night",   56),
    ]
    hourly = []
    for hr, cond, temp in hours:
        hourly.append(HourlyForecast(
            hour_local=f"{hr:02d}:00",
            temp_f=temp, feels_like_f=temp - 2,
            condition=cond, icon=_icon_for(cond),
            description=_description_for(cond),
            precip_chance_pct=0, wind_mph=6,
        ))
    return DailyForecast(
        location_label=location,
        fetched_at=_dt.datetime.now().astimezone().isoformat(),
        sunrise="06:30 AM", sunset="07:50 PM",
        high_f=68, low_f=52,
        summary="Fixture forecast — clear skies · High 68°F · Low 52°F",
        hourly=hourly,
    )


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #


def freeze_for_tests(location: str, forecast: DailyForecast | dict) -> None:
    key = f"weather:{location.lower()}"
    if isinstance(forecast, DailyForecast):
        external_feeds._FROZEN_VALUES[key] = forecast.to_dict()
    else:
        external_feeds._FROZEN_VALUES[key] = forecast
