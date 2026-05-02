#!/usr/bin/env python3
# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Generate tools/pandadoc_*.py + pandadoc_operations.py from the OpenAPI spec.

Reads pandadoc_openapi.json (the upstream spec saved at the project
root) and emits:

  pandadoc_operations.py
      OPERATION_TABLE = {operation_id: {method, path, tag, ...}, ...}
      Used by pandadoc_client.call() to resolve operation_id → HTTP.

  tools/pandadoc_documents.py
  tools/pandadoc_templates.py
  tools/pandadoc_workspace.py
  tools/pandadoc_content.py
  tools/pandadoc_webhooks.py
  tools/pandadoc_misc.py
      One MCP tool per operation. Pydantic input model auto-built
      from the operation's parameters + requestBody schema.

The generator is idempotent — running it again replaces the files.
Re-run any time the upstream OpenAPI spec changes.

Module split (122 ops total):
    documents (49) — Documents tag + Attachments + Sections + Recipients
        + Fields + Settings + Audit Trail + Structure View
    templates (10) — Templates + Template Settings
    workspace (25) — User and Workspace + Members + Folders + Contacts
        + Communication Preferences
    content   (12) — Content Library + Product Catalog + Forms + Quotes
    webhooks  (8)  — Webhook subscriptions + Webhook events
    misc      (18) — Notary + Reminders + CRM Links + API Logs
        + OAuth 2.0 Authentication

Usage:
    python3 scripts/generate_pandadoc_tools.py
        [--spec /path/to/pandadoc_openapi.json]
        [--out-root /path/to/project/root]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import textwrap
from typing import Any

# -----------------------------------------------------------------------------
# Tag → module mapping
# -----------------------------------------------------------------------------

TAG_TO_MODULE = {
    # documents (49)
    "Documents": "documents",
    "Document Attachments": "documents",
    "Document Sections (Bundles)": "documents",
    "Document Recipients": "documents",
    "Document Fields": "documents",
    "Document Settings": "documents",
    "Document Reminders": "misc",
    "Document Link to CRM": "misc",
    "Document Audit Trail": "documents",
    "Document Structure View": "documents",
    # templates (10)
    "Templates": "templates",
    "Template Settings": "templates",
    # workspace (25)
    "User and Workspace management": "workspace",
    "Members": "workspace",
    "Folders": "workspace",
    "Contacts": "workspace",
    "Communication Preferences": "workspace",
    # content (12)
    "Content Library Items": "content",
    "Product catalog": "content",
    "Forms": "content",
    "Quotes": "content",
    # webhooks (8)
    "Webhook subscriptions": "webhooks",
    "Webhook events": "webhooks",
    # misc (18)
    "Notary": "misc",
    "API Logs": "misc",
    "OAuth 2.0 Authentication": "misc",
}

# Operations that need 202-polling.
POLLING_OPERATIONS = {"getDocumentSummary", "getDocumentContent"}

# Operations that take multipart/form-data uploads.
MULTIPART_OPERATIONS = {
    "createDocumentFromUpload",
    "createDocumentFromMarkdownUpload",
    "changeDocumentStatusWithUpload",
    "createDocumentAttachmentFromFileUpload",
    "uploadSectionWithUpload",
    "createContentLibraryItemFromUpload",
    "createTemplateWithUpload",
}

# Tool naming — every PandaDoc tool gets the pandadoc_ prefix so it
# doesn't collide with anything in the existing MCP surface.
TOOL_PREFIX = "pandadoc_"


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _camel_to_snake(name: str) -> str:
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _operation_to_tool_name(operation_id: str) -> str:
    return TOOL_PREFIX + _camel_to_snake(operation_id)


def _safe_param_name(name: str) -> str:
    """Pydantic field-safe identifier for a parameter name."""
    safe = re.sub(r"[^A-Za-z0-9_]", "_", name)
    if safe and safe[0].isdigit():
        safe = "_" + safe
    if safe in {"class", "from", "import", "return", "global", "lambda"}:
        safe = safe + "_"
    return safe


def _python_type_for(schema: dict[str, Any] | None) -> str:
    """Best-effort Pydantic type hint from a JSON schema fragment."""
    if not schema:
        return "Any"
    if "$ref" in schema:
        # Refs are component schemas — punt to Any so we don't
        # have to inline the whole component graph.
        return "dict[str, Any]"
    t = schema.get("type")
    if t == "string":
        if schema.get("format") in ("date", "date-time"):
            return "str"
        return "str"
    if t == "integer":
        return "int"
    if t == "number":
        return "float"
    if t == "boolean":
        return "bool"
    if t == "array":
        inner = _python_type_for(schema.get("items") or {})
        return f"list[{inner}]"
    if t == "object" or t is None:
        return "dict[str, Any]"
    return "Any"


def _module_for_tag(tags: list[str]) -> str:
    for t in tags:
        if t in TAG_TO_MODULE:
            return TAG_TO_MODULE[t]
    return "misc"


# -----------------------------------------------------------------------------
# Operation extraction
# -----------------------------------------------------------------------------

def _extract_operations(spec: dict) -> list[dict]:
    """Walk the spec and produce a flat list of operation records."""
    out = []
    for path, methods in (spec.get("paths") or {}).items():
        for method in ("get", "post", "put", "patch", "delete"):
            if method not in methods:
                continue
            op = methods[method]
            opid = op.get("operationId") or f"{method}_{path}"
            tags = op.get("tags") or ["_untagged"]

            # Path + query parameters.
            path_params = []
            query_params = []
            for p in op.get("parameters") or []:
                if "$ref" in p:
                    continue
                in_ = p.get("in")
                rec = {
                    "name": p.get("name"),
                    "required": bool(p.get("required")),
                    "type_hint": _python_type_for(p.get("schema") or {}),
                    "description": (p.get("description") or "").strip().replace(
                        "\n", " ",
                    )[:300],
                }
                if in_ == "path":
                    path_params.append(rec)
                elif in_ == "query":
                    query_params.append(rec)

            # Request body.
            rb = op.get("requestBody") or {}
            content = rb.get("content") or {}
            has_json_body = "application/json" in content
            has_multipart = "multipart/form-data" in content
            body_required = bool(rb.get("required", False))

            out.append({
                "operation_id": opid,
                "method": method.upper(),
                "path": path,
                "tag": tags[0],
                "module": _module_for_tag(tags),
                "summary": (op.get("summary") or "").strip(),
                "description": (op.get("description") or "").strip(),
                "deprecated": bool(op.get("deprecated")),
                "path_params": path_params,
                "query_params": query_params,
                "has_json_body": has_json_body,
                "has_multipart": has_multipart or opid in MULTIPART_OPERATIONS,
                "body_required": body_required,
                "needs_polling": opid in POLLING_OPERATIONS,
            })
    return out


# -----------------------------------------------------------------------------
# Code emission — pandadoc_operations.py
# -----------------------------------------------------------------------------

def emit_operation_table(operations: list[dict], out_path: pathlib.Path) -> None:
    """Emit pandadoc_operations.py — must be Python-valid, not JSON.

    Earlier rev used json.dumps which produces false/true/null and fails
    to import. Use pprint.pformat for Python-valid output.
    """
    import pprint
    table = {}
    for op in operations:
        table[op["operation_id"]] = {
            "method": op["method"],
            "path": op["path"],
            "tag": op["tag"],
            "module": op["module"],
            "deprecated": op["deprecated"],
            "needs_polling": op["needs_polling"],
            "has_multipart": op["has_multipart"],
        }
    body = textwrap.dedent('''\
        # © 2026 CoAssisted Workspace. Licensed under MIT.
        """Auto-generated from pandadoc_openapi.json — DO NOT EDIT BY HAND.

        Re-generate via:
            python3 scripts/generate_pandadoc_tools.py

        Maps every PandaDoc API operationId to its HTTP method, path, and
        tag/module assignment. pandadoc_client.call() uses this table to
        resolve which endpoint to hit.
        """

        from __future__ import annotations

        OPERATION_TABLE: dict[str, dict] = ''')
    body += pprint.pformat(table, indent=2, width=100, sort_dicts=True) + "\n"
    out_path.write_text(body, encoding="utf-8")


# -----------------------------------------------------------------------------
# Code emission — tools/pandadoc_<module>.py
# -----------------------------------------------------------------------------

MODULE_HEADER = '''\
# © 2026 CoAssisted Workspace. Licensed under MIT.
"""Auto-generated PandaDoc API tools — module: {module}.

DO NOT EDIT BY HAND. Regenerate via:
    python3 scripts/generate_pandadoc_tools.py

This module wraps {count} PandaDoc operations under tag(s): {tags}.

Pydantic input classes live at MODULE scope (not inside register())
so FastMCP's typing.get_type_hints can resolve them. Earlier rev
nested them in register() and triggered InvalidSignature on startup.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

import pandadoc_client
'''


def _render_tool_block(op: dict) -> tuple[str, str]:
    """Generate (module_level_class, register_body) for one operation.

    The Pydantic input class MUST live at module scope — FastMCP's
    func_metadata uses typing.get_type_hints(globalns=func.__globals__),
    which can't see closure variables from register(). Earlier rev put
    the class inside register(); FastMCP raised InvalidSignature on
    server startup.

    Returns:
        (class_block, register_body)
            class_block    — zero-indent Pydantic class definition.
            register_body  — 4-space-indent @mcp.tool() + def lines for
                             the register() function body.
    """
    tool_name = _operation_to_tool_name(op["operation_id"])
    op_id = op["operation_id"]

    # Build the Pydantic input model fields (8-space indent, inside class body).
    field_lines: list[str] = []
    used_names: set[str] = set()
    path_param_map: list[tuple[str, str]] = []
    query_param_map: list[tuple[str, str]] = []

    def _q(s: str) -> str:
        # Make a string safe to embed inside a "..."-quoted Field description.
        return s.replace("\\", "\\\\").replace('"', "'").replace("\n", " ")

    for p in op["path_params"]:
        safe = _safe_param_name(p["name"])
        while safe in used_names:
            safe = safe + "_"
        used_names.add(safe)
        path_param_map.append((p["name"], safe))
        desc = _q(p["description"])[:200]
        field_lines.append(
            f'{safe}: {p["type_hint"]} = Field(..., description="Path: {desc}")'
        )

    for p in op["query_params"]:
        safe = _safe_param_name(p["name"])
        while safe in used_names:
            safe = safe + "_"
        used_names.add(safe)
        query_param_map.append((p["name"], safe))
        desc = _q(p["description"])[:200]
        if p["required"]:
            field_lines.append(
                f'{safe}: {p["type_hint"]} = Field(..., description="Query: {desc}")'
            )
        else:
            field_lines.append(
                f'{safe}: Optional[{p["type_hint"]}] = '
                f'Field(None, description="Query: {desc}")'
            )

    if op["has_json_body"]:
        if op["body_required"]:
            field_lines.append(
                'body: dict[str, Any] = Field(..., description='
                '"JSON body — see PandaDoc docs for the schema of this endpoint.")'
            )
        else:
            field_lines.append(
                'body: Optional[dict[str, Any]] = Field(None, description='
                '"Optional JSON body — see PandaDoc docs.")'
            )

    if op["has_multipart"]:
        field_lines.extend([
            'file_path: Optional[str] = Field(None, description='
            '"Local path to the file to upload. At least one of file_path '
            'or file_bytes_b64 is required.")',
            'file_bytes_b64: Optional[str] = Field(None, description='
            '"Base64-encoded file bytes (alternative to file_path).")',
            'file_name: Optional[str] = Field(None, description='
            '"Filename to send. Defaults to basename of file_path or upload.bin.")',
            'content_type: Optional[str] = Field(None, description='
            '"MIME type. Guessed from extension if unset.")',
            'multipart_extra_fields: Optional[dict[str, Any]] = '
            'Field(None, description="Extra non-file form fields to '
            'include in the multipart body.")',
        ])

    if op["needs_polling"]:
        field_lines.append(
            'poll_max_seconds: Optional[int] = Field(None, description='
            '"Override config.pandadoc.poll_max_seconds for this call.")'
        )

    # call() invocation arguments.
    call_kwargs: list[str] = []
    if path_param_map:
        pairs = ", ".join(f'"{orig}": params.{safe}' for orig, safe in path_param_map)
        call_kwargs.append(f"path_params={{{pairs}}}")
    if query_param_map:
        pairs = ", ".join(f'"{orig}": params.{safe}' for orig, safe in query_param_map)
        call_kwargs.append(f"query={{{pairs}}}")
    if op["has_json_body"]:
        call_kwargs.append("json_body=params.body")
    if op["has_multipart"]:
        call_kwargs.append("multipart=mp_parts")
    if op["needs_polling"]:
        call_kwargs.append("poll=True")
        call_kwargs.append("poll_max_seconds=params.poll_max_seconds")

    # Docstring.
    docstring = (op["summary"] or op_id).strip().replace('"""', "'''")
    if op["deprecated"]:
        docstring = "[DEPRECATED] " + docstring

    # Module-level class — zero indent for the class line, 4-space
    # indent for the body.
    class_lines: list[str] = []
    class_lines.append(f"class _Input_{op_id}(BaseModel):")
    class_lines.append("    model_config = ConfigDict(extra='forbid')")
    if not field_lines:
        class_lines.append("    pass")
    else:
        for fl in field_lines:
            class_lines.append(f"    {fl}")
    class_block = "\n".join(class_lines) + "\n"

    # register() body — 4-space outer indent (we're inside the
    # register() function), 8-space inner indent (function body).
    BASE = "    "
    INNER = "        "
    body_lines: list[str] = []
    body_lines.append("")  # blank between tools
    body_lines.append(f"{BASE}@mcp.tool()")
    body_lines.append(f"{BASE}def {tool_name}(params: _Input_{op_id}) -> Any:")
    body_lines.append(f'{INNER}"""{docstring}"""')

    if op["has_multipart"]:
        multipart_prep = [
            "import base64, mimetypes, os",
            'filename = params.file_name or "upload.bin"',
            "ctype = params.content_type",
            "if params.file_path:",
            '    with open(params.file_path, "rb") as fp:',
            "        payload_bytes = fp.read()",
            "    if not params.file_name:",
            "        filename = os.path.basename(params.file_path)",
            "    if not ctype:",
            '        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"',
            "elif params.file_bytes_b64:",
            "    payload_bytes = base64.b64decode(params.file_bytes_b64)",
            "    if not ctype:",
            '        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"',
            "else:",
            '    return {"error": "Provide either file_path or file_bytes_b64."}',
            'mp_parts = [("file", (filename, payload_bytes, ctype))]',
            "if params.multipart_extra_fields:",
            "    import json as _json",
            "    for k, v in params.multipart_extra_fields.items():",
            "        if isinstance(v, (dict, list)):",
            "            v = _json.dumps(v)",
            '        mp_parts.append((k, (None, str(v).encode("utf-8"), "text/plain")))',
        ]
        for ml in multipart_prep:
            body_lines.append(f"{INNER}{ml}")

    body_lines.append(f"{INNER}return pandadoc_client.call(")
    body_lines.append(f'{INNER}    "{op_id}",')
    for ck in call_kwargs:
        body_lines.append(f"{INNER}    {ck},")
    body_lines.append(f"{INNER})")

    return class_block, "\n".join(body_lines) + "\n"


def emit_module(
    module_name: str,
    operations: list[dict],
    out_dir: pathlib.Path,
) -> None:
    """Emit one tools/pandadoc_<module>.py.

    Layout:
        <header with imports>
        <class _Input_xxx ...> × N    # module level
        <class _Input_yyy ...>
        ...

        def register(mcp):
            @mcp.tool()
            def pandadoc_xxx(...): ...

            @mcp.tool()
            def pandadoc_yyy(...): ...
    """
    tags = sorted({op["tag"] for op in operations})
    sorted_ops = sorted(operations, key=lambda o: o["operation_id"])

    class_blocks: list[str] = []
    register_bodies: list[str] = []
    for op in sorted_ops:
        cls, body = _render_tool_block(op)
        class_blocks.append(cls)
        register_bodies.append(body)

    parts: list[str] = []
    parts.append(MODULE_HEADER.format(
        module=module_name, count=len(operations),
        tags=", ".join(tags),
    ))
    parts.append("\n")
    parts.append("\n".join(class_blocks))
    parts.append("\n\n")
    parts.append("def register(mcp) -> None:  # noqa: ANN001\n")
    parts.append('    """Register every tool in this module with the FastMCP instance."""\n')
    parts.append("".join(register_bodies))

    out_path = out_dir / f"pandadoc_{module_name}.py"
    out_path.write_text("".join(parts), encoding="utf-8")


# -----------------------------------------------------------------------------
# main
# -----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec",
        default=None,
        help="Path to pandadoc_openapi.json (default: <project>/pandadoc_openapi.json)",
    )
    parser.add_argument(
        "--out-root",
        default=None,
        help="Project root (default: parent of this script's parent)",
    )
    args = parser.parse_args(argv)

    here = pathlib.Path(__file__).resolve().parent
    project_root = pathlib.Path(args.out_root) if args.out_root else here.parent
    spec_path = (
        pathlib.Path(args.spec)
        if args.spec
        else project_root / "pandadoc_openapi.json"
    )
    if not spec_path.exists():
        print(f"ERROR: spec not found at {spec_path}", file=sys.stderr)
        return 2

    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    operations = _extract_operations(spec)

    # Group by module.
    by_module: dict[str, list[dict]] = {}
    for op in operations:
        by_module.setdefault(op["module"], []).append(op)

    # Emit.
    tools_dir = project_root / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    emit_operation_table(operations, project_root / "pandadoc_operations.py")
    for module_name, ops in sorted(by_module.items()):
        emit_module(module_name, ops, tools_dir)

    print(f"Generated {len(operations)} operations across {len(by_module)} modules:")
    for m, ops in sorted(by_module.items()):
        print(f"  tools/pandadoc_{m}.py — {len(ops)} tools")
    print(f"  pandadoc_operations.py  — {len(operations)} operations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
