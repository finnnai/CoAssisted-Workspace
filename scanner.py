# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Background scanner — generic 'check every N hours, fire trigger' primitive.

The MCP server is request/response, so 'background' here means: an external
scheduler (cron, launchd, GitHub Actions) calls workflow_run_scanner periodically,
and the scanner runs all checks that are 'due' based on their cadence.

API:
    register_check(name, cadence_hours, fn, channel="json")
        Register a callable. fn() returns a list[dict] of fired alerts.

    run_due() -> dict
        Run every check whose cadence has elapsed since its last run.
        Returns a summary { ran: [...], skipped: [...], total_alerts: int }.

    run_one(name) -> dict
        Force-run one check by name regardless of cadence.

    list_checks() -> list[dict]
        Inspect registered checks + their last_run / last_alert_count.

State is persisted to scan_state.json (atomic write) so cadence + last-fire
data survives MCP restart.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


_STATE_PATH = Path(__file__).resolve().parent / "scan_state.json"


# --------------------------------------------------------------------------- #
# Data classes
# --------------------------------------------------------------------------- #


@dataclass
class CheckSpec:
    """Definition of a registered scanner check."""
    name: str
    cadence_hours: float
    fn: Callable[[], list[dict]]
    channel: str = "json"          # "json" | "chat" | "email" | "log"
    description: str = ""


@dataclass
class CheckRun:
    """Result of running one check."""
    name: str
    ran_at: str
    alert_count: int
    alerts: list[dict] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ran_at": self.ran_at,
            "alert_count": self.alert_count,
            "alerts": list(self.alerts),
            "error": self.error,
        }


# --------------------------------------------------------------------------- #
# Registry — module-global
# --------------------------------------------------------------------------- #


_REGISTRY: dict[str, CheckSpec] = {}


def register_check(
    name: str,
    cadence_hours: float,
    fn: Callable[[], list[dict]],
    channel: str = "json",
    description: str = "",
) -> None:
    """Register a check in the global registry.

    Calling register_check with an existing name overwrites the spec.
    Idempotent — modules can call this at import time.
    """
    if not name:
        raise ValueError("Check name required.")
    if cadence_hours <= 0:
        raise ValueError("cadence_hours must be > 0.")
    _REGISTRY[name] = CheckSpec(
        name=name, cadence_hours=cadence_hours,
        fn=fn, channel=channel, description=description,
    )


def unregister_check(name: str) -> bool:
    return _REGISTRY.pop(name, None) is not None


def list_checks() -> list[dict]:
    """List all registered checks with their last-run state."""
    state = _load_state()
    out: list[dict] = []
    for spec in _REGISTRY.values():
        last = state.get(spec.name, {})
        out.append({
            "name": spec.name,
            "cadence_hours": spec.cadence_hours,
            "channel": spec.channel,
            "description": spec.description,
            "last_run": last.get("last_run"),
            "last_alert_count": last.get("last_alert_count"),
            "next_due": _next_due(spec, last.get("last_run")),
        })
    return out


# --------------------------------------------------------------------------- #
# State persistence — atomic-write JSON sidecar
# --------------------------------------------------------------------------- #


def _load_state() -> dict[str, dict]:
    if not _STATE_PATH.exists():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict[str, dict]) -> None:
    """Atomic write — write to .tmp then rename. Same pattern as vendor_followups."""
    fd, tmp = tempfile.mkstemp(
        prefix="scan_state.", suffix=".tmp", dir=str(_STATE_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, _STATE_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat()


def _next_due(spec: CheckSpec, last_run: str | None) -> str | None:
    if not last_run:
        return None
    try:
        last_dt = _dt.datetime.fromisoformat(last_run)
    except ValueError:
        return None
    return (last_dt + _dt.timedelta(hours=spec.cadence_hours)).isoformat()


def _is_due(spec: CheckSpec, last_run: str | None,
            now: _dt.datetime | None = None) -> bool:
    if not last_run:
        return True
    try:
        last_dt = _dt.datetime.fromisoformat(last_run)
    except ValueError:
        return True
    now = now or _dt.datetime.now().astimezone()
    elapsed_hours = (now - last_dt).total_seconds() / 3600.0
    return elapsed_hours >= spec.cadence_hours


# --------------------------------------------------------------------------- #
# Run helpers
# --------------------------------------------------------------------------- #


def run_one(name: str) -> CheckRun:
    """Force-run one check regardless of cadence."""
    spec = _REGISTRY.get(name)
    if not spec:
        return CheckRun(
            name=name, ran_at=_now_iso(), alert_count=0,
            error=f"unknown check: {name}",
        )

    ran_at = _now_iso()
    try:
        alerts = spec.fn() or []
        alerts_clean = [dict(a) for a in alerts]
        run = CheckRun(
            name=name, ran_at=ran_at,
            alert_count=len(alerts_clean), alerts=alerts_clean,
        )
    except Exception as e:
        run = CheckRun(
            name=name, ran_at=ran_at, alert_count=0, error=str(e),
        )

    state = _load_state()
    state[name] = {
        "last_run": ran_at,
        "last_alert_count": run.alert_count,
        "last_error": run.error,
    }
    _save_state(state)
    return run


def run_due() -> dict:
    """Run every check whose cadence has elapsed.

    Returns:
        {
            "ran": [CheckRun.to_dict(), ...],
            "skipped": [{"name", "reason"}, ...],
            "total_alerts": int,
            "ran_at": iso timestamp,
        }
    """
    state = _load_state()
    now = _dt.datetime.now().astimezone()
    ran: list[dict] = []
    skipped: list[dict] = []
    total_alerts = 0

    for spec in list(_REGISTRY.values()):
        last = state.get(spec.name, {}).get("last_run")
        if not _is_due(spec, last, now=now):
            skipped.append({
                "name": spec.name,
                "reason": "not yet due",
                "last_run": last,
                "next_due": _next_due(spec, last),
            })
            continue
        result = run_one(spec.name)
        ran.append(result.to_dict())
        total_alerts += result.alert_count

    return {
        "ran": ran,
        "skipped": skipped,
        "total_alerts": total_alerts,
        "ran_at": _now_iso(),
    }


# --------------------------------------------------------------------------- #
# Test helpers
# --------------------------------------------------------------------------- #


def _override_state_path_for_tests(p: Path) -> None:
    global _STATE_PATH
    _STATE_PATH = p


def _reset_registry_for_tests() -> None:
    _REGISTRY.clear()
