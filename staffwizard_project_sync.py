# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""StaffWizard → project_registry sync.

v0.9.0 makes StaffWizard the source of truth for project codes. Every receipt
submission must resolve to a project that exists in StaffWizard's active set.

This module runs at the end of every StaffWizard ingest. For each unique
(JobNumber, JobDescription) pair seen in the parsed labor rows, we upsert a
project record into `project_registry.py` with:

    - code: derived from JobNumber when present, slugged from
      JobDescription otherwise. Stable across runs (deterministic from
      inputs).
    - name: JobDescription verbatim.
    - staffwizard_job_number / staffwizard_job_desc: stamped on the record
      so the AP-7 labor ingestion can map labor rows back to a project_code.
    - staffwizard_authoritative: True. Flags this record as upstream-owned;
      the receipt validator uses this set as the gate for "is this a real
      project?".
    - active: True if the project appeared in the most recent N days of
      StaffWizard reports (default 14). Older projects flip to active=False
      so the picker list stays short.

`SMOKE` (and any other project flagged `not_staffwizard_authoritative=True`
in the registry) is preserved as a non-StaffWizard project for tests +
internal use.

Public surface
--------------
    sync_projects_from_rows(rows, *, active_window_days=14) -> dict
        Drives the upsert. `rows` is the same shape staffwizard_pipeline
        produces (list of dict with keys 'Date', 'Job Number',
        'Job Description', etc.).

    active_staffwizard_codes() -> set[str]
        The set the receipt validator gates on. Includes only records
        with active=True AND staffwizard_authoritative=True.

    list_active_projects(*, limit=None) -> list[dict]
        Active StaffWizard projects, sorted by recency (last_seen desc).
        Each entry has {code, name, last_seen, recent_revenue}.
"""

from __future__ import annotations

import datetime as _dt
import re
from collections import defaultdict
from typing import Iterable, Optional

import project_registry as _pr


# --------------------------------------------------------------------------- #
# Code derivation
# --------------------------------------------------------------------------- #

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def _slug_from_description(desc: str) -> str:
    """Derive a stable, human-friendly code from the description.

    Picks the first 1-2 distinctive tokens (skipping common words),
    uppercases, hyphenates. Max 16 chars. Stable across runs because we
    don't randomize.

    Examples:
        'Children's Hospital' → 'CHILDRENS-HOSP'
        'Google - Golden Eagle 1' → 'GOLDEN-EAGLE-1'
        'Surefox Internal' → 'SUREFOX-INTERNAL'
        'Bill.Com Condor 9' → 'BILLCOM-CONDOR-9'
    """
    if not desc:
        return ""
    s = str(desc).strip()
    # Drop common filler tokens that don't disambiguate.
    skip = {"the", "and", "of", "for", "to", "a", "an", "with", "at", "in"}
    parts = [p for p in _NON_ALNUM.split(s) if p and p.lower() not in skip]
    if not parts:
        return ""
    if len(parts) >= 3 and len(parts[0]) <= 3:
        # First token is a short prefix like 'GE' or 'A16Z'; keep more.
        joined = "-".join(parts[:4])
    else:
        joined = "-".join(parts[:3])
    return joined.upper()[:16].rstrip("-")


def _code_from_pair(job_number: str, job_description: str) -> str:
    """Stable project code from a (JobNumber, JobDescription) pair.

    JobNumber wins when present and matches a code-shaped string
    ([A-Za-z0-9-]{3,16}). Otherwise we slug the description.
    """
    jn = (job_number or "").strip()
    if jn and re.fullmatch(r"[A-Za-z0-9\-]{3,16}", jn):
        return jn.upper()
    slug = _slug_from_description(job_description)
    if slug:
        return slug
    if jn:
        # Fallback: use whatever's in JobNumber, sanitized.
        return _NON_ALNUM.sub("-", jn).upper()[:16].rstrip("-")
    return ""


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #


def sync_projects_from_rows(
    rows: Iterable[dict],
    *,
    active_window_days: int = 14,
    today: Optional[_dt.date] = None,
) -> dict:
    """Upsert every (JobNumber, JobDescription) pair seen into project_registry.

    Logic:
        1. Group rows by (JobNumber, JobDescription).
        2. For each group, derive a stable code, find latest WorkDate,
           sum BillableDollars (revenue) and Hours.
        3. Upsert via project_registry.register(...) with staffwizard_*
           fields stamped. Mark 'active' if last seen within
           active_window_days, else False.
        4. Preserve non-StaffWizard projects (e.g. SMOKE) untouched.

    Returns:
        {status, registered, updated, made_inactive, preserved_non_sw,
         active_codes, sample_window_days}
    """
    today = today or _dt.date.today()
    cutoff = today - _dt.timedelta(days=active_window_days)

    # Aggregate by (job_number, job_description).
    groups: dict[tuple[str, str], dict] = defaultdict(
        lambda: {
            "last_date": None, "first_date": None,
            "shifts": 0, "revenue": 0.0, "hours": 0.0, "cost": 0.0,
        }
    )
    for r in rows:
        jn = str(r.get("Job Number") or "").strip()
        jd = str(r.get("Job Description") or "").strip()
        if not jn and not jd:
            continue
        try:
            d = _dt.date.fromisoformat(str(r.get("Date") or "")[:10])
        except (ValueError, TypeError):
            d = None
        agg = groups[(jn, jd)]
        agg["shifts"] += 1
        agg["revenue"] += float(r.get("Billable $") or 0.0)
        agg["hours"] += float(r.get("Total Hours") or 0.0)
        agg["cost"] += float(r.get("Total Cost $") or 0.0)
        if d:
            agg["last_date"] = max(agg["last_date"] or d, d)
            agg["first_date"] = min(agg["first_date"] or d, d)

    # Pre-existing projects, partitioned: SW-authoritative vs. not.
    existing = _pr.list_all(active_only=False)
    existing_by_code = {p["code"]: p for p in existing}
    sw_codes_seen_now: set[str] = set()

    registered = 0
    updated = 0
    made_inactive = 0

    for (jn, jd), agg in groups.items():
        code = _code_from_pair(jn, jd)
        if not code:
            continue
        sw_codes_seen_now.add(code)
        is_active = bool(agg["last_date"] and agg["last_date"] >= cutoff)

        was_existing = code in existing_by_code

        # Use register(...) — it merges name_aliases / sender_emails and
        # overwrites scalar fields when supplied. We only stamp scalar
        # fields the sync owns, so manual additions stick.
        _pr.register(
            code,
            name=jd or code,
            client=None,  # let manual edits win; client isn't from SW
            staffwizard_job_number=jn or None,
            staffwizard_job_desc=jd or None,
            active=is_active,
        )
        # The 'authoritative' flag isn't a register() kwarg, so we set it
        # via a follow-up direct write into the registry's loaded dict.
        _stamp_authoritative(code, agg, sw_active=is_active)
        if was_existing:
            updated += 1
        else:
            registered += 1

    # Any previously-SW-authoritative project not seen in this batch flips
    # to active=False (project is winding down or finished).
    for code, rec in existing_by_code.items():
        if not rec.get("staffwizard_authoritative"):
            continue
        if code in sw_codes_seen_now:
            continue
        if rec.get("active", True):
            _pr.register(code, name=rec.get("name") or code, active=False)
            made_inactive += 1

    return {
        "status": "ok",
        "registered": registered,
        "updated": updated,
        "made_inactive": made_inactive,
        "preserved_non_sw": sum(
            1 for r in existing if not r.get("staffwizard_authoritative")
        ),
        "active_codes": sorted(active_staffwizard_codes()),
        "sample_window_days": active_window_days,
    }


def _stamp_authoritative(code: str, agg: dict, *, sw_active: bool) -> None:
    """Write the staffwizard_authoritative + recent stats fields directly
    on the registry record. register() doesn't take these kwargs (yet)
    so we do a manual atomic-write.
    """
    # pylint: disable=protected-access
    data = _pr._load()  # noqa: SLF001
    key = _pr._normalize_code(code)  # noqa: SLF001
    rec = data.get(key)
    if not rec:
        return
    rec["staffwizard_authoritative"] = True
    rec["staffwizard_last_seen"] = (
        agg["last_date"].isoformat() if agg.get("last_date") else None
    )
    rec["staffwizard_first_seen"] = (
        agg["first_date"].isoformat() if agg.get("first_date") else None
    )
    rec["staffwizard_recent_revenue"] = round(float(agg.get("revenue") or 0.0), 2)
    rec["staffwizard_recent_hours"] = round(float(agg.get("hours") or 0.0), 2)
    rec["staffwizard_recent_cost"] = round(float(agg.get("cost") or 0.0), 2)
    rec["active"] = bool(sw_active)
    data[key] = rec
    _pr._save(data)  # noqa: SLF001


# --------------------------------------------------------------------------- #
# Lookups used by the receipt validator + the picker tools
# --------------------------------------------------------------------------- #


def active_staffwizard_codes() -> set[str]:
    """Set of active StaffWizard-authoritative project codes."""
    return {
        p["code"] for p in _pr.list_all(active_only=True)
        if p.get("staffwizard_authoritative")
    }


def list_active_projects(*, limit: Optional[int] = None) -> list[dict]:
    """Active StaffWizard projects, sorted by recency desc.

    Each entry: {code, name, last_seen, recent_revenue, recent_hours}.
    """
    rows = []
    for p in _pr.list_all(active_only=True):
        if not p.get("staffwizard_authoritative"):
            continue
        rows.append({
            "code": p["code"],
            "name": p.get("name", p["code"]),
            "last_seen": p.get("staffwizard_last_seen"),
            "recent_revenue": p.get("staffwizard_recent_revenue", 0.0),
            "recent_hours": p.get("staffwizard_recent_hours", 0.0),
        })
    rows.sort(key=lambda r: r.get("last_seen") or "", reverse=True)
    if limit:
        return rows[:limit]
    return rows


def lookup_by_input(user_text: str) -> Optional[dict]:
    """Best-effort match of free-text user input to a StaffWizard project.

    Used by the chat-back picker reply handler. Tries:
        1. Exact code match (case-insensitive).
        2. Exact name match.
        3. Substring match on name (only if exactly one project matches).

    Returns the matched project dict, or None on no/ambiguous match.
    """
    if not user_text:
        return None
    t = str(user_text).strip()
    if not t:
        return None
    upper = t.upper()

    actives = list_active_projects()
    # Tier 1: code.
    for p in actives:
        if p["code"] == upper:
            return p
    # Tier 2: exact name.
    for p in actives:
        if (p.get("name") or "").strip().lower() == t.lower():
            return p
    # Tier 3: unique substring on name.
    matches = [p for p in actives if t.lower() in (p.get("name") or "").lower()]
    if len(matches) == 1:
        return matches[0]
    return None
