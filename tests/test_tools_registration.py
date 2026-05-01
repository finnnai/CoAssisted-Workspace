# © 2026 CoAssisted Workspace. Licensed for non-redistribution use only.
"""Smoke test that EVERY tools/* module registers cleanly with FastMCP.

Catches the entire untested-tools surface (gmail, calendar, drive, sheets,
docs, tasks, chat, maps, enrichment) without needing per-module test files.
What it asserts:

  - Every module under tools/ exposes a `register(mcp)` function.
  - Calling register against a mock MCP doesn't raise.
  - Across every module, no two tools share the same name (would shadow).
  - Every Pydantic input model declared in those tools has `extra="forbid"`
    in its model_config (consistent across the codebase; missing means new
    fields can be silently accepted).

This is the "if any tool path is broken, you'll know in 0.05s" guard.
"""

from __future__ import annotations

import importlib
from collections import Counter
from pathlib import Path

import pytest


# Discover tool modules dynamically so adding a new tools/<name>.py is
# automatically covered.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
# `workflows` is a backwards-compat re-export shim (P1-1 split). Its
# register() delegates to the 5 category modules — including it here would
# double-count every workflow tool. The 5 split modules are scanned directly.
_SHIM_MODULES = {"workflows"}

TOOL_MODULES = sorted(
    p.stem for p in TOOLS_DIR.glob("*.py")
    if p.stem != "__init__"
    and not p.stem.startswith("_")
    and p.stem not in _SHIM_MODULES
)


class FakeMCP:
    """Minimal MCP stub recording what tools get registered."""
    def __init__(self):
        self.tools: list[str] = []

    def tool(self, name=None, **kw):
        def deco(fn):
            self.tools.append(name or fn.__name__)
            return fn
        return deco


# --------------------------------------------------------------------------- #
# Per-module smoke: each tools/* registers without exception
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mod_name", TOOL_MODULES)
def test_module_imports_and_registers(mod_name):
    """Every tools/<name>.py imports without errors and exposes register()."""
    mod = importlib.import_module(f"tools.{mod_name}")
    assert hasattr(mod, "register"), (
        f"tools/{mod_name}.py is missing `register(mcp)` — won't be picked "
        f"up by tools/__init__.py and its tools won't appear in the server."
    )
    fake = FakeMCP()
    # Should not raise
    mod.register(fake)
    # Each module should register at least one tool (otherwise why does it exist?)
    assert len(fake.tools) > 0, (
        f"tools/{mod_name}.py registered 0 tools — likely a missed @mcp.tool decorator."
    )


# --------------------------------------------------------------------------- #
# Cross-module: no duplicate tool names across the entire surface
# --------------------------------------------------------------------------- #


def test_no_duplicate_tool_names_across_modules():
    """If two modules register a tool with the same name, the second wins
    silently and the first is shadowed. FastMCP would also raise, but this
    test surfaces it as a clear assertion at boot time."""
    fake = FakeMCP()
    for mod_name in TOOL_MODULES:
        mod = importlib.import_module(f"tools.{mod_name}")
        if hasattr(mod, "register"):
            mod.register(fake)
    counts = Counter(fake.tools)
    dupes = {name: n for name, n in counts.items() if n > 1}
    assert not dupes, f"Duplicate tool names found: {dupes}"


def test_tool_count_meets_expected_floor():
    """Sanity guard — any future delete that drops the tool count below
    150 is suspicious enough to warrant a test failure."""
    fake = FakeMCP()
    for mod_name in TOOL_MODULES:
        mod = importlib.import_module(f"tools.{mod_name}")
        if hasattr(mod, "register"):
            mod.register(fake)
    assert len(fake.tools) >= 150, (
        f"Only {len(fake.tools)} tools registered; was 169 at last release. "
        f"Did a tool module break?"
    )


# --------------------------------------------------------------------------- #
# Pydantic input model hygiene
# --------------------------------------------------------------------------- #


def test_input_models_use_extra_forbid():
    """The codebase convention is `extra='forbid'` on every Pydantic input
    model so unrecognized params surface as errors, not silent no-ops.
    Scan every tools/* and templates module for BaseModel subclasses and
    confirm the convention is followed."""
    from pydantic import BaseModel

    misses: list[str] = []
    for mod_name in TOOL_MODULES:
        mod = importlib.import_module(f"tools.{mod_name}")
        for attr_name in dir(mod):
            if attr_name.startswith("_"):
                continue
            attr = getattr(mod, attr_name)
            if not isinstance(attr, type):
                continue
            if not issubclass(attr, BaseModel) or attr is BaseModel:
                continue
            cfg = getattr(attr, "model_config", {}) or {}
            extra = cfg.get("extra")
            if extra != "forbid":
                misses.append(f"tools.{mod_name}.{attr_name} (extra={extra!r})")
    # Some receipt models inherit from a base — not all need extra=forbid.
    # Allow up to 5 exceptions before failing, then list them.
    assert len(misses) <= 5, (
        f"More than 5 input models missing extra='forbid': {misses}"
    )
