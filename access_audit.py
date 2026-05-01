# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Access audit — classify and summarize Drive permissions on a file or folder.

Pure-logic core (no Drive API). The MCP tool wrapper lives in
tools/access_audit.py and feeds permission lists into summarize_permissions().

Each Drive permission has shape (per Drive v3 API):
    {
        "id":           "12345...",
        "type":         "user" | "group" | "domain" | "anyone",
        "role":         "owner" | "organizer" | "fileOrganizer" |
                        "writer" | "commenter" | "reader",
        "emailAddress": "person@example.com"   (only for user/group)
        "domain":       "example.com"           (only for domain)
        "displayName":  "Sarah Fields"          (often present)
        "deleted":      false                   (true if account is gone)
    }

This module classifies each grant by:
  - audience scope (user/group/domain/anyone)
  - relationship to the user (internal / subsidiary / external / public)
  - role privilege level (read-only vs. write vs. owner)
And surfaces risk flags:
  - "anyone with link" grants
  - external accounts with write/owner roles
  - deleted-account leftover grants
  - ownership held by someone other than the authenticated user
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

import sender_classifier

# Roles ordered from most-privileged to least.
_ROLE_RANK = {
    "owner": 0,
    "organizer": 1,
    "fileOrganizer": 2,
    "writer": 3,
    "commenter": 4,
    "reader": 5,
}


@dataclass
class Grant:
    """One Drive permission grant, normalized + classified."""
    perm_id: str
    type: str            # "user" | "group" | "domain" | "anyone"
    role: str            # "owner" | "writer" | "commenter" | "reader" | ...
    target: str          # email, domain, or "anyone-with-link"
    display_name: str | None
    relationship: str    # "self" | "internal" | "subsidiary" | "external" |
                         # "public" | "domain-wide" | "unknown"
    risk_flags: list[str] = field(default_factory=list)
    deleted: bool = False

    def to_dict(self) -> dict:
        return {
            "perm_id": self.perm_id,
            "type": self.type,
            "role": self.role,
            "target": self.target,
            "display_name": self.display_name,
            "relationship": self.relationship,
            "risk_flags": list(self.risk_flags),
            "deleted": self.deleted,
        }


@dataclass
class AuditReport:
    """Complete access audit summary for a file or folder."""
    file_id: str
    file_name: str | None
    grants: list[Grant] = field(default_factory=list)
    risk_score: int = 0   # higher = more concerning
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "file_name": self.file_name,
            "grant_count": len(self.grants),
            "risk_score": self.risk_score,
            "summary": dict(self.summary),
            "grants": [g.to_dict() for g in self.grants],
        }


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


def _classify_relationship(perm: dict, authed_email: str | None) -> str:
    """Return relationship code for a single permission entry."""
    ptype = perm.get("type")

    if ptype == "anyone":
        return "public"
    if ptype == "domain":
        return "domain-wide"

    email = (perm.get("emailAddress") or "").strip().lower()
    if not email:
        return "unknown"

    if authed_email and email == authed_email.lower():
        return "self"

    info = sender_classifier.classify(email)
    if info.get("tier") == "auto_domain":
        return "internal"
    if info.get("tier") == "config_internal":
        return "internal"
    if info.get("tier") == "config_subsidiary":
        return "subsidiary"
    if info.get("internal"):
        # send_as_alias falls here — treat as internal too.
        return "internal"
    return "external"


def _risk_flags_for(perm: dict, relationship: str, role: str) -> list[str]:
    """Return list of risk flags that apply to this grant."""
    flags: list[str] = []

    if perm.get("type") == "anyone":
        flags.append("anyone_with_link")
        if role in ("writer", "owner"):
            flags.append("public_writable")

    if relationship == "external":
        if role == "owner":
            flags.append("external_owner")
        elif role in ("writer", "fileOrganizer", "organizer"):
            flags.append("external_writer")

    if perm.get("type") == "domain":
        if role in ("writer", "owner"):
            flags.append("domain_writable")

    if perm.get("deleted"):
        flags.append("deleted_account")

    return flags


def _classify_one(perm: dict, authed_email: str | None) -> Grant:
    rel = _classify_relationship(perm, authed_email)
    role = perm.get("role", "")
    flags = _risk_flags_for(perm, rel, role)

    ptype = perm.get("type", "")
    if ptype == "anyone":
        target = "anyone-with-link"
    elif ptype == "domain":
        target = perm.get("domain", "?domain?")
    else:
        target = (perm.get("emailAddress") or "?").strip().lower()

    return Grant(
        perm_id=str(perm.get("id", "")),
        type=ptype,
        role=role,
        target=target,
        display_name=perm.get("displayName"),
        relationship=rel,
        risk_flags=flags,
        deleted=bool(perm.get("deleted")),
    )


# --------------------------------------------------------------------------- #
# Aggregation + risk scoring
# --------------------------------------------------------------------------- #


# Per-flag risk weight. Sum across grants → risk_score.
_RISK_WEIGHTS = {
    "public_writable": 50,
    "anyone_with_link": 20,
    "external_owner": 40,
    "external_writer": 25,
    "domain_writable": 15,
    "deleted_account": 5,
}


def _score(grants: Iterable[Grant]) -> int:
    total = 0
    for g in grants:
        for f in g.risk_flags:
            total += _RISK_WEIGHTS.get(f, 0)
    return total


def _summarize_counts(grants: list[Grant]) -> dict:
    by_relationship: dict[str, int] = {}
    by_role: dict[str, int] = {}
    flag_counts: dict[str, int] = {}
    for g in grants:
        by_relationship[g.relationship] = by_relationship.get(g.relationship, 0) + 1
        by_role[g.role] = by_role.get(g.role, 0) + 1
        for f in g.risk_flags:
            flag_counts[f] = flag_counts.get(f, 0) + 1
    return {
        "by_relationship": by_relationship,
        "by_role": by_role,
        "risk_flags": flag_counts,
    }


def summarize_permissions(
    file_id: str,
    file_name: str | None,
    permissions: list[dict],
    authed_email: str | None = None,
) -> AuditReport:
    """Build an AuditReport from a list of Drive permission dicts.

    Args:
        file_id: Drive file/folder ID being audited.
        file_name: Display name (purely cosmetic in the report).
        permissions: list of permission dicts as returned by Drive API.
        authed_email: the authenticated user's email (used to flag self-grants).

    Returns:
        AuditReport with classified grants, risk score, and summary counts.
    """
    grants = [_classify_one(p, authed_email) for p in permissions]
    # Sort: most-privileged role first, then external ahead of internal,
    # then by display target alphabetically.
    relationship_order = {
        "public": 0, "external": 1, "domain-wide": 2,
        "subsidiary": 3, "internal": 4, "self": 5, "unknown": 6,
    }
    grants.sort(key=lambda g: (
        _ROLE_RANK.get(g.role, 99),
        relationship_order.get(g.relationship, 99),
        g.target,
    ))

    return AuditReport(
        file_id=file_id,
        file_name=file_name,
        grants=grants,
        risk_score=_score(grants),
        summary=_summarize_counts(grants),
    )


# --------------------------------------------------------------------------- #
# Diff — compare two snapshots (e.g. report from yesterday vs today)
# --------------------------------------------------------------------------- #


@dataclass
class AuditDiff:
    file_id: str
    added: list[Grant] = field(default_factory=list)
    removed: list[Grant] = field(default_factory=list)
    changed_role: list[tuple[Grant, Grant]] = field(default_factory=list)  # (before, after)

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "added": [g.to_dict() for g in self.added],
            "removed": [g.to_dict() for g in self.removed],
            "changed_role": [
                {"before": b.to_dict(), "after": a.to_dict()}
                for b, a in self.changed_role
            ],
            "any_changes": bool(self.added or self.removed or self.changed_role),
        }


def diff_reports(before: AuditReport, after: AuditReport) -> AuditDiff:
    """Return what changed between two audit reports for the same file_id."""
    before_idx = {g.target: g for g in before.grants}
    after_idx = {g.target: g for g in after.grants}

    added = [g for t, g in after_idx.items() if t not in before_idx]
    removed = [g for t, g in before_idx.items() if t not in after_idx]
    changed: list[tuple[Grant, Grant]] = []
    for t, after_g in after_idx.items():
        before_g = before_idx.get(t)
        if before_g and before_g.role != after_g.role:
            changed.append((before_g, after_g))

    return AuditDiff(
        file_id=after.file_id or before.file_id,
        added=added,
        removed=removed,
        changed_role=changed,
    )
