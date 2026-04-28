"""Lightweight template renderer for mail-merge and templated emails.

Syntax:
    {field_name}              — substitutes the value of `field_name`
    {field_name|fallback}     — uses the fallback literal if field is missing/empty
    {{literal braces}}        — escape: `{{` and `}}` render as single `{` and `}`

Examples:
    render("Hi {first_name|there}", {"first_name": "Josh"})   → "Hi Josh"
    render("Hi {first_name|there}", {})                       → "Hi there"
    render("Quote: {{example}}", {})                          → "Quote: {example}"

Keeps things simple on purpose — no loops, no conditionals. If you need more
than this, use the raw Gmail tools and build the string in Python yourself.
"""

from __future__ import annotations

import re
from typing import Any

# Match either an escaped `{{` or `}}`, or a single `{field}` / `{field|fallback}` expression.
_PATTERN = re.compile(r"\{\{|\}\}|\{([^{}]+)\}")


def render(template: str, fields: dict[str, Any], default: str = "") -> str:
    """Render a template string against a dict of field values.

    Missing or empty fields render as the per-placeholder fallback (if given
    via `{field|fallback}` syntax) or else the `default` argument.
    """
    def replace(match: re.Match) -> str:
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        expr = match.group(1)
        if "|" in expr:
            key, fallback = expr.split("|", 1)
        else:
            key, fallback = expr, default
        value = fields.get(key.strip())
        if value is None or (isinstance(value, str) and not value.strip()):
            return fallback
        return str(value)

    return _PATTERN.sub(replace, template)


def extract_placeholders(template: str) -> list[str]:
    """Return the set of field names referenced in a template, in order of first appearance."""
    seen: list[str] = []
    for match in _PATTERN.finditer(template):
        token = match.group(0)
        if token in ("{{", "}}"):
            continue
        expr = match.group(1)
        key = expr.split("|", 1)[0].strip()
        if key not in seen:
            seen.append(key)
    return seen
