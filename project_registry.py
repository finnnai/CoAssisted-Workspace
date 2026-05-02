# © 2026 CoAssisted Workspace. Licensed under MIT.
# See LICENSE file for terms.
"""Project registry — persistent map of project_code → routing rules.

Used by the project-invoice extractor to decide which project an inbound
invoice belongs to. Routing follows a fixed-priority ladder; the first tier
that resolves wins.

Resolution ladder (highest authority first):
    1. Explicit project_code passed by the caller        (1.00 confidence)
    2. Filename regex match (e.g. ^INV-ALPHA-)           (0.95)
    3. Sender email exact match                          (0.90)
    4. Chat space ID match (when source is Gchat)        (0.85)
    5. LLM inference over invoice content + project list (variable)
    6. Park in "Needs Project Assignment" sheet          (resolution=None)

Storage:
    ~/Claude/google_workspace_mcp/projects.json (atomic writes — same pattern
    as merchant_cache).

Project record shape:
    {
        "code":              "ALPHA",
        "name":              "Project Alpha — Surefox HQ Build",
        "client":            "Surefox",
        "sender_emails":     ["pm@subcontractor.com"],
        "chat_space_ids":    ["spaces/AAQA..."],
        "filename_patterns": ["^INV-ALPHA-", "(?i)\\balpha\\b"],
        "default_billable":  true,
        "default_markup_pct": 15.0,
        "sheet_id":          "1AbCd...",
        "sheet_name":        "Project Expenses — ALPHA",
        "currency":          "USD",
        "active":            true,
        "first_seen":        "2026-04-26T...",
        "last_seen":         "2026-04-26T...",
        "invoice_count":     0
    }

Operations:
    register(code, ...)                  - upsert a project
    get(code) -> dict | None             - fetch one project
    list_all(*, active_only=True) -> list
    forget(code) -> bool
    increment_invoice_count(code, n=1)
    resolve(...) -> ResolveResult        - the 5-tier ladder
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_PROJECT_ROOT = Path(__file__).resolve().parent
_REGISTRY_PATH = _PROJECT_ROOT / "projects.json"


# Resolution confidences per tier. Caller can compare these against a
# threshold before deciding to write the row vs. parking it.
CONF_EXPLICIT = 1.00
CONF_FILENAME = 0.95
CONF_SENDER   = 0.90
CONF_CHAT     = 0.85
# LLM inference confidence comes back from the LLM itself.

# Confidence floor below which we PARK the invoice instead of routing it.
# An LLM inference of 0.7 is fine; 0.5 is "just guessing" and should be
# manually confirmed.
RESOLVE_THRESHOLD = 0.65


# --------------------------------------------------------------------------- #
# Resolve result shape
# --------------------------------------------------------------------------- #


@dataclass
class ResolveResult:
    project_code: Optional[str]    # None → park in Needs Review
    confidence: float
    tier: str                      # 'explicit' | 'filename' | 'sender' |
                                   # 'chat_space' | 'llm' | 'unresolved'
    reason: str                    # human-readable: which rule matched

    def as_dict(self) -> dict:
        return {
            "project_code": self.project_code,
            "confidence": round(self.confidence, 2),
            "tier": self.tier,
            "reason": self.reason,
        }


# --------------------------------------------------------------------------- #
# Storage primitives — mirror merchant_cache.py atomic-write pattern
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _normalize_code(code: str) -> str:
    """Project codes are case-insensitive on lookup, stored uppercase."""
    return (code or "").strip().upper()


def _load() -> dict[str, dict]:
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        with _REGISTRY_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, dict]) -> None:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix="projects.", suffix=".json.tmp",
        dir=str(_REGISTRY_PATH.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_path, _REGISTRY_PATH)
    except (OSError, TypeError, ValueError):
        # OSError: filesystem (disk full, permissions, replace failed).
        # TypeError/ValueError: non-serializable value snuck into data.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


def register(
    code: str,
    *,
    name: str,
    client: Optional[str] = None,
    sender_emails: Optional[list[str]] = None,
    chat_space_ids: Optional[list[str]] = None,
    filename_patterns: Optional[list[str]] = None,
    default_billable: bool = True,
    default_markup_pct: float = 0.0,
    sheet_id: Optional[str] = None,
    sheet_name: Optional[str] = None,
    currency: str = "USD",
    active: bool = True,
    # --- Wave 2 additions (AP-5 / AP-6 / AP-9) ---
    drive_folder_id: Optional[str] = None,
    drive_subfolders: Optional[dict[str, Optional[str]]] = None,
    name_aliases: Optional[list[str]] = None,
    staffwizard_job_number: Optional[str] = None,
    staffwizard_job_desc: Optional[str] = None,
    assigned_team_emails: Optional[list[str]] = None,
    billing_origin_state: Optional[str] = None,
    billing_terms: Optional[str] = None,
    billing_cadence: Optional[str] = None,
    customer_email: Optional[str] = None,
) -> dict:
    """Upsert a project. Re-registering with the same code merges incrementally
    (new sender_emails / patterns / aliases are appended, dedup'd; scalar
    fields overwrite when supplied, preserve when None).

    Wave 2 fields:
        drive_folder_id, drive_subfolders — Drive tree IDs created by AP-6
            workflow_register_new_project. drive_subfolders maps subfolder
            name (receipts/invoices/labor/statements_amex/statements_wex/
            workday_supplier/workday_journal) → folder ID. None values mean
            "not created yet, build lazily on first write."
        name_aliases — alternate spellings the AP-5 subject classifier
            should recognize (e.g. ["GE1", "Golden Eagle"] for "Google -
            Golden Eagle 1").
        staffwizard_job_number, staffwizard_job_desc — keys from the
            StaffWizard Overall Report. Used by AP-7 labor ingestion to
            map the job back to a project_code.
        assigned_team_emails — guards / managers on this project. AP-5
            uses this to route inbound receipts when subject matching
            misses.
        billing_origin_state — "NY" unlocks the weekly-billing option in
            AP-9 per the operator's New York project rule.
        billing_terms — "Net-15" default; override per customer.
        billing_cadence — "monthly" default; "weekly" for NY projects.
        customer_email — where AR-9 sends customer invoices.
    """
    key = _normalize_code(code)
    if not key:
        raise ValueError("project code is required")
    data = _load()
    now = _now_iso()
    existing = data.get(key, {})

    def _merge_list(prev: list, new: Optional[list]) -> list:
        if not new:
            return list(prev or [])
        out = list(prev or [])
        for item in new:
            if item and item not in out:
                out.append(item)
        return out

    record = dict(existing) if existing else {
        "code": key,
        "first_seen": now,
        "invoice_count": 0,
    }
    record["name"] = name
    if client is not None:
        record["client"] = client
    record["sender_emails"] = _merge_list(
        existing.get("sender_emails"), sender_emails,
    )
    record["chat_space_ids"] = _merge_list(
        existing.get("chat_space_ids"), chat_space_ids,
    )
    record["filename_patterns"] = _merge_list(
        existing.get("filename_patterns"), filename_patterns,
    )
    record["default_billable"] = bool(default_billable)
    record["default_markup_pct"] = float(default_markup_pct or 0.0)
    if sheet_id is not None:
        record["sheet_id"] = sheet_id
    if sheet_name is not None:
        record["sheet_name"] = sheet_name
    record["currency"] = currency or "USD"
    record["active"] = bool(active)
    record["last_seen"] = now

    # --- Wave 2 fields. None means "preserve existing", explicit values overwrite. ---
    if drive_folder_id is not None:
        record["drive_folder_id"] = drive_folder_id
    if drive_subfolders is not None:
        # Merge: existing subfolder IDs are kept unless the caller passes
        # a new value (including explicit None to clear).
        merged = dict(existing.get("drive_subfolders") or {})
        merged.update(drive_subfolders)
        record["drive_subfolders"] = merged
    record["name_aliases"] = _merge_list(
        existing.get("name_aliases"), name_aliases,
    )
    if staffwizard_job_number is not None:
        record["staffwizard_job_number"] = staffwizard_job_number
    if staffwizard_job_desc is not None:
        record["staffwizard_job_desc"] = staffwizard_job_desc
    record["assigned_team_emails"] = _merge_list(
        existing.get("assigned_team_emails"), assigned_team_emails,
    )
    if billing_origin_state is not None:
        record["billing_origin_state"] = billing_origin_state
    if billing_terms is not None:
        record["billing_terms"] = billing_terms
    if billing_cadence is not None:
        record["billing_cadence"] = billing_cadence
    if customer_email is not None:
        record["customer_email"] = customer_email

    data[key] = record
    _save(data)
    return dict(record)


def get(code: str) -> Optional[dict]:
    if not code:
        return None
    return _load().get(_normalize_code(code))


def list_all(*, active_only: bool = True) -> list[dict]:
    data = _load()
    rows = [dict(rec) for rec in data.values()]
    if active_only:
        rows = [r for r in rows if r.get("active", True)]
    rows.sort(key=lambda r: r.get("name", "").lower())
    return rows


def forget(code: str) -> bool:
    if not code:
        return False
    key = _normalize_code(code)
    data = _load()
    if key not in data:
        return False
    del data[key]
    _save(data)
    return True


def increment_invoice_count(code: str, n: int = 1) -> None:
    if not code:
        return
    key = _normalize_code(code)
    data = _load()
    if key not in data:
        return
    data[key]["invoice_count"] = int(data[key].get("invoice_count", 0)) + n
    data[key]["last_seen"] = _now_iso()
    _save(data)


def clear() -> int:
    """Drop ALL projects. Returns count removed. Admin/test only."""
    data = _load()
    n = len(data)
    _save({})
    return n


# --------------------------------------------------------------------------- #
# Resolution ladder
# --------------------------------------------------------------------------- #


def _norm_email(s: str) -> str:
    return (s or "").strip().lower()


def _extract_email_address(sender: str) -> str:
    """Pull the email out of an RFC 'Name <addr@host>' header."""
    if not sender:
        return ""
    s = sender.strip()
    if "<" in s and ">" in s:
        s = s.split("<", 1)[1].split(">", 1)[0]
    return _norm_email(s)


def resolve(
    *,
    project_code_hint: Optional[str] = None,
    filename: Optional[str] = None,
    sender_email: Optional[str] = None,
    chat_space_id: Optional[str] = None,
    invoice_text: Optional[str] = None,
    use_llm: bool = True,
) -> ResolveResult:
    """Resolve a project_code via the 5-tier ladder.

    Tiers stop at the first match. Returns ResolveResult — caller decides
    whether to honor low-confidence resolutions or park them.

    `use_llm=False` skips Tier 5 entirely (e.g. when the caller wants a
    purely deterministic resolve for tests).
    """
    # Tier 1: explicit caller hint
    if project_code_hint:
        key = _normalize_code(project_code_hint)
        rec = get(key)
        if rec:
            return ResolveResult(
                project_code=key,
                confidence=CONF_EXPLICIT,
                tier="explicit",
                reason=f"explicit project_code='{project_code_hint}'",
            )
        # Unknown explicit code → still respect it but flag with lower conf
        return ResolveResult(
            project_code=key,
            confidence=0.7,
            tier="explicit",
            reason=f"explicit but unregistered code='{project_code_hint}'",
        )

    projects = list_all(active_only=True)
    if not projects:
        return ResolveResult(
            project_code=None, confidence=0.0,
            tier="unresolved", reason="no_projects_registered",
        )

    # Tier 2: filename pattern
    if filename:
        for proj in projects:
            patterns = proj.get("filename_patterns") or []
            for pat in patterns:
                try:
                    if re.search(pat, filename):
                        return ResolveResult(
                            project_code=proj["code"],
                            confidence=CONF_FILENAME,
                            tier="filename",
                            reason=f"filename ~ /{pat}/",
                        )
                except re.error:
                    continue  # bad regex in registry; keep going

    # Tier 3: sender email
    sender_clean = _extract_email_address(sender_email or "")
    if sender_clean:
        for proj in projects:
            for addr in proj.get("sender_emails") or []:
                if _norm_email(addr) == sender_clean:
                    return ResolveResult(
                        project_code=proj["code"],
                        confidence=CONF_SENDER,
                        tier="sender",
                        reason=f"sender={sender_clean}",
                    )

    # Tier 4: chat space
    if chat_space_id:
        for proj in projects:
            for sp in proj.get("chat_space_ids") or []:
                if sp == chat_space_id:
                    return ResolveResult(
                        project_code=proj["code"],
                        confidence=CONF_CHAT,
                        tier="chat_space",
                        reason=f"chat_space={chat_space_id}",
                    )

    # Tier 5: LLM inference over content
    if use_llm and invoice_text:
        try:
            inferred = _llm_infer_project(invoice_text, projects)
        except Exception:
            inferred = None
        if inferred and inferred.get("code"):
            conf = float(inferred.get("confidence") or 0.5)
            return ResolveResult(
                project_code=inferred["code"],
                confidence=conf,
                tier="llm",
                reason=f"llm_inferred (conf={conf:.2f}): "
                       f"{(inferred.get('rationale') or '')[:120]}",
            )

    return ResolveResult(
        project_code=None, confidence=0.0,
        tier="unresolved", reason="no_rule_matched",
    )


def _llm_infer_project(invoice_text: str, projects: list[dict]) -> Optional[dict]:
    """Ask Claude Haiku which registered project this invoice belongs to.

    Returns dict {code, confidence, rationale} or None on any failure.
    """
    try:
        import llm as _llm
    except ImportError:
        return None
    ok, _why = _llm.is_available()
    if not ok:
        return None

    # Compact list — code + name + client only. Keeps prompt cheap.
    project_lines = "\n".join(
        f"- {p['code']}: {p.get('name', '')}"
        f" (client: {p.get('client') or 'n/a'})"
        for p in projects
    )

    prompt = (
        "Given an invoice's text, pick which project it belongs to from the "
        "list below. Return ONLY valid JSON — no prose, no code fences.\n\n"
        f"Available projects:\n{project_lines}\n\n"
        "Invoice content (first 2000 chars):\n"
        f"{invoice_text[:2000]}\n\n"
        'JSON shape: {"code": "<one of the codes above OR null>", '
        '"confidence": <0.0-1.0>, "rationale": "<one sentence>"}\n\n'
        "Use null for code if no project clearly matches."
    )

    try:
        resp = _llm.call_simple(prompt, max_tokens=300, temperature=0.0)
    except Exception:
        return None

    text = (resp.get("text") or "").strip()
    # Strip code fences if Claude relapses into them.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    code = data.get("code")
    if not code:
        return None
    norm = _normalize_code(code)
    # Sanity check: only honor codes that exist in the registry.
    valid = {p["code"] for p in projects}
    if norm not in valid:
        return None
    return {
        "code": norm,
        "confidence": float(data.get("confidence") or 0.5),
        "rationale": data.get("rationale") or "",
    }


# --------------------------------------------------------------------------- #
# Wave 2 helpers — alias / team / StaffWizard resolution + Drive subfolders
# --------------------------------------------------------------------------- #


# Confidence boost when an additional Wave 2 signal corroborates a tier match.
CONF_ALIAS = 0.92
CONF_TEAM = 0.88
CONF_STAFFWIZARD = 0.95


def resolve_by_alias(query: str) -> Optional[dict]:
    """Match a free-text query against project_name + name_aliases.

    Used by AP-5 when an inbound subject line carries a project hint
    like 'Receipt for GE1' or 'Condor 12 fuel'. Case- and whitespace-
    insensitive substring match. First match wins (registry order).
    """
    if not query:
        return None
    q = query.lower().strip()
    for record in list_all(active_only=True):
        candidates = [record.get("name") or "", record.get("code") or ""]
        candidates += list(record.get("name_aliases") or [])
        for c in candidates:
            if c and c.lower() in q:
                return dict(record)
    return None


def resolve_by_team_email(email: str) -> list[dict]:
    """Return projects this email is assigned to, sorted by recency.

    Multiple matches are possible (rotating duty). AP-5 falls back to
    calendar / geo tiebreakers when len > 1.
    """
    if not email:
        return []
    e = _norm_email(email)
    out = []
    for record in list_all(active_only=True):
        emails = [_norm_email(x) for x in (record.get("assigned_team_emails") or [])]
        if e in emails:
            out.append(dict(record))
    out.sort(key=lambda r: r.get("last_seen") or "", reverse=True)
    return out


def resolve_by_staffwizard_job(
    job_number: Optional[str],
    job_description: Optional[str],
) -> Optional[dict]:
    """Match against StaffWizard Overall Report's JobNumber + JobDescription.

    AP-7 labor ingestion uses this — the daily report carries a
    {JobNumber, JobDescription} pair per shift, and we need to know
    which registered project_code that maps to.
    """
    if not job_number and not job_description:
        return None
    jn = (job_number or "").strip().lower()
    jd = (job_description or "").strip().lower()
    for record in list_all(active_only=True):
        rn = (record.get("staffwizard_job_number") or "").strip().lower()
        rd = (record.get("staffwizard_job_desc") or "").strip().lower()
        if jn and rn and jn == rn:
            if not jd or not rd or jd == rd:
                return dict(record)
    return None


def update_drive_subfolder(
    code: str,
    subfolder_key: str,
    folder_id: str,
) -> bool:
    """Record a Drive folder ID for a per-project subfolder.

    Used by AP-6 lazy expansion (e.g., the first June-2026 receipt
    triggers creation of `Receipts/2026-06/` and stamps the ID here)
    and by `workflow_register_new_project` which creates the full
    subtree on registration.

    Subfolder keys are free-form strings; conventional ones include:
    'receipts', 'invoices', 'labor', 'statements_amex', 'statements_wex',
    'workday_supplier', 'workday_journal', plus monthly buckets like
    'receipts_2026_05'.
    """
    key = _normalize_code(code)
    data = _load()
    if key not in data:
        return False
    subs = data[key].setdefault("drive_subfolders", {})
    subs[subfolder_key] = folder_id
    data[key]["last_seen"] = _now_iso()
    _save(data)
    return True


def get_drive_subfolder(code: str, subfolder_key: str) -> Optional[str]:
    """Return the Drive folder ID for a project's subfolder, or None."""
    rec = get(code)
    if not rec:
        return None
    subs = rec.get("drive_subfolders") or {}
    return subs.get(subfolder_key)


# Test helper — let unit tests redirect the registry to a tempdir.
def _override_path_for_tests(p: Path) -> None:
    global _REGISTRY_PATH
    _REGISTRY_PATH = Path(p)
