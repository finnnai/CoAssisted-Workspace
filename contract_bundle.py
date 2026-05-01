# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Contract bundle generator — pure-logic core.

Builds an index from a list of contract Drive files. The MCP wrapper at
tools/contract_bundle.py is responsible for searching Drive, downloading
the files, packaging them as a ZIP, and uploading the index Doc.

A "contract file" is just a Drive file dict; the index logic doesn't
care where it came from.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Iterable

# Patterns that suggest a file is a contract / NDA / agreement.
# Note: we normalize "_" → " " before matching so underscore-separated
# filenames like "Acme_signed.pdf" still match \b boundaries.
_CONTRACT_NAME_PATTERNS = [
    r"\bnda\b",
    r"\bagreement\b",
    r"\bcontract\b",
    r"\bmsa\b",
    r"\bsow\b",
    r"\bsigned\b",
    r"\bexecuted\b",
    r"\bdpa\b",
]
_CONTRACT_REGEX = [re.compile(p, re.IGNORECASE) for p in _CONTRACT_NAME_PATTERNS]


def _normalize_name(name: str) -> str:
    """Replace separators (_, -) with spaces so \\b regex matches reliably."""
    return re.sub(r"[_\-]", " ", name)

# Doc types that are very likely contracts (PDF, Word).
_LIKELY_MIME = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.google-apps.document",
}


@dataclass
class ContractFile:
    """One contract document discovered during the search."""
    file_id: str
    name: str
    mime_type: str
    modified_time: str | None    # ISO 8601 from Drive
    web_view_link: str | None
    counterparty: str | None     # parsed from filename if recognizable

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "name": self.name,
            "mime_type": self.mime_type,
            "modified_time": self.modified_time,
            "web_view_link": self.web_view_link,
            "counterparty": self.counterparty,
        }


@dataclass
class ContractBundle:
    """A grouped+filtered set of contracts with index metadata."""
    title: str
    year: int | None
    contract_type: str | None     # e.g. 'NDA', 'MSA', 'all'
    files: list[ContractFile] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "year": self.year,
            "contract_type": self.contract_type,
            "file_count": len(self.files),
            "files": [f.to_dict() for f in self.files],
        }


# --------------------------------------------------------------------------- #
# Filename heuristics
# --------------------------------------------------------------------------- #


def looks_like_contract(name: str, mime: str | None = None) -> bool:
    """True if the filename + mime suggests this is a contract document."""
    if not name:
        return False
    if mime and mime not in _LIKELY_MIME:
        # Allow folders out, but pass-through anything else; Drive returns
        # mime="application/vnd.google-apps.folder" for folders which we
        # explicitly never want.
        if mime == "application/vnd.google-apps.folder":
            return False
    haystack = _normalize_name(name)
    for rx in _CONTRACT_REGEX:
        if rx.search(haystack):
            return True
    return False


def extract_counterparty(name: str) -> str | None:
    """Try to pull a counterparty name from a contract filename.

    Heuristic: take the longest token cluster that isn't a known noise word.
    Examples that should resolve well:
        'NDA - Acme Corp - 2025.pdf'                  → 'Acme Corp'
        'Acme_NDA_signed.pdf'                          → 'Acme'
        '2025-09-12 MSA Anthropic v3 (executed).pdf'   → 'Anthropic'
    """
    if not name:
        return None
    # Strip extension
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name)
    # Replace separators with spaces
    cleaned = re.sub(r"[_\-]+", " ", stem)
    # Drop bracketed annotations like (executed) or [v3]
    cleaned = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", cleaned)
    # Drop dates and version markers
    cleaned = re.sub(r"\b\d{4}[\-/.]\d{1,2}[\-/.]\d{1,2}\b", " ", cleaned)
    cleaned = re.sub(r"\b\d{4}\b", " ", cleaned)
    cleaned = re.sub(r"\bv\d+(\.\d+)*\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    NOISE = {
        "nda", "msa", "sow", "agreement", "contract", "signed", "executed",
        "draft", "final", "vendor", "client", "service", "services", "and",
        "the", "of", "to", "with", "fully", "for", "dpa",
    }
    tokens = [t for t in cleaned.split(" ")
              if t and t.lower() not in NOISE]
    if not tokens:
        return None
    # Take the first contiguous run of capitalized words; if none, take the
    # first 1-3 remaining tokens.
    cap_run = []
    for t in tokens:
        if t[:1].isupper() or t.isupper():
            cap_run.append(t)
        elif cap_run:
            break
    if cap_run:
        return " ".join(cap_run[:4])
    return " ".join(tokens[:2])


# --------------------------------------------------------------------------- #
# Filtering + indexing
# --------------------------------------------------------------------------- #


def filter_contracts(
    files: Iterable[dict],
    year: int | None = None,
    contract_type: str | None = None,
) -> list[ContractFile]:
    """Filter a list of Drive files down to contracts matching given criteria.

    Args:
        files: Drive file dicts (id, name, mimeType, modifiedTime, webViewLink).
        year: if provided, restrict to files modified in this year.
        contract_type: if provided (e.g. 'NDA', 'MSA'), restrict to files
                       with that token in the name.

    Returns:
        list of ContractFile, sorted by modifiedTime descending.
    """
    out: list[ContractFile] = []
    type_rx = (re.compile(rf"\b{re.escape(contract_type)}\b", re.IGNORECASE)
               if contract_type and contract_type.lower() != "all" else None)
    for f in files:
        name = f.get("name") or ""
        mime = f.get("mimeType")
        if not looks_like_contract(name, mime):
            continue
        if type_rx and not type_rx.search(_normalize_name(name)):
            continue
        modified = f.get("modifiedTime")
        if year is not None and modified:
            try:
                mod_dt = _dt.datetime.fromisoformat(modified.replace("Z", "+00:00"))
                if mod_dt.year != year:
                    continue
            except ValueError:
                pass
        out.append(ContractFile(
            file_id=f.get("id", ""),
            name=name,
            mime_type=mime or "",
            modified_time=modified,
            web_view_link=f.get("webViewLink"),
            counterparty=extract_counterparty(name),
        ))
    out.sort(key=lambda c: c.modified_time or "", reverse=True)
    return out


def build_index_markdown(bundle: ContractBundle) -> str:
    """Render a markdown index Doc body for the bundle."""
    lines: list[str] = []
    lines.append(f"# {bundle.title}")
    if bundle.year:
        lines.append(f"_Year: {bundle.year}_")
    if bundle.contract_type and bundle.contract_type.lower() != "all":
        lines.append(f"_Type: {bundle.contract_type}_")
    lines.append(f"_Generated: {_dt.datetime.now().date().isoformat()}_")
    lines.append("")
    lines.append(f"**{len(bundle.files)} document(s) included.**")
    lines.append("")
    lines.append("| # | Counterparty | Filename | Modified | Link |")
    lines.append("|---|---|---|---|---|")
    for i, f in enumerate(bundle.files, 1):
        cp = f.counterparty or "—"
        modified = (f.modified_time or "")[:10]
        link = f.web_view_link or ""
        # escape pipes in name
        safe_name = (f.name or "").replace("|", "\\|")
        link_md = f"[open]({link})" if link else ""
        lines.append(f"| {i} | {cp} | {safe_name} | {modified} | {link_md} |")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"
