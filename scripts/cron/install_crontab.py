#!/usr/bin/env python3
# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Timing-aware crontab installer — Finnn 2026-05-01 patch.

Reads `dist/install_crontab_template.txt`, substitutes `$HOME` and
`$VENV_PYTHON`, then for each entry:

  1. Computes next-fire and most-recent-past-fire timestamps via
     croniter.
  2. Prints a table of all entries with their next-fire times.
  3. For any entry whose **most recent** scheduled time was today
     (within the last 24 hours, before now), prompts the operator
     to backfill (run the entrypoint inline). Per Joshua's
     2026-05-01 question-1 answer: **default Y** — operator opts
     out with `--no-backfill`.
  4. Refuses to install if the existing crontab differs from the
     canonical layout, unless `--force` is passed.
  5. After installation, runs `system_check_cron` and prints the
     result.

Usage:
  python3 scripts/cron/install_crontab.py
  python3 scripts/cron/install_crontab.py --no-backfill
  python3 scripts/cron/install_crontab.py --force      # overwrite anyway
  python3 scripts/cron/install_crontab.py --dry-run    # preview only
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE_PATH = Path(__file__).resolve().parent / "crontab_template.txt"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
JOBS_JSON_PATH = PROJECT_ROOT / "cron_jobs.json"


# =============================================================================
# Canonical schedule loading + substitution
#
# v0.9.3+ — cron_jobs.json is the source of truth. The legacy
# crontab_template.txt is the bootstrap source on first run (cron_manager
# materializes it into cron_jobs.json) and stays as documentation.
# =============================================================================


def _substitute(line: str) -> str:
    """Substitute $HOME and $VENV_PYTHON. The legacy template uses $HOME
    pointing at the project root; we mirror that here so log paths sit
    alongside the project.
    """
    repo_root = str(PROJECT_ROOT)
    return (
        line.replace("$VENV_PYTHON", str(VENV_PYTHON))
        .replace("$HOME", repo_root)
    )


def load_template() -> list[str]:
    """Return the canonical crontab as a list of substituted lines.

    Reads from cron_jobs.json (v0.9.3+) when present; falls back to
    crontab_template.txt for first-run bootstrap.
    """
    # v0.9.3+ path: cron_jobs.json.
    if JOBS_JSON_PATH.exists():
        try:
            # Lazy import keeps install_crontab.py runnable as a standalone
            # script even before cron_manager.py is sync'd.
            sys.path.insert(0, str(PROJECT_ROOT))
            import cron_manager  # type: ignore
            jobs = cron_manager.list_jobs(enabled_only=True)
            out: list[str] = []
            for j in jobs:
                schedule = (j.get("schedule") or "").strip()
                command = _substitute((j.get("command") or "").strip())
                if not schedule or not command:
                    continue
                out.append(f"{schedule} {command}")
            if out:
                return out
            # Empty enabled set — fall through to template (defensive).
        except Exception as e:
            print(f"  warn: failed to load cron_jobs.json: {e}; "
                  "falling back to template", file=sys.stderr)

    # Bootstrap path: legacy template.
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(
            f"Neither {JOBS_JSON_PATH} nor {TEMPLATE_PATH} exist. "
            "Run cron_manager.bootstrap_from_template() to seed the JSON store."
        )

    out: list[str] = []
    raw = TEMPLATE_PATH.read_text(encoding="utf-8")
    for line in raw.splitlines():
        s = line.rstrip()
        if not s.strip() or s.lstrip().startswith("#"):
            continue
        out.append(_substitute(s))
    return out


# =============================================================================
# Cron parse + next-fire math
# =============================================================================

def parse_entry(line: str) -> dict | None:
    """Split a cron line into ``{schedule, command, log_path, label}``.

    Label is derived from the entrypoint Python file name in the
    command (e.g. ``refresh_stats.py`` → ``refresh_stats``) so the
    table is readable. Returns None if the line is malformed.
    """
    parts = line.split(None, 5)
    if len(parts) < 6:
        return None
    schedule = " ".join(parts[:5])
    command = parts[5]
    log_match = re.search(r">>\s*(\S+)", command)
    log_path = log_match.group(1) if log_match else None
    # Pull label from the .py filename in the command.
    py_match = re.search(r"(\w+)\.py", command)
    label = py_match.group(1) if py_match else "(unknown)"
    return {
        "schedule": schedule,
        "command": command,
        "log_path": log_path,
        "label": label,
    }


def next_fire(schedule: str, *, base: _dt.datetime | None = None) -> _dt.datetime | None:
    """Compute next-fire timestamp via croniter, or None if unavailable."""
    base = base or _dt.datetime.now()
    try:
        from croniter import croniter
        return croniter(schedule, base).get_next(_dt.datetime)
    except ImportError:
        return None
    except Exception:
        return None


def prev_fire(schedule: str, *, base: _dt.datetime | None = None) -> _dt.datetime | None:
    """Compute most-recent past-fire (excluding `base` itself)."""
    base = base or _dt.datetime.now()
    try:
        from croniter import croniter
        return croniter(schedule, base).get_prev(_dt.datetime)
    except ImportError:
        return None
    except Exception:
        return None


def humanize_delta(td: _dt.timedelta) -> str:
    """Render a timedelta as a short human-readable string.

    Examples: ``"4h 23m"``, ``"2d 6h"``, ``"in 12s"``.
    """
    secs = int(td.total_seconds())
    if secs < 0:
        return f"{humanize_delta(-td)} ago"
    if secs < 60:
        return f"{secs}s"
    mins, secs = divmod(secs, 60)
    if mins < 60:
        return f"{mins}m"
    hrs, mins = divmod(mins, 60)
    if hrs < 24:
        return f"{hrs}h {mins}m"
    days, hrs = divmod(hrs, 24)
    return f"{days}d {hrs}h"


# =============================================================================
# Existing crontab inspection
# =============================================================================

def read_existing_crontab() -> list[str]:
    """Return the existing crontab as a list of non-comment lines."""
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return [
        ln.rstrip()
        for ln in result.stdout.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def diff_against_canonical(
    existing: list[str], canonical: list[str]
) -> tuple[list[str], list[str]]:
    """Return (entries-to-add, entries-to-remove) when reconciling.

    Existing lines that aren't in the canonical set are kept (operator
    may have personal cron entries — preserving them is the polite
    behavior). Only CoAssisted-managed entries are touched. We
    identify those by checking whether the path in the command points
    at the project root.
    """
    repo_root = str(PROJECT_ROOT)
    canonical_set = set(canonical)
    existing_managed = [
        ln for ln in existing
        if repo_root in ln
    ]
    existing_unmanaged = [
        ln for ln in existing
        if repo_root not in ln
    ]
    to_add = [ln for ln in canonical if ln not in canonical_set or ln not in existing_managed]
    to_remove = [ln for ln in existing_managed if ln not in canonical_set]
    return to_add, to_remove, existing_unmanaged


# =============================================================================
# Backfill — run the entrypoint inline for entries past their last fire
# =============================================================================

def run_backfill(entry: dict) -> tuple[bool, str]:
    """Execute an entry's command inline. Returns (succeeded, output)."""
    print(f"  ▶ Backfilling: {entry['label']} ...", flush=True)
    try:
        result = subprocess.run(
            ["bash", "-c", entry["command"]],
            capture_output=True, text=True, timeout=300,
        )
        ok = result.returncode == 0
        out = (result.stdout or "") + (result.stderr or "")
        return ok, out[-500:] if out else ""
    except subprocess.TimeoutExpired:
        return False, "timed out after 5 minutes"
    except Exception as e:
        return False, str(e)


# =============================================================================
# Operator prompts
# =============================================================================

def prompt_yes(question: str, *, default_yes: bool = True) -> bool:
    """Y/N prompt. Default specified by `default_yes` (1a → True)."""
    suffix = "[Y/n]" if default_yes else "[y/N]"
    try:
        ans = input(f"{question} {suffix} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not ans:
        return default_yes
    return ans in {"y", "yes"}


# =============================================================================
# Main install flow
# =============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install the CoAssisted Workspace canonical crontab.",
    )
    parser.add_argument(
        "--no-backfill", action="store_true",
        help=(
            "Skip the prompt for backfilling entries whose last "
            "scheduled fire was earlier today. Per Joshua's 2026-05-01 "
            "answer to question 1: backfill defaults to ON; this flag "
            "opts out."
        ),
    )
    parser.add_argument(
        "--force", action="store_true",
        help=(
            "Overwrite the existing crontab even when it differs from "
            "the canonical layout. Personal (non-CoAssisted) entries "
            "are preserved either way."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the next-fire table + diff but don't install.",
    )
    args = parser.parse_args()

    print("CoAssisted Workspace — crontab installer")
    print("=" * 60)
    print(f"  Project root: {PROJECT_ROOT}")
    if JOBS_JSON_PATH.exists():
        print(f"  Source:       cron_jobs.json (v0.9.3+ source of truth)")
    else:
        print(f"  Source:       {TEMPLATE_PATH.relative_to(PROJECT_ROOT)} "
              "(legacy — will bootstrap to cron_jobs.json on first manage call)")
    print(f"  Venv python:  {VENV_PYTHON}")
    print()

    if not VENV_PYTHON.exists():
        print(f"WARNING: {VENV_PYTHON} doesn't exist. Did you run install.sh?")
        if not args.force:
            print("Re-run with --force to install anyway.")
            return 2

    canonical = load_template()
    if not canonical:
        print("ERROR: Template is empty.")
        return 3

    # Per-entry table.
    now = _dt.datetime.now()
    today = now.date()
    print(f"Canonical schedule ({len(canonical)} entries):")
    print()
    print(f"  {'Job':<24} {'Schedule':<14} {'Next fire':<22} {'In':<10}")
    print(f"  {'-'*24} {'-'*14} {'-'*22} {'-'*10}")
    backfill_candidates: list[dict] = []
    parsed_entries = []
    for line in canonical:
        entry = parse_entry(line)
        if not entry:
            continue
        nfire = next_fire(entry["schedule"], base=now)
        pfire = prev_fire(entry["schedule"], base=now)
        entry["next_fire"] = nfire
        entry["prev_fire"] = pfire
        parsed_entries.append(entry)

        nfire_str = nfire.strftime("%Y-%m-%d %H:%M") if nfire else "-"
        in_str = humanize_delta(nfire - now) if nfire else "-"
        print(f"  {entry['label']:<24} {entry['schedule']:<14} {nfire_str:<22} {in_str:<10}")

        # Backfill candidate: prev_fire was today, before now.
        if pfire and pfire.date() == today and pfire < now:
            backfill_candidates.append(entry)

    print()

    # Diff vs. existing.
    existing = read_existing_crontab()
    to_add, to_remove, untouched = diff_against_canonical(existing, canonical)
    print(f"Crontab diff: +{len(to_add)} new / -{len(to_remove)} removed / "
          f"{len(untouched)} personal entries preserved")
    if to_remove and not args.force:
        print()
        print("WARNING: Existing CoAssisted entries differ from canonical.")
        print("Pass --force to replace them anyway.")
        return 4

    if args.dry_run:
        print()
        print("[--dry-run] No changes made.")
        return 0

    # Backfill prompts (per 1a default ON).
    if backfill_candidates and not args.no_backfill:
        print()
        print(f"Found {len(backfill_candidates)} entries whose last "
              f"scheduled time today has passed:")
        for entry in backfill_candidates:
            pfire = entry["prev_fire"]
            ago = humanize_delta(now - pfire)
            print(f"  • {entry['label']} — was due at {pfire.strftime('%H:%M')} "
                  f"({ago} ago)")
        print()
        if prompt_yes(
            "Run these jobs inline now (backfill)?", default_yes=True,
        ):
            for entry in backfill_candidates:
                ok, out = run_backfill(entry)
                marker = "✓" if ok else "✗"
                print(f"  {marker} {entry['label']}")
                if out and not ok:
                    print(f"     stderr (last 500 chars): {out}")

    # Install.
    print()
    print("Writing crontab ...")
    new_crontab = "\n".join(untouched + canonical) + "\n"
    try:
        proc = subprocess.run(
            ["crontab", "-"], input=new_crontab, text=True,
            capture_output=True, timeout=10,
        )
        if proc.returncode != 0:
            print(f"  ✗ crontab failed: {proc.stderr}")
            return 5
    except Exception as e:
        print(f"  ✗ Couldn't run crontab: {e}")
        return 5
    print(f"  ✓ Installed {len(canonical)} canonical entries.")

    # Run system_check_cron and print.
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from tools.system import _check_cron
        result = _check_cron()
        print()
        print(f"system_check_cron: {result.get('status', '?').upper()}")
        print(f"  {result.get('message', '')}")
    except Exception as e:
        print(f"  (Couldn't run system_check_cron: {e})")

    print()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
