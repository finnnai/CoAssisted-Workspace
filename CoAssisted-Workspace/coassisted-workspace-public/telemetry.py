# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE.
"""Telemetry — sanitized health-check reports for software improvement.

Used by `system_doctor`: when any check returns warn/fail, we build a
*sanitized* report (emails, API keys, file paths, IPs all replaced with
`<PLACEHOLDER>`) and save it to `logs/health_reports/<timestamp>.json`.

Default behavior is **never auto-send**. The user opts in by either:
  - Setting `telemetry_email` in `config.json` and running `system_share_health_report`
  - Manually emailing the local file

This module ONLY produces the report. Sending is in `tools/system.py`.
"""

from __future__ import annotations

import datetime as _dt
import json
import platform
import re
import sys
from pathlib import Path
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Sanitization
# --------------------------------------------------------------------------- #

# Patterns conservative enough to minimize false positives. Order matters —
# we run more-specific patterns first (API keys before generic email-shaped
# tokens, etc.).
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Anthropic API keys: sk-ant-api03-...
    (re.compile(r"sk-ant-api\d+-[A-Za-z0-9_\-]{40,}"), "<anthropic_key>"),
    # Google API keys: AIza... (39 chars total)
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), "<google_api_key>"),
    # OAuth refresh tokens: 1//... (Google's format)
    (re.compile(r"\b1//[0-9A-Za-z_\-]{20,}\b"), "<oauth_refresh_token>"),
    # Email addresses
    (
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "<email>",
    ),
    # Absolute file paths under /Users/<name>/...
    (re.compile(r"/Users/[A-Za-z0-9_\-]+"), "/Users/<USER>"),
    # IPv4 addresses
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    # GCP project IDs of the form name-NNNNNN — common pattern
    (re.compile(r"\b[a-z][a-z0-9\-]{4,28}-[0-9]{4,8}\b"), "<gcp_project>"),
]


def sanitize_string(s: str) -> str:
    """Apply every regex replacement in order. Returns the redacted string."""
    if not isinstance(s, str):
        return s
    for pat, repl in _PATTERNS:
        s = pat.sub(repl, s)
    return s


def sanitize(obj: Any) -> Any:
    """Recursively sanitize all string leaves of a JSON-shaped structure."""
    if isinstance(obj, str):
        return sanitize_string(obj)
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(sanitize(v) for v in obj)
    # Numbers, bools, None — leave alone.
    return obj


# --------------------------------------------------------------------------- #
# Environment metadata
# --------------------------------------------------------------------------- #


def gather_environment() -> dict:
    """Collect non-PII environment info to ship with reports."""
    import config as _cfg
    env: dict = {
        "os_system": platform.system(),
        "os_release": platform.release(),
        "os_machine": platform.machine(),
        "python_version": (
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
    }
    # Optional-integration boolean flags (no values, just presence).
    try:
        env["maps_configured"] = bool(_cfg.get("google_maps_api_key"))
    except Exception:
        env["maps_configured"] = False
    try:
        env["anthropic_configured"] = bool(_cfg.get("anthropic_api_key"))
    except Exception:
        env["anthropic_configured"] = False
    try:
        env["home_address_set"] = bool(_cfg.get("home_address"))
    except Exception:
        env["home_address_set"] = False
    # corelocationcli installed?
    import shutil
    cl_present = bool(
        shutil.which("CoreLocationCLI")
        or Path("/opt/homebrew/bin/CoreLocationCLI").exists()
        or Path("/usr/local/bin/CoreLocationCLI").exists()
    )
    env["corelocationcli_present"] = cl_present
    # macOS version (more useful than uname)
    if platform.system() == "Darwin":
        try:
            import subprocess
            sw = subprocess.run(
                ["sw_vers", "-productVersion"],
                capture_output=True, text=True, timeout=2,
            )
            if sw.returncode == 0:
                env["macos_version"] = sw.stdout.strip()
        except Exception:
            pass
    return env


def gather_recent_actions(limit: int = 20) -> list[dict]:
    """Last N records from recent_actions.jsonl, sanitized.

    Used in reports so the developer can see what the user was doing when
    things went sideways (timestamps + tool names) — but with all PII redacted.
    """
    try:
        import recent_actions
        records = recent_actions.list_recent(limit=limit)
    except Exception:
        return []
    # Reduce to the lightweight fields — drop snapshots which are PII-heavy
    # even after sanitization.
    light: list[dict] = []
    for r in records:
        light.append({
            "timestamp": r.get("timestamp"),
            "tool": r.get("tool"),
            "action": r.get("action"),
            "target_kind": r.get("target_kind"),
            # target_id and summary go through sanitize() since they often
            # contain emails / resource_names like 'people/c123'.
            "target_id": sanitize_string(r.get("target_id") or ""),
            "summary": sanitize_string(r.get("summary") or ""),
            "reverted": r.get("reverted", False),
        })
    return light


# --------------------------------------------------------------------------- #
# Report assembly + storage
# --------------------------------------------------------------------------- #


def build_report(
    doctor_results: list[dict],
    *, include_recent_actions: bool = True,
) -> dict:
    """Assemble a sanitized health report.

    `doctor_results` is the `checks` list from system_doctor. We keep failed +
    warned entries (passes are uninteresting) and sanitize each.
    """
    interesting = [
        c for c in doctor_results
        if c.get("status") in ("warn", "fail")
    ]
    return {
        "report_version": 1,
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": {
            "passed": sum(1 for c in doctor_results if c.get("status") == "pass"),
            "warned": sum(1 for c in doctor_results if c.get("status") == "warn"),
            "failed": sum(1 for c in doctor_results if c.get("status") == "fail"),
        },
        "checks_with_issues": sanitize(interesting),
        "environment": gather_environment(),
        "recent_actions": gather_recent_actions(20) if include_recent_actions else [],
    }


def save_report(report: dict) -> Path:
    """Write the report to logs/health_reports/<timestamp>.json. Returns the path."""
    project_dir = Path(__file__).resolve().parent
    out_dir = project_dir / "logs" / "health_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    path = out_dir / f"health_report_{ts}.json"
    path.write_text(json.dumps(report, indent=2))
    return path


def find_latest_report() -> Optional[Path]:
    """Return the most-recent saved report, or None if none exist."""
    project_dir = Path(__file__).resolve().parent
    out_dir = project_dir / "logs" / "health_reports"
    if not out_dir.exists():
        return None
    candidates = sorted(out_dir.glob("health_report_*.json"))
    return candidates[-1] if candidates else None
