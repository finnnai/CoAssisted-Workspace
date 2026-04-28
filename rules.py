"""Auto-tagging rules applied on contact create/update.

Loads `rules.json` at project root. Shape:

    {
      "domain_rules": {
        "@acme.com":    {"organization": "Acme Corp",  "tier": "enterprise"},
        "@startupx.io": {"organization": "StartupX",   "tier": "growth"}
      }
    }

When a contact is created or updated, any rule whose key matches the contact's
primary email domain contributes its fields. Existing user-supplied values
always win — rules only fill in blanks. Managed keys (CRM stats) are never
touched by rules.

Rules are advisory: if rules.json is missing or malformed, the contact tool
silently proceeds without applying rules. A warning is logged so you know.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from logging_util import log

_PROJECT_DIR = Path(__file__).resolve().parent
_RULES_PATH = _PROJECT_DIR / "rules.json"

_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    if not _RULES_PATH.exists():
        _cache = {}
        return _cache
    try:
        _cache = json.loads(_RULES_PATH.read_text())
    except Exception as e:
        log.warning("rules.json parse failed: %s — ignoring rules", e)
        _cache = {}
    return _cache


def reload() -> None:
    """Force re-read of rules.json."""
    global _cache
    _cache = None


def _domain_rules() -> dict[str, dict[str, str]]:
    data = _load()
    return data.get("domain_rules", {}) or {}


def apply_rules(
    email: str | None,
    *,
    existing_fields: dict[str, Any] | None = None,
    existing_custom: dict[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    """Evaluate rules for a given email; return (top_level_updates, custom_updates, applied_rules).

    Only fills in blanks — if `existing_fields` already has 'organization', the
    rule's organization value is ignored. Same for custom fields.

    `top_level_updates` covers fields like first_name, last_name, organization,
    title. `custom_updates` covers userDefined key/value tags. The caller is
    responsible for merging these into the contact payload.
    """
    if not email:
        return {}, {}, []

    existing_fields = existing_fields or {}
    existing_custom = existing_custom or {}

    email_lc = email.lower()
    rules = _domain_rules()
    applied: list[str] = []

    top_updates: dict[str, Any] = {}
    custom_updates: dict[str, str] = {}

    # Match rules by domain suffix. Key can be "@acme.com" or just "acme.com".
    for key, fields in rules.items():
        key_norm = key.lower().lstrip("@")
        if not email_lc.endswith("@" + key_norm) and not email_lc.endswith("." + key_norm):
            continue
        applied.append(key)
        for k, v in (fields or {}).items():
            if k in ("first_name", "last_name", "organization", "title"):
                if not existing_fields.get(k):
                    top_updates[k] = v
            else:
                # Treat anything else as a custom field.
                if k not in existing_custom:
                    custom_updates[k] = v

    return top_updates, custom_updates, applied
