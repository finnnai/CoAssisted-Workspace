# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""Geotab Drive API client (v0.9.1).

Replaces the v0.8.x stub with a real client that authenticates against
Geotab's MyGeotab API and pulls vehicle position data for the AP-5 routing
tiebreaker. When the operator hasn't configured Geotab credentials yet, the
client falls through to a 'no_credentials' status — the caller treats this
the same as a stub and falls through to the next tiebreaker (calendar,
chat picker).

Geotab API basics:
    - Endpoint: https://my.geotab.com/apiv1/
    - Auth: Authenticate with username + password OR existing session;
      receive a sessionId that's passed on subsequent calls.
    - Method: Get / GetCount / GetFeed POST with JSON envelope.

We use Get on `LogRecord` (vehicle position pings) filtered by Device +
DateTime range. Each record carries Latitude, Longitude, Speed,
DateTime — that's enough to map a transaction's timestamp to a vehicle's
location, which the AP-5 routing tiebreaker geocodes against project sites.

Public surface
--------------
    is_configured() -> bool
        Quick check the operator can call to know if Geotab is wired up.

    authenticate(*, force_refresh=False) -> dict
        Exchange username + password for a sessionId. Cached for the
        process lifetime. force_refresh=True forces re-auth.

    get_vehicle_positions(*, at_time, vehicle_ids=None, window_seconds=300)
        -> list[dict]
        Pull every position log for the given vehicles within
        [at_time - window, at_time + window]. Returns canonical dicts
        with vehicle_id, lat, lon, speed_kph, when_iso.

    lookup_position_by_driver(driver_email, *, at_time, window_seconds=300)
        -> dict | None
        Resolve driver → device (via Geotab's User → Device assignment)
        then position. Returns None if no record falls inside the window.

    list_devices() -> list[dict]
        For mapping config (driver email → vehicle_id) and operator UI.

Falls through cleanly to {status: 'no_credentials'} when:
    - config.geotab block is absent
    - config.geotab.username / password are empty
    - The first auth attempt fails (returns the error verbatim so the
      operator can fix creds)

State: in-memory only; sessionId expires server-side, refreshed lazily.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import threading
import urllib.error
import urllib.request
from typing import Optional


_log = logging.getLogger(__name__)


# Geotab API endpoint. Override via config.geotab.endpoint when a customer
# is on a regional my2/my3/my4 server.
DEFAULT_ENDPOINT = "https://my.geotab.com/apiv1/"

# Cached credentials state. Cleared on process restart.
_session_lock = threading.Lock()
_session_state: dict = {
    "session_id": None,
    "user_name": None,
    "database": None,
    "endpoint": None,
    "obtained_at": None,
}


# --------------------------------------------------------------------------- #
# Config helpers
# --------------------------------------------------------------------------- #


def _cfg() -> dict:
    try:
        import config
        block = config.get("geotab", {}) or {}
        return block if isinstance(block, dict) else {}
    except Exception:
        return {}


def is_configured() -> bool:
    """True iff config.geotab has username + password + database set."""
    cfg = _cfg()
    return bool(
        cfg.get("username")
        and cfg.get("password")
        and cfg.get("database")
    )


# --------------------------------------------------------------------------- #
# Low-level RPC
# --------------------------------------------------------------------------- #


def _post(method: str, params: dict, *, endpoint: Optional[str] = None) -> dict:
    """POST a JSON-RPC call to Geotab. Raises on transport / API errors.

    Geotab's wire format:
        {"method": "Get", "params": {"typeName": "Device", ...}}
        → {"result": [...]} on success
        → {"error": {"message": "..."}} on API error
    """
    body = json.dumps({"method": method, "params": params}).encode("utf-8")
    target = endpoint or _cfg().get("endpoint") or DEFAULT_ENDPOINT
    req = urllib.request.Request(
        target,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        raise ConnectionError(f"Geotab RPC transport error: {e}") from e

    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as e:
        raise ValueError(f"Geotab RPC bad JSON: {e}") from e

    if "error" in decoded:
        msg = (decoded["error"] or {}).get("message", "unknown error")
        raise RuntimeError(f"Geotab RPC error: {msg}")
    return decoded.get("result", {})


# --------------------------------------------------------------------------- #
# Authenticate
# --------------------------------------------------------------------------- #


def authenticate(*, force_refresh: bool = False) -> dict:
    """Exchange config.geotab credentials for a sessionId.

    Returns:
        {status: 'ok', session_id, user_name, database, endpoint, obtained_at}
        or {status: 'no_credentials'} if config is empty
        or {status: 'error', error: <verbatim>} if the API rejects.
    """
    if not is_configured():
        return {"status": "no_credentials"}

    cfg = _cfg()
    with _session_lock:
        if not force_refresh and _session_state.get("session_id"):
            return _ok_state()

        params = {
            "userName": cfg["username"],
            "password": cfg["password"],
            "database": cfg["database"],
        }
        try:
            res = _post("Authenticate", params)
        except Exception as e:
            return {"status": "error", "error": f"{type(e).__name__}: {e}"}

        # Geotab's Authenticate result shape:
        # {"path": "...", "credentials": {"userName": ..., "database": ..., "sessionId": ...}}
        creds = (res or {}).get("credentials", {})
        if not creds.get("sessionId"):
            return {"status": "error", "error": "no sessionId in Authenticate response"}

        _session_state.update({
            "session_id": creds["sessionId"],
            "user_name": creds.get("userName"),
            "database": creds.get("database"),
            "endpoint": cfg.get("endpoint") or DEFAULT_ENDPOINT,
            "obtained_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        })
    return _ok_state()


def _ok_state() -> dict:
    return {
        "status": "ok",
        "session_id": _session_state["session_id"],
        "user_name": _session_state["user_name"],
        "database": _session_state["database"],
        "endpoint": _session_state["endpoint"],
        "obtained_at": _session_state["obtained_at"],
    }


def _credentials() -> dict:
    """Build the Geotab credentials envelope for non-Authenticate calls.
    Lazily authenticates on first use.
    """
    if not _session_state.get("session_id"):
        auth = authenticate()
        if auth.get("status") != "ok":
            raise RuntimeError(f"Geotab auth required: {auth}")
    return {
        "userName": _session_state["user_name"],
        "database": _session_state["database"],
        "sessionId": _session_state["session_id"],
    }


# --------------------------------------------------------------------------- #
# Domain calls
# --------------------------------------------------------------------------- #


def list_devices() -> list[dict]:
    """All devices visible to this user. Returns canonical dicts:
        {device_id, name, license_plate, vin, serial_number}
    """
    if not is_configured():
        return []
    res = _post("Get", {
        "typeName": "Device",
        "credentials": _credentials(),
    })
    out = []
    for d in res or []:
        out.append({
            "device_id": d.get("id"),
            "name": d.get("name"),
            "license_plate": d.get("licensePlate"),
            "vin": d.get("vehicleIdentificationNumber"),
            "serial_number": d.get("serialNumber"),
            "comment": d.get("comment"),
        })
    return out


def get_vehicle_positions(
    *,
    at_time: _dt.datetime,
    vehicle_ids: Optional[list[str]] = None,
    window_seconds: int = 300,
) -> list[dict]:
    """Pull LogRecord (position pings) for the given vehicles within
    [at_time - window, at_time + window].

    If vehicle_ids is None, pulls for every device in the account — only
    use that for small fleets.

    Returns list of dicts:
        {device_id, when_iso, lat, lon, speed_kph}
    """
    if not is_configured():
        return []
    if at_time.tzinfo is None:
        at_time = at_time.replace(tzinfo=_dt.timezone.utc)

    half = _dt.timedelta(seconds=window_seconds)
    from_iso = (at_time - half).astimezone(_dt.timezone.utc).isoformat()
    to_iso = (at_time + half).astimezone(_dt.timezone.utc).isoformat()

    search: dict = {
        "fromDate": from_iso,
        "toDate": to_iso,
    }
    if vehicle_ids:
        search["deviceSearch"] = {"id": vehicle_ids[0]} if len(vehicle_ids) == 1 else None
        # Geotab supports either a single deviceSearch or per-device fan-out.
        # For the common single-device case we pass deviceSearch directly.
        if not search["deviceSearch"]:
            del search["deviceSearch"]

    out: list[dict] = []
    for vid in (vehicle_ids or [None]):
        params = {
            "typeName": "LogRecord",
            "credentials": _credentials(),
            "search": dict(search, deviceSearch={"id": vid} if vid else None),
        }
        # Drop deviceSearch=None for the "all devices" case.
        if params["search"].get("deviceSearch") is None:
            params["search"].pop("deviceSearch", None)
        try:
            res = _post("Get", params)
        except Exception as e:
            _log.warning("Geotab LogRecord Get failed: %s", e)
            continue
        for r in res or []:
            out.append({
                "device_id": (r.get("device") or {}).get("id"),
                "when_iso": r.get("dateTime"),
                "lat": r.get("latitude"),
                "lon": r.get("longitude"),
                "speed_kph": r.get("speed"),
            })
    return out


def lookup_position_by_driver(
    driver_email: str,
    *,
    at_time: _dt.datetime,
    window_seconds: int = 300,
) -> Optional[dict]:
    """Resolve driver_email → device, then position at (or near) at_time.

    Driver→device mapping is derived from `config.geotab.driver_devices`
    (a dict of {email: device_id}). When that's empty, returns None and
    the caller falls through to the next AP-5 tiebreaker.
    """
    if not is_configured():
        return None
    cfg = _cfg()
    mapping = (cfg.get("driver_devices") or {})
    device_id = mapping.get(driver_email)
    if not device_id:
        return None
    pings = get_vehicle_positions(
        at_time=at_time, vehicle_ids=[device_id],
        window_seconds=window_seconds,
    )
    if not pings:
        return None
    # Closest ping to at_time wins.
    def _delta(p: dict) -> float:
        try:
            t = _dt.datetime.fromisoformat((p.get("when_iso") or "").replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=_dt.timezone.utc)
            return abs((t - at_time).total_seconds())
        except (TypeError, ValueError):
            return float("inf")
    pings.sort(key=_delta)
    closest = pings[0]
    closest["driver_email"] = driver_email
    return closest
