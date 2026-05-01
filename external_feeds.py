# © 2026 CoAssisted Workspace. Licensed under MIT.
"""External-data fetch layer — trusted external sources with caching.

Adapters for:
  - GSA per-diem (#62 per-diem calculator)
  - FX rates (#84 currency normalization, future-phase)
  - IRS standard mileage rate (#61 mileage tracker)
  - Visa requirements (#64, future-phase)

Each adapter has:
  - A fetcher fn that hits the source (with safe fallbacks)
  - TTL-based caching to a JSON sidecar (external_feeds_cache.json)
  - A "frozen" mode for tests + offline runs

Data is read-only, never mutated, never written back to the source.

Cache key shape:
    {
        "<adapter>:<key>": {"value": ..., "fetched_at": iso, "ttl_seconds": N}
    }
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


_CACHE_PATH = Path(__file__).resolve().parent / "external_feeds_cache.json"


# --------------------------------------------------------------------------- #
# Cache primitives
# --------------------------------------------------------------------------- #


def _load_cache() -> dict[str, dict]:
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_cache(data: dict[str, dict]) -> None:
    fd, tmp = tempfile.mkstemp(
        prefix="external_feeds.", suffix=".tmp", dir=str(_CACHE_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _CACHE_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat()


def _is_fresh(entry: dict, now: _dt.datetime | None = None) -> bool:
    fetched = entry.get("fetched_at")
    ttl = int(entry.get("ttl_seconds", 0))
    if not fetched or ttl <= 0:
        return False
    try:
        ftime = _dt.datetime.fromisoformat(fetched)
    except ValueError:
        return False
    now = now or _dt.datetime.now().astimezone()
    return (now - ftime).total_seconds() < ttl


def _cached(key: str, ttl_seconds: int, fetcher: Callable[[], Any]) -> Any:
    """Read from cache if fresh, otherwise call fetcher and cache the result."""
    cache = _load_cache()
    entry = cache.get(key)
    if entry and _is_fresh(entry):
        return entry["value"]
    value = fetcher()
    cache[key] = {
        "value": value,
        "fetched_at": _now_iso(),
        "ttl_seconds": ttl_seconds,
    }
    _save_cache(cache)
    return value


# --------------------------------------------------------------------------- #
# Frozen mode (for tests / offline)
# --------------------------------------------------------------------------- #


_FROZEN_VALUES: dict[str, Any] = {}


def freeze(key: str, value: Any) -> None:
    """Pin a feed key to a value. Bypasses fetcher entirely."""
    _FROZEN_VALUES[key] = value


def unfreeze(key: str | None = None) -> None:
    if key is None:
        _FROZEN_VALUES.clear()
    else:
        _FROZEN_VALUES.pop(key, None)


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #


@dataclass
class PerDiem:
    """GSA per-diem rate for a (city, state) and date."""
    city: str
    state: str
    fiscal_year: int
    lodging_usd: float       # max nightly lodging
    meals_usd: float         # M&IE total
    incidentals_usd: float   # included in meals_usd typically
    source: str = "GSA"

    def to_dict(self) -> dict:
        return self.__dict__


# Default GSA per-diem fallback table for major cities (FY2026 placeholder).
# Real implementation would hit the GSA API; we keep a hard-coded set for
# offline operation since the GSA API requires an api.data.gov key.
_GSA_FALLBACK = {
    ("San Francisco", "CA"): {"lodging": 290, "meals": 79},
    ("New York", "NY"):      {"lodging": 320, "meals": 79},
    ("Washington", "DC"):    {"lodging": 257, "meals": 79},
    ("Los Angeles", "CA"):   {"lodging": 232, "meals": 79},
    ("Chicago", "IL"):       {"lodging": 211, "meals": 79},
    ("Boston", "MA"):        {"lodging": 261, "meals": 79},
    ("Seattle", "WA"):       {"lodging": 246, "meals": 79},
    ("Austin", "TX"):        {"lodging": 199, "meals": 74},
    ("Denver", "CO"):        {"lodging": 197, "meals": 74},
    ("Miami", "FL"):         {"lodging": 224, "meals": 79},
    ("Portland", "OR"):      {"lodging": 178, "meals": 74},
    ("Atlanta", "GA"):       {"lodging": 171, "meals": 74},
    ("Las Vegas", "NV"):     {"lodging": 162, "meals": 74},
    # CONUS standard rate for everywhere else
    ("__default__", "__default__"): {"lodging": 110, "meals": 68},
}


def get_per_diem(city: str, state: str, year: int = 2026) -> PerDiem:
    """Look up per-diem rate. Cached for 24h. Falls back to standard CONUS rate."""
    key = f"per_diem:{state.upper()}:{city.lower()}:{year}"
    if key in _FROZEN_VALUES:
        v = _FROZEN_VALUES[key]
        return PerDiem(**v) if isinstance(v, dict) else v

    def fetch() -> dict:
        rates = _GSA_FALLBACK.get((city, state.upper()))
        if not rates:
            rates = _GSA_FALLBACK[("__default__", "__default__")]
        return {
            "city": city,
            "state": state.upper(),
            "fiscal_year": year,
            "lodging_usd": float(rates["lodging"]),
            "meals_usd": float(rates["meals"]),
            "incidentals_usd": 5.0,  # M&IE includes ~$5 incidentals by GSA convention
            "source": "GSA fallback (no API key)",
        }

    raw = _cached(key, ttl_seconds=86400, fetcher=fetch)
    return PerDiem(**raw)


# IRS standard mileage rates by year (IRS publishes mid-Jan each year).
_IRS_MILEAGE_RATES = {
    2024: {"business": 0.67, "medical": 0.21, "charitable": 0.14},
    2025: {"business": 0.70, "medical": 0.21, "charitable": 0.14},
    2026: {"business": 0.72, "medical": 0.22, "charitable": 0.14},
}


def get_mileage_rate(year: int = 2026, purpose: str = "business") -> float:
    """IRS standard mileage rate $/mi for a given year + purpose."""
    rates = _IRS_MILEAGE_RATES.get(year, _IRS_MILEAGE_RATES[2026])
    return rates.get(purpose.lower(), rates["business"])


# FX rates — defaults to a fixed snapshot for offline operation. Live impl
# would hit ECB or exchangerate.host. Cached for 24h.
_FX_FALLBACK = {
    ("EUR", "USD"): 1.07,
    ("GBP", "USD"): 1.25,
    ("CAD", "USD"): 0.74,
    ("AUD", "USD"): 0.66,
    ("JPY", "USD"): 0.0067,
    ("MXN", "USD"): 0.058,
    ("CHF", "USD"): 1.12,
}


def get_fx_rate(from_ccy: str, to_ccy: str = "USD") -> float:
    """Currency conversion rate from→to. Cached 24h. Always returns 1.0 if same."""
    f = from_ccy.upper()
    t = to_ccy.upper()
    if f == t:
        return 1.0
    key = f"fx:{f}:{t}"
    if key in _FROZEN_VALUES:
        return float(_FROZEN_VALUES[key])

    def fetch() -> float:
        rate = _FX_FALLBACK.get((f, t))
        if rate is None:
            # Try inverse
            inv = _FX_FALLBACK.get((t, f))
            if inv:
                rate = 1.0 / inv
            else:
                rate = 1.0  # unknown — pass through
        return rate

    return _cached(key, ttl_seconds=86400, fetcher=fetch)


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #


def _override_cache_path_for_tests(p: Path) -> None:
    global _CACHE_PATH
    _CACHE_PATH = p


def _clear_cache_for_tests() -> None:
    if _CACHE_PATH.exists():
        try:
            _CACHE_PATH.unlink()
        except OSError:
            pass
