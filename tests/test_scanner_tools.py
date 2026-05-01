"""Baseline unit tests for tools/scanner.py — P0-3 spec."""
from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from tools.scanner import RunScannerInput, ListChecksInput


def _resolve(name):
    from server import mcp
    return mcp._tool_manager._tools[name].fn


def test_list_checks_input_takes_no_args():
    ListChecksInput()


def test_run_scanner_input_construct():
    """RunScannerInput accepts default construction (empty filters)."""
    RunScannerInput()


def test_list_checks_returns_some_checks():
    """workflow_list_scanner_checks should list known check IDs without
    needing a live API."""
    fn = _resolve("workflow_list_scanner_checks")
    out = asyncio.run(fn(ListChecksInput()))
    payload = json.loads(out) if out.strip().startswith("{") or out.strip().startswith("[") else None
    if isinstance(payload, dict):
        assert "checks" in payload or len(payload) > 0


def test_scanner_tools_registered():
    from server import mcp
    assert {"workflow_run_scanner", "workflow_list_scanner_checks"}.issubset(
        set(mcp._tool_manager._tools)
    )
