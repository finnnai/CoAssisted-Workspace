# © 2026 CoAssisted Workspace contributors. Licensed under MIT — see LICENSE.
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
    except Exception:
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
) -> dict:
    """Upsert a project. Re-registering with the same code merges incrementally
    (new sender_emails / patterns are appended, dedup'd; scalar fields overwrite).
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
    except Exception:
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


# Test helper — let unit tests redirect the registry to a tempdir.
def _override_path_for_tests(p: Path) -> None:
    global _REGISTRY_PATH
    _REGISTRY_PATH = Path(p)
