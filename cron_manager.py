# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""Cron schedule manager (v0.9.3).

The canonical crontab template (`scripts/cron/crontab_template.txt`) was the
source of truth through v0.9.2. v0.9.3 promotes a structured JSON store
(`cron_jobs.json`) to the source of truth so the operator can toggle / edit
/ add / remove jobs without hand-editing a template.

The installer (`scripts/cron/install_crontab.py`) reads from this JSON when
present and falls back to the legacy template when it's not (first-run
bootstrap path).

Each job record:
    id              short slug, unique. Derived from the entrypoint
                    filename when bootstrapped, or supplied by the
                    operator when added manually.
    name            human-readable label.
    description     one-line explanation.
    schedule        standard 5-field cron expression.
    command         shell command. May contain `$HOME` and `$VENV_PYTHON`
                    placeholders; the installer substitutes them.
    enabled         bool. Disabled jobs render as commented-out lines in
                    the crontab — kept for visibility but not active.
    category        optional grouping ('AP/AR', 'CRM', 'receipts', etc.).
    managed         True for CoAssisted-managed jobs (default). The
                    installer never touches non-managed entries already
                    in the operator's crontab.
    created_at      ISO timestamp.
    updated_at      ISO timestamp.

Public surface
--------------
    load() -> dict
    save(data) -> None
    list_jobs(*, enabled_only=False) -> list[dict]
    get(job_id) -> dict | None
    toggle(job_id, enabled=None) -> dict
    update_schedule(job_id, schedule) -> dict
    update_command(job_id, command) -> dict
    add_job(...) -> dict
    remove_job(job_id) -> bool
    validate_schedule(expr) -> tuple[bool, str]
    render_crontab(*, include_disabled=False) -> str
    install_crontab() -> dict
    bootstrap_from_template(template_path=None) -> dict
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


_PROJECT_ROOT = Path(__file__).resolve().parent
_JOBS_PATH = _PROJECT_ROOT / "cron_jobs.json"
_TEMPLATE_PATH = _PROJECT_ROOT / "scripts" / "cron" / "crontab_template.txt"
_VENV_PYTHON = _PROJECT_ROOT / ".venv" / "bin" / "python"

# Marker comment that delimits CoAssisted-managed entries inside the live
# crontab. Anything between the begin/end markers gets rewritten by
# install_crontab(); everything else is preserved verbatim.
_BEGIN_MARKER = "# >>> coassisted-workspace-managed (do not edit between markers) >>>"
_END_MARKER = "# <<< coassisted-workspace-managed <<<"


# --------------------------------------------------------------------------- #
# Schedule validation
# --------------------------------------------------------------------------- #


_FIELD_RE = re.compile(r"^[\d\*\,\-\/]+$")


def validate_schedule(expr: str) -> tuple[bool, str]:
    """Validate a 5-field cron expression. Returns (ok, reason)."""
    if not expr or not isinstance(expr, str):
        return False, "schedule is required"
    parts = expr.strip().split()
    if len(parts) != 5:
        return False, f"expected 5 fields (min hour dom mon dow), got {len(parts)}"
    field_names = ("minute", "hour", "day-of-month", "month", "day-of-week")
    for name, val in zip(field_names, parts):
        if not _FIELD_RE.match(val):
            return False, f"{name} field {val!r} contains invalid characters"
    # Bonus: try croniter for deeper validation if available.
    try:
        from croniter import croniter  # type: ignore
        croniter(expr, _dt.datetime.now())
    except ImportError:
        pass
    except Exception as e:
        return False, f"croniter rejected expression: {e}"
    return True, "ok"


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


def load() -> dict:
    """Read the JSON store. Auto-bootstraps from the template on first call."""
    if not _JOBS_PATH.exists():
        bootstrap_from_template()
    try:
        return json.loads(_JOBS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"jobs": {}, "meta": {}}


def save(data: dict) -> None:
    data.setdefault("meta", {})["last_saved"] = _now_iso()
    _atomic_write(_JOBS_PATH, data)


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,40}$")


def _normalize_id(job_id: str) -> str:
    return (job_id or "").strip().lower().replace(" ", "_")


def _validate_id(job_id: str) -> tuple[bool, str]:
    if not job_id:
        return False, "id is required"
    if not _ID_RE.match(job_id):
        return False, (
            "id must be 2-40 chars, lowercase letters/digits/underscore/dash, "
            "starting with a letter or digit"
        )
    return True, "ok"


def list_jobs(*, enabled_only: bool = False) -> list[dict]:
    data = load()
    jobs = list((data.get("jobs") or {}).values())
    if enabled_only:
        jobs = [j for j in jobs if j.get("enabled", True)]
    # Sort by schedule's first field (minute) then second (hour) for stable
    # display order; jobs without a parseable schedule sink to the bottom.
    def _key(j):
        try:
            parts = (j.get("schedule") or "").split()
            return (int(parts[1]) if parts[1].isdigit() else 99,
                    int(parts[0]) if parts[0].isdigit() else 99,
                    j.get("id", ""))
        except (IndexError, ValueError):
            return (99, 99, j.get("id", ""))
    jobs.sort(key=_key)
    return jobs


def get(job_id: str) -> Optional[dict]:
    if not job_id:
        return None
    return (load().get("jobs") or {}).get(_normalize_id(job_id))


def toggle(job_id: str, enabled: Optional[bool] = None) -> dict:
    """Enable / disable a job. If enabled is None, flips the current state."""
    key = _normalize_id(job_id)
    data = load()
    job = (data.get("jobs") or {}).get(key)
    if not job:
        return {"status": "not_found", "job_id": key}
    new_state = (not job.get("enabled", True)) if enabled is None else bool(enabled)
    job["enabled"] = new_state
    job["updated_at"] = _now_iso()
    data["jobs"][key] = job
    save(data)
    return {"status": "ok", "job": job}


def update_schedule(job_id: str, schedule: str) -> dict:
    ok, reason = validate_schedule(schedule)
    if not ok:
        return {"status": "invalid_schedule", "reason": reason}
    key = _normalize_id(job_id)
    data = load()
    job = (data.get("jobs") or {}).get(key)
    if not job:
        return {"status": "not_found", "job_id": key}
    job["schedule"] = schedule.strip()
    job["updated_at"] = _now_iso()
    data["jobs"][key] = job
    save(data)
    return {"status": "ok", "job": job}


def update_command(job_id: str, command: str) -> dict:
    if not command or not command.strip():
        return {"status": "invalid_command", "reason": "command is required"}
    key = _normalize_id(job_id)
    data = load()
    job = (data.get("jobs") or {}).get(key)
    if not job:
        return {"status": "not_found", "job_id": key}
    job["command"] = command.strip()
    job["updated_at"] = _now_iso()
    data["jobs"][key] = job
    save(data)
    return {"status": "ok", "job": job}


def add_job(
    *,
    id: str,  # noqa: A002 — domain name
    name: str,
    schedule: str,
    command: str,
    description: str = "",
    category: Optional[str] = None,
    enabled: bool = True,
) -> dict:
    """Add a new CoAssisted-managed job. id must be unique."""
    ok, reason = _validate_id(_normalize_id(id))
    if not ok:
        return {"status": "invalid_id", "reason": reason}
    ok, reason = validate_schedule(schedule)
    if not ok:
        return {"status": "invalid_schedule", "reason": reason}
    if not command or not command.strip():
        return {"status": "invalid_command", "reason": "command is required"}
    key = _normalize_id(id)
    data = load()
    if key in (data.get("jobs") or {}):
        return {"status": "duplicate", "job_id": key}
    now = _now_iso()
    job = {
        "id": key,
        "name": name.strip() or key,
        "description": (description or "").strip(),
        "schedule": schedule.strip(),
        "command": command.strip(),
        "enabled": bool(enabled),
        "category": (category or "Custom").strip() or "Custom",
        "managed": True,
        "created_at": now,
        "updated_at": now,
    }
    data.setdefault("jobs", {})[key] = job
    save(data)
    return {"status": "ok", "job": job}


def remove_job(job_id: str) -> bool:
    key = _normalize_id(job_id)
    data = load()
    if key not in (data.get("jobs") or {}):
        return False
    del data["jobs"][key]
    save(data)
    return True


# --------------------------------------------------------------------------- #
# Render + install
# --------------------------------------------------------------------------- #


def _substitute(line: str) -> str:
    """Substitute $HOME and $VENV_PYTHON. Mirrors install_crontab.py's
    behavior so the JSON path produces identical output to the template
    path it replaces.
    """
    home = os.environ.get("HOME") or str(Path.home())
    return (
        line.replace("$VENV_PYTHON", str(_VENV_PYTHON))
        .replace("$HOME", home)
    )


def render_crontab(*, include_disabled: bool = False) -> str:
    """Produce the full materialized crontab text for the managed block.

    Disabled jobs render as commented lines (so the operator can see them
    in the live crontab and re-enable). Includes the begin/end markers so
    install_crontab() can rewrite this block in-place without disturbing
    the operator's personal entries.
    """
    jobs = list_jobs(enabled_only=False)
    lines = [
        _BEGIN_MARKER,
        f"# Generated by cron_manager.render_crontab at {_now_iso()}",
        f"# Source of truth: {_JOBS_PATH}",
        "# minute hour dom mon dow  command",
    ]
    for j in jobs:
        if not j.get("enabled", True) and not include_disabled:
            lines.append(
                f"# DISABLED [{j.get('id')}] {j.get('schedule')}  "
                f"{_substitute(j.get('command', ''))}"
            )
            continue
        lines.append(f"{j.get('schedule')}  {_substitute(j.get('command', ''))}")
    lines.append(_END_MARKER)
    return "\n".join(lines) + "\n"


def _read_live_crontab() -> str:
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            return result.stdout
        # No crontab installed yet — that's fine.
        return ""
    except FileNotFoundError:
        # crontab binary not installed (e.g. in CI); treat as empty.
        return ""


def _splice_managed_block(existing: str, managed: str) -> str:
    """Replace the managed block in `existing` with `managed`. If no
    existing markers are present, append the managed block to whatever
    personal entries exist.
    """
    if _BEGIN_MARKER in existing and _END_MARKER in existing:
        before = existing.split(_BEGIN_MARKER, 1)[0].rstrip("\n")
        after = existing.split(_END_MARKER, 1)[1].lstrip("\n")
        chunks = [c for c in (before, managed.strip(), after) if c.strip()]
        return "\n\n".join(chunks).rstrip("\n") + "\n"
    # No markers yet — preserve any existing entries (operator's personal
    # cron lines), append our managed block.
    if existing.strip():
        return existing.rstrip("\n") + "\n\n" + managed
    return managed


def install_crontab() -> dict:
    """Install the rendered crontab via `crontab -`. Returns a summary."""
    rendered_managed = render_crontab()
    existing = _read_live_crontab()
    new = _splice_managed_block(existing, rendered_managed)

    # Write to a temp file, then pipe via `crontab <file>` (more reliable
    # than streaming on stdin under some shells).
    fd, tmp_path = tempfile.mkstemp(prefix="crontab.", suffix=".txt")
    os.close(fd)
    try:
        Path(tmp_path).write_text(new, encoding="utf-8")
        result = subprocess.run(
            ["crontab", tmp_path], capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return {
                "status": "error",
                "error": f"crontab exit {result.returncode}: "
                         f"{result.stderr or result.stdout}",
                "rendered": rendered_managed,
            }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    enabled = sum(1 for j in list_jobs() if j.get("enabled", True))
    total = len(list_jobs())
    return {
        "status": "ok",
        "enabled_jobs": enabled,
        "total_jobs": total,
        "disabled_jobs": total - enabled,
        "managed_block": rendered_managed,
    }


# --------------------------------------------------------------------------- #
# Bootstrap from existing template
# --------------------------------------------------------------------------- #


def bootstrap_from_template(template_path: Optional[Path] = None) -> dict:
    """First-run path: parse the legacy template, populate cron_jobs.json.

    Idempotent — calling again on a populated store is a no-op (returns
    existing state).
    """
    if _JOBS_PATH.exists():
        try:
            existing = json.loads(_JOBS_PATH.read_text(encoding="utf-8"))
            if existing.get("jobs"):
                return existing
        except (OSError, json.JSONDecodeError):
            pass

    template_path = template_path or _TEMPLATE_PATH
    if not template_path.exists():
        # Fresh-install path with no legacy template — start empty.
        empty = {"jobs": {}, "meta": {"bootstrapped_at": _now_iso(),
                                      "source": "empty"}}
        _atomic_write(_JOBS_PATH, empty)
        return empty

    raw = template_path.read_text(encoding="utf-8")
    jobs: dict = {}
    for line in raw.splitlines():
        s = line.rstrip()
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        parts = s.split(None, 5)
        if len(parts) < 6:
            continue
        schedule = " ".join(parts[:5])
        command = parts[5]
        # Derive a stable id from the .py entrypoint name in the command.
        m = re.search(r"(\w+)\.py", command)
        base_id = (m.group(1) if m else "job").lower()
        # Suffix a counter if duplicates (e.g. two receipt sweep entries).
        candidate = base_id
        n = 2
        while candidate in jobs:
            candidate = f"{base_id}_{n}"
            n += 1
        # Friendly category guess.
        if any(k in command for k in ("staffwizard", "labor")):
            category = "AP/AR — labor"
        elif any(k in command for k in ("receipts", "ap_", "invoice", "baseline")):
            category = "AP/AR"
        elif any(k in command for k in ("vendor", "scanner")):
            category = "Vendors"
        elif any(k in command for k in ("crm", "stats", "enrich")):
            category = "CRM"
        elif any(k in command for k in ("brief", "executive")):
            category = "Briefings"
        else:
            category = "Other"

        now = _now_iso()
        jobs[candidate] = {
            "id": candidate,
            "name": candidate.replace("_", " ").title(),
            "description": f"Imported from crontab_template.txt — {candidate}",
            "schedule": schedule,
            "command": command,
            "enabled": True,
            "category": category,
            "managed": True,
            "created_at": now,
            "updated_at": now,
        }

    payload = {
        "jobs": jobs,
        "meta": {
            "bootstrapped_at": _now_iso(),
            "source": str(template_path),
            "source_count": len(jobs),
        },
    }
    _atomic_write(_JOBS_PATH, payload)
    return payload


# --------------------------------------------------------------------------- #
# Stats / inspection
# --------------------------------------------------------------------------- #


def stats() -> dict:
    data = load()
    jobs = list(data.get("jobs", {}).values())
    enabled = sum(1 for j in jobs if j.get("enabled", True))
    by_category: dict[str, int] = {}
    for j in jobs:
        cat = j.get("category", "Other") or "Other"
        by_category[cat] = by_category.get(cat, 0) + 1
    return {
        "total_jobs": len(jobs),
        "enabled_jobs": enabled,
        "disabled_jobs": len(jobs) - enabled,
        "categories": by_category,
        "store_path": str(_JOBS_PATH),
        "last_saved": (data.get("meta") or {}).get("last_saved"),
        "bootstrapped_from": (data.get("meta") or {}).get("source"),
    }
