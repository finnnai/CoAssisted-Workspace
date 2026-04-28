"""Dry-run support.

A tool can accept a `dry_run` argument. If True (or if `config.dry_run` is
globally True), the tool should return a preview-style JSON payload instead of
performing the side effect.

This module provides:
    - `is_dry_run(tool_arg)` — resolve the effective value considering global
      config override.
    - `dry_run_preview(tool_name, payload)` — canonical preview response shape.
"""

from __future__ import annotations

import json
from typing import Any

import config


def is_dry_run(tool_arg: bool | None) -> bool:
    """Resolve effective dry-run. Tool arg wins if set; else global config."""
    if tool_arg is not None:
        return bool(tool_arg)
    return bool(config.get("dry_run", False))


def dry_run_preview(tool_name: str, payload: dict[str, Any]) -> str:
    """Canonical preview JSON. Use this as the tool's return value when skipping side effects."""
    return json.dumps(
        {
            "status": "dry_run",
            "tool": tool_name,
            "would_do": payload,
        },
        indent=2,
    )
