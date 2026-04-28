"""Template library for mail merge.

Templates live in `templates/` as Markdown files with YAML frontmatter:

    ---
    subject: "Following up, {first_name|there}"
    html_body: "<p>Optional HTML alternative</p>"
    ---
    Hi {first_name|there},

    <plain-text body here, supports {placeholders}>

Each file's name (minus `.md`) is the template's name.

Frontmatter format is deliberately minimal:
    subject:     string, required
    html_body:   string, optional (multiline via YAML | block)
    description: string, optional (shown in list_templates)

Body is everything after the second `---`. Leading whitespace on the first line
after the closing `---` is trimmed.

No third-party YAML dep — frontmatter is a simple `key: "value"` format (or
`key: |` for multiline), which we parse inline. Keeps the MCP lightweight.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


@dataclass
class Template:
    name: str
    subject: str
    body: str
    html_body: Optional[str] = None
    description: Optional[str] = None


class TemplateError(RuntimeError):
    """Raised when a template file is missing or malformed."""


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter-dict, body). Empty dict + original text if no frontmatter."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    fm_raw, body = m.group(1), m.group(2)
    meta: dict[str, str] = {}

    lines = fm_raw.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        if ":" not in line:
            i += 1
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        # Multiline scalar: `key: |` or `key: >`
        if value in ("|", ">"):
            collected: list[str] = []
            i += 1
            # Determine the leading indent from the first non-empty line.
            base_indent: int | None = None
            while i < len(lines):
                ln = lines[i]
                if base_indent is None and ln.strip():
                    base_indent = len(ln) - len(ln.lstrip())
                if base_indent is not None and ln.strip() and (
                    len(ln) - len(ln.lstrip()) < base_indent
                ):
                    break
                collected.append(ln[base_indent:] if base_indent else ln)
                i += 1
            joined = "\n".join(collected).rstrip("\n")
            meta[key] = joined if value == "|" else joined.replace("\n", " ")
            continue

        # Strip surrounding quotes if present.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        meta[key] = value
        i += 1

    return meta, body.lstrip("\n")


def templates_dir() -> Path:
    return _TEMPLATES_DIR


def list_templates() -> list[dict]:
    """List every template file's name, subject, and description."""
    if not _TEMPLATES_DIR.exists():
        return []
    out: list[dict] = []
    for path in sorted(_TEMPLATES_DIR.glob("*.md")):
        try:
            tpl = load(path.stem)
            out.append(
                {
                    "name": tpl.name,
                    "subject": tpl.subject,
                    "description": tpl.description,
                    "has_html": bool(tpl.html_body),
                    "path": str(path),
                }
            )
        except TemplateError as e:
            out.append({"name": path.stem, "error": str(e)})
    return out


def load(name: str) -> Template:
    """Load a named template. Raises TemplateError if missing or malformed."""
    safe_name = name.strip()
    if not safe_name or "/" in safe_name or ".." in safe_name:
        raise TemplateError(f"Invalid template name: {name!r}")
    path = _TEMPLATES_DIR / f"{safe_name}.md"
    if not path.is_file():
        raise TemplateError(
            f"Template {safe_name!r} not found at {path}. "
            f"Use gmail_list_templates to see what's available."
        )
    text = path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    subject = fm.get("subject")
    if not subject:
        raise TemplateError(f"Template {safe_name!r} is missing 'subject' in frontmatter.")
    return Template(
        name=safe_name,
        subject=subject,
        body=body.strip(),
        html_body=fm.get("html_body"),
        description=fm.get("description"),
    )
